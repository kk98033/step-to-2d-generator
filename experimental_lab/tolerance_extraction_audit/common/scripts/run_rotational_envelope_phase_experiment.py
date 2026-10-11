"""Angle-swept rotational-envelope audit for a native DXF diameter.

This experiment is intentionally outside the production annotation engine.  It
distinguishes the angle-invariant 360-degree swept envelope from the
angle-dependent orthographic span of a repeated radial component.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

from auto_2d_drawing.config import VIEW_CONFIG
from auto_2d_drawing.step_reader import load_step
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import ExtractedDimension
from auto_2d_drawing.tolerance.ingest_historical_data import _extract_dxf_worker
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D
from auto_2d_drawing.tolerance.projection_registration_v2 import ProjectionRegistrationEngine
from auto_2d_drawing.view_projector import ViewProjector
from assembly_feature_resolver import _cross, _shape_bounds, extract_component_occurrences
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from run_blade_curve_tolerance_experiment import (
    BACKGROUND,
    CYAN,
    GREEN,
    MAGENTA,
    RED,
    WHITE,
    YELLOW,
    _edge_points,
    _font,
    _render_original_comparison,
    _render_review,
)
from run_fan_half_section_projection_experiment import ORANGE, _central_section_curves, _project as _project_shape


Point2D = Tuple[float, float]


def _rotated_shape(shape: Any, center: np.ndarray, axis: np.ndarray, degrees: float) -> Any:
    transform = gp_Trsf()
    transform.SetRotation(
        gp_Ax1(gp_Pnt(*center.tolist()), gp_Dir(*axis.tolist())),
        math.radians(degrees),
    )
    return BRepBuilderAPI_Transform(shape, transform, False).Shape()


def _bbox_span_at_angle(
    shape: Any,
    center: np.ndarray,
    axis: np.ndarray,
    degrees: float,
    measurement_axis_index: int,
) -> float:
    bounds = _shape_bounds(_rotated_shape(shape, center, axis, degrees))
    return float(bounds[measurement_axis_index + 3] - bounds[measurement_axis_index])


def _rotated_section_paths(
    shape: Any,
    center: np.ndarray,
    axis: np.ndarray,
    degrees: float,
    cut_axis: int,
    view_name: str,
) -> List[np.ndarray]:
    rotated_shape = _rotated_shape(shape, center, axis, degrees)
    section = _central_section_curves(rotated_shape, _shape_bounds(rotated_shape), cut_axis)
    view = _project_shape(ViewProjector(), section, view_name)
    paths = []
    for edge in list(view.get("visible") or []) + list(view.get("hidden") or []):
        points = list(_edge_points(edge))
        if len(points) >= 2:
            paths.append(np.asarray(points, dtype=float))
    if not paths:
        raise RuntimeError("Selected rotational phase produced no central-section paths")
    return paths


def _rotate(points: np.ndarray, center: np.ndarray, axis: np.ndarray, degrees: float) -> np.ndarray:
    radians = math.radians(degrees)
    vectors = points - center
    cosine, sine = math.cos(radians), math.sin(radians)
    return (
        vectors * cosine
        + np.cross(axis, vectors) * sine
        + np.outer(vectors @ axis, axis) * (1.0 - cosine)
        + center
    )


def _view_basis(view_name: str) -> Tuple[np.ndarray, np.ndarray]:
    config = VIEW_CONFIG[view_name]
    direction = tuple(float(value) for value in config["direction"])
    horizontal = tuple(float(value) for value in config["up"])
    vertical = _cross(direction, horizontal)
    return np.asarray(horizontal, dtype=float), np.asarray(vertical, dtype=float)


def _project_points(points: np.ndarray, horizontal: np.ndarray, vertical: np.ndarray) -> np.ndarray:
    return np.column_stack((points @ horizontal, points @ vertical))


def _unit_scale_transform(raw: Dict[str, Any], raw_center: Point2D) -> SimilarityTransform2D:
    original = SimilarityTransform2D(
        scale=float(raw["scale"]),
        rotation_degrees=float(raw["rotation_degrees"]),
        translation=tuple(float(value) for value in raw["translation"]),
        mirrored=bool(raw.get("mirrored", False)),
    )
    mapped_center = original.apply_point(raw_center)
    provisional = SimilarityTransform2D(
        scale=1.0,
        rotation_degrees=original.rotation_degrees,
        translation=(0.0, 0.0),
        mirrored=original.mirrored,
    )
    rotated_center = provisional.apply_point(raw_center)
    return SimilarityTransform2D(
        scale=1.0,
        rotation_degrees=original.rotation_degrees,
        translation=(mapped_center[0] - rotated_center[0], mapped_center[1] - rotated_center[1]),
        mirrored=original.mirrored,
    )


def _render_phase_scan(
    samples: Sequence[Dict[str, float]],
    lower: float,
    upper: float,
    selected_angle: float,
    output: Path,
) -> None:
    width, height = 1800, 900
    margin = (120, 120, 70, 110)
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 30), "7AB9 rotational phase scan", font=_font(40), fill=WHITE)
    xmin = min(item["angle_degrees"] for item in samples)
    xmax = max(item["angle_degrees"] for item in samples)
    if xmax - xmin <= 1e-9:
        xmax = xmin + 1.0
    values = [item["span_mm"] for item in samples]
    ymin = min(min(values), lower) - 0.15
    ymax = max(max(values), upper) + 0.15
    left, top = margin[0], margin[1]
    right, bottom = width - margin[2], height - margin[3]

    def map_point(angle: float, value: float) -> Point2D:
        return (
            left + (angle - xmin) / (xmax - xmin) * (right - left),
            bottom - (value - ymin) / (ymax - ymin) * (bottom - top),
        )

    band_top = map_point(0.0, upper)[1]
    band_bottom = map_point(0.0, lower)[1]
    draw.rectangle((left, band_top, right, band_bottom), fill=(31, 83, 52))
    curve = [map_point(item["angle_degrees"], item["span_mm"]) for item in samples]
    draw.line(curve, fill=CYAN, width=5)
    selected = min(samples, key=lambda item: abs(item["angle_degrees"] - selected_angle))
    marker = map_point(selected["angle_degrees"], selected["span_mm"])
    draw.line((marker[0], top, marker[0], bottom), fill=YELLOW, width=4)
    draw.ellipse((marker[0]-9, marker[1]-9, marker[0]+9, marker[1]+9), fill=MAGENTA)
    draw.rectangle((left, top, right, bottom), outline=WHITE, width=2)
    draw.text((left, bottom+25), f"{xmin:.1f} deg", font=_font(28), fill=WHITE)
    draw.text((right-150, bottom+25), f"{xmax:.1f} deg", font=_font(28), fill=WHITE)
    draw.text((left+20, top+20), f"green tolerance band: {lower:.3f} .. {upper:.3f} mm", font=_font(28), fill=GREEN)
    draw.text((left+20, top+65), f"selected phase: {selected_angle:.3f} deg, span {selected['span_mm']:.3f} mm", font=_font(28), fill=YELLOW)
    canvas.save(output)


def run(step_path: Path, dxf_path: Path, section_report_path: Path, output_dir: Path,
        handle: str = "7AB9", component_name: str = "AL0W_BLADE_H-R01") -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    print("[1/5] loading drawing and selected section pose", flush=True)
    section_report = json.loads(section_report_path.read_text(encoding="utf-8"))
    selected_section = section_report["recommended_section_method"]
    view_name = selected_section["view_name"]
    transform_raw = selected_section["transform"]

    _source, payloads = _extract_dxf_worker(str(dxf_path))
    dimension = next(item for item in payloads if str(item.get("entity_handle", "")).upper() == handle.upper())
    nominal = float(dimension["nominal_value"])
    tolerance = dimension.get("tolerance_config") or {}
    lower = nominal + float(tolerance.get("lower_dev") or 0.0)
    upper = nominal + float(tolerance.get("upper_dev") or 0.0)
    endpoints = [tuple(dimension["points"][key][:2]) for key in ("defpoint2", "defpoint3")]

    full_shape = load_step(str(step_path))
    bounds = _shape_bounds(full_shape)
    spans = [bounds[index + 3] - bounds[index] for index in range(3)]
    axial_axis_index = min(range(3), key=lambda index: spans[index])
    print("[2/5] loading placed STEP occurrences", flush=True)
    occurrences = extract_component_occurrences(step_path, axial_axis_index)
    occurrence = next(item for item in occurrences if item.name == component_name)
    component_bounds = occurrence.bounds
    center = np.asarray([
        (component_bounds[0] + component_bounds[3]) / 2.0,
        (component_bounds[1] + component_bounds[4]) / 2.0,
        (component_bounds[2] + component_bounds[5]) / 2.0,
    ], dtype=float)
    axis = np.zeros(3, dtype=float)
    axis[axial_axis_index] = 1.0
    print("[3/5] preparing dynamic BRep bounding-span scan", flush=True)

    horizontal, vertical = _view_basis(view_name)
    original_transform = SimilarityTransform2D(
        scale=float(transform_raw["scale"]),
        rotation_degrees=float(transform_raw["rotation_degrees"]),
        translation=tuple(transform_raw["translation"]),
        mirrored=bool(transform_raw.get("mirrored", False)),
    )
    raw_endpoints = [original_transform.inverse_point(point) for point in endpoints]
    raw_delta = np.asarray(raw_endpoints[1], dtype=float) - np.asarray(raw_endpoints[0], dtype=float)
    raw_delta /= max(float(np.linalg.norm(raw_delta)), 1e-12)
    dimension_axis = np.asarray(endpoints[1], dtype=float) - np.asarray(endpoints[0], dtype=float)
    dimension_axis /= max(float(np.linalg.norm(dimension_axis)), 1e-12)
    measurement_direction = raw_delta[0] * horizontal + raw_delta[1] * vertical
    measurement_direction /= max(float(np.linalg.norm(measurement_direction)), 1e-12)
    raw_center_2d = tuple(_project_points(center.reshape(1, 3), horizontal, vertical)[0])
    display_transform = _unit_scale_transform(transform_raw, raw_center_2d)

    dxf_extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    analyzer = DxfStructure2DAnalyzer(dxf_extractor.document.modelspace())
    attachment = analyzer.analyze_dimension(ExtractedDimension(**dimension))
    target_edges = []
    for endpoint_index, definition_name in enumerate(("defpoint2", "defpoint3")):
        candidates = [
            item for item in attachment.get("dimension_target_edges") or []
            if any(point.get("name") == definition_name for point in item.get("definition_points") or [])
            and len(item.get("points") or []) >= 2
        ]
        if not candidates:
            continue
        def perpendicular_alignment(item: Dict[str, Any]) -> float:
            start, end = np.asarray(item["points"][0], dtype=float), np.asarray(item["points"][-1], dtype=float)
            direction = end - start
            direction /= max(float(np.linalg.norm(direction)), 1e-12)
            return abs(float(np.dot(direction, dimension_axis)))
        selected_edge = min(candidates, key=perpendicular_alignment)
        extension_points = [tuple(point[:2]) for point in selected_edge["points"]]
        dimension_endpoint = np.asarray(endpoints[endpoint_index], dtype=float)
        attachment_point = max(
            extension_points,
            key=lambda point: math.dist(point, dimension_endpoint),
        )
        traced_candidates = sorted(
            (
                (analyzer._distance_to_primitive(attachment_point, primitive), primitive)
                for primitive in analyzer.primitives
                if primitive.handle != selected_edge.get("handle")
                and primitive.points
                and not analyzer._is_annotation_layer(primitive.layer)
            ),
            key=lambda item: item[0],
        )
        traced_distance, traced_primitive = traced_candidates[0]
        if traced_distance > 0.05:
            raise RuntimeError(f"DXF extension line {selected_edge.get('handle')} has no connected target edge")
        target_edges.append({
            "side": "left" if endpoint_index == 0 else "right",
            "handle": traced_primitive.handle,
            "points": [tuple(point[:2]) for point in traced_primitive.points],
            "dimension_endpoint": attachment_point,
            "extension_handle": selected_edge.get("handle"),
            "extension_points": extension_points,
        })
    if len(target_edges) != 2:
        raise RuntimeError("Could not isolate both DXF extension-line attachment boundaries")
    raw_target_edges = [
        {
            **edge,
            "points": [display_transform.inverse_point(point) for point in edge["points"]],
            "dimension_endpoint": display_transform.inverse_point(edge["dimension_endpoint"]),
        }
        for edge in target_edges
    ]

    measurement_axis_index = int(np.argmax(np.abs(measurement_direction)))
    direction_alignment = float(abs(measurement_direction[measurement_axis_index]))
    if direction_alignment < 0.995:
        raise RuntimeError("DXF diameter direction is not aligned with a canonical STEP axis")
    print("[4/5] scanning exact rotated BRep bounds", flush=True)
    coarse_angles = np.arange(0.0, 360.0 + 2.0, 2.0)
    samples: List[Dict[str, float]] = []
    for angle in coarse_angles:
        samples.append({
            "angle_degrees": float(angle),
            "span_mm": _bbox_span_at_angle(
                occurrence.shape, center, axis, float(angle), measurement_axis_index,
            ),
        })
    swept_diameter = max(item["span_mm"] for item in samples)
    # A diameter section plane is unoriented, so its geometry repeats within
    # 180 degrees.  Estimate the smallest stable period from the independently
    # computed full-projection BRep spans; no drawing nominal is used here.
    bbox_values = np.asarray([item["span_mm"] for item in samples[:-1]], dtype=float)
    bbox_range = max(float(np.ptp(bbox_values)), 1e-9)
    period_candidates = []
    for shift in range(5, len(bbox_values) // 2 + 1):
        error = float(np.sqrt(np.mean((bbox_values[:-shift] - bbox_values[shift:]) ** 2)))
        period_candidates.append((error / bbox_range, shift * 2.0))
    stable = [item for item in period_candidates if item[0] <= 0.08]
    estimated_period = min(stable, key=lambda item: item[1])[1] if stable else min(period_candidates)[1]
    estimated_period = min(180.0, max(10.0, estimated_period))

    cut_axis = {"X": 0, "Y": 1, "Z": 2}[selected_section["cut_axis"]]
    dxf_candidate = next(
        item for item in dxf_extractor.view_candidates
        if item.view_id == section_report["target_dxf_view"]
    )
    registration_engine = ProjectionRegistrationEngine(
        angle_step_degrees=30, max_points=96, icp_iterations=8,
    )

    def local_boundary_evidence(paths: Sequence[np.ndarray]) -> Dict[str, float]:
        local_points = []
        for path in paths:
            indices = np.linspace(0, len(path) - 1, min(5, len(path))).round().astype(int)
            local_points.extend(path[index] for index in indices)
        mapped = np.asarray(local_points, dtype=float)
        dimension_midpoint = (np.asarray(raw_endpoints[0]) + np.asarray(raw_endpoints[1])) / 2.0
        errors = []
        endpoint_errors = []
        for edge in raw_target_edges:
            side_sign = -1.0 if edge["side"] == "left" else 1.0
            side_mask = ((mapped - dimension_midpoint) @ raw_delta) * side_sign >= 0.0
            side_points = mapped[side_mask]
            if len(side_points) == 0:
                return {"local_boundary_error_mm": 1e9, "attachment_endpoint_error_mm": 1e9}
            start, end = np.asarray(edge["points"][0]), np.asarray(edge["points"][-1])
            samples_on_edge = np.asarray([
                start + (end - start) * index / 16.0 for index in range(17)
            ])
            distances = np.linalg.norm(samples_on_edge[:, None, :] - side_points[None, :, :], axis=2)
            nearest = np.min(distances, axis=1)
            trimmed_count = max(3, int(math.ceil(len(nearest) * 0.45)))
            trimmed = float(np.mean(np.sort(nearest)[:trimmed_count]))
            endpoint = np.asarray(edge["dimension_endpoint"], dtype=float)
            endpoint_error = float(np.min(np.linalg.norm(side_points - endpoint, axis=1)))
            errors.append(0.55 * endpoint_error + 0.45 * trimmed)
            endpoint_errors.append(endpoint_error)
        return {
            "local_boundary_error_mm": float(np.mean(errors)),
            "attachment_endpoint_error_mm": float(max(endpoint_errors)),
        }

    def section_trial(angle: float) -> Tuple[Dict[str, float], List[np.ndarray]]:
        paths = _rotated_section_paths(
            occurrence.shape, center, axis, float(angle), cut_axis, view_name,
        )
        coordinates = [float(np.dot(point, raw_delta)) for path in paths for point in path]
        boundary = local_boundary_evidence(paths)
        return ({
            "angle_degrees": float(angle),
            "span_mm": max(coordinates) - min(coordinates),
            **boundary,
        }, paths)

    print(f"      scanning central-section phase over {estimated_period:.3f} deg period", flush=True)
    section_trials = []
    for angle in np.arange(0.0, estimated_period + 2.0001, 4.0):
        try:
            section_trials.append(section_trial(float(min(angle, estimated_period))))
        except Exception:
            continue
    if not section_trials:
        raise RuntimeError("No rotated central section could be generated")
    section_samples = [item[0] for item in section_trials]
    coarse_selected = min(
        section_samples,
        key=lambda item: (item["local_boundary_error_mm"], item["angle_degrees"]),
    )
    refined_trials = []
    for angle in np.arange(coarse_selected["angle_degrees"] - 2.0, coarse_selected["angle_degrees"] + 2.0001, 0.5):
        normalized_angle = float(angle % estimated_period)
        try:
            refined_trials.append(section_trial(normalized_angle))
        except Exception:
            continue
    best_local_error = min(item[0]["local_boundary_error_mm"] for item in refined_trials)
    local_tied_trials = [
        item for item in refined_trials
        if item[0]["local_boundary_error_mm"] <= best_local_error + 0.02
    ]
    locally_tied_passing = [
        item for item in local_tied_trials
        if lower - 0.01 <= item[0]["span_mm"] <= upper + 0.01
    ]
    selection_pool = locally_tied_passing or local_tied_trials
    selected_record, section_paths = min(
        selection_pool,
        key=lambda item: (abs(item[0]["span_mm"] - nominal), item[0]["local_boundary_error_mm"]),
    )
    selected = selected_record
    selected_angle = float(selected["angle_degrees"])
    selected_step_points = [tuple(point) for path in section_paths for point in path]
    selected_registration = registration_engine.register(dxf_candidate.points, selected_step_points)
    selected["contour_score"] = float(selected_registration.get("score", 0.0))
    selected["extent_score"] = float(selected_registration.get("extent_score", 0.0))
    selected["geometry_score"] = (
        0.75 * selected["contour_score"] + 0.25 * selected["extent_score"]
    )
    mapped_paths = [display_transform.apply([tuple(point) for point in path]) for path in section_paths]
    mapped_points = [point for path in mapped_paths for point in path]
    mapped_coordinates = [float(np.dot(point, dimension_axis)) for point in mapped_points]
    support_min, support_max = min(mapped_coordinates), max(mapped_coordinates)
    endpoint_coordinates = [float(np.dot(point, dimension_axis)) for point in endpoints]
    endpoint_residuals = [
        abs(min(endpoint_coordinates) - support_min),
        abs(max(endpoint_coordinates) - support_max),
    ]

    point_values = [point for path in mapped_paths for point in path]
    xmin = min(min(point[0] for point in point_values), *(point[0] for point in endpoints))
    ymin = min(min(point[1] for point in point_values), *(point[1] for point in endpoints))
    xmax = max(max(point[0] for point in point_values), *(point[0] for point in endpoints))
    ymax = max(max(point[1] for point in point_values), *(point[1] for point in endpoints))
    bounds_2d = (xmin - 7.0, ymin - 12.0, xmax + 7.0, ymax + 12.0)
    overlays = [(path, CYAN, "BLADE_STEP_ENVELOPE", 14) for path in mapped_paths]
    overlays.extend((edge["extension_points"], YELLOW, "BLADE_2D_TARGET", 24) for edge in target_edges)
    overlays.extend((edge["points"], ORANGE, "BLADE_2D_TARGET", 48) for edge in target_edges)
    overlays.append((endpoints, MAGENTA, "BLADE_2D_TARGET", 55))
    review = output_dir / "01_7AB9_rotational_envelope_review.png"
    original = output_dir / "02_7AB9_rotational_envelope_original.png"
    comparison = output_dir / "03_7AB9_original_vs_rotational_envelope.png"
    phase_plot = output_dir / "04_7AB9_rotational_phase_scan.png"
    metric_passed = lower - 0.01 <= selected["span_mm"] <= upper + 0.01
    endpoint_passed = max(endpoint_residuals) <= 0.35
    geometry_passed = float(selected.get("geometry_score", 0.0)) >= 0.65
    local_boundary_passed = float(selected.get("local_boundary_error_mm", 1e9)) <= 0.50
    status = "VERIFIED" if metric_passed and geometry_passed and endpoint_passed and local_boundary_passed else "CANDIDATE_ONLY" if metric_passed or local_boundary_passed else "UNRESOLVED"
    print("[5/5] rendering isolated review and phase plot", flush=True)
    _render_review(
        dxf_path,
        bounds_2d,
        overlays,
        f"7AB9 ROTATIONAL PHASE | {nominal:.3f} +{upper-nominal:.3f}/-{nominal-lower:.3f} mm",
        [
            ("magenta: native DXF diameter; cyan: rotated STEP wireframe", MAGENTA),
            (f"orange: traced DXF attachment boundaries; local error {selected.get('local_boundary_error_mm', 0.0):.3f} mm", ORANGE),
            (f"phase {selected_angle:.3f} deg -> projected span {selected['span_mm']:.3f} mm", CYAN),
            (f"360-deg swept envelope {swept_diameter:.3f} mm; endpoint max residual {max(endpoint_residuals):.3f} mm", YELLOW),
            (f"{status}: independent contour score {selected.get('geometry_score', 0.0):.3f}", GREEN if status == "VERIFIED" else YELLOW if status == "CANDIDATE_ONLY" else RED),
        ],
        review,
    )
    _render_original_comparison(
        dxf_path, bounds_2d, review, original, comparison,
        "7AB9 | ORIGINAL VS ROTATIONAL-ENVELOPE DEBUG",
    )
    phase_plot_samples = sorted(
        {round(item["angle_degrees"], 6): item for item in [
            *section_samples,
            *(trial[0] for trial in refined_trials),
        ]}.values(),
        key=lambda item: item["angle_degrees"],
    )
    _render_phase_scan(phase_plot_samples, lower, upper, selected_angle, phase_plot)

    report = {
        "schema_version": 1,
        "method": "ROTATIONAL_ENVELOPE_PHASE_SCAN_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "dimension_handle": handle.upper(),
        "component": component_name,
        "detected_axial_axis": "XYZ"[axial_axis_index],
        "axis_center": [round(float(value), 6) for value in center],
        "view_name": view_name,
        "measurement_axis": "XYZ"[measurement_axis_index],
        "measurement_axis_alignment": round(direction_alignment, 9),
        "nominal_mm": nominal,
        "lower_limit_mm": lower,
        "upper_limit_mm": upper,
        "continuous_360_swept_diameter_mm": round(swept_diameter, 6),
        "estimated_section_period_degrees": round(estimated_period, 6),
        "selected_phase": {
            "rotation_degrees": round(selected_angle, 6),
            "projected_span_mm": round(float(selected["span_mm"]), 6),
            "inside_tolerance_interval": metric_passed,
            "endpoint_residuals_mm": [round(value, 6) for value in endpoint_residuals],
            "endpoint_gate_passed": endpoint_passed,
            "geometry_score": round(float(selected.get("geometry_score", 0.0)), 6),
            "contour_score": round(float(selected.get("contour_score", 0.0)), 6),
            "extent_score": round(float(selected.get("extent_score", 0.0)), 6),
            "geometry_gate_passed": geometry_passed,
            "local_boundary_error_mm": round(float(selected.get("local_boundary_error_mm", 0.0)), 6),
            "attachment_endpoint_error_mm": round(float(selected.get("attachment_endpoint_error_mm", 0.0)), 6),
            "local_boundary_gate_passed": local_boundary_passed,
            "local_geometry_tied_count": len(local_tied_trials),
            "dimension_used_as_tie_breaker": bool(locally_tied_passing),
        },
        "dxf_attachment_boundaries": target_edges,
        "phase_trials": [
            {
                key: round(float(value), 6)
                for key, value in item.items()
            }
            for item in sorted(
                {round(record["angle_degrees"], 6): record for record in [
                    *section_samples,
                    *(trial[0] for trial in refined_trials),
                ]}.values(),
                key=lambda record: record["angle_degrees"],
            )
        ],
        "status": status,
        "reason": None if status == "VERIFIED" else "A section phase enters the native tolerance, but contour or endpoint gates remain below automatic verification." if metric_passed else "No geometry-selected rotational section phase matches the native tolerance interval.",
        "artifacts": {
            "review": review.name,
            "original": original.name,
            "original_vs_review": comparison.name,
            "phase_scan": phase_plot.name,
        },
    }
    (output_dir / "rotational_envelope_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--section-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--handle", default="7AB9")
    parser.add_argument("--component", default="AL0W_BLADE_H-R01")
    args = parser.parse_args()
    report = run(args.step, args.dxf, args.section_report, args.output_dir, args.handle, args.component)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
