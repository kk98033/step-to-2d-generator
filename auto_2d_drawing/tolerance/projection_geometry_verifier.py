"""Conservative 2D dimension to STEP projection evidence verifier.

This module does not modify the drawing or legacy annotation engines.  It
combines three independent observations before a historical tolerance can be
promoted:

1. the DXF dimension is geometrically attached to an unambiguous drawing view;
2. the attached/cross-view entities support the claimed feature semantics;
3. a STEP HLR projection contains the corresponding nominal geometry.

Filename pairing and a matching number alone are intentionally insufficient.
"""

from __future__ import annotations

import bisect
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.config import VIEW_CONFIG
from auto_2d_drawing.view_projector import ViewProjector


class ProjectionGeometryVerifier:
    """Produce explainable 2D/3D evidence for one dimension-feature match."""

    METHOD = "DXF_ATTACHMENT_STEP_HLR_V2"
    MIN_VIEW_SCORE = 0.45
    MIN_CONTOUR_SCORE = 0.08
    MIN_VERIFIED_SCORE = 0.80

    def __init__(self, modelspace, shape=None, step_views: Optional[Dict[str, Any]] = None):
        self.structure = DxfStructure2DAnalyzer(modelspace)
        if step_views is not None:
            self.step_views = step_views
        elif shape is not None:
            self.step_views = ViewProjector().project_all_views(shape)
        else:
            self.step_views = {}

    def verify(self, dimension, node, matched_field: str) -> Dict[str, Any]:
        association = self.structure.analyze_dimension(dimension)
        category = str(getattr(dimension, "dimension_category", "") or "").upper()
        nominal = abs(float(getattr(dimension, "nominal_value", 0.0) or 0.0))
        feature_type = str(getattr(node, "feature_type", "") or "")

        local = self._local_feature_evidence(
            dimension,
            association,
            feature_type,
            matched_field,
            nominal,
        )
        projection = self._step_projection_evidence(feature_type, matched_field, category, nominal)
        signature_views = {
            str(item.get("view"))
            for item in projection.get("matching_views", [])
            if item.get("view")
        }
        view_match = self._best_view_match(
            association.get("primary_view_id"),
            allowed_step_views=signature_views,
        )
        localization = self._node_projection_localization(
            dimension,
            node,
            association,
            view_match,
        )
        contour_score = view_match.get("contour_score")

        checks = {
            "reliable_dimension_attachment": (
                association.get("association_status") in {
                    "NATIVE_ASSOCIATIVE",
                    "GEOMETRIC_ATTACHMENT",
                    "RECOVERED_DIMENSION_GEOMETRY",
                }
                and float(association.get("association_confidence", 0.0) or 0.0) >= 0.90
                and bool(association.get("primary_view_id"))
            ),
            "local_feature_semantics": bool(local.get("passed")),
            "step_projection_signature": bool(projection.get("passed")),
            "drawing_view_matches_step_projection": (
                float(view_match.get("score", 0.0) or 0.0) >= self.MIN_VIEW_SCORE
                and (contour_score is None or float(contour_score) >= self.MIN_CONTOUR_SCORE)
            ),
            "dimension_matches_projected_feature_location": bool(localization.get("passed")),
        }
        score = (
            (0.22 if checks["reliable_dimension_attachment"] else 0.0)
            + (0.18 if checks["local_feature_semantics"] else 0.0)
            + (0.16 if checks["step_projection_signature"] else 0.0)
            + 0.14 * float(view_match.get("score", 0.0) or 0.0)
            + 0.30 * float(localization.get("score", 0.0) or 0.0)
        )
        passed = all(checks.values()) and score >= self.MIN_VERIFIED_SCORE
        return {
            "method": self.METHOD,
            "status": "GEOMETRY_VERIFIED" if passed else "INSUFFICIENT_GEOMETRY_EVIDENCE",
            "passed": passed,
            "score": round(score, 4),
            "checks": checks,
            "association": association,
            "local_feature_evidence": local,
            "step_projection_evidence": projection,
            "view_registration": view_match,
            "feature_localization": localization,
        }

    def _local_feature_evidence(
        self,
        dimension,
        association: Dict[str, Any],
        feature_type: str,
        matched_field: str,
        nominal: float,
    ) -> Dict[str, Any]:
        category = str(getattr(dimension, "dimension_category", "") or "").upper()
        cross_view = dict(association.get("cross_view_evidence") or {})
        visible_pairs = [item for item in cross_view.get("matching_visible_pairs", []) if item.get("outer_silhouette")]
        hidden_pairs = list(cross_view.get("matching_hidden_pairs", []))
        links = list(association.get("definition_point_links") or [])
        exact_links = [
            item for item in links
            if any(candidate.get("relation") == "EXACT_ATTACHMENT" for candidate in item.get("nearest_geometry", []))
        ]
        recovered = dict(association.get("recovered_dimension_geometry") or {})
        recovered_diameter_pair = (
            association.get("association_status") == "RECOVERED_DIMENSION_GEOMETRY"
            and recovered.get("kind") == "DIAMETER_ENDPOINT_PAIR"
            and recovered.get("view_id") == association.get("primary_view_id")
        )
        common_handles = set(association.get("common_attachment_handles") or [])
        exact_circle_handles = {
            candidate.get("handle")
            for link in exact_links
            for candidate in link.get("nearest_geometry", [])
            if (
                candidate.get("relation") == "EXACT_ATTACHMENT"
                and candidate.get("entity_type") == "CIRCLE"
                and candidate.get("handle")
            )
        }
        exact_attached_circle = bool(common_handles & exact_circle_handles)

        passed = False
        signature = "NONE"
        if category == "DIAMETER" and matched_field in {"diameter", "groove_diameter"}:
            if feature_type == "hole" and hidden_pairs and not visible_pairs:
                passed, signature = True, "HIDDEN_PARALLEL_PAIR"
            elif feature_type == "shaft_segment" and visible_pairs and not hidden_pairs:
                passed, signature = True, "OUTER_VISIBLE_PARALLEL_PAIR"
            elif feature_type == "retaining_ring_groove" and (visible_pairs or hidden_pairs):
                passed, signature = True, "GROOVE_DIAMETER_CROSS_VIEW_PAIR"
            elif feature_type in {"hole", "shaft_segment"} and exact_attached_circle:
                # The circle proves that the callout is attached to local
                # cylindrical geometry.  Hole-vs-shaft identity still comes
                # from the unique STEP node plus the independent projection
                # localization check; 2D geometry alone is not promoted.
                passed, signature = True, "EXACT_ATTACHED_CIRCLE_WITH_3D_CLASSIFICATION"
            elif feature_type in {"hole", "shaft_segment", "retaining_ring_groove"} and recovered_diameter_pair:
                # The endpoint pair proves the local measured diameter.  The
                # separate STEP projection and node-location checks must still
                # pass before this case can become AUTO_VERIFIED.
                passed, signature = True, "RECOVERED_DIAMETER_ENDPOINT_PAIR"
        elif category == "LINEAR" and matched_field in {
            "length", "groove_width", "step_height", "chamfer_height"
        }:
            exact_handle_sets = []
            for link in exact_links:
                exact_handle_sets.append({
                    item.get("handle")
                    for item in link.get("nearest_geometry", [])
                    if item.get("relation") == "EXACT_ATTACHMENT" and item.get("handle")
                })
            distinct_handles = set().union(*exact_handle_sets) if exact_handle_sets else set()
            passed = len(exact_links) >= 2 and len(distinct_handles) >= 2
            signature = "TWO_EXACT_EXTENSION_ENDPOINTS" if passed else "INSUFFICIENT_EXTENSION_ENDPOINTS"
        elif category == "CHAMFER" and matched_field == "chamfer_height":
            passed = len(exact_links) >= 2
            signature = "CHAMFER_CALLOUT_ATTACHED" if passed else "UNATTACHED_CHAMFER_CALLOUT"
        elif category == "RADIUS" and matched_field == "radius":
            passed = len(exact_links) >= 1
            signature = "RADIUS_CALLOUT_ATTACHED" if passed else "UNATTACHED_RADIUS_CALLOUT"

        return {
            "passed": passed,
            "signature": signature,
            "exact_definition_point_count": len(exact_links),
            "outer_visible_pair_count": len(visible_pairs),
            "hidden_pair_count": len(hidden_pairs),
            "exact_attached_circle": exact_attached_circle,
            "recovered_diameter_endpoint_pair": recovered_diameter_pair,
            "nominal_value": nominal,
        }

    def _step_projection_evidence(
        self,
        feature_type: str,
        matched_field: str,
        category: str,
        nominal: float,
    ) -> Dict[str, Any]:
        matches: List[Dict[str, Any]] = []
        tolerance = max(0.02, min(0.20, nominal * 0.003))
        for view_name, view in self.step_views.items():
            visible = list(view.get("visible") or [])
            hidden = list(view.get("hidden") or [])
            visible_separation = self._has_axis_separation(visible, nominal, tolerance)
            hidden_separation = self._has_axis_separation(hidden, nominal, tolerance)
            circle_match = self._matching_circles(visible + hidden, nominal, tolerance)
            line_length_match = self._matching_line_lengths(visible, nominal, tolerance)

            matched = False
            signature = None
            if category == "DIAMETER" and matched_field in {"diameter", "groove_diameter"}:
                if feature_type == "hole":
                    matched = bool(circle_match or hidden_separation)
                    signature = "CIRCLE_OR_HIDDEN_PAIR"
                else:
                    matched = bool(circle_match or visible_separation)
                    signature = "CIRCLE_OR_VISIBLE_PAIR"
            elif category in {"LINEAR", "CHAMFER", "RADIUS"}:
                matched = bool(visible_separation or line_length_match)
                signature = "VISIBLE_LENGTH_OR_SEPARATION"
            if matched:
                matches.append({
                    "view": view_name,
                    "signature": signature,
                    "circle_matches": circle_match,
                    "visible_axis_separation": visible_separation,
                    "hidden_axis_separation": hidden_separation,
                    "visible_line_length": line_length_match,
                })
        return {
            "passed": bool(matches),
            "nominal_value": nominal,
            "tolerance": round(tolerance, 6),
            "matching_views": matches,
        }

    def _best_view_match(
        self,
        dxf_view_id: Optional[str],
        allowed_step_views: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if not dxf_view_id:
            return {"score": 0.0, "dxf_view_id": None, "step_view": None}
        cluster = next((item for item in self.structure.view_clusters if item.view_id == dxf_view_id), None)
        if cluster is None or cluster.width <= 0.0 or cluster.height <= 0.0:
            return {"score": 0.0, "dxf_view_id": dxf_view_id, "step_view": None}

        dxf_points = self._dxf_cluster_points(cluster)
        best = {
            "score": 0.0,
            "dxf_view_id": dxf_view_id,
            "step_view": None,
            "contour_score": None,
        }
        allowed = set(allowed_step_views or ())
        if allowed_step_views is not None and not allowed:
            return best
        for name, view in self.step_views.items():
            if allowed and name not in allowed:
                continue
            width, height = view.get("size") or (0.0, 0.0)
            width, height = float(width), float(height)
            if width <= 0.0 or height <= 0.0:
                continue
            step_points = self._step_view_points(view)
            transforms = []
            for rotation in (0, 90, 180, 270):
                shape_score = self._shape_score(
                    cluster.width,
                    cluster.height,
                    width if rotation % 180 == 0 else height,
                    height if rotation % 180 == 0 else width,
                )
                for mirrored in (False, True):
                    contour = self._contour_similarity(
                        dxf_points,
                        step_points,
                        cluster.bbox,
                        view.get("bbox") or (),
                        rotation,
                        mirrored,
                    )
                    score = shape_score if contour is None else 0.45 * shape_score + 0.55 * contour
                    transforms.append((score, contour, rotation, mirrored))
            score, contour, rotation, mirrored = max(transforms, key=lambda item: item[0])
            if score > best["score"]:
                best = {
                    "score": round(score, 4),
                    "dxf_view_id": dxf_view_id,
                    "step_view": name,
                    "rotation_degrees": rotation,
                    "mirrored": mirrored,
                    "contour_score": None if contour is None else round(contour, 4),
                    "dxf_entity_count": len(cluster.primitive_indexes),
                    "dxf_bbox": [round(value, 4) for value in cluster.bbox],
                    "step_bbox": [round(float(value), 4) for value in (view.get("bbox") or ())],
                }
        return best

    def _node_projection_localization(
        self,
        dimension,
        node,
        association: Dict[str, Any],
        view_match: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check that the dimension lies at the matched 3D node projection.

        A matching nominal value anywhere in the assembly is not enough.  The
        projected feature center must agree with the dimension attachment in
        the registered drawing view.  Linear dimensions are compared most
        strongly along their measurement axis because extension lines may be
        placed far away in the perpendicular direction.
        """

        source = dict(getattr(node, "source_info", {}) or {})
        center = source.get("center")
        step_view = view_match.get("step_view")
        dxf_view_id = association.get("primary_view_id")
        if not isinstance(center, (list, tuple)) or len(center) < 3:
            return {
                "passed": False,
                "score": 0.0,
                "status": "MISSING_NODE_SPATIAL_PROVENANCE",
            }
        if not step_view or step_view not in self.step_views or not dxf_view_id:
            return {"passed": False, "score": 0.0, "status": "VIEW_REGISTRATION_UNAVAILABLE"}
        cluster = next((item for item in self.structure.view_clusters if item.view_id == dxf_view_id), None)
        if cluster is None or cluster.width <= 0.0 or cluster.height <= 0.0:
            return {"passed": False, "score": 0.0, "status": "DXF_VIEW_UNAVAILABLE"}

        step_bbox = self.step_views[step_view].get("bbox") or ()
        if len(step_bbox) != 4 or float(step_bbox[2]) <= float(step_bbox[0]) or float(step_bbox[3]) <= float(step_bbox[1]):
            return {"passed": False, "score": 0.0, "status": "STEP_VIEW_BBOX_UNAVAILABLE"}
        projected = self._project_point_to_view(center, step_view)
        step_normalized = (
            (projected[0] - float(step_bbox[0])) / (float(step_bbox[2]) - float(step_bbox[0])),
            (projected[1] - float(step_bbox[1])) / (float(step_bbox[3]) - float(step_bbox[1])),
        )
        expected = self._transform_normalized(
            step_normalized,
            int(view_match.get("rotation_degrees", 0) or 0),
            bool(view_match.get("mirrored", False)),
        )
        anchor = self._dimension_anchor(dimension, association)
        if anchor is None:
            return {"passed": False, "score": 0.0, "status": "DIMENSION_ANCHOR_UNAVAILABLE"}
        observed = (
            (anchor[0] - cluster.bbox[0]) / cluster.width,
            (anchor[1] - cluster.bbox[1]) / cluster.height,
        )
        delta = (expected[0] - observed[0], expected[1] - observed[1])
        category = str(getattr(dimension, "dimension_category", "") or "").upper()

        axis = self._normalized_dimension_axis(dimension, cluster)
        if category in {"LINEAR", "CHAMFER"} and axis is not None:
            along_error = abs(delta[0] * axis[0] + delta[1] * axis[1])
            cross_error = abs(delta[0] * axis[1] - delta[1] * axis[0])
            passed = along_error <= 0.12 and cross_error <= 0.45
            score = math.exp(-along_error / 0.08) * math.exp(-max(0.0, cross_error - 0.20) / 0.25)
            distance = math.hypot(*delta)
        else:
            distance = math.hypot(*delta)
            along_error = distance
            cross_error = 0.0
            passed = distance <= 0.16
            score = math.exp(-distance / 0.10)
        return {
            "passed": passed,
            "score": round(score, 4),
            "status": "PROJECTED_NODE_ALIGNED" if passed else "PROJECTED_NODE_MISMATCH",
            "step_view": step_view,
            "node_center_3d": [round(float(value), 4) for value in center[:3]],
            "projected_step_point": [round(value, 4) for value in projected],
            "expected_normalized_point": [round(value, 4) for value in expected],
            "observed_normalized_anchor": [round(value, 4) for value in observed],
            "normalized_distance": round(distance, 4),
            "along_axis_error": round(along_error, 4),
            "cross_axis_error": round(cross_error, 4),
        }

    @staticmethod
    def _shape_score(dxf_width: float, dxf_height: float, step_width: float, step_height: float) -> float:
        scale_x = dxf_width / step_width
        scale_y = dxf_height / step_height
        scale_consistency = min(scale_x, scale_y) / max(scale_x, scale_y)
        dxf_aspect = dxf_width / dxf_height
        step_aspect = step_width / step_height
        aspect_score = math.exp(-abs(math.log(max(dxf_aspect, 1e-9) / max(step_aspect, 1e-9))))
        return 0.65 * scale_consistency + 0.35 * aspect_score

    def _dxf_cluster_points(self, cluster) -> List[Tuple[float, float]]:
        primitives = getattr(self.structure, "primitives", [])
        result: List[Tuple[float, float]] = []
        for index in cluster.primitive_indexes:
            if index >= len(primitives):
                continue
            primitive = primitives[index]
            if primitive.center is not None and primitive.radius > 0.0:
                cx, cy = primitive.center
                result.extend(
                    (
                        cx + primitive.radius * math.cos(2.0 * math.pi * step / 32.0),
                        cy + primitive.radius * math.sin(2.0 * math.pi * step / 32.0),
                    )
                    for step in range(32)
                )
            else:
                for start, end in zip(primitive.points, primitive.points[1:]):
                    result.extend(self._sample_segment(start, end, 24))
        return result

    @classmethod
    def _step_view_points(cls, view: Dict[str, Any]) -> List[Tuple[float, float]]:
        result: List[Tuple[float, float]] = []
        for edge in list(view.get("visible") or []) + list(view.get("hidden") or []):
            if edge.get("type") == "line" and edge.get("p1") and edge.get("p2"):
                result.extend(cls._sample_segment(edge["p1"], edge["p2"], 24))
            elif edge.get("type") == "circle" and edge.get("center") and edge.get("radius"):
                cx, cy = edge["center"]
                radius = float(edge["radius"])
                result.extend(
                    (
                        float(cx) + radius * math.cos(2.0 * math.pi * step / 32.0),
                        float(cy) + radius * math.sin(2.0 * math.pi * step / 32.0),
                    )
                    for step in range(32)
                )
            elif edge.get("points"):
                points = edge["points"]
                for start, end in zip(points, points[1:]):
                    result.extend(cls._sample_segment(start, end, 12))
        return result

    @staticmethod
    def _sample_segment(start, end, max_count: int) -> List[Tuple[float, float]]:
        length = math.dist(start, end)
        count = max(1, min(max_count, int(math.ceil(length)) + 1))
        return [
            (
                float(start[0]) + (float(end[0]) - float(start[0])) * index / count,
                float(start[1]) + (float(end[1]) - float(start[1])) * index / count,
            )
            for index in range(count + 1)
        ]

    @classmethod
    def _contour_similarity(
        cls,
        dxf_points: List[Tuple[float, float]],
        step_points: List[Tuple[float, float]],
        dxf_bbox,
        step_bbox,
        rotation: int,
        mirrored: bool,
        grid_size: int = 32,
    ) -> Optional[float]:
        if not dxf_points or not step_points or len(step_bbox) != 4:
            return None
        dw = float(dxf_bbox[2]) - float(dxf_bbox[0])
        dh = float(dxf_bbox[3]) - float(dxf_bbox[1])
        sw = float(step_bbox[2]) - float(step_bbox[0])
        sh = float(step_bbox[3]) - float(step_bbox[1])
        if min(dw, dh, sw, sh) <= 0.0:
            return None

        def cells(points, bbox, width, height, transform=False):
            occupied = set()
            for x, y in points:
                normalized = (
                    (float(x) - float(bbox[0])) / width,
                    (float(y) - float(bbox[1])) / height,
                )
                if transform:
                    normalized = cls._transform_normalized(normalized, rotation, mirrored)
                gx = max(0, min(grid_size - 1, int(normalized[0] * (grid_size - 1))))
                gy = max(0, min(grid_size - 1, int(normalized[1] * (grid_size - 1))))
                occupied.add((gx, gy))
            return occupied

        dxf_cells = cells(dxf_points, dxf_bbox, dw, dh)
        step_cells = cells(step_points, step_bbox, sw, sh, transform=True)
        if not dxf_cells or not step_cells:
            return None

        def directional(source, target):
            matches = 0
            for x, y in source:
                if any((x + dx, y + dy) in target for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                    matches += 1
            return matches / len(source)

        return 0.5 * directional(dxf_cells, step_cells) + 0.5 * directional(step_cells, dxf_cells)

    @staticmethod
    def _transform_normalized(point: Tuple[float, float], rotation: int, mirrored: bool) -> Tuple[float, float]:
        x, y = point
        if mirrored:
            x = 1.0 - x
        rotation %= 360
        if rotation == 90:
            return 1.0 - y, x
        if rotation == 180:
            return 1.0 - x, 1.0 - y
        if rotation == 270:
            return y, 1.0 - x
        return x, y

    @staticmethod
    def _project_point_to_view(point, view_name: str) -> Tuple[float, float]:
        cfg = VIEW_CONFIG[view_name]
        direction = tuple(float(value) for value in cfg["direction"])
        up = tuple(float(value) for value in cfg["up"])
        y_axis = (
            direction[1] * up[2] - direction[2] * up[1],
            direction[2] * up[0] - direction[0] * up[2],
            direction[0] * up[1] - direction[1] * up[0],
        )
        xyz = tuple(float(value) for value in point[:3])
        return (
            sum(xyz[index] * up[index] for index in range(3)),
            sum(xyz[index] * y_axis[index] for index in range(3)),
        )

    def _dimension_anchor(self, dimension, association: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        category = str(getattr(dimension, "dimension_category", "") or "").upper()
        recovered = dict(association.get("recovered_dimension_geometry") or {})
        recovered_center = recovered.get("center")
        if (
            association.get("association_status") == "RECOVERED_DIMENSION_GEOMETRY"
            and recovered.get("kind") == "DIAMETER_ENDPOINT_PAIR"
            and isinstance(recovered_center, (list, tuple))
            and len(recovered_center) >= 2
        ):
            return float(recovered_center[0]), float(recovered_center[1])
        if category in {"DIAMETER", "RADIUS"}:
            centers = []
            primitives = getattr(self.structure, "primitives", [])
            handle_index = getattr(self.structure, "handle_index", {})
            for handle in association.get("attached_geometry_handles", []):
                index = handle_index.get(handle)
                if index is not None and index < len(primitives) and primitives[index].center is not None:
                    centers.append(primitives[index].center)
            if centers:
                return (
                    sum(point[0] for point in centers) / len(centers),
                    sum(point[1] for point in centers) / len(centers),
                )
        points = dict(getattr(dimension, "points", {}) or {})
        keys = ("defpoint2", "defpoint3") if category not in {"DIAMETER", "RADIUS"} else ("defpoint", "defpoint4")
        values = [points.get(key) for key in keys]
        values = [value for value in values if isinstance(value, (list, tuple)) and len(value) >= 2]
        if not values:
            return None
        return (
            sum(float(value[0]) for value in values) / len(values),
            sum(float(value[1]) for value in values) / len(values),
        )

    @staticmethod
    def _normalized_dimension_axis(dimension, cluster) -> Optional[Tuple[float, float]]:
        points = dict(getattr(dimension, "points", {}) or {})
        start, end = points.get("defpoint2"), points.get("defpoint3")
        if not isinstance(start, (list, tuple)) or not isinstance(end, (list, tuple)) or len(start) < 2 or len(end) < 2:
            return None
        dx = (float(end[0]) - float(start[0])) / cluster.width
        dy = (float(end[1]) - float(start[1])) / cluster.height
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            return None
        return dx / length, dy / length

    @staticmethod
    def _axis_coordinates(edges: Iterable[Dict[str, Any]]) -> Tuple[List[float], List[float]]:
        horizontal: List[float] = []
        vertical: List[float] = []
        for edge in edges:
            if edge.get("type") != "line":
                continue
            p1, p2 = edge.get("p1"), edge.get("p2")
            if not p1 or not p2:
                continue
            dx, dy = abs(float(p2[0]) - float(p1[0])), abs(float(p2[1]) - float(p1[1]))
            if dx >= max(dy * 20.0, 1e-8):
                horizontal.append((float(p1[1]) + float(p2[1])) / 2.0)
            elif dy >= max(dx * 20.0, 1e-8):
                vertical.append((float(p1[0]) + float(p2[0])) / 2.0)
        return sorted(set(horizontal)), sorted(set(vertical))

    @classmethod
    def _has_axis_separation(cls, edges: Iterable[Dict[str, Any]], target: float, tolerance: float) -> bool:
        for coordinates in cls._axis_coordinates(edges):
            for value in coordinates:
                expected = value + target
                index = bisect.bisect_left(coordinates, expected - tolerance)
                if index < len(coordinates) and coordinates[index] <= expected + tolerance:
                    return True
        return False

    @staticmethod
    def _matching_circles(edges: Iterable[Dict[str, Any]], target: float, tolerance: float) -> int:
        return sum(
            abs(float(edge.get("radius", 0.0) or 0.0) * 2.0 - target) <= tolerance
            for edge in edges
            if edge.get("type") == "circle" and not edge.get("is_arc", False)
        )

    @staticmethod
    def _matching_line_lengths(edges: Iterable[Dict[str, Any]], target: float, tolerance: float) -> int:
        count = 0
        for edge in edges:
            if edge.get("type") != "line" or not edge.get("p1") or not edge.get("p2"):
                continue
            if abs(math.dist(edge["p1"], edge["p2"]) - target) <= tolerance:
                count += 1
        return count
