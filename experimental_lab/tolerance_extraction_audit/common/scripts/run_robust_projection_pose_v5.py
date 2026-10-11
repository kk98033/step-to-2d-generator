"""Resolve projection pose without component-specific direction rules.

The component, drawing and local DXF region come from projection_pairing_v2.
Inside that immutable region every generated STEP view, rotation and reflection
competes.  Close hypotheses remain ambiguous instead of being auto-accepted.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)
from auto_2d_drawing.tolerance.projection_registration_v2 import (
    AdaptiveProjectionCandidateGenerator,
    ProjectionRegistrationEngine,
    SimilarityTransform2D,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2, DxfViewCandidate
from generate_locked_projection_review_v3 import (
    BACKGROUND, BLUE, GREEN, MUTED, ORANGE, PANEL, WHITE,
    _fit, _font, _matching_hit, _safe_name,
)
from run_locked_projection_tolerance_experiment import (
    _balanced_arrays, _oblique_directions, _registration_payload, _verify,
)
from run_projection_pairing_v2 import render_overlay


RED = "#ef4444"
YELLOW = "#facc15"


def _intersection_area(left, right):
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _candidate_and_parent(dxf_path: Path, view_id: str):
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    analyzer = DxfStructure2DAnalyzer(extractor.document.modelspace())
    helper = GlobalGeometryPairSearcher(maximum_record_points=256)
    if view_id.startswith("legacy_"):
        cluster_id = view_id.removeprefix("legacy_")
        cluster = next(item for item in analyzer.view_clusters if item.view_id == cluster_id)
        points = helper._reduce_points(helper._dxf_cluster_points(analyzer, cluster))
        primitives = [analyzer.primitives[index] for index in cluster.primitive_indexes]
        candidate = DxfViewCandidate(
            view_id=view_id,
            source_path=str(dxf_path),
            source="legacy_insert_or_path_cluster",
            primitive_indexes=[],
            points=points,
            bbox=cluster.bbox,
            primitive_count=len(primitives),
            curved_primitive_count=sum(
                item.entity_type in {"ARC", "CIRCLE"} for item in primitives
            ),
            layer_count=len({item.layer for item in primitives}),
        )
        return candidate, cluster_id

    candidate = next(item for item in extractor.view_candidates if item.view_id == view_id)
    # V2 may split one old large cluster into a precise local window.  The
    # parent is used only for dimension semantics, never for registration.
    ranked = []
    cx = (candidate.bbox[0] + candidate.bbox[2]) / 2.0
    cy = (candidate.bbox[1] + candidate.bbox[3]) / 2.0
    for cluster in analyzer.view_clusters:
        area = _intersection_area(candidate.bbox, cluster.bbox)
        contains = cluster.bbox[0] <= cx <= cluster.bbox[2] and cluster.bbox[1] <= cy <= cluster.bbox[3]
        ranked.append((int(contains), area, cluster.view_id))
    ranked.sort(reverse=True)
    parent = ranked[0][2] if ranked and (ranked[0][0] or ranked[0][1] > 0.0) else None
    return candidate, parent


def _geometry_key(payload):
    coverage = min(
        float(payload.get("source_coverage", 0.0)),
        float(payload.get("target_coverage", 0.0)),
    )
    robust = (
        0.62 * float(payload.get("score", 0.0))
        + 0.20 * coverage
        + 0.18 * float(payload.get("extent_score", 0.0))
    )
    return robust, coverage


def _overlay_distinct(left, right, engine):
    """Treat symmetry-equivalent 3D poses as one observable 2D result."""
    first = left["transform"].apply_array(np.asarray(left["step_points"], dtype=float))
    second = right["transform"].apply_array(np.asarray(right["step_points"], dtype=float))
    first = engine._farthest_point_sample(first, min(len(first), 256))
    second = engine._farthest_point_sample(second, min(len(second), 256))
    if len(first) > len(second):
        first = engine._farthest_point_sample(first, len(second))
    elif len(second) > len(first):
        second = engine._farthest_point_sample(second, len(first))
    metrics = engine._distance_metrics(first, second)
    return not (
        float(metrics.get("score", 0.0)) >= 0.985
        and float(metrics.get("extent_score", 0.0)) >= 0.985
    )


def _resolve_pose(component, dxf_path: Path, hit: dict, overlay_path: Path):
    candidate, verifier_view_id = _candidate_and_parent(dxf_path, str(hit["dxf_view"]))
    helper = GlobalGeometryPairSearcher(maximum_record_points=256)
    ingestor = HistoricalDataIngestor()
    graph = ingestor.frg_extractor.build_graph(component.shape)
    step_views = AdaptiveProjectionCandidateGenerator(max_views=32).project_all(
        component.shape,
        feature_nodes=graph.nodes,
        include_feature_axes=True,
        extra_directions=_oblique_directions(),
    )
    engine = ProjectionRegistrationEngine(max_points=320)
    hypotheses = []
    for step_view_name, step_view in step_views.items():
        step_points = helper._reduce_points(helper._step_view_points(step_view))
        if len(step_points) < 3:
            continue
        registration = engine.register(candidate.points, step_points)
        target, source = _balanced_arrays(engine, candidate.points, step_points)
        for alternative in (registration.get("alternatives") or [])[:8]:
            transform = SimilarityTransform2D(
                scale=float(alternative["scale"]),
                rotation_degrees=float(alternative["rotation_degrees"]),
                translation=tuple(alternative["translation"]),
                mirrored=bool(alternative.get("mirrored", False)),
            )
            payload = {
                **_registration_payload(engine, transform, target, source),
                "dxf_view_id": verifier_view_id,
                "step_view": step_view_name,
                "dxf_bbox": [round(float(value), 4) for value in candidate.bbox],
                "step_bbox": [round(float(value), 4) for value in step_view.get("bbox") or ()],
                "projection": dict(step_view.get("projection") or {}),
                "lock_source": "PROJECTION_PAIRING_V2_LOCAL_REGION_ROBUST_POSE_V5",
            }
            robust_score, coverage = _geometry_key(payload)
            hypotheses.append({
                "step_view": step_view_name,
                "step_view_payload": step_view,
                "step_points": step_points,
                "transform": transform,
                "registration": payload,
                "geometry_score": robust_score,
                "coverage": coverage,
                "verified_count": 0,
                "feature_ids": [],
            })
    hypotheses.sort(key=lambda item: item["geometry_score"], reverse=True)
    if not hypotheses:
        raise RuntimeError(f"No registration hypotheses for {component.name}")

    _source, payloads = _extract_dxf_worker(str(dxf_path))
    dimensions = [ExtractedDimension(**item) for item in payloads]
    dimensions, suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
    dimensions = [item for item in dimensions if HistoricalDataIngestor._tolerance_is_plausible(item)]

    best_geometry = hypotheses[0]["geometry_score"]
    semantic_shortlist = [
        item for item in hypotheses
        if item["geometry_score"] >= best_geometry - 0.055
    ][:10]
    if verifier_view_id:
        for item in semantic_shortlist:
            cases, _stats = _verify(
                ingestor, component, dxf_path, dimensions, item["registration"]
            )
            item["verified_count"] = len(cases)
            item["feature_ids"] = [
                case.source_metadata.get("matched_feature_id") for case in cases
            ]
    admissible = [item for item in hypotheses if item["geometry_score"] >= best_geometry - 0.055]
    admissible.sort(
        key=lambda item: (item["verified_count"], item["geometry_score"]),
        reverse=True,
    )
    selected = admissible[0]
    distinct_runner = next(
        (item for item in admissible[1:] if _overlay_distinct(selected, item, engine)),
        None,
    )
    margin = (
        selected["geometry_score"] - distinct_runner["geometry_score"]
        if distinct_runner is not None else 1.0
    )
    semantic_margin = (
        selected["verified_count"] - distinct_runner["verified_count"]
        if distinct_runner is not None else selected["verified_count"]
    )
    payload = selected["registration"]
    quality_gate = (
        float(payload.get("score", 0.0)) >= 0.72
        and float(payload.get("extent_score", 0.0)) >= 0.68
        and selected["coverage"] >= 0.58
    )
    status = (
        "AUTO_SELECTED"
        if quality_gate and (semantic_margin > 0 or margin >= 0.025)
        else "AMBIGUOUS_REVIEW"
    )
    render_overlay(
        candidate,
        selected["transform"].apply(selected["step_points"]),
        payload,
        overlay_path,
    )
    def summarize(item):
        record = item["registration"]
        return {
            "step_view": item["step_view"],
            "projection_direction": record.get("projection", {}).get("direction"),
            "rotation_degrees": record["rotation_degrees"],
            "mirrored": record["mirrored"],
            "scale": record["scale"],
            "contour_score": record["score"],
            "extent_score": record["extent_score"],
            "coverage": round(item["coverage"], 4),
            "robust_geometry_score": round(item["geometry_score"], 6),
            "verified_tolerance_count": item["verified_count"],
            "feature_ids": item["feature_ids"],
        }
    top = [summarize(item) for item in admissible[:8]]
    return {
        "method": "LOCKED_LOCAL_REGION_ALL_DIRECTION_POSE_SEARCH_V5",
        "status": status,
        "verifier_view_id": verifier_view_id,
        "generated_step_view_count": len(step_views),
        "hypothesis_count": len(hypotheses),
        "semantic_shortlist_count": len(semantic_shortlist),
        "plausible_dimension_count": len(dimensions),
        "suppressed_duplicate_count": int(suppressed),
        "selected": top[0],
        "distinct_runner_up": summarize(distinct_runner) if distinct_runner is not None else None,
        "geometry_margin": round(float(margin), 6),
        "semantic_margin": int(semantic_margin),
        "quality_gate": quality_gate,
        "top_hypotheses": top,
        "locked_registration": payload,
        "overlay_path": str(overlay_path.resolve()),
    }


def _render_board(component: dict, hit: dict, source_root: Path, overlay_path: Path, result, output: Path):
    relative_board = Path(str(hit["review_board"]).replace("\\", "/"))
    original_path = (source_root / relative_board).parent / "02_original_dxf_and_hit_region.png"
    width, height = 2200, 930
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=16, fill=PANEL)
    status_color = GREEN if result["status"] == "AUTO_SELECTED" else YELLOW
    draw.text((42, 28), f"{component['component_name']} | {result['status']}", font=_font(30), fill=status_color)
    selected = result["selected"]
    draw.text(
        (42, 72),
        f"DXF={hit['dxf']}  locked region={hit['dxf_view']}  selected STEP={selected['step_view']}  "
        f"rotation={selected['rotation_degrees']:.2f}  mirrored={selected['mirrored']}",
        font=_font(18), fill=MUTED,
    )
    draw.text(
        (42, 100),
        f"geometry={selected['robust_geometry_score']:.4f}  geometry margin={result['geometry_margin']:.4f}  "
        f"verified tolerances={selected['verified_tolerance_count']}  semantic margin={result['semantic_margin']}",
        font=_font(18), fill=WHITE,
    )
    draw.text((42, 140), "AUTOMATIC ALL-DIRECTION POSE", font=_font(22), fill=WHITE)
    draw.text((1120, 140), "ORIGINAL 2D DRAWING REVIEW", font=_font(22), fill=WHITE)
    with Image.open(overlay_path) as overlay_source, Image.open(original_path) as original_source:
        overlay = _fit(overlay_source, (1030, 690))
        original = _fit(original_source, (1030, 690))
    boxes = ((35, 178, 1085, 878), (1115, 178, 2165, 878))
    for box in boxes:
        draw.rectangle(box, fill=BACKGROUND, outline="#404040", width=2)
    for fitted, box in ((overlay, boxes[0]), (original, boxes[1])):
        image.paste(fitted, (box[0] + (box[2]-box[0]-fitted.width)//2, box[1] + (box[3]-box[1]-fitted.height)//2))
    draw.rectangle((52, 845, 70, 863), fill=BLUE)
    draw.text((78, 841), "DXF local geometry", font=_font(16), fill=WHITE)
    draw.rectangle((300, 845, 318, 863), fill=ORANGE)
    draw.text((326, 841), "selected STEP HLR", font=_font(16), fill=WHITE)
    image.save(output)
    return str(original_path.resolve())


def run(config_path: Path, report_path: Path, output_dir: Path):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source_root = report_path.parent
    expected = dict(config.get("expected_dxf_by_component") or {})
    components = {
        item.name: item
        for step_path in config.get("step_paths") or []
        for item in extract_leaf_components(str(Path(step_path).resolve()))
        if item.name in expected
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = output_dir / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    rows, boards = [], []
    for row in report.get("components") or []:
        name = str(row["component_name"])
        hit = _matching_hit(row)
        overlay = artifact_dir / f"{_safe_name(name)}_robust_pose_overlay.png"
        dxf_path = Path(config["dxf_dir"]).resolve() / str(hit["dxf"])
        result = _resolve_pose(components[name], dxf_path, hit, overlay)
        board = output_dir / f"{_safe_name(name)}_robust_pose_with_original.png"
        original = _render_board(row, hit, source_root, overlay, result, board)
        boards.append(board)
        rows.append({
            "component_name": name,
            "drawing": hit["dxf"],
            "locked_dxf_view": hit["dxf_view"],
            "pose_search": result,
            "source_original_2d_review": original,
            "review_board": board.name,
        })
    contact = Image.new("RGB", (2200, 930 * len(boards)), BACKGROUND)
    for index, board_path in enumerate(boards):
        with Image.open(board_path) as board:
            contact.paste(board.convert("RGB"), (0, 930 * index))
    contact_path = output_dir / "00_robust_projection_pose_contact_sheet.png"
    contact.save(contact_path)
    payload = {
        "schema_version": 1,
        "method": "LOCKED_DXF_REGION_ALL_DIRECTION_POSE_SEARCH_V5",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "web_ui_updated": False,
        "component_specific_rules": False,
        "ambiguity_is_not_auto_verified": True,
        "components": rows,
        "contact_sheet": contact_path.name,
    }
    (output_dir / "robust_projection_pose_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Robust projection pose V5\n\n"
        "The projection_pairing_v2 component, drawing and local DXF region are immutable. "
        "Canonical, feature-axis and deterministic oblique STEP views compete with all "
        "registration rotations/reflections. Close distinct poses remain AMBIGUOUS_REVIEW. "
        "No component-name special case and no web/database update are used.\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--pairing-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    payload = run(args.config.resolve(), args.pairing_report.resolve(), args.output_dir.resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
