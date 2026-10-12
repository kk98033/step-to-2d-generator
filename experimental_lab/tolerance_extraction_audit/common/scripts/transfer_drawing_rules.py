"""Transfer view-relative drawing style between canonical 3D features.

The target DXF is opened only after transfer and is used as holdout evidence.
Tolerance values are intentionally not transferred: this experiment measures
drawing intent (view, side, rank and target geometry), not tolerance policy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.canonical_features import extract_canonical_features  # noqa: E402
from auto_2d_drawing.drawing_projection import feature_view_geometry, project_point  # noqa: E402
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from generate_tolerance_edge_review import _render_native_dxf  # noqa: E402
from replay_drawing_rules import (  # noqa: E402
    BACKGROUND,
    MUTED,
    PANEL,
    RELIABLE,
    REVIEW,
    WHITE,
    _draw_model,
    _draw_rule,
    _expand_bbox,
    _font,
    _mapper,
    _native_crop_bounds,
)


METHOD = "CANONICAL_FEATURE_DRAWING_STYLE_TRANSFER_V1"
TRANSFER_FIELDS = {"length", "diameter", "groove_diameter", "groove_width", "radius"}


def load_source_feature_links(
    case_base_path: Path,
    drawing_file: str,
) -> dict[str, dict[str, Any]]:
    """Return the strongest canonical link for each source DXF handle."""
    payload = json.loads(case_base_path.read_text(encoding="utf-8"))
    cases = payload if isinstance(payload, list) else payload.get("cases") or []
    result: dict[str, dict[str, Any]] = {}
    for case in cases:
        metadata = case.get("source_metadata") or {}
        handle = str(metadata.get("entity_handle") or "")
        if metadata.get("drawing_file") != drawing_file or not handle:
            continue
        feature_id = metadata.get("canonical_feature_id")
        nominal_field = metadata.get("matched_nominal_field")
        if not feature_id or nominal_field not in TRANSFER_FIELDS:
            continue
        item = {
            "dimension_handle": handle,
            "canonical_feature_id": str(feature_id),
            "nominal_field": str(nominal_field),
            "verification_status": str(case.get("verification_status") or "UNRESOLVED"),
            "confidence": float(case.get("confidence") or 0.0),
            "feature_type": case.get("feature_type"),
            "feature_role": metadata.get("feature_role"),
            "case_id": case.get("case_id"),
        }
        previous = result.get(handle)
        if previous is None or item["confidence"] > previous["confidence"]:
            result[handle] = item
    return result


def select_transfer_view(
    overall_record: dict[str, Any],
    view_data: dict[str, Any],
    source_rules: Sequence[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Select a side view whose projected main axis matches source layout."""
    source_horizontal = sum(
        str(rule.get("placement", {}).get("side")) in {"TOP", "BOTTOM"}
        for rule in source_rules
    ) >= max(1, len(source_rules) / 2)
    nominal_length = float((overall_record.get("nominal") or {}).get("length") or 0.0)
    ranked = []
    for view_name in view_data:
        footprint = feature_view_geometry(overall_record.get("geometry") or {}, view_name, view_data[view_name].get("bbox"))
        width, height = float(footprint["width"]), float(footprint["height"])
        long_span = max(width, height)
        if nominal_length <= 0.0:
            agreement = 0.0
        else:
            agreement = abs(long_span - nominal_length) / nominal_length
        orientation_ok = width >= height if source_horizontal else height > width
        score = (2.0 if orientation_ok else 0.0) + max(0.0, 1.0 - agreement) + min(long_span / max(min(width, height), 1e-9), 10.0) * 0.02
        ranked.append((score, view_name, footprint, agreement))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    _score, view_name, footprint, agreement = ranked[0]
    return view_name, {
        "footprint": footprint,
        "length_agreement_error": round(float(agreement), 6),
        "source_layout_orientation": "HORIZONTAL" if source_horizontal else "VERTICAL",
        "candidates": [
            {"view": name, "score": round(float(score), 6), "length_agreement_error": round(float(error), 6)}
            for score, name, _candidate_footprint, error in ranked
        ],
    }


def canonical_nominal(record: dict[str, Any], field: str) -> float | None:
    value = (record.get("nominal") or {}).get(field)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0.0 else None


def _axis_vector(geometry: dict[str, Any]) -> tuple[float, float, float] | None:
    axis = geometry.get("axis")
    if isinstance(axis, str) and axis.upper() in {"X", "Y", "Z"}:
        return {
            "X": (1.0, 0.0, 0.0),
            "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0),
        }[axis.upper()]
    axis_dir = geometry.get("axis_dir")
    if isinstance(axis_dir, Sequence) and len(axis_dir) >= 3:
        return float(axis_dir[0]), float(axis_dir[1]), float(axis_dir[2])
    return None


def feature_measurement_anchors(
    record: dict[str, Any],
    nominal_field: str,
    view_name: str,
    fallback_bbox: Sequence[float],
) -> list[tuple[float, float]]:
    geometry = record.get("geometry") or {}
    footprint = feature_view_geometry(geometry, view_name, fallback_bbox)
    x0, y0, x1, y1 = (float(value) for value in footprint["bbox"])
    cx, cy = (float(value) for value in footprint["center"])
    axis_vector = _axis_vector(geometry)
    projected_axis = None
    if axis_vector is not None:
        origin = project_point((0.0, 0.0, 0.0), view_name)
        endpoint = project_point(axis_vector, view_name)
        projected_axis = endpoint[0] - origin[0], endpoint[1] - origin[1]

    if nominal_field in {"diameter", "groove_diameter", "radius"}:
        # Diameter is perpendicular to the shaft axis in a longitudinal view.
        if projected_axis and abs(projected_axis[0]) >= abs(projected_axis[1]):
            return [(cx, y0), (cx, y1)]
        if projected_axis:
            return [(x0, cy), (x1, cy)]
        return [(x0, cy), (x1, cy)] if abs(x1 - x0) >= abs(y1 - y0) else [(cx, y0), (cx, y1)]

    # Axial lengths follow the projected feature axis.  If the axis is not
    # available, use the longer feature span.
    if projected_axis and abs(projected_axis[0]) >= abs(projected_axis[1]):
        return [(x0, cy), (x1, cy)]
    if projected_axis:
        return [(cx, y0), (cx, y1)]
    return [(x0, cy), (x1, cy)] if abs(x1 - x0) >= abs(y1 - y0) else [(cx, y0), (cx, y1)]


def build_transfers(
    source_rules: Sequence[dict[str, Any]],
    source_links: dict[str, dict[str, Any]],
    target_records: Sequence[dict[str, Any]],
    target_view: str,
    target_view_bbox: Sequence[float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = {str(record.get("id")): record for record in target_records}
    transferred = []
    rejected = []
    for source_rule in source_rules:
        handle = str(source_rule.get("dimension_handle") or "")
        link = source_links.get(handle)
        if link is None:
            rejected.append({"dimension_handle": handle, "reason": "NO_CANONICAL_SOURCE_LINK"})
            continue
        target_record = records.get(link["canonical_feature_id"])
        if target_record is None:
            rejected.append({**link, "reason": "TARGET_FEATURE_ABSENT"})
            continue
        nominal = canonical_nominal(target_record, link["nominal_field"])
        if nominal is None:
            rejected.append({**link, "reason": "TARGET_NOMINAL_FIELD_ABSENT"})
            continue
        dim_type = "DIAMETER" if link["nominal_field"] in {"diameter", "groove_diameter"} else "RADIAL" if link["nominal_field"] == "radius" else "LINEAR"
        transfer = {
            "dimension_handle": handle,
            "source_case_id": link["case_id"],
            "source_verification_status": link["verification_status"],
            "canonical_feature_id": link["canonical_feature_id"],
            "nominal_field": link["nominal_field"],
            "target_nominal_value": nominal,
            "target_feature_type": target_record.get("type"),
            "target_feature_role": target_record.get("role"),
            "target_view": target_view,
            "anchors": [list(point) for point in feature_measurement_anchors(target_record, link["nominal_field"], target_view, target_view_bbox)],
            "drawing_style": {
                "side": source_rule.get("placement", {}).get("side") or "BOTTOM",
                "rank": int(source_rule.get("placement", {}).get("rank") or 1),
                "dimstyle": source_rule.get("style", {}).get("dimstyle"),
            },
            "tolerance_policy": "NOT_TRANSFERRED_SEPARATE_RECOMMENDATION_REQUIRED",
            "evidence_status": "EXTRACTED_RELIABLE_2D" if link["verification_status"] == "AUTO_VERIFIED" else "REVIEW_CANDIDATE",
            "dimension": {
                "dim_type": dim_type,
                "nominal_value": nominal,
                "prefix": "Ø" if dim_type == "DIAMETER" else "R" if dim_type == "RADIAL" else "",
                "tolerance_config": {"mode": "NONE"},
            },
            "placement": {
                "side": source_rule.get("placement", {}).get("side") or "BOTTOM",
                "rank": int(source_rule.get("placement", {}).get("rank") or 1),
            },
        }
        transferred.append(transfer)
    return transferred, rejected


def evaluate_holdout(
    transferred: Sequence[dict[str, Any]],
    target_dxf: Path,
) -> dict[str, Any]:
    dimensions = DxfToleranceExtractor().extract_from_file(
        str(target_dxf), include_rejected=True, native_dimensions_only=True
    )
    results = []
    for transfer in transferred:
        expected_type = str(transfer["dimension"]["dim_type"])
        nominal = float(transfer["target_nominal_value"])
        tolerance = max(0.05, nominal * 0.005)
        matches = []
        for dimension in dimensions:
            category = str(dimension.dimension_category or dimension.dim_type or "").upper()
            comparable = category == expected_type or (expected_type == "RADIAL" and category in {"RADIUS", "RADIAL"})
            if comparable and abs(float(dimension.nominal_value or 0.0) - nominal) <= tolerance:
                matches.append({
                    "handle": dimension.entity_handle,
                    "nominal_value": float(dimension.nominal_value),
                    "dimension_category": category,
                    "tolerance_config": dimension.tolerance_config,
                })
        results.append({
            "source_handle": transfer["dimension_handle"],
            "canonical_feature_id": transfer["canonical_feature_id"],
            "nominal_field": transfer["nominal_field"],
            "target_nominal_value": nominal,
            "holdout_supported": bool(matches),
            "holdout_matches": matches,
        })
    return {
        "policy": "TARGET_DXF_OPENED_ONLY_AFTER_TRANSFER_FOR_EVALUATION",
        "native_dimension_count": len(dimensions),
        "supported_count": sum(item["holdout_supported"] for item in results),
        "evaluated_count": len(results),
        "results": results,
    }


def _render_native_crop(dxf_path: Path, bounds: Sequence[float], output: Path) -> None:
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    dimensions = DxfToleranceExtractor().extract_from_file(str(dxf_path), include_rejected=True, native_dimensions_only=True)
    if not dimensions:
        raise RuntimeError(f"No native dimensions in {dxf_path}")
    _render_native_dxf(
        dxf_path, dimensions[0], {}, [], extractor.sheet_bbox, bounds, output, (1700, 1420)
    )


def _source_crop(source_rules: Sequence[dict[str, Any]]) -> tuple[float, float, float, float]:
    return _native_crop_bounds(source_rules)


def _target_crop(registration: dict[str, Any]) -> tuple[float, float, float, float]:
    # Keep room for native target dimensions without shrinking the matched
    # component into a full-sheet thumbnail.  A very large vertical expansion
    # makes longitudinal parts unreadable in the review board.
    return _expand_bbox(registration["dxf_bbox"], 0.45, 0.9)


def render_transfer_board(
    source_original: Path,
    target_original: Path,
    target_view_data: dict[str, Any],
    transferred: Sequence[dict[str, Any]],
    rejected: Sequence[dict[str, Any]],
    holdout: dict[str, Any],
    source_name: str,
    target_name: str,
    target_view: str,
    output: Path,
) -> None:
    canvas = Image.new("RGB", (5400, 1900), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((20, 20, 5380, 1880), radius=18, fill=PANEL)
    draw.text((55, 40), "Cross-model Drawing Rule Transfer", font=_font(34, True), fill=WHITE)
    draw.text(
        (55, 92),
        f"source={source_name}  target={target_name}  auto-selected target view={target_view}  "
        f"transferred={len(transferred)}  rejected={len(rejected)}  holdout={holdout['supported_count']}/{holdout['evaluated_count']}",
        font=_font(20), fill=MUTED,
    )
    boxes = ((55, 160, 1745, 1725), (1855, 160, 3545, 1725), (3655, 160, 5345, 1725))
    titles = ("SOURCE DRAWING RULES", "TARGET STEP + TRANSFERRED STYLE", "TARGET HOLDOUT DRAWING (EVALUATION ONLY)")
    for box, title in zip(boxes, titles):
        draw.rectangle(box, fill=BACKGROUND, outline="#404040", width=2)
        draw.text((box[0] + 18, box[1] + 14), title, font=_font(21, True), fill=WHITE)
    for image_path, box in ((source_original, boxes[0]), (target_original, boxes[2])):
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        image.thumbnail((box[2] - box[0] - 40, box[3] - box[1] - 130), Image.Resampling.LANCZOS)
        canvas.paste(image, (box[0] + (box[2] - box[0] - image.width) // 2, box[1] + 75))

    model_bbox = target_view_data["bbox"]
    world = _expand_bbox(model_bbox, 0.55, 1.65)
    map_point = _mapper(world, (1885, 245, 3515, 1585))
    _draw_model(draw, target_view_data, map_point)
    supported = {item["source_handle"] for item in holdout["results"] if item["holdout_supported"]}
    for transfer in transferred:
        _draw_rule(draw, transfer, [tuple(point) for point in transfer["anchors"]], model_bbox, map_point)
        feature_label = f"{transfer['dimension_handle']} -> {transfer['canonical_feature_id']}.{transfer['nominal_field']}"
        color = RELIABLE if transfer["evidence_status"] == "EXTRACTED_RELIABLE_2D" else REVIEW
        suffix = " | holdout nominal found" if transfer["dimension_handle"] in supported else " | no holdout nominal"
        draw.text((1880, 1605 + 24 * (transferred.index(transfer) % 4)), feature_label + suffix, font=_font(15), fill=color)
    if rejected:
        rejection_text = "; ".join(f"{item.get('dimension_handle')}: {item.get('reason')}" for item in rejected[:5])
        draw.text((55, 1780), "Rejected: " + rejection_text, font=_font(17), fill=REVIEW)
    draw.text((55, 1820), "Tolerance values were not transferred; only drawing view/side/rank/target semantics were tested.", font=_font(17), fill=MUTED)
    canvas.save(output)


def run_transfer(
    source_rules_path: Path,
    case_base_path: Path,
    target_pose_report_path: Path,
    target_step: Path,
    target_dxf: Path,
    target_component_name: str,
    target_component_label: str,
    output_dir: Path,
) -> dict[str, Any]:
    source_report = json.loads(source_rules_path.read_text(encoding="utf-8"))
    source_rules = list(source_report.get("drawing_rule_cases") or [])
    source_drawing = str(source_report["source"]["path"])
    source_links = load_source_feature_links(case_base_path, Path(source_drawing).name)

    shape = load_component_shape(str(target_step), target_component_label)
    if shape is None or shape.IsNull():
        raise RuntimeError(f"Unable to load target component {target_component_label}")
    feature_set = extract_canonical_features(shape, part_hint=target_component_name)
    view_data = ViewProjector().project_all_views(shape, view_names=["front", "top", "right", "left", "back"])
    overall = feature_set.get("shaft_overall")
    if overall is None:
        raise RuntimeError("Target has no canonical shaft_overall feature")
    linked_source_rules = [rule for rule in source_rules if rule.get("dimension_handle") in source_links]
    target_view, view_selection = select_transfer_view(overall, view_data, linked_source_rules)
    transferred, rejected_linked = build_transfers(
        linked_source_rules,
        source_links,
        feature_set.records,
        target_view,
        view_data[target_view]["bbox"],
    )
    unlinked = [
        {"dimension_handle": rule.get("dimension_handle"), "reason": "NO_CANONICAL_SOURCE_LINK"}
        for rule in source_rules if rule.get("dimension_handle") not in source_links
    ]
    rejected = rejected_linked + unlinked

    # Holdout target evidence is deliberately loaded after transfer is frozen.
    holdout = evaluate_holdout(transferred, target_dxf)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_original = output_dir / "00_source_original.png"
    target_original = output_dir / "01_target_holdout_original.png"
    _render_native_crop(Path(source_drawing), _source_crop(source_rules), source_original)
    pose_payload = json.loads(target_pose_report_path.read_text(encoding="utf-8"))
    pose_component = next(item for item in pose_payload["components"] if item.get("component_name") == target_component_name)
    registration = pose_component["pose_search"]["locked_registration"]
    _render_native_crop(target_dxf, _target_crop(registration), target_original)
    board = output_dir / "02_cross_model_transfer_review.png"
    render_transfer_board(
        source_original,
        target_original,
        view_data[target_view],
        transferred,
        rejected,
        holdout,
        Path(source_drawing).stem,
        target_component_name,
        target_view,
        board,
    )
    linked_count = len(linked_source_rules)
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "source": {
            "drawing": source_drawing,
            "rule_count": len(source_rules),
            "canonical_linked_rule_count": linked_count,
        },
        "target": {
            "component_name": target_component_name,
            "component_label": target_component_label,
            "step_path": str(target_step),
            "part_type": feature_set.part_type,
            "selected_view": target_view,
            "view_selection": view_selection,
        },
        "policy": {
            "semantic_match": "EXACT_CANONICAL_FEATURE_ID_AND_NOMINAL_FIELD_ONLY",
            "target_dxf_used_during_transfer": False,
            "tolerance_values_transferred": False,
        },
        "metrics": {
            "all_source_rule_transfer_rate": round(len(transferred) / max(len(source_rules), 1), 6),
            "canonical_linked_transfer_rate": round(len(transferred) / max(linked_count, 1), 6),
            "holdout_nominal_support_rate": round(holdout["supported_count"] / max(holdout["evaluated_count"], 1), 6),
        },
        "transferred_rules": transferred,
        "rejected_rules": rejected,
        "holdout_evaluation": holdout,
        "review_board": str(board),
    }
    (output_dir / "drawing_rule_transfer_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-rules", required=True)
    parser.add_argument("--case-base", required=True)
    parser.add_argument("--target-pose-report", required=True)
    parser.add_argument("--target-step", required=True)
    parser.add_argument("--target-dxf", required=True)
    parser.add_argument("--target-component-name", required=True)
    parser.add_argument("--target-component-label", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run_transfer(
        Path(args.source_rules).resolve(),
        Path(args.case_base).resolve(),
        Path(args.target_pose_report).resolve(),
        Path(args.target_step).resolve(),
        Path(args.target_dxf).resolve(),
        args.target_component_name,
        args.target_component_label,
        Path(args.output).resolve(),
    )
    print(json.dumps({"metrics": report["metrics"], "target_view": report["target"]["selected_view"], "review_board": report["review_board"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
