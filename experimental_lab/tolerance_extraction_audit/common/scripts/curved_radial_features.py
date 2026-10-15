"""Detect repeated curved radial boundary pairs from an HLR projection."""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from typing import Any, Sequence

from relational_dimension_intents import (
    _projection_center,
    build_pairwise_center_relations,
    circular_difference,
    cluster_angles,
)


METHOD = "CURVED_RADIAL_BOUNDARY_PAIR_V1"


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(float(value) for value in point[:2]) for point in edge.get("points") or []]


def _signed_delta(start: float, end: float) -> float:
    return (float(end) - float(start) + 180.0) % 360.0 - 180.0


def _coefficient_of_variation(values: Sequence[float]) -> float:
    values = [float(value) for value in values]
    if not values:
        return 1.0
    mean = sum(values) / len(values)
    if abs(mean) <= 1e-9:
        return 1.0
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) / abs(mean)


def _deduplicate_candidates(candidates: Sequence[dict[str, Any]], tolerance: float = 0.20) -> list[dict[str, Any]]:
    unique_angles = cluster_angles([float(item["inner_angle"]) for item in candidates], tolerance)
    return [
        dict(min(candidates, key=lambda item: abs(_signed_delta(angle, float(item["inner_angle"])))))
        for angle in unique_angles
    ]


def _group_quality(group: Sequence[dict[str, Any]]) -> dict[str, Any]:
    angles = sorted(float(item["inner_angle"]) for item in group)
    pitches = [circular_difference(angle, angles[(index + 1) % len(angles)]) for index, angle in enumerate(angles)]
    return {
        "count": len(group),
        "mean_pitch": sum(pitches) / len(pitches) if pitches else 0.0,
        "pitch_cv": _coefficient_of_variation(pitches),
        "mean_inner_radius": sum(float(item["inner_radius"]) for item in group) / len(group),
        "mean_outer_radius": sum(float(item["outer_radius"]) for item in group) / len(group),
        "mean_tangent_offset": sum(float(item["tangent_offset"]) for item in group) / len(group),
    }


def _pair_boundary_groups(
    left: Sequence[dict[str, Any]],
    right: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    unused = set(range(len(right)))
    pairs = []
    for first in sorted(left, key=lambda item: float(item["inner_angle"])):
        if not unused:
            break
        index = min(unused, key=lambda candidate: abs(_signed_delta(first["inner_angle"], right[candidate]["inner_angle"])))
        second = right[index]
        unused.remove(index)
        delta = _signed_delta(first["inner_angle"], second["inner_angle"])
        if delta >= 0:
            start, end = first, second
        else:
            start, end = second, first
            delta = -delta
        pairs.append({
            "start_angle": float(start["inner_angle"]),
            "end_angle": float(end["inner_angle"]),
            "angle": float(delta),
            "center_angle": (float(start["inner_angle"]) + float(delta) / 2.0) % 360.0,
            "left_boundary_ref": start["edge_ref"],
            "right_boundary_ref": end["edge_ref"],
            "left_boundary_type": start["edge_type"],
            "right_boundary_type": end["edge_type"],
        })
    return sorted(pairs, key=lambda item: float(item["start_angle"]))


def detect_curved_radial_partitions(view: dict[str, Any]) -> dict[str, Any]:
    center, center_evidence = _projection_center(view)
    x0, y0, x1, y1 = [float(value) for value in view["bbox"]]
    span = max(x1 - x0, y1 - y0, 1e-9)
    visible_count = len(view.get("visible") or [])
    all_edges = (view.get("visible") or []) + (view.get("hidden") or [])
    candidates = []
    for edge_index, edge in enumerate(all_edges):
        if edge.get("type") in ("line", "circle"):
            continue
        points = _edge_points(edge)
        if len(points) < 2:
            continue
        inner = min(points, key=lambda point: math.dist(point, center))
        outer = max(points, key=lambda point: math.dist(point, center))
        inner_radius, outer_radius = math.dist(inner, center), math.dist(outer, center)
        radial_extension = outer_radius - inner_radius
        if not (span * 0.18 <= inner_radius <= span * 0.38):
            continue
        if radial_extension < span * 0.06 or outer_radius > span * 0.55:
            continue
        inner_angle = math.degrees(math.atan2(inner[1] - center[1], inner[0] - center[0])) % 360.0
        tangent_angle = math.degrees(math.atan2(outer[1] - inner[1], outer[0] - inner[0])) % 360.0
        tangent_offset = _signed_delta(inner_angle, tangent_angle)
        if not (5.0 <= abs(tangent_offset) <= 80.0):
            continue
        edge_ref = (
            f"visible:{edge_index}"
            if edge_index < visible_count
            else f"hidden:{edge_index - visible_count}"
        )
        candidates.append({
            "edge_ref": edge_ref,
            "edge_type": edge.get("type"),
            "inner_point": list(inner),
            "outer_point": list(outer),
            "inner_radius": inner_radius,
            "outer_radius": outer_radius,
            "radial_extension": radial_extension,
            "inner_angle": inner_angle,
            "tangent_offset": tangent_offset,
        })
    if not candidates:
        return {
            "resolved": False,
            "reason": "NO_CURVED_RADIAL_CANDIDATES",
            "method": METHOD,
            "center": list(center),
            "center_evidence": center_evidence,
            "candidate_line_count": 0,
        }

    radial_bin = max(span * 0.005, 1e-6)
    tangent_bin = 3.0
    raw_groups: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        raw_groups[(
            round(candidate["inner_radius"] / radial_bin),
            round(candidate["outer_radius"] / radial_bin),
            round(candidate["tangent_offset"] / tangent_bin),
        )].append(candidate)
    groups = []
    for key, raw_group in raw_groups.items():
        group = _deduplicate_candidates(raw_group)
        if len(group) < 6:
            continue
        quality = _group_quality(group)
        if quality["pitch_cv"] > 0.08:
            continue
        groups.append({"key": key, "items": group, "quality": quality})
    ranked_pairs = []
    for left, right in itertools.combinations(groups, 2):
        lq, rq = left["quality"], right["quality"]
        count_ratio = min(lq["count"], rq["count"]) / max(lq["count"], rq["count"])
        pitch_error = abs(lq["mean_pitch"] - rq["mean_pitch"]) / max(lq["mean_pitch"], rq["mean_pitch"], 1e-9)
        outer_error = abs(lq["mean_outer_radius"] - rq["mean_outer_radius"]) / max(span, 1e-9)
        if count_ratio < 0.90 or pitch_error > 0.04 or outer_error > 0.02:
            continue
        elements = _pair_boundary_groups(left["items"], right["items"])
        if len(elements) < 6:
            continue
        widths = [float(item["angle"]) for item in elements]
        mean_pitch = (lq["mean_pitch"] + rq["mean_pitch"]) / 2.0
        if max(widths) >= mean_pitch * 0.80:
            continue
        score = len(elements) * count_ratio * (1.0 - pitch_error) * (1.0 - outer_error)
        ranked_pairs.append((score, left, right, elements, mean_pitch))
    if not ranked_pairs:
        return {
            "resolved": False,
            "reason": "NO_COMPATIBLE_CURVED_BOUNDARY_PAIR",
            "method": METHOD,
            "center": list(center),
            "center_evidence": center_evidence,
            "candidate_line_count": len(candidates),
            "qualified_group_count": len(groups),
        }

    _, left, right, elements, mean_pitch = max(ranked_pairs, key=lambda item: item[0])
    for index, element in enumerate(elements, start=1):
        element.update({
            "feature_id": f"curved_radial_element_{index:03d}",
            "semantic": "curved_radial_partition.element_angle",
            "verification_status": "REVIEW_CANDIDATE",
        })
    gaps = []
    for index, element in enumerate(elements):
        following = elements[(index + 1) % len(elements)]
        gap_angle = circular_difference(element["end_angle"], following["start_angle"])
        gaps.append({
            "feature_id": f"curved_radial_gap_{index + 1:03d}",
            "semantic": "curved_radial_partition.gap_angle",
            "start_angle": element["end_angle"],
            "end_angle": following["start_angle"],
            "angle": gap_angle,
            "center_angle": (element["end_angle"] + gap_angle / 2.0) % 360.0,
            "left_boundary_ref": element["right_boundary_ref"],
            "right_boundary_ref": following["left_boundary_ref"],
            "verification_status": "REVIEW_CANDIDATE",
        })
    centers = sorted(float(item["center_angle"]) for item in elements)
    pitches = [
        {
            "start_angle": center_angle,
            "end_angle": centers[(index + 1) % len(centers)],
            "angle": circular_difference(center_angle, centers[(index + 1) % len(centers)]),
            "semantic": "curved_radial_partition.center_pitch_angle",
        }
        for index, center_angle in enumerate(centers)
    ]
    boundary_refs = [
        reference
        for element in elements
        for reference in (element["left_boundary_ref"], element["right_boundary_ref"])
    ]
    return {
        "resolved": True,
        "reason": "CURVED_BOUNDARY_PAIR_RESOLVED",
        "method": METHOD,
        "center": list(center),
        "center_evidence": center_evidence,
        "boundary_angles": sorted([angle for item in elements for angle in (item["start_angle"], item["end_angle"])]),
        "split_threshold_degrees": mean_pitch / 2.0,
        "split_ratio": (
            sum(float(item["angle"]) for item in gaps) / max(len(gaps), 1)
        ) / max(sum(float(item["angle"]) for item in elements) / max(len(elements), 1), 1e-9),
        "elements": elements,
        "gaps": gaps,
        "center_pitches": pitches,
        "pairwise_center_relations": build_pairwise_center_relations(centers),
        "candidate_line_count": len(candidates),
        "selected_line_count": len(boundary_refs),
        "selected_boundary_edge_refs": boundary_refs,
        "selected_curve_types": sorted({item["left_boundary_type"] for item in elements} | {item["right_boundary_type"] for item in elements}),
        "qualified_group_count": len(groups),
        "reference_radius": (
            left["quality"]["mean_inner_radius"] + right["quality"]["mean_inner_radius"]
        ) / 2.0,
        "projected_edge_identity_persisted": False,
        "verification_status": "REVIEW_CANDIDATE",
    }
