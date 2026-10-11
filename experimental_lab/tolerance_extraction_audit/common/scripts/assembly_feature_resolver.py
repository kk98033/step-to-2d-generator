"""Assembly-aware tolerance feature resolution for the experimental pipeline.

The resolver keeps XCAF occurrence placements, projects every leaf occurrence
through the already-selected full-assembly section pose, and attributes native
DXF dimension endpoints to their owning components.  It deliberately remains
outside the production annotation engine.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

from auto_2d_drawing.config import VIEW_CONFIG
from auto_2d_drawing.feature_extractor import FeatureExtractor
from auto_2d_drawing.step_reader import get_label_id, get_label_name, load_step
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import ExtractedDimension
from auto_2d_drawing.tolerance.feature_graph import FeatureGraphExtractor
from auto_2d_drawing.tolerance.historical_manifest import parse_part_identity
from auto_2d_drawing.tolerance.ingest_historical_data import (
    HistoricalDataIngestor,
    _extract_dxf_worker,
)
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D
from auto_2d_drawing.view_projector import ViewProjector
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from run_blade_curve_tolerance_experiment import (
    CYAN,
    GREEN,
    MAGENTA,
    RED,
    YELLOW,
    _edge_points,
    _render_original_comparison,
    _render_review,
)
from run_fan_half_section_projection_experiment import (
    ORANGE,
    _central_section_curves,
    _project,
    _shape_bounds,
)


Point2D = Tuple[float, float]


@dataclass
class AssemblyOccurrence:
    occurrence_entry: str
    definition_entry: str
    name: str
    shape: Any
    bounds: Tuple[float, float, float, float, float, float]
    graph: Any
    axial_planes: List[float]
    section_paths: List[List[Point2D]] = field(default_factory=list)
    projected_bbox: Optional[Tuple[float, float, float, float]] = None


def _bounds(shape) -> Tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    return tuple(float(value) for value in box.Get())


def _cross(left: Sequence[float], right: Sequence[float]) -> Tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def view_axis_2d(view_name: str, axis_index: int) -> Point2D:
    """Project one world-space coordinate axis into ViewProjector coordinates."""
    config = VIEW_CONFIG[view_name]
    direction = tuple(float(value) for value in config["direction"])
    horizontal = tuple(float(value) for value in config["up"])
    vertical = _cross(direction, horizontal)
    axis = tuple(1.0 if index == axis_index else 0.0 for index in range(3))
    raw = (_dot(axis, horizontal), _dot(axis, vertical))
    norm = math.hypot(*raw)
    if norm <= 1e-9:
        raise ValueError(f"Axis {axis_index} is hidden in {view_name} projection")
    return raw[0] / norm, raw[1] / norm


def _point_segment_distance(point: Point2D, start: Point2D, end: Point2D) -> float:
    vx, vy = end[0] - start[0], end[1] - start[1]
    wx, wy = point[0] - start[0], point[1] - start[1]
    denominator = vx * vx + vy * vy
    parameter = 0.0 if denominator <= 1e-12 else max(
        0.0, min(1.0, (wx * vx + wy * vy) / denominator)
    )
    nearest = start[0] + parameter * vx, start[1] + parameter * vy
    return math.dist(point, nearest)


def _path_distance(point: Point2D, paths: Iterable[Sequence[Point2D]]) -> float:
    return min(
        (
            _point_segment_distance(point, start, end)
            for path in paths
            for start, end in zip(path, path[1:])
        ),
        default=float("inf"),
    )


def _project_bbox(bounds: Sequence[float], view_name: str) -> Tuple[float, float, float, float]:
    config = VIEW_CONFIG[view_name]
    direction = tuple(float(value) for value in config["direction"])
    horizontal = tuple(float(value) for value in config["up"])
    vertical = _cross(direction, horizontal)
    points = []
    for x in (bounds[0], bounds[3]):
        for y in (bounds[1], bounds[4]):
            for z in (bounds[2], bounds[5]):
                point = (x, y, z)
                points.append((_dot(point, horizontal), _dot(point, vertical)))
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _axial_planes(shape, axis_index: int) -> List[float]:
    extractor = FeatureExtractor(shape)
    values = set()
    for plane in extractor.planes:
        normal = plane.get("normal") or []
        center = plane.get("center_of_mass") or plane.get("location") or []
        if len(normal) < 3 or len(center) < 3:
            continue
        if abs(float(normal[axis_index])) < 0.98:
            continue
        values.add(round(float(center[axis_index]), 6))
    return sorted(values)


def extract_component_occurrences(step_path: Path, axis_index: int) -> List[AssemblyOccurrence]:
    """Return placed XCAF leaf occurrences instead of unplaced definitions."""
    app = XCAFApp_Application.GetApplication()
    document = TDocStd_Document("MDTV-XCAF")
    app.NewDocument("MDTV-XCAF", document)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(False)
    reader.SetLayerMode(False)
    reader.SetNameMode(True)
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone or not reader.Transfer(document):
        raise RuntimeError(f"Unable to load XCAF assembly: {step_path}")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(document.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    occurrences: List[AssemblyOccurrence] = []

    def visit(label) -> None:
        if shape_tool.IsReference(label):
            referred = TDF_Label()
            shape_tool.GetReferredShape(label, referred)
            shape = shape_tool.GetShape(label)
            if shape.IsNull():
                return
            name = get_label_name(referred) or get_label_name(label) or get_label_id(referred)
            occurrences.append(AssemblyOccurrence(
                occurrence_entry=get_label_id(label),
                definition_entry=get_label_id(referred),
                name=name,
                shape=shape,
                bounds=_bounds(shape),
                graph=FeatureGraphExtractor().build_graph(shape),
                axial_planes=_axial_planes(shape, axis_index),
            ))
            return
        if shape_tool.IsAssembly(label):
            children = TDF_LabelSequence()
            shape_tool.GetComponents(label, children)
            for index in range(1, children.Length() + 1):
                visit(children.Value(index))
            return
        shape = shape_tool.GetShape(label)
        if shape.IsNull():
            return
        name = get_label_name(label) or get_label_id(label)
        occurrences.append(AssemblyOccurrence(
            occurrence_entry=get_label_id(label),
            definition_entry=get_label_id(label),
            name=name,
            shape=shape,
            bounds=_bounds(shape),
            graph=FeatureGraphExtractor().build_graph(shape),
            axial_planes=_axial_planes(shape, axis_index),
        ))

    for index in range(1, free_shapes.Length() + 1):
        visit(free_shapes.Value(index))
    return occurrences


def _nearest_plane_reference(
    occurrence: AssemblyOccurrence,
    point: Point2D,
    axis_2d: Point2D,
    axis_index: int,
) -> Optional[Dict[str, Any]]:
    if not occurrence.axial_planes:
        return None
    projected_value = point[0] * axis_2d[0] + point[1] * axis_2d[1]
    # VIEW_CONFIG uses orthonormal axes.  Projecting the 3D axial coordinate
    # and then taking its dot product with ``axis_2d`` therefore recovers the
    # original signed world coordinate without a model-specific convention.
    error, coordinate = min(
        (
            (abs(projected_value - coordinate), coordinate)
            for coordinate in occurrence.axial_planes
        ),
        key=lambda item: (item[0], item[1]),
    )
    return {
        "coordinate": round(coordinate, 6),
        "projected_coordinate": round(coordinate, 6),
        "error_mm": round(error, 6),
    }


def _owner_for_point(
    point: Point2D,
    occurrences: Sequence[AssemblyOccurrence],
    axis_2d: Point2D,
    axis_index: int,
) -> Dict[str, Any]:
    ranked = sorted(
        (
            (_path_distance(point, occurrence.section_paths), occurrence)
            for occurrence in occurrences
            if occurrence.section_paths
        ),
        key=lambda item: (item[0], item[1].name),
    )
    best_distance, best = ranked[0] if ranked else (float("inf"), None)
    second_distance = ranked[1][0] if len(ranked) > 1 else float("inf")
    direct = bool(
        best is not None
        and best_distance <= 0.80
        and second_distance - best_distance >= 0.08
    )
    if best is not None and direct:
        return {
            "component": best.name,
            "occurrence_entry": best.occurrence_entry,
            "method": "SECTION_CONTOUR_NEAREST_OWNER_V1",
            "distance_mm": round(best_distance, 6),
            "margin_mm": round(second_distance - best_distance, 6),
            "confidence": "DIRECT",
            "plane": _nearest_plane_reference(best, point, axis_2d, axis_index),
        }

    fallback = []
    for occurrence in occurrences:
        if occurrence.projected_bbox is None:
            continue
        xmin, ymin, xmax, ymax = occurrence.projected_bbox
        if not (xmin - 0.6 <= point[0] <= xmax + 0.6 and ymin - 0.6 <= point[1] <= ymax + 0.6):
            continue
        plane = _nearest_plane_reference(occurrence, point, axis_2d, axis_index)
        if plane and float(plane["error_mm"]) <= 0.35:
            fallback.append((float(plane["error_mm"]), occurrence, plane))
    if fallback:
        error, occurrence, plane = min(fallback, key=lambda item: (item[0], item[1].name))
        return {
            "component": occurrence.name,
            "occurrence_entry": occurrence.occurrence_entry,
            "method": "COMPONENT_BBOX_PLUS_AXIAL_PLANE_V1",
            "distance_mm": None,
            "margin_mm": None,
            "confidence": "INFERRED",
            "plane": plane,
        }
    return {
        "component": None,
        "method": "NO_COMPONENT_OWNER",
        "distance_mm": None if math.isinf(best_distance) else round(best_distance, 6),
        "confidence": "UNRESOLVED",
    }


def _diameter_candidate(occurrence: AssemblyOccurrence, nominal: float) -> Optional[Dict[str, Any]]:
    tolerance = max(0.03, abs(nominal) * 0.003)
    candidates = []
    for node in occurrence.graph.nodes:
        diameter = (node.nominal or {}).get("diameter")
        if not isinstance(diameter, (int, float)):
            continue
        difference = abs(float(diameter) - nominal)
        if difference <= tolerance:
            candidates.append((difference, node))
    if not candidates:
        return None
    difference, node = min(candidates, key=lambda item: (item[0], item[1].id))
    surface = "inner_cylindrical_surface" if node.feature_type == "hole" else "outer_cylindrical_surface"
    return {
        "feature_id": f"{occurrence.name}:{node.id}",
        "feature_type": node.feature_type,
        "semantic_path": f"component[{occurrence.name}].{surface}.diameter",
        "step_nominal_mm": round(float(node.nominal["diameter"]), 6),
        "nominal_error_mm": round(difference, 6),
    }


def _metric_components(
    first: Point2D,
    second: Point2D,
    axis_2d: Point2D,
) -> Tuple[float, float]:
    delta = second[0] - first[0], second[1] - first[1]
    axial = abs(delta[0] * axis_2d[0] + delta[1] * axis_2d[1])
    transverse = abs(delta[0] * -axis_2d[1] + delta[1] * axis_2d[0])
    return axial, transverse


def classify_relation_kind(
    first_owner: Optional[str],
    second_owner: Optional[str],
    first_confidence: str,
    second_confidence: str,
) -> str:
    if not first_owner or not second_owner:
        return "UNRESOLVED"
    if first_owner == second_owner:
        return "COMPONENT_FEATURE" if first_confidence == second_confidence == "DIRECT" else "COMPONENT_FEATURE_CANDIDATE"
    return "ASSEMBLY_RELATION" if first_confidence == second_confidence == "DIRECT" else "ASSEMBLY_RELATION_CANDIDATE"


def _resolve_dimension(
    dimension: ExtractedDimension,
    occurrences: Sequence[AssemblyOccurrence],
    transform: SimilarityTransform2D,
    axis_2d: Point2D,
    axis_index: int,
) -> Dict[str, Any]:
    raw_endpoints = [
        (dimension.points or {}).get("defpoint2"),
        (dimension.points or {}).get("defpoint3"),
    ]
    base = {
        "entity_handle": str(dimension.entity_handle).upper(),
        "nominal_value": float(dimension.nominal_value),
        "original_dimension_category": dimension.dimension_category,
        "tolerance_config": dict(dimension.tolerance_config or {}),
    }
    if not all(raw_endpoints):
        return {**base, "status": "UNRESOLVED", "reason": "MISSING_DIMENSION_ENDPOINTS"}
    endpoints = [
        transform.inverse_point((float(point[0]), float(point[1])))
        for point in raw_endpoints
    ]
    owners = [
        _owner_for_point(point, occurrences, axis_2d, axis_index)
        for point in endpoints
    ]
    axial_span, transverse_span = _metric_components(endpoints[0], endpoints[1], axis_2d)
    nominal = abs(float(dimension.nominal_value))
    relation_kind = classify_relation_kind(
        owners[0].get("component"), owners[1].get("component"),
        owners[0].get("confidence", "UNRESOLVED"), owners[1].get("confidence", "UNRESOLVED"),
    )
    evidence = {
        "inverse_projected_endpoints": [[round(value, 6) for value in point] for point in endpoints],
        "endpoint_owners": owners,
        "axial_span_mm": round(axial_span, 6),
        "transverse_span_mm": round(transverse_span, 6),
        "relation_kind": relation_kind,
    }

    if relation_kind.startswith("COMPONENT_FEATURE"):
        owner_name = owners[0]["component"]
        occurrence = next(item for item in occurrences if item.name == owner_name)
        candidate = _diameter_candidate(occurrence, nominal)
        transverse_error = abs(transverse_span - nominal)
        diameter_geometry = bool(
            candidate
            and axial_span <= max(0.50, nominal * 0.03)
            and transverse_error <= max(0.35, nominal * 0.01)
        )
        if diameter_geometry and relation_kind == "COMPONENT_FEATURE":
            return {
                **base,
                "status": "VERIFIED",
                "feature_class": "component_feature",
                "feature_id": candidate["feature_id"],
                "semantic_path": candidate["semantic_path"],
                "effective_dimension_category": "DIAMETER",
                "reason": "Both endpoints map to one placed component and a unique cylindrical BRep feature.",
                "verification_methods": [
                    "XCAF_PLACED_OCCURRENCE_SECTION_V1",
                    "SECTION_CONTOUR_ENDPOINT_OWNERSHIP_V1",
                    "COMPONENT_BREP_DIAMETER_MATCH_V1",
                ],
                "feature_candidate": candidate,
                "metric_error_mm": round(transverse_error, 6),
                "assembly_evidence": evidence,
            }
        axial_error = abs(axial_span - nominal)
        planes = [owner.get("plane") or {} for owner in owners]
        distinct_planes = bool(
            all("coordinate" in plane for plane in planes)
            and abs(float(planes[0]["coordinate"]) - float(planes[1]["coordinate"])) > 1e-6
        )
        axial_geometry = bool(
            distinct_planes
            and transverse_span <= max(1.0, nominal * 0.20)
            and axial_error <= max(0.25, nominal * 0.015)
            and max(float(plane.get("error_mm", 999.0)) for plane in planes) <= 0.25
        )
        if axial_geometry and relation_kind == "COMPONENT_FEATURE":
            coordinates = [round(float(plane["coordinate"]), 6) for plane in planes]
            semantic = (
                f"component[{owner_name}].composite_axial_span"
                f"[{coordinates[0]:g}->{coordinates[1]:g}]"
            )
            return {
                **base,
                "status": "VERIFIED",
                "feature_class": "component_composite_feature",
                "feature_id": semantic,
                "semantic_path": semantic,
                "effective_dimension_category": "LINEAR",
                "reason": "Both endpoints map to distinct axial faces on one placed component and the STEP span matches the nominal dimension.",
                "verification_methods": [
                    "XCAF_PLACED_OCCURRENCE_SECTION_V1",
                    "SECTION_CONTOUR_ENDPOINT_OWNERSHIP_V1",
                    "COMPONENT_COMPOSITE_AXIAL_SPAN_V1",
                ],
                "metric_error_mm": round(axial_error, 6),
                "assembly_evidence": evidence,
            }

    if relation_kind.startswith("ASSEMBLY_RELATION"):
        axial_error = abs(axial_span - nominal)
        metric_passed = axial_error <= max(0.25, nominal * 0.015)
        direct = relation_kind == "ASSEMBLY_RELATION"
        status = "VERIFIED" if metric_passed and direct else "CANDIDATE_ONLY"
        first_name, second_name = owners[0].get("component"), owners[1].get("component")
        semantic = f"assembly.axial_span[{first_name}->{second_name}]"
        return {
            **base,
            "status": status,
            "feature_class": "assembly_relation_feature",
            "feature_id": semantic,
            "semantic_path": semantic,
            "effective_dimension_category": "LINEAR",
            "reason": (
                "Both endpoints map directly to different placed components and the STEP section span matches the nominal dimension."
                if status == "VERIFIED" else
                "A cross-component span is geometrically plausible, but at least one endpoint owner is inferred rather than attached to a visible section contour."
            ),
            "verification_methods": [
                "XCAF_PLACED_OCCURRENCE_SECTION_V1",
                "CROSS_COMPONENT_ENDPOINT_OWNERSHIP_V1",
                "ASSEMBLY_AXIAL_SPAN_METRIC_V1",
            ],
            "metric_error_mm": round(axial_error, 6),
            "assembly_evidence": evidence,
        }

    return {
        **base,
        "status": "UNRESOLVED",
        "feature_class": None,
        "feature_id": None,
        "semantic_path": None,
        "reason": "No unique component feature or direct cross-component relation passed all geometry gates.",
        "verification_methods": ["XCAF_PLACED_OCCURRENCE_SECTION_V1"],
        "assembly_evidence": evidence,
    }


def _render(
    dxf_path: Path,
    extractor: DxfProjectionViewExtractorV2,
    target_view_id: str,
    dimensions: Sequence[ExtractedDimension],
    rows: Sequence[Dict[str, Any]],
    occurrences: Sequence[AssemblyOccurrence],
    transform: SimilarityTransform2D,
    output_dir: Path,
    externally_verified_handles: Sequence[str] = (),
) -> Dict[str, str]:
    candidate = next(item for item in extractor.view_candidates if item.view_id == target_view_id)
    row_by_handle = {item["entity_handle"]: item for item in rows}
    component_colors = [CYAN, ORANGE, MAGENTA]
    overlays = []
    for index, occurrence in enumerate(occurrences):
        color = component_colors[index % len(component_colors)]
        overlays.extend((transform.apply(path), color, "BLADE_STEP_CANDIDATE", 12) for path in occurrence.section_paths)
    points_for_bounds = []
    view_xmin, view_ymin, view_xmax, view_ymax = candidate.bbox
    for dimension in dimensions:
        row = row_by_handle.get(str(dimension.entity_handle).upper())
        if not row:
            continue
        if (
            row.get("status") == "UNRESOLVED"
            and str(dimension.entity_handle).upper() in externally_verified_handles
        ):
            continue
        if (row.get("assembly_evidence") or {}).get("relation_kind") == "UNRESOLVED":
            continue
        endpoints = [
            tuple((dimension.points or {})[key][:2])
            for key in ("defpoint2", "defpoint3")
            if (dimension.points or {}).get(key)
        ]
        if len(endpoints) != 2:
            continue
        midpoint = (
            (endpoints[0][0] + endpoints[1][0]) / 2.0,
            (endpoints[0][1] + endpoints[1][1]) / 2.0,
        )
        if not (
            view_xmin - 20.0 <= midpoint[0] <= view_xmax + 20.0
            and view_ymin - 20.0 <= midpoint[1] <= view_ymax + 20.0
        ):
            continue
        points_for_bounds.extend(endpoints)
        status = row.get("status")
        color = GREEN if status == "VERIFIED" else YELLOW if status == "CANDIDATE_ONLY" else RED
        overlays.append((endpoints, color, "BLADE_2D_TARGET", 38))
    xmin, ymin, xmax, ymax = candidate.bbox
    if points_for_bounds:
        xmin = min(xmin, *(point[0] for point in points_for_bounds))
        ymin = min(ymin, *(point[1] for point in points_for_bounds))
        xmax = max(xmax, *(point[0] for point in points_for_bounds))
        ymax = max(ymax, *(point[1] for point in points_for_bounds))
    bounds = (xmin - 8.0, ymin - 12.0, xmax + 8.0, ymax + 12.0)
    review = output_dir / "01_assembly_feature_review.png"
    original = output_dir / "02_assembly_feature_original.png"
    comparison = output_dir / "03_assembly_feature_original_vs_review.png"
    _render_review(
        dxf_path,
        bounds,
        overlays,
        "ASSEMBLY-AWARE FEATURE RESOLUTION | Section E-E",
        [
            ("cyan/orange/magenta: placed STEP component section contours", CYAN),
            ("green: verified component or cross-component feature", GREEN),
            ("yellow: inferred-owner candidate; red: unresolved", YELLOW),
        ],
        review,
    )
    _render_original_comparison(
        dxf_path, bounds, review, original, comparison,
        "ASSEMBLY FEATURE RESOLVER | ORIGINAL VS DEBUG",
    )
    return {
        "review": review.name,
        "original": original.name,
        "original_vs_review": comparison.name,
    }


def _render_unresolved_reviews(
    dxf_path: Path,
    dimensions: Sequence[ExtractedDimension],
    rows: Sequence[Dict[str, Any]],
    occurrences: Sequence[AssemblyOccurrence],
    transform: SimilarityTransform2D,
    output_dir: Path,
    externally_verified_handles: Sequence[str] = (),
) -> List[Dict[str, str]]:
    """Render one uncluttered original/debug pair per geometrically-localized unresolved dimension."""
    dimensions_by_handle = {
        str(item.entity_handle).upper(): item for item in dimensions
    }
    occurrence_by_name = {item.name: item for item in occurrences}
    artifacts: List[Dict[str, str]] = []
    sequence = 4
    for row in rows:
        evidence = row.get("assembly_evidence") or {}
        if row.get("status") != "UNRESOLVED" or evidence.get("relation_kind") == "UNRESOLVED":
            continue
        handle = str(row.get("entity_handle", "")).upper()
        if handle in externally_verified_handles:
            continue
        dimension = dimensions_by_handle.get(handle)
        if dimension is None:
            continue
        endpoints = [
            tuple((dimension.points or {})[key][:2])
            for key in ("defpoint2", "defpoint3")
            if (dimension.points or {}).get(key)
        ]
        if len(endpoints) != 2:
            continue

        owner_names = {
            item.get("component")
            for item in evidence.get("endpoint_owners") or []
            if item.get("component")
        }
        overlays = []
        bound_points = list(endpoints)
        for owner_name in sorted(owner_names):
            occurrence = occurrence_by_name.get(owner_name)
            if occurrence is None:
                continue
            for path in occurrence.section_paths:
                placed_path = transform.apply(path)
                overlays.append((placed_path, CYAN, "BLADE_STEP_CANDIDATE", 16))
                bound_points.extend(placed_path)

        overlays.append((endpoints, RED, "BLADE_2D_TARGET", 46))
        for endpoint in endpoints:
            radius = 1.5
            overlays.append(([
                (endpoint[0] - radius, endpoint[1]),
                (endpoint[0] + radius, endpoint[1]),
            ], YELLOW, "BLADE_2D_TARGET", 42))
            overlays.append(([
                (endpoint[0], endpoint[1] - radius),
                (endpoint[0], endpoint[1] + radius),
            ], YELLOW, "BLADE_2D_TARGET", 42))

        endpoint_span = max(
            10.0,
            math.hypot(
                endpoints[1][0] - endpoints[0][0],
                endpoints[1][1] - endpoints[0][1],
            ),
        )
        endpoint_midpoint = (
            (endpoints[0][0] + endpoints[1][0]) / 2.0,
            (endpoints[0][1] + endpoints[1][1]) / 2.0,
        )
        for value in (dimension.points or {}).values():
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                point = (float(value[0]), float(value[1]))
                if math.hypot(
                    point[0] - endpoint_midpoint[0],
                    point[1] - endpoint_midpoint[1],
                ) <= endpoint_span * 2.0:
                    bound_points.append(point)
        if not bound_points:
            continue
        xmin = min(point[0] for point in bound_points)
        ymin = min(point[1] for point in bound_points)
        xmax = max(point[0] for point in bound_points)
        ymax = max(point[1] for point in bound_points)
        xpad = max(6.0, (xmax - xmin) * 0.06)
        ypad = max(8.0, (ymax - ymin) * 0.08)
        bounds = (xmin - xpad, ymin - ypad, xmax + xpad, ymax + ypad)

        tolerance = row.get("tolerance_config") or {}
        nominal = float(row.get("nominal_value") or 0.0)
        upper = float(tolerance.get("upper_dev") or 0.0)
        lower = float(tolerance.get("lower_dev") or 0.0)
        measured = float(evidence.get("transverse_span_mm") or 0.0)
        review = output_dir / f"{sequence:02d}_unresolved_{handle}_review.png"
        original = output_dir / f"{sequence + 1:02d}_unresolved_{handle}_original.png"
        comparison = output_dir / f"{sequence + 2:02d}_unresolved_{handle}_original_vs_review.png"
        _render_review(
            dxf_path,
            bounds,
            overlays,
            f"UNRESOLVED {handle} | {nominal:.3f} +{upper:.3f}/-{lower:.3f} mm",
            [
                ("red/yellow: native DXF dimension endpoints", RED),
                ("cyan: placed STEP central-section contour of inferred owner", CYAN),
                (f"inverse-projected span {measured:.3f} mm; no unique BRep diameter passed", YELLOW),
            ],
            review,
        )
        _render_original_comparison(
            dxf_path,
            bounds,
            review,
            original,
            comparison,
            f"UNRESOLVED {handle} | ORIGINAL VS ISOLATED DEBUG",
        )
        artifacts.append({
            "entity_handle": handle,
            "review": review.name,
            "original": original.name,
            "original_vs_review": comparison.name,
        })
        sequence += 3
    return artifacts


def run(
    step_path: Path,
    dxf_path: Path,
    baseline_path: Path,
    section_report_path: Path,
    output_dir: Path,
    current_final_path: Optional[Path] = None,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    section_report = json.loads(section_report_path.read_text(encoding="utf-8"))
    externally_verified_handles = set()
    if current_final_path and current_final_path.is_file():
        current_final = json.loads(current_final_path.read_text(encoding="utf-8"))
        externally_verified_handles = {
            str(item.get("dimension_handle", "")).upper()
            for item in current_final.get("tolerances") or []
            if item.get("status") == "VERIFIED"
        }
    selected = section_report["recommended_section_method"]
    if selected.get("kind") != "CENTRAL_SECTION_CURVES":
        raise RuntimeError("Assembly resolver requires a verified central-section registration")

    full_shape = load_step(str(step_path))
    full_bounds = _shape_bounds(full_shape)
    spans = [full_bounds[index + 3] - full_bounds[index] for index in range(3)]
    axial_axis = min(range(3), key=lambda index: spans[index])
    cut_axis = {"X": 0, "Y": 1, "Z": 2}[selected["cut_axis"]]
    axis_2d = view_axis_2d(selected["view_name"], axial_axis)
    transform_data = selected["transform"]
    transform = SimilarityTransform2D(
        scale=float(transform_data["scale"]),
        rotation_degrees=float(transform_data["rotation_degrees"]),
        translation=tuple(float(value) for value in transform_data["translation"]),
        mirrored=bool(transform_data.get("mirrored", False)),
    )

    occurrences = extract_component_occurrences(step_path, axial_axis)
    projector = ViewProjector()
    for occurrence in occurrences:
        section = _central_section_curves(occurrence.shape, full_bounds, cut_axis)
        view = _project(projector, section, selected["view_name"])
        occurrence.section_paths = []
        for edge in view["visible"]:
            points = list(_edge_points(edge))
            if len(points) >= 2:
                occurrence.section_paths.append(points)
        occurrence.projected_bbox = _project_bbox(occurrence.bounds, selected["view_name"])

    _source, payloads = _extract_dxf_worker(str(dxf_path))
    dimensions = [ExtractedDimension(**item) for item in payloads]
    dimensions, _suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
    dimensions = [item for item in dimensions if HistoricalDataIngestor._tolerance_is_plausible(item)]
    baseline_rows = {
        str(item.get("entity_handle", "")).upper(): item
        for item in baseline.get("dimensions") or []
    }
    targets = [
        item for item in dimensions
        if (baseline_rows.get(str(item.entity_handle).upper()) or {}).get("status") != "VERIFIED"
    ]
    rows = [
        _resolve_dimension(item, occurrences, transform, axis_2d, axial_axis)
        for item in targets
    ]
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    artifacts = _render(
        dxf_path, extractor, section_report["target_dxf_view"], targets,
        rows, occurrences, transform, output_dir, externally_verified_handles,
    )
    artifacts["unresolved_reviews"] = _render_unresolved_reviews(
        dxf_path, targets, rows, occurrences, transform, output_dir,
        externally_verified_handles,
    )
    report = {
        "schema_version": 1,
        "method": "ASSEMBLY_AWARE_FEATURE_RESOLVER_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "step": str(step_path),
        "drawing": str(dxf_path),
        "section_registration": {
            "source": str(section_report_path),
            "dxf_view": section_report["target_dxf_view"],
            "kind": selected["kind"],
            "cut_axis": selected["cut_axis"],
            "view_name": selected["view_name"],
            "contour_score": selected["contour_score"],
            "extent_score": selected["extent_score"],
        },
        "components": [
            {
                "name": item.name,
                "occurrence_entry": item.occurrence_entry,
                "definition_entry": item.definition_entry,
                "part_number": (
                    parse_part_identity(item.name).part_number
                    if parse_part_identity(item.name) else None
                ),
                "bounds": [round(value, 6) for value in item.bounds],
                "section_path_count": len(item.section_paths),
                "feature_node_count": len(item.graph.nodes),
                "axial_plane_count": len(item.axial_planes),
            }
            for item in occurrences
        ],
        "summary": {
            "tested_nonverified_dimensions": len(rows),
            "new_verified": sum(item["status"] == "VERIFIED" for item in rows),
            "candidate_only": sum(item["status"] == "CANDIDATE_ONLY" for item in rows),
            "unresolved": sum(item["status"] == "UNRESOLVED" for item in rows),
            "component_features": sum(item.get("feature_class") == "component_feature" for item in rows),
            "component_composite_features": sum(item.get("feature_class") == "component_composite_feature" for item in rows),
            "assembly_relation_features": sum(item.get("feature_class") == "assembly_relation_feature" for item in rows),
        },
        "dimensions": rows,
        "artifacts": artifacts,
    }
    (output_dir / "assembly_feature_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--section-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--current-final", type=Path)
    args = parser.parse_args()
    report = run(
        args.step.resolve(), args.dxf.resolve(), args.baseline.resolve(),
        args.section_report.resolve(), args.output_dir.resolve(),
        args.current_final.resolve() if args.current_final else None,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
