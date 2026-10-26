"""Adaptive repeated curved-radial detector for experiment 15.

Unlike the V1 detector, this variant does not require a projected hub circle
and does not bind the inner endpoint to a fixed fraction of the view bbox.
The fallback centre is accepted only when repeated edge families independently
provide rotational-periodicity evidence.
"""

from __future__ import annotations

import itertools
import math
import statistics
from typing import Any, Sequence

from curved_radial_features import (  # Reuse stable relation construction only.
    _coefficient_of_variation, _deduplicate_candidates, _edge_points,
    _group_quality, _pair_boundary_groups,
)
from relational_dimension_intents import (
    _projection_center, build_pairwise_center_relations, circular_difference,
)


METHOD = "ADAPTIVE_CURVED_RADIAL_BOUNDARY_PAIR_V2"


def _signed_delta(start: float, end: float) -> float:
    return (float(end) - float(start) + 180.0) % 360.0 - 180.0


def _candidate_distance(left: dict[str, Any], right: dict[str, Any], radius: float) -> float:
    inner = abs(float(left["inner_radius"]) - float(right["inner_radius"])) / max(radius * 0.035, 1e-9)
    outer = abs(float(left["outer_radius"]) - float(right["outer_radius"])) / max(radius * 0.045, 1e-9)
    tangent = abs(_signed_delta(float(left["tangent_offset"]), float(right["tangent_offset"]))) / 8.0
    return math.sqrt(inner * inner + outer * outer + tangent * tangent)


def _cluster_candidates(candidates: Sequence[dict[str, Any]], radius: float) -> list[list[dict[str, Any]]]:
    """Greedy scale-normalized clustering with centroid re-evaluation."""
    clusters: list[list[dict[str, Any]]] = []
    for candidate in sorted(candidates, key=lambda item: -float(item["radial_extension"])):
        representatives = [
            {
                "inner_radius": sum(float(item["inner_radius"]) for item in cluster) / len(cluster),
                "outer_radius": sum(float(item["outer_radius"]) for item in cluster) / len(cluster),
                "tangent_offset": sum(float(item["tangent_offset"]) for item in cluster) / len(cluster),
            }
            for cluster in clusters
        ]
        distances = [_candidate_distance(candidate, representative, radius) for representative in representatives]
        if distances and min(distances) <= 1.0:
            clusters[distances.index(min(distances))].append(candidate)
        else:
            clusters.append([candidate])
    return clusters


def _periodic_fits(group: Sequence[dict[str, Any]], maximum_count: int = 160) -> list[dict[str, Any]]:
    """Infer a fundamental repeat count from one or more edge sub-families."""
    angles = sorted(float(item["inner_angle"]) % 360.0 for item in group)
    if len(angles) < 6:
        return []
    fits = []
    for stride in range(1, min(6, len(angles) - 1) + 1):
        deltas = [circular_difference(angle, angles[(index + stride) % len(angles)]) for index, angle in enumerate(angles)]
        pitch = statistics.median(deltas)
        if pitch <= 1e-6:
            continue
        count = round(360.0 / pitch)
        if not (6 <= count <= maximum_count):
            continue
        mad = statistics.median(abs(value - pitch) for value in deltas)
        coverage = len(angles) / max(count * stride, 1)
        if not (0.72 <= coverage <= 1.28):
            continue
        residual = mad / pitch + abs(coverage - 1.0)
        fits.append({"stride": stride, "pattern_count": count, "pitch": pitch, "coverage": coverage, "residual": residual})
    return sorted(fits, key=lambda item: (item["residual"], -item["stride"]))


def _periodic_fit(group: Sequence[dict[str, Any]], maximum_count: int = 160) -> dict[str, Any] | None:
    fits = _periodic_fits(group, maximum_count=maximum_count)
    return fits[0] if fits else None


def _reduce_to_periodic_slots(group: Sequence[dict[str, Any]], fit: dict[str, Any]) -> list[dict[str, Any]]:
    pitch = 360.0 / int(fit["pattern_count"])
    seeds = [float(item["inner_angle"]) % pitch for item in group]
    phase = min(
        seeds,
        key=lambda candidate: sum(abs(_signed_delta(candidate, float(item["inner_angle"]) % pitch)) for item in group),
    )
    selected = []
    for index in range(int(fit["pattern_count"])):
        target = (phase + index * pitch) % 360.0
        nearest = min(group, key=lambda item: abs(_signed_delta(target, float(item["inner_angle"]))))
        if abs(_signed_delta(target, float(nearest["inner_angle"]))) <= pitch * 0.28:
            selected.append(dict(nearest))
    unique = {item["edge_ref"]: item for item in selected}
    return sorted(unique.values(), key=lambda item: float(item["inner_angle"]))


def detect_adaptive_curved_radial_partitions(view: dict[str, Any]) -> dict[str, Any]:
    if view.get("center_hint") is not None:
        center = tuple(float(value) for value in view["center_hint"][:2])
        center_evidence = {
            "method": "BND_BOX_AXIS_SECTION_CENTER",
            "confidence": float(view.get("center_hint_confidence") or 0.80),
        }
    else:
        center, center_evidence = _projection_center(view)
    all_edges = (view.get("visible") or []) + (view.get("hidden") or [])
    visible_count = len(view.get("visible") or [])
    all_points = [point for edge in all_edges for point in _edge_points(edge)]
    if not all_points:
        return {"resolved": False, "reason": "NO_PROJECTED_EDGES", "method": METHOD}
    outer_reference = max(math.dist(point, center) for point in all_points)
    candidates = []
    for edge_index, edge in enumerate(all_edges):
        if edge.get("type") in ("line", "circle"):
            continue
        points = _edge_points(edge)
        if len(points) < 3:
            continue
        inner = min(points, key=lambda point: math.dist(point, center))
        outer = max(points, key=lambda point: math.dist(point, center))
        inner_radius, outer_radius = math.dist(inner, center), math.dist(outer, center)
        extension = outer_radius - inner_radius
        if not (outer_reference * 0.08 <= inner_radius <= outer_reference * 0.88):
            continue
        # Vendor STEP exports commonly split one blade boundary into many
        # short B-spline edges. Periodicity is therefore detected on a stable
        # radial band first; the selected edge is traced outward later by the
        # profile semantic extractor. Requiring a full-span edge here loses
        # otherwise clear 59/61/72-blade impellers.
        if extension < outer_reference * 0.004 or outer_radius > outer_reference * 1.02:
            continue
        inner_angle = math.degrees(math.atan2(inner[1] - center[1], inner[0] - center[0])) % 360.0
        sample_index = max(1, min(len(points) - 1, round((len(points) - 1) * 0.12)))
        oriented = points if math.dist(points[0], center) <= math.dist(points[-1], center) else list(reversed(points))
        vector = (oriented[sample_index][0] - oriented[0][0], oriented[sample_index][1] - oriented[0][1])
        tangent_angle = math.degrees(math.atan2(vector[1], vector[0])) % 360.0
        tangent_offset = _signed_delta(inner_angle, tangent_angle)
        if not (3.0 <= abs(tangent_offset) <= 87.0):
            continue
        candidates.append({
            "edge_ref": f"visible:{edge_index}" if edge_index < visible_count else f"hidden:{edge_index - visible_count}",
            "edge_type": edge.get("type"),
            "inner_point": list(inner),
            "outer_point": list(outer),
            "inner_radius": inner_radius,
            "outer_radius": outer_radius,
            "radial_extension": extension,
            "inner_angle": inner_angle,
            "tangent_offset": tangent_offset,
        })
    raw_clusters = _cluster_candidates(candidates, outer_reference)
    raw_group_diagnostics = []
    groups = []
    for raw in raw_clusters:
        group = _deduplicate_candidates(raw, tolerance=0.16)
        quality = _group_quality(group) if group else {}
        raw_group_diagnostics.append({
            "raw_count": len(raw),
            "deduplicated_count": len(group),
            "pitch_cv": float(quality.get("pitch_cv") or 1.0),
            "mean_pitch": float(quality.get("mean_pitch") or 0.0),
        })
        if len(group) < 6:
            continue
        fit = _periodic_fit(group)
        if quality["pitch_cv"] > 0.16 and (fit is None or fit["residual"] > 0.24):
            continue
        groups.append({"items": group, "quality": quality, "periodic_fit": fit})
    ranked = []
    for left, right in itertools.combinations(groups, 2):
        lq, rq = left["quality"], right["quality"]
        count_ratio = min(lq["count"], rq["count"]) / max(lq["count"], rq["count"])
        pitch_error = abs(lq["mean_pitch"] - rq["mean_pitch"]) / max(lq["mean_pitch"], rq["mean_pitch"], 1e-9)
        outer_error = abs(lq["mean_outer_radius"] - rq["mean_outer_radius"]) / max(outer_reference, 1e-9)
        if count_ratio < 0.86 or pitch_error > 0.08 or outer_error > 0.06:
            continue
        elements = _pair_boundary_groups(left["items"], right["items"])
        widths = [float(item["angle"]) for item in elements]
        mean_pitch = (lq["mean_pitch"] + rq["mean_pitch"]) / 2.0
        if len(elements) < 6 or not widths or max(widths) >= mean_pitch * 0.88:
            continue
        score = len(elements) * count_ratio * (1.0 - pitch_error) * (1.0 - outer_error)
        ranked.append((score, left, right, elements, mean_pitch))
    if not ranked and groups:
        periodic_groups = [item for item in groups if item.get("periodic_fit")]
        if periodic_groups:
            chosen = min(periodic_groups, key=lambda item: (item["periodic_fit"]["residual"], -item["periodic_fit"]["pattern_count"]))
            fit = chosen["periodic_fit"]
            representatives = _reduce_to_periodic_slots(chosen["items"], fit)
            if len(representatives) >= int(fit["pattern_count"]) * 0.72:
                elements = [{
                    "start_angle": float(item["inner_angle"]),
                    "end_angle": float(item["inner_angle"]),
                    "angle": 0.0,
                    "center_angle": float(item["inner_angle"]),
                    "left_boundary_ref": item["edge_ref"],
                    "right_boundary_ref": item["edge_ref"],
                    "left_boundary_type": item["edge_type"],
                    "right_boundary_type": item["edge_type"],
                    "feature_id": f"adaptive_single_boundary_{index:03d}",
                    "semantic": "curved_radial_partition.single_boundary_family",
                    "verification_status": "REVIEW_CANDIDATE",
                } for index, item in enumerate(representatives, start=1)]
                centers = sorted(float(item["center_angle"]) for item in elements)
                pitches = [{
                    "start_angle": angle,
                    "end_angle": centers[(index + 1) % len(centers)],
                    "angle": circular_difference(angle, centers[(index + 1) % len(centers)]),
                    "semantic": "curved_radial_partition.center_pitch_angle",
                } for index, angle in enumerate(centers)]
                refs = [item["left_boundary_ref"] for item in elements]
                evidence = dict(center_evidence)
                if evidence.get("method") != "CONCENTRIC_PROJECTED_CIRCLES":
                    evidence = {"method": "ROTATIONAL_EDGE_PERIODICITY", "confidence": max(0.0, min(0.88, 1.0 - fit["residual"]))}
                return {
                    "resolved": True,
                    "reason": "ADAPTIVE_SINGLE_BOUNDARY_PERIODICITY_RESOLVED",
                    "method": METHOD,
                    "center": list(center),
                    "center_evidence": evidence,
                    "pattern_count": int(fit["pattern_count"]),
                    "boundary_angles": centers,
                    "split_threshold_degrees": float(fit["pitch"]) / 2.0,
                    "split_ratio": 2.0,
                    "elements": elements,
                    "gaps": [{"angle": item["angle"]} for item in pitches],
                    "center_pitches": pitches,
                    "pairwise_center_relations": build_pairwise_center_relations(centers),
                    "candidate_line_count": len(candidates),
                    "selected_line_count": len(refs),
                    "selected_boundary_edge_refs": refs,
                    "selected_curve_types": sorted({item["left_boundary_type"] for item in elements}),
                    "qualified_group_count": len(groups),
                    "reference_radius": chosen["quality"]["mean_inner_radius"],
                    "outer_reference_radius": outer_reference,
                    "projected_edge_identity_persisted": False,
                    "verification_status": "REVIEW_CANDIDATE",
                    "periodic_fit": fit,
                    "pattern_count_candidates": sorted({int(item["pattern_count"]) for item in _periodic_fits(chosen["items"])}),
                }
    if not ranked:
        return {
            "resolved": False,
            "reason": "NO_ADAPTIVE_PERIODIC_BOUNDARY_PAIR",
            "method": METHOD,
            "center": list(center),
            "center_evidence": center_evidence,
            "candidate_line_count": len(candidates),
            "qualified_group_count": len(groups),
            "largest_raw_groups": sorted(
                raw_group_diagnostics,
                key=lambda item: (-item["deduplicated_count"], item["pitch_cv"]),
            )[:8],
            "outer_reference_radius": outer_reference,
        }
    _, left, right, elements, mean_pitch = max(ranked, key=lambda item: item[0])
    element_angle_records = [{"inner_angle": item["center_angle"]} for item in elements]
    element_fits = _periodic_fits(element_angle_records)
    element_fit = element_fits[0] if element_fits else None
    pattern_count = len(elements)
    if element_fit and element_fit["pattern_count"] < len(elements) * 0.80 and element_fit["residual"] <= 0.18:
        pattern_count = int(element_fit["pattern_count"])
        pitch = 360.0 / pattern_count
        phases = [float(item["center_angle"]) % pitch for item in elements]
        phase = min(
            phases,
            key=lambda candidate: sum(abs(_signed_delta(candidate, float(item["center_angle"]) % pitch)) for item in elements),
        )
        reduced = []
        for slot in range(pattern_count):
            target = (phase + slot * pitch) % 360.0
            nearest = min(elements, key=lambda item: abs(_signed_delta(target, float(item["center_angle"]))))
            if abs(_signed_delta(target, float(nearest["center_angle"]))) <= pitch * 0.32:
                reduced.append(nearest)
        elements = list({(item["left_boundary_ref"], item["right_boundary_ref"]): item for item in reduced}.values())
        elements.sort(key=lambda item: float(item["start_angle"]))
    for index, element in enumerate(elements, start=1):
        element.update({
            "feature_id": f"adaptive_curved_radial_element_{index:03d}",
            "semantic": "curved_radial_partition.element_angle",
            "verification_status": "REVIEW_CANDIDATE",
        })
    gaps = []
    for index, element in enumerate(elements):
        following = elements[(index + 1) % len(elements)]
        gap = circular_difference(element["end_angle"], following["start_angle"])
        gaps.append({
            "feature_id": f"adaptive_curved_radial_gap_{index + 1:03d}",
            "semantic": "curved_radial_partition.gap_angle",
            "start_angle": element["end_angle"],
            "end_angle": following["start_angle"],
            "angle": gap,
            "center_angle": (element["end_angle"] + gap / 2.0) % 360.0,
            "left_boundary_ref": element["right_boundary_ref"],
            "right_boundary_ref": following["left_boundary_ref"],
            "verification_status": "REVIEW_CANDIDATE",
        })
    centers = sorted(float(item["center_angle"]) for item in elements)
    pitches = [{
        "start_angle": angle,
        "end_angle": centers[(index + 1) % len(centers)],
        "angle": circular_difference(angle, centers[(index + 1) % len(centers)]),
        "semantic": "curved_radial_partition.center_pitch_angle",
    } for index, angle in enumerate(centers)]
    references = [ref for item in elements for ref in (item["left_boundary_ref"], item["right_boundary_ref"])]
    periodicity_cv = _coefficient_of_variation([item["angle"] for item in pitches])
    evidence = dict(center_evidence)
    if evidence.get("method") != "CONCENTRIC_PROJECTED_CIRCLES":
        evidence = {
            "method": "ROTATIONAL_EDGE_PERIODICITY",
            "confidence": max(0.0, min(0.92, 1.0 - periodicity_cv)),
            "supporting_edge_family_count": 2,
        }
    return {
        "resolved": True,
        "reason": "ADAPTIVE_PERIODIC_BOUNDARY_PAIR_RESOLVED",
        "method": METHOD,
        "center": list(center),
        "center_evidence": evidence,
        "pattern_count": pattern_count,
        "pattern_count_candidates": sorted({int(item["pattern_count"]) for item in element_fits}),
        "periodic_fit": element_fit,
        "boundary_angles": sorted(angle for item in elements for angle in (item["start_angle"], item["end_angle"])),
        "split_threshold_degrees": mean_pitch / 2.0,
        "split_ratio": (sum(item["angle"] for item in gaps) / len(gaps)) / max(sum(item["angle"] for item in elements) / len(elements), 1e-9),
        "elements": elements,
        "gaps": gaps,
        "center_pitches": pitches,
        "pairwise_center_relations": build_pairwise_center_relations(centers),
        "candidate_line_count": len(candidates),
        "selected_line_count": len(references),
        "selected_boundary_edge_refs": references,
        "selected_curve_types": sorted({item["left_boundary_type"] for item in elements} | {item["right_boundary_type"] for item in elements}),
        "qualified_group_count": len(groups),
        "reference_radius": (left["quality"]["mean_inner_radius"] + right["quality"]["mean_inner_radius"]) / 2.0,
        "outer_reference_radius": outer_reference,
        "projected_edge_identity_persisted": False,
        "verification_status": "REVIEW_CANDIDATE",
    }
