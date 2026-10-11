"""Compare a drawing section view with dynamically half-cut full fan assemblies.

The experiment deliberately loads the complete STEP assembly so the shaft and
housing remain present.  It tries both radial cutting-plane normals and both
retained half-spaces, then registers visible/visible+hidden HLR linework to the
dimension-local DXF section view.  No production pipeline is modified.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Section
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt

from auto_2d_drawing.step_reader import load_step
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher
from auto_2d_drawing.tolerance.projection_registration_v2 import (
    ProjectionRegistrationEngine,
    SimilarityTransform2D,
)
from auto_2d_drawing.view_projector import ViewProjector
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from run_blade_curve_tolerance_experiment import (
    CYAN,
    MAGENTA,
    RED,
    YELLOW,
    _dimension_map,
    _edge_points,
    _render_original_comparison,
    _render_review,
)


ORANGE = (249, 115, 22)


def _shape_bounds(shape):
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    return tuple(float(value) for value in box.Get())


def _half_cut(shape, bounds, normal_axis: int, keep_positive: bool):
    minimum = list(bounds[:3])
    maximum = list(bounds[3:])
    spans = [maximum[index]-minimum[index] for index in range(3)]
    center = [(minimum[index]+maximum[index])/2.0 for index in range(3)]
    padding = max(spans) * 0.25 + 1.0
    box_min = [minimum[index]-padding for index in range(3)]
    box_max = [maximum[index]+padding for index in range(3)]
    # BRepAlgoAPI_Cut removes the opposite half and keeps the requested side.
    if keep_positive:
        box_max[normal_axis] = center[normal_axis]
    else:
        box_min[normal_axis] = center[normal_axis]
    cutter = BRepPrimAPI_MakeBox(gp_Pnt(*box_min), gp_Pnt(*box_max)).Shape()
    operation = BRepAlgoAPI_Cut(shape, cutter)
    operation.Build()
    if not operation.IsDone() or operation.Shape().IsNull():
        raise RuntimeError(
            f"Half-space cut failed for axis={normal_axis}, keep_positive={keep_positive}"
        )
    return operation.Shape(), center, spans


def _central_section_curves(shape, bounds, normal_axis: int):
    center = [(bounds[index]+bounds[index+3])/2.0 for index in range(3)]
    normal = [0.0, 0.0, 0.0]
    normal[normal_axis] = 1.0
    plane = gp_Pln(gp_Pnt(*center), gp_Dir(*normal))
    face = BRepBuilderAPI_MakeFace(plane).Face()
    operation = BRepAlgoAPI_Section(shape, face, False)
    operation.Build()
    if not operation.IsDone() or operation.Shape().IsNull():
        raise RuntimeError(f"Central section failed for axis={normal_axis}")
    return operation.Shape()


def _project(projector, shape, view_name):
    visible, hidden, outline_visible, outline_hidden = projector.project(shape, view_name)
    visible_edges = projector.extract_edges(visible)
    hidden_edges = projector.extract_edges(hidden)
    if outline_visible is not None and not outline_visible.IsNull():
        visible_edges.extend(projector.extract_edges(outline_visible))
    if outline_hidden is not None and not outline_hidden.IsNull():
        hidden_edges.extend(projector.extract_edges(outline_hidden))
    all_edges = visible_edges + hidden_edges
    bbox = projector.get_edges_bbox(all_edges)
    return {
        "visible": visible_edges,
        "hidden": hidden_edges,
        "bbox": tuple(float(value) for value in bbox),
    }


def _local_section_candidate(extractor, midpoint, nominal_span):
    ranked = []
    for candidate in extractor.view_candidates:
        xmin, ymin, xmax, ymax = candidate.bbox
        width, height = xmax-xmin, ymax-ymin
        if not (xmin <= midpoint[0] <= xmax and ymin <= midpoint[1] <= ymax):
            continue
        if not (0.80*nominal_span <= width <= 1.35*nominal_span):
            continue
        if not (4.0 <= height <= 0.60*nominal_span):
            continue
        ranked.append((abs(width-nominal_span), -candidate.primitive_count, candidate))
    if not ranked:
        raise RuntimeError("No DXF section-view candidate around the 75.19 dimension")
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def _register(candidate, view, include_hidden):
    helper = GlobalGeometryPairSearcher(maximum_record_points=160)
    engine = ProjectionRegistrationEngine(
        angle_step_degrees=30,
        max_points=96,
        icp_iterations=8,
    )
    payload = {
        "visible": view["visible"],
        "hidden": view["hidden"] if include_hidden else [],
    }
    step_points = helper._reduce_points(helper._step_view_points(payload))
    result = engine.register(candidate.points, step_points)
    raw = result.get("transform")
    if not raw:
        return result, None
    transform = SimilarityTransform2D(
        scale=float(raw["scale"]),
        rotation_degrees=float(raw["rotation_degrees"]),
        translation=tuple(raw["translation"]),
        mirrored=bool(raw.get("mirrored", False)),
    )
    return result, transform


def _view_paths(view, transform, include_hidden=False):
    paths = []
    edges = list(view["visible"])
    if include_hidden:
        edges.extend(view["hidden"])
    for edge in edges:
        points = _edge_points(edge)
        if len(points) >= 2:
            paths.append(transform.apply(points))
    return paths


def _shaft_like_paths(view, transform):
    bbox = view["bbox"]
    center_x = (bbox[0]+bbox[2])/2.0
    width = max(bbox[2]-bbox[0], 1e-9)
    height = max(bbox[3]-bbox[1], 1e-9)
    result = []
    for edge in view["visible"]:
        points = _edge_points(edge)
        if len(points) < 2:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        near_axis = max(abs(value-center_x) for value in xs) <= 0.07*width
        axial_span = max(ys)-min(ys) >= 0.12*height
        if near_axis and axial_span:
            result.append(transform.apply(points))
    return result


def run(step_path: Path, dxf_path: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    shape = load_step(str(step_path))
    bounds = _shape_bounds(shape)
    spans = [bounds[index+3]-bounds[index] for index in range(3)]
    axial_axis = min(range(3), key=lambda index: spans[index])
    radial_axes = [index for index in range(3) if index != axial_axis]

    dimensions = _dimension_map(dxf_path)
    outer = dimensions["7ADF"]
    endpoints = [
        tuple(outer.points["defpoint2"][:2]),
        tuple(outer.points["defpoint3"][:2]),
    ]
    midpoint = (
        (endpoints[0][0]+endpoints[1][0])/2.0,
        (endpoints[0][1]+endpoints[1][1])/2.0,
    )
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    target = _local_section_candidate(extractor, midpoint, float(outer.nominal_value))

    projector = ViewProjector()
    axis_views = {
        0: ("right", "left"),
        1: ("top",),
        2: ("front", "back"),
    }
    trials = []

    # Uncut full assembly is the baseline proving whether half-sectioning adds value.
    for radial_axis in radial_axes:
        for view_name in axis_views[radial_axis]:
            view = _project(projector, shape, view_name)
            for include_hidden in (False, True):
                registration, transform = _register(target, view, include_hidden)
                if transform is None:
                    continue
                trials.append({
                    "kind": "UNCUT_BASELINE",
                    "cut_axis": None,
                    "keep_positive": None,
                    "view_name": view_name,
                    "include_hidden": include_hidden,
                    "view": view,
                    "registration": registration,
                    "transform": transform,
                })

    for radial_axis in radial_axes:
        section_shape = _central_section_curves(shape, bounds, radial_axis)
        for view_name in axis_views[radial_axis]:
            view = _project(projector, section_shape, view_name)
            registration, transform = _register(target, view, False)
            if transform is not None:
                trials.append({
                    "kind": "CENTRAL_SECTION_CURVES",
                    "cut_axis": "XYZ"[radial_axis],
                    "keep_positive": None,
                    "view_name": view_name,
                    "include_hidden": False,
                    "view": view,
                    "registration": registration,
                    "transform": transform,
                })
        for keep_positive in (False, True):
            cut_shape, _center, _spans = _half_cut(
                shape, bounds, radial_axis, keep_positive
            )
            for view_name in axis_views[radial_axis]:
                view = _project(projector, cut_shape, view_name)
                for include_hidden in (False, True):
                    registration, transform = _register(target, view, include_hidden)
                    if transform is None:
                        continue
                    trials.append({
                        "kind": "FULL_ASSEMBLY_HALF_SECTION",
                        "cut_axis": "XYZ"[radial_axis],
                        "keep_positive": keep_positive,
                        "view_name": view_name,
                        "include_hidden": include_hidden,
                        "view": view,
                        "registration": registration,
                        "transform": transform,
                    })

    for trial in trials:
        registration = trial["registration"]
        scale = max(float(trial["transform"].scale), 1e-9)
        scale_score = math.exp(-abs(math.log(scale))/0.12)
        trial["selection_score"] = (
            0.65*float(registration.get("score", 0.0))
            + 0.20*float(registration.get("extent_score", 0.0))
            + 0.15*scale_score
        )
    half_trials = [item for item in trials if item["kind"] == "FULL_ASSEMBLY_HALF_SECTION"]
    baseline_trials = [item for item in trials if item["kind"] == "UNCUT_BASELINE"]
    section_trials = [item for item in trials if item["kind"] == "CENTRAL_SECTION_CURVES"]
    half_trials.sort(key=lambda item: item["selection_score"], reverse=True)
    baseline_trials.sort(key=lambda item: item["selection_score"], reverse=True)
    section_trials.sort(key=lambda item: item["selection_score"], reverse=True)
    selected = half_trials[0]
    baseline = baseline_trials[0]
    section_selected = section_trials[0]

    paths = _view_paths(
        selected["view"], selected["transform"], selected["include_hidden"]
    )
    shaft_paths = _shaft_like_paths(selected["view"], selected["transform"])
    overlays = [
        (path, CYAN, "BLADE_STEP_ENVELOPE", 38) for path in paths
    ] + [
        (path, ORANGE, "BLADE_STEP_CANDIDATE", 75) for path in shaft_paths
    ] + [
        (endpoints, MAGENTA, "BLADE_2D_TARGET", 130),
    ]
    focus = (
        target.bbox[0]-8.0, target.bbox[1]-8.0,
        target.bbox[2]+8.0, target.bbox[3]+14.0,
    )
    registration = selected["registration"]
    overlay_path = output_dir / "01_full_assembly_half_section_overlay.png"
    _render_review(
        dxf_path,
        focus,
        overlays,
        "Full fan assembly - center half-section projection",
        [
            ("cyan: half-cut full assembly visible HLR; orange: shaft-axis region", CYAN),
            ("magenta: original 75.19 drawing supports", MAGENTA),
            (f"score {float(registration.get('score', 0.0)):.3f}, extent {float(registration.get('extent_score', 0.0)):.3f}, scale {selected['transform'].scale:.3f}", YELLOW),
        ],
        overlay_path,
    )
    _render_original_comparison(
        dxf_path,
        focus,
        overlay_path,
        output_dir / "02_original_section_dxf.png",
        output_dir / "03_original_vs_half_section.png",
        "Original assembly section and generated full-assembly half-section",
    )

    section_paths = _view_paths(
        section_selected["view"], section_selected["transform"], False
    )
    section_shaft_paths = _shaft_like_paths(
        section_selected["view"], section_selected["transform"]
    )
    section_overlay_path = output_dir / "04_central_section_curves_overlay.png"
    section_registration = section_selected["registration"]
    _render_review(
        dxf_path,
        focus,
        [
            *[(path, CYAN, "BLADE_STEP_ENVELOPE", 42) for path in section_paths],
            *[(path, ORANGE, "BLADE_STEP_CANDIDATE", 75) for path in section_shaft_paths],
            (endpoints, MAGENTA, "BLADE_2D_TARGET", 130),
        ],
        "Full fan assembly - central section-plane intersection",
        [
            ("cyan: full-assembly section curves; orange: shaft-axis region", CYAN),
            ("magenta: original 75.19 drawing supports", MAGENTA),
            (f"score {float(section_registration.get('score', 0.0)):.3f}, extent {float(section_registration.get('extent_score', 0.0)):.3f}, scale {section_selected['transform'].scale:.3f}", YELLOW),
        ],
        section_overlay_path,
    )
    _render_original_comparison(
        dxf_path,
        focus,
        section_overlay_path,
        output_dir / "02_original_section_dxf.png",
        output_dir / "05_original_vs_central_section.png",
        "Original drawing and full-assembly central section curves",
    )

    def public_trial(item):
        registration = item["registration"]
        return {
            "kind": item["kind"],
            "cut_axis": item["cut_axis"],
            "keep_positive": item["keep_positive"],
            "view_name": item["view_name"],
            "include_hidden": item["include_hidden"],
            "visible_edge_count": len(item["view"]["visible"]),
            "hidden_edge_count": len(item["view"]["hidden"]),
            "contour_score": float(registration.get("score", 0.0)),
            "extent_score": float(registration.get("extent_score", 0.0)),
            "selection_score": round(float(item["selection_score"]), 6),
            "transform": item["transform"].to_dict(),
        }

    improvement = (
        float(selected["registration"].get("score", 0.0))
        - float(baseline["registration"].get("score", 0.0))
    )
    recommended = max(
        (selected, section_selected),
        key=lambda item: item["selection_score"],
    )
    report = {
        "schema_version": 1,
        "method": "FULL_ASSEMBLY_DYNAMIC_CENTER_HALF_SECTION_HLR_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "step_path": str(step_path.resolve()),
        "dxf_path": str(dxf_path.resolve()),
        "assembly_bounds": [round(value, 6) for value in bounds],
        "assembly_spans": [round(value, 6) for value in spans],
        "detected_axial_axis": "XYZ"[axial_axis],
        "tested_radial_cut_axes": ["XYZ"[index] for index in radial_axes],
        "target_dxf_view": target.view_id,
        "best_uncut_baseline": public_trial(baseline),
        "best_central_section_curves": public_trial(section_trials[0]),
        "best_half_section": public_trial(selected),
        "recommended_section_method": public_trial(recommended),
        "contour_score_improvement": round(improvement, 6),
        "automatic_match": (
            float(recommended["registration"].get("score", 0.0)) >= 0.72
            and float(recommended["registration"].get("extent_score", 0.0)) >= 0.80
            and 0.92 <= recommended["transform"].scale <= 1.08
        ),
        "top_half_section_trials": [public_trial(item) for item in half_trials[:8]],
        "artifacts": {
            "overlay": "01_full_assembly_half_section_overlay.png",
            "original": "02_original_section_dxf.png",
            "comparison": "03_original_vs_half_section.png",
            "central_section_overlay": "04_central_section_curves_overlay.png",
            "central_section_comparison": "05_original_vs_central_section.png",
        },
    }
    (output_dir / "fan_half_section_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.step, args.dxf, args.output_dir)
    print(json.dumps({
        "method": report["method"],
        "detected_axial_axis": report["detected_axial_axis"],
        "best_uncut_baseline": report["best_uncut_baseline"],
        "best_central_section_curves": report["best_central_section_curves"],
        "best_half_section": report["best_half_section"],
        "recommended_section_method": report["recommended_section_method"],
        "contour_score_improvement": report["contour_score_improvement"],
        "automatic_match": report["automatic_match"],
        "artifacts": report["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
