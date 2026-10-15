"""Renderer-neutral local placement for thin-wall dimensions."""

from __future__ import annotations

import math
from typing import Any, Sequence


METHOD = "LOCAL_THICKNESS_RENDERER_V1"


def format_thickness_label(intent: dict[str, Any]) -> str:
    measurement = intent["measurement"]
    if measurement.get("display_text"):
        return str(measurement["display_text"])
    value = float(measurement.get("display_value", measurement["nominal_value"]))
    value_text = f"{value:.3f}".rstrip("0").rstrip(".")
    tolerance = measurement.get("tolerance") or {}
    if tolerance.get("mode") == "SYMMETRIC":
        deviation = abs(float(tolerance["upper"]))
        return f"{value_text} +/- {deviation:.3f} mm"
    if tolerance.get("mode") == "LIMITS":
        return f"{value_text} +{float(tolerance['upper']):.3f}/{float(tolerance['lower']):.3f} mm"
    return f"{value_text} mm"


def build_thickness_render_task(
    intent: dict[str, Any],
    view_bbox: Sequence[float],
    *,
    lane: int = 0,
) -> dict[str, Any]:
    anchors = [[float(value) for value in point[:2]] for point in intent["projection"]["anchors"]]
    if len(anchors) != 2:
        raise ValueError("Thickness rendering requires exactly two anchors")
    p0, p1 = anchors
    vx, vy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(vx, vy)
    if length <= 1e-12:
        raise ValueError("Thickness anchors have zero span")
    nx, ny = -vy / length, vx / length
    x0, y0, x1, y1 = [float(value) for value in view_bbox]
    model_centre = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    midpoint = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    if nx * (midpoint[0] - model_centre[0]) + ny * (midpoint[1] - model_centre[1]) < 0.0:
        nx, ny = -nx, -ny
    span = max(x1 - x0, y1 - y0, 1e-9)
    offset = span * (0.055 + max(int(lane), 0) * 0.035)
    q0 = [p0[0] + nx * offset, p0[1] + ny * offset]
    q1 = [p1[0] + nx * offset, p1[1] + ny * offset]
    text_position = [
        (q0[0] + q1[0]) / 2.0 + nx * span * 0.018,
        (q0[1] + q1[1]) / 2.0 + ny * span * 0.018,
    ]
    return {
        "schema_version": 1,
        "method": METHOD,
        "intent_id": str(intent["intent_id"]),
        "semantic": str(intent["measurement"]["semantic"]),
        "measurement_role": str(intent["measurement"]["role"]),
        "verification_status": str(intent["verification"]["status"]),
        "source_anchors": anchors,
        "dimension_points": [q0, q1],
        "text_position": text_position,
        "offset_direction": [nx, ny],
        "offset": offset,
        "label": format_thickness_label(intent),
        "renderer": "THICKNESS_RENDERER",
        "tolerance_source": intent["measurement"].get("tolerance_source", "NONE"),
    }
