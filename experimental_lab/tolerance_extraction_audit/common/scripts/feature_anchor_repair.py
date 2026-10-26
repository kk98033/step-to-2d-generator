"""Topology-aware feature anchors used by drawing-rule experiments.

This module intentionally lives outside the production extractor.  It repairs
candidate-rule view geometry from the primitive evidence already produced by
``FeatureExtractor``.  No part number or fixed model coordinate is used.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Iterable, Sequence

from auto_2d_drawing.drawing_projection import diameter_endpoints, linear_endpoints, project_point, view_basis


VIEWS = ("front", "top", "right", "left", "back")


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def _normalise(vector: Sequence[float]) -> tuple[float, float, float]:
    length = math.sqrt(_dot(vector, vector))
    if length <= 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(float(value) / length for value in vector[:3])  # type: ignore[return-value]


def _primitive_bbox(
    center: Sequence[float],
    axis: Sequence[float],
    length: float,
    radius: float,
    view_name: str,
) -> list[float]:
    """Exact orthographic envelope of a finite cylinder/cone outer bound."""
    basis = view_basis(view_name)
    direction = _normalise(axis)
    cx, cy = project_point(center, view_name)
    axial_x = abs(_dot(direction, basis["right"])) * max(float(length), 0.0) / 2.0
    axial_y = abs(_dot(direction, basis["up"])) * max(float(length), 0.0) / 2.0
    radial_x = max(float(radius), 0.0) * math.sqrt(max(0.0, 1.0 - _dot(direction, basis["right"]) ** 2))
    radial_y = max(float(radius), 0.0) * math.sqrt(max(0.0, 1.0 - _dot(direction, basis["up"]) ** 2))
    return [cx - axial_x - radial_x, cy - axial_y - radial_y, cx + axial_x + radial_x, cy + axial_y + radial_y]


def _points_bbox(points: Iterable[Sequence[float]], view_name: str, margin: float = 0.0) -> list[float] | None:
    projected = [project_point(point, view_name) for point in points]
    if not projected:
        return None
    xs = [point[0] for point in projected]
    ys = [point[1] for point in projected]
    return [min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin]


def _union(boxes: Iterable[Sequence[float]]) -> list[float] | None:
    valid = [list(map(float, box)) for box in boxes if len(box) == 4]
    if not valid:
        return None
    return [min(box[0] for box in valid), min(box[1] for box in valid), max(box[2] for box in valid), max(box[3] for box in valid)]


def _linear_endpoints_for_nominal(footprint: dict[str, Any], nominal: float) -> dict[str, Any]:
    """Select the projected axis whose span actually represents the nominal."""
    x0, y0, x1, y1 = [float(value) for value in footprint["bbox"]]
    cx, cy = [float(value) for value in footprint["center"]]
    x_span, y_span = abs(x1 - x0), abs(y1 - y0)
    if nominal > 1e-9:
        axis = "x" if abs(x_span - nominal) <= abs(y_span - nominal) else "y"
    else:
        axis = "x" if x_span >= y_span else "y"
    if axis == "x":
        return {"start_proj": [x0, cy], "end_proj": [x1, cy], "axis": axis}
    return {"start_proj": [cx, y0], "end_proj": [cx, y1], "axis": axis}


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((float(left[i]) - float(right[i])) ** 2 for i in range(3)))


def _nominal(record: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = (record.get("nominal") or {}).get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _match_primitive(record: dict[str, Any], primitives: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    geometry = record.get("geometry") or {}
    center = geometry.get("center") or (0.0, 0.0, 0.0)
    diameter = _nominal(record, "diameter", float(geometry.get("diameter") or 0.0))
    length = _nominal(record, "length", float(geometry.get("length") or 0.0))
    candidates = []
    for primitive in primitives:
        pd = float(primitive.get("diameter") or 0.0)
        pl = float(primitive.get("length") or 0.0)
        pc = primitive.get("center") or center
        scale = max(diameter, length, pd, pl, 1.0)
        score = abs(pd - diameter) / scale + 0.35 * abs(pl - length) / scale + 0.05 * _distance(pc, center) / scale
        candidates.append((score, primitive))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _step_primitive(record: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any] | None:
    diameter = _nominal(record, "diameter")
    length = _nominal(record, "length")
    position = _nominal(record, "position")
    candidates = []
    for primitive in summary.get("cylinders_raw") or []:
        scale = max(diameter, length, 1.0)
        score = (
            abs(float(primitive.get("diameter") or 0.0) - diameter) / scale
            + 0.4 * abs(float(primitive.get("length") or 0.0) - length) / scale
            + 0.1 * abs(float(primitive.get("position") or position) - position) / scale
        )
        candidates.append((score, primitive))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _bbox_for_record(record: dict[str, Any], summary: dict[str, Any], view_name: str) -> tuple[list[float] | None, str, str]:
    geometry = record.get("geometry") or {}
    kind = str(geometry.get("kind") or "").lower()
    source = str((record.get("source") or {}).get("extractor") or "")
    main_axis = str(summary.get("main_axis") or "Z").upper()
    axes = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}

    if kind in {"cylinder", "groove", "axial_step"}:
        record_type = str(record.get("type") or "").lower()
        role = str(record.get("role") or "").lower()
        if record_type == "hole" or "bore" in role or "hole" in role:
            pools = list(summary.get("holes") or [])
        elif record_type == "shaft_or_boss" or "outer" in role or "boss" in role:
            pools = list(summary.get("shafts") or [])
        else:
            pools = list(summary.get("holes") or []) + list(summary.get("shafts") or [])
        primitive = _match_primitive(record, pools)
        if kind == "axial_step":
            primitive = _step_primitive(record, summary) or primitive
        if primitive:
            bbox = _primitive_bbox(
                primitive.get("center") or geometry.get("center") or (0, 0, 0),
                primitive.get("axis_dir") or axes.get(main_axis, axes["Z"]),
                float(primitive.get("length") or _nominal(record, "length") or 0.0),
                float(primitive.get("radius") or primitive.get("diameter", 0.0) / 2.0),
                view_name,
            )
            relation = "INNER_MATERIAL_BOUNDARY" if bool(primitive.get("is_hole")) or "hole" in source.lower() else "OUTER_MATERIAL_BOUNDARY"
            return bbox, "MATCHED_BREP_PRIMITIVE", relation

    if kind == "cone":
        primitive = _match_primitive(record, summary.get("cones") or [])
        if primitive:
            return _primitive_bbox(
                primitive.get("center") or (0, 0, 0),
                primitive.get("axis_dir") or axes.get(main_axis, axes["Z"]),
                float(primitive.get("height") or 0.0),
                float(primitive.get("max_radius") or primitive.get("max_diameter", 0.0) / 2.0),
                view_name,
            ), "MATCHED_BREP_PRIMITIVE", "INNER_MATERIAL_BOUNDARY" if primitive.get("is_hole") else "OUTER_MATERIAL_BOUNDARY"

    if kind == "fillet":
        primitive = _match_primitive(record, summary.get("fillets") or [])
        if primitive:
            points = [primitive[key] for key in ("start_point", "mid_point", "end_point") if primitive.get(key)]
            bbox = _points_bbox(points, view_name, max(float(primitive.get("radius") or 0.0) * 0.12, 0.02))
            return bbox, "MATCHED_BREP_EDGE", "EDGE_TRANSITION"

    if kind == "circle_pattern":
        diameter = _nominal(record, "diameter", float(geometry.get("hole_diameter") or 0.0))
        pcd = _nominal(record, "pcd", float(geometry.get("pcd") or 0.0))
        patterns = summary.get("hole_patterns") or []
        matched = min(patterns, key=lambda item: abs(float(item.get("pcd") or 0.0) - pcd) + abs(float(item.get("hole_diameter") or 0.0) - diameter), default=None)
        if matched:
            boxes = [
                _primitive_bbox(
                    hole.get("center") or (0, 0, 0),
                    hole.get("axis_dir") or axes.get(main_axis, axes["Z"]),
                    float(hole.get("length") or 0.0),
                    float(hole.get("radius") or hole.get("diameter", 0.0) / 2.0),
                    view_name,
                )
                for hole in matched.get("holes") or []
            ]
            return _union(boxes), "BREP_PATTERN_MEMBER_UNION", "INNER_MATERIAL_BOUNDARY"

    if kind == "thickness":
        bounds = summary.get("bounds") or (0, 0, 0, 0, 0, 0)
        axis_name = str(geometry.get("axis") or (record.get("nominal") or {}).get("axis") or main_axis).upper()
        pos1, pos2 = float(geometry.get("pos1") or 0.0), float(geometry.get("pos2") or 0.0)
        centre = [(float(bounds[i]) + float(bounds[i + 3])) / 2.0 for i in range(3)]
        size = [float(bounds[i + 3]) - float(bounds[i]) for i in range(3)]
        axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis_name, 2)
        centre[axis_index] = (pos1 + pos2) / 2.0
        size[axis_index] = abs(pos2 - pos1)
        from auto_2d_drawing.drawing_projection import project_bbox
        return list(project_bbox(centre, size, view_name)), "PAIRED_PLANAR_FACE_SLAB", "MATERIAL_THICKNESS"

    return None, "NO_TOPOLOGY_MATCH", "UNKNOWN"


def repair_rules(
    rules: Sequence[dict[str, Any]],
    records: Sequence[dict[str, Any]],
    feature_extractor: Any,
    view_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return copied rules and an audit entry for every attempted repair."""
    repaired = copy.deepcopy(list(rules))
    records_by_id = {str(record.get("id")): record for record in records}
    summary = dict(feature_extractor.summary())
    summary["cylinders_raw"] = list(getattr(feature_extractor, "cylinders_raw", []) or [])
    audit: dict[str, dict[str, Any]] = {}
    for rule in repaired:
        feature_id = str(rule.get("rule_id") or rule.get("id") or "")
        record = records_by_id.get(feature_id)
        canonical_feature_id = feature_id
        if not record:
            # Candidate rules commonly split a canonical primitive into a
            # diameter rule and an axial length/width rule.  Resolve that
            # declared parent identity before attempting geometric matching.
            for suffix in ("_len", "_width", "_depth", "_height"):
                if feature_id.endswith(suffix) and feature_id[:-len(suffix)] in records_by_id:
                    canonical_feature_id = feature_id[:-len(suffix)]
                    record = records_by_id[canonical_feature_id]
                    break
        if not record:
            audit[feature_id] = {"status": "SKIPPED_NO_CANONICAL_RECORD"}
            continue
        repaired_views: dict[str, Any] = {}
        methods: set[str] = set()
        relations: set[str] = set()
        for view_name in VIEWS:
            if view_name not in view_data:
                continue
            bbox, method, relation = _bbox_for_record(record, summary, view_name)
            if not bbox:
                continue
            payload = dict((rule.get("view_geometry") or {}).get(view_name) or {})
            center = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
            footprint = {"bbox": bbox, "center": center}
            if str(rule.get("dim_type") or "").upper() == "DIAMETER":
                payload.update(diameter_endpoints(footprint))
            elif str(rule.get("dim_type") or "").upper() == "LINEAR":
                payload.update(_linear_endpoints_for_nominal(footprint, float(rule.get("nominal_value") or 0.0)))
            elif str(rule.get("dim_type") or "").upper() == "CENTERLINES":
                payload.update(linear_endpoints(footprint))
            else:
                payload["center"] = center
                payload.setdefault("start_proj", [bbox[2], bbox[3]])
            payload["projected_feature_bbox"] = [round(float(value), 4) for value in bbox]
            payload["anchor_method"] = method
            payload["material_relation"] = relation
            repaired_views[view_name] = payload
            methods.add(method)
            relations.add(relation)
        if repaired_views:
            before = copy.deepcopy(rule.get("view_geometry") or {})
            rule["view_geometry"] = {**before, **repaired_views}
            if str(rule.get("dim_type") or "").upper() == "DIAMETER":
                nominal = abs(float(rule.get("nominal_value") or 0.0))
                def diameter_view_quality(item: tuple[str, dict[str, Any]]) -> tuple[float, float, int]:
                    view_name, payload = item
                    bbox = payload["projected_feature_bbox"]
                    width, height = abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])
                    span = min(width, height)
                    nominal_error = abs(span - nominal) / max(nominal, 1e-9)
                    circularity_error = abs(width - height) / max(width, height, 1e-9)
                    return nominal_error, circularity_error, VIEWS.index(view_name)
                best_view = min(repaired_views.items(), key=diameter_view_quality)[0]
                rule["preferred_view"] = best_view
                rule["target_views"] = [best_view]
                rule["views"] = [best_view]
            elif str(rule.get("dim_type") or "").upper() == "LINEAR":
                nominal = abs(float(rule.get("nominal_value") or 0.0))
                def linear_view_quality(item: tuple[str, dict[str, Any]]) -> tuple[float, int]:
                    view_name, payload = item
                    start, end = payload.get("start_proj") or (), payload.get("end_proj") or ()
                    if len(start) != 2 or len(end) != 2:
                        return (float("inf"), VIEWS.index(view_name))
                    span = math.dist(start, end)
                    return (abs(span - nominal) / max(nominal, 1e-9), VIEWS.index(view_name))
                best_view = min(repaired_views.items(), key=linear_view_quality)[0]
                rule["preferred_view"] = best_view
                rule["target_views"] = [best_view]
                rule["views"] = [best_view]
            preferred = str(rule.get("preferred_view") or "")
            if preferred in repaired_views:
                rule["geometry_payload"] = repaired_views[preferred]
            audit[feature_id] = {
                "status": "REPAIRED",
                "canonical_feature_id": canonical_feature_id,
                "methods": sorted(methods),
                "material_relations": sorted(relations),
                "view_count": len(repaired_views),
            }
        else:
            audit[feature_id] = {"status": "SKIPPED_NO_TOPOLOGY_MATCH"}
    return repaired, audit
