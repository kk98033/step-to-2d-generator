"""Explainable 2D DXF feature inference.

This module classifies a dimension into the same taxonomy used by
``FeatureGraphExtractor``.  It deliberately does not claim 3D identity: a
high-confidence result means that the 2D evidence strongly indicates a
feature *type*, not that a particular STEP face has been located.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from auto_2d_drawing.tolerance.feature_graph import (
    CANONICAL_FEATURE_TYPES,
    candidate_feature_types_for_dimension,
)
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer


Point2D = Tuple[float, float]


@dataclass
class _GeometryPrimitive:
    entity_type: str
    handle: str
    layer: str
    points: List[Point2D] = field(default_factory=list)
    center: Optional[Point2D] = None
    radius: float = 0.0


@dataclass
class _Candidate:
    feature_type: str
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)

    def raise_to(self, confidence: float, reason: str) -> None:
        self.confidence = max(self.confidence, min(0.99, confidence))
        if reason not in self.evidence:
            self.evidence.append(reason)


class FeatureInference2DEngine:
    """Infer FeatureGraph-compatible feature types from structured DXF data."""

    METHOD = "DXF_2D_RULES_V1"
    AUTO_THRESHOLD = 0.90
    REVIEW_THRESHOLD = 0.65
    MIN_MARGIN = 0.12
    HIDDEN_LAYER_TOKENS = ("hidden", "hid", "dash", "隱藏", "隐藏")

    def __init__(self, modelspace=None):
        self.structure_analyzer = DxfStructure2DAnalyzer(modelspace) if modelspace is not None else None
        self.primitives = self.structure_analyzer.primitives if self.structure_analyzer is not None else []

    def infer(self, dimension) -> Dict[str, Any]:
        category = str(getattr(dimension, "dimension_category", "UNKNOWN") or "UNKNOWN").upper()
        candidates = {
            name: _Candidate(name, 0.20, [f"{category} 尺寸允許此 FeatureGraph 類型"])
            for name in candidate_feature_types_for_dimension(category)
        }
        raw_text = str(getattr(dimension, "raw_text", "") or "")
        clean_text = self._clean_text(raw_text)
        tolerance = dict(getattr(dimension, "tolerance_config", {}) or {})

        self._apply_dimension_semantics(category, clean_text, tolerance, candidates)
        geometry = self._observe_geometry(dimension)
        self._apply_geometry_evidence(category, dimension, geometry, candidates)
        structure = self.structure_analyzer.analyze_dimension(dimension) if self.structure_analyzer is not None else {}
        self._apply_structure_evidence(category, structure, candidates)

        # Python's sort is stable, so equal scores retain the canonical
        # candidate order instead of gaining a misleading alphabetical winner.
        ranked = sorted(candidates.values(), key=lambda item: -item.confidence)
        top = ranked[0] if ranked else None
        runner_up = ranked[1].confidence if len(ranked) > 1 else 0.0
        margin = (top.confidence - runner_up) if top else 0.0

        feature_type: Optional[str] = None
        status = "UNRESOLVED"
        if top and top.confidence >= self.AUTO_THRESHOLD and margin >= self.MIN_MARGIN:
            feature_type = top.feature_type
            status = "AUTO_INFERRED_2D"
        elif top and top.confidence >= self.REVIEW_THRESHOLD:
            status = "REVIEW_CANDIDATE"

        if feature_type is not None and feature_type not in CANONICAL_FEATURE_TYPES:
            raise ValueError(f"2D inference emitted a non-canonical feature type: {feature_type}")

        return {
            "method": self.METHOD,
            "taxonomy": "FeatureGraphExtractor",
            "status": status,
            "feature_type": feature_type,
            "confidence": round(top.confidence if top else 0.0, 3),
            "margin": round(margin, 3),
            "candidates": [
                {
                    "feature_type": item.feature_type,
                    "confidence": round(item.confidence, 3),
                    "evidence": item.evidence,
                }
                for item in ranked
            ],
            "geometry_context": geometry,
            "structure_context": structure,
            "feature_identity_verified": False,
            "retrieval_eligible": False,
        }

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"\\[A-Za-z0-9_]+(?:;|\s)?", " ", text)
        return text.replace("{", " ").replace("}", " ").strip().lower()

    @staticmethod
    def _candidate(candidates: Dict[str, _Candidate], feature_type: str) -> Optional[_Candidate]:
        return candidates.get(feature_type)

    def _apply_dimension_semantics(
        self,
        category: str,
        text: str,
        tolerance: Dict[str, Any],
        candidates: Dict[str, _Candidate],
    ) -> None:
        if category == "RADIUS":
            candidate = self._candidate(candidates, "transition_fillet")
            if candidate:
                candidate.raise_to(0.78, "R/半徑尺寸指向圓弧；仍需相切關係才能確認為過渡圓角")
        elif category == "CHAMFER":
            candidate = self._candidate(candidates, "pilot_chamfer")
            if candidate:
                candidate.raise_to(0.96, "C 倒角尺寸直接指向倒角特徵")
        elif category == "ANGULAR":
            candidate = self._candidate(candidates, "pilot_chamfer")
            if candidate:
                candidate.raise_to(0.72, "角度尺寸可能屬於倒角，但單獨角度不足以核實")

        if tolerance.get("mode") == "FIT":
            fit_class = str(tolerance.get("fit_class", "") or "")
            if fit_class:
                feature_type = "hole" if fit_class[0].isupper() else "shaft_segment"
                candidate = self._candidate(candidates, feature_type)
                if candidate:
                    kind = "孔" if feature_type == "hole" else "軸"
                    candidate.raise_to(0.98, f"ISO 配合代號 {fit_class} 的字母大小寫指向{kind}公差帶")

        token_rules = (
            ("retaining_ring_groove", ("卡簧", "扣環", "止動環", "circlip", "snap ring", "retaining ring", "groove"), 0.94),
            ("locating_shoulder", ("軸肩", "台階", "段差", "shoulder", "step"), 0.92),
            ("pilot_chamfer", ("倒角", "chamfer"), 0.94),
            ("transition_fillet", ("圓角", "圆角", "fillet"), 0.94),
            ("hole", ("孔深", "深度", "depth", "deep"), 0.84),
        )
        for feature_type, tokens, confidence in token_rules:
            if any(token in text for token in tokens):
                candidate = self._candidate(candidates, feature_type)
                if candidate:
                    candidate.raise_to(confidence, f"尺寸文字包含 {feature_type} 專屬語意")

    def _apply_geometry_evidence(
        self,
        category: str,
        dimension,
        geometry: Dict[str, Any],
        candidates: Dict[str, _Candidate],
    ) -> None:
        hidden_count = int(geometry.get("nearby_hidden_entities", 0))
        matching_circles = geometry.get("matching_circles", [])

        if category == "DIAMETER":
            hole = self._candidate(candidates, "hole")
            shaft = self._candidate(candidates, "shaft_segment")
            if any(item.get("has_larger_concentric_circle") for item in matching_circles):
                if hole:
                    hole.raise_to(0.72, "同心封閉輪廓可能是內孔，但也可能是階梯軸端視圖")
                if shaft:
                    shaft.raise_to(0.72, "同心封閉輪廓也可能是外部階梯軸，單一端視圖無法判定內外")
            elif matching_circles:
                if shaft:
                    shaft.raise_to(0.68, "找到相符直徑的封閉圓，但無外部輪廓可證明內外關係")
                if hole:
                    hole.raise_to(0.58, "封閉圓也可能是孔，需側視圖或配合代號確認")
            if hidden_count >= 2 and hole:
                hole.raise_to(0.86, "尺寸端點附近有成對隱藏線，較符合內孔投影")
            elif geometry.get("nearby_visible_lines", 0) >= 2 and shaft:
                shaft.raise_to(0.66, "尺寸端點附近有可見外輪廓線，但仍可能是剖視內孔")

        if category == "LINEAR":
            hole = self._candidate(candidates, "hole")
            if hidden_count >= 2 and hole:
                hole.raise_to(0.68, "線性尺寸兩端鄰近隱藏輪廓，可能是孔深或內部段長")

    def _apply_structure_evidence(
        self,
        category: str,
        structure: Dict[str, Any],
        candidates: Dict[str, _Candidate],
    ) -> None:
        if category != "DIAMETER" or not structure:
            return
        cross_view = structure.get("cross_view_evidence", {})
        visible_pairs = cross_view.get("matching_visible_pairs", [])
        outer_visible_pairs = [item for item in visible_pairs if item.get("outer_silhouette")]
        hidden_pairs = cross_view.get("matching_hidden_pairs", [])
        shaft = self._candidate(candidates, "shaft_segment")
        hole = self._candidate(candidates, "hole")
        association_status = structure.get("association_status")
        attachment_is_reliable = association_status in {"NATIVE_ASSOCIATIVE", "GEOMETRIC_ATTACHMENT"}

        if attachment_is_reliable and outer_visible_pairs and not hidden_pairs and shaft:
            shaft.raise_to(0.93, "尺寸已連到圓輪廓，對齊的另一視圖存在相同直徑可見線對")
        elif attachment_is_reliable and hidden_pairs and not visible_pairs and hole:
            hole.raise_to(0.93, "尺寸已連到圓輪廓，對齊的另一視圖存在相同直徑隱藏線對")
        elif visible_pairs and hidden_pairs:
            if shaft:
                shaft.raise_to(0.76, "跨視圖同時存在可見與隱藏線對，可能有重疊特徵")
            if hole:
                hole.raise_to(0.76, "跨視圖同時存在可見與隱藏線對，無法唯一判定內外")
        elif visible_pairs and not outer_visible_pairs:
            if shaft:
                shaft.raise_to(0.72, "跨視圖找到可見線對，但它不是局部最外輪廓，可能來自剖視內部")
            if hole:
                hole.raise_to(0.72, "內側可見線對可能是剖視孔，也可能是其他內部結構")

    def _observe_geometry(self, dimension) -> Dict[str, Any]:
        reference_points = self._dimension_reference_points(dimension)
        nominal = abs(float(getattr(dimension, "nominal_value", 0.0) or 0.0))
        span = self._reference_span(reference_points)
        proximity = max(0.25, min(2.0, max(span, nominal, 1.0) * 0.04))
        diameter_tolerance = max(0.02, nominal * 0.002)

        nearby: List[_GeometryPrimitive] = []
        matching_circles: List[_GeometryPrimitive] = []
        for primitive in self.primitives:
            if reference_points and min(self._distance_to_primitive(point, primitive) for point in reference_points) <= proximity:
                nearby.append(primitive)
            if (
                primitive.entity_type == "CIRCLE"
                and nominal > 0.0
                and abs(primitive.radius * 2.0 - nominal) <= diameter_tolerance
                and (
                    not reference_points
                    or min(self._distance_to_primitive(point, primitive) for point in reference_points) <= proximity
                )
            ):
                matching_circles.append(primitive)

        circle_payload = []
        for circle in matching_circles:
            has_larger = any(
                other.entity_type == "CIRCLE"
                and other.center is not None
                and circle.center is not None
                and other.radius > circle.radius + diameter_tolerance
                and math.dist(other.center, circle.center) <= proximity
                for other in self.primitives
            )
            circle_payload.append({
                "handle": circle.handle,
                "diameter": round(circle.radius * 2.0, 4),
                "has_larger_concentric_circle": has_larger,
            })

        hidden = [item for item in nearby if self._is_hidden_layer(item.layer, getattr(item, "linetype", ""))]
        visible_lines = [item for item in nearby if item.entity_type in {"LINE", "LWPOLYLINE", "POLYLINE"} and not self._is_hidden_layer(item.layer, getattr(item, "linetype", ""))]
        return {
            "reference_points": [[round(x, 3), round(y, 3)] for x, y in reference_points],
            "search_radius": round(proximity, 4),
            "nearby_entity_handles": [item.handle for item in nearby[:24] if item.handle],
            "nearby_entity_types": sorted({item.entity_type for item in nearby}),
            "nearby_hidden_entities": len(hidden),
            "nearby_visible_lines": len(visible_lines),
            "matching_circles": circle_payload,
        }

    @staticmethod
    def _dimension_reference_points(dimension) -> List[Point2D]:
        points = dict(getattr(dimension, "points", {}) or {})
        category = str(getattr(dimension, "dimension_category", "") or "").upper()
        if category in {"DIAMETER", "RADIUS"}:
            keys = ("defpoint", "defpoint4")
        elif category == "ANGULAR":
            keys = ("defpoint2", "defpoint3", "defpoint4")
        else:
            keys = ("defpoint2", "defpoint3")
        result: List[Point2D] = []
        for key in keys:
            value = points.get(key)
            if not isinstance(value, Sequence) or len(value) < 2:
                continue
            point = (float(value[0]), float(value[1]))
            if point not in result:
                result.append(point)
        if not result and isinstance(points.get("defpoint"), Sequence):
            value = points["defpoint"]
            if len(value) >= 2:
                result.append((float(value[0]), float(value[1])))
        return result

    @staticmethod
    def _reference_span(points: Sequence[Point2D]) -> float:
        if len(points) < 2:
            return 0.0
        return max(math.dist(left, right) for index, left in enumerate(points) for right in points[index + 1:])

    def _is_hidden_layer(self, layer: str, linetype: str = "") -> bool:
        lowered = f"{layer or ''} {linetype or ''}".lower()
        return any(token in lowered for token in self.HIDDEN_LAYER_TOKENS)

    @classmethod
    def _distance_to_primitive(cls, point: Point2D, primitive: _GeometryPrimitive) -> float:
        if primitive.center is not None and primitive.radius > 0.0:
            return abs(math.dist(point, primitive.center) - primitive.radius)
        if len(primitive.points) == 1:
            return math.dist(point, primitive.points[0])
        if len(primitive.points) >= 2:
            return min(
                cls._distance_to_segment(point, left, right)
                for left, right in zip(primitive.points, primitive.points[1:])
            )
        return float("inf")

    @staticmethod
    def _distance_to_segment(point: Point2D, start: Point2D, end: Point2D) -> float:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            return math.dist(point, start)
        ratio = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
        projection = (start[0] + ratio * dx, start[1] + ratio * dy)
        return math.dist(point, projection)

    @staticmethod
    def _collect_primitives(modelspace) -> List[_GeometryPrimitive]:
        primitives: List[_GeometryPrimitive] = []
        for entity in modelspace:
            entity_type = entity.dxftype()
            if entity_type not in {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE"}:
                continue
            handle = str(getattr(entity.dxf, "handle", "") or "")
            layer = str(getattr(entity.dxf, "layer", "0") or "0")
            try:
                if entity_type == "LINE":
                    points = [
                        (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                        (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                    ]
                    primitives.append(_GeometryPrimitive(entity_type, handle, layer, points=points))
                elif entity_type in {"CIRCLE", "ARC"}:
                    center = (float(entity.dxf.center.x), float(entity.dxf.center.y))
                    primitives.append(_GeometryPrimitive(entity_type, handle, layer, center=center, radius=float(entity.dxf.radius)))
                elif entity_type == "LWPOLYLINE":
                    points = [(float(item[0]), float(item[1])) for item in entity.get_points("xy")]
                    if bool(getattr(entity, "closed", False)) and points:
                        points.append(points[0])
                    primitives.append(_GeometryPrimitive(entity_type, handle, layer, points=points))
                elif entity_type == "POLYLINE":
                    points = [(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices]
                    if bool(getattr(entity, "is_closed", False)) and points:
                        points.append(points[0])
                    primitives.append(_GeometryPrimitive(entity_type, handle, layer, points=points))
            except (AttributeError, TypeError, ValueError):
                continue
        return primitives
