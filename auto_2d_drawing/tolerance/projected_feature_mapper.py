"""Map registered DXF dimension anchors to projected 3D feature footprints.

The mapping is analytic and explainable: every footprint is generated from a
``FeatureNode``'s geometry provenance and therefore retains the feature ID and
type.  It complements HLR outline registration without pretending that a
nearby number alone identifies a feature.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from auto_2d_drawing.config import VIEW_CONFIG
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D


Point2D = Tuple[float, float]
Point3D = Tuple[float, float, float]


@dataclass
class ProjectedFeatureFootprint:
    feature_id: str
    feature_type: str
    status: str
    projection_view: str
    points: List[Point2D]
    center: Optional[Point2D]
    bbox: Optional[Tuple[float, float, float, float]]
    geometry_kind: str
    nominal: Dict[str, float]

    def to_dict(self, include_points: bool = False) -> Dict[str, Any]:
        payload = asdict(self)
        if not include_points:
            payload.pop("points", None)
            payload["point_count"] = len(self.points)
        if self.center is not None:
            payload["center"] = [round(value, 5) for value in self.center]
        if self.bbox is not None:
            payload["bbox"] = [round(value, 5) for value in self.bbox]
        return payload


class FeatureFootprintProjector:
    """Create a feature-tagged 2D footprint in a registered drawing view."""

    METHOD = "ANALYTIC_FEATURE_FOOTPRINT_V1"

    def project(
        self,
        node: Any,
        view_name: str,
        view: Dict[str, Any],
        transform_payload: Optional[Dict[str, Any]] = None,
    ) -> ProjectedFeatureFootprint:
        source = dict(getattr(node, "source_info", {}) or {})
        nominal = dict(getattr(node, "nominal", {}) or {})
        points_3d, kind = self._sample_feature(source, nominal)
        if not points_3d:
            return ProjectedFeatureFootprint(
                feature_id=str(getattr(node, "id", "")),
                feature_type=str(getattr(node, "feature_type", "")),
                status="MISSING_SPATIAL_PROVENANCE",
                projection_view=view_name,
                points=[],
                center=None,
                bbox=None,
                geometry_kind=kind,
                nominal=nominal,
            )

        projection = dict(view.get("projection") or {})
        config = projection if projection.get("direction") and projection.get("up") else VIEW_CONFIG.get(view_name)
        if not config:
            return ProjectedFeatureFootprint(
                feature_id=str(getattr(node, "id", "")),
                feature_type=str(getattr(node, "feature_type", "")),
                status="MISSING_PROJECTION_FRAME",
                projection_view=view_name,
                points=[],
                center=None,
                bbox=None,
                geometry_kind=kind,
                nominal=nominal,
            )
        points_2d = [self._project_point(point, config) for point in points_3d]
        center_3d = source.get("center")
        center_2d = self._project_point(center_3d, config) if self._valid_point3(center_3d) else None
        transform = self._transform(transform_payload)
        if transform is not None:
            points_2d = transform.apply(points_2d)
            if center_2d is not None:
                center_2d = transform.apply_point(center_2d)
        xs = [point[0] for point in points_2d]
        ys = [point[1] for point in points_2d]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        return ProjectedFeatureFootprint(
            feature_id=str(getattr(node, "id", "")),
            feature_type=str(getattr(node, "feature_type", "")),
            status="PROJECTED",
            projection_view=view_name,
            points=points_2d,
            center=center_2d,
            bbox=bbox,
            geometry_kind=kind,
            nominal=nominal,
        )

    def evaluate_anchor(
        self,
        anchor: Point2D,
        footprint: ProjectedFeatureFootprint,
        view_bbox: Sequence[float],
    ) -> Dict[str, Any]:
        if footprint.status != "PROJECTED" or not footprint.bbox or not footprint.points:
            return {
                "passed": False,
                "score": 0.0,
                "status": footprint.status,
                "footprint": footprint.to_dict(),
            }
        if len(view_bbox) != 4:
            return {
                "passed": False,
                "score": 0.0,
                "status": "MISSING_VIEW_BBOX",
                "footprint": footprint.to_dict(),
            }
        view_diagonal = max(
            math.hypot(float(view_bbox[2]) - float(view_bbox[0]), float(view_bbox[3]) - float(view_bbox[1])),
            1e-9,
        )
        bbox_distance = self._distance_to_bbox(anchor, footprint.bbox)
        point_distance = min(math.dist(anchor, point) for point in footprint.points)
        center_distance = (
            math.dist(anchor, footprint.center) if footprint.center is not None else point_distance
        )
        footprint_diagonal = max(
            math.hypot(
                footprint.bbox[2] - footprint.bbox[0],
                footprint.bbox[3] - footprint.bbox[1],
            ),
            view_diagonal * 0.01,
        )
        # Use feature-scale uncertainty.  A tolerance based mainly on the
        # entire drawing diagonal makes a tiny hole inherit several
        # millimetres of slack on a large assembly and can select a nearby
        # repeated hole with the same nominal diameter.
        bbox_limit = max(
            view_diagonal * 0.0025,
            min(footprint_diagonal * 0.10, view_diagonal * 0.008),
        )
        inside = bbox_distance <= bbox_limit
        # Long shafts legitimately place a diameter callout far from the node
        # center while still lying inside the projected cylindrical span.
        center_limit = max(footprint_diagonal * 0.80, view_diagonal * 0.015)
        passed = inside and center_distance <= center_limit
        containment_score = math.exp(-bbox_distance / max(bbox_limit, 1e-9))
        center_score = math.exp(-center_distance / max(center_limit, 1e-9))
        score = 0.72 * containment_score + 0.28 * center_score
        return {
            "passed": passed,
            "score": round(score, 4),
            "status": "ANCHOR_IN_FEATURE_FOOTPRINT" if passed else "ANCHOR_OUTSIDE_FEATURE_FOOTPRINT",
            "anchor": [round(float(value), 5) for value in anchor],
            "bbox_distance": round(bbox_distance, 6),
            "nearest_sample_distance": round(point_distance, 6),
            "center_distance": round(center_distance, 6),
            "bbox_distance_limit": round(bbox_limit, 6),
            "center_distance_limit": round(center_limit, 6),
            "view_normalized_bbox_distance": round(bbox_distance / view_diagonal, 6),
            "footprint": footprint.to_dict(),
        }

    def _sample_feature(
        self,
        source: Dict[str, Any],
        nominal: Dict[str, float],
    ) -> Tuple[List[Point3D], str]:
        center = source.get("center")
        if not self._valid_point3(center):
            return [], str(source.get("type") or "unknown")
        center_array = np.asarray(center[:3], dtype=float)
        kind = str(source.get("type") or "unknown").lower()
        axis = self._normalize3(source.get("axis_dir"))

        if kind in {"cylinder", "torus", "cone"} and axis is not None:
            radial_a, radial_b = self._plane_basis(axis)
            if kind == "cylinder":
                radius = abs(float(source.get("radius", nominal.get("diameter", 0.0) / 2.0) or 0.0))
                length = abs(float(source.get("length", nominal.get("length", 0.0)) or 0.0))
                return self._sample_rings(center_array, axis, radial_a, radial_b, radius, radius, length), kind
            if kind == "torus":
                radius = abs(float(nominal.get("groove_diameter", 0.0) or 0.0)) / 2.0
                length = abs(float(nominal.get("groove_width", source.get("minor_radius", 0.0) * 2.0) or 0.0))
                return self._sample_rings(center_array, axis, radial_a, radial_b, radius, radius, length), kind
            radius_a = abs(float(source.get("min_radius", 0.0) or 0.0))
            radius_b = abs(float(source.get("max_radius", radius_a) or radius_a))
            length = abs(float(source.get("height", nominal.get("chamfer_height", 0.0)) or 0.0))
            return self._sample_rings(center_array, axis, radial_a, radial_b, radius_a, radius_b, length), kind

        radius = abs(float(source.get("radius", nominal.get("radius", 0.0)) or 0.0))
        if radius > 0.0:
            points = []
            for axis_a, axis_b in ((0, 1), (0, 2), (1, 2)):
                for index in range(32):
                    angle = 2.0 * math.pi * index / 32.0
                    point = center_array.copy()
                    point[axis_a] += radius * math.cos(angle)
                    point[axis_b] += radius * math.sin(angle)
                    points.append(tuple(float(value) for value in point))
            return points, kind
        return [tuple(float(value) for value in center_array)], "center_only"

    @staticmethod
    def _sample_rings(
        center: np.ndarray,
        axis: np.ndarray,
        radial_a: np.ndarray,
        radial_b: np.ndarray,
        radius_a: float,
        radius_b: float,
        length: float,
    ) -> List[Point3D]:
        if max(radius_a, radius_b, length) <= 1e-10:
            return [tuple(float(value) for value in center)]
        points: List[Point3D] = []
        for axial_ratio, radius in ((-0.5, radius_a), (0.0, (radius_a + radius_b) / 2.0), (0.5, radius_b)):
            ring_center = center + axis * length * axial_ratio
            if radius <= 1e-10:
                points.append(tuple(float(value) for value in ring_center))
                continue
            for index in range(40):
                angle = 2.0 * math.pi * index / 40.0
                point = ring_center + radius * math.cos(angle) * radial_a + radius * math.sin(angle) * radial_b
                points.append(tuple(float(value) for value in point))
        return points

    @staticmethod
    def _project_point(point: Sequence[float], config: Dict[str, Any]) -> Point2D:
        direction = np.asarray(config["direction"], dtype=float)
        up = np.asarray(config["up"], dtype=float)
        side = np.cross(direction, up)
        xyz = np.asarray(point[:3], dtype=float)
        return float(np.dot(xyz, up)), float(np.dot(xyz, side))

    @staticmethod
    def _transform(payload: Optional[Dict[str, Any]]) -> Optional[SimilarityTransform2D]:
        if not isinstance(payload, dict):
            return None
        return SimilarityTransform2D(
            scale=float(payload.get("scale", 1.0) or 1.0),
            rotation_degrees=float(payload.get("rotation_degrees", 0.0) or 0.0),
            translation=tuple(payload.get("translation") or (0.0, 0.0)),
            mirrored=bool(payload.get("mirrored", False)),
        )

    @staticmethod
    def _valid_point3(value: Any) -> bool:
        return isinstance(value, (list, tuple)) and len(value) >= 3

    @staticmethod
    def _normalize3(value: Any) -> Optional[np.ndarray]:
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return None
        result = np.asarray(value[:3], dtype=float)
        length = float(np.linalg.norm(result))
        return result / length if length > 1e-10 else None

    @staticmethod
    def _plane_basis(axis: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        candidates = np.eye(3)
        alignments = [abs(float(np.dot(item, axis))) for item in candidates]
        seed = candidates[int(np.argmin(alignments))]
        first = seed - float(np.dot(seed, axis)) * axis
        first /= np.linalg.norm(first)
        second = np.cross(axis, first)
        second /= np.linalg.norm(second)
        return first, second

    @staticmethod
    def _distance_to_bbox(point: Point2D, bbox: Sequence[float]) -> float:
        dx = max(float(bbox[0]) - point[0], point[0] - float(bbox[2]), 0.0)
        dy = max(float(bbox[1]) - point[1], point[1] - float(bbox[3]), 0.0)
        return math.hypot(dx, dy)


class GlobalFeatureAssignmentResolver:
    """Maximum-weight one-to-one assignment for ambiguous dimension links."""

    METHOD = "GLOBAL_BIPARTITE_FEATURE_ASSIGNMENT_V1"

    def resolve(
        self,
        candidates: Sequence[Dict[str, Any]],
        min_score: float = 0.80,
        min_margin: float = 0.04,
    ) -> Dict[str, Any]:
        eligible = [item for item in candidates if item.get("passed") and float(item.get("score", 0.0)) >= min_score]
        dimensions = sorted({str(item["dimension_key"]) for item in eligible})
        features = sorted({str(item["feature_key"]) for item in eligible})
        if not dimensions or not features:
            return {"method": self.METHOD, "selected": [], "rejected": list(candidates)}
        dimension_index = {key: index for index, key in enumerate(dimensions)}
        feature_index = {key: index for index, key in enumerate(features)}
        weights = [[0.0 for _feature in features] for _dimension in dimensions]
        lookup: Dict[Tuple[int, int], Dict[str, Any]] = {}
        by_dimension: Dict[str, List[Dict[str, Any]]] = {}
        for item in eligible:
            row = dimension_index[str(item["dimension_key"])]
            column = feature_index[str(item["feature_key"])]
            score = float(item.get("score", 0.0))
            if score > weights[row][column]:
                weights[row][column] = score
                lookup[(row, column)] = item
            by_dimension.setdefault(str(item["dimension_key"]), []).append(item)

        selected: List[Dict[str, Any]] = []
        selected_ids = set()
        for row, column in self._hungarian_max(weights):
            item = lookup.get((row, column))
            if item is None:
                continue
            ranked = sorted(
                (float(value.get("score", 0.0)) for value in by_dimension[str(item["dimension_key"])]),
                reverse=True,
            )
            margin = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]
            if margin < min_margin:
                continue
            chosen = dict(item)
            chosen["assignment_margin"] = round(margin, 4)
            selected.append(chosen)
            selected_ids.add(id(item))
        return {
            "method": self.METHOD,
            "selected": selected,
            "rejected": [item for item in candidates if id(item) not in selected_ids],
        }

    @staticmethod
    def _hungarian_max(weights: Sequence[Sequence[float]]) -> List[Tuple[int, int]]:
        """Return row/column pairs using the rectangular Hungarian algorithm."""
        row_count = len(weights)
        column_count = max((len(row) for row in weights), default=0)
        if not row_count or not column_count:
            return []
        size = max(row_count, column_count)
        maximum = max((max(row, default=0.0) for row in weights), default=0.0)
        cost = [[maximum for _ in range(size)] for _ in range(size)]
        for row in range(row_count):
            for column in range(len(weights[row])):
                cost[row][column] = maximum - float(weights[row][column])

        u = [0.0] * (size + 1)
        v = [0.0] * (size + 1)
        p = [0] * (size + 1)
        way = [0] * (size + 1)
        for row in range(1, size + 1):
            p[0] = row
            column0 = 0
            minimum = [float("inf")] * (size + 1)
            used = [False] * (size + 1)
            while True:
                used[column0] = True
                row0 = p[column0]
                delta = float("inf")
                column1 = 0
                for column in range(1, size + 1):
                    if used[column]:
                        continue
                    current = cost[row0 - 1][column - 1] - u[row0] - v[column]
                    if current < minimum[column]:
                        minimum[column] = current
                        way[column] = column0
                    if minimum[column] < delta:
                        delta = minimum[column]
                        column1 = column
                for column in range(size + 1):
                    if used[column]:
                        u[p[column]] += delta
                        v[column] -= delta
                    else:
                        minimum[column] -= delta
                column0 = column1
                if p[column0] == 0:
                    break
            while True:
                column1 = way[column0]
                p[column0] = p[column1]
                column0 = column1
                if column0 == 0:
                    break
        assignment = []
        for column in range(1, size + 1):
            row = p[column] - 1
            actual_column = column - 1
            if row < row_count and actual_column < column_count and weights[row][actual_column] > 0.0:
                assignment.append((row, actual_column))
        return assignment
