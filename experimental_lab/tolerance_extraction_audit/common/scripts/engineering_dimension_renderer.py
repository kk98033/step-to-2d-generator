"""Render experimental engineering dimensions with auditable endpoint geometry."""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence


DIAMETER_BASE_ANGLE_DEG = 145.0
DIAMETER_LANE_SPACING_DEG = 22.0


def diameter_label_sector(diameter_count: int) -> dict[str, Any] | None:
    """Return the polar corridor occupied by full-span diameter labels."""
    if diameter_count <= 0:
        return None
    label_angles = [
        ((DIAMETER_BASE_ANGLE_DEG + lane * DIAMETER_LANE_SPACING_DEG) + 180.0) % 360.0
        for lane in range(diameter_count)
    ]
    x = sum(math.cos(math.radians(angle)) for angle in label_angles)
    y = sum(math.sin(math.radians(angle)) for angle in label_angles)
    midpoint = math.degrees(math.atan2(y, x)) % 360.0
    spread = max((_circular_difference(angle, midpoint) for angle in label_angles), default=0.0)
    return {
        "mid_angle": midpoint,
        "span": max(60.0, spread * 2.0 + 24.0),
        "kind": "diameter_label_corridor",
    }


def orient_diameter_arrows_inward(document: Any, dimension: Any) -> None:
    """Aim both rendered arrow tips from their endpoints toward the circle centre."""
    endpoints = (dimension.dxf.defpoint, dimension.dxf.defpoint4)
    block = document.blocks.get(dimension.dxf.geometry)
    arrows = [entity for entity in block if entity.dxftype() == "INSERT"]
    if len(arrows) < 2:
        raise ValueError("Rendered diameter does not contain two arrowheads")

    unused = list(arrows)
    for endpoint, opposite in ((endpoints[0], endpoints[1]), (endpoints[1], endpoints[0])):
        arrow = min(
            unused,
            key=lambda entity: math.dist(
                (entity.dxf.insert.x, entity.dxf.insert.y),
                (endpoint.x, endpoint.y),
            ),
        )
        unused.remove(arrow)
        arrow.dxf.rotation = math.degrees(
            math.atan2(opposite.y - endpoint.y, opposite.x - endpoint.x)
        ) % 360.0


def diameter_arrow_audit(dimension: Any) -> dict[str, Any]:
    """Verify that two endpoint arrow vectors point toward the opposite endpoint."""
    endpoints = (dimension.dxf.defpoint, dimension.dxf.defpoint4)
    arrows = [entity for entity in dimension.virtual_entities() if entity.dxftype() == "INSERT"]
    unused = list(arrows)
    checks: list[bool] = []
    for endpoint, opposite in ((endpoints[0], endpoints[1]), (endpoints[1], endpoints[0])):
        if not unused:
            checks.append(False)
            continue
        arrow = min(
            unused,
            key=lambda entity: math.dist(
                (entity.dxf.insert.x, entity.dxf.insert.y),
                (endpoint.x, endpoint.y),
            ),
        )
        unused.remove(arrow)
        rotation = math.radians(float(arrow.dxf.rotation))
        target = (opposite.x - endpoint.x, opposite.y - endpoint.y)
        target_length = math.hypot(*target)
        alignment = (
            math.cos(rotation) * target[0] + math.sin(rotation) * target[1]
        ) / max(target_length, 1e-12)
        checks.append(alignment > 0.99)
    return {
        "arrowhead_count": len(arrows),
        "two_arrowheads": len(arrows) >= 2,
        "arrowheads_point_inward": len(arrows) >= 2 and all(checks),
    }


def render_annular_dimensions(
    document: Any,
    tasks: Sequence[Mapping[str, Any]],
    *,
    map_point: Callable[[Sequence[float]], tuple[float, float]],
    drawing_bbox: Sequence[float],
) -> None:
    """Render full-span diameters and conventional aligned annular dimensions."""
    modelspace = document.modelspace()
    span = max(
        float(drawing_bbox[2]) - float(drawing_bbox[0]),
        float(drawing_bbox[3]) - float(drawing_bbox[1]),
    )
    for lane, task in enumerate(tasks):
        anchors = [map_point(point) for point in task["anchors"]]
        value = float(task["nominal_value"])
        if str(task["dim_type"]).upper() == "DIAMETER":
            center = (
                (anchors[0][0] + anchors[1][0]) / 2.0,
                (anchors[0][1] + anchors[1][1]) / 2.0,
            )
            radius = math.dist(anchors[0], anchors[1]) / 2.0
            override = modelspace.add_diameter_dim(
                center=center,
                radius=radius,
                angle=DIAMETER_BASE_ANGLE_DEG + lane * DIAMETER_LANE_SPACING_DEG,
                text=str(task.get("label") or f"%%c{value:.2f}"),
                dimstyle="EZ_RADIUS",
                override={"dimtofl": 1},
                dxfattribs={"layer": "DIM"},
            )
            override.render()
            orient_diameter_arrows_inward(document, override.dimension)
        else:
            offset = span * (0.035 + lane * 0.02)
            dimension = modelspace.add_aligned_dim(
                p1=anchors[0],
                p2=anchors[1],
                distance=offset,
                text=str(task.get("label") or f"{value:.2f}"),
                dimstyle="EZDXF",
                dxfattribs={"layer": "DIM"},
            )
            dimension.render()


def _circular_difference(first: float, second: float) -> float:
    delta = abs(float(first) - float(second)) % 360.0
    return min(delta, 360.0 - delta)
