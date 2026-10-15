"""Unknown-model trigger and selection policy for radial angular dimensions.

The policy consumes measured projection relations.  Model names, part numbers,
blade counts and fixed 3D coordinates are deliberately absent.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from relational_dimension_intents import detect_hub_radial_partitions
from curved_radial_features import detect_curved_radial_partitions


METHOD = "UNKNOWN_MODEL_RADIAL_ANGLE_ORCHESTRATOR_V1"


def _coefficient_of_variation(values: Sequence[float]) -> float:
    values = [float(value) for value in values]
    if not values:
        return 1.0
    mean = sum(values) / len(values)
    if abs(mean) <= 1e-9:
        return 1.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / abs(mean)


def cluster_relation_values(
    relations: Sequence[dict[str, Any]],
    tolerance_degrees: float = 0.20,
) -> list[dict[str, Any]]:
    """Group repeated measured angles without generating synthetic geometry."""
    groups: list[list[dict[str, Any]]] = []
    for relation in sorted(relations, key=lambda item: float(item["angle"])):
        value = float(relation["angle"])
        group = next(
            (
                candidate
                for candidate in groups
                if abs(value - sum(float(item["angle"]) for item in candidate) / len(candidate))
                <= tolerance_degrees
            ),
            None,
        )
        if group is None:
            groups.append([relation])
        else:
            group.append(relation)
    return [
        {
            "mean_angle": sum(float(item["angle"]) for item in group) / len(group),
            "support": len(group),
            "relations": [dict(item) for item in group],
        }
        for group in groups
    ]


def assess_radial_trigger(result: dict[str, Any]) -> dict[str, Any]:
    """Score whether a projection contains credible repeated radial partitions."""
    elements = result.get("elements") or []
    gaps = result.get("gaps") or []
    boundaries = result.get("boundary_angles") or []
    centre = result.get("center_evidence") or {}
    pitches = result.get("center_pitches") or []
    selected_count = int(result.get("selected_line_count") or 0)
    candidate_count = int(result.get("candidate_line_count") or 0)

    checks = {
        "detector_resolved": bool(result.get("resolved")),
        "concentric_center_evidence": centre.get("method") == "CONCENTRIC_PROJECTED_CIRCLES",
        "at_least_three_repeated_elements": len(elements) >= 3,
        "balanced_element_gap_count": len(elements) == len(gaps) and len(elements) > 0,
        "boundary_accounting_complete": len(boundaries) == len(elements) + len(gaps),
        "measured_center_relations_available": len(pitches) >= 3,
        "boundary_topology_references_available": bool(elements) and all(
            item.get("left_boundary_ref") and item.get("right_boundary_ref") for item in elements
        ),
    }
    element_cv = _coefficient_of_variation([float(item["angle"]) for item in elements])
    centre_confidence = float(centre.get("confidence") or 0.0)
    purity = selected_count / candidate_count if candidate_count else 0.0
    split_ratio = float(result.get("split_ratio") or 0.0)

    score = 0.0
    score += 0.25 if checks["detector_resolved"] else 0.0
    score += 0.18 if checks["concentric_center_evidence"] else 0.0
    score += 0.12 if checks["balanced_element_gap_count"] else 0.0
    score += 0.10 if checks["boundary_accounting_complete"] else 0.0
    score += 0.10 if checks["measured_center_relations_available"] else 0.0
    score += 0.10 if checks["boundary_topology_references_available"] else 0.0
    score += 0.08 * max(0.0, min(1.0, centre_confidence))
    score += 0.04 if element_cv <= 0.08 else 0.0
    score += 0.02 if split_ratio >= 1.8 else 0.0
    score += 0.01 * max(0.0, min(1.0, purity))
    required = all(checks.values())
    eligible = required and score >= 0.78
    if not result.get("resolved"):
        reason = str(result.get("reason") or "DETECTOR_UNRESOLVED")
    elif not required:
        reason = "TRIGGER_EVIDENCE_INCOMPLETE"
    elif score < 0.78:
        reason = "TRIGGER_SCORE_BELOW_THRESHOLD"
    else:
        reason = "GEOMETRY_TRIGGER_PASSED"
    return {
        "eligible": eligible,
        "score": round(score, 6),
        "reason": reason,
        "checks": checks,
        "metrics": {
            "element_angle_cv": element_cv,
            "center_confidence": centre_confidence,
            "selected_candidate_purity": purity,
            "split_ratio": split_ratio,
            "boundary_count": len(boundaries),
            "element_count": len(elements),
            "gap_count": len(gaps),
        },
    }


def _representative(group: dict[str, Any]) -> dict[str, Any]:
    return dict(min(group["relations"], key=lambda item: abs(float(item["angle"]) - group["mean_angle"])))


def select_automatic_angle_specifications(
    result: dict[str, Any],
    maximum: int = 3,
) -> list[dict[str, Any]]:
    """Select local spacing families and an actual compound centre span.

    For two non-uniform adjacent spacing families A and B the third target is
    an actually measured centre-to-centre relation near A+B.  For a uniform
    pattern it is an actual relation near 2A.  This mirrors common drafting
    intent without assuming 360/count.
    """
    pitches = result.get("center_pitches") or []
    pairwise = result.get("pairwise_center_relations") or []
    if not pitches or not pairwise or maximum <= 0:
        return []
    pitch_groups = sorted(
        (group for group in cluster_relation_values(pitches) if float(group["mean_angle"]) <= 180.0),
        key=lambda group: (float(group["mean_angle"]), -int(group["support"])),
    )
    # Ignore tiny one-off projection fragments when a repeated family exists.
    repeated = [group for group in pitch_groups if int(group["support"]) >= 2]
    local_groups = repeated[:2]
    if len(local_groups) < 2:
        local_groups.extend(group for group in pitch_groups if group not in local_groups)
    local_groups = sorted(local_groups[:2], key=lambda group: float(group["mean_angle"]))
    specifications = []
    for index, group in enumerate(local_groups):
        relation = _representative(group)
        specifications.append({
            "id": f"local_spacing_{index + 1:02d}",
            "semantic": "hub_radial_partition.local_center_spacing_angle",
            "target_angle": float(relation["angle"]),
            "preferred_mid_angle": (
                float(relation["start_angle"]) + float(relation["angle"]) / 2.0
            ) % 360.0,
            "selection_reason": "REPEATED_ADJACENT_CENTER_SPACING",
            "family_support": int(group["support"]),
        })
    if len(specifications) < maximum:
        local_values = [float(item["target_angle"]) for item in specifications]
        compound_target = sum(local_values) if len(local_values) >= 2 else local_values[0] * 2.0
        if compound_target <= 180.0:
            compound = min(pairwise, key=lambda item: abs(float(item["angle"]) - compound_target))
            if all(abs(float(compound["angle"]) - value) > 0.20 for value in local_values):
                specifications.append({
                    "id": "compound_spacing_01",
                    "semantic": "hub_radial_partition.compound_center_span_angle",
                    "target_angle": float(compound["angle"]),
                    "preferred_mid_angle": (
                        float(compound["start_angle"]) + float(compound["angle"]) / 2.0
                    ) % 360.0,
                    "selection_reason": "ACTUAL_PAIRWISE_RELATION_NEAR_LOCAL_SPACING_SUM",
                    "family_support": 1,
                })
    return specifications[:maximum]


def orchestrate_radial_views(views: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for view_name, view in views.items():
        for detector_name, detector in (
            ("STRAIGHT_RADIAL_BOUNDARIES", detect_hub_radial_partitions),
            ("CURVED_RADIAL_BOUNDARIES", detect_curved_radial_partitions),
        ):
            detection = detector(view)
            assessment = assess_radial_trigger(detection)
            candidates.append({
                "view": view_name,
                "detector": detector_name,
                "detection": detection,
                "assessment": assessment,
            })
    best = max(
        candidates,
        key=lambda item: (
            bool(item["assessment"]["eligible"]),
            float(item["assessment"]["score"]),
            len(item["detection"].get("boundary_angles") or []),
        ),
    )
    eligible = bool(best["assessment"]["eligible"])
    topology_persisted = bool(best["detection"].get("projected_edge_identity_persisted"))
    decision = "NO_RENDER"
    verification_status = "UNRESOLVED"
    if eligible:
        decision = "AUTO_RENDER" if topology_persisted and best["assessment"]["score"] >= 0.92 else "REVIEW_RENDER"
        verification_status = "AUTO_VERIFIED" if decision == "AUTO_RENDER" else "REVIEW_CANDIDATE"
    specifications = select_automatic_angle_specifications(best["detection"]) if eligible else []
    return {
        "method": METHOD,
        "decision": decision,
        "verification_status": verification_status,
        "best_view": best["view"],
        "selected_detector": best["detector"],
        "best_detection": best["detection"],
        "best_assessment": best["assessment"],
        "angle_specifications": specifications,
        "view_candidates": [
            {
                "view": item["view"],
                "detector": item["detector"],
                "resolved": bool(item["detection"].get("resolved")),
                "reason": item["assessment"]["reason"],
                "score": item["assessment"]["score"],
                "eligible": item["assessment"]["eligible"],
                "boundary_count": len(item["detection"].get("boundary_angles") or []),
            }
            for item in candidates
        ],
    }
