"""Generic angular-dimension rendering contracts for experimental drawings."""

from __future__ import annotations

import math
from typing import Any, Sequence


def minor_arc(start_angle: float, end_angle: float) -> tuple[float, float, float]:
    """Return a positive, at-most-180-degree arc between two real rays."""
    start = float(start_angle) % 360.0
    end = float(end_angle) % 360.0
    forward = (end - start) % 360.0
    if forward <= 180.0:
        return start, end, forward
    return end, start, 360.0 - forward


def relation_mid_angle(relation: dict[str, Any]) -> float:
    start, _end, span = minor_arc(relation["start_angle"], relation["end_angle"])
    return (start + span / 2.0) % 360.0


def select_relation_near(
    relations: Sequence[dict[str, Any]],
    target_angle: float,
    preferred_mid_angle: float | None = None,
) -> dict[str, Any]:
    """Select an actual measured relation; target values never create geometry."""
    if not relations:
        raise ValueError("At least one measured relation is required")

    def score(item: dict[str, Any]) -> tuple[float, float]:
        value_error = abs(float(item["angle"]) - float(target_angle))
        if preferred_mid_angle is None:
            position_error = 0.0
        else:
            delta = abs(relation_mid_angle(item) - float(preferred_mid_angle)) % 360.0
            position_error = min(delta, 360.0 - delta)
        return value_error, position_error

    return dict(min(relations, key=score))


def build_angular_render_tasks(
    relations: Sequence[dict[str, Any]],
    specifications: Sequence[dict[str, Any]],
    base_radius: float,
    lane_spacing: float,
) -> list[dict[str, Any]]:
    """Build renderer-neutral tasks from measured ray-to-ray relations."""
    tasks = []
    for lane, specification in enumerate(specifications):
        relation = select_relation_near(
            relations,
            float(specification["target_angle"]),
            specification.get("preferred_mid_angle"),
        )
        start, end, span = minor_arc(relation["start_angle"], relation["end_angle"])
        radius = float(base_radius) + lane * float(lane_spacing)
        tasks.append({
            "id": str(specification.get("id") or f"angular_{lane + 1:02d}"),
            "semantic": str(specification.get("semantic") or "radial_relation.included_angle"),
            "start_angle": start,
            "end_angle": end,
            "angle": span,
            "measured_relation_angle": float(relation["angle"]),
            "radius": radius,
            "lane": lane,
            "label": str(specification.get("label") or f"{span:.2f}°"),
            "left_element_index": relation.get("left_element_index"),
            "right_element_index": relation.get("right_element_index"),
            "source_relation": relation,
        })
    return tasks
