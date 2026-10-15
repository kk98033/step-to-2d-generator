"""Generic annular feature discovery from STEP BRep cylindrical faces.

An annular section is defined by an inner and outer cylindrical face that are
owned by the same solid, coaxial, and overlap along their common axis.  The
implementation uses no model names and no fixed coordinates.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer

from auto_2d_drawing.drawing_projection import project_point, view_basis


STANDARD_VIEWS = ("front", "top", "right", "left", "back")


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
    return tuple(float(value) / length for value in vector[:3])  # type: ignore[return-value]


def _canonical_axis(axis: Sequence[float]) -> tuple[float, float, float]:
    result = _normalise(axis)
    for value in result:
        if abs(value) > 1e-9:
            if value < 0:
                return tuple(-item for item in result)  # type: ignore[return-value]
            break
    return result


def _axis_line_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    axis = _canonical_axis(left["axis"])
    delta = _sub(right["location"], left["location"])
    along = _dot(delta, axis)
    perpendicular = tuple(delta[index] - along * axis[index] for index in range(3))
    return _norm(perpendicular)


def _axis_coordinate(point: Sequence[float], axis: Sequence[float]) -> float:
    return _dot(point, _canonical_axis(axis))


def _shape_diagonal(shape: Any) -> float:
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)


def _iter_solids(shape: Any) -> list[Any]:
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solids = []
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    return solids or [shape]


def extract_cylindrical_faces(shape: Any) -> list[dict[str, Any]]:
    """Return de-duplicated cylinders while preserving owning-solid identity."""
    faces: list[dict[str, Any]] = []
    for solid_index, solid in enumerate(_iter_solids(shape), start=1):
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        seen = set()
        face_index = 0
        while explorer.More():
            face_index += 1
            face = explorer.Current()
            surface = BRepAdaptor_Surface(face)
            if surface.GetType() == GeomAbs_Cylinder:
                cylinder = surface.Cylinder()
                location = cylinder.Location()
                direction = cylinder.Axis().Direction()
                axis = _canonical_axis((direction.X(), direction.Y(), direction.Z()))
                origin = (location.X(), location.Y(), location.Z())
                v0, v1 = float(surface.FirstVParameter()), float(surface.LastVParameter())
                u0, u1 = float(surface.FirstUParameter()), float(surface.LastUParameter())
                sample_u = (u0 + u1) / 2.0 if math.isfinite(u0) and math.isfinite(u1) else 0.0
                # V follows the cylinder's original oriented axis.  Evaluating
                # the OCC surface first avoids applying V to a sign-canonical
                # axis and mirroring negative-axis faces in world space.
                surface_p0 = surface.Value(sample_u, v0)
                surface_p1 = surface.Value(sample_u, v1)
                start, end = sorted((
                    _axis_coordinate((surface_p0.X(), surface_p0.Y(), surface_p0.Z()), axis),
                    _axis_coordinate((surface_p1.X(), surface_p1.Y(), surface_p1.Z()), axis),
                ))
                axial_mid = (start + end) / 2.0
                origin_axis = _axis_coordinate(origin, axis)
                center = tuple(origin[index] + (axial_mid - origin_axis) * axis[index] for index in range(3))
                radius = float(cylinder.Radius())
                is_inner = face.Orientation() == TopAbs_REVERSED
                key = (
                    round(radius, 5),
                    *(round(value, 5) for value in center),
                    *(round(value, 5) for value in axis),
                    round(start, 5), round(end, 5), is_inner,
                )
                if key not in seen:
                    seen.add(key)
                    faces.append({
                        "solid_index": solid_index,
                        "face_index": face_index,
                        "radius": radius,
                        "diameter": radius * 2.0,
                        "location": origin,
                        "center": center,
                        "axis": axis,
                        "start": start,
                        "end": end,
                        "length": max(end - start, 0.0),
                        "material_relation": "INNER_MATERIAL_BOUNDARY" if is_inner else "OUTER_MATERIAL_BOUNDARY",
                    })
            explorer.Next()
    return faces


def pair_annular_faces(cylinders: Sequence[dict[str, Any]], model_diagonal: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair each inner cylinder with its nearest valid outer cylinder."""
    position_tolerance = max(0.03, float(model_diagonal) * 0.001)
    radius_tolerance = max(0.01, float(model_diagonal) * 0.0002)
    pairs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    inner_faces = [face for face in cylinders if face["material_relation"] == "INNER_MATERIAL_BOUNDARY"]
    outer_faces = [face for face in cylinders if face["material_relation"] == "OUTER_MATERIAL_BOUNDARY"]
    for inner in inner_faces:
        candidates = []
        reasons = []
        for outer in outer_faces:
            if outer["solid_index"] != inner["solid_index"]:
                continue
            alignment = abs(_dot(inner["axis"], outer["axis"]))
            if alignment < 0.999:
                continue
            line_offset = _axis_line_distance(inner, outer)
            if line_offset > position_tolerance:
                continue
            if outer["radius"] <= inner["radius"] + radius_tolerance:
                continue
            overlap_start = max(inner["start"], outer["start"])
            overlap_end = min(inner["end"], outer["end"])
            overlap = max(0.0, overlap_end - overlap_start)
            min_length = max(min(inner["length"], outer["length"]), 1e-9)
            overlap_ratio = overlap / min_length
            if overlap <= position_tolerance or overlap_ratio < 0.35:
                continue
            radial_thickness = outer["radius"] - inner["radius"]
            candidates.append((radial_thickness, -overlap_ratio, line_offset, outer, overlap_start, overlap_end, overlap_ratio, alignment))
        if not candidates:
            rejected.append({
                "solid_index": inner["solid_index"],
                "inner_face_index": inner["face_index"],
                "inner_diameter": inner["diameter"],
                "reason": "NO_SAME_SOLID_COAXIAL_OVERLAPPING_OUTER_FACE",
            })
            continue
        _, _, line_offset, outer, overlap_start, overlap_end, overlap_ratio, alignment = min(candidates, key=lambda item: item[:3])
        axis = _canonical_axis(inner["axis"])
        axial_mid = (overlap_start + overlap_end) / 2.0
        origin_axis = _axis_coordinate(inner["location"], axis)
        center = tuple(inner["location"][index] + (axial_mid - origin_axis) * axis[index] for index in range(3))
        radial_thickness = outer["radius"] - inner["radius"]
        confidence = max(0.0, min(1.0, 0.45 * alignment + 0.35 * overlap_ratio + 0.20 * (1.0 - line_offset / position_tolerance)))
        pairs.append({
            "id": f"annular_s{inner['solid_index']:03d}_i{inner['face_index']:04d}_o{outer['face_index']:04d}",
            "solid_index": inner["solid_index"],
            "inner_face_index": inner["face_index"],
            "outer_face_index": outer["face_index"],
            "center": center,
            "axis": axis,
            "inner_radius": inner["radius"],
            "outer_radius": outer["radius"],
            "inner_diameter": inner["diameter"],
            "outer_diameter": outer["diameter"],
            "radial_wall_thickness": radial_thickness,
            "axial_thickness": overlap_end - overlap_start,
            "axial_start": overlap_start,
            "axial_end": overlap_end,
            "axis_alignment": alignment,
            "axis_line_offset": line_offset,
            "axial_overlap_ratio": overlap_ratio,
            "confidence": confidence,
            "status": "AUTO_VERIFIED" if confidence >= 0.90 else "REVIEW_CANDIDATE",
        })
    return pairs, rejected


def _best_views(axis: Sequence[float]) -> tuple[str, str]:
    ranked = []
    for index, view_name in enumerate(STANDARD_VIEWS):
        normal = view_basis(view_name)["normal"]
        alignment = abs(_dot(axis, normal))
        ranked.append((alignment, -index, view_name))
    end_view = max(ranked)[2]
    # Prefer a canonical view that sees the axis in profile.
    profile_view = min(ranked, key=lambda item: (item[0], -item[1]))[2]
    return end_view, profile_view


def build_annular_dimension_candidates(feature: dict[str, Any]) -> list[dict[str, Any]]:
    """Create inner/outer/radial/axial dimension candidates for one pair."""
    center = feature["center"]
    axis = feature["axis"]
    end_view, profile_view = _best_views(axis)
    end_center = project_point(center, end_view)
    profile_center = project_point(center, profile_view)
    profile_basis = view_basis(profile_view)
    axis_2d = (_dot(axis, profile_basis["right"]), _dot(axis, profile_basis["up"]))
    axis_2d_len = max(math.hypot(*axis_2d), 1e-9)
    axis_2d = (axis_2d[0] / axis_2d_len, axis_2d[1] / axis_2d_len)
    half_axial = feature["axial_thickness"] / 2.0
    axial_start = [profile_center[0] - axis_2d[0] * half_axial, profile_center[1] - axis_2d[1] * half_axial]
    axial_end = [profile_center[0] + axis_2d[0] * half_axial, profile_center[1] + axis_2d[1] * half_axial]
    base = {
        "composite_feature_id": feature["id"],
        "confidence": feature["confidence"],
        "verification_status": feature["status"],
    }
    inner_r, outer_r = feature["inner_radius"], feature["outer_radius"]
    return [
        {**base, "id": feature["id"] + ".inner_diameter", "semantic": "annular_section.inner_diameter", "dim_type": "DIAMETER", "nominal_value": feature["inner_diameter"], "view": end_view, "anchors": [[end_center[0] - inner_r, end_center[1]], [end_center[0] + inner_r, end_center[1]]]},
        {**base, "id": feature["id"] + ".outer_diameter", "semantic": "annular_section.outer_diameter", "dim_type": "DIAMETER", "nominal_value": feature["outer_diameter"], "view": end_view, "anchors": [[end_center[0] - outer_r, end_center[1]], [end_center[0] + outer_r, end_center[1]]]},
        {**base, "id": feature["id"] + ".radial_wall_thickness", "semantic": "annular_section.radial_wall_thickness", "dim_type": "LINEAR", "nominal_value": feature["radial_wall_thickness"], "view": end_view, "anchors": [[end_center[0] + inner_r, end_center[1]], [end_center[0] + outer_r, end_center[1]]]},
        {**base, "id": feature["id"] + ".axial_thickness", "semantic": "annular_section.axial_thickness", "dim_type": "LINEAR", "nominal_value": feature["axial_thickness"], "view": profile_view, "anchors": [axial_start, axial_end]},
    ]


def extract_annular_composites(shape: Any) -> dict[str, Any]:
    diagonal = _shape_diagonal(shape)
    cylinders = extract_cylindrical_faces(shape)
    features, rejected = pair_annular_faces(cylinders, diagonal)
    dimensions = [candidate for feature in features for candidate in build_annular_dimension_candidates(feature)]
    return {
        "method": "SAME_SOLID_COAXIAL_OVERLAPPING_CYLINDERS_V1",
        "model_diagonal": diagonal,
        "cylindrical_face_count": len(cylinders),
        "annular_feature_count": len(features),
        "dimension_candidate_count": len(dimensions),
        "auto_verified_count": sum(feature["status"] == "AUTO_VERIFIED" for feature in features),
        "review_candidate_count": sum(feature["status"] == "REVIEW_CANDIDATE" for feature in features),
        "features": features,
        "dimensions": dimensions,
        "rejected_inner_faces": rejected,
    }
