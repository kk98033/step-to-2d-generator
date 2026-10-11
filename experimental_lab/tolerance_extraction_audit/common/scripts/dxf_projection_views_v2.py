"""Experimental DXF curve extraction and multi-scale drawing-view segmentation.

This module is intentionally isolated from the production tolerance pipeline.
It keeps curved CAD entities, expands INSERT transforms, removes known
annotation layers, and emits overlapping view candidates at several spatial
scales so one bad bridge line cannot force an entire sheet into one cluster.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import ezdxf
import numpy as np
from ezdxf import bbox as ezdxf_bbox


Point2D = Tuple[float, float]
BBox2D = Tuple[float, float, float, float]


ANNOTATION_LAYER_TOKENS = (
    "dim", "尺寸", "標注", "标注", "text", "note", "title", "border",
    "frame", "圖框", "图框", "rev", "ecn", "cen", "center", "中心",
)


@dataclass
class CurvePrimitive:
    primitive_id: str
    entity_type: str
    layer: str
    points: List[Point2D]

    @property
    def bbox(self) -> BBox2D:
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def center(self) -> Point2D:
        box = self.bbox
        return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

    @property
    def length(self) -> float:
        return sum(math.dist(left, right) for left, right in zip(self.points, self.points[1:]))

    @property
    def curved(self) -> bool:
        return self.entity_type in {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"}


@dataclass
class DxfViewCandidate:
    view_id: str
    source_path: str
    source: str
    primitive_indexes: List[int]
    points: List[Point2D]
    bbox: BBox2D
    primitive_count: int
    curved_primitive_count: int
    layer_count: int

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


class DxfProjectionViewExtractorV2:
    METHOD = "DXF_CURVES_MULTISCALE_V2"

    def __init__(self, path: str):
        self.path = str(Path(path).resolve())
        self.document = ezdxf.readfile(self.path)
        self.hatch_bboxes = self._collect_hatch_bboxes(self.document.modelspace())
        self.all_primitives = self._collect_primitives(self.document.modelspace())
        self.sheet_bbox = self._primitive_bounds(self.all_primitives)
        self.geometry_primitives = [
            primitive
            for primitive in self.all_primitives
            if not self._is_annotation_layer(primitive.layer)
        ]
        self.view_candidates = self._build_candidates()

    @staticmethod
    def _primitive_bounds(primitives: Sequence[CurvePrimitive]) -> BBox2D | None:
        if not primitives:
            return None
        boxes = [primitive.bbox for primitive in primitives]
        return (
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        )

    @staticmethod
    def _intersection_fraction(box: BBox2D, region: BBox2D) -> float:
        width = max(0.0, min(box[2], region[2]) - max(box[0], region[0]))
        height = max(0.0, min(box[3], region[3]) - max(box[1], region[1]))
        area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)
        return width * height / area

    def is_reserved_table_candidate(self, box: BBox2D) -> bool:
        """Reject title/revision tables in the three known sheet corners.

        Regions are relative to each drawing's own extents, so this remains
        independent of paper size, origin and DXF unit system.
        """
        if self.sheet_bbox is None:
            return False
        xmin, ymin, xmax, ymax = self.sheet_bbox
        width, height = xmax - xmin, ymax - ymin
        if width <= 1e-9 or height <= 1e-9:
            return False
        regions = (
            (xmin, ymin, xmin + 0.48 * width, ymin + 0.30 * height),
            (xmin + 0.48 * width, ymin, xmax, ymin + 0.32 * height),
            (xmin + 0.55 * width, ymin + 0.78 * height, xmax, ymax),
        )
        return any(self._intersection_fraction(box, region) >= 0.72 for region in regions)

    @staticmethod
    def _collect_hatch_bboxes(modelspace) -> List[BBox2D]:
        result = []
        for hatch in modelspace.query("HATCH"):
            try:
                bounds = ezdxf_bbox.extents([hatch])
                if not bounds.has_data:
                    continue
                result.append((
                    float(bounds.extmin.x), float(bounds.extmin.y),
                    float(bounds.extmax.x), float(bounds.extmax.y),
                ))
            except Exception:
                continue
        return result

    @classmethod
    def _is_annotation_layer(cls, layer: str) -> bool:
        lowered = str(layer or "").lower()
        return any(token in lowered for token in ANNOTATION_LAYER_TOKENS)

    def _collect_primitives(self, entities: Iterable, prefix: str = "", depth: int = 0) -> List[CurvePrimitive]:
        if depth > 8:
            return []
        result: List[CurvePrimitive] = []
        for position, entity in enumerate(entities):
            entity_type = entity.dxftype()
            handle = str(getattr(entity.dxf, "handle", "") or position)
            primitive_id = f"{prefix}{handle}"
            if entity_type == "INSERT":
                try:
                    result.extend(self._collect_primitives(
                        list(entity.virtual_entities()),
                        prefix=f"{primitive_id}/",
                        depth=depth + 1,
                    ))
                except Exception:
                    pass
                continue
            if entity_type not in {
                "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE",
                "LWPOLYLINE", "POLYLINE",
            }:
                continue
            points = self._entity_points(entity)
            if len(points) < 2:
                continue
            result.append(CurvePrimitive(
                primitive_id=primitive_id,
                entity_type=entity_type,
                layer=str(getattr(entity.dxf, "layer", "0") or "0"),
                points=self._reduce_path(points, 160),
            ))
        return result

    @staticmethod
    def _entity_points(entity) -> List[Point2D]:
        entity_type = entity.dxftype()
        try:
            if entity_type == "LINE":
                return [
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                ]
            if entity_type in {"ARC", "CIRCLE"}:
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                start = 0.0 if entity_type == "CIRCLE" else float(entity.dxf.start_angle)
                end = 360.0 if entity_type == "CIRCLE" else float(entity.dxf.end_angle)
                if end <= start:
                    end += 360.0
                span = end - start
                count = max(12, min(128, int(math.ceil(abs(math.radians(span) * radius) / 0.5))))
                return [
                    (
                        float(center.x) + radius * math.cos(math.radians(start + span * index / count)),
                        float(center.y) + radius * math.sin(math.radians(start + span * index / count)),
                    )
                    for index in range(count + 1)
                ]
            if entity_type in {"ELLIPSE", "SPLINE"}:
                return [(float(point.x), float(point.y)) for point in entity.flattening(0.05, segments=8)]
            if entity_type == "LWPOLYLINE":
                points: List[Point2D] = []
                for virtual in entity.virtual_entities():
                    segment = DxfProjectionViewExtractorV2._entity_points(virtual)
                    if points and segment:
                        segment = segment[1:]
                    points.extend(segment)
                return points
            if entity_type == "POLYLINE":
                return [
                    (float(vertex.dxf.location.x), float(vertex.dxf.location.y))
                    for vertex in entity.vertices
                ]
        except Exception:
            return []
        return []

    @staticmethod
    def _reduce_path(points: Sequence[Point2D], limit: int) -> List[Point2D]:
        if len(points) <= limit:
            return [(float(x), float(y)) for x, y in points]
        indexes = np.linspace(0, len(points) - 1, limit).round().astype(int)
        return [(float(points[index][0]), float(points[index][1])) for index in indexes]

    def _build_candidates(self) -> List[DxfViewCandidate]:
        primitives = self.geometry_primitives
        if not primitives:
            return []
        lengths = [primitive.length for primitive in primitives if primitive.length > 1e-6]
        typical = statistics.median(lengths) if lengths else 1.0
        base = max(0.75, min(8.0, typical * 0.75))
        groups = []
        for multiplier in (0.6, 1.0, 1.7):
            groups.extend((f"path_grid_{multiplier:.1f}", group) for group in self._path_grid_groups(base * multiplier))
            groups.extend((f"center_grid_{multiplier:.1f}", group) for group in self._center_grid_groups(base * multiplier * 2.0))

        by_layer = {}
        for index, primitive in enumerate(primitives):
            by_layer.setdefault(primitive.layer, []).append(index)
        for layer, indexes in by_layer.items():
            if len(indexes) >= 3:
                groups.append((f"layer:{layer}", indexes))

        candidates: List[DxfViewCandidate] = []
        seen = set()
        for source, indexes in groups:
            indexes = sorted(set(indexes))
            if len(indexes) < 2:
                continue
            box = self._bbox(indexes)
            if self.is_reserved_table_candidate(box):
                continue
            width, height = box[2] - box[0], box[3] - box[1]
            if min(width, height) < 0.35 or max(width, height) < 1.0:
                continue
            points = self._sample_group(indexes, 320)
            if len(points) < 12:
                continue
            signature = tuple(round(value, 2) for value in box) + (len(indexes),)
            if signature in seen:
                continue
            seen.add(signature)
            items = [primitives[index] for index in indexes]
            candidates.append(DxfViewCandidate(
                view_id=f"view_v2_{len(candidates) + 1:03d}",
                source_path=self.path,
                source=source,
                primitive_indexes=indexes,
                points=points,
                bbox=box,
                primitive_count=len(indexes),
                curved_primitive_count=sum(item.curved for item in items),
                layer_count=len({item.layer for item in items}),
            ))
        return candidates

    def _path_grid_groups(self, cell_size: float) -> List[List[int]]:
        buckets = {}
        for index, primitive in enumerate(self.geometry_primitives):
            for cell in self._path_cells(primitive.points, cell_size):
                buckets.setdefault(cell, set()).add(index)
        return self._connected_bucket_groups(buckets)

    def _center_grid_groups(self, cell_size: float) -> List[List[int]]:
        buckets = {}
        for index, primitive in enumerate(self.geometry_primitives):
            center = primitive.center
            cell = (math.floor(center[0] / cell_size), math.floor(center[1] / cell_size))
            buckets.setdefault(cell, set()).add(index)
        return self._connected_bucket_groups(buckets)

    @staticmethod
    def _path_cells(points: Sequence[Point2D], cell_size: float):
        cells = set()
        for left, right in zip(points, points[1:]):
            length = math.dist(left, right)
            count = max(1, min(96, int(math.ceil(length / cell_size))))
            for step in range(count + 1):
                x = left[0] + (right[0] - left[0]) * step / count
                y = left[1] + (right[1] - left[1]) * step / count
                cells.add((math.floor(x / cell_size), math.floor(y / cell_size)))
        return cells

    @staticmethod
    def _connected_bucket_groups(buckets) -> List[List[int]]:
        unvisited = set(buckets)
        groups = []
        while unvisited:
            start = unvisited.pop()
            stack = [start]
            indexes = set()
            while stack:
                cell = stack.pop()
                indexes.update(buckets[cell])
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        neighbor = (cell[0] + dx, cell[1] + dy)
                        if neighbor in unvisited:
                            unvisited.remove(neighbor)
                            stack.append(neighbor)
            groups.append(sorted(indexes))
        return groups

    def _bbox(self, indexes: Sequence[int]) -> BBox2D:
        boxes = [self.geometry_primitives[index].bbox for index in indexes]
        return (
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        )

    def _sample_group(self, indexes: Sequence[int], limit: int) -> List[Point2D]:
        points = np.asarray([
            point
            for index in indexes
            for point in self.geometry_primitives[index].points
        ], dtype=float)
        points = np.unique(np.round(points, 6), axis=0)
        if len(points) > limit:
            center = np.mean(points, axis=0)
            selected = [int(np.argmax(np.sum((points - center) ** 2, axis=1)))]
            minimum = np.sum((points - points[selected[0]]) ** 2, axis=1)
            while len(selected) < limit:
                chosen = int(np.argmax(minimum))
                selected.append(chosen)
                minimum = np.minimum(minimum, np.sum((points - points[chosen]) ** 2, axis=1))
            points = points[np.asarray(selected)]
        return [(float(point[0]), float(point[1])) for point in points]

    @staticmethod
    def candidate_id(path: str, view_id: str) -> str:
        digest = hashlib.sha1(str(Path(path).resolve()).lower().encode("utf-8")).hexdigest()[:12]
        return f"{digest}:{view_id}"
