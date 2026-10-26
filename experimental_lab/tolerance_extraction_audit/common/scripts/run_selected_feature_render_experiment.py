"""Exercise the engineer-selected feature-to-drawing contract.

This experiment intentionally does not decide which dimensions belong on a
drawing.  A JSON fixture acts as the engineer and supplies the feature, view,
side, and tolerance.  The experiment then checks topology-derived anchors,
invokes the production smart renderer, and creates a visual review board.

Legacy ``dimension_engine.py`` and legacy extractors are not imported here.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402


METHOD = "ENGINEER_SELECTED_FEATURE_RENDER_CONTRACT_V1"
BACKGROUND = "#111111"
PANEL = "#171717"
GRID = "#404040"
MODEL = "#d4d4d4"
HIDDEN = "#525252"
CYAN = "#22d3ee"
ORANGE = "#f97316"
YELLOW = "#facc15"
PASS = "#34d399"
WARN = "#fbbf24"
FAIL = "#f87171"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _font(size: int, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    from PIL import ImageFont

    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(point) for point in edge.get("points") or []]


def _mapper(bbox: Sequence[float], box: Sequence[int]):
    x0, y0, x1, y1 = (float(value) for value in bbox)
    left, top, right, bottom = box
    width = max(x1 - x0, 1e-9)
    height = max(y1 - y0, 1e-9)
    scale = min((right - left) / width, (bottom - top) / height)
    pad_x = ((right - left) - width * scale) / 2.0
    pad_y = ((bottom - top) - height * scale) / 2.0

    def map_point(point: Sequence[float]) -> tuple[float, float]:
        return (
            left + pad_x + (float(point[0]) - x0) * scale,
            bottom - pad_y - (float(point[1]) - y0) * scale,
        )

    return map_point


def validate_selection(rule: dict[str, Any], view_name: str, view: dict[str, Any]) -> dict[str, Any]:
    """Validate exact per-view geometry without silently inventing anchors."""
    warnings: list[str] = []
    errors: list[str] = []
    allowed_views = list(rule.get("target_views") or rule.get("views") or [])
    if allowed_views and view_name not in allowed_views:
        warnings.append("ENGINEER_VIEW_OUTSIDE_RECOMMENDED_SET")

    payload = (rule.get("view_geometry") or {}).get(view_name)
    if not payload:
        errors.append("NO_TOPOLOGY_DERIVED_VIEW_GEOMETRY")
        payload = {}

    projected_bbox = payload.get("projected_feature_bbox")
    if not isinstance(projected_bbox, Sequence) or len(projected_bbox) != 4:
        errors.append("NO_PROJECTED_FEATURE_BBOX")
        projected_bbox = [0.0, 0.0, 0.0, 0.0]
    feature_width = abs(float(projected_bbox[2]) - float(projected_bbox[0]))
    feature_height = abs(float(projected_bbox[3]) - float(projected_bbox[1]))
    if max(feature_width, feature_height) <= 1e-6:
        errors.append("DEGENERATE_PROJECTED_FEATURE")

    view_bbox = view.get("bbox") or (0.0, 0.0, 0.0, 0.0)
    view_width = abs(float(view_bbox[2]) - float(view_bbox[0]))
    view_height = abs(float(view_bbox[3]) - float(view_bbox[1]))
    bbox_error = max(abs(float(projected_bbox[i]) - float(view_bbox[i])) for i in range(4))
    if bbox_error <= max(view_width, view_height, 1.0) * 1e-5:
        warnings.append("FEATURE_GEOMETRY_FELL_BACK_TO_WHOLE_VIEW")

    start = payload.get("start_proj")
    end = payload.get("end_proj")
    anchor_distance = None
    dim_type = str(rule.get("dim_type") or "").upper()
    if dim_type in {"LINEAR", "DIAMETER", "CENTERLINES"}:
        if not (
            isinstance(start, Sequence) and len(start) >= 2
            and isinstance(end, Sequence) and len(end) >= 2
        ):
            errors.append("MISSING_MEASUREMENT_ANCHORS")
        else:
            anchor_distance = math.dist(
                (float(start[0]), float(start[1])),
                (float(end[0]), float(end[1])),
            )
            if anchor_distance <= 1e-6:
                errors.append("ZERO_LENGTH_MEASUREMENT_ANCHOR")
    elif dim_type == "LEADER":
        if not (isinstance(start, Sequence) and len(start) >= 2):
            errors.append("MISSING_LEADER_CONTACT_POINT")
    elif dim_type == "HOLE_PATTERN":
        center = payload.get("center")
        if not (isinstance(center, Sequence) and len(center) >= 2):
            errors.append("MISSING_PATTERN_CENTER")

    nominal = abs(float(rule.get("nominal_value") or 0.0))
    relative_error = None
    if anchor_distance is not None and nominal > 1e-6 and dim_type in {"LINEAR", "DIAMETER"}:
        relative_error = abs(anchor_distance - nominal) / nominal
        if relative_error > 0.25:
            errors.append("PROJECTED_SPAN_CANNOT_REPRESENT_NOMINAL")
        elif relative_error > 0.05:
            warnings.append("PROJECTED_SPAN_DISAGREES_WITH_NOMINAL")

    suggested_view = None
    all_geometry = rule.get("view_geometry") or {}
    if dim_type == "HOLE_PATTERN":
        nominal_data = rule.get("nominal") or {}
        if "pcd" not in nominal_data:
            errors.append("PATTERN_HAS_NO_PCD_SEMANTIC")
        face_scores: dict[str, float] = {}
        for candidate_view, candidate_payload in all_geometry.items():
            candidate_bbox = candidate_payload.get("projected_feature_bbox") or []
            if len(candidate_bbox) != 4:
                continue
            candidate_width = abs(float(candidate_bbox[2]) - float(candidate_bbox[0]))
            candidate_height = abs(float(candidate_bbox[3]) - float(candidate_bbox[1]))
            face_scores[str(candidate_view)] = min(candidate_width, candidate_height) / max(
                candidate_width, candidate_height, 1e-9
            )
        if face_scores:
            suggested_view = max(face_scores, key=face_scores.get)
            if face_scores.get(view_name, 0.0) < 0.35:
                errors.append("PATTERN_EDGE_ON_IN_SELECTED_VIEW")
    elif nominal > 1e-6 and dim_type in {"LINEAR", "DIAMETER"}:
        view_errors: dict[str, float] = {}
        for candidate_view, candidate_payload in all_geometry.items():
            candidate_start = candidate_payload.get("start_proj")
            candidate_end = candidate_payload.get("end_proj")
            if not (
                isinstance(candidate_start, Sequence) and len(candidate_start) >= 2
                and isinstance(candidate_end, Sequence) and len(candidate_end) >= 2
            ):
                continue
            span = math.dist(
                (float(candidate_start[0]), float(candidate_start[1])),
                (float(candidate_end[0]), float(candidate_end[1])),
            )
            view_errors[str(candidate_view)] = abs(span - nominal) / nominal
        if view_errors:
            best_view = min(view_errors, key=view_errors.get)
            if view_errors[best_view] <= 0.05:
                suggested_view = best_view

    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "payload": payload,
        "anchor_distance": round(anchor_distance, 6) if anchor_distance is not None else None,
        "nominal_relative_error": round(relative_error, 6) if relative_error is not None else None,
        "suggested_view": suggested_view,
    }


def _draw_selection_panel(
    canvas: Image.Image,
    panel: Sequence[int],
    view_name: str,
    view: dict[str, Any],
    rule: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = panel
    draw.rectangle(panel, fill=BACKGROUND, outline=GRID, width=2)
    status = validation["status"]
    status_color = PASS if status == "PASS" else WARN if status == "WARN" else FAIL
    draw.text((left + 18, top + 14), f"{rule.get('rule_id')} | {view_name.upper()} | {rule.get('side')}", font=_font(20, True), fill=WHITE)
    draw.text(
        (left + 18, top + 48),
        f"{rule.get('dim_type')}  nominal={float(rule.get('nominal_value') or 0):.3f} mm  validation={status}",
        font=_font(16, True), fill=status_color,
    )
    drawing_box = (left + 22, top + 90, right - 22, bottom - 170)
    mapper = _mapper(view["bbox"], drawing_box)
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=HIDDEN, width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=MODEL, width=2)

    payload = validation["payload"]
    bbox = payload.get("projected_feature_bbox")
    if isinstance(bbox, Sequence) and len(bbox) == 4:
        p0 = mapper((bbox[0], bbox[1]))
        p1 = mapper((bbox[2], bbox[3]))
        draw.rectangle((p0[0], p1[1], p1[0], p0[1]), outline=ORANGE, width=5)
    start, end = payload.get("start_proj"), payload.get("end_proj")
    if isinstance(start, Sequence) and len(start) >= 2:
        ps = mapper(start)
        draw.ellipse((ps[0] - 8, ps[1] - 8, ps[0] + 8, ps[1] + 8), fill=YELLOW)
        if isinstance(end, Sequence) and len(end) >= 2:
            pe = mapper(end)
            draw.line((ps, pe), fill=CYAN, width=7)
            draw.ellipse((pe[0] - 8, pe[1] - 8, pe[0] + 8, pe[1] + 8), fill=YELLOW)
    center = payload.get("center")
    if isinstance(center, Sequence) and len(center) >= 2:
        pc = mapper(center)
        draw.line((pc[0] - 10, pc[1], pc[0] + 10, pc[1]), fill=YELLOW, width=3)
        draw.line((pc[0], pc[1] - 10, pc[0], pc[1] + 10), fill=YELLOW, width=3)

    messages = validation["errors"] + validation["warnings"]
    message = "; ".join(messages) if messages else "Topology-derived anchors passed the contract."
    draw.text((left + 18, bottom - 132), message[:92], font=_font(15), fill=status_color)
    if validation["anchor_distance"] is not None:
        draw.text(
            (left + 18, bottom - 96),
            f"anchor span={validation['anchor_distance']:.3f} mm | orange=feature footprint | cyan=measurement anchors",
            font=_font(14), fill=MUTED,
        )


def _render_review_board(
    output: Path,
    case_name: str,
    selections: list[dict[str, Any]],
    view_data: dict[str, Any],
    rendered_png: Path | None,
) -> None:
    canvas = Image.new("RGB", (4200, 2200), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((40, 28), f"Engineer-selected feature render contract | {case_name}", font=_font(34, True), fill=WHITE)
    draw.text((40, 78), "Selection is fixed by JSON. No feature or view is automatically chosen in this experiment.", font=_font(19), fill=MUTED)
    count = max(len(selections), 1)
    panel_width = 2900 // count
    for index, item in enumerate(selections):
        left = 40 + index * panel_width
        panel = (left, 130, left + panel_width - 18, 2050)
        _draw_selection_panel(canvas, panel, item["view"], view_data[item["view"]], item["rule"], item["validation"])

    actual_box = (2990, 130, 4160, 2050)
    draw.rectangle(actual_box, fill=BACKGROUND, outline=GRID, width=2)
    draw.text((3010, 148), "ACTUAL SMART-RENDERER OUTPUT", font=_font(20, True), fill=WHITE)
    if rendered_png and rendered_png.exists():
        with Image.open(rendered_png) as source:
            image = source.convert("RGB")
        image.thumbnail((1110, 1780), Image.Resampling.LANCZOS)
        canvas.paste(image, (3020 + (1110 - image.width) // 2, 210 + (1780 - image.height) // 2))
    else:
        draw.text((3030, 250), "Renderer output unavailable; inspect report.json.", font=_font(18), fill=FAIL)
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    case_reports: list[dict[str, Any]] = []
    for case in config.get("cases") or []:
        name = str(case["name"])
        case_dir = output_dir / name
        case_dir.mkdir(parents=True, exist_ok=True)
        step_path = _resolve_path(str(case["step_path"]))
        shape = load_step(str(step_path))
        view_names = sorted({str(item["view"]) for item in case.get("selections") or []} | {"front", "top", "right"})
        view_data = ViewProjector().project_all_views(shape, view_names=view_names)
        engine = SmartAnnotationEngine()
        rules = engine.get_candidate_rules(shape, view_data, part_hint=str(case.get("part_hint") or name))
        by_id = {str(rule.get("rule_id") or rule.get("id")): rule for rule in rules}
        configured: list[dict[str, Any]] = []
        reviewed: list[dict[str, Any]] = []
        missing: list[str] = []
        for selection in case.get("selections") or []:
            feature_id = str(selection["feature_id"])
            source_rule = by_id.get(feature_id)
            if source_rule is None:
                missing.append(feature_id)
                continue
            rule = copy.deepcopy(source_rule)
            view_name = str(selection["view"])
            side = str(selection.get("side") or rule.get("side") or "BOTTOM").upper()
            rule.update({
                "enabled": True,
                "target_views": [view_name],
                "views": [view_name],
                "preferred_view": view_name,
                "side": side,
                "sides": [side],
                "tolerance": str(selection.get("tolerance", rule.get("default_tolerance") or "")),
            })
            validation = validate_selection(rule, view_name, view_data[view_name])
            configured.append(rule)
            reviewed.append({"view": view_name, "rule": rule, "validation": validation})

        rendered_png: Path | None = case_dir / "01_selected_features_rendered.png"
        render_error = None
        try:
            engine.render_custom_drawing(
                dxf_path=str(case_dir / "01_selected_features_rendered.dxf"),
                pdf_path=str(case_dir / "01_selected_features_rendered.pdf"),
                png_path=str(rendered_png),
                feature_records=configured,
                view_data=view_data,
                title_info={"part_name": name, "drawing_no": f"EXP-{name}", "model_code": METHOD},
            )
        except Exception as exc:  # keep geometry evidence when export dependencies fail
            render_error = f"{type(exc).__name__}: {exc}"
            rendered_png = None

        review_board = case_dir / "00_selected_feature_contract_review.png"
        _render_review_board(review_board, name, reviewed, view_data, rendered_png)
        selection_results = [
            {
                "feature_id": item["rule"].get("rule_id"),
                "view": item["view"],
                "side": item["rule"].get("side"),
                "dim_type": item["rule"].get("dim_type"),
                "nominal_value": item["rule"].get("nominal_value"),
                "validation": {key: value for key, value in item["validation"].items() if key != "payload"},
            }
            for item in reviewed
        ]
        report = {
            "name": name,
            "step_path": str(step_path),
            "candidate_rule_count": len(rules),
            "requested_selection_count": len(case.get("selections") or []),
            "rendered_selection_count": len(configured),
            "missing_feature_ids": missing,
            "render_error": render_error,
            "selection_results": selection_results,
            "review_board": str(review_board),
            "rendered_png": str(rendered_png) if rendered_png else None,
        }
        (case_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        case_reports.append(report)

    all_results = [item for case in case_reports for item in case["selection_results"]]
    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "selection_authority": "EXPLICIT_ENGINEER_FIXTURE",
        "historical_drawing_used_for_geometry": False,
        "cases": case_reports,
        "metrics": {
            "case_count": len(case_reports),
            "selection_count": len(all_results),
            "pass": sum(item["validation"]["status"] == "PASS" for item in all_results),
            "warn": sum(item["validation"]["status"] == "WARN" for item in all_results),
            "fail": sum(item["validation"]["status"] == "FAIL" for item in all_results),
            "render_errors": sum(bool(case["render_error"]) for case in case_reports),
        },
    }
    (output_dir / "selected_feature_render_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
