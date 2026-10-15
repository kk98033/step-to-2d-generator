"""Dynamic 3D blade section extraction for experiment 16."""

from __future__ import annotations

import math
from typing import Any, Sequence

from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Section
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt


METHOD = "DYNAMIC_BREP_BLADE_SECTION_V1"


def shape_bounds(shape) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    if box.IsVoid():
        raise ValueError("Cannot section a shape with void bounds")
    return tuple(float(value) for value in box.Get())


def infer_rotation_axis(bounds: Sequence[float]) -> dict[str, Any]:
    minimum, maximum = list(bounds[:3]), list(bounds[3:])
    spans = [maximum[index] - minimum[index] for index in range(3)]
    axis = min(range(3), key=lambda index: spans[index])
    radial_axes = [index for index in range(3) if index != axis]
    center = [(minimum[index] + maximum[index]) / 2.0 for index in range(3)]
    sorted_spans = sorted(spans)
    confidence = 1.0 - min(1.0, sorted_spans[0] / max(sorted_spans[1], 1e-9))
    return {
        "axis_index": axis,
        "radial_axes": radial_axes,
        "center": center,
        "spans": spans,
        "confidence": confidence,
        "method": "BND_BOX_MINIMUM_EXTENT_AXIS",
    }


def _sample_edge(edge, radial_axes: Sequence[int], sample_count: int) -> list[tuple[float, float]]:
    curve = BRepAdaptor_Curve(edge)
    first, last = float(curve.FirstParameter()), float(curve.LastParameter())
    if not (math.isfinite(first) and math.isfinite(last)) or abs(last - first) <= 1e-12:
        return []
    points = []
    for index in range(sample_count):
        parameter = first + (last - first) * index / max(sample_count - 1, 1)
        point = curve.Value(parameter)
        coordinates = (float(point.X()), float(point.Y()), float(point.Z()))
        points.append((coordinates[radial_axes[0]], coordinates[radial_axes[1]]))
    return points


def section_shape_view(
    shape,
    station_fraction: float,
    sample_count: int = 41,
) -> dict[str, Any]:
    bounds = shape_bounds(shape)
    axis = infer_rotation_axis(bounds)
    axis_index = int(axis["axis_index"])
    fraction = max(0.02, min(0.98, float(station_fraction)))
    coordinate = float(bounds[axis_index]) + (float(bounds[axis_index + 3]) - float(bounds[axis_index])) * fraction
    origin = list(axis["center"])
    origin[axis_index] = coordinate
    normal = [0.0, 0.0, 0.0]
    normal[axis_index] = 1.0
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*origin), gp_Dir(*normal))).Face()
    operation = BRepAlgoAPI_Section(shape, face, False)
    operation.Approximation(True)
    operation.Build()
    if not operation.IsDone() or operation.Shape().IsNull():
        raise RuntimeError(f"B-Rep section failed at fraction {fraction:.3f}")
    edges = []
    explorer = TopExp_Explorer(operation.Shape(), TopAbs_EDGE)
    while explorer.More():
        try:
            points = _sample_edge(explorer.Current(), axis["radial_axes"], sample_count)
            if len(points) >= 2:
                edges.append({"type": "spline", "points": [list(point) for point in points]})
        except Exception:
            pass
        explorer.Next()
    all_points = [point for edge in edges for point in edge["points"]]
    if not all_points:
        raise RuntimeError(f"B-Rep section produced no sampleable edges at fraction {fraction:.3f}")
    xs, ys = [point[0] for point in all_points], [point[1] for point in all_points]
    radial_center = [axis["center"][index] for index in axis["radial_axes"]]
    return {
        "method": METHOD,
        "station_fraction": fraction,
        "station_coordinate": coordinate,
        "axis": axis,
        "visible": edges,
        "hidden": [],
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "center_hint": radial_center,
        "center_hint_confidence": axis["confidence"],
        "section_edge_count": len(edges),
    }
