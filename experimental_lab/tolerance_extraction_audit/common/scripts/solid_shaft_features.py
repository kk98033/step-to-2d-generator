"""Generic solid stepped-shaft discovery from STEP BRep topology.

The extractor groups same-solid, coaxial outer cylindrical faces into an
ordered shaft chain.  It intentionally excludes cylindrical intervals that
overlap an inner cylindrical material boundary; those belong to the annular
composite workflow instead.  No model name or fixed coordinate is used.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_VERTEX
from OCC.Core.TopExp import TopExp_Explorer

from auto_2d_drawing.drawing_projection import project_point, view_basis
try:
    from .annular_composite_features import extract_cylindrical_faces, _shape_diagonal
except ImportError:  # Direct script execution adds this directory to sys.path.
    from annular_composite_features import extract_cylindrical_faces, _shape_diagonal


STANDARD_VIEWS = ("front", "right", "left", "back", "top")


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def _sub(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return tuple(float(left[index]) - float(right[index]) for index in range(3))  # type: ignore[return-value]


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _normalise(vector: Sequence[float]) -> tuple[float, float, float]:
    length = _norm(vector)
    if length <= 1e-12:
        return (0.0, 0.0, 1.0)
    result = tuple(float(value) / length for value in vector[:3])
    for value in result:
        if abs(value) > 1e-9:
            return tuple(-item for item in result) if value < 0 else result  # type: ignore[return-value]
    return result  # type: ignore[return-value]


def _axis_line_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    axis = _normalise(left["axis"])
    delta = _sub(right["location"], left["location"])
    along = _dot(delta, axis)
    return _norm(tuple(delta[index] - along * axis[index] for index in range(3)))


def _overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(0.0, min(float(left["end"]), float(right["end"])) - max(float(left["start"]), float(right["start"])))


def _union_length(intervals: Sequence[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    merged = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _point_on_axis(location: Sequence[float], axis: Sequence[float], coordinate: float) -> tuple[float, float, float]:
    direction = _normalise(axis)
    origin_coordinate = _dot(location, direction)
    return tuple(float(location[index]) + (coordinate - origin_coordinate) * direction[index] for index in range(3))  # type: ignore[return-value]


def _solid_axis_extents(shape: Any, solid_index: int, axis: Sequence[float]) -> tuple[float, float] | None:
    solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    current_index = 0
    target = None
    while solid_explorer.More():
        current_index += 1
        if current_index == solid_index:
            target = solid_explorer.Current()
            break
        solid_explorer.Next()
    if target is None:
        target = shape if solid_index == 1 else None
    if target is None:
        return None
    direction = _normalise(axis)
    values = []
    vertex_explorer = TopExp_Explorer(target, TopAbs_VERTEX)
    while vertex_explorer.More():
        point = BRep_Tool.Pnt(vertex_explorer.Current())
        values.append(_dot((point.X(), point.Y(), point.Z()), direction))
        vertex_explorer.Next()
    return (min(values), max(values)) if values else None


def _profile_view(axis: Sequence[float]) -> str:
    direction = _normalise(axis)
    return min(
        STANDARD_VIEWS,
        key=lambda name: (abs(_dot(direction, view_basis(name)["normal"])), STANDARD_VIEWS.index(name)),
    )


def build_solid_shaft_chains(
    cylinders: Sequence[dict[str, Any]],
    model_diagonal: float,
    solid_extents: dict[Any, tuple[float, float]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build ordered coaxial outer-cylinder chains and reject hollow spans."""
    position_tolerance = max(0.03, float(model_diagonal) * 0.001)
    join_tolerance = max(0.05, float(model_diagonal) * 0.003)
    inners = [item for item in cylinders if item["material_relation"] == "INNER_MATERIAL_BOUNDARY"]
    outers = [item for item in cylinders if item["material_relation"] == "OUTER_MATERIAL_BOUNDARY"]
    solid_outers = []
    rejected = []
    for outer in outers:
        hollow_matches = [
            inner for inner in inners
            if inner["solid_index"] == outer["solid_index"]
            and abs(_dot(_normalise(inner["axis"]), _normalise(outer["axis"]))) >= 0.999
            and _axis_line_distance(inner, outer) <= position_tolerance
            and _overlap(inner, outer) > position_tolerance
        ]
        if hollow_matches:
            rejected.append({
                "solid_index": outer["solid_index"],
                "outer_face_index": outer["face_index"],
                "reason": "OVERLAPS_INNER_CYLINDER_USE_ANNULAR_WORKFLOW",
            })
        else:
            solid_outers.append(outer)

    clusters: list[list[dict[str, Any]]] = []
    for outer in sorted(solid_outers, key=lambda item: (item["solid_index"], item["start"], item["radius"])):
        matched = None
        for cluster in clusters:
            reference = cluster[0]
            if reference["solid_index"] != outer["solid_index"]:
                continue
            if abs(_dot(_normalise(reference["axis"]), _normalise(outer["axis"]))) < 0.999:
                continue
            if _axis_line_distance(reference, outer) > position_tolerance:
                continue
            matched = cluster
            break
        if matched is None:
            matched = []
            clusters.append(matched)
        matched.append(outer)

    chains = []
    for cluster_index, cluster in enumerate(clusters, start=1):
        cluster.sort(key=lambda item: (item["start"], item["end"], item["radius"]))
        axis = _normalise(cluster[0]["axis"])
        cylinder_start = min(float(item["start"]) for item in cluster)
        cylinder_end = max(float(item["end"]) for item in cluster)
        span = cylinder_end - cylinder_start
        max_diameter = max(float(item["diameter"]) for item in cluster)
        # A lone short boss is not a stepped shaft. A long single journal is.
        if len(cluster) < 2 and span < max_diameter * 1.5:
            rejected.append({
                "solid_index": cluster[0]["solid_index"],
                "outer_face_index": cluster[0]["face_index"],
                "reason": "ISOLATED_SHORT_OUTER_CYLINDER",
            })
            continue
        extent_key = (int(cluster[0]["solid_index"]), tuple(round(value, 6) for value in axis))
        extents = (solid_extents or {}).get(extent_key) or (solid_extents or {}).get(int(cluster[0]["solid_index"]))
        overall_start, overall_end = extents or (cylinder_start, cylinder_end)
        coverage = _union_length([(float(item["start"]), float(item["end"])) for item in cluster]) / max(overall_end - overall_start, 1e-9)
        chain_id = f"solid_shaft_s{cluster[0]['solid_index']:03d}_c{cluster_index:02d}"
        segments = []
        for index, item in enumerate(cluster, start=1):
            length = float(item["length"])
            confidence = max(0.0, min(1.0, 0.72 + 0.20 * min(length / max(model_diagonal * 0.02, 1e-9), 1.0) + 0.08 * min(coverage, 1.0)))
            segments.append({
                "id": f"{chain_id}.segment_{index:02d}",
                "face_index": item["face_index"],
                "center": _point_on_axis(item["location"], axis, (float(item["start"]) + float(item["end"])) / 2.0),
                "axis": axis,
                "start": float(item["start"]),
                "end": float(item["end"]),
                "length": length,
                "radius": float(item["radius"]),
                "diameter": float(item["diameter"]),
                "confidence": confidence,
                "status": "AUTO_VERIFIED" if confidence >= 0.90 else "REVIEW_CANDIDATE",
            })
        shoulders = []
        for left, right in zip(segments, segments[1:]):
            gap = right["start"] - left["end"]
            if abs(gap) <= join_tolerance and abs(left["radius"] - right["radius"]) > position_tolerance:
                shoulders.append({
                    "id": f"{chain_id}.shoulder_{len(shoulders) + 1:02d}",
                    "axis_coordinate": (left["end"] + right["start"]) / 2.0,
                    "left_segment_id": left["id"],
                    "right_segment_id": right["id"],
                    "radial_step": abs(left["radius"] - right["radius"]),
                    "join_gap": gap,
                    "status": "AUTO_VERIFIED",
                })
        line_location = cluster[0]["location"]
        chain_confidence = max(0.0, min(1.0, 0.70 + 0.25 * min(coverage, 1.0) + 0.05 * min(len(segments) / 2.0, 1.0)))
        chains.append({
            "id": chain_id,
            "solid_index": cluster[0]["solid_index"],
            "axis": axis,
            "axis_location": line_location,
            "profile_view": _profile_view(axis),
            "overall_start": overall_start,
            "overall_end": overall_end,
            "overall_length": overall_end - overall_start,
            "cylindrical_start": cylinder_start,
            "cylindrical_end": cylinder_end,
            "terminal_start_allowance": max(0.0, cylinder_start - overall_start),
            "terminal_end_allowance": max(0.0, overall_end - cylinder_end),
            "coverage_ratio": coverage,
            "confidence": chain_confidence,
            "status": "AUTO_VERIFIED" if chain_confidence >= 0.90 else "REVIEW_CANDIDATE",
            "segments": segments,
            "shoulders": shoulders,
        })
    return chains, rejected


def build_solid_shaft_dimension_candidates(chain: dict[str, Any]) -> list[dict[str, Any]]:
    """Create profile-view OD, segment-length, and overall-length intents."""
    view_name = chain["profile_view"]
    basis = view_basis(view_name)
    axis = _normalise(chain["axis"])
    axis_2d = (_dot(axis, basis["right"]), _dot(axis, basis["up"]))
    axis_length = max(math.hypot(*axis_2d), 1e-9)
    axis_2d = (axis_2d[0] / axis_length, axis_2d[1] / axis_length)
    radial_2d = (-axis_2d[1], axis_2d[0])
    candidates = []
    for segment in chain["segments"]:
        center = project_point(segment["center"], view_name)
        half_length = segment["length"] / 2.0
        radius = segment["radius"]
        base = {
            "shaft_chain_id": chain["id"],
            "segment_id": segment["id"],
            "view": view_name,
            "confidence": segment["confidence"],
            "verification_status": segment["status"],
        }
        candidates.extend([
            {
                **base,
                "id": segment["id"] + ".outer_diameter",
                "semantic": "solid_shaft.segment_outer_diameter",
                "dim_type": "DIAMETER",
                "nominal_value": segment["diameter"],
                "anchors": [[center[0] - radial_2d[0] * radius, center[1] - radial_2d[1] * radius], [center[0] + radial_2d[0] * radius, center[1] + radial_2d[1] * radius]],
            },
            {
                **base,
                "id": segment["id"] + ".axial_length",
                "semantic": "solid_shaft.segment_axial_length",
                "dim_type": "LINEAR",
                "nominal_value": segment["length"],
                "anchors": [[center[0] - axis_2d[0] * half_length, center[1] - axis_2d[1] * half_length], [center[0] + axis_2d[0] * half_length, center[1] + axis_2d[1] * half_length]],
            },
        ])
    overall_center_coordinate = (chain["overall_start"] + chain["overall_end"]) / 2.0
    overall_center_3d = _point_on_axis(chain["axis_location"], axis, overall_center_coordinate)
    overall_center = project_point(overall_center_3d, view_name)
    half_overall = chain["overall_length"] / 2.0
    candidates.append({
        "shaft_chain_id": chain["id"],
        "segment_id": None,
        "id": chain["id"] + ".overall_length",
        "semantic": "solid_shaft.overall_length",
        "dim_type": "LINEAR",
        "nominal_value": chain["overall_length"],
        "view": view_name,
        "confidence": chain["confidence"],
        "verification_status": chain["status"],
        "anchors": [[overall_center[0] - axis_2d[0] * half_overall, overall_center[1] - axis_2d[1] * half_overall], [overall_center[0] + axis_2d[0] * half_overall, overall_center[1] + axis_2d[1] * half_overall]],
    })
    return candidates


def extract_solid_shaft_features(shape: Any) -> dict[str, Any]:
    diagonal = _shape_diagonal(shape)
    cylinders = extract_cylindrical_faces(shape)
    preliminary, _ = build_solid_shaft_chains(cylinders, diagonal)
    extents = {
        (int(chain["solid_index"]), tuple(round(value, 6) for value in chain["axis"])): _solid_axis_extents(
            shape, int(chain["solid_index"]), chain["axis"]
        )
        for chain in preliminary
    }
    chains, rejected = build_solid_shaft_chains(
        cylinders,
        diagonal,
        {key: value for key, value in extents.items() if value is not None},
    )
    dimensions = [candidate for chain in chains for candidate in build_solid_shaft_dimension_candidates(chain)]
    return {
        "method": "SAME_SOLID_COAXIAL_OUTER_CYLINDER_CHAIN_V1",
        "model_diagonal": diagonal,
        "cylindrical_face_count": len(cylinders),
        "shaft_chain_count": len(chains),
        "segment_count": sum(len(chain["segments"]) for chain in chains),
        "shoulder_count": sum(len(chain["shoulders"]) for chain in chains),
        "dimension_candidate_count": len(dimensions),
        "auto_verified_dimension_count": sum(item["verification_status"] == "AUTO_VERIFIED" for item in dimensions),
        "review_candidate_dimension_count": sum(item["verification_status"] == "REVIEW_CANDIDATE" for item in dimensions),
        "chains": chains,
        "dimensions": dimensions,
        "rejected_cylinders": rejected,
    }
