"""Reusable composite features built from the canonical STEP feature graph.

Composite features do not replace atomic canonical features.  They describe a
dimensioned relationship spanning a contiguous chain of atomic features, such
as a pilot chamfer, its transition fillet, and the locating shoulder behind it.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from auto_2d_drawing.tolerance.feature_graph import FeatureNode, FeatureRelationGraph
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D
from auto_2d_drawing.feature_extractor import FeatureExtractor

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_VERTEX
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import topods


COMPOSITE_AXIAL_SPAN = "compound_axial_span"
COMPOSITE_SCHEMA = "compound-axial-span/v1"
ANNULAR_RIM = "annular_rim"
ANNULAR_RIM_SCHEMA = "annular-rim/v1"
COMPOSITE_PLANAR_SPAN = "composite_planar_span"
COMPOSITE_PLANAR_SPAN_SCHEMA = "composite-planar-span/v1"


def build_annular_rim_features(graph: FeatureRelationGraph) -> List[FeatureNode]:
    """Group coaxial outer and inner cylinders into one functional rim."""
    outers = [node for node in graph.nodes if node.feature_type == "shaft_segment"]
    inners = [node for node in graph.nodes if node.feature_type == "hole"]
    result: List[FeatureNode] = []
    seen = set()
    for outer in outers:
        outer_source = dict(outer.source_info or {})
        outer_axis = outer_source.get("axis_dir")
        outer_center = outer_source.get("center")
        outer_diameter = float((outer.nominal or {}).get("diameter", 0.0) or 0.0)
        if not isinstance(outer_axis, (list, tuple)) or not isinstance(outer_center, (list, tuple)):
            continue
        axis_norm = math.sqrt(sum(float(value) ** 2 for value in outer_axis[:3]))
        if axis_norm <= 1e-9 or outer_diameter <= 0.0:
            continue
        axis = tuple(float(value) / axis_norm for value in outer_axis[:3])
        for inner in inners:
            inner_source = dict(inner.source_info or {})
            inner_axis = inner_source.get("axis_dir")
            inner_center = inner_source.get("center")
            inner_diameter = float((inner.nominal or {}).get("diameter", 0.0) or 0.0)
            if not isinstance(inner_axis, (list, tuple)) or not isinstance(inner_center, (list, tuple)) or not 0.0 < inner_diameter < outer_diameter:
                continue
            if inner_diameter / outer_diameter < 0.50:
                continue
            inner_norm = math.sqrt(sum(float(value) ** 2 for value in inner_axis[:3]))
            if inner_norm <= 1e-9:
                continue
            inner_axis_unit = tuple(float(value) / inner_norm for value in inner_axis[:3])
            if abs(sum(axis[index] * inner_axis_unit[index] for index in range(3))) < 0.995:
                continue
            delta = tuple(float(inner_center[index]) - float(outer_center[index]) for index in range(3))
            axial_delta = sum(delta[index] * axis[index] for index in range(3))
            radial_delta = math.sqrt(max(0.0, sum(value * value for value in delta) - axial_delta * axial_delta))
            if radial_delta > max(0.03, outer_diameter * 0.001):
                continue
            start = max(float(outer.axial_span[0]), float(inner.axial_span[0]))
            end = min(float(outer.axial_span[1]), float(inner.axial_span[1]))
            if end - start <= 0.05:
                continue
            key = (round(outer_diameter, 4), round(inner_diameter, 4), round(start, 4), round(end, 4))
            if key in seen:
                continue
            seen.add(key)
            center = [(float(outer_center[index]) + float(inner_center[index])) / 2.0 for index in range(3)]
            descriptor = {
                "schema": ANNULAR_RIM_SCHEMA,
                "member_feature_types": [outer.feature_type, inner.feature_type],
                "semantic_role": "ANNULAR_INTERFACE",
                "outer_to_inner_ratio": round(inner_diameter / outer_diameter, 6),
                "wall_thickness_ratio": round((outer_diameter - inner_diameter) / (2.0 * outer_diameter), 6),
            }
            digest = hashlib.sha1(f"{outer_diameter:.5f}|{inner_diameter:.5f}|{end-start:.5f}".encode("utf-8")).hexdigest()[:10]
            result.append(FeatureNode(
                id=f"annular_rim_{digest}",
                feature_type=ANNULAR_RIM,
                nominal={
                    "outer_diameter": round(outer_diameter, 6),
                    "inner_diameter": round(inner_diameter, 6),
                    "length": round(end - start, 6),
                    "wall_thickness": round((outer_diameter - inner_diameter) / 2.0, 6),
                },
                axial_span=[round(start, 6), round(end, 6)],
                center_axial=round((start + end) / 2.0, 6),
                boundary_position=outer.boundary_position,
                neighbor_types=sorted(set(outer.neighbor_types + inner.neighbor_types)),
                inferred_role="ANNULAR_INTERFACE",
                source_info={
                    "type": ANNULAR_RIM,
                    "center": center,
                    "axis_dir": list(axis),
                    "length": round(end - start, 6),
                    "outer_radius": round(outer_diameter / 2.0, 6),
                    "inner_radius": round(inner_diameter / 2.0, 6),
                    "member_feature_ids": [outer.id, inner.id],
                    "composite_descriptor": descriptor,
                },
            ))
    return result


def build_planar_span_features_from_planes(
    graph: FeatureRelationGraph,
    planes: Sequence[Dict[str, Any]],
    requested_lengths: Iterable[float],
) -> List[FeatureNode]:
    """Build only dimension-requested spans between opposite cardinal planes.

    Complex housings often dimension a relation between two planar faces that
    is not itself an atomic STEP feature.  Group coplanar faces first so a
    tessellated/flanged surface does not create hundreds of duplicate pairs,
    then retain only spans matching an observed drawing nominal.  No drawing
    coordinate is used to construct the 3D feature.
    """
    requested = sorted({
        abs(float(value))
        for value in requested_lengths
        if abs(float(value)) > 1e-9
    })
    if not requested:
        return []
    grouped: Dict[Tuple[str, float], Dict[str, Any]] = {}
    for plane in planes:
        normal = plane.get("normal")
        center = plane.get("center_of_mass") or plane.get("location")
        if not isinstance(normal, (list, tuple)) or len(normal) < 3:
            continue
        if not isinstance(center, (list, tuple)) or len(center) < 3:
            continue
        dominant = max(range(3), key=lambda index: abs(float(normal[index])))
        if abs(float(normal[dominant])) < 0.985:
            continue
        axis_name = "XYZ"[dominant]
        position = round(float(center[dominant]), 3)
        key = (axis_name, position)
        area = max(0.0, float(plane.get("area", 0.0) or 0.0))
        entry = grouped.setdefault(key, {
            "axis": axis_name,
            "position": position,
            "positive": False,
            "negative": False,
            "total_area": 0.0,
            "representative_area": -1.0,
            "representative_center": [float(value) for value in center[:3]],
            "surface_points": [],
        })
        entry["positive"] = bool(entry["positive"] or float(normal[dominant]) > 0.0)
        entry["negative"] = bool(entry["negative"] or float(normal[dominant]) < 0.0)
        entry["total_area"] += area
        entry["surface_points"].extend(
            [float(value) for value in point[:3]]
            for point in plane.get("surface_points", [])
            if isinstance(point, (list, tuple)) and len(point) >= 3
        )
        if area > entry["representative_area"]:
            entry["representative_area"] = area
            entry["representative_center"] = [float(value) for value in center[:3]]

    result: List[FeatureNode] = []
    by_axis: Dict[str, List[Dict[str, Any]]] = {"X": [], "Y": [], "Z": []}
    for entry in grouped.values():
        by_axis[entry["axis"]].append(entry)
    axis_vectors = {"X": [1.0, 0.0, 0.0], "Y": [0.0, 1.0, 0.0], "Z": [0.0, 0.0, 1.0]}
    for axis_name, entries in by_axis.items():
        entries.sort(key=lambda item: item["position"])
        for left_index, left in enumerate(entries):
            for right in entries[left_index + 1:]:
                length = float(right["position"] - left["position"])
                matched_nominal = next((
                    nominal for nominal in requested
                    if abs(length - nominal) <= max(0.02, nominal * 0.006)
                ), None)
                if matched_nominal is None:
                    continue
                opposite = (
                    (left["positive"] and right["negative"])
                    or (left["negative"] and right["positive"])
                )
                if not opposite:
                    continue
                center = [
                    (float(left["representative_center"][index]) + float(right["representative_center"][index])) / 2.0
                    for index in range(3)
                ]
                center["XYZ".index(axis_name)] = (left["position"] + right["position"]) / 2.0
                digest = hashlib.sha1(
                    f"{axis_name}|{left['position']:.3f}|{right['position']:.3f}".encode("utf-8")
                ).hexdigest()[:10]
                result.append(FeatureNode(
                    id=f"planar_span_{axis_name.lower()}_{digest}",
                    feature_type=COMPOSITE_PLANAR_SPAN,
                    nominal={"length": round(length, 6), "axis": axis_name},
                    axial_span=[round(left["position"], 6), round(right["position"], 6)],
                    center_axial=round((left["position"] + right["position"]) / 2.0, 6),
                    inferred_role="PLANAR_FACE_SPAN",
                    source_info={
                        "type": COMPOSITE_PLANAR_SPAN,
                        "center": center,
                        "axis": axis_name,
                        "axis_dir": axis_vectors[axis_name],
                        "length": round(length, 6),
                        "plane_positions": [left["position"], right["position"]],
                        "plane_centers": [
                            left["representative_center"], right["representative_center"],
                        ],
                        "plane_areas": [
                            round(float(left["total_area"]), 6),
                            round(float(right["total_area"]), 6),
                        ],
                        "surface_points": _unique_points(
                            left["surface_points"] + right["surface_points"]
                        ),
                        "composite_descriptor": {
                            "schema": COMPOSITE_PLANAR_SPAN_SCHEMA,
                            "semantic_role": "PLANAR_FACE_SPAN",
                            "axis": axis_name,
                        },
                    },
                ))
    return result


def build_planar_span_features(
    shape,
    graph: FeatureRelationGraph,
    requested_lengths: Iterable[float],
) -> List[FeatureNode]:
    """Extract plane evidence with the same OpenCASCADE feature extractor."""
    if shape is None:
        return []
    summary = FeatureExtractor(shape).summary()
    planes = list(summary.get("planes") or [])
    _attach_planar_face_vertices(shape, planes)
    return build_planar_span_features_from_planes(
        graph,
        planes,
        requested_lengths,
    )


def _unique_points(points: Iterable[Sequence[float]]) -> List[List[float]]:
    result: List[List[float]] = []
    seen = set()
    for point in points:
        key = tuple(round(float(value), 6) for value in point[:3])
        if key in seen:
            continue
        seen.add(key)
        result.append(list(key))
    return result


def _attach_planar_face_vertices(shape, planes: List[Dict[str, Any]]) -> None:
    """Attach B-Rep boundary points to planes classified by FeatureExtractor."""
    candidates = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = topods.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            direction = surface.Plane().Axis().Direction()
            normal = (
                round(float(direction.X()), 3),
                round(float(direction.Y()), 3),
                round(float(direction.Z()), 3),
            )
            vertices: List[List[float]] = []
            vertex_explorer = TopExp_Explorer(face, TopAbs_VERTEX)
            while vertex_explorer.More():
                point = BRep_Tool.Pnt(topods.Vertex(vertex_explorer.Current()))
                vertices.append([float(point.X()), float(point.Y()), float(point.Z())])
                vertex_explorer.Next()
            if vertices:
                center = tuple(
                    sum(point[index] for point in vertices) / len(vertices)
                    for index in range(3)
                )
                candidates.append((center, normal, _unique_points(vertices)))
        explorer.Next()
    for plane in planes:
        center = plane.get("center_of_mass") or plane.get("location")
        normal = plane.get("normal")
        if not center or not normal:
            continue
        matching = [
            (math.dist(tuple(float(value) for value in center[:3]), candidate_center), vertices)
            for candidate_center, candidate_normal, vertices in candidates
            if all(abs(float(normal[index]) - candidate_normal[index]) <= 0.002 for index in range(3))
        ]
        if matching:
            distance, vertices = min(matching, key=lambda item: item[0])
            if distance <= 0.25:
                plane["surface_points"] = vertices


def _projection_axis(registration: Dict[str, Any], main_axis: str) -> Tuple[float, float]:
    axis = {
        "x": (1.0, 0.0, 0.0),
        "y": (0.0, 1.0, 0.0),
        "z": (0.0, 0.0, 1.0),
    }.get(str(main_axis).lower(), (0.0, 0.0, 1.0))
    projection = dict(registration.get("projection") or {})
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


def dimension_endpoints_to_axial_span(
    graph: FeatureRelationGraph,
    endpoints: Sequence[Sequence[float]],
    registration: Dict[str, Any],
) -> Optional[List[float]]:
    """Map registered DXF endpoints back to coordinates on the STEP main axis."""
    if len(endpoints) != 2:
        return None
    axis = _projection_axis(registration, graph.main_axis)
    norm_sq = axis[0] * axis[0] + axis[1] * axis[1]
    if norm_sq <= 0.25:
        return None
    transform_data = dict(registration.get("transform") or registration)
    try:
        transform = SimilarityTransform2D(
            scale=float(transform_data["scale"]),
            rotation_degrees=float(transform_data["rotation_degrees"]),
            translation=tuple(float(value) for value in transform_data["translation"][:2]),
            mirrored=bool(transform_data.get("mirrored", False)),
        )
        source_points = [transform.inverse_point((float(point[0]), float(point[1]))) for point in endpoints]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    axial = [
        (point[0] * axis[0] + point[1] * axis[1]) / norm_sq
        for point in source_points
    ]
    return [round(min(axial), 6), round(max(axial), 6)]


def _inverse_projected_points(
    endpoints: Sequence[Sequence[float]], registration: Dict[str, Any]
) -> Optional[List[Tuple[float, float]]]:
    if len(endpoints) != 2:
        return None
    transform_data = dict(registration.get("transform") or registration)
    try:
        transform = SimilarityTransform2D(
            scale=float(transform_data["scale"]),
            rotation_degrees=float(transform_data["rotation_degrees"]),
            translation=tuple(float(value) for value in transform_data["translation"][:2]),
            mirrored=bool(transform_data.get("mirrored", False)),
        )
        return [transform.inverse_point((float(point[0]), float(point[1]))) for point in endpoints]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def match_transverse_diameter(
    graph: FeatureRelationGraph,
    endpoints: Sequence[Sequence[float]],
    nominal_value: float,
    registration: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Recover diameter semantics for DXF LINEAR entities drawn across an axis.

    Some drawings encode a vertical diameter as a linear dimension and add the
    diameter symbol through text formatting.  Geometry is authoritative here:
    a span nearly perpendicular to the projected STEP main axis, whose length
    equals a localized canonical diameter, is treated as a diameter.
    """
    source_points = _inverse_projected_points(endpoints, registration)
    if source_points is None:
        return None
    axis = _projection_axis(registration, graph.main_axis)
    axis_norm = math.hypot(*axis)
    if axis_norm <= 0.5:
        return None
    delta = (
        source_points[1][0] - source_points[0][0],
        source_points[1][1] - source_points[0][1],
    )
    measured = math.hypot(*delta)
    if measured <= 1e-9:
        return None
    along = abs(delta[0] * axis[0] + delta[1] * axis[1]) / (measured * axis_norm)
    if along > 0.12:
        return None
    nominal = abs(float(nominal_value))
    length_tolerance = max(0.02, nominal * 0.004)
    if abs(measured - nominal) > length_tolerance:
        return None
    midpoint = (
        (source_points[0][0] + source_points[1][0]) / 2.0,
        (source_points[0][1] + source_points[1][1]) / 2.0,
    )
    axial = (midpoint[0] * axis[0] + midpoint[1] * axis[1]) / (axis_norm * axis_norm)
    candidates = []
    for node in graph.nodes:
        if node.feature_type in {"overall_dimension", "datum", COMPOSITE_AXIAL_SPAN}:
            continue
        diameter = (node.nominal or {}).get("diameter")
        if not isinstance(diameter, (int, float)) or abs(float(diameter) - nominal) > length_tolerance:
            continue
        start, end = sorted(map(float, node.axial_span or [node.center_axial, node.center_axial]))
        location_error = 0.0 if start <= axial <= end else min(abs(axial - start), abs(axial - end))
        if location_error <= max(0.08, float(graph.total_length or 0.0) * 0.006):
            candidates.append((location_error, abs(float(diameter) - nominal), node))
    if not candidates:
        return None
    _, _, node = min(candidates, key=lambda item: (item[0], item[1], item[2].id))
    return {
        "node": node,
        "nominal_field": "diameter",
        "measured_transverse_span": round(measured, 6),
        "mapped_axial_position": round(axial, 6),
        "axis_alignment": round(along, 6),
        "method": "TRANSVERSE_LINEAR_TO_DIAMETER_SEMANTICS_V1",
    }


def _merged_coverage(intervals: Iterable[Sequence[float]], start: float, end: float) -> float:
    clipped = sorted(
        (max(start, min(map(float, span))), min(end, max(map(float, span))))
        for span in intervals
        if max(map(float, span)) > start and min(map(float, span)) < end
    )
    merged: List[List[float]] = []
    for left, right in clipped:
        if right <= left:
            continue
        if merged and left <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return sum(right - left for left, right in merged) / max(end - start, 1e-9)


def composite_descriptor(node: FeatureNode) -> Dict[str, Any]:
    return dict((node.source_info or {}).get("composite_descriptor") or {})


def composite_structure_similarity(query: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    """Compare semantic structure independently of model-specific feature IDs."""
    if not query or not candidate:
        return 0.0
    q_types = list(query.get("member_feature_types") or [])
    c_types = list(candidate.get("member_feature_types") or [])
    max_count = max(len(q_types), len(c_types), 1)
    ordered_matches = sum(left == right for left, right in zip(q_types, c_types)) / max_count
    q_set, c_set = set(q_types), set(c_types)
    set_score = len(q_set & c_set) / max(1, len(q_set | c_set))
    boundary_score = 1.0 if query.get("boundary_position") == candidate.get("boundary_position") else 0.25
    role_score = 1.0 if query.get("semantic_role") == candidate.get("semantic_role") else 0.4
    return max(0.0, min(1.0, 0.45 * ordered_matches + 0.25 * set_score + 0.15 * boundary_score + 0.15 * role_score))


class CompositeAxialSpanBuilder:
    """Enumerate deterministic axial spans from adjacent canonical features."""

    EXCLUDED_TYPES = {"overall_dimension", "datum", "plane", "axis"}

    @staticmethod
    def _localized_nodes(graph: FeatureRelationGraph) -> List[FeatureNode]:
        return sorted(
            (
                node for node in graph.nodes
                if node.feature_type not in CompositeAxialSpanBuilder.EXCLUDED_TYPES
                and str((node.source_info or {}).get("type") or "").lower() not in {"bbox", "axis", "plane"}
                and not str(node.inferred_role or "").upper().startswith("DATUM")
                and len(node.axial_span or []) == 2
                and float(node.axial_span[1]) > float(node.axial_span[0])
            ),
            key=lambda node: (float(node.axial_span[0]), float(node.axial_span[1]), node.id),
        )

    @staticmethod
    def _semantic_role(members: Sequence[FeatureNode], boundary: str) -> str:
        roles = {str(node.inferred_role or "").upper() for node in members}
        types = {node.feature_type for node in members}
        if boundary in {"LEFT_END", "RIGHT_END"} and (
            "LEAD_IN_PILOT" in roles or "pilot_chamfer" in types
        ):
            return "PILOT_END_REGION"
        if boundary in {"LEFT_END", "RIGHT_END"}:
            return "END_FEATURE_REGION"
        return "AXIAL_FEATURE_STACK"

    def enumerate(
        self,
        graph: FeatureRelationGraph,
        min_members: int = 2,
        max_members: Optional[int] = None,
    ) -> List[FeatureNode]:
        nodes = self._localized_nodes(graph)
        if len(nodes) < min_members:
            return []
        max_members = min(len(nodes), max_members if max_members is not None else len(nodes))
        overall = next((node for node in graph.nodes if node.feature_type == "overall_dimension"), None)
        if overall and len(overall.axial_span or []) == 2:
            overall_min, overall_max = sorted(map(float, overall.axial_span))
        else:
            overall_min = min(float(node.axial_span[0]) for node in nodes)
            overall_max = max(float(node.axial_span[1]) for node in nodes)
        total_length = max(overall_max - overall_min, float(graph.total_length or 0.0), 1e-9)
        join_tolerance = max(0.05, total_length * 0.012)
        end_tolerance = max(0.05, total_length * 0.006)
        composites: List[FeatureNode] = []
        seen = set()
        for first in range(len(nodes)):
            for count in range(min_members, max_members + 1):
                members = nodes[first:first + count]
                if len(members) != count:
                    continue
                disconnected = any(
                    float(right.axial_span[0]) - float(left.axial_span[1]) > join_tolerance
                    for left, right in zip(members, members[1:])
                )
                if disconnected:
                    continue
                start = min(float(node.axial_span[0]) for node in members)
                end = max(float(node.axial_span[1]) for node in members)
                if _merged_coverage((node.axial_span for node in members), start, end) < 0.82:
                    continue
                boundary = (
                    "LEFT_END" if abs(start - overall_min) <= end_tolerance else
                    "RIGHT_END" if abs(end - overall_max) <= end_tolerance else
                    "INTERIOR"
                )
                member_types = [node.feature_type for node in members]
                semantic_role = self._semantic_role(members, boundary)
                signature_text = "|".join([boundary, semantic_role, *member_types])
                digest = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:10]
                node_id = f"compound_axial_{boundary.lower()}_{digest}"
                dedupe_key = (round(start, 5), round(end, 5), tuple(member_types), boundary)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                descriptor = {
                    "schema": COMPOSITE_SCHEMA,
                    "semantic_role": semantic_role,
                    "boundary_position": boundary,
                    "member_feature_types": member_types,
                    "member_roles": [str(node.inferred_role or "") for node in members],
                    "member_count": len(members),
                    "length_to_part_ratio": round((end - start) / total_length, 6),
                }
                composites.append(FeatureNode(
                    id=node_id,
                    feature_type=COMPOSITE_AXIAL_SPAN,
                    nominal={"length": round(end - start, 6)},
                    axial_span=[round(start, 6), round(end, 6)],
                    center_axial=round((start + end) / 2.0, 6),
                    adjacent_left_type=members[0].adjacent_left_type,
                    adjacent_right_type=members[-1].adjacent_right_type,
                    neighbor_types=member_types,
                    boundary_position=boundary,
                    inferred_role=semantic_role,
                    source_info={
                        "type": COMPOSITE_AXIAL_SPAN,
                        "construction_method": "CONTIGUOUS_CANONICAL_FEATURE_CHAIN_V1",
                        "member_feature_ids": [node.id for node in members],
                        "composite_descriptor": descriptor,
                    },
                ))
        return composites

    def build_from_axial_span(
        self,
        graph: FeatureRelationGraph,
        axial_span: Sequence[float],
        nominal_length: float,
    ) -> Optional[FeatureNode]:
        """Build a composite by snapping measured endpoints to 3D boundaries.

        This avoids an arbitrary member-count limit and also works when one
        dimension definition point has no nearby DXF entity.  Both endpoints
        must independently agree with canonical STEP boundaries.
        """
        if len(axial_span) != 2:
            return None
        measured_start, measured_end = sorted(map(float, axial_span))
        nominal = abs(float(nominal_length))
        if abs((measured_end - measured_start) - nominal) > max(0.02, nominal * 0.006):
            return None
        nodes = self._localized_nodes(graph)
        if not nodes:
            return None
        overall = next((node for node in graph.nodes if node.feature_type == "overall_dimension"), None)
        if overall and len(overall.axial_span or []) == 2:
            overall_min, overall_max = sorted(map(float, overall.axial_span))
        else:
            overall_min = min(float(node.axial_span[0]) for node in nodes)
            overall_max = max(float(node.axial_span[1]) for node in nodes)
        total_length = max(overall_max - overall_min, float(graph.total_length or 0.0), 1e-9)
        snap_tolerance = max(0.025, total_length * 0.0025)
        boundaries = sorted({
            round(float(value), 9)
            for node in nodes
            for value in node.axial_span
        } | {round(overall_min, 9), round(overall_max, 9)})
        start = min(boundaries, key=lambda value: abs(value - measured_start))
        end = min(boundaries, key=lambda value: abs(value - measured_end))
        if abs(start - measured_start) > snap_tolerance or abs(end - measured_end) > snap_tolerance:
            return None
        if abs((end - start) - nominal) > max(0.03, nominal * 0.008):
            return None
        members = [
            node for node in nodes
            if min(float(node.axial_span[1]), end) - max(float(node.axial_span[0]), start) > 1e-6
        ]
        if len(members) < 2 or _merged_coverage((node.axial_span for node in members), start, end) < 0.90:
            return None
        boundary = (
            "LEFT_END" if abs(start - overall_min) <= snap_tolerance else
            "RIGHT_END" if abs(end - overall_max) <= snap_tolerance else
            "INTERIOR"
        )
        semantic_role = self._semantic_role(members, boundary)
        member_types = [node.feature_type for node in members]
        signature_text = "|".join([boundary, semantic_role, *member_types])
        digest = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:10]
        descriptor = {
            "schema": COMPOSITE_SCHEMA,
            "semantic_role": semantic_role,
            "boundary_position": boundary,
            "member_feature_types": member_types,
            "member_roles": [str(node.inferred_role or "") for node in members],
            "member_count": len(members),
            "length_to_part_ratio": round((end - start) / total_length, 6),
        }
        return FeatureNode(
            id=f"compound_axial_{boundary.lower()}_{digest}",
            feature_type=COMPOSITE_AXIAL_SPAN,
            nominal={"length": round(end - start, 6)},
            axial_span=[round(start, 6), round(end, 6)],
            center_axial=round((start + end) / 2.0, 6),
            adjacent_left_type=members[0].adjacent_left_type,
            adjacent_right_type=members[-1].adjacent_right_type,
            neighbor_types=member_types,
            boundary_position=boundary,
            inferred_role=semantic_role,
            source_info={
                "type": COMPOSITE_AXIAL_SPAN,
                "construction_method": "PROJECTED_ENDPOINT_BOUNDARY_SNAP_V1",
                "member_feature_ids": [node.id for node in members],
                "composite_descriptor": descriptor,
            },
        )

    def match_projected_dimension(
        self,
        graph: FeatureRelationGraph,
        endpoints: Sequence[Sequence[float]],
        nominal_length: float,
        registration: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        measured_span = dimension_endpoints_to_axial_span(graph, endpoints, registration)
        if measured_span is None:
            return None
        snapped = self.build_from_axial_span(graph, measured_span, nominal_length)
        if snapped is not None:
            return {
                "node": snapped,
                "mapped_axial_span": measured_span,
                "endpoint_error": round(max(
                    abs(float(snapped.axial_span[0]) - measured_span[0]),
                    abs(float(snapped.axial_span[1]) - measured_span[1]),
                ), 6),
                "method": "PROJECTED_ENDPOINT_BOUNDARY_SNAP_V1",
            }
        nominal = abs(float(nominal_length))
        candidates = []
        for node in self.enumerate(graph):
            length = float((node.nominal or {}).get("length", 0.0) or 0.0)
            nominal_error = abs(length - nominal)
            endpoint_error = max(
                abs(float(node.axial_span[0]) - measured_span[0]),
                abs(float(node.axial_span[1]) - measured_span[1]),
            )
            tolerance = max(0.02, nominal * 0.006)
            if nominal_error <= tolerance and endpoint_error <= max(0.05, nominal * 0.02):
                candidates.append((endpoint_error + nominal_error, node))
        if not candidates:
            return None
        _, node = min(candidates, key=lambda item: (item[0], item[1].id))
        return {
            "node": node,
            "mapped_axial_span": measured_span,
            "endpoint_error": round(max(
                abs(float(node.axial_span[0]) - measured_span[0]),
                abs(float(node.axial_span[1]) - measured_span[1]),
            ), 6),
            "method": "PROJECTED_ENDPOINTS_TO_COMPOSITE_AXIAL_CHAIN_V1",
        }
