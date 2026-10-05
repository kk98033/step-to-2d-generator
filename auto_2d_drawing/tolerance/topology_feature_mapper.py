"""Map DXF dimension definition points to STEP B-Rep topology.

The mapper provides two explainable paths:

* registered ray casting for views with a trustworthy STEP-to-DXF transform;
* a local circular-profile proof for diameter dimensions in rearranged
  assembly drawings where a global sheet registration is not meaningful.

Face identity and boundary-edge candidates come from the real TopoDS shape.
An edge is reported as ambiguous when front/back boundary edges collapse to
the same orthographic curve; the mapper never invents a unique edge ID.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from OCC.Core.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.GeomAbs import (
    GeomAbs_Circle,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Line,
    GeomAbs_Plane,
    GeomAbs_Torus,
)
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Vec

from auto_2d_drawing.config import VIEW_CONFIG
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D


Point2D = Tuple[float, float]
Point3D = Tuple[float, float, float]


def _round_point(values: Sequence[float], digits: int = 6) -> List[float]:
    return [round(float(value), digits) for value in values[:3]]


def _normalise(values: Sequence[float]) -> Optional[Point3D]:
    if len(values) < 3:
        return None
    length = math.sqrt(sum(float(value) ** 2 for value in values[:3]))
    if length <= 1e-12:
        return None
    return tuple(float(value) / length for value in values[:3])


def _axis_alignment(left: Sequence[float], right: Sequence[float]) -> float:
    a, b = _normalise(left), _normalise(right)
    if a is None or b is None:
        return 0.0
    return abs(sum(a[index] * b[index] for index in range(3)))


@dataclass
class TopologyEdgeRecord:
    topology_id: str
    curve_type: str
    samples: List[Point3D]
    radius: Optional[float] = None
    center: Optional[Point3D] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topology_id": self.topology_id,
            "curve_type": self.curve_type,
            "radius": None if self.radius is None else round(self.radius, 6),
            "center": None if self.center is None else _round_point(self.center),
            "sample_count": len(self.samples),
        }


@dataclass
class TopologyFaceRecord:
    topology_id: str
    surface_type: str
    face: Any = field(repr=False)
    center: Optional[Point3D] = None
    axis: Optional[Point3D] = None
    radius: Optional[float] = None
    length: Optional[float] = None
    is_reversed: bool = False
    boundary_edges: List[TopologyEdgeRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topology_id": self.topology_id,
            "surface_type": self.surface_type,
            "center": None if self.center is None else _round_point(self.center),
            "axis": None if self.axis is None else _round_point(self.axis),
            "radius": None if self.radius is None else round(self.radius, 6),
            "length": None if self.length is None else round(self.length, 6),
            "is_reversed": self.is_reversed,
            "boundary_edges": [edge.to_dict() for edge in self.boundary_edges],
        }


class TopologyFeatureMapper:
    """Resolve a FeatureNode and dimension endpoints against B-Rep topology."""

    METHOD = "DXF_ENDPOINT_TO_BREP_TOPOLOGY_V1"

    def __init__(self, shape):
        self.shape = shape
        self.face_records = self._index_faces(shape)
        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        self.shape_diagonal = max(math.dist((xmin, ymin, zmin), (xmax, ymax, zmax)), 1.0)

    def map_dimension(
        self,
        dimension: Any,
        node: Any,
        matched_field: str,
        association: Dict[str, Any],
        view_name: Optional[str] = None,
        view: Optional[Dict[str, Any]] = None,
        transform_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        category = str(getattr(dimension, "dimension_category", "") or "").upper()
        local = None
        if category == "DIAMETER" and matched_field in {"diameter", "groove_diameter"}:
            local = self._map_local_circular_profile(dimension, node, association)
            if local.get("passed"):
                return local
        if view_name and view and isinstance(transform_payload, dict):
            return self._map_registered_rays(
                dimension,
                node,
                association,
                view_name,
                view,
                transform_payload,
            )
        if local is not None:
            return local
        return {
            "method": self.METHOD,
            "status": "TOPOLOGY_MAPPING_UNAVAILABLE",
            "passed": False,
            "score": 0.0,
            "feature_id": str(getattr(node, "id", "")),
            "matched_field": matched_field,
        }

    def _map_local_circular_profile(
        self,
        dimension: Any,
        node: Any,
        association: Dict[str, Any],
    ) -> Dict[str, Any]:
        nominal = abs(float(getattr(dimension, "nominal_value", 0.0) or 0.0))
        source = dict(getattr(node, "source_info", {}) or {})
        if str(source.get("type") or "").lower() != "cylinder":
            return self._failure(node, "LOCAL_PROFILE_REQUIRES_CYLINDER")
        profile_tolerance = max(0.01, min(0.08, nominal * 0.002))
        profiles = [
            profile for profile in association.get("circular_attachment_profiles", [])
            if int(profile.get("definition_point_count", 0) or 0) >= 2
            and abs(float(profile.get("diameter", 0.0) or 0.0) - nominal) <= profile_tolerance
        ]
        if len(profiles) != 1:
            return self._failure(
                node,
                "NO_UNIQUE_DXF_CIRCULAR_PROFILE",
                candidate_profile_count=len(profiles),
            )
        face_match = self._match_node_face(node)
        if not face_match.get("passed"):
            return {
                **self._failure(node, "NO_UNIQUE_BREP_FACE"),
                "face_match": face_match,
            }
        face: TopologyFaceRecord = face_match["record"]
        face_records: List[TopologyFaceRecord] = face_match.get("records") or [face]
        for record in face_records:
            self._ensure_face_edges(record)
        profile = profiles[0]
        center = tuple(float(value) for value in source.get("center", ()))
        axis = _normalise(source.get("axis_dir") or ())
        radius = abs(float(source.get("radius", nominal / 2.0) or 0.0))
        if len(center) < 3 or axis is None or radius <= 0.0:
            return self._failure(node, "MISSING_CYLINDER_GEOMETRY")
        basis_a, basis_b = self._plane_basis(axis)
        profile_center = tuple(float(value) for value in profile["center"][:2])
        profile_radius = max(float(profile.get("radius", 0.0) or 0.0), 1e-12)
        reference_points = self._reference_points(association)
        endpoint_mappings = []
        circular_edges = [
            edge for record in face_records for edge in record.boundary_edges
            if edge.curve_type == "circle"
            and edge.radius is not None
            and abs(edge.radius - radius) <= max(0.01, radius * 0.002)
        ]
        edge_ids = [edge.topology_id for edge in circular_edges]
        for name, point in reference_points:
            radial = ((point[0] - profile_center[0]) / profile_radius, (point[1] - profile_center[1]) / profile_radius)
            radial_length = max(math.hypot(*radial), 1e-12)
            radial = (radial[0] / radial_length, radial[1] / radial_length)
            mapped = tuple(
                center[index]
                + radius * radial[0] * basis_a[index]
                + radius * radial[1] * basis_b[index]
                for index in range(3)
            )
            endpoint_mappings.append({
                "definition_point": name,
                "dxf_point": [round(point[0], 6), round(point[1], 6)],
                "mapped_point_3d": _round_point(mapped),
                "face_id": face.topology_id if len(face_records) == 1 else None,
                "face_candidates": [record.topology_id for record in face_records],
                "logical_surface_id": face_match.get("logical_surface_id"),
                "edge_id": edge_ids[0] if len(edge_ids) == 1 else None,
                "edge_candidates": edge_ids,
                "mapping_kind": "CYLINDRICAL_FACE_RADIAL_POINT",
            })
        endpoint_count = len(endpoint_mappings)
        edge_status = (
            "UNIQUE_BOUNDARY_EDGE" if len(edge_ids) == 1
            else "AMBIGUOUS_COINCIDENT_BOUNDARY_EDGES" if len(edge_ids) > 1
            else "FACE_ONLY_NO_BOUNDARY_EDGE"
        )
        score = 0.98 if len(edge_ids) == 1 else 0.94
        passed = endpoint_count >= 2 and bool(face_match.get("unique"))
        return {
            "method": self.METHOD,
            "mode": "LOCAL_CIRCULAR_PROFILE",
            "status": "TOPOLOGY_FACE_MAPPED" if passed else "INSUFFICIENT_ENDPOINTS",
            "passed": passed,
            "score": round(score if passed else 0.0, 4),
            "feature_id": str(getattr(node, "id", "")),
            "feature_type": str(getattr(node, "feature_type", "")),
            "face": face.to_dict(),
            "face_candidates": [record.topology_id for record in face_records],
            "logical_surface_id": face_match.get("logical_surface_id"),
            "topological_face_status": (
                "UNIQUE_FACE" if len(face_records) == 1 else "LOGICAL_SURFACE_MULTIPLE_FACE_PATCHES"
            ),
            "face_match_score": face_match.get("score"),
            "face_match_margin": face_match.get("margin"),
            "dxf_profile": profile,
            "endpoint_mappings": endpoint_mappings,
            "mapped_endpoint_count": endpoint_count,
            "edge_mapping_status": edge_status,
            "edge_identity_verified": len(edge_ids) == 1,
            "face_identity_verified": passed and len(face_records) == 1,
            "logical_surface_identity_verified": passed,
            "checks": {
                "two_exact_dxf_profile_points": endpoint_count >= 2,
                "diameter_matches_dxf_profile": True,
                "unique_brep_cylindrical_face": bool(face_match.get("unique")),
                "node_matches_brep_face_geometry": bool(face_match.get("passed")),
            },
        }

    def _map_registered_rays(
        self,
        dimension: Any,
        node: Any,
        association: Dict[str, Any],
        view_name: str,
        view: Dict[str, Any],
        transform_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        face_match = self._match_node_face(node)
        if not face_match.get("passed"):
            return {**self._failure(node, "NO_UNIQUE_BREP_FACE"), "face_match": face_match}
        face: TopologyFaceRecord = face_match["record"]
        self._ensure_face_edges(face)
        transform = SimilarityTransform2D(
            scale=float(transform_payload.get("scale", 1.0) or 1.0),
            rotation_degrees=float(transform_payload.get("rotation_degrees", 0.0) or 0.0),
            translation=tuple(transform_payload.get("translation") or (0.0, 0.0)),
            mirrored=bool(transform_payload.get("mirrored", False)),
        )
        projection = dict(view.get("projection") or {})
        config = projection if projection.get("direction") and projection.get("up") else VIEW_CONFIG.get(view_name)
        if not config:
            return self._failure(node, "MISSING_PROJECTION_FRAME")
        direction = _normalise(config["direction"])
        up = _normalise(config["up"])
        if direction is None or up is None:
            return self._failure(node, "INVALID_PROJECTION_FRAME")
        side = (
            direction[1] * up[2] - direction[2] * up[1],
            direction[2] * up[0] - direction[0] * up[2],
            direction[0] * up[1] - direction[1] * up[0],
        )
        endpoint_mappings = []
        for name, dxf_point in self._reference_points(association):
            step_point = transform.inverse_point(dxf_point)
            origin = tuple(step_point[0] * up[index] + step_point[1] * side[index] for index in range(3))
            hit = self._intersect_face(face, origin, direction)
            edge_resolution = self._nearest_projected_edge(face, step_point, up, side)
            endpoint_mappings.append({
                "definition_point": name,
                "dxf_point": [round(value, 6) for value in dxf_point],
                "step_projection_point": [round(value, 6) for value in step_point],
                "mapped_point_3d": None if hit is None else _round_point(hit),
                "face_id": face.topology_id if hit is not None else None,
                **edge_resolution,
                "mapping_kind": "REGISTERED_ORTHOGRAPHIC_RAY",
            })
        required = 2 if str(getattr(dimension, "dimension_category", "")).upper() in {"LINEAR", "DIAMETER", "ANGULAR"} else 1
        hit_count = sum(item["mapped_point_3d"] is not None for item in endpoint_mappings)
        passed = hit_count >= required and bool(face_match.get("unique"))
        unique_edges = sum(bool(item.get("edge_id")) for item in endpoint_mappings)
        score = 0.72 + 0.18 * min(1.0, hit_count / max(required, 1)) + 0.10 * min(1.0, unique_edges / max(required, 1))
        return {
            "method": self.METHOD,
            "mode": "REGISTERED_ORTHOGRAPHIC_RAY",
            "status": "TOPOLOGY_FACE_MAPPED" if passed else "RAY_FACE_MISS",
            "passed": passed,
            "score": round(score if passed else 0.0, 4),
            "feature_id": str(getattr(node, "id", "")),
            "feature_type": str(getattr(node, "feature_type", "")),
            "face": face.to_dict(),
            "endpoint_mappings": endpoint_mappings,
            "mapped_endpoint_count": hit_count,
            "edge_identity_verified": unique_edges >= required,
            "face_identity_verified": passed,
        }

    def _match_node_face(self, node: Any) -> Dict[str, Any]:
        source = dict(getattr(node, "source_info", {}) or {})
        kind = str(source.get("type") or "").lower()
        if kind != "cylinder":
            return {"passed": False, "unique": False, "status": "UNSUPPORTED_FEATURE_SURFACE"}
        center = source.get("center") or ()
        axis = source.get("axis_dir") or ()
        radius = abs(float(source.get("radius", 0.0) or 0.0))
        length = abs(float(source.get("length", 0.0) or 0.0))
        is_hole = bool(source.get("is_hole", False))
        ranked = []
        for record in self.face_records:
            if record.surface_type != "cylinder" or record.center is None or record.axis is None:
                continue
            radius_error = abs(float(record.radius or 0.0) - radius)
            length_error = abs(float(record.length or 0.0) - length)
            center_error = math.dist(record.center, center[:3]) if len(center) >= 3 else float("inf")
            alignment = _axis_alignment(record.axis, axis)
            orientation_matches = record.is_reversed == is_hole
            radius_limit = max(0.005, radius * 0.001)
            length_limit = max(0.01, length * 0.002)
            center_limit = max(0.01, length * 0.002)
            if (
                radius_error <= radius_limit
                and length_error <= length_limit
                and center_error <= center_limit
                and alignment >= 0.999
                and orientation_matches
            ):
                score = (
                    0.35 * math.exp(-radius_error / radius_limit)
                    + 0.25 * math.exp(-length_error / length_limit)
                    + 0.25 * math.exp(-center_error / center_limit)
                    + 0.15 * alignment
                )
                ranked.append((score, record))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            return {"passed": False, "unique": False, "status": "NO_MATCHING_FACE"}
        logical_groups: Dict[str, List[Tuple[float, TopologyFaceRecord]]] = {}
        for score, record in ranked:
            signature = json.dumps({
                "surface": record.surface_type,
                "center": _round_point(record.center or (0.0, 0.0, 0.0)),
                "axis": _round_point(record.axis or (0.0, 0.0, 0.0)),
                "radius": round(float(record.radius or 0.0), 6),
                "length": round(float(record.length or 0.0), 6),
                "reversed": record.is_reversed,
            }, sort_keys=True)
            logical_groups.setdefault(signature, []).append((score, record))
        grouped = sorted(
            ((max(item[0] for item in members), signature, members) for signature, members in logical_groups.items()),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best_signature, best_members = grouped[0]
        best = best_members[0][1]
        margin = best_score - grouped[1][0] if len(grouped) > 1 else best_score
        unique = len(grouped) == 1 or margin >= 0.05
        logical_surface_id = "surface_" + hashlib.sha1(best_signature.encode("utf-8")).hexdigest()[:12]
        return {
            "passed": best_score >= 0.90 and unique,
            "unique": unique,
            "status": "UNIQUE_FACE" if unique else "AMBIGUOUS_FACE",
            "score": round(best_score, 4),
            "margin": round(margin, 4),
            "candidate_count": len(ranked),
            "logical_candidate_count": len(grouped),
            "logical_surface_id": logical_surface_id,
            "record": best,
            "records": [item[1] for item in best_members],
        }

    def _index_faces(self, shape) -> List[TopologyFaceRecord]:
        records: List[TopologyFaceRecord] = []
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            face = explorer.Current()
            try:
                surface = BRepAdaptor_Surface(face)
                surface_type = self._surface_name(surface.GetType())
                center = axis = None
                radius = length = None
                if surface.GetType() == GeomAbs_Cylinder:
                    cylinder = surface.Cylinder()
                    location = cylinder.Location()
                    direction = cylinder.Axis().Direction()
                    v0, v1 = surface.FirstVParameter(), surface.LastVParameter()
                    midpoint = location.Translated(gp_Vec(direction).Multiplied((v0 + v1) / 2.0))
                    center = (midpoint.X(), midpoint.Y(), midpoint.Z())
                    axis = (direction.X(), direction.Y(), direction.Z())
                    radius = float(cylinder.Radius())
                    length = abs(float(v1 - v0))
                signature = {
                    "surface": surface_type,
                    "center": None if center is None else _round_point(center),
                    "axis": None if axis is None else _round_point(axis),
                    "radius": None if radius is None else round(radius, 6),
                    "length": None if length is None else round(length, 6),
                    "reversed": face.Orientation() == TopAbs_REVERSED,
                }
                digest = hashlib.sha1(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()[:12]
                ordinal = sum(item.topology_id.startswith(f"face_{digest}") for item in records) + 1
                record = TopologyFaceRecord(
                    topology_id=f"face_{digest}_{ordinal:02d}",
                    surface_type=surface_type,
                    face=face,
                    center=center,
                    axis=axis,
                    radius=radius,
                    length=length,
                    is_reversed=face.Orientation() == TopAbs_REVERSED,
                )
                records.append(record)
            except Exception:
                pass
            explorer.Next()
        return records

    def _ensure_face_edges(self, record: TopologyFaceRecord) -> None:
        """Populate boundary topology only after a face becomes relevant."""

        if not record.boundary_edges:
            record.boundary_edges = self._index_face_edges(record.face, record.topology_id)

    def _index_face_edges(self, face, face_id: str) -> List[TopologyEdgeRecord]:
        result = []
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        position = 0
        while explorer.More():
            position += 1
            try:
                curve = BRepAdaptor_Curve(explorer.Current())
                start, end = float(curve.FirstParameter()), float(curve.LastParameter())
                if not math.isfinite(start) or not math.isfinite(end):
                    explorer.Next()
                    continue
                samples = []
                for index in range(25):
                    point = curve.Value(start + (end - start) * index / 24.0)
                    samples.append((point.X(), point.Y(), point.Z()))
                curve_type = "circle" if curve.GetType() == GeomAbs_Circle else "line" if curve.GetType() == GeomAbs_Line else "curve"
                radius = center = None
                if curve.GetType() == GeomAbs_Circle:
                    circle = curve.Circle()
                    radius = float(circle.Radius())
                    location = circle.Location()
                    center = (location.X(), location.Y(), location.Z())
                signature = {
                    "type": curve_type,
                    "start": _round_point(samples[0]),
                    "end": _round_point(samples[-1]),
                    "center": None if center is None else _round_point(center),
                    "radius": None if radius is None else round(radius, 6),
                }
                digest = hashlib.sha1(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()[:10]
                result.append(TopologyEdgeRecord(
                    topology_id=f"{face_id}:edge_{digest}_{position:02d}",
                    curve_type=curve_type,
                    samples=samples,
                    radius=radius,
                    center=center,
                ))
            except Exception:
                pass
            explorer.Next()
        return result

    def _intersect_face(
        self,
        face: TopologyFaceRecord,
        origin: Point3D,
        direction: Point3D,
    ) -> Optional[Point3D]:
        try:
            intersector = IntCurvesFace_ShapeIntersector()
            intersector.Load(face.face, max(self.shape_diagonal * 1e-7, 1e-7))
            intersector.Perform(
                gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)),
                -self.shape_diagonal * 2.0,
                self.shape_diagonal * 2.0,
            )
            if not intersector.IsDone() or intersector.NbPnt() <= 0:
                return None
            index = min(range(1, intersector.NbPnt() + 1), key=lambda item: abs(intersector.WParameter(item)))
            point = intersector.Pnt(index)
            return point.X(), point.Y(), point.Z()
        except Exception:
            return None

    def _nearest_projected_edge(
        self,
        face: TopologyFaceRecord,
        point: Point2D,
        up: Point3D,
        side: Point3D,
    ) -> Dict[str, Any]:
        ranked = []
        for edge in face.boundary_edges:
            distance = min(
                math.dist(point, (
                    sum(sample[index] * up[index] for index in range(3)),
                    sum(sample[index] * side[index] for index in range(3)),
                ))
                for sample in edge.samples
            )
            ranked.append((distance, edge.topology_id))
        ranked.sort()
        limit = max(0.01, self.shape_diagonal * 0.0025)
        if not ranked or ranked[0][0] > limit:
            return {"edge_id": None, "edge_candidates": [], "edge_distance": None}
        candidates = [item for item in ranked if item[0] <= ranked[0][0] + limit * 0.25]
        return {
            "edge_id": candidates[0][1] if len(candidates) == 1 else None,
            "edge_candidates": [item[1] for item in candidates],
            "edge_distance": round(ranked[0][0], 6),
        }

    @staticmethod
    def _reference_points(association: Dict[str, Any]) -> List[Tuple[str, Point2D]]:
        result = []
        for item in association.get("definition_point_links", []):
            point = item.get("point")
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                result.append((
                    str(item.get("definition_point") or "point"),
                    (float(point[0]), float(point[1])),
                ))
        recovered = dict(association.get("recovered_dimension_geometry") or {})
        if len(result) < 2 and recovered.get("kind") == "DIAMETER_ENDPOINT_PAIR":
            result = [
                (f"recovered_{index + 1}", (float(point[0]), float(point[1])))
                for index, point in enumerate(recovered.get("endpoints") or [])
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
        return result

    @staticmethod
    def _plane_basis(axis: Point3D) -> Tuple[Point3D, Point3D]:
        candidates = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        seed = min(candidates, key=lambda value: abs(sum(value[index] * axis[index] for index in range(3))))
        projection = sum(seed[index] * axis[index] for index in range(3))
        first = tuple(seed[index] - projection * axis[index] for index in range(3))
        first = _normalise(first)
        assert first is not None
        second = (
            axis[1] * first[2] - axis[2] * first[1],
            axis[2] * first[0] - axis[0] * first[2],
            axis[0] * first[1] - axis[1] * first[0],
        )
        normalised_second = _normalise(second)
        assert normalised_second is not None
        return first, normalised_second

    @staticmethod
    def _surface_name(surface_type: Any) -> str:
        return {
            GeomAbs_Cylinder: "cylinder",
            GeomAbs_Plane: "plane",
            GeomAbs_Cone: "cone",
            GeomAbs_Torus: "torus",
        }.get(surface_type, "other")

    def _failure(self, node: Any, status: str, **extra: Any) -> Dict[str, Any]:
        return {
            "method": self.METHOD,
            "status": status,
            "passed": False,
            "score": 0.0,
            "feature_id": str(getattr(node, "id", "")),
            "feature_type": str(getattr(node, "feature_type", "")),
            **extra,
        }
