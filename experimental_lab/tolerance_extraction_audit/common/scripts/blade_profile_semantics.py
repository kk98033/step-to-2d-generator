"""Extract rotation-invariant blade profile endpoint angles from projected curves.

This module measures geometry only.  It does not use model names, expected
blade counts, historical drawing labels, or fixed model coordinates.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

from auto_2d_drawing.view_projector import ViewProjector


METHOD = "BLADE_PROFILE_ENDPOINT_TANGENT_V1"


class DenseCurveViewProjector(ViewProjector):
    """Experiment-only projector retaining enough curve samples for tangents."""

    def __init__(self, sample_count: int = 41):
        super().__init__()
        self.sample_count = max(int(sample_count), 5)

    def _discretize(self, curve, t0, t1, deflection=None):
        points = []
        for index in range(self.sample_count):
            parameter = t0 + (t1 - t0) * index / (self.sample_count - 1)
            point = curve.Value(parameter)
            points.append((point.X(), point.Y()))
        return points


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(float(value) for value in point[:2]) for point in edge.get("points") or []]


def _angle(vector: Sequence[float]) -> float:
    return math.degrees(math.atan2(float(vector[1]), float(vector[0]))) % 360.0


def _signed_delta(start: float, end: float) -> float:
    return (float(end) - float(start) + 180.0) % 360.0 - 180.0


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _robust_summary(values: Sequence[float]) -> dict[str, float]:
    values = [float(value) for value in values]
    if not values:
        return {"median": 0.0, "q10": 0.0, "q90": 0.0, "spread": 0.0}
    q10, q90 = _quantile(values, 0.10), _quantile(values, 0.90)
    return {
        "median": statistics.median(values),
        "q10": q10,
        "q90": q90,
        "spread": q90 - q10,
    }


def _orient_inner_to_outer(
    points: Sequence[Sequence[float]], center: Sequence[float]
) -> list[tuple[float, float]]:
    clean = [tuple(float(value) for value in point[:2]) for point in points]
    if len(clean) < 2:
        return clean
    first_radius = math.dist(clean[0], center)
    last_radius = math.dist(clean[-1], center)
    return clean if first_radius <= last_radius else list(reversed(clean))


def _point_at_arclength_fraction(
    points: Sequence[Sequence[float]], fraction: float, from_end: bool = False
) -> tuple[float, float]:
    ordered = list(reversed(points)) if from_end else list(points)
    segments = [math.dist(ordered[index], ordered[index + 1]) for index in range(len(ordered) - 1)]
    total = sum(segments)
    if total <= 1e-12:
        return tuple(float(value) for value in ordered[-1][:2])
    target = total * max(0.0, min(1.0, fraction))
    travelled = 0.0
    for index, length in enumerate(segments):
        if travelled + length >= target and length > 1e-12:
            ratio = (target - travelled) / length
            start, end = ordered[index], ordered[index + 1]
            return (
                float(start[0]) + (float(end[0]) - float(start[0])) * ratio,
                float(start[1]) + (float(end[1]) - float(start[1])) * ratio,
            )
        travelled += length
    return tuple(float(value) for value in ordered[-1][:2])


def measure_curve_endpoint_angles(
    points: Sequence[Sequence[float]],
    center: Sequence[float],
    tangent_window_fraction: float = 0.10,
) -> dict[str, Any] | None:
    """Measure local endpoint tangents in a radial frame.

    Curves are oriented from their smaller-radius endpoint toward their
    larger-radius endpoint.  Both endpoint tangent vectors therefore point in
    the physical inner-to-outer direction and remain invariant under global
    model rotation.
    """
    oriented = _orient_inner_to_outer(points, center)
    if len(oriented) < 2:
        return None
    inner, outer = oriented[0], oriented[-1]
    inner_sample = _point_at_arclength_fraction(oriented, tangent_window_fraction)
    outer_sample = _point_at_arclength_fraction(oriented, tangent_window_fraction, from_end=True)
    inner_vector = (inner_sample[0] - inner[0], inner_sample[1] - inner[1])
    outer_vector = (outer[0] - outer_sample[0], outer[1] - outer_sample[1])
    if math.hypot(*inner_vector) <= 1e-10 or math.hypot(*outer_vector) <= 1e-10:
        return None
    inner_radial = _angle((inner[0] - center[0], inner[1] - center[1]))
    outer_radial = _angle((outer[0] - center[0], outer[1] - center[1]))
    inner_tangent, outer_tangent = _angle(inner_vector), _angle(outer_vector)
    return {
        "inner_point": list(inner),
        "outer_point": list(outer),
        "inner_sample_point": list(inner_sample),
        "outer_sample_point": list(outer_sample),
        "inner_radial_angle": inner_radial,
        "outer_radial_angle": outer_radial,
        "inner_tangent_angle": inner_tangent,
        "outer_tangent_angle": outer_tangent,
        "inner_relative_to_radial": _signed_delta(inner_radial, inner_tangent),
        "outer_relative_to_radial": _signed_delta(outer_radial, outer_tangent),
        "inner_relative_to_tangent": _signed_delta(inner_radial + 90.0, inner_tangent),
        "outer_relative_to_tangent": _signed_delta(outer_radial + 90.0, outer_tangent),
    }


def _resolve_reference(view: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if ":" not in reference:
        return None
    collection_name, index_text = reference.split(":", 1)
    collection = view.get(collection_name) or []
    try:
        index = int(index_text)
    except ValueError:
        return None
    return collection[index] if 0 <= index < len(collection) else None


def _view_edge_records(view: dict[str, Any], center: Sequence[float]) -> list[dict[str, Any]]:
    records = []
    for collection_name in ("visible", "hidden"):
        for index, edge in enumerate(view.get(collection_name) or []):
            if edge.get("type") == "circle":
                continue
            points = _orient_inner_to_outer(_edge_points(edge), center)
            if len(points) < 2:
                continue
            records.append({
                "ref": f"{collection_name}:{index}",
                "edge_type": edge.get("type"),
                "points": points,
                "inner_radius": math.dist(points[0], center),
                "outer_radius": math.dist(points[-1], center),
            })
    return records


def trace_outward_boundary_chain(
    view: dict[str, Any],
    start_reference: str,
    center: Sequence[float],
    connection_tolerance_fraction: float = 0.004,
    maximum_segments: int = 12,
) -> dict[str, Any]:
    """Trace connected non-circular HLR edges toward the outer envelope."""
    x0, y0, x1, y1 = [float(value) for value in view["bbox"]]
    span = max(x1 - x0, y1 - y0, 1e-9)
    tolerance = span * connection_tolerance_fraction
    records = _view_edge_records(view, center)
    by_reference = {item["ref"]: item for item in records}
    start = by_reference.get(start_reference)
    if start is None:
        return {"resolved": False, "reason": "START_EDGE_NOT_FOUND", "edge_refs": []}
    chain = [start]
    used = {start_reference}
    while len(chain) < maximum_segments:
        current = chain[-1]
        endpoint = current["points"][-1]
        current_radius = float(current["outer_radius"])
        candidates = []
        for candidate in records:
            if candidate["ref"] in used:
                continue
            radius_gain = float(candidate["outer_radius"]) - current_radius
            if radius_gain <= tolerance * 0.20:
                continue
            distance = math.dist(endpoint, candidate["points"][0])
            if distance > tolerance:
                continue
            current_vector = (
                endpoint[0] - current["points"][-2][0],
                endpoint[1] - current["points"][-2][1],
            )
            candidate_vector = (
                candidate["points"][1][0] - candidate["points"][0][0],
                candidate["points"][1][1] - candidate["points"][0][1],
            )
            turn = abs(_signed_delta(_angle(current_vector), _angle(candidate_vector)))
            if turn > 75.0:
                continue
            score = distance / max(tolerance, 1e-9) + turn / 90.0 - radius_gain / span * 0.15
            candidates.append((score, candidate))
        if not candidates:
            break
        chosen = min(candidates, key=lambda item: item[0])[1]
        chain.append(chosen)
        used.add(chosen["ref"])
    points = list(chain[0]["points"])
    for item in chain[1:]:
        if math.dist(points[-1], item["points"][0]) <= tolerance:
            points.extend(item["points"][1:])
        else:
            points.extend(item["points"])
    return {
        "resolved": True,
        "reason": "OUTWARD_CHAIN_TRACED",
        "edge_refs": [item["ref"] for item in chain],
        "segment_count": len(chain),
        "points": [list(point) for point in points],
        "inner_radius": float(chain[0]["inner_radius"]),
        "outer_radius": float(chain[-1]["outer_radius"]),
        "connection_tolerance": tolerance,
    }


def trace_full_radial_boundary_chain(
    view: dict[str, Any],
    start_reference: str,
    center: Sequence[float],
    connection_tolerance_fraction: float = 0.004,
    maximum_segments: int = 24,
) -> dict[str, Any]:
    """Trace a fragmented blade boundary both toward hub and outer envelope."""
    x0, y0, x1, y1 = [float(value) for value in view["bbox"]]
    span = max(x1 - x0, y1 - y0, 1e-9)
    tolerance = span * connection_tolerance_fraction
    records = _view_edge_records(view, center)
    by_reference = {item["ref"]: item for item in records}
    start = by_reference.get(start_reference)
    if start is None:
        return {"resolved": False, "reason": "START_EDGE_NOT_FOUND", "edge_refs": []}
    inward: list[dict[str, Any]] = []
    outward: list[dict[str, Any]] = []
    used = {start_reference}

    current = start
    while len(inward) + len(outward) + 1 < maximum_segments:
        endpoint = current["points"][0]
        current_radius = float(current["inner_radius"])
        candidates = []
        for candidate in records:
            if candidate["ref"] in used:
                continue
            radius_gain = current_radius - float(candidate["inner_radius"])
            if radius_gain <= tolerance * 0.20:
                continue
            distance = math.dist(endpoint, candidate["points"][-1])
            if distance > tolerance:
                continue
            candidate_vector = (
                candidate["points"][-1][0] - candidate["points"][-2][0],
                candidate["points"][-1][1] - candidate["points"][-2][1],
            )
            current_vector = (
                current["points"][1][0] - current["points"][0][0],
                current["points"][1][1] - current["points"][0][1],
            )
            turn = abs(_signed_delta(_angle(candidate_vector), _angle(current_vector)))
            if turn > 75.0:
                continue
            score = distance / max(tolerance, 1e-9) + turn / 90.0 - radius_gain / span * 0.15
            candidates.append((score, candidate))
        if not candidates:
            break
        chosen = min(candidates, key=lambda item: item[0])[1]
        inward.insert(0, chosen)
        used.add(chosen["ref"])
        current = chosen

    current = start
    while len(inward) + len(outward) + 1 < maximum_segments:
        endpoint = current["points"][-1]
        current_radius = float(current["outer_radius"])
        candidates = []
        for candidate in records:
            if candidate["ref"] in used:
                continue
            radius_gain = float(candidate["outer_radius"]) - current_radius
            if radius_gain <= tolerance * 0.20:
                continue
            distance = math.dist(endpoint, candidate["points"][0])
            if distance > tolerance:
                continue
            current_vector = (
                current["points"][-1][0] - current["points"][-2][0],
                current["points"][-1][1] - current["points"][-2][1],
            )
            candidate_vector = (
                candidate["points"][1][0] - candidate["points"][0][0],
                candidate["points"][1][1] - candidate["points"][0][1],
            )
            turn = abs(_signed_delta(_angle(current_vector), _angle(candidate_vector)))
            if turn > 75.0:
                continue
            score = distance / max(tolerance, 1e-9) + turn / 90.0 - radius_gain / span * 0.15
            candidates.append((score, candidate))
        if not candidates:
            break
        chosen = min(candidates, key=lambda item: item[0])[1]
        outward.append(chosen)
        used.add(chosen["ref"])
        current = chosen

    chain = inward + [start] + outward
    points = list(chain[0]["points"])
    for item in chain[1:]:
        points.extend(item["points"][1:] if math.dist(points[-1], item["points"][0]) <= tolerance else item["points"])
    return {
        "resolved": True,
        "reason": "FULL_RADIAL_CHAIN_TRACED",
        "edge_refs": [item["ref"] for item in chain],
        "segment_count": len(chain),
        "points": [list(point) for point in points],
        "inner_radius": float(chain[0]["inner_radius"]),
        "outer_radius": float(chain[-1]["outer_radius"]),
        "radial_coverage_fraction": (
            float(chain[-1]["outer_radius"]) - float(chain[0]["inner_radius"])
        ) / max(span * 0.5, 1e-9),
        "connection_tolerance": tolerance,
    }


def extract_blade_profile_semantics(
    view: dict[str, Any], detection: dict[str, Any], tangent_window_fraction: float = 0.10
) -> dict[str, Any]:
    center = detection.get("center")
    elements = detection.get("elements") or []
    if not center or not elements:
        return {"resolved": False, "reason": "NO_RESOLVED_CURVED_PATTERN", "method": METHOD}

    measurements = []
    for element_index, element in enumerate(elements, start=1):
        for family, key in (("left", "left_boundary_ref"), ("right", "right_boundary_ref")):
            reference = str(element.get(key) or "")
            edge = _resolve_reference(view, reference)
            if edge is None:
                continue
            chain = trace_full_radial_boundary_chain(view, reference, center)
            source_points = chain.get("points") or _edge_points(edge)
            measurement = measure_curve_endpoint_angles(
                source_points, center, tangent_window_fraction=tangent_window_fraction
            )
            if measurement is not None:
                measurements.append({
                    **measurement,
                    "element_index": element_index,
                    "boundary_family": family,
                    "edge_ref": reference,
                    "chain_edge_refs": chain.get("edge_refs") or [reference],
                    "chain_segment_count": int(chain.get("segment_count") or 1),
                })
    if len(measurements) < 3:
        return {
            "resolved": False,
            "reason": "INSUFFICIENT_ENDPOINT_TANGENT_MEASUREMENTS",
            "method": METHOD,
            "measurement_count": len(measurements),
        }

    summaries = {}
    for field in (
        "inner_relative_to_radial",
        "outer_relative_to_radial",
        "inner_relative_to_tangent",
        "outer_relative_to_tangent",
    ):
        summaries[field] = _robust_summary([float(item[field]) for item in measurements])
    family_summaries = {}
    for family in ("left", "right"):
        subset = [item for item in measurements if item["boundary_family"] == family]
        family_summaries[family] = {
            field: _robust_summary([float(item[field]) for item in subset])
            for field in summaries
        }
    return {
        "resolved": True,
        "reason": "PROFILE_ENDPOINT_TANGENTS_MEASURED",
        "method": METHOD,
        "center": list(center),
        "pattern_count": int(detection.get("pattern_count") or len(elements)),
        "pattern_count_candidates": list(detection.get("pattern_count_candidates") or []),
        "measurement_count": len(measurements),
        "tangent_window_fraction": tangent_window_fraction,
        "summaries": summaries,
        "family_summaries": family_summaries,
        "measurements": measurements,
        "verification_status": "REVIEW_CANDIDATE",
    }
