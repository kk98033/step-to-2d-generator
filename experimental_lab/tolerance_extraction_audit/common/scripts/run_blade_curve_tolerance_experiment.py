"""Focused, geometry-first experiment for curved blade tolerances.

This is deliberately isolated from the production verifier.  It tests two
different semantics on the AL0W blade drawing:

* an overall blade-envelope span (75.19 +0.20/-0.00); and
* a repeated local blade-profile bend (7-R2.15).

The first can be verified against the registered STEP envelope.  The second
is kept as a review candidate unless the projected B-Rep curve agrees both in
location and local curvature.  Proximity alone is never treated as proof.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import ezdxf
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout as drawing_layout
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.math import BoundingBox2d
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg
import pypdfium2 as pdfium

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GeomAbs import GeomAbs_BSplineCurve, GeomAbs_Circle, GeomAbs_Line
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.TopExp import TopExp_Explorer

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension,
    _extract_dxf_worker,
)
from auto_2d_drawing.tolerance.projection_registration_v2 import (
    AdaptiveProjectionCandidateGenerator,
    ProjectionRegistrationEngine,
    SimilarityTransform2D,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
CYAN = (34, 211, 238)
MAGENTA = (255, 43, 214)
YELLOW = (250, 204, 21)
RED = (239, 68, 68)
GREEN = (34, 197, 94)


def _font(size: int):
    for path in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _edge_points(edge: dict):
    if edge.get("p1") and edge.get("p2"):
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(point) for point in edge.get("points") or []]


def _sample_dxf_arc(document, handle: str, count: int = 96):
    entity = document.entitydb.get(handle)
    if entity is None or entity.dxftype() not in {"ARC", "CIRCLE"}:
        return []
    center = entity.dxf.center
    radius = float(entity.dxf.radius)
    start = 0.0 if entity.dxftype() == "CIRCLE" else float(entity.dxf.start_angle)
    end = 360.0 if entity.dxftype() == "CIRCLE" else float(entity.dxf.end_angle)
    if end <= start:
        end += 360.0
    return [
        (
            float(center.x) + radius * math.cos(math.radians(start + (end-start)*i/count)),
            float(center.y) + radius * math.sin(math.radians(start + (end-start)*i/count)),
        )
        for i in range(count + 1)
    ]


def _nearest_distances(source, target):
    left = np.asarray(source, dtype=float)
    right = np.asarray(target, dtype=float)
    if not len(left) or not len(right):
        return np.asarray([], dtype=float)
    difference = left[:, None, :] - right[None, :, :]
    return np.sqrt(np.min(np.einsum("ijk,ijk->ij", difference, difference), axis=1))


def _fit_circle(points):
    array = np.asarray(points, dtype=float)
    if len(array) < 3:
        return None
    first, middle, last = array[0], array[len(array)//2], array[-1]
    ax, ay = first
    bx, by = middle
    cx0, cy0 = last
    determinant = 2.0 * (ax*(by-cy0) + bx*(cy0-ay) + cx0*(ay-by))
    if abs(determinant) <= 1e-10:
        return None
    aa, bb, cc = ax*ax+ay*ay, bx*bx+by*by, cx0*cx0+cy0*cy0
    cx = (aa*(by-cy0) + bb*(cy0-ay) + cc*(ay-by)) / determinant
    cy = (aa*(cx0-bx) + bb*(ax-cx0) + cc*(bx-ax)) / determinant
    radius = math.hypot(ax-cx, ay-cy)
    residual = np.abs(np.linalg.norm(array - np.asarray((cx, cy)), axis=1) - radius)
    return {
        "center": [round(float(cx), 6), round(float(cy), 6)],
        "radius": round(float(radius), 6),
        "mean_residual": round(float(np.mean(residual)), 6),
        "p90_residual": round(float(np.percentile(residual, 90)), 6),
    }


def _dimension_map(dxf_path: Path):
    _source, payloads = _extract_dxf_worker(str(dxf_path))
    return {
        str(item.entity_handle): item
        for item in (ExtractedDimension(**payload) for payload in payloads)
    }


def _hub_center(analyzer, dimensions, reference_point):
    circular = []
    for dim in dimensions.values():
        if str(dim.dimension_category).upper() != "DIAMETER":
            continue
        association = analyzer.analyze_dimension(dim)
        for profile in association.get("circular_attachment_profiles") or []:
            center = profile.get("center")
            radius = float(profile.get("radius") or 0.0)
            if center and radius > 0.0:
                circular.append((radius, (float(center[0]), float(center[1]))))
    if not circular:
        raise RuntimeError("No circular hub reference could be derived from the DXF")
    # A sheet can contain several fan views.  Bind the hub to the current
    # dimension's extension-span midpoint instead of taking a sheet-global
    # median, which can silently jump to a neighbouring view.
    _radius, center = min(
        circular,
        key=lambda item: math.dist(item[1], reference_point),
    )
    return center


def _select_local_registration(extractor, hub_center, nominal_span, step_view):
    helper = GlobalGeometryPairSearcher(maximum_record_points=256)
    engine = ProjectionRegistrationEngine(
        angle_step_degrees=30,
        max_points=96,
        icp_iterations=8,
    )
    step_points = helper._reduce_points(helper._step_view_points(step_view))
    plausible = []
    for candidate in extractor.view_candidates:
        xmin, ymin, xmax, ymax = candidate.bbox
        width, height = xmax-xmin, ymax-ymin
        contains_hub = xmin <= hub_center[0] <= xmax and ymin <= hub_center[1] <= ymax
        plausible_size = (
            0.65 * nominal_span <= width <= 1.55 * nominal_span
            and 0.65 * nominal_span <= height <= 1.55 * nominal_span
        )
        if not contains_hub or not plausible_size or len(candidate.points) < 12:
            continue
        center_error = math.dist(
            ((xmin+xmax)/2.0, (ymin+ymax)/2.0),
            hub_center,
        )
        plausible.append((center_error, -candidate.curved_primitive_count, candidate))
    # Multiscale extraction often emits nested copies of the same view.  The
    # richest curved candidate nearest the dimension-local hub is sufficient;
    # registering every duplicate is expensive and does not add evidence.
    plausible.sort(key=lambda item: (round(item[0], 3), item[1]))
    candidates = []
    for _center_error, _negative_curve_count, candidate in plausible[:1]:
        result = engine.register(candidate.points, step_points)
        transform_data = result.get("transform")
        if not transform_data:
            continue
        score = 0.75*float(result.get("score", 0.0)) + 0.25*float(result.get("extent_score", 0.0))
        candidates.append((score, candidate, result))
    if not candidates:
        raise RuntimeError("No local blade-view registration candidate")
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, candidate, result = candidates[0]
    raw = result["transform"]
    transform = SimilarityTransform2D(
        scale=float(raw["scale"]),
        rotation_degrees=float(raw["rotation_degrees"]),
        translation=tuple(raw["translation"]),
        mirrored=bool(raw.get("mirrored", False)),
    )
    # Hub-centered translation refinement is geometry-derived.  It removes a
    # small ICP bias caused by leaders and section marks inside the local view.
    refined = _refine_transform_center(transform, step_view, hub_center)
    return candidate, result, refined


def _select_span_registration(extractor, midpoint, nominal_span, step_views):
    local_candidates = []
    for candidate in extractor.view_candidates:
        xmin, ymin, xmax, ymax = candidate.bbox
        width, height = xmax-xmin, ymax-ymin
        if not (xmin <= midpoint[0] <= xmax and ymin <= midpoint[1] <= ymax):
            continue
        if not (0.80*nominal_span <= width <= 1.35*nominal_span and 4.0 <= height <= 0.60*nominal_span):
            continue
        local_candidates.append((abs(width-nominal_span), -candidate.primitive_count, candidate))
    if not local_candidates:
        raise RuntimeError("No dimension-local side-view candidate for the outer span")
    local_candidates.sort(key=lambda item: (item[0], item[1]))
    candidate = local_candidates[0][2]
    helper = GlobalGeometryPairSearcher(maximum_record_points=160)
    engine = ProjectionRegistrationEngine(angle_step_degrees=30, max_points=96, icp_iterations=8)
    trials = []
    for view_name, view in step_views.items():
        bbox = view["bbox"]
        width, height = bbox[2]-bbox[0], bbox[3]-bbox[1]
        if height <= 0.0 or width/max(height, 1e-9) < 2.0:
            continue
        points = helper._reduce_points(helper._step_view_points(view))
        result = engine.register(candidate.points, points)
        raw = result.get("transform")
        if not raw:
            continue
        combined = 0.75*float(result.get("score", 0.0)) + 0.25*float(result.get("extent_score", 0.0))
        transform = SimilarityTransform2D(
            float(raw["scale"]), float(raw["rotation_degrees"]),
            tuple(raw["translation"]), bool(raw.get("mirrored", False)),
        )
        trials.append((combined, view_name, view, result, transform))
    if not trials:
        raise RuntimeError("No STEP side projection could be registered to the outer span view")
    trials.sort(key=lambda item: item[0], reverse=True)
    return candidate, trials[0], trials


def _geometry_constrained_envelope_phase(
    registration,
    step_view,
    hub_center,
    dimension_endpoints,
    nominal,
    lower,
    upper,
    geometry_tie_band=0.005,
):
    """Measure an envelope after the drawing-local projection phase is known.

    The registration score selects the phase first.  The dimension value may
    only break a genuinely near-tied geometry result; it never searches the
    full rotation range for a value that happens to equal the annotation.
    """
    dx = float(dimension_endpoints[1][0]) - float(dimension_endpoints[0][0])
    dy = float(dimension_endpoints[1][1]) - float(dimension_endpoints[0][1])
    magnitude = math.hypot(dx, dy)
    if magnitude <= 1e-9:
        raise RuntimeError("Envelope dimension has no measurable direction")
    dimension_axis = (dx / magnitude, dy / magnitude)

    raw_paths = []
    for edge in list(step_view.get("visible") or []) + list(step_view.get("hidden") or []):
        points = _edge_points(edge)
        if len(points) >= 2:
            raw_paths.append(points)
    if not raw_paths:
        raise RuntimeError("STEP projection has no envelope paths")

    payloads = [registration] + list(registration.get("alternatives") or [])
    hypotheses = []
    seen = set()
    for payload in payloads:
        raw = payload.get("transform") or {}
        if not raw:
            continue
        key = (
            round(float(raw["rotation_degrees"]), 4),
            bool(raw.get("mirrored", False)),
            round(float(raw["scale"]), 6),
        )
        if key in seen:
            continue
        seen.add(key)
        transform = SimilarityTransform2D(
            scale=float(raw["scale"]),
            rotation_degrees=float(raw["rotation_degrees"]),
            translation=tuple(raw["translation"]),
            mirrored=bool(raw.get("mirrored", False)),
        )
        transform = _refine_transform_center(transform, step_view, hub_center)
        mapped_paths = [transform.apply(path) for path in raw_paths]
        coordinates = [
            point[0] * dimension_axis[0] + point[1] * dimension_axis[1]
            for path in mapped_paths
            for point in path
        ]
        physical_span = (max(coordinates) - min(coordinates)) / max(transform.scale, 1e-12)
        hypotheses.append({
            "payload": payload,
            "transform": transform,
            "mapped_paths": mapped_paths,
            "geometry_score": float(payload.get("score", 0.0)),
            "extent_score": float(payload.get("extent_score", 0.0)),
            "physical_span_mm": physical_span,
            "nominal_error_mm": abs(physical_span - nominal),
            "inside_tolerance_interval": lower - 0.01 <= physical_span <= upper + 0.01,
        })
    if not hypotheses:
        raise RuntimeError("No drawing-local projection phase hypothesis")

    best_geometry = max(item["geometry_score"] for item in hypotheses)
    geometry_tied = [
        item for item in hypotheses
        if item["geometry_score"] >= best_geometry - geometry_tie_band
    ]
    passing_tied = [item for item in geometry_tied if item["inside_tolerance_interval"]]
    selection_pool = passing_tied or geometry_tied
    selection_pool.sort(key=lambda item: (
        item["nominal_error_mm"] if len(selection_pool) > 1 else 0.0,
        -item["geometry_score"],
        -item["extent_score"],
    ))
    selected = selection_pool[0]
    selected["method"] = "DRAWING_LOCAL_GEOMETRY_CONSTRAINED_PHASE_V1"
    selected["dimension_axis"] = [round(value, 9) for value in dimension_axis]
    selected["hypothesis_count"] = len(hypotheses)
    selected["geometry_tied_count"] = len(geometry_tied)
    selected["dimension_used_as_tie_breaker"] = len(geometry_tied) > 1
    return selected


def _sample_step_edges(shape, transform, samples=17):
    result = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    edge_index = 0
    while explorer.More():
        edge_index += 1
        edge = explorer.Current()
        try:
            curve = BRepAdaptor_Curve(edge)
            first, last = float(curve.FirstParameter()), float(curve.LastParameter())
            if not (math.isfinite(first) and math.isfinite(last)) or abs(last-first) <= 1e-12:
                explorer.Next()
                continue
            raw = []
            for index in range(samples):
                parameter = first + (last-first)*index/(samples-1)
                point = curve.Value(parameter)
                raw.append((float(point.X()), float(point.Y())))
            projected = transform.apply(raw)
            curve_type = int(curve.GetType())
            name = {
                int(GeomAbs_Line): "LINE",
                int(GeomAbs_Circle): "CIRCLE",
                int(GeomAbs_BSplineCurve): "BSPLINE",
            }.get(curve_type, f"CURVE_{curve_type}")
            result.append({
                "edge_index": edge_index,
                "curve_type": name,
                "raw_front_points": raw,
                "projected_points": projected,
            })
        except Exception:
            pass
        explorer.Next()
    return result


def _refine_transform_center(transform, step_view, hub_center):
    bbox = step_view["bbox"]
    step_center = ((bbox[0]+bbox[2])/2.0, (bbox[1]+bbox[3])/2.0)
    mapped_center = transform.apply_point(step_center)
    return SimilarityTransform2D(
        scale=transform.scale,
        rotation_degrees=transform.rotation_degrees,
        translation=(
            transform.translation[0] + hub_center[0] - mapped_center[0],
            transform.translation[1] + hub_center[1] - mapped_center[1],
        ),
        mirrored=transform.mirrored,
    )


def _transform_step_edges(edges, transform):
    return [
        {
            **edge,
            "projected_points": transform.apply(edge["raw_front_points"]),
        }
        for edge in edges
    ]


def _nearby_transformed_edges(edges, target_points, transform, margin_mm=4.0):
    raw_target = np.asarray([transform.inverse_point(point) for point in target_points], dtype=float)
    target_box = (
        raw_target[:, 0].min(), raw_target[:, 1].min(),
        raw_target[:, 0].max(), raw_target[:, 1].max(),
    )
    margin = float(margin_mm) / max(abs(transform.scale), 1e-9)
    nearby = []
    for edge in edges:
        points = np.asarray(edge["raw_front_points"], dtype=float)
        box = (points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max())
        dx = max(target_box[0]-box[2], box[0]-target_box[2], 0.0)
        dy = max(target_box[1]-box[3], box[1]-target_box[3], 0.0)
        if math.hypot(dx, dy) <= margin:
            nearby.append({**edge, "projected_points": transform.apply(edge["raw_front_points"])})
    return nearby


def _rank_curve_candidates(step_edges, target_points, include_circle_fit=True):
    ranked = []
    target_array = np.asarray(target_points, dtype=float)
    target_box = (
        float(target_array[:, 0].min()), float(target_array[:, 1].min()),
        float(target_array[:, 0].max()), float(target_array[:, 1].max()),
    )
    for edge in step_edges:
        points = edge["projected_points"]
        point_array = np.asarray(points, dtype=float)
        box = (
            float(point_array[:, 0].min()), float(point_array[:, 1].min()),
            float(point_array[:, 0].max()), float(point_array[:, 1].max()),
        )
        dx = max(target_box[0]-box[2], box[0]-target_box[2], 0.0)
        dy = max(target_box[1]-box[3], box[1]-target_box[3], 0.0)
        if math.hypot(dx, dy) > 8.0:
            continue
        target_distance = _nearest_distances(target_points, points)
        candidate_distance = _nearest_distances(points, target_points)
        if not len(target_distance):
            continue
        local_points = [point for point, distance in zip(points, candidate_distance) if distance <= 1.0]
        fit = _fit_circle(local_points) if include_circle_fit and len(local_points) >= 5 else None
        ranked.append({
            **edge,
            "target_mean_distance": float(np.mean(target_distance)),
            "target_p90_distance": float(np.percentile(target_distance, 90)),
            "target_coverage_0_5": float(np.mean(target_distance <= 0.5)),
            "candidate_local_point_count": len(local_points),
            "local_circle_fit": fit,
            "rank_score": float(np.mean(target_distance) + 0.35*np.percentile(target_distance, 90)),
        })
    ranked.sort(key=lambda item: item["rank_score"])
    # Projected duplicates are common (top/bottom faces). Keep distinct traces.
    unique = []
    signatures = set()
    for item in ranked:
        points = np.asarray(item["projected_points"], dtype=float)
        signature = tuple(round(value, 2) for value in (
            points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max(),
            points[:, 0].mean(), points[:, 1].mean(),
        ))
        if signature in signatures:
            continue
        signatures.add(signature)
        unique.append(item)
        if len(unique) >= 8:
            break
    return unique


def _rgb(entity, value):
    entity.rgb = value
    return entity


def _add_polyline(msp, points, color, layer, width=100):
    if len(points) < 2:
        return
    if width >= 60:
        black = msp.add_lwpolyline(points, dxfattribs={"layer": layer, "lineweight": min(width+70, 211)})
        _rgb(black, (5, 5, 5))
    entity = msp.add_lwpolyline(points, dxfattribs={"layer": layer, "lineweight": width})
    _rgb(entity, color)


def _render_document(doc, bounds, output: Path, size=(2600, 1700)):
    context = RenderContext(doc)
    context.current_layout_properties.set_colors(BACKGROUND)
    backend = SVGBackend()
    Frontend(context, backend).draw_layout(doc.modelspace(), finalize=True)
    xmin, ymin, xmax, ymax = bounds
    width, height = max(xmax-xmin, 1e-9), max(ymax-ymin, 1e-9)
    margin = 0.045
    render_box = BoundingBox2d([
        (xmin-width*margin, ymin-height*margin),
        (xmax+width*margin, ymax+height*margin),
    ])
    page = drawing_layout.Page.from_dxf_layout(doc.modelspace())
    svg_path = output.with_suffix(".svg")
    pdf_path = output.with_suffix(".pdf")
    svg_path.write_text(backend.get_string(page, render_box=render_box), encoding="utf-8")
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise RuntimeError(f"SVG conversion failed: {svg_path}")
    renderPDF.drawToFile(drawing, str(pdf_path))
    pdf = pdfium.PdfDocument(str(pdf_path))
    pw, ph = pdf[0].get_size()
    scale = max(size[0]/max(pw, 1.0), size[1]/max(ph, 1.0))
    image = pdf[0].render(scale=scale).to_pil().convert("RGB")
    pdf.close()
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, BACKGROUND)
    canvas.paste(image, ((size[0]-image.width)//2, (size[1]-image.height)//2))
    canvas.save(output)
    svg_path.unlink(missing_ok=True)
    pdf_path.unlink(missing_ok=True)


def _render_review(dxf_path, bounds, overlays, title, notes, output):
    doc = ezdxf.readfile(str(dxf_path))
    for layer in ("BLADE_2D_TARGET", "BLADE_STEP_CANDIDATE", "BLADE_STEP_ENVELOPE"):
        if layer not in doc.layers:
            doc.layers.add(name=layer, color=7)
    msp = doc.modelspace()
    for points, color, layer, width in overlays:
        _add_polyline(msp, points, color, layer, width)
    drawing_path = output.with_name(output.stem + "_drawing.png")
    _render_document(doc, bounds, drawing_path)
    with Image.open(drawing_path) as source:
        drawing = source.convert("RGB")
    header = 190
    board = Image.new("RGB", (drawing.width, drawing.height+header), BACKGROUND)
    board.paste(drawing, (0, header))
    draw = ImageDraw.Draw(board)
    draw.text((34, 22), title, font=_font(38), fill=WHITE)
    y = 76
    for note, color in notes:
        draw.ellipse((38, y+5, 54, y+21), fill=color)
        draw.text((68, y), note, font=_font(24), fill=WHITE if color != RED else "#fca5a5")
        y += 38
    board.save(output)
    drawing_path.unlink(missing_ok=True)


def _render_original_comparison(dxf_path, bounds, debug_path, original_path, comparison_path, title):
    original_doc = ezdxf.readfile(str(dxf_path))
    _render_document(original_doc, bounds, original_path, size=(2200, 1500))
    with Image.open(original_path) as source:
        original = source.convert("RGB")
    with Image.open(debug_path) as source:
        debug = source.convert("RGB")
    cell_width, cell_height = 1800, 1250
    original.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
    debug.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
    board = Image.new("RGB", (cell_width*2, cell_height+150), BACKGROUND)
    draw = ImageDraw.Draw(board)
    draw.text((30, 22), title, font=_font(38), fill=WHITE)
    draw.text((30, 82), "ORIGINAL DXF - no audit overlay", font=_font(28), fill=WHITE)
    draw.text((cell_width+30, 82), "DEBUG OVERLAY - same drawing region", font=_font(28), fill=WHITE)
    board.paste(original, ((cell_width-original.width)//2, 150+(cell_height-original.height)//2))
    board.paste(debug, (cell_width+(cell_width-debug.width)//2, 150+(cell_height-debug.height)//2))
    board.save(comparison_path)


def run(step_path: Path, dxf_path: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    print("[1/7] loading STEP assembly", flush=True)
    components = extract_leaf_components(str(step_path))
    component = next((item for item in components if item.name == "AL0W_BLADE_H-R01"), None)
    if component is None:
        raise RuntimeError("AL0W_BLADE_H-R01 was not found in the STEP assembly")

    print("[2/7] extracting DXF dimensions and local view", flush=True)
    dimensions = _dimension_map(dxf_path)
    outer = dimensions.get("7ADF")
    radius_dim = dimensions.get("FC4D")
    if outer is None or radius_dim is None:
        raise RuntimeError("Required DXF dimensions 7ADF/FC4D were not found")

    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    analyzer = DxfStructure2DAnalyzer(extractor.document.modelspace())
    outer_endpoints = [
        tuple(outer.points["defpoint2"][:2]),
        tuple(outer.points["defpoint3"][:2]),
    ]
    outer_midpoint = (
        (outer_endpoints[0][0] + outer_endpoints[1][0]) / 2.0,
        (outer_endpoints[0][1] + outer_endpoints[1][1]) / 2.0,
    )
    hub = _hub_center(analyzer, dimensions, outer_midpoint)
    print("[3/7] generating front HLR projection", flush=True)
    step_views = AdaptiveProjectionCandidateGenerator(max_views=6).project_all(component.shape)
    step_view = step_views["front"]
    print("[4/7] registering dimension-local view", flush=True)
    candidate, registration, _initial_transform = _select_local_registration(
        extractor, hub, float(outer.nominal_value), step_view
    )
    nominal = float(outer.nominal_value)
    cfg = dict(outer.tolerance_config or {})
    lower = nominal + float(cfg.get("lower_dev") or 0.0)
    upper = nominal + float(cfg.get("upper_dev") or 0.0)
    envelope_phase = _geometry_constrained_envelope_phase(
        registration,
        step_view,
        hub,
        outer_endpoints,
        nominal,
        lower,
        upper,
    )
    span_candidate = candidate
    span_view_name = "front+drawing_local_phase"
    span_view = step_view
    span_registration = envelope_phase["payload"]
    span_transform = envelope_phase["transform"]
    radius_association = analyzer.analyze_dimension(radius_dim)
    radius_edges = radius_association.get("dimension_target_edges") or []
    exact_radius_edges = [
        edge for edge in radius_edges
        if abs(float(edge.get("radius") or 0.0) - float(radius_dim.nominal_value)) <= 0.01
    ]
    if not exact_radius_edges:
        raise RuntimeError("R2.15 target arc could not be isolated from its leader attachment")
    radius_handle = str(exact_radius_edges[0]["handle"]).split("/")[-1]
    target_arc = _sample_dxf_arc(extractor.document, radius_handle)

    print("[5/7] sampling curves and resolving near-tied poses from the R2.15 attachment", flush=True)
    identity = SimilarityTransform2D(1.0, 0.0, (0.0, 0.0), False)
    raw_step_edges = _sample_step_edges(component.shape, identity)
    print(f"      sampled {len(raw_step_edges)} STEP edges", flush=True)
    pose_payloads = [registration] + list(registration.get("alternatives") or [])
    best_geometry_score = max(float(item.get("score", 0.0)) for item in pose_payloads)
    pose_trials = []
    seen_poses = set()
    for payload in pose_payloads:
        if float(payload.get("score", 0.0)) < best_geometry_score - 0.02:
            continue
        raw_transform = payload.get("transform") or {}
        pose_key = (
            round(float(raw_transform["rotation_degrees"]), 3),
            bool(raw_transform.get("mirrored", False)),
            round(float(raw_transform["scale"]), 4),
        )
        if pose_key in seen_poses:
            continue
        seen_poses.add(pose_key)
        hypothesis = SimilarityTransform2D(
            scale=float(raw_transform["scale"]),
            rotation_degrees=float(raw_transform["rotation_degrees"]),
            translation=tuple(raw_transform["translation"]),
            mirrored=bool(raw_transform.get("mirrored", False)),
        )
        hypothesis = _refine_transform_center(hypothesis, step_view, hub)
        hypothesis_edges = _nearby_transformed_edges(raw_step_edges, target_arc, hypothesis)
        print(f"      pose prefilter kept {len(hypothesis_edges)} edges", flush=True)
        ranked = _rank_curve_candidates(hypothesis_edges, target_arc, include_circle_fit=False)
        print(
            f"      pose {hypothesis.rotation_degrees:.3f} mirrored={hypothesis.mirrored} "
            f"candidates={len(ranked)}",
            flush=True,
        )
        if ranked:
            pose_trials.append((ranked[0]["rank_score"], payload, hypothesis, hypothesis_edges, ranked))
    if not pose_trials:
        raise RuntimeError("No near-tied registration pose could be checked against R2.15")
    pose_trials.sort(key=lambda item: item[0])
    _pose_score, selected_registration, transform, step_edges, _pose_curve_candidates = pose_trials[0]
    curve_candidates = _rank_curve_candidates(step_edges, target_arc, include_circle_fit=False)
    best_curve = curve_candidates[0]
    best_candidate_distance = _nearest_distances(best_curve["projected_points"], target_arc)
    best_local_points = [
        point for point, distance in zip(best_curve["projected_points"], best_candidate_distance)
        if distance <= 1.0
    ]
    fit = _fit_circle(best_local_points) if len(best_local_points) >= 5 else {}
    best_curve["local_circle_fit"] = fit or None
    radius_delta = abs(float(fit.get("radius", 1e9)) - float(radius_dim.nominal_value))
    curve_verified = (
        best_curve["target_mean_distance"] <= 0.25
        and best_curve["target_p90_distance"] <= 0.50
        and radius_delta <= 0.20
    )

    model_span = float(envelope_phase["physical_span_mm"])
    span_metric_matches = bool(envelope_phase["inside_tolerance_interval"])
    span_verified = (
        span_metric_matches
        and float(span_registration.get("score", 0.0)) >= 0.72
        and float(span_registration.get("extent_score", 0.0)) >= 0.80
    )

    outline_paths = envelope_phase["mapped_paths"]

    focus = (
        span_candidate.bbox[0]-8.0,
        min(span_candidate.bbox[1]-8.0, min(point[1] for point in outer_endpoints)-8.0),
        span_candidate.bbox[2]+8.0,
        span_candidate.bbox[3]+14.0,
    )
    envelope_overlays = [
        (path, CYAN, "BLADE_STEP_ENVELOPE", 55) for path in outline_paths
    ] + [
        (outer_endpoints, MAGENTA, "BLADE_2D_TARGET", 140),
    ]
    print("[6/7] rendering outer-envelope review", flush=True)
    _render_review(
        dxf_path,
        focus,
        envelope_overlays,
        "Blade envelope tolerance: 75.19 +0.20/-0.00 mm",
        [
            ("magenta: DXF measured span", MAGENTA),
            (f"cyan: registered STEP {span_view_name} projection", CYAN),
            (f"phase {span_transform.rotation_degrees:.3f} deg; dimension used only as tie-breaker={envelope_phase['dimension_used_as_tie_breaker']}", YELLOW),
            (("VERIFIED" if span_verified else "CANDIDATE ONLY") + f": STEP span {model_span:.3f} mm; contour {float(span_registration.get('score', 0.0)):.3f}", GREEN if span_verified else RED),
        ],
        output_dir / "01_outer_envelope_75_19_review.png",
    )

    curve_overlays = [
        (target_arc, MAGENTA, "BLADE_2D_TARGET", 155),
        (best_curve["projected_points"], CYAN, "BLADE_STEP_CANDIDATE", 95),
    ]
    curve_focus = (
        candidate.bbox[0]-8.0, candidate.bbox[1]-18.0,
        candidate.bbox[2]+8.0, candidate.bbox[3]+14.0,
    )
    print("[7/7] rendering curved-bend review", flush=True)
    _render_review(
        dxf_path,
        curve_focus,
        curve_overlays,
        "Blade profile bend: 7-R2.15",
        [
            ("magenta: exact DXF R2.15 target arc", MAGENTA),
            (f"cyan: STEP {best_curve['curve_type']} edge #{best_curve['edge_index']}", CYAN),
            (("VERIFIED" if curve_verified else "CANDIDATE ONLY") + f": mean {best_curve['target_mean_distance']:.3f} mm, p90 {best_curve['target_p90_distance']:.3f} mm, fitted R {float(fit.get('radius', 0.0)):.3f}", GREEN if curve_verified else RED),
        ],
        output_dir / "02_blade_bend_R2_15_review.png",
    )

    # Minimal endpoint-only debug: the full HLR overlay is intentionally not
    # drawn here because overlapping blade edges hide the actual failure.
    step_support_points = [point for path in outline_paths for point in path]
    support_x = (
        min(point[0] for point in step_support_points),
        max(point[0] for point in step_support_points),
    )
    support_y = (
        min(point[1] for point in step_support_points),
        max(point[1] for point in step_support_points),
    )
    marker_bottom = min(support_y[0], outer_endpoints[0][1])
    marker_top = max(support_y[1], outer_endpoints[0][1])
    endpoint_overlays = [
        (outer_endpoints, MAGENTA, "BLADE_2D_TARGET", 155),
        ([(outer_endpoints[0][0], marker_bottom), (outer_endpoints[0][0], marker_top)], MAGENTA, "BLADE_2D_TARGET", 95),
        ([(outer_endpoints[1][0], marker_bottom), (outer_endpoints[1][0], marker_top)], MAGENTA, "BLADE_2D_TARGET", 95),
        ([(support_x[0], marker_bottom), (support_x[0], marker_top)], CYAN, "BLADE_STEP_ENVELOPE", 95),
        ([(support_x[1], marker_bottom), (support_x[1], marker_top)], CYAN, "BLADE_STEP_ENVELOPE", 95),
        ([(outer_endpoints[0][0], marker_top), (support_x[0], marker_top)], YELLOW, "BLADE_STEP_CANDIDATE", 65),
        ([(outer_endpoints[1][0], marker_top), (support_x[1], marker_top)], YELLOW, "BLADE_STEP_CANDIDATE", 65),
    ]
    endpoint_focus = (
        min(outer_endpoints[0][0], support_x[0])-5.0,
        marker_bottom-5.0,
        max(outer_endpoints[1][0], support_x[1])+5.0,
        marker_top+10.0,
    )
    _render_review(
        dxf_path,
        endpoint_focus,
        endpoint_overlays,
        "DEBUG A - 75.19 endpoint and STEP-extrema comparison",
        [
            ("magenta: two DXF measurement supports", MAGENTA),
            ("cyan: registered STEP extrema; yellow: endpoint offsets", CYAN),
            (f"BLOCKED: contour {float(span_registration.get('score', 0.0)):.3f}, scale {span_transform.scale:.3f}; side/section representation does not register reliably", RED),
        ],
        output_dir / "03_outer_envelope_endpoint_debug.png",
    )

    candidate_points = best_curve["projected_points"]
    connector_overlays = []
    for index in np.linspace(0, len(target_arc)-1, 7).round().astype(int):
        target_point = target_arc[int(index)]
        nearest = min(candidate_points, key=lambda point: math.dist(point, target_point))
        connector_overlays.append(
            ([target_point, nearest], YELLOW, "BLADE_STEP_CANDIDATE", 20)
        )
    curve_debug_points = target_arc + candidate_points
    curve_text = tuple(radius_dim.points.get("text_mid") or target_arc[0])
    curve_debug_focus = (
        min(min(point[0] for point in curve_debug_points)-3.0, curve_text[0]-3.0),
        min(min(point[1] for point in curve_debug_points)-3.0, curve_text[1]-3.0),
        max(max(point[0] for point in curve_debug_points)+3.0, curve_text[0]+8.0),
        max(max(point[1] for point in curve_debug_points)+3.0, curve_text[1]+3.0),
    )
    _render_review(
        dxf_path,
        curve_debug_focus,
        [
            *connector_overlays,
            (target_arc, MAGENTA, "BLADE_2D_TARGET", 50),
            (candidate_points, CYAN, "BLADE_STEP_CANDIDATE", 38),
        ],
        "DEBUG B - 7-R2.15 local curve correspondence",
        [
            ("magenta: exact DXF R2.15 arc; cyan: projected STEP B-Spline", MAGENTA),
            ("yellow: sampled nearest-point errors", YELLOW),
            (f"BLOCKED: mean {best_curve['target_mean_distance']:.3f}, p90 {best_curve['target_p90_distance']:.3f}, fitted R {float(fit.get('radius', 0.0)):.3f} vs R2.150", RED),
        ],
        output_dir / "04_R2_15_local_curve_debug.png",
    )

    _render_original_comparison(
        dxf_path,
        endpoint_focus,
        output_dir / "03_outer_envelope_endpoint_debug.png",
        output_dir / "05_75_19_original_dxf.png",
        output_dir / "05_75_19_original_vs_debug.png",
        "75.19 mm - original drawing and endpoint debug",
    )
    _render_original_comparison(
        dxf_path,
        curve_debug_focus,
        output_dir / "04_R2_15_local_curve_debug.png",
        output_dir / "06_R2_15_original_dxf.png",
        output_dir / "06_R2_15_original_vs_debug.png",
        "7-R2.15 - original drawing and curve debug",
    )

    angle_dimensions = [
        {
            "handle": handle,
            "nominal": float(dim.nominal_value),
            "raw_text": dim.raw_text,
            "tolerance": dim.tolerance_config,
        }
        for handle, dim in dimensions.items()
        if str(dim.dimension_category).upper() == "ANGULAR"
    ]
    report = {
        "schema_version": 1,
        "method": "BLADE_CURVE_TOLERANCE_GEOMETRY_EXPERIMENT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "component": component.name,
        "step_path": str(step_path.resolve()),
        "dxf_path": str(dxf_path.resolve()),
        "registration": {
            "selected_dxf_view": candidate.view_id,
            "dxf_bbox": [round(float(value), 6) for value in candidate.bbox],
            "initial_best_contour_score": round(float(registration.get("score", 0.0)), 6),
            "selected_contour_score": round(float(selected_registration.get("score", 0.0)), 6),
            "extent_score": round(float(selected_registration.get("extent_score", 0.0)), 6),
            "near_tied_pose_count": len(pose_trials),
            "pose_tie_breaker": "DXF_R2.15_TARGET_TO_PROJECTED_BREP_CURVE",
            "hub_center": [round(float(value), 6) for value in hub],
            "refined_transform": transform.to_dict(),
        },
        "debug_images": [
            "03_outer_envelope_endpoint_debug.png",
            "04_R2_15_local_curve_debug.png",
            "05_75_19_original_dxf.png",
            "05_75_19_original_vs_debug.png",
            "06_R2_15_original_dxf.png",
            "06_R2_15_original_vs_debug.png",
        ],
        "tests": [
            {
                "dimension_handle": str(outer.entity_handle),
                "semantic_feature": "blade_outer_envelope_diameter",
                "nominal_mm": nominal,
                "lower_limit_mm": lower,
                "upper_limit_mm": upper,
                "step_projected_span_mm": round(model_span, 6),
                "metric_matches_tolerance_interval": span_metric_matches,
                "selected_step_view": span_view_name,
                "selected_dxf_view": span_candidate.view_id,
                "contour_score": round(float(span_registration.get("score", 0.0)), 6),
                "extent_score": round(float(span_registration.get("extent_score", 0.0)), 6),
                "projection_phase": {
                    "method": envelope_phase["method"],
                    "rotation_degrees": round(float(span_transform.rotation_degrees), 6),
                    "mirrored": bool(span_transform.mirrored),
                    "dimension_axis": envelope_phase["dimension_axis"],
                    "hypothesis_count": envelope_phase["hypothesis_count"],
                    "geometry_tied_count": envelope_phase["geometry_tied_count"],
                    "dimension_used_as_tie_breaker": envelope_phase["dimension_used_as_tie_breaker"],
                    "nominal_error_mm": round(float(envelope_phase["nominal_error_mm"]), 6),
                },
                "status": "VERIFIED" if span_verified else "CANDIDATE_ONLY",
                "reason": None if span_verified else "The numerical span is plausible, but the dimension-local side-view registration is below the automatic contour gate.",
                "review_image": "01_outer_envelope_75_19_review.png",
            },
            {
                "dimension_handle": str(radius_dim.entity_handle),
                "semantic_feature": "repeated_blade_profile_bend",
                "multiplicity": 7,
                "nominal_radius_mm": float(radius_dim.nominal_value),
                "dxf_target_handle": radius_handle,
                "status": "VERIFIED" if curve_verified else "CANDIDATE_ONLY",
                "reason": None if curve_verified else "Projected location is plausible, but B-Spline curvature/fit gates do not all pass.",
                "best_candidate": {
                    key: value for key, value in best_curve.items()
                    if key not in {"raw_front_points", "projected_points"}
                },
                "top_candidates": [
                    {
                        "edge_index": item["edge_index"],
                        "curve_type": item["curve_type"],
                        "target_mean_distance": round(item["target_mean_distance"], 6),
                        "target_p90_distance": round(item["target_p90_distance"], 6),
                        "target_coverage_0_5": round(item["target_coverage_0_5"], 6),
                        "local_circle_fit": item["local_circle_fit"],
                    }
                    for item in curve_candidates[:5]
                ],
                "review_image": "02_blade_bend_R2_15_review.png",
            },
            {
                "semantic_feature": "blade_angle",
                "status": "NOT_TESTED_NO_UNAMBIGUOUS_TOLERANCED_NATIVE_DIMENSION",
                "native_angular_dimensions": angle_dimensions,
                "reason": "The DXF has angular dimensions, but none is an unambiguous native 21-degree blade tolerance; this experiment does not relabel a hub/repetition angle as blade pitch.",
            },
        ],
    }
    (output_dir / "blade_curve_tolerance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Blade curve tolerance experiment\n\n"
        "This isolated run compares the original annotated DXF with the registered STEP projection. "
        "Magenta is drawing evidence and cyan is STEP evidence. VERIFIED requires semantic, location, "
        "and metric gates; CANDIDATE_ONLY is intentionally excluded from automatic case ingestion.\n",
        encoding="utf-8",
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
        "registration": report["registration"],
        "tests": [
            {key: item.get(key) for key in ("dimension_handle", "semantic_feature", "status", "review_image")}
            for item in report["tests"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
