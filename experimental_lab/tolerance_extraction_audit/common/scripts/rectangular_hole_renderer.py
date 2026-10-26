"""Renderer-neutral contracts for rectangular mounting-hole dimensions."""

from __future__ import annotations

import math
from typing import Any, Sequence


METHOD = "RECTANGULAR_HOLE_PATTERN_RENDERER_V1"


def rectangular_hole_frame(centers: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Validate four centres as a rectangle and return its local edge frame.

    The old renderer clustered global X/Y coordinates, which rejected valid
    rotated patterns and disagreed with the detector's looser bbox test.
    """
    points = [[float(value) for value in point[:2]] for point in centers]
    if len(points) != 4:
        raise ValueError("Rectangular hole rendering requires exactly four centres")
    center = [sum(point[index] for point in points) / 4.0 for index in range(2)]
    ordered = sorted(points, key=lambda point: math.atan2(point[1] - center[1], point[0] - center[0]))
    edges = [
        [ordered[(index + 1) % 4][axis] - ordered[index][axis] for axis in range(2)]
        for index in range(4)
    ]
    lengths = [math.hypot(*edge) for edge in edges]
    span = max(lengths, default=0.0)
    if min(lengths, default=0.0) <= max(span * 1e-6, 1e-9):
        raise ValueError("Rectangular hole centres contain a degenerate edge")
    relative_tolerance = 0.01
    opposite_a_error = abs(lengths[0] - lengths[2]) / max(lengths[0], lengths[2])
    opposite_b_error = abs(lengths[1] - lengths[3]) / max(lengths[1], lengths[3])
    if opposite_a_error > relative_tolerance:
        raise ValueError("Opposite hole-centre edges have inconsistent lengths")
    if opposite_b_error > relative_tolerance:
        raise ValueError("Opposite hole-centre edges have inconsistent lengths")
    perpendicular_errors = []
    for index in range(4):
        left, right = edges[index - 1], edges[index]
        cosine = abs((left[0] * right[0] + left[1] * right[1]) / (lengths[index - 1] * lengths[index]))
        perpendicular_errors.append(cosine)
        if cosine > 0.02:
            raise ValueError("Adjacent hole-centre edges are not perpendicular")

    pair_a = [(ordered[0], ordered[1]), (ordered[2], ordered[3])]
    pair_b = [(ordered[1], ordered[2]), (ordered[3], ordered[0])]
    direction_a = edges[0]
    direction_b = edges[1]
    horizontal_pairs, vertical_pairs = (
        (pair_a, pair_b) if abs(direction_a[0]) >= abs(direction_b[0]) else (pair_b, pair_a)
    )
    horizontal = max(horizontal_pairs, key=lambda pair: (pair[0][1] + pair[1][1]) / 2.0)
    vertical = max(vertical_pairs, key=lambda pair: (pair[0][0] + pair[1][0]) / 2.0)
    return {
        "center": center,
        "ordered_centers": ordered,
        "horizontal_pair": horizontal,
        "vertical_pair": vertical,
        "width": math.dist(*horizontal),
        "height": math.dist(*vertical),
        "residual_ratio": max(opposite_a_error, opposite_b_error, *perpendicular_errors),
    }


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
    frame = rectangular_hole_frame(centers)
    diameter = float(feature.get("diameter") or 0.0)
    if not math.isfinite(diameter) or diameter <= 0.0:
        raise ValueError("Hole diameter must be positive")
    span = max(float(frame["width"]), float(frame["height"]), diameter)
    width = float(frame["width"])
    height = float(frame["height"])
    expected_width = float(feature.get("horizontal_center_distance") or width)
    expected_height = float(feature.get("vertical_center_distance") or height)
    measurement_tolerance = max(0.001, span * 0.01)
    if abs(width - expected_width) > measurement_tolerance:
        raise ValueError("Horizontal centre distance disagrees with centre coordinates")
    if abs(height - expected_height) > measurement_tolerance:
        raise ValueError("Vertical centre distance disagrees with centre coordinates")
    horizontal_start, horizontal_end = frame["horizontal_pair"]
    vertical_start, vertical_end = frame["vertical_pair"]
    diameter_center = horizontal_start
    return [
        {
            "task_id": "hole_pattern.diameter",
            "renderer": "DIAMETER_RENDERER",
            "semantic": "mounting_hole_pattern.hole_diameter",
            "center": diameter_center,
            "radius": diameter / 2.0,
            "nominal_value": diameter,
            "label": f"4-%%c{diameter:.3f}",
        },
        {
            "task_id": "hole_pattern.horizontal_center_distance",
            "renderer": "LINEAR_DIMENSION_RENDERER",
            "semantic": "mounting_hole_pattern.horizontal_center_distance",
            "p1": horizontal_start,
            "p2": horizontal_end,
            "nominal_value": width,
            "label": f"{width:.3f}",
            "orientation": "HORIZONTAL",
        },
        {
            "task_id": "hole_pattern.vertical_center_distance",
            "renderer": "LINEAR_DIMENSION_RENDERER",
            "semantic": "mounting_hole_pattern.vertical_center_distance",
            "p1": vertical_start,
            "p2": vertical_end,
            "nominal_value": height,
            "label": f"{height:.3f}",
            "orientation": "VERTICAL",
        },
    ]
