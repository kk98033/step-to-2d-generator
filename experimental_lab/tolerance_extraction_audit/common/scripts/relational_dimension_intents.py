"""Geometry-only relational dimension intents for experiment 11.

This module intentionally contains no model-name rules and never derives a
radial angle from ``360 / count``.  Hub angles are measured from detected STEP
projection boundaries; rectangular hole patterns are measured from cylindrical
BRep faces.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from typing import Any, Sequence

from auto_2d_drawing.drawing_projection import project_point

try:
    from .annular_composite_features import _best_views, extract_cylindrical_faces
except ImportError:  # Direct script execution keeps the scripts directory on sys.path.
    from annular_composite_features import _best_views, extract_cylindrical_faces


def circular_difference(start: float, end: float) -> float:
    return (float(end) - float(start)) % 360.0


def cluster_angles(angles: Sequence[float], tolerance_degrees: float = 0.35) -> list[float]:
    """Deduplicate circular angles while correctly merging the 0/360 seam."""
    values = sorted(float(value) % 360.0 for value in angles)
    if not values:
        return []
    groups: list[list[float]] = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] <= tolerance_degrees:
            groups[-1].append(value)
        else:
            groups.append([value])
    if len(groups) > 1 and circular_difference(groups[-1][-1], groups[0][0]) <= tolerance_degrees:
        seam = groups.pop() + [value + 360.0 for value in groups.pop(0)]
        groups.insert(0, seam)
    return sorted((sum(group) / len(group)) % 360.0 for group in groups)


def classify_radial_intervals(boundary_angles: Sequence[float]) -> dict[str, Any]:
    """Classify narrow material elements and the gaps between their boundaries."""
    angles = cluster_angles(boundary_angles)
    if len(angles) < 6:
        return {"resolved": False, "reason": "TOO_FEW_RADIAL_BOUNDARIES", "boundary_angles": angles}
    intervals = []
    for index, start in enumerate(angles):
        end = angles[(index + 1) % len(angles)]
        span = circular_difference(start, end)
        intervals.append({"start_angle": start, "end_angle": end, "angle": span})

    positive = sorted(item["angle"] for item in intervals if item["angle"] > 0.05)
    if len(positive) < 2:
        return {"resolved": False, "reason": "DEGENERATE_INTERVALS", "boundary_angles": angles}
    split_candidates = []
    for left, right in zip(positive, positive[1:]):
        if left > 0:
            split_candidates.append((right / left, math.sqrt(left * right)))
    ratio, threshold = max(split_candidates, default=(1.0, 0.0))
    if ratio < 1.8:
        return {
            "resolved": False,
            "reason": "NO_SEPARABLE_ELEMENT_AND_GAP_WIDTHS",
            "boundary_angles": angles,
            "intervals": intervals,
        }

    elements, gaps = [], []
    for item in intervals:
        enriched = dict(item)
        enriched["center_angle"] = (item["start_angle"] + item["angle"] / 2.0) % 360.0
        enriched["semantic"] = (
            "hub_radial_partition.element_angle"
            if item["angle"] <= threshold
            else "hub_radial_partition.gap_angle"
        )
        (elements if item["angle"] <= threshold else gaps).append(enriched)
    if len(elements) < 3 or len(gaps) < 3:
        return {
            "resolved": False,
            "reason": "INSUFFICIENT_REPEATED_ELEMENT_GAP_EVIDENCE",
            "boundary_angles": angles,
            "intervals": intervals,
        }

    centres = sorted(item["center_angle"] for item in elements)
    pitches = [
        {
            "start_angle": centre,
            "end_angle": centres[(index + 1) % len(centres)],
            "angle": circular_difference(centre, centres[(index + 1) % len(centres)]),
            "semantic": "hub_radial_partition.center_pitch_angle",
        }
        for index, centre in enumerate(centres)
    ]
    pairwise = build_pairwise_center_relations(centres)
    return {
        "resolved": True,
        "boundary_angles": angles,
        "split_threshold_degrees": threshold,
        "split_ratio": ratio,
        "elements": elements,
        "gaps": gaps,
        "center_pitches": pitches,
        "pairwise_center_relations": pairwise,
    }


def build_pairwise_center_relations(center_angles: Sequence[float]) -> list[dict[str, Any]]:
    """Enumerate actual centre-line relations without assuming uniform spacing."""
    values = sorted(float(value) % 360.0 for value in center_angles)
    relations = []
    for left_index, right_index in itertools.combinations(range(len(values)), 2):
        directed = circular_difference(values[left_index], values[right_index])
        minor = min(directed, 360.0 - directed)
        relations.append({
            "left_element_index": left_index,
            "right_element_index": right_index,
            "start_angle": values[left_index],
            "end_angle": values[right_index],
            "angle": minor,
            "semantic": "hub_radial_partition.center_to_center_angle",
        })
    return relations


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(point) for point in edge.get("points") or []]


def _projection_center(view: dict[str, Any]) -> tuple[tuple[float, float], dict[str, Any]]:
    x0, y0, x1, y1 = [float(value) for value in view["bbox"]]
    span = max(x1 - x0, y1 - y0, 1e-9)
    circles = [edge for edge in (view.get("visible") or []) + (view.get("hidden") or []) if edge.get("type") == "circle"]
    clusters: list[dict[str, Any]] = []
    tolerance = span * 0.003
    for edge in circles:
        center = tuple(float(value) for value in edge["center"])
        found = next((item for item in clusters if math.dist(center, item["center"]) <= tolerance), None)
        if found is None:
            found = {"center": center, "edges": []}
            clusters.append(found)
        found["edges"].append(edge)
        count = len(found["edges"])
        found["center"] = (
            (found["center"][0] * (count - 1) + center[0]) / count,
            (found["center"][1] * (count - 1) + center[1]) / count,
        )
    if not clusters:
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0), {"method": "BBOX_FALLBACK", "confidence": 0.25}
    winner = max(clusters, key=lambda item: (len(item["edges"]), len({round(float(edge["radius"]), 2) for edge in item["edges"]})))
    return winner["center"], {
        "method": "CONCENTRIC_PROJECTED_CIRCLES",
        "confidence": min(1.0, 0.55 + len(winner["edges"]) / max(len(circles), 1)),
        "supporting_circle_count": len(winner["edges"]),
        "supporting_radii": sorted({round(float(edge["radius"]), 4) for edge in winner["edges"]}),
    }


def detect_hub_radial_partitions(view: dict[str, Any]) -> dict[str, Any]:
    """Detect paired radial boundaries from one end-on HLR projection."""
    center, center_evidence = _projection_center(view)
    x0, y0, x1, y1 = [float(value) for value in view["bbox"]]
    span = min(x1 - x0, y1 - y0)
    candidates = []
    all_edges = (view.get("visible") or []) + (view.get("hidden") or [])
    visible_count = len(view.get("visible") or [])
    for edge_index, edge in enumerate(all_edges):
        if edge.get("type") != "line":
            continue
        p0, p1 = (tuple(float(value) for value in edge[key]) for key in ("p1", "p2"))
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < span * 0.018 or length > span * 0.16:
            continue
        midpoint = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
        radius = math.dist(midpoint, center)
        if radius < span * 0.06 or radius > span * 0.30:
            continue
        line_distance = abs(dx * (center[1] - p0[1]) - dy * (center[0] - p0[0])) / max(length, 1e-9)
        radial_alignment = abs(
            (dx * (midpoint[0] - center[0]) + dy * (midpoint[1] - center[1]))
            / max(length * radius, 1e-9)
        )
        if line_distance > span * 0.012 or radial_alignment < 0.965:
            continue
        angle = math.degrees(math.atan2(midpoint[1] - center[1], midpoint[0] - center[0])) % 360.0
        candidates.append({
            "angle": angle,
            "mid_radius": radius,
            "length": length,
            "line_distance": line_distance,
            "radial_alignment": radial_alignment,
            "edge_ref": f"{'visible' if edge_index < visible_count else 'hidden'}:{edge_index if edge_index < visible_count else edge_index - visible_count}",
            "p0": list(p0),
            "p1": list(p1),
        })

    radial_bin = max(span * 0.012, 1e-6)
    length_bin = max(span * 0.010, 1e-6)
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[(round(candidate["mid_radius"] / radial_bin), round(candidate["length"] / length_bin))].append(candidate)
    ranked = []
    for key, group in groups.items():
        unique = cluster_angles([item["angle"] for item in group])
        mean_alignment = sum(item["radial_alignment"] for item in group) / len(group)
        ranked.append((len(unique) * mean_alignment, len(unique), -sum(item["line_distance"] for item in group), key, group, unique))
    if not ranked:
        return {
            "resolved": False,
            "reason": "NO_RADIAL_BOUNDARY_CLUSTER",
            "center": list(center),
            "center_evidence": center_evidence,
            "candidate_line_count": len(candidates),
        }
    _, _, _, group_key, group, angles = max(ranked)
    classification = classify_radial_intervals(angles)
    angle_to_edge = {}
    for angle in angles:
        angle_to_edge[str(round(angle, 6))] = min(
            group,
            key=lambda item: min(circular_difference(angle, item["angle"]), circular_difference(item["angle"], angle)),
        )["edge_ref"]
    for element_index, element in enumerate(classification.get("elements") or [], start=1):
        element["feature_id"] = f"hub_radial_element_{element_index:03d}"
        element["left_boundary_ref"] = angle_to_edge.get(str(round(float(element["start_angle"]), 6)))
        element["right_boundary_ref"] = angle_to_edge.get(str(round(float(element["end_angle"]), 6)))
        element["verification_status"] = "REVIEW_CANDIDATE"
    for gap_index, gap in enumerate(classification.get("gaps") or [], start=1):
        gap["feature_id"] = f"hub_radial_gap_{gap_index:03d}"
        gap["left_boundary_ref"] = angle_to_edge.get(str(round(float(gap["start_angle"]), 6)))
        gap["right_boundary_ref"] = angle_to_edge.get(str(round(float(gap["end_angle"]), 6)))
        gap["verification_status"] = "REVIEW_CANDIDATE"
    classification.update({
        "center": list(center),
        "center_evidence": center_evidence,
        "candidate_line_count": len(candidates),
        "selected_cluster_key": list(group_key),
        "selected_line_count": len(group),
        "boundary_edge_refs": angle_to_edge,
        "reference_radius": sum(item["mid_radius"] for item in group) / len(group),
        "projected_edge_identity_persisted": False,
        "verification_status": "REVIEW_CANDIDATE" if classification.get("resolved") else "UNRESOLVED",
    })
    return classification


def _canonical_axis(axis: Sequence[float]) -> tuple[float, float, float]:
    values = [float(value) for value in axis[:3]]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    values = [value / norm for value in values]
    for value in values:
        if abs(value) > 1e-9:
            if value < 0:
                values = [-item for item in values]
            break
    return tuple(values)  # type: ignore[return-value]


def _axis_key(axis: Sequence[float], radius: float, radius_tolerance: float) -> tuple[int, int, int, int]:
    canonical = _canonical_axis(axis)
    return (*[round(value * 1000) for value in canonical], round(float(radius) / radius_tolerance))


def detect_rectangular_hole_pattern(shape: Any) -> dict[str, Any]:
    """Find a same-diameter cylindrical-face group occupying four rectangle corners."""
    cylinders = [item for item in extract_cylindrical_faces(shape) if item["material_relation"] == "INNER_MATERIAL_BOUNDARY"]
    if not cylinders:
        return {"resolved": False, "reason": "NO_INNER_CYLINDRICAL_FACES"}
    radii = [float(item["radius"]) for item in cylinders]
    radius_tolerance = max(0.01, (max(radii) - min(radii)) * 0.002)
    groups: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for cylinder in cylinders:
        groups[_axis_key(cylinder["axis"], cylinder["radius"], radius_tolerance)].append(cylinder)

    patterns = []
    for group in groups.values():
        if len(group) < 4:
            continue
        view_name, _ = _best_views(group[0]["axis"])
        unique = []
        for cylinder in group:
            point = project_point(cylinder["center"], view_name)
            if any(math.dist(point, item["point"]) <= max(0.02, cylinder["radius"] * 0.05) for item in unique):
                continue
            unique.append({"point": point, "cylinder": cylinder})
        if len(unique) < 4:
            continue
        for combo in itertools.combinations(unique, 4):
            xs = [item["point"][0] for item in combo]
            ys = [item["point"][1] for item in combo]
            width, height = max(xs) - min(xs), max(ys) - min(ys)
            if width <= 4 * group[0]["radius"] or height <= 4 * group[0]["radius"]:
                continue
            corners = [(min(xs), min(ys)), (min(xs), max(ys)), (max(xs), min(ys)), (max(xs), max(ys))]
            residual = sum(min(math.dist(corner, item["point"]) for item in combo) for corner in corners) / 4.0
            normalised = residual / max(math.hypot(width, height), 1e-9)
            if normalised > 0.02:
                continue
            patterns.append((width * height * (1.0 - normalised), normalised, view_name, combo, width, height))
    if not patterns:
        return {"resolved": False, "reason": "NO_RECTANGULAR_SAME_DIAMETER_HOLE_GROUP", "cylindrical_face_count": len(cylinders)}
    _, residual, view_name, combo, width, height = max(patterns, key=lambda item: item[0])
    ordered = sorted(combo, key=lambda item: (item["point"][1], item["point"][0]))
    radius = sum(item["cylinder"]["radius"] for item in combo) / 4.0
    confidence = max(0.0, min(1.0, 0.96 - residual * 4.0))
    face_refs = [f"solid:{item['cylinder']['solid_index']}/face:{item['cylinder']['face_index']}" for item in ordered]
    return {
        "resolved": True,
        "view": view_name,
        "axis": list(_canonical_axis(combo[0]["cylinder"]["axis"])),
        "diameter": radius * 2.0,
        "horizontal_center_distance": width,
        "vertical_center_distance": height,
        "centers": [list(item["point"]) for item in ordered],
        "topology_refs": face_refs,
        "rectangle_residual_ratio": residual,
        "verification_status": "AUTO_VERIFIED" if confidence >= 0.90 else "REVIEW_CANDIDATE",
        "confidence": confidence,
        "intents": [
            {"semantic": "mounting_hole_pattern.hole_diameter", "nominal_value": radius * 2.0, "unit": "mm", "topology_refs": face_refs},
            {"semantic": "mounting_hole_pattern.horizontal_center_distance", "nominal_value": width, "unit": "mm", "topology_refs": face_refs},
            {"semantic": "mounting_hole_pattern.vertical_center_distance", "nominal_value": height, "unit": "mm", "topology_refs": face_refs},
        ],
    }
