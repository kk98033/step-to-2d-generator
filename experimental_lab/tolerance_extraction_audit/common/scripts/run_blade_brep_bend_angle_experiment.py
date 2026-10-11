"""Measure the 7-R2.15 bend angle from the original B-Rep curve derivatives."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.gp import gp_Pnt, gp_Vec

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for value in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D
from run_blade_curve_tolerance_experiment import (
    CYAN, MAGENTA, YELLOW, _nearest_distances,
    _render_original_comparison, _render_review, _sample_dxf_arc,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2

ORANGE = (249, 115, 22)


def _unit(vector):
    vector = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(vector))
    return vector / max(norm, 1e-12)


def _angle(left, right):
    dot = float(np.clip(np.dot(_unit(left), _unit(right)), -1.0, 1.0))
    return math.degrees(math.acos(dot))


def _transform_vector(vector, transform):
    x, y = float(vector[0]), float(vector[1])
    if transform.mirrored:
        x = -x
    angle = math.radians(transform.rotation_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.asarray((
        transform.scale * (x*cosine-y*sine),
        transform.scale * (x*sine+y*cosine),
    ))


def _line(point, direction, length=2.8):
    direction = _unit(direction)
    point = np.asarray(point, dtype=float)
    return [tuple(point-length*direction), tuple(point+length*direction)]


def run(step_path: Path, dxf_path: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    print('[1/4] loading blade B-Rep', flush=True)
    component = next(
        item for item in extract_leaf_components(str(step_path))
        if item.name == 'AL0W_BLADE_H-R01'
    )
    target_edge_index = 1150
    explorer = TopExp_Explorer(component.shape, TopAbs_EDGE)
    edge_index, edge = 0, None
    while explorer.More():
        edge_index += 1
        if edge_index == target_edge_index:
            edge = explorer.Current()
            break
        explorer.Next()
    if edge is None:
        raise RuntimeError(f'B-Rep edge {target_edge_index} was not found')

    print('[2/4] sampling exact B-Spline and native DXF arc', flush=True)
    curve = BRepAdaptor_Curve(edge)
    first, last = float(curve.FirstParameter()), float(curve.LastParameter())
    parameters = np.linspace(first, last, 801)
    raw = []
    for parameter in parameters:
        point = curve.Value(float(parameter))
        raw.append((float(point.X()), float(point.Y())))
    transform = SimilarityTransform2D(
        scale=1.0010075335,
        rotation_degrees=0.338298,
        translation=(1370.0219951, 268.89970259),
        mirrored=False,
    )
    projected = np.asarray(transform.apply(raw), dtype=float)
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    target = np.asarray(_sample_dxf_arc(extractor.document, '6FE2', count=192), dtype=float)

    direct = (
        np.linalg.norm(projected-target[0], axis=1).min()
        + np.linalg.norm(projected-target[-1], axis=1).min()
    )
    reverse = (
        np.linalg.norm(projected-target[-1], axis=1).min()
        + np.linalg.norm(projected-target[0], axis=1).min()
    )
    target_start, target_end = (target[0], target[-1]) if direct <= reverse else (target[-1], target[0])
    start_index = int(np.argmin(np.linalg.norm(projected-target_start, axis=1)))
    end_index = int(np.argmin(np.linalg.norm(projected-target_end, axis=1)))
    start_parameter = float(parameters[start_index])
    end_parameter = float(parameters[end_index])
    direction_sign = 1.0 if end_parameter >= start_parameter else -1.0

    point_start, derivative_start = gp_Pnt(), gp_Vec()
    point_end, derivative_end = gp_Pnt(), gp_Vec()
    curve.D1(start_parameter, point_start, derivative_start)
    curve.D1(end_parameter, point_end, derivative_end)
    candidate_start_tangent = direction_sign * _transform_vector(
        (derivative_start.X(), derivative_start.Y()), transform
    )
    candidate_end_tangent = direction_sign * _transform_vector(
        (derivative_end.X(), derivative_end.Y()), transform
    )
    if direct > reverse:
        target_direction_sign = -1.0
    else:
        target_direction_sign = 1.0
    target_start_tangent = target_direction_sign * (target[1]-target[0])
    target_end_tangent = target_direction_sign * (target[-1]-target[-2])
    if target_direction_sign < 0:
        target_start_tangent, target_end_tangent = target_end_tangent, target_start_tangent

    candidate_turn = _angle(candidate_start_tangent, candidate_end_tangent)
    target_turn = _angle(target_start_tangent, target_end_tangent)
    start_tangent_error = _angle(candidate_start_tangent, target_start_tangent)
    end_tangent_error = _angle(candidate_end_tangent, target_end_tangent)
    lo, hi = sorted((start_index, end_index))
    segment = projected[lo:hi+1]
    if end_index < start_index:
        segment = segment[::-1]
    target_distances = _nearest_distances(target, segment)

    print('[3/4] evaluating tangent and location gates', flush=True)
    angle_error = abs(candidate_turn-target_turn)
    p90 = float(np.percentile(target_distances, 90))
    mean = float(np.mean(target_distances))
    passed = (
        angle_error <= 5.0
        and start_tangent_error <= 8.0
        and end_tangent_error <= 8.0
        and p90 <= 0.50
    )
    report = {
        'schema_version': 1,
        'method': 'BREP_BSPLINE_ENDPOINT_D1_TANGENT_TURN_V1',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'production_or_current_final_updated': False,
        'dxf_handle': '6FE2',
        'brep_edge_index': target_edge_index,
        'target': {
            'radius_mm': 2.15,
            'tangent_turn_degrees': round(target_turn, 6),
        },
        'candidate': {
            'tangent_turn_degrees': round(candidate_turn, 6),
            'angle_error_degrees': round(angle_error, 6),
            'start_tangent_error_degrees': round(start_tangent_error, 6),
            'end_tangent_error_degrees': round(end_tangent_error, 6),
            'mean_distance_mm': round(mean, 6),
            'p90_distance_mm': round(p90, 6),
            'parameter_interval': [start_parameter, end_parameter],
        },
        'status': 'ANGLE_VERIFIED' if passed else 'CANDIDATE_ONLY',
        'gates': {
            'turn_error_le_5_deg': angle_error <= 5.0,
            'endpoint_tangent_errors_le_8_deg': start_tangent_error <= 8.0 and end_tangent_error <= 8.0,
            'p90_distance_le_0_5_mm': p90 <= 0.50,
        },
    }
    (output_dir/'blade_brep_bend_angle_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )

    print('[4/4] rendering native DXF debug', flush=True)
    all_points = np.vstack((target, segment))
    bounds = (
        float(all_points[:, 0].min()-4.0), float(all_points[:, 1].min()-4.0),
        float(all_points[:, 0].max()+4.0), float(all_points[:, 1].max()+4.0),
    )
    candidate_start, candidate_end = segment[0], segment[-1]
    overlays = [
        ([tuple(value) for value in target], MAGENTA, 'DXF_TARGET_ARC', 95),
        ([tuple(value) for value in segment], CYAN, 'BREP_BSPLINE_SEGMENT', 70),
        (_line(target_start, target_start_tangent), YELLOW, 'DXF_TANGENT', 60),
        (_line(target_end, target_end_tangent), YELLOW, 'DXF_TANGENT', 60),
        (_line(candidate_start, candidate_start_tangent), ORANGE, 'BREP_TANGENT', 55),
        (_line(candidate_end, candidate_end_tangent), ORANGE, 'BREP_TANGENT', 55),
    ]
    review = output_dir/'01_blade_brep_bend_angle_debug.png'
    _render_review(
        dxf_path, bounds, overlays,
        '7-R2.15 B-Rep tangent-angle verification',
        [
            (f'magenta DXF turn {target_turn:.2f} deg; cyan B-Rep turn {candidate_turn:.2f} deg', MAGENTA),
            (f'yellow DXF tangents; orange B-Rep tangents; error {angle_error:.2f} deg', YELLOW),
            (f'{report["status"]}: mean {mean:.3f} mm, p90 {p90:.3f} mm', ORANGE),
        ],
        review,
    )
    _render_original_comparison(
        dxf_path, bounds, review,
        output_dir/'02_original_R2_15_region.png',
        output_dir/'03_original_vs_brep_angle_debug.png',
        'Original R2.15 region and exact B-Rep tangent debug',
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--step', required=True, type=Path)
    parser.add_argument('--dxf', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    report = run(args.step.resolve(), args.dxf.resolve(), args.output_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
