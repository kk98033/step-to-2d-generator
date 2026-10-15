"""Geometry-only thin-wall thickness semantics for experiment 17.

The detector deliberately does not inspect part names, product families, or
historical dimension values.  It supports two geometry paths:

* parallel planar face pairs for ordinary plates and housing walls;
* local companion boundaries in repeated curved sections for thin swept
  elements whose two physical sides are not planar.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.GProp import GProp_GProps
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import topods

from blade_section_semantics import shape_bounds


METHOD = "PAIRED_SURFACE_THICKNESS_V1"


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower, upper = int(math.floor(position)), int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def robust_summary(values: Sequence[float]) -> dict[str, float]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {"count": 0, "median": 0.0, "q10": 0.0, "q90": 0.0, "spread": 0.0, "cv": 0.0}
    mean = statistics.fmean(clean)
    q10, q90 = _quantile(clean, 0.10), _quantile(clean, 0.90)
    return {
        "count": len(clean),
        "median": statistics.median(clean),
        "q10": q10,
        "q90": q90,
        "spread": q90 - q10,
        "cv": statistics.pstdev(clean) / max(abs(mean), 1e-12) if len(clean) > 1 else 0.0,
    }


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _sub(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    return tuple(float(a) - float(b) for a, b in zip(left, right))


def extract_parallel_planar_thickness(shape) -> dict[str, Any]:
    """Find a dominant close pair of parallel planar faces.

    This path is intended for generic plates/walls.  The small-span and area
    gates prevent the end faces of a solid shaft from impersonating wall
    thickness.
    """
    bounds = shape_bounds(shape)
    spans = [float(bounds[index + 3]) - float(bounds[index]) for index in range(3)]
    model_span = max(spans)
    total_properties = GProp_GProps()
    brepgprop.SurfaceProperties(shape, total_properties)
    total_surface_area = max(float(total_properties.Mass()), 1e-12)
    faces = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = topods.Face(explorer.Current())
        try:
            surface = BRepAdaptor_Surface(face)
            if surface.GetType() != GeomAbs_Plane:
                explorer.Next()
                continue
            plane = surface.Plane()
            direction = plane.Axis().Direction()
            props = GProp_GProps()
            brepgprop.SurfaceProperties(face, props)
            center = props.CentreOfMass()
            area = float(props.Mass())
            if area > max(model_span * model_span * 1e-8, 1e-8):
                faces.append({
                    "index": len(faces),
                    "normal": [float(direction.X()), float(direction.Y()), float(direction.Z())],
                    "center": [float(center.X()), float(center.Y()), float(center.Z())],
                    "area": area,
                })
        except Exception:
            pass
        explorer.Next()
    candidates = []
    for left_index, left in enumerate(faces):
        for right in faces[left_index + 1:]:
            alignment = abs(_dot(left["normal"], right["normal"]))
            if alignment < 0.995:
                continue
            delta = _sub(right["center"], left["center"])
            separation = abs(_dot(delta, left["normal"]))
            if separation <= model_span * 1e-5 or separation > model_span * 0.20:
                continue
            area_ratio = min(left["area"], right["area"]) / max(left["area"], right["area"])
            if area_ratio < 0.62:
                continue
            area_coverage = min(left["area"], right["area"]) / total_surface_area
            # A material wall is supported by dominant opposite skins. Small
            # planar shoulders on a shaft may be close and parallel, but they
            # occupy only a tiny fraction of the solid's surface.
            if area_coverage < 0.12:
                continue
            normal_offset = _dot(delta, left["normal"])
            lateral = tuple(delta[index] - normal_offset * left["normal"][index] for index in range(3))
            lateral_offset = math.sqrt(_dot(lateral, lateral))
            characteristic = math.sqrt(max(min(left["area"], right["area"]), 1e-12))
            if lateral_offset > characteristic * 0.30:
                continue
            score = area_ratio * alignment / (1.0 + lateral_offset / max(characteristic, 1e-9))
            candidates.append({
                "semantic": "thin_wall.normal_thickness",
                "method": "PARALLEL_PLANAR_FACE_PAIR",
                "thickness": separation,
                "score": score,
                "area_ratio": area_ratio,
                "area_coverage": area_coverage,
                "normal_alignment": alignment,
                "lateral_offset": lateral_offset,
                "left_face": left,
                "right_face": right,
            })
    candidates.sort(key=lambda item: (-float(item["score"]), float(item["thickness"])))
    chosen = candidates[0] if candidates else None
    return {
        "resolved": chosen is not None,
        "reason": "PARALLEL_PLANAR_FACE_PAIR_RESOLVED" if chosen else "NO_QUALIFIED_PLANAR_FACE_PAIR",
        "method": METHOD,
        "model_bounds": list(bounds),
        "total_surface_area": total_surface_area,
        "planar_face_count": len(faces),
        "candidate_count": len(candidates),
        "candidate": chosen,
        "verification_status": "REVIEW_CANDIDATE" if chosen else "NO_CANDIDATE",
    }


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(float(value) for value in point[:2]) for point in edge.get("points") or []]


def _sample(points: Sequence[Sequence[float]], count: int = 9) -> list[tuple[float, float]]:
    clean = [tuple(float(value) for value in point[:2]) for point in points]
    if len(clean) <= count:
        return clean
    return [clean[round(index * (len(clean) - 1) / (count - 1))] for index in range(count)]


def _sampled_symmetric_distance(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    a, b = _sample(left), _sample(right)
    distances = [min(math.dist(point, candidate) for candidate in b) for point in a]
    distances += [min(math.dist(point, candidate) for candidate in a) for point in b]
    return statistics.median(distances)


def _resolve_reference(view: dict[str, Any], reference: str) -> tuple[dict[str, Any], int, dict[str, Any]] | None:
    if ":" not in reference:
        return None
    collection_name, index_text = reference.split(":", 1)
    collection = view.get(collection_name) or []
    try:
        index = int(index_text)
    except ValueError:
        return None
    if not 0 <= index < len(collection):
        return None
    return collection, index, collection[index]


def extract_section_boundary_thickness(view: dict[str, Any], detection: dict[str, Any]) -> dict[str, Any]:
    """Measure local normal gaps between companion section boundaries."""
    if not detection.get("resolved"):
        return {"resolved": False, "reason": "SECTION_PATTERN_NOT_RESOLVED", "method": METHOD}
    center = tuple(float(value) for value in detection.get("center") or view.get("center_hint") or (0.0, 0.0))
    x0, y0, x1, y1 = [float(value) for value in view["bbox"]]
    span = max(x1 - x0, y1 - y0, 1e-9)
    all_edges = []
    for collection_name in ("visible", "hidden"):
        for index, edge in enumerate(view.get(collection_name) or []):
            points = _edge_points(edge)
            if len(points) < 2:
                continue
            radii = [math.dist(point, center) for point in points]
            length = sum(math.dist(points[item], points[item + 1]) for item in range(len(points) - 1))
            all_edges.append({
                "reference": f"{collection_name}:{index}", "points": points,
                "radius_min": min(radii), "radius_max": max(radii), "length": length,
            })
    by_reference = {item["reference"]: item for item in all_edges}
    source_references = list(dict.fromkeys(detection.get("selected_boundary_edge_refs") or []))
    measurements = []
    for reference in source_references:
        source = by_reference.get(reference)
        if source is None or source["length"] <= span * 0.01:
            continue
        ranked = []
        source_range = source["radius_max"] - source["radius_min"]
        for candidate in all_edges:
            if candidate["reference"] == reference:
                continue
            candidate_range = candidate["radius_max"] - candidate["radius_min"]
            overlap = min(source["radius_max"], candidate["radius_max"]) - max(source["radius_min"], candidate["radius_min"])
            if overlap < 0.50 * min(source_range, candidate_range):
                continue
            length_ratio = min(source["length"], candidate["length"]) / max(source["length"], candidate["length"], 1e-12)
            if length_ratio < 0.45:
                continue
            distance = _sampled_symmetric_distance(source["points"], candidate["points"])
            if not span * 1e-4 <= distance <= span * 0.025:
                continue
            ranked.append((distance, -length_ratio, candidate))
        if not ranked:
            continue
        distance, negative_ratio, companion = min(ranked, key=lambda item: (item[0], item[1]))
        measurements.append({
            "source_reference": reference,
            "companion_reference": companion["reference"],
            "thickness": distance,
            "length_ratio": -negative_ratio,
            "source_points": [list(point) for point in source["points"]],
            "companion_points": [list(point) for point in companion["points"]],
        })
    summary = robust_summary([item["thickness"] for item in measurements])
    support_ratio = len(measurements) / max(len(source_references), 1)
    stable = (
        len(measurements) >= 6
        and support_ratio >= 0.70
        and summary["cv"] <= 0.10
        and summary["spread"] <= max(summary["median"] * 0.20, span * 5e-4)
    )
    return {
        "resolved": stable,
        "reason": "STABLE_LOCAL_BOUNDARY_PAIR_RESOLVED" if stable else "LOCAL_BOUNDARY_PAIR_UNSTABLE",
        "method": METHOD,
        "semantic": "thin_wall.local_normal_thickness",
        "context_role": "repeated_radial_element" if int(detection.get("pattern_count") or 0) >= 3 else "local_swept_wall",
        "pattern_count": int(detection.get("pattern_count") or 0),
        "source_boundary_count": len(source_references),
        "matched_boundary_count": len(measurements),
        "support_ratio": support_ratio,
        "summary": summary,
        "measurements": measurements,
        "verification_status": "AUTO_VERIFIED" if stable else "REVIEW_CANDIDATE",
    }


def aggregate_station_thickness(stations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    resolved = [item for item in stations if item.get("resolved")]
    medians = [float(item["summary"]["median"]) for item in resolved]
    summary = robust_summary(medians)
    sufficient = len(resolved) >= 3
    relative_spread = summary["spread"] / max(summary["median"], 1e-12) if medians else 0.0
    uniform = sufficient and summary["cv"] <= 0.03 and relative_spread <= 0.06
    reason = (
        "MULTI_STATION_UNIFORM_THICKNESS"
        if uniform
        else "MULTI_STATION_VARIABLE_THICKNESS"
        if sufficient
        else "INSUFFICIENT_STABLE_STATIONS"
    )
    return {
        "resolved": sufficient,
        "uniform_thickness": uniform,
        "reason": reason,
        "method": METHOD,
        "semantic": "thin_wall.normal_thickness" if uniform else "thin_wall.thickness_profile",
        "station_count": len(stations),
        "resolved_station_count": len(resolved),
        "summary": summary,
        "relative_station_spread": relative_spread,
        "station_medians": medians,
        "verification_status": "AUTO_VERIFIED" if uniform else "REVIEW_CANDIDATE",
    }
