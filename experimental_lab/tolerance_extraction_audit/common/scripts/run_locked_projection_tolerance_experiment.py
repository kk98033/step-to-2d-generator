"""Test tolerance extraction with PROJECTION HIT REVIEW as the locked parent pose.

The upstream STEP view, DXF view, rotation, reflection and scale are immutable.
An optional shaft-axis refinement may change translation only in the direction
perpendicular to the projected main axis.  This keeps the useful centre-axis
correction without allowing a new view search or tail-to-middle axial drift.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import numpy as np
import ezdxf
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
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
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from experimental_lab.tolerance_extraction_audit.common.scripts.generate_projection_hit_review import (
    BACKGROUND,
    _draw_overlay,
    _safe_name,
    _transform_from_dict,
)


def _balanced_arrays(engine, dxf_points, step_points):
    target = engine._prepare(dxf_points)
    source = engine._prepare(step_points)
    if len(target) > len(source):
        target = engine._farthest_point_sample(target, len(source))
    return target, source


def _registration_payload(
    engine: ProjectionRegistrationEngine,
    transform: SimilarityTransform2D,
    target: np.ndarray,
    source: np.ndarray,
) -> Dict[str, Any]:
    metrics = engine._distance_metrics(transform.apply_array(source), target)
    return {
        "method": engine.METHOD,
        "registration_method": engine.METHOD,
        "status": "REGISTERED",
        "registration_status": "REGISTERED",
        "score": round(float(metrics["score"]), 4),
        "contour_score": round(float(metrics["score"]), 4),
        "normalized_chamfer": round(float(metrics["normalized_chamfer"]), 6),
        "inlier_ratio": round(float(metrics["inlier_ratio"]), 4),
        "source_coverage": round(float(metrics["source_coverage"]), 4),
        "target_coverage": round(float(metrics["target_coverage"]), 4),
        "ranked_hausdorff": round(float(metrics["ranked_hausdorff"]), 6),
        "extent_score": round(float(metrics["extent_score"]), 4),
        "bbox_edge_error": round(float(metrics["bbox_edge_error"]), 6),
        "span_ratio": round(float(metrics["span_ratio"]), 4),
        "transform": transform.to_dict(),
        "rotation_degrees": round(float(transform.rotation_degrees), 4),
        "mirrored": bool(transform.mirrored),
        "scale": round(float(transform.scale), 8),
        "translation": [round(float(value), 6) for value in transform.translation],
        "dxf_point_count": int(len(target)),
        "step_point_count": int(len(source)),
    }


def _projected_axis(view: Dict[str, Any], main_axis: str) -> Tuple[float, float]:
    axis = {
        "x": (1.0, 0.0, 0.0),
        "y": (0.0, 1.0, 0.0),
        "z": (0.0, 0.0, 1.0),
    }.get(str(main_axis).lower(), (0.0, 0.0, 1.0))
    projection = dict(view.get("projection") or {})
    direction = tuple(float(value) for value in projection.get("direction", (0.0, 0.0, 1.0)))
    up = tuple(float(value) for value in projection.get("up", (0.0, 1.0, 0.0)))
    horizontal = (
        direction[1] * up[2] - direction[2] * up[1],
        direction[2] * up[0] - direction[0] * up[2],
        direction[0] * up[1] - direction[1] * up[0],
    )
    return (
        sum(axis[index] * up[index] for index in range(3)),
        sum(axis[index] * horizontal[index] for index in range(3)),
    )


def _transform_direction(vector, transform: SimilarityTransform2D) -> Tuple[float, float]:
    x, y = float(vector[0]), float(vector[1])
    if transform.mirrored:
        x = -x
    radians = math.radians(transform.rotation_degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    return x * cosine - y * sine, x * sine + y * cosine


def _refine_cross_axis_translation(
    engine: ProjectionRegistrationEngine,
    base: SimilarityTransform2D,
    target: np.ndarray,
    source: np.ndarray,
    projected_axis,
) -> Tuple[SimilarityTransform2D, Dict[str, Any]]:
    axis = _transform_direction(projected_axis, base)
    axis_length = math.hypot(*axis)
    if axis_length <= 0.15:
        return base, {
            "status": "SKIPPED_AXIS_NORMAL_TO_VIEW",
            "cross_axis_shift": 0.0,
            "along_axis_shift": 0.0,
        }
    axis = (axis[0] / axis_length, axis[1] / axis_length)
    perpendicular = (-axis[1], axis[0])
    target_span = np.ptp(target, axis=0)
    maximum_shift = 0.03 * max(float(np.linalg.norm(target_span)), 1e-9)
    base_metrics = engine._distance_metrics(base.apply_array(source), target)
    best_transform = base
    best_metrics = base_metrics
    best_shift = 0.0

    low, high = -maximum_shift, maximum_shift
    for _pass in range(3):
        for shift in np.linspace(low, high, 41):
            candidate = SimilarityTransform2D(
                scale=base.scale,
                rotation_degrees=base.rotation_degrees,
                translation=(
                    base.translation[0] + float(shift) * perpendicular[0],
                    base.translation[1] + float(shift) * perpendicular[1],
                ),
                mirrored=base.mirrored,
            )
            metrics = engine._distance_metrics(candidate.apply_array(source), target)
            if float(metrics["score"]) > float(best_metrics["score"]):
                best_transform, best_metrics, best_shift = candidate, metrics, float(shift)
        radius = (high - low) / 10.0
        low, high = best_shift - radius, best_shift + radius

    improved = float(best_metrics["score"]) >= float(base_metrics["score"]) + 0.001
    if not improved:
        best_transform, best_metrics, best_shift = base, base_metrics, 0.0
    return best_transform, {
        "status": "APPLIED" if improved else "UNCHANGED_NO_SCORE_GAIN",
        "method": "LOCKED_POSE_CROSS_AXIS_TRANSLATION_V1",
        "maximum_shift": round(maximum_shift, 6),
        "cross_axis_shift": round(best_shift, 6),
        "along_axis_shift": 0.0,
        "rotation_change_degrees": 0.0,
        "scale_change": 0.0,
        "mirror_changed": False,
        "score_before": round(float(base_metrics["score"]), 4),
        "score_after": round(float(best_metrics["score"]), 4),
    }


def _verify(
    ingestor,
    component,
    dxf_path: Path,
    dimensions: Iterable[ExtractedDimension],
    registration: Dict[str, Any],
):
    view_extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    evidence = {
        "pair_id": f"locked-review:{component.name}:{dxf_path.name}",
        "pair_method": "PROJECTION_HIT_REVIEW_LOCKED_V1",
        "component_label_entry": component.label_entry,
        "component_name": component.name,
        "matched_dxf_view_ids": [registration["dxf_view_id"]],
        "matched_step_view": registration["step_view"],
        "locked_view_registration": registration,
        "allow_secondary_view_search": True,
        # Reuse the exact multiscale DXF regions from PROJECTION HIT REVIEW.
        # The tolerance layer may choose another region for a dimension, but
        # it does not invent a second view-segmentation algorithm.
        "secondary_dxf_regions": [
            {
                "view_id": candidate.view_id,
                "bbox": list(candidate.bbox),
                "points": [list(point) for point in candidate.points],
                "primitive_count": candidate.primitive_count,
                "source": candidate.source,
            }
            for candidate in view_extractor.view_candidates
        ],
        "verification_checks": [
            "projection_hit_review_parent_pose_locked",
            "cross_axis_translation_only",
        ],
    }
    cases_with_keys, stats = ingestor._verify_pair(
        component.step_path,
        str(dxf_path),
        list(dimensions),
        pair_evidence=evidence,
        collect_diagnostics=True,
    )
    return [case for case, _keys in cases_with_keys], stats


def _oblique_directions():
    """Small deterministic isometric grid for non-orthographic assembly views."""
    directions = []
    for z in (-0.5, 0.5):
        radial = math.sqrt(1.0 - z * z)
        for azimuth in range(0, 360, 45):
            radians = math.radians(azimuth)
            directions.append((
                radial * math.cos(radians),
                radial * math.sin(radians),
                z,
            ))
    return directions


def run(config_path: Path, hit_summary_path: Path, output_dir: Path) -> Dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    hit_summary = json.loads(hit_summary_path.read_text(encoding="utf-8"))
    expected = dict(config.get("expected_dxf_by_component") or {})
    dxf_dir = Path(config["dxf_dir"]).resolve()
    components = {
        component.name: component
        for step_path in config.get("step_paths") or []
        for component in extract_leaf_components(str(Path(step_path).resolve()))
        if component.name in expected
    }
    hit_rows = {row["component_name"]: row for row in hit_summary.get("components") or []}
    output_dir.mkdir(parents=True, exist_ok=True)
    searcher = GlobalGeometryPairSearcher()
    metric_engine = ProjectionRegistrationEngine(max_points=320)
    ingestor = HistoricalDataIngestor()
    results = []
    panels = []

    for component_name, drawing_name in expected.items():
        component = components[component_name]
        dxf_path = (dxf_dir / drawing_name).resolve()
        hit = hit_rows[component_name]["expected_drawing_best_hit"]
        records, errors = searcher.build_dxf_index([str(dxf_path)])
        record = next(item for item in records if item.view_id == hit["dxf_view"])
        analyzer = DxfStructure2DAnalyzer(ezdxf.readfile(dxf_path).modelspace())
        cluster = next(item for item in analyzer.view_clusters if item.view_id == hit["dxf_view"])
        dxf_points = searcher._dxf_cluster_points(analyzer, cluster)
        extra_directions = (
            _oblique_directions()
            if component_name == "AL0W_BLADE_H-R01"
            else []
        )
        step_views = AdaptiveProjectionCandidateGenerator(
            max_views=6 + len(extra_directions)
        ).project_all(component.shape, extra_directions=extra_directions)
        step_view = step_views[hit["step_view"]]
        step_points = searcher._step_view_points(step_view)
        target, source = _balanced_arrays(metric_engine, dxf_points, step_points)
        base_transform = _transform_from_dict(hit["registration"]["transform"])
        graph = ingestor.frg_extractor.build_graph(component.shape)
        base_registration = {
            **_registration_payload(metric_engine, base_transform, target, source),
            "dxf_view_id": hit["dxf_view"],
            "step_view": hit["step_view"],
            "dxf_bbox": [round(float(value), 4) for value in cluster.bbox],
            "step_bbox": [round(float(value), 4) for value in step_view.get("bbox") or ()],
            "projection": dict(step_view.get("projection") or {}),
            "lock_source": "PROJECTION_HIT_REVIEW_EXPECTED_DRAWING_BEST_HIT",
        }

        _source, payloads = _extract_dxf_worker(str(dxf_path))
        dimensions = [ExtractedDimension(**item) for item in payloads]
        dimensions, suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
        dimensions = [item for item in dimensions if HistoricalDataIngestor._tolerance_is_plausible(item)]
        base_cases, base_stats = _verify(ingestor, component, dxf_path, dimensions, base_registration)

        pose_candidates = []
        candidate_view_names = [hit["step_view"]]
        candidate_view_names.extend(
            name for name, view in step_views.items()
            if view.get("projection", {}).get("source") == "requested_extra_direction"
        )
        for candidate_view_name in candidate_view_names:
            candidate_view = step_views[candidate_view_name]
            candidate_points = searcher._step_view_points(candidate_view)
            candidate_target, candidate_source = _balanced_arrays(
                metric_engine, dxf_points, candidate_points
            )
            registration = metric_engine.register(dxf_points, candidate_points)
            for alternative in (registration.get("alternatives") or [])[:4]:
                transform = SimilarityTransform2D(
                    scale=float(alternative["scale"]),
                    rotation_degrees=float(alternative["rotation_degrees"]),
                    translation=tuple(alternative["translation"]),
                    mirrored=bool(alternative.get("mirrored", False)),
                )
                payload = {
                    **_registration_payload(
                        metric_engine, transform, candidate_target, candidate_source
                    ),
                    "dxf_view_id": hit["dxf_view"],
                    "step_view": candidate_view_name,
                    "dxf_bbox": base_registration["dxf_bbox"],
                    "step_bbox": [
                        round(float(value), 4)
                        for value in candidate_view.get("bbox") or ()
                    ],
                    "projection": dict(candidate_view.get("projection") or {}),
                    "lock_source": "PROJECTION_HIT_REVIEW_DXF_REGION",
                }
                if str(graph.part_type).upper() == "SHAFT":
                    cases, stats = _verify(
                        ingestor, component, dxf_path, dimensions, payload
                    )
                else:
                    cases, stats = [], {}
                pose_candidates.append({
                    "view_name": candidate_view_name,
                    "view": candidate_view,
                    "points": candidate_points,
                    "target": candidate_target,
                    "source": candidate_source,
                    "transform": transform,
                    "registration": payload,
                    "cases": cases,
                    "stats": stats,
                    "quality_gate": bool(
                        float(payload.get("score", 0.0)) >= 0.72
                        and float(payload.get("extent_score", 0.0)) >= 0.72
                        and float(payload.get("target_coverage", 0.0)) >= 0.65
                    ),
                })
        pose_candidates.sort(
            key=lambda item: (
                len(item["cases"]),
                int(item["quality_gate"]),
                float(item["registration"].get("score", 0.0)),
                float(item["registration"].get("extent_score", 0.0)),
            ),
            reverse=True,
        )
        selected_pose = pose_candidates[0]
        selected_transform = selected_pose["transform"]
        selected_view = selected_pose["view"]
        if str(graph.part_type).upper() == "SHAFT":
            refined_transform, refinement = _refine_cross_axis_translation(
                metric_engine,
                selected_transform,
                selected_pose["target"],
                selected_pose["source"],
                _projected_axis(selected_view, graph.main_axis),
            )
        else:
            refined_transform = selected_transform
            refinement = {
                "status": "SKIPPED_NON_SHAFT_COMPONENT",
                "method": "LOCKED_POSE_CROSS_AXIS_TRANSLATION_V1",
                "cross_axis_shift": 0.0,
                "along_axis_shift": 0.0,
                "rotation_change_degrees": 0.0,
                "scale_change": 0.0,
                "mirror_changed": False,
            }
        refined_registration = {
            **_registration_payload(
                metric_engine,
                refined_transform,
                selected_pose["target"],
                selected_pose["source"],
            ),
            "dxf_view_id": hit["dxf_view"],
            "step_view": selected_pose["view_name"],
            "dxf_bbox": base_registration["dxf_bbox"],
            "step_bbox": selected_pose["registration"]["step_bbox"],
            "projection": selected_pose["registration"]["projection"],
            "lock_source": "PROJECTION_HIT_REVIEW_DXF_REGION",
            "axis_refinement": refinement,
        }
        refined_cases, refined_stats = _verify(ingestor, component, dxf_path, dimensions, refined_registration)

        stem = _safe_name(component_name)
        base_png = output_dir / f"{stem}_01_locked_review_pose.png"
        refined_png = output_dir / f"{stem}_02_axis_refined.png"
        _draw_overlay(
            base_png, component_name, "LOCKED REVIEW POSE", drawing_name,
            hit["step_view"], hit["dxf_view"], base_registration,
            dxf_points, base_transform.apply(step_points),
        )
        _draw_overlay(
            refined_png, component_name, "REGION LOCKED + POSE DISAMBIGUATION", drawing_name,
            selected_pose["view_name"], hit["dxf_view"], refined_registration,
            dxf_points, refined_transform.apply(selected_pose["points"]),
        )
        panels.append((base_png, refined_png))
        results.append({
            "component_name": component_name,
            "drawing": drawing_name,
            "dxf_index_errors": errors,
            "upstream_lock": {
                "source_summary": str(hit_summary_path.resolve()),
                "dxf_view": hit["dxf_view"],
                "step_view": hit["step_view"],
                "rotation_degrees": base_transform.rotation_degrees,
                "mirrored": base_transform.mirrored,
                "scale": base_transform.scale,
            },
            "axis_refinement": refinement,
            "pose_disambiguation": {
                "method": "LOCKED_DXF_REGION_TOLERANCE_SEMANTIC_POSE_SELECTION_V1",
                "candidate_count": len(pose_candidates),
                "selected_step_view": selected_pose["view_name"],
                "selected_projection_direction": refined_registration["projection"].get("direction"),
                "selected_rotation_degrees": refined_transform.rotation_degrees,
                "selected_mirrored": refined_transform.mirrored,
                "selected_geometry_score": refined_registration["score"],
                "selected_verified_tolerance_count": len(refined_cases),
            },
            "plausible_dimension_count": len(dimensions),
            "suppressed_duplicate_count": int(suppressed),
            "locked_pose_verified_count": len(base_cases),
            "axis_refined_verified_count": len(refined_cases),
            "locked_pose_feature_ids": [case.source_metadata.get("matched_feature_id") for case in base_cases],
            "axis_refined_feature_ids": [case.source_metadata.get("matched_feature_id") for case in refined_cases],
            "locked_pose_stats": {key: value for key, value in base_stats.items() if not key.startswith("_")},
            "axis_refined_stats": {key: value for key, value in refined_stats.items() if not key.startswith("_")},
            "locked_pose_overlay": base_png.name,
            "axis_refined_overlay": refined_png.name,
        })

    panel_width, panel_height = 1100, 760
    contact = Image.new("RGB", (panel_width * 2, panel_height * len(panels)), BACKGROUND)
    for row, (base_png, refined_png) in enumerate(panels):
        with Image.open(base_png) as base_image, Image.open(refined_png) as refined_image:
            contact.paste(base_image, (0, row * panel_height))
            contact.paste(refined_image, (panel_width, row * panel_height))
    contact_path = output_dir / "00_locked_projection_axis_refinement_contact_sheet.png"
    contact.save(contact_path)
    report = {
        "schema_version": 1,
        "method": "PROJECTION_HIT_REVIEW_LOCKED_WITH_CROSS_AXIS_REFINEMENT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "web_ui_updated": False,
        "invariants": {
            "component_locked": True,
            "drawing_file_locked": True,
            "dxf_view_locked": True,
            "table_or_other_region_search_disabled": True,
            "pose_disambiguation_confined_to_locked_dxf_view": True,
            "axis_refinement_along_axis_translation_locked": True,
            "cross_axis_translation_limit_ratio": 0.03,
        },
        "components": results,
        "contact_sheet": contact_path.name,
    }
    (output_dir / "locked_projection_tolerance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Locked Projection Tolerance Experiment\n\n"
        "- Left: the exact parent pose selected by PROJECTION HIT REVIEW.\n"
        "- Right: pose disambiguation inside the same locked DXF view, followed by bounded shaft cross-axis refinement.\n"
        "- Component, drawing and DXF region are locked; no other drawing region may be searched.\n"
        "- Tolerance anchors may resolve left/right symmetry, and non-orthographic assembly views may use oblique HLR candidates.\n"
        "- This experiment does not feed the web UI or production case database.\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--hit-summary", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.config.resolve(), args.hit_summary.resolve(), args.output_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
