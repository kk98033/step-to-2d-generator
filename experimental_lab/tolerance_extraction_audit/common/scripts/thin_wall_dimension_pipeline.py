"""End-to-end geometry-to-intent adapter for generic thin-wall dimensions."""

from __future__ import annotations

import math
from typing import Any, Sequence

from auto_2d_drawing.config import VIEW_CONFIG
from auto_2d_drawing.view_projector import ViewProjector
from adaptive_curved_radial_features import detect_adaptive_curved_radial_partitions
from blade_section_semantics import section_shape_view
from paired_surface_thickness import (
    aggregate_station_thickness,
    extract_parallel_planar_thickness,
    extract_section_boundary_thickness,
)


METHOD = "THIN_WALL_DIMENSION_PIPELINE_V1"
STANDARD_VIEWS = ("front", "top", "right", "left", "back")


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _cross(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (
        float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
        float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
        float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
    )


def _project_point(point: Sequence[float], view_name: str) -> list[float]:
    config = VIEW_CONFIG[view_name]
    direction, x_axis = config["direction"], config["up"]
    y_axis = _cross(direction, x_axis)
    return [_dot(point, x_axis), _dot(point, y_axis)]


def _view_data(projector: ViewProjector, shape, view_name: str) -> dict[str, Any]:
    visible, hidden, _outline_visible, _outline_hidden = projector.project(shape, view_name)
    visible_edges = projector.extract_edges(visible)
    hidden_edges = projector.extract_edges(hidden)
    all_edges = visible_edges + hidden_edges
    return {
        "name": view_name,
        "visible": visible_edges,
        "hidden": hidden_edges,
        "bbox": list(projector.get_edges_bbox(all_edges)),
    }


def _nearest_curve_pair(
    source: Sequence[Sequence[float]],
    companion: Sequence[Sequence[float]],
    target: float,
) -> tuple[list[float], list[float], float]:
    candidates = []
    for point in source:
        nearest = min(companion, key=lambda other: math.dist(point[:2], other[:2]))
        candidates.append((abs(math.dist(point[:2], nearest[:2]) - target), point, nearest))
    for point in companion:
        nearest = min(source, key=lambda other: math.dist(point[:2], other[:2]))
        candidates.append((abs(math.dist(point[:2], nearest[:2]) - target), nearest, point))
    _error, left, right = min(candidates, key=lambda item: item[0])
    anchors = ([float(value) for value in left[:2]], [float(value) for value in right[:2]])
    return anchors[0], anchors[1], math.dist(*anchors)


def _measurement_payload(
    nominal: float,
    specification: dict[str, Any] | None,
    *,
    variable_range: Sequence[float] | None = None,
) -> dict[str, Any]:
    specification = specification or {}
    payload: dict[str, Any] = {
        "display_value": float(specification.get("display_value", round(float(nominal), 3))),
        "tolerance_source": str(specification.get("tolerance_source") or "NONE"),
    }
    if specification.get("tolerance") is not None:
        payload["tolerance"] = specification["tolerance"]
    if specification.get("display_text"):
        payload["display_text"] = str(specification["display_text"])
    elif variable_range:
        payload["display_text"] = f"T {min(variable_range):.3f}~{max(variable_range):.3f} mm [REVIEW]"
    return payload


def extract_periodic_thin_wall_dimension(
    shape,
    specification: dict[str, Any] | None = None,
    fractions: Sequence[float] = (0.1, 0.3, 0.5, 0.6),
) -> dict[str, Any]:
    stations = []
    for fraction in fractions:
        try:
            view = section_shape_view(shape, float(fraction))
            detection = detect_adaptive_curved_radial_partitions(view)
            thickness = extract_section_boundary_thickness(view, detection)
            stations.append({"fraction": float(fraction), "view": view, "detection": detection, "thickness": thickness})
        except Exception as exc:
            stations.append({"fraction": float(fraction), "error": str(exc), "thickness": {"resolved": False}})
    aggregate = aggregate_station_thickness([item["thickness"] for item in stations])
    resolved = [item for item in stations if item["thickness"].get("resolved")]
    if not aggregate.get("resolved") or not resolved:
        return {
            "resolved": False,
            "reason": aggregate.get("reason") or "NO_PERIODIC_THIN_WALL",
            "method": METHOD,
            "stations": stations,
        }
    aggregate_nominal = float(aggregate["summary"]["median"])
    station = min(resolved, key=lambda item: abs(float(item["thickness"]["summary"]["median"]) - aggregate_nominal))
    measurement = min(
        station["thickness"]["measurements"],
        key=lambda item: abs(float(item["thickness"]) - aggregate_nominal),
    )
    anchor0, anchor1, local_nominal = _nearest_curve_pair(
        measurement["source_points"], measurement["companion_points"], aggregate_nominal,
    )
    axis_index = int(station["view"]["axis"]["axis_index"])
    axis = [0.0, 0.0, 0.0]
    axis[axis_index] = 1.0
    semantic = "thin_wall.normal_thickness" if aggregate["uniform_thickness"] else "thin_wall.thickness_profile"
    feature_id = "thin_wall_periodic_section_001"
    dimension = {
        "id": "thin_wall_dimension_001",
        "feature_id": feature_id,
        "semantic": semantic,
        "dim_type": "LINEAR",
        "nominal_value": local_nominal,
        "view": f"section_{station['fraction']:.3f}",
        "anchors": [anchor0, anchor1],
        "verification_status": aggregate["verification_status"],
        "confidence": max(0.0, min(0.99, 1.0 - float(aggregate["summary"]["cv"]))),
        **_measurement_payload(local_nominal, specification, variable_range=aggregate.get("station_medians") if not aggregate["uniform_thickness"] else None),
    }
    return {
        "resolved": True,
        "reason": aggregate["reason"],
        "method": METHOD,
        "path": "PERIODIC_SECTION_BOUNDARY_PAIR",
        "features": [{
            "id": feature_id,
            "semantic": semantic,
            "solid_index": 0,
            "topology_refs": [measurement["source_reference"], measurement["companion_reference"]],
            "axis": axis,
            "aggregate": aggregate,
            "station_fraction": station["fraction"],
            "pattern_count": int(station["detection"].get("pattern_count") or 0),
        }],
        "dimensions": [dimension],
        "aggregate": aggregate,
        "view": station["view"],
        "stations": stations,
    }


def extract_planar_thin_wall_dimension(
    shape,
    specification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = extract_parallel_planar_thickness(shape)
    if not result.get("resolved"):
        return {"resolved": False, "reason": result["reason"], "method": METHOD, "detector": result}
    candidate = result["candidate"]
    normal = candidate["left_face"]["normal"]
    qualified_views = [
        name for name in STANDARD_VIEWS
        if abs(_dot(VIEW_CONFIG[name]["direction"], normal)) <= 0.15
    ]
    projector = ViewProjector()
    ranked = []
    for view_name in qualified_views:
        view = _view_data(projector, shape, view_name)
        delta = [
            float(candidate["right_face"]["center"][index]) - float(candidate["left_face"]["center"][index])
            for index in range(3)
        ]
        direction_sign = 1.0 if _dot(delta, normal) >= 0.0 else -1.0
        left_anchor = [float(value) for value in candidate["left_face"]["center"]]
        right_anchor = [
            left_anchor[index] + direction_sign * float(candidate["thickness"]) * float(normal[index])
            for index in range(3)
        ]
        anchors = [
            _project_point(left_anchor, view_name),
            _project_point(right_anchor, view_name),
        ]
        anchor_span = math.dist(*anchors)
        bbox = view["bbox"]
        drawing_span = max(float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1]), 1e-9)
        ranked.append((abs(anchor_span - float(candidate["thickness"])), -drawing_span, view_name, view, anchors))
    if not ranked:
        return {"resolved": False, "reason": "NO_THICKNESS_VISIBLE_STANDARD_VIEW", "method": METHOD, "detector": result}
    _error, _negative_span, view_name, view, anchors = min(ranked, key=lambda item: (item[0], item[1]))
    nominal = float(candidate["thickness"])
    feature_id = "thin_wall_planar_pair_001"
    dimension = {
        "id": "thin_wall_dimension_001",
        "feature_id": feature_id,
        "semantic": "thin_wall.normal_thickness",
        "dim_type": "LINEAR",
        "nominal_value": nominal,
        "view": view_name,
        "anchors": anchors,
        "verification_status": str(result.get("verification_status") or "REVIEW_CANDIDATE"),
        "confidence": max(0.0, min(0.99, float(candidate["score"]))),
        **_measurement_payload(nominal, specification),
    }
    return {
        "resolved": True,
        "reason": "PLANAR_THIN_WALL_DIMENSION_RESOLVED",
        "method": METHOD,
        "path": "PARALLEL_PLANAR_FACE_PAIR",
        "features": [{
            "id": feature_id,
            "semantic": "thin_wall.normal_thickness",
            "solid_index": 0,
            "topology_refs": [
                f"face:{candidate['left_face']['index']}",
                f"face:{candidate['right_face']['index']}",
            ],
            "axis": normal,
            "detector_candidate": candidate,
        }],
        "dimensions": [dimension],
        "view": view,
        "detector": result,
    }


def extract_thin_wall_dimension(
    shape,
    specification: dict[str, Any] | None = None,
    fractions: Sequence[float] = (0.1, 0.3, 0.5, 0.6),
) -> dict[str, Any]:
    periodic = extract_periodic_thin_wall_dimension(shape, specification, fractions)
    if periodic.get("resolved"):
        return periodic
    planar = extract_planar_thin_wall_dimension(shape, specification)
    if planar.get("resolved"):
        planar["periodic_attempt"] = {"reason": periodic.get("reason")}
        return planar
    return {
        "resolved": False,
        "reason": "NO_GENERIC_THIN_WALL_DIMENSION",
        "method": METHOD,
        "periodic_attempt": {"reason": periodic.get("reason")},
        "planar_attempt": {"reason": planar.get("reason")},
        "features": [],
        "dimensions": [],
    }
