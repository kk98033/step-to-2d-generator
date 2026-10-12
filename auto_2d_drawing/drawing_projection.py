"""Shared 3D-to-2D helpers for smart drawing-rule generation.

The HLR renderer and the rule generator must agree on the view coordinate
system.  Rules store relative geometry derived from the feature bounding box;
they never assume that a feature is centred at drawing coordinate ``(0, 0)``.
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from auto_2d_drawing.config import VIEW_CONFIG


Point2D = Tuple[float, float]
BBox2D = Tuple[float, float, float, float]


def _normalize(vector: Sequence[float]) -> Tuple[float, float, float]:
    length = math.sqrt(sum(float(value) ** 2 for value in vector))
    if length <= 1e-12:
        raise ValueError("Projection basis vector cannot be zero")
    return tuple(float(value) / length for value in vector)  # type: ignore[return-value]


def _cross(left: Sequence[float], right: Sequence[float]) -> Tuple[float, float, float]:
    return (
        float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
        float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
        float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
    )


def view_basis(view_name: str) -> Dict[str, Tuple[float, float, float]]:
    """Return the exact local X/Y basis used by ``HLRAlgo_Projector``.

    ``ViewProjector`` constructs ``gp_Ax2(origin, direction, up_config)``.
    OpenCASCADE treats that third vector as the projector's local X direction,
    while local Y is ``direction × local_x``.  Using a conventional camera
    right/up basis here would rotate and mirror every smart-rule anchor.
    """
    config = VIEW_CONFIG[view_name]
    normal = _normalize(config["direction"])
    local_x = _normalize(config["up"])
    local_y = _normalize(_cross(normal, local_x))
    return {"right": local_x, "up": local_y, "normal": normal}


def project_point(point: Sequence[float], view_name: str) -> Point2D:
    """Project a world point into the same right/up plane used by a view."""
    basis = view_basis(view_name)
    xyz = tuple(float(value) for value in point[:3])
    return (
        sum(xyz[index] * basis["right"][index] for index in range(3)),
        sum(xyz[index] * basis["up"][index] for index in range(3)),
    )


def project_bbox(center: Sequence[float], size: Sequence[float], view_name: str) -> BBox2D:
    """Project all eight corners of a dynamic 3D Bnd_Box-style geometry."""
    c = [float(value) for value in center[:3]]
    half = [max(0.0, float(value)) / 2.0 for value in size[:3]]
    points = [
        project_point(
            [c[index] + signs[index] * half[index] for index in range(3)],
            view_name,
        )
        for signs in itertools.product((-1.0, 1.0), repeat=3)
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def feature_view_geometry(
    geometry: Dict[str, Any],
    view_name: str,
    fallback_bbox: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Build a serializable projected feature footprint for one view."""
    center = geometry.get("center")
    size = geometry.get("size")
    if isinstance(center, (list, tuple)) and len(center) >= 3 and isinstance(size, (list, tuple)) and len(size) >= 3:
        bbox = project_bbox(center, size, view_name)
        projected_center = project_point(center, view_name)
    elif fallback_bbox and len(fallback_bbox) == 4:
        bbox = tuple(float(value) for value in fallback_bbox)
        projected_center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    else:
        bbox = (0.0, 0.0, 0.0, 0.0)
        projected_center = (0.0, 0.0)
    return {
        "center": [round(projected_center[0], 4), round(projected_center[1], 4)],
        "bbox": [round(value, 4) for value in bbox],
        "width": round(abs(bbox[2] - bbox[0]), 4),
        "height": round(abs(bbox[3] - bbox[1]), 4),
    }


def linear_endpoints(footprint: Dict[str, Any], axis: Optional[str] = None) -> Dict[str, Any]:
    """Return endpoints on the actual projected footprint, not the origin."""
    x0, y0, x1, y1 = [float(value) for value in footprint["bbox"]]
    cx, cy = [float(value) for value in footprint["center"]]
    resolved_axis = axis or ("x" if abs(x1 - x0) >= abs(y1 - y0) else "y")
    if resolved_axis == "x":
        start, end = [x0, cy], [x1, cy]
    else:
        start, end = [cx, y0], [cx, y1]
    return {"start_proj": start, "end_proj": end, "axis": resolved_axis}


def diameter_endpoints(footprint: Dict[str, Any]) -> Dict[str, Any]:
    """Anchor a diameter across the shorter projected feature span."""
    x0, y0, x1, y1 = [float(value) for value in footprint["bbox"]]
    cx, cy = [float(value) for value in footprint["center"]]
    if abs(x1 - x0) <= abs(y1 - y0):
        start, end, axis = [x0, cy], [x1, cy], "x"
    else:
        start, end, axis = [cx, y0], [cx, y1], "y"
    return {"start_proj": start, "end_proj": end, "center": [cx, cy], "axis": axis}
