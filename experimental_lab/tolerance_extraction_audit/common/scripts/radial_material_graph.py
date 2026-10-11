"""Geometry-derived radial material graph for cylindrical STEP features.

The classifier does not trust face orientation or component names.  It probes
the solid on both radial sides of each cylindrical face, then groups coaxial
surfaces with overlapping axial ranges into solid annuli or annular gaps.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_OUT
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.gp import gp_Vec


Point3 = Tuple[float, float, float]


def _unit(values: Sequence[float]) -> Point3:
    length = math.sqrt(sum(float(value) ** 2 for value in values[:3]))
    if length <= 1e-12:
        raise ValueError("zero-length axis")
    result = tuple(float(value) / length for value in values[:3])
    for value in result:
        if abs(value) > 1e-9:
            if value < 0.0:
                result = tuple(-item for item in result)
            break
    return result


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def _state(shape, point, tolerance: float) -> str:
    classifier = BRepClass3d_SolidClassifier(shape, point, tolerance)
    state = classifier.State()
    if state == TopAbs_IN:
        return "MATERIAL"
    if state == TopAbs_OUT:
        return "VOID"
    return "BOUNDARY_OR_UNKNOWN"


@dataclass
class RadialSurface:
    surface_id: str
    radius: float
    diameter: float
    axis: Point3
    axis_offset: Point3
    axial_span: Tuple[float, float]
    radial_inner_state: str
    radial_outer_state: str
    material_role: str
    confidence: float
    face_orientation_reversed: bool
    face_count: int = 1
    sample_votes: List[Tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "radius": round(self.radius, 6),
            "diameter": round(self.diameter, 6),
            "axis": [round(value, 6) for value in self.axis],
            "axis_offset": [round(value, 6) for value in self.axis_offset],
            "axial_span": [round(value, 6) for value in self.axial_span],
            "radial_inner_state": self.radial_inner_state,
            "radial_outer_state": self.radial_outer_state,
            "material_role": self.material_role,
            "confidence": round(self.confidence, 6),
            "face_orientation_reversed": self.face_orientation_reversed,
            "face_count": self.face_count,
            "sample_votes": [list(item) for item in self.sample_votes],
        }


class RadialMaterialGraphBuilder:
    METHOD = "BREP_RADIAL_MATERIAL_SIDE_GRAPH_V1"

    def __init__(self, relative_probe: float = 0.002):
        self.relative_probe = float(relative_probe)

    def build(self, shape) -> Dict[str, Any]:
        raw = self._extract_surfaces(shape)
        surfaces = self._deduplicate(raw)
        relations = self._build_relations(surfaces)
        return {
            "method": self.METHOD,
            "surface_count_raw": len(raw),
            "surface_count_logical": len(surfaces),
            "surfaces": [item.to_dict() for item in surfaces],
            "radial_relations": relations,
        }

    def classify_diameter(self, graph: Dict[str, Any], diameter: float) -> Dict[str, Any]:
        tolerance = max(0.01, abs(float(diameter)) * 0.001)
        relations = []
        for relation in graph.get("radial_relations") or []:
            fields = {
                key: value for key, value in relation.items()
                if key.endswith("diameter")
            }
            for field_name, value in fields.items():
                if abs(float(value) - float(diameter)) <= tolerance:
                    relations.append({
                        "semantic_feature": relation["feature_type"],
                        "semantic_field": field_name,
                        "semantic_path": f"{relation['feature_type']}.{field_name}",
                        "relation_id": relation["relation_id"],
                        "difference_mm": round(abs(float(value) - float(diameter)), 6),
                    })
        if len(relations) == 1:
            return {"status": "UNIQUE_RADIAL_SEMANTIC", **relations[0]}
        surfaces = [
            item for item in graph.get("surfaces") or []
            if abs(float(item["diameter"]) - float(diameter)) <= tolerance
        ]
        roles = sorted({item["material_role"] for item in surfaces})
        fallback = {
            "MATERIAL_INSIDE_VOID_OUTSIDE": "outer_cylindrical_surface.diameter",
            "VOID_INSIDE_MATERIAL_OUTSIDE": "inner_cylindrical_surface.diameter",
        }
        if len(roles) == 1 and roles[0] in fallback:
            return {
                "status": "UNIQUE_ATOMIC_RADIAL_SEMANTIC",
                "semantic_path": fallback[roles[0]],
                "surface_ids": [item["surface_id"] for item in surfaces],
            }
        return {
            "status": "AMBIGUOUS_RADIAL_SEMANTIC",
            "relation_candidates": relations,
            "surface_roles": roles,
            "surface_ids": [item["surface_id"] for item in surfaces],
        }

    def _extract_surfaces(self, shape) -> List[RadialSurface]:
        result: List[RadialSurface] = []
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        face_index = 0
        while explorer.More():
            face_index += 1
            face = explorer.Current()
            surface = BRepAdaptor_Surface(face)
            if surface.GetType() != GeomAbs_Cylinder:
                explorer.Next()
                continue
            cylinder = surface.Cylinder()
            radius = float(cylinder.Radius())
            axis_direction = cylinder.Axis().Direction()
            axis = _unit((axis_direction.X(), axis_direction.Y(), axis_direction.Z()))
            location = cylinder.Location()
            location_values = (float(location.X()), float(location.Y()), float(location.Z()))
            location_axial = _dot(location_values, axis)
            axis_offset = tuple(
                location_values[index] - location_axial * axis[index]
                for index in range(3)
            )
            v_values = (float(surface.FirstVParameter()), float(surface.LastVParameter()))
            # BRep adaptor V follows the cylinder's native axis, which can be
            # opposite to the canonicalised axis.
            native_axis = (axis_direction.X(), axis_direction.Y(), axis_direction.Z())
            sign = 1.0 if _dot(native_axis, axis) >= 0.0 else -1.0
            axial = sorted(location_axial + sign * value for value in v_values)
            epsilon = max(0.002, radius * self.relative_probe)
            votes: List[Tuple[str, str]] = []
            u0, u1 = float(surface.FirstUParameter()), float(surface.LastUParameter())
            v_mid = sum(v_values) / 2.0
            for fraction in (0.18, 0.34, 0.50, 0.66, 0.82):
                try:
                    point = surface.Value(u0 + (u1 - u0) * fraction, v_mid)
                    delta = gp_Vec(location, point)
                    native_t = delta.Dot(gp_Vec(axis_direction))
                    axis_point = location.Translated(gp_Vec(axis_direction).Multiplied(native_t))
                    radial = gp_Vec(axis_point, point)
                    if radial.Magnitude() <= 1e-12:
                        continue
                    radial.Normalize()
                    inner = point.Translated(radial.Multiplied(-epsilon))
                    outer = point.Translated(radial.Multiplied(epsilon))
                    votes.append((_state(shape, inner, epsilon * 0.05), _state(shape, outer, epsilon * 0.05)))
                except Exception:
                    continue
            counts: Dict[Tuple[str, str], int] = {}
            for vote in votes:
                counts[vote] = counts.get(vote, 0) + 1
            majority = max(counts, key=counts.get) if counts else ("BOUNDARY_OR_UNKNOWN", "BOUNDARY_OR_UNKNOWN")
            confidence = counts.get(majority, 0) / max(len(votes), 1)
            role = {
                ("MATERIAL", "VOID"): "MATERIAL_INSIDE_VOID_OUTSIDE",
                ("VOID", "MATERIAL"): "VOID_INSIDE_MATERIAL_OUTSIDE",
                ("MATERIAL", "MATERIAL"): "MATERIAL_BOTH_SIDES",
                ("VOID", "VOID"): "VOID_BOTH_SIDES",
            }.get(majority, "UNRESOLVED_MATERIAL_SIDE")
            digest = hashlib.sha1(
                f"{axis_offset}|{axis}|{radius:.8f}|{axial}".encode("utf-8")
            ).hexdigest()[:12]
            result.append(RadialSurface(
                surface_id=f"radial_surface_{digest}_{face_index}",
                radius=radius,
                diameter=2.0 * radius,
                axis=axis,
                axis_offset=axis_offset,
                axial_span=(axial[0], axial[1]),
                radial_inner_state=majority[0],
                radial_outer_state=majority[1],
                material_role=role,
                confidence=confidence,
                face_orientation_reversed=int(face.Orientation()) == 1,
                sample_votes=votes,
            ))
            explorer.Next()
        return result

    @staticmethod
    def _deduplicate(raw: Iterable[RadialSurface]) -> List[RadialSurface]:
        grouped: Dict[Tuple[Any, ...], RadialSurface] = {}
        for item in raw:
            key = (
                tuple(round(value, 3) for value in item.axis_offset),
                tuple(round(value, 4) for value in item.axis),
                round(item.radius, 4),
                tuple(round(value, 3) for value in item.axial_span),
                item.material_role,
            )
            if key not in grouped:
                grouped[key] = item
            else:
                grouped[key].face_count += 1
                grouped[key].confidence = min(grouped[key].confidence, item.confidence)
                grouped[key].sample_votes.extend(item.sample_votes)
        return sorted(grouped.values(), key=lambda item: (item.axis_offset, item.axial_span, item.radius))

    @staticmethod
    def _same_axis(left: RadialSurface, right: RadialSurface) -> bool:
        alignment = abs(_dot(left.axis, right.axis))
        offset_distance = math.dist(left.axis_offset, right.axis_offset)
        scale = max(left.radius, right.radius, 1.0)
        return alignment >= 0.999 and offset_distance <= max(0.01, scale * 0.001)

    def _build_relations(self, surfaces: List[RadialSurface]) -> List[Dict[str, Any]]:
        result = []
        for left_index, left in enumerate(surfaces):
            for right in surfaces[left_index + 1:]:
                inner, outer = (left, right) if left.radius < right.radius else (right, left)
                if not self._same_axis(inner, outer):
                    continue
                overlap_start = max(inner.axial_span[0], outer.axial_span[0])
                overlap_end = min(inner.axial_span[1], outer.axial_span[1])
                overlap = overlap_end - overlap_start
                if overlap <= max(0.02, 0.05 * min(
                    inner.axial_span[1] - inner.axial_span[0],
                    outer.axial_span[1] - outer.axial_span[0],
                )):
                    continue
                # A radial relation must connect neighbouring material boundaries.
                # Pairing every nested cylinder creates plausible-looking but false
                # hub/gap features that jump across one or more real boundaries.
                # Test adjacency only over this pair's shared axial interval so a
                # cylinder that exists elsewhere along the same axis does not block it.
                has_intermediate_boundary = False
                for middle in surfaces:
                    if middle is inner or middle is outer:
                        continue
                    radial_tolerance = max(0.005, outer.radius * 1e-5)
                    if not (
                        inner.radius + radial_tolerance
                        < middle.radius
                        < outer.radius - radial_tolerance
                    ):
                        continue
                    if not self._same_axis(inner, middle):
                        continue
                    middle_overlap = min(overlap_end, middle.axial_span[1]) - max(
                        overlap_start, middle.axial_span[0]
                    )
                    if middle_overlap > max(0.02, 0.05 * overlap):
                        has_intermediate_boundary = True
                        break
                if has_intermediate_boundary:
                    continue
                feature_type = None
                fields = {}
                if (
                    inner.material_role == "VOID_INSIDE_MATERIAL_OUTSIDE"
                    and outer.material_role == "MATERIAL_INSIDE_VOID_OUTSIDE"
                ):
                    feature_type = "annular_hub"
                    fields = {
                        "inner_diameter": inner.diameter,
                        "outer_diameter": outer.diameter,
                        "wall_thickness": outer.radius - inner.radius,
                    }
                elif (
                    inner.material_role == "MATERIAL_INSIDE_VOID_OUTSIDE"
                    and outer.material_role == "VOID_INSIDE_MATERIAL_OUTSIDE"
                ):
                    feature_type = "annular_interface_gap"
                    fields = {
                        "inner_member_outer_diameter": inner.diameter,
                        "outer_member_inner_diameter": outer.diameter,
                        "radial_clearance": outer.radius - inner.radius,
                    }
                if feature_type is None:
                    continue
                digest = hashlib.sha1(
                    f"{feature_type}|{inner.surface_id}|{outer.surface_id}".encode("utf-8")
                ).hexdigest()[:12]
                result.append({
                    "relation_id": f"{feature_type}_{digest}",
                    "feature_type": feature_type,
                    **{key: round(value, 6) for key, value in fields.items()},
                    "axial_overlap": [round(overlap_start, 6), round(overlap_end, 6)],
                    "inner_surface_id": inner.surface_id,
                    "outer_surface_id": outer.surface_id,
                    "minimum_material_confidence": round(min(inner.confidence, outer.confidence), 6),
                })
        return result
