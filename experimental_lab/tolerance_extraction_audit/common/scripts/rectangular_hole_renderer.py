"""Renderer-neutral contracts for rectangular mounting-hole dimensions."""

from __future__ import annotations

import math
from typing import Any, Sequence


METHOD = "RECTANGULAR_HOLE_PATTERN_RENDERER_V1"


def _cluster(values: Sequence[float], tolerance: float) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(float(item) for item in values):
        if not groups or abs(value - sum(groups[-1]) / len(groups[-1])) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def build_rectangular_hole_render_tasks(
    feature: dict[str, Any],
    *,
    offset_ratio: float = 0.12,
) -> list[dict[str, Any]]:
    centers = [[float(value) for value in point[:2]] for point in feature.get("centers") or []]
    if len(centers) != 4:
        raise ValueError("Rectangular hole rendering requires exactly four centres")
    diameter = float(feature.get("diameter") or 0.0)
    if not math.isfinite(diameter) or diameter <= 0.0:
        raise ValueError("Hole diameter must be positive")
    span = max(
        max(point[0] for point in centers) - min(point[0] for point in centers),
        max(point[1] for point in centers) - min(point[1] for point in centers),
        diameter,
    )
    tolerance = max(span * 1e-5, 1e-6)
    xs = _cluster([point[0] for point in centers], tolerance)
    ys = _cluster([point[1] for point in centers], tolerance)
    if len(xs) != 2 or len(ys) != 2:
        raise ValueError("Hole centres do not form two X and two Y coordinate groups")
    left, right = min(xs), max(xs)
    bottom, top = min(ys), max(ys)
    width = right - left
    height = top - bottom
    expected_width = float(feature.get("horizontal_center_distance") or width)
    expected_height = float(feature.get("vertical_center_distance") or height)
    measurement_tolerance = max(0.001, span * 1e-5)
    if abs(width - expected_width) > measurement_tolerance:
        raise ValueError("Horizontal centre distance disagrees with centre coordinates")
    if abs(height - expected_height) > measurement_tolerance:
        raise ValueError("Vertical centre distance disagrees with centre coordinates")
    gap = max(span * float(offset_ratio), diameter * 2.0)
    top_left = [left, top]
    top_right = [right, top]
    bottom_right = [right, bottom]
    return [
        {
            "task_id": "hole_pattern.diameter",
            "renderer": "DIAMETER_RENDERER",
            "semantic": "mounting_hole_pattern.hole_diameter",
            "center": top_left,
            "radius": diameter / 2.0,
            "nominal_value": diameter,
            "label": f"4-%%c{diameter:.3f}",
        },
        {
            "task_id": "hole_pattern.horizontal_center_distance",
            "renderer": "LINEAR_DIMENSION_RENDERER",
            "semantic": "mounting_hole_pattern.horizontal_center_distance",
            "p1": top_left,
            "p2": top_right,
            "dimension_line": [[left, top + gap], [right, top + gap]],
            "nominal_value": width,
            "label": f"{width:.3f}",
            "orientation": "HORIZONTAL",
        },
        {
            "task_id": "hole_pattern.vertical_center_distance",
            "renderer": "LINEAR_DIMENSION_RENDERER",
            "semantic": "mounting_hole_pattern.vertical_center_distance",
            "p1": bottom_right,
            "p2": top_right,
            "dimension_line": [[right + gap, bottom], [right + gap, top]],
            "nominal_value": height,
            "label": f"{height:.3f}",
            "orientation": "VERTICAL",
        },
    ]
