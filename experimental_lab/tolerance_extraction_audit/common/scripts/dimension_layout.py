"""Experimental, geometry-driven layout for validated DimensionIntent records.

The module only plans presentation.  It never changes feature identity, nominal
values, tolerance semantics, or the upstream projection anchors.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Sequence


SUPPORTED_STATUSES = {"AUTO_VERIFIED", "REVIEW_CANDIDATE", "REVIEW_CONFLICT"}


def _normalised_bbox(bbox: Sequence[float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _label(intent: dict[str, Any]) -> str:
    explicit = intent.get("measurement", {}).get("display_text")
    if explicit:
        return str(explicit)
    prefix = str(intent.get("renderer_contract", {}).get("prefix") or "")
    value = float(intent["measurement"].get("display_value", intent["measurement"]["nominal_value"]))
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    tolerance = intent.get("measurement", {}).get("tolerance") or {}
    if tolerance.get("mode") == "SYMMETRIC":
        return f"{prefix}{text} +/- {abs(float(tolerance['upper'])):.3f} mm"
    if tolerance.get("mode") == "LIMITS":
        return f"{prefix}{text} +{float(tolerance['upper']):.3f}/{float(tolerance['lower']):.3f} mm"
    return f"{prefix}{text} mm"


def _orientation(anchors: Sequence[Sequence[float]]) -> str:
    dx = abs(float(anchors[1][0]) - float(anchors[0][0]))
    dy = abs(float(anchors[1][1]) - float(anchors[0][1]))
    return "HORIZONTAL" if dx >= dy else "VERTICAL"


def _side_preferences(
    intent: dict[str, Any], orientation: str, model_centre: Sequence[float],
) -> tuple[str, str]:
    role = str(intent["measurement"]["role"])
    anchors = intent["projection"]["anchors"]
    if orientation == "HORIZONTAL":
        # Shaft lengths conventionally form a bottom baseline stack.  Circular
        # measurements use the opposite side so the two families do not fight.
        if role in {"AXIAL_OVERALL_LENGTH", "AXIAL_SEGMENT_LENGTH"}:
            return "BOTTOM", "TOP"
        return "TOP", "BOTTOM"
    mean_x = sum(float(point[0]) for point in anchors) / len(anchors)
    return ("RIGHT", "LEFT") if mean_x >= float(model_centre[0]) else ("LEFT", "RIGHT")


def _expanded_interval(
    intent: dict[str, Any], orientation: str, model_span: float,
) -> tuple[float, float]:
    anchors = intent["projection"]["anchors"]
    axis = 0 if orientation == "HORIZONTAL" else 1
    lo, hi = sorted(float(point[axis]) for point in anchors)
    # Approximate CAD text width in model units.  This is intentionally
    # scale-relative and therefore works without model-specific coordinates.
    text_extent = max(model_span * 0.055, len(_label(intent)) * model_span * 0.006)
    centre = (lo + hi) / 2.0
    half = max((hi - lo) / 2.0, text_extent / 2.0)
    padding = model_span * 0.012
    return centre - half - padding, centre + half + padding


def _overlaps(first: Sequence[float], second: Sequence[float]) -> bool:
    return max(float(first[0]), float(second[0])) < min(float(first[1]), float(second[1]))


def _lane_for(interval: Sequence[float], lanes: list[list[tuple[float, float]]]) -> int:
    for index, occupied in enumerate(lanes):
        if all(not _overlaps(interval, item) for item in occupied):
            occupied.append((float(interval[0]), float(interval[1])))
            return index
    lanes.append([(float(interval[0]), float(interval[1]))])
    return len(lanes) - 1


def _priority(intent: dict[str, Any]) -> tuple[int, float, str]:
    role = str(intent["measurement"]["role"])
    # Local dimensions get the nearest lanes.  Overall dimensions are placed
    # later and therefore naturally become the outermost baseline.
    order = {
        "RADIAL_WALL_THICKNESS": 0,
        "NORMAL_WALL_THICKNESS": 0,
        "THICKNESS_PROFILE": 0,
        "AXIAL_THICKNESS": 1,
        "INNER_DIAMETER": 2,
        "OUTER_DIAMETER": 3,
        "AXIAL_SEGMENT_LENGTH": 4,
        "AXIAL_OVERALL_LENGTH": 9,
    }.get(role, 5)
    return order, float(intent["measurement"]["nominal_value"]), str(intent["intent_id"])


def plan_dimension_layout(
    intents: Sequence[dict[str, Any]],
    view_bbox: Sequence[float],
    *,
    include_review: bool = True,
) -> dict[str, Any]:
    """Assign side/lane and explicit dimension geometry without moving anchors."""
    x0, y0, x1, y1 = _normalised_bbox(view_bbox)
    width, height = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    diagonal = math.hypot(width, height)
    base_gap = max(diagonal * 0.045, 0.5)
    lane_spacing = max(diagonal * 0.055, 0.7)
    lanes: dict[str, list[list[tuple[float, float]]]] = {
        "TOP": [], "BOTTOM": [], "LEFT": [], "RIGHT": [],
    }
    placements: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for source in sorted(intents, key=_priority):
        intent = copy.deepcopy(source)
        status = str(intent.get("verification", {}).get("status") or "")
        if status not in SUPPORTED_STATUSES or (status != "AUTO_VERIFIED" and not include_review):
            skipped.append({"intent_id": str(intent.get("intent_id")), "reason": f"STATUS_{status or 'UNKNOWN'}"})
            continue
        anchors = intent.get("projection", {}).get("anchors") or []
        if len(anchors) != 2:
            skipped.append({"intent_id": str(intent.get("intent_id")), "reason": "INVALID_ANCHORS"})
            continue
        orientation = _orientation(anchors)
        model_span = width if orientation == "HORIZONTAL" else height
        interval = _expanded_interval(intent, orientation, model_span)
        primary, secondary = _side_preferences(intent, orientation, ((x0 + x1) / 2.0, (y0 + y1) / 2.0))

        # Prefer the convention side.  If it is already substantially deeper
        # than the opposite side, balance the drawing without sacrificing the
        # overall-length convention.
        role = str(intent["measurement"]["role"])
        if role != "AXIAL_OVERALL_LENGTH" and len(lanes[primary]) > len(lanes[secondary]) + 1:
            primary, secondary = secondary, primary
        lane = _lane_for(interval, lanes[primary])
        side = primary

        offset = base_gap + lane * lane_spacing
        p0 = [float(value) for value in anchors[0][:2]]
        p1 = [float(value) for value in anchors[1][:2]]
        if side == "TOP":
            q0, q1 = [p0[0], y1 + offset], [p1[0], y1 + offset]
            text = [(p0[0] + p1[0]) / 2.0, y1 + offset + lane_spacing * 0.16]
        elif side == "BOTTOM":
            q0, q1 = [p0[0], y0 - offset], [p1[0], y0 - offset]
            text = [(p0[0] + p1[0]) / 2.0, y0 - offset - lane_spacing * 0.16]
        elif side == "RIGHT":
            q0, q1 = [x1 + offset, p0[1]], [x1 + offset, p1[1]]
            text = [x1 + offset + lane_spacing * 0.16, (p0[1] + p1[1]) / 2.0]
        else:
            q0, q1 = [x0 - offset, p0[1]], [x0 - offset, p1[1]]
            text = [x0 - offset - lane_spacing * 0.16, (p0[1] + p1[1]) / 2.0]

        placements.append({
            "intent_id": str(intent["intent_id"]),
            "measurement_role": role,
            "semantic": str(intent["measurement"]["semantic"]),
            "nominal_value": float(intent["measurement"]["nominal_value"]),
            "verification_status": status,
            "label": _label(intent),
            "orientation": orientation,
            "side": side,
            "lane": lane,
            "source_anchors": [p0, p1],
            "dimension_points": [q0, q1],
            "text_position": text,
            "occupied_interval": list(interval),
            "renderer": str(intent["renderer_contract"]["renderer"]),
        })

    return {
        "schema_version": 1,
        "method": "DIMENSION_LAYOUT_PLANNER_V1",
        "view_bbox": [x0, y0, x1, y1],
        "base_gap": base_gap,
        "lane_spacing": lane_spacing,
        "placements": placements,
        "skipped": skipped,
        "lane_counts": {side: len(items) for side, items in lanes.items()},
    }


def count_lane_collisions(plan: dict[str, Any]) -> int:
    """Count interval overlaps in a side/lane; a correct plan returns zero."""
    grouped: dict[tuple[str, int], list[Sequence[float]]] = {}
    for item in plan.get("placements") or []:
        grouped.setdefault((str(item["side"]), int(item["lane"])), []).append(item["occupied_interval"])
    collisions = 0
    for intervals in grouped.values():
        for index, first in enumerate(intervals):
            collisions += sum(_overlaps(first, second) for second in intervals[index + 1:])
    return collisions
