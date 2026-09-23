"""Recover dimension-to-geometry links and drawing-view structure from DXF.

Native AutoCAD associative dependencies are preferred when present.  For
converted drawings that lost those handles, this module falls back to exact
definition-point attachment and conservative spatial view grouping.
"""

from __future__ import annotations

import math
import statistics
import bisect
import heapq
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


Point2D = Tuple[float, float]
BBox2D = Tuple[float, float, float, float]


@dataclass
class CadPrimitive2D:
    entity_type: str
    handle: str
    layer: str
    linetype: str = "CONTINUOUS"
    points: List[Point2D] = field(default_factory=list)
    center: Optional[Point2D] = None
    radius: float = 0.0

    @property
    def bbox(self) -> BBox2D:
        if self.center is not None and self.radius > 0.0:
            x, y = self.center
            return (x - self.radius, y - self.radius, x + self.radius, y + self.radius)
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def length(self) -> float:
        if self.radius > 0.0:
            return self.radius * 2.0
        return sum(math.dist(left, right) for left, right in zip(self.points, self.points[1:]))


@dataclass
class ViewCluster2D:
    view_id: str
    primitive_indexes: List[int]
    bbox: BBox2D

    @property
    def center(self) -> Point2D:
        return ((self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0)

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


class DxfStructure2DAnalyzer:
    """Analyze vector topology without modifying the legacy drawing engine."""

    METHOD = "DXF_ASSOCIATION_AND_VIEW_GRAPH_V1"
    ANNOTATION_LAYER_TOKENS = (
        "dim", "text", "note", "title", "border", "frame", "尺寸", "標注",
        "标注", "圖框", "图框", "rev", "ecn",
    )
    HIDDEN_LAYER_TOKENS = ("hidden", "hid", "dash", "隱藏", "隐藏")

    def __init__(self, modelspace):
        self.primitives = self._collect_primitives(modelspace)
        self.handle_index = {item.handle: index for index, item in enumerate(self.primitives) if item.handle}
        self.view_clusters = self._build_view_clusters()
        self.primitive_to_view: Dict[int, str] = {}
        for cluster in self.view_clusters:
            for primitive_index in cluster.primitive_indexes:
                self.primitive_to_view[primitive_index] = cluster.view_id
        self.axis_lines: Dict[str, List[Tuple[float, int]]] = {"HORIZONTAL": [], "VERTICAL": []}
        for index, primitive in enumerate(self.primitives):
            if primitive.entity_type != "LINE":
                continue
            orientation = self._line_orientation(primitive)
            if orientation == "HORIZONTAL":
                coordinate = (primitive.points[0][1] + primitive.points[-1][1]) / 2.0
                self.axis_lines[orientation].append((coordinate, index))
            elif orientation == "VERTICAL":
                coordinate = (primitive.points[0][0] + primitive.points[-1][0]) / 2.0
                self.axis_lines[orientation].append((coordinate, index))
        for values in self.axis_lines.values():
            values.sort()

    def analyze_dimension(self, dimension) -> Dict[str, Any]:
        references = self._dimension_reference_points(dimension)
        nominal = abs(float(getattr(dimension, "nominal_value", 0.0) or 0.0))
        exact_tolerance = max(0.01, min(0.10, max(nominal, 1.0) * 0.001))
        nearby_tolerance = max(0.25, min(2.0, max(nominal, 1.0) * 0.04))
        native_metadata = dict(getattr(dimension, "association_metadata", {}) or {})

        native_indexes = [
            self.handle_index[handle]
            for handle in native_metadata.get("direct_geometry_handles", [])
            if handle in self.handle_index
        ]
        reference_results: List[Dict[str, Any]] = []
        attached_indexes = set(native_indexes)
        for key, point in references:
            ranked = heapq.nsmallest(
                4,
                ((self._distance_to_primitive(point, item), index, item) for index, item in enumerate(self.primitives)),
                key=lambda row: row[0],
            )
            nearest = []
            for distance, index, item in ranked:
                if distance > nearby_tolerance or len(nearest) >= 4:
                    break
                relation = "EXACT_ATTACHMENT" if distance <= exact_tolerance else "NEARBY_GEOMETRY"
                nearest.append({
                    "handle": item.handle,
                    "entity_type": item.entity_type,
                    "layer": item.layer,
                    "distance": round(distance, 6),
                    "relation": relation,
                })
                if distance <= exact_tolerance:
                    attached_indexes.add(index)
            reference_results.append({
                "definition_point": key,
                "point": [round(point[0], 4), round(point[1], 4)],
                "nearest_geometry": nearest,
            })

        exact_by_reference = [
            [item for item in result["nearest_geometry"] if item["relation"] == "EXACT_ATTACHMENT"]
            for result in reference_results
        ]
        exact_handle_sets = [
            {item["handle"] for item in items if item["handle"]}
            for items in exact_by_reference if items
        ]
        common_exact_handles = (
            sorted(set.intersection(*exact_handle_sets)) if len(exact_handle_sets) >= 2 else []
        )
        references_with_exact = sum(bool(items) for items in exact_by_reference)
        ambiguous_exact = sum(len(items) > 1 for items in exact_by_reference)
        if native_indexes:
            association_status = "NATIVE_ASSOCIATIVE"
            confidence = 0.99
        elif len(common_exact_handles) == 1:
            association_status = "GEOMETRIC_ATTACHMENT"
            confidence = 0.95
        elif references_with_exact and not ambiguous_exact:
            association_status = "GEOMETRIC_ATTACHMENT"
            confidence = 0.92 if references_with_exact >= 2 else 0.82
        elif references_with_exact:
            association_status = "AMBIGUOUS_ATTACHMENT"
            confidence = 0.60
        else:
            association_status = "NO_ATTACHMENT"
            confidence = 0.0

        view_ids = sorted({
            self.primitive_to_view[index]
            for index in attached_indexes
            if index in self.primitive_to_view
        })
        primary_view = view_ids[0] if len(view_ids) == 1 else None
        cross_view = self._cross_view_evidence(dimension, attached_indexes, primary_view)

        return {
            "method": self.METHOD,
            "native_association_available": bool(native_indexes),
            "association_status": association_status,
            "association_confidence": round(confidence, 3),
            "definition_point_links": reference_results,
            "attached_geometry_handles": sorted({self.primitives[index].handle for index in attached_indexes if self.primitives[index].handle}),
            "common_attachment_handles": common_exact_handles,
            "view_ids": view_ids,
            "primary_view_id": primary_view,
            "cross_view_evidence": cross_view,
            "view_cluster_count": len(self.view_clusters),
        }

    def view_summaries(self) -> List[Dict[str, Any]]:
        summaries = []
        for cluster in self.view_clusters:
            items = [self.primitives[index] for index in cluster.primitive_indexes]
            summaries.append({
                "view_id": cluster.view_id,
                "bbox": [round(value, 3) for value in cluster.bbox],
                "center": [round(value, 3) for value in cluster.center],
                "entity_count": len(items),
                "circle_count": sum(item.entity_type == "CIRCLE" for item in items),
                "hidden_entity_count": sum(self._is_hidden(item.layer, item.linetype) for item in items),
            })
        return summaries

    def _cross_view_evidence(
        self,
        dimension,
        attached_indexes: Iterable[int],
        primary_view: Optional[str],
    ) -> Dict[str, Any]:
        category = str(getattr(dimension, "dimension_category", "") or "").upper()
        nominal = abs(float(getattr(dimension, "nominal_value", 0.0) or 0.0))
        if category != "DIAMETER" or nominal <= 0.0:
            return {"matching_visible_pairs": [], "matching_hidden_pairs": []}

        attached_circles = [
            self.primitives[index]
            for index in attached_indexes
            if self.primitives[index].entity_type == "CIRCLE" and self.primitives[index].center is not None
        ]
        if not attached_circles:
            return {"matching_visible_pairs": [], "matching_hidden_pairs": []}
        anchor = attached_circles[0].center
        tolerance = max(0.05, nominal * 0.01)
        visible_pairs: List[Dict[str, Any]] = []
        hidden_pairs: List[Dict[str, Any]] = []

        alignment_tolerance = max(0.5, nominal * 0.08)
        for left_orientation in ("HORIZONTAL", "VERTICAL"):
            anchor_axis = anchor[1] if left_orientation == "HORIZONTAL" else anchor[0]
            lower_indexes = self._axis_line_indexes_near(left_orientation, anchor_axis - nominal / 2.0, tolerance)
            upper_indexes = self._axis_line_indexes_near(left_orientation, anchor_axis + nominal / 2.0, tolerance)
            for left_index in lower_indexes:
                left = self.primitives[left_index]
                for right_index in upper_indexes:
                    if left_index == right_index:
                        continue
                    right = self.primitives[right_index]
                    separation = self._parallel_line_separation(left, right, left_orientation)
                    if abs(separation - nominal) > tolerance or not self._line_ranges_overlap(left, right, left_orientation):
                        continue
                    pair_center = self._line_pair_center(left, right)
                    aligned = (
                        abs(pair_center[1] - anchor[1]) <= alignment_tolerance
                        if left_orientation == "HORIZONTAL"
                        else abs(pair_center[0] - anchor[0]) <= alignment_tolerance
                    )
                    if not aligned or math.dist(pair_center, anchor) <= nominal:
                        continue
                    view_ids = sorted({
                        self.primitive_to_view[index]
                        for index in (left_index, right_index)
                        if index in self.primitive_to_view
                    })
                    payload = {
                        "view_ids": view_ids,
                        "handles": [left.handle, right.handle],
                        "orientation": left_orientation,
                        "separation": round(separation, 4),
                        "outer_silhouette": self._is_outer_silhouette_pair(
                            left_index,
                            right_index,
                            left,
                            right,
                            left_orientation,
                            tolerance,
                        ),
                    }
                    if self._is_hidden(left.layer, left.linetype) and self._is_hidden(right.layer, right.linetype):
                        hidden_pairs.append(payload)
                    elif not self._is_hidden(left.layer, left.linetype) and not self._is_hidden(right.layer, right.linetype):
                        visible_pairs.append(payload)
        return {
            "matching_visible_pairs": visible_pairs[:12],
            "matching_hidden_pairs": hidden_pairs[:12],
        }

    def _axis_line_indexes_near(self, orientation: str, coordinate: float, tolerance: float) -> List[int]:
        values = self.axis_lines.get(orientation, [])
        coordinates = [item[0] for item in values]
        start = bisect.bisect_left(coordinates, coordinate - tolerance)
        end = bisect.bisect_right(coordinates, coordinate + tolerance)
        return [index for _value, index in values[start:end]]

    def _build_view_clusters(self) -> List[ViewCluster2D]:
        candidate_indexes = [
            index for index, item in enumerate(self.primitives)
            if not self._is_annotation_layer(item.layer)
        ]
        if not candidate_indexes:
            return []
        lengths = [self.primitives[index].length for index in candidate_indexes if self.primitives[index].length > 1e-6]
        typical = statistics.median(lengths) if lengths else 1.0
        max_length = max(100.0, typical * 30.0)
        candidate_indexes = [index for index in candidate_indexes if self.primitives[index].length <= max_length]

        # Sparse center-grid clustering is intentionally linear in entity
        # count.  Expanding long entity bboxes across every occupied grid cell
        # made dense production drawings prohibitively expensive.
        cell_size = max(5.0, min(50.0, typical * 4.0))
        buckets: Dict[Tuple[int, int], List[int]] = {}
        for index in candidate_indexes:
            bbox = self.primitives[index].bbox
            center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
            cell = (math.floor(center[0] / cell_size), math.floor(center[1] / cell_size))
            buckets.setdefault(cell, []).append(index)

        groups: List[List[int]] = []
        unvisited = set(buckets)
        while unvisited:
            start = unvisited.pop()
            stack = [start]
            indexes: List[int] = []
            while stack:
                cell = stack.pop()
                indexes.extend(buckets[cell])
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        neighbor = (cell[0] + dx, cell[1] + dy)
                        if neighbor in unvisited:
                            unvisited.remove(neighbor)
                            stack.append(neighbor)
            groups.append(indexes)
        retained = [
            indexes for indexes in groups
            if len(indexes) >= 2 or any(self.primitives[index].entity_type == "CIRCLE" for index in indexes)
        ]
        retained.sort(key=lambda indexes: self._group_bbox(indexes))
        return [
            ViewCluster2D(f"view_{number:03d}", indexes, self._group_bbox(indexes))
            for number, indexes in enumerate(retained, start=1)
        ]

    def _group_bbox(self, indexes: Sequence[int]) -> BBox2D:
        boxes = [self.primitives[index].bbox for index in indexes]
        return (
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        )

    @staticmethod
    def _dimension_reference_points(dimension) -> List[Tuple[str, Point2D]]:
        points = dict(getattr(dimension, "points", {}) or {})
        category = str(getattr(dimension, "dimension_category", "") or "").upper()
        if category in {"DIAMETER", "RADIUS"}:
            keys = ("defpoint", "defpoint4")
        elif category == "ANGULAR":
            keys = ("defpoint2", "defpoint3", "defpoint4")
        else:
            keys = ("defpoint2", "defpoint3")
        result = []
        for key in keys:
            value = points.get(key)
            if isinstance(value, Sequence) and len(value) >= 2:
                point = (float(value[0]), float(value[1]))
                if point != (0.0, 0.0) or not result:
                    result.append((key, point))
        return result

    @classmethod
    def _distance_to_primitive(cls, point: Point2D, primitive: CadPrimitive2D) -> float:
        if primitive.center is not None and primitive.radius > 0.0:
            return abs(math.dist(point, primitive.center) - primitive.radius)
        if len(primitive.points) == 1:
            return math.dist(point, primitive.points[0])
        if len(primitive.points) >= 2:
            return min(cls._distance_to_segment(point, left, right) for left, right in zip(primitive.points, primitive.points[1:]))
        return float("inf")

    @staticmethod
    def _distance_to_segment(point: Point2D, start: Point2D, end: Point2D) -> float:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            return math.dist(point, start)
        ratio = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
        projection = (start[0] + ratio * dx, start[1] + ratio * dy)
        return math.dist(point, projection)

    @staticmethod
    def _bbox_distance(left: BBox2D, right: BBox2D) -> float:
        dx = max(left[0] - right[2], right[0] - left[2], 0.0)
        dy = max(left[1] - right[3], right[1] - left[3], 0.0)
        return math.hypot(dx, dy)

    @staticmethod
    def _expand_bbox(bbox: BBox2D, amount: float) -> BBox2D:
        return (bbox[0] - amount, bbox[1] - amount, bbox[2] + amount, bbox[3] + amount)

    def _is_hidden(self, layer: str, linetype: str = "") -> bool:
        lowered = f"{layer or ''} {linetype or ''}".lower()
        return any(token in lowered for token in self.HIDDEN_LAYER_TOKENS)

    def _is_annotation_layer(self, layer: str) -> bool:
        lowered = str(layer or "").lower()
        return any(token in lowered for token in self.ANNOTATION_LAYER_TOKENS)

    @staticmethod
    def _line_orientation(line: CadPrimitive2D) -> Optional[str]:
        if len(line.points) < 2:
            return None
        dx = abs(line.points[-1][0] - line.points[0][0])
        dy = abs(line.points[-1][1] - line.points[0][1])
        if dx >= max(dy * 20.0, 1e-8):
            return "HORIZONTAL"
        if dy >= max(dx * 20.0, 1e-8):
            return "VERTICAL"
        return None

    @staticmethod
    def _parallel_line_separation(left: CadPrimitive2D, right: CadPrimitive2D, orientation: str) -> float:
        axis = 1 if orientation == "HORIZONTAL" else 0
        left_value = sum(point[axis] for point in left.points[:2]) / 2.0
        right_value = sum(point[axis] for point in right.points[:2]) / 2.0
        return abs(left_value - right_value)

    @staticmethod
    def _line_ranges_overlap(left: CadPrimitive2D, right: CadPrimitive2D, orientation: str) -> bool:
        axis = 0 if orientation == "HORIZONTAL" else 1
        left_range = sorted((left.points[0][axis], left.points[-1][axis]))
        right_range = sorted((right.points[0][axis], right.points[-1][axis]))
        return min(left_range[1], right_range[1]) >= max(left_range[0], right_range[0])

    @staticmethod
    def _line_pair_center(left: CadPrimitive2D, right: CadPrimitive2D) -> Point2D:
        points = left.points[:2] + right.points[:2]
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )

    def _is_outer_silhouette_pair(
        self,
        left_index: int,
        right_index: int,
        left: CadPrimitive2D,
        right: CadPrimitive2D,
        orientation: str,
        tolerance: float,
    ) -> bool:
        range_axis = 0 if orientation == "HORIZONTAL" else 1
        separation_axis = 1 if orientation == "HORIZONTAL" else 0
        overlap_start = max(
            min(left.points[0][range_axis], left.points[-1][range_axis]),
            min(right.points[0][range_axis], right.points[-1][range_axis]),
        )
        overlap_end = min(
            max(left.points[0][range_axis], left.points[-1][range_axis]),
            max(right.points[0][range_axis], right.points[-1][range_axis]),
        )
        sample = (overlap_start + overlap_end) / 2.0
        pair_coordinates = [
            sum(point[separation_axis] for point in item.points[:2]) / 2.0
            for item in (left, right)
        ]
        lower, upper = min(pair_coordinates), max(pair_coordinates)
        pair_view_ids = {
            self.primitive_to_view[index]
            for index in (left_index, right_index)
            if index in self.primitive_to_view
        }
        for primitive_index, primitive in enumerate(self.primitives):
            if pair_view_ids and self.primitive_to_view.get(primitive_index) not in pair_view_ids:
                continue
            if primitive.entity_type != "LINE" or self._is_hidden(primitive.layer, primitive.linetype):
                continue
            if self._line_orientation(primitive) != orientation:
                continue
            primitive_range = sorted((primitive.points[0][range_axis], primitive.points[-1][range_axis]))
            if not (primitive_range[0] - tolerance <= sample <= primitive_range[1] + tolerance):
                continue
            coordinate = sum(point[separation_axis] for point in primitive.points[:2]) / 2.0
            if coordinate < lower - tolerance or coordinate > upper + tolerance:
                return False
        return True

    @staticmethod
    def _collect_primitives(modelspace) -> List[CadPrimitive2D]:
        primitives: List[CadPrimitive2D] = []
        document = getattr(modelspace, "doc", None)
        for entity in modelspace:
            entity_type = entity.dxftype()
            if entity_type not in {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE"}:
                continue
            handle = str(getattr(entity.dxf, "handle", "") or "")
            layer = str(getattr(entity.dxf, "layer", "0") or "0")
            linetype = str(getattr(entity.dxf, "linetype", "BYLAYER") or "BYLAYER")
            if linetype.upper() == "BYLAYER" and document is not None:
                try:
                    linetype = str(document.layers.get(layer).dxf.linetype or linetype)
                except Exception:
                    pass
            try:
                if entity_type == "LINE":
                    points = [(float(entity.dxf.start.x), float(entity.dxf.start.y)), (float(entity.dxf.end.x), float(entity.dxf.end.y))]
                    primitives.append(CadPrimitive2D(entity_type, handle, layer, linetype=linetype, points=points))
                elif entity_type in {"CIRCLE", "ARC"}:
                    center = (float(entity.dxf.center.x), float(entity.dxf.center.y))
                    primitives.append(CadPrimitive2D(entity_type, handle, layer, linetype=linetype, center=center, radius=float(entity.dxf.radius)))
                elif entity_type == "LWPOLYLINE":
                    points = [(float(item[0]), float(item[1])) for item in entity.get_points("xy")]
                    if bool(getattr(entity, "closed", False)) and points:
                        points.append(points[0])
                    primitives.append(CadPrimitive2D(entity_type, handle, layer, linetype=linetype, points=points))
                else:
                    points = [(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices]
                    if bool(getattr(entity, "is_closed", False)) and points:
                        points.append(points[0])
                    primitives.append(CadPrimitive2D(entity_type, handle, layer, linetype=linetype, points=points))
            except (AttributeError, TypeError, ValueError):
                continue
        return primitives
