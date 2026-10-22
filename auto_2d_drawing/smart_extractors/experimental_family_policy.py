"""Feature-family placement policies for Experimental Main candidates."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def _anchor_midpoint(rule: dict[str, Any]) -> tuple[float, float]:
    payload = rule.get("geometry_payload") or {}
    anchors = payload.get("start_proj"), payload.get("end_proj")
    if not all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in anchors):
        return (0.0, 0.0)
    return (
        (float(anchors[0][0]) + float(anchors[1][0])) / 2.0,
        (float(anchors[0][1]) + float(anchors[1][1])) / 2.0,
    )


def _apply_shaft_policy(rules: list[dict[str, Any]]) -> None:
    """Preserve shaft ordering and dual-baseline placement semantics."""
    by_chain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rule in rules:
        if str(rule.get("feature_family") or "").upper() != "SOLID_SHAFT":
            continue
        chain_id = str(rule.get("shaft_chain_id") or rule.get("parent_feature_id") or "shaft")
        by_chain[chain_id].append(rule)

    for chain_rules in by_chain.values():
        overall = next(
            (rule for rule in chain_rules if rule.get("measurement_role") == "AXIAL_OVERALL_LENGTH"),
            None,
        )
        axial = [rule for rule in chain_rules if rule.get("measurement_role") == "AXIAL_SEGMENT_LENGTH"]
        diameters = [rule for rule in chain_rules if rule.get("measurement_role") == "OUTER_DIAMETER"]
        midpoints = [_anchor_midpoint(rule) for rule in axial]
        if overall is not None:
            midpoints.append(_anchor_midpoint(overall))
        span_x = max((point[0] for point in midpoints), default=0.0) - min(
            (point[0] for point in midpoints), default=0.0
        )
        span_y = max((point[1] for point in midpoints), default=0.0) - min(
            (point[1] for point in midpoints), default=0.0
        )
        axis_index = 0 if span_x >= span_y else 1
        ordered_axial = sorted(axial, key=lambda rule: _anchor_midpoint(rule)[axis_index])
        chain_center = (
            _anchor_midpoint(overall)[axis_index]
            if overall is not None
            else sum(_anchor_midpoint(rule)[axis_index] for rule in ordered_axial)
            / max(len(ordered_axial), 1)
        )
        left_lane = 0
        right_lane = 0
        for rule in ordered_axial:
            is_left = _anchor_midpoint(rule)[axis_index] <= chain_center
            rule["baseline"] = "LEFT" if is_left else "RIGHT"
            rule["side"] = "BOTTOM"
            rule["sides"] = ["BOTTOM"]
            rule["rank"] = 1
            rule["policy_lane"] = left_lane if is_left else right_lane
            if is_left:
                left_lane += 1
            else:
                right_lane += 1

        if overall is not None:
            overall["baseline"] = "NONE"
            overall["side"] = "BOTTOM"
            overall["sides"] = ["BOTTOM"]
            overall["rank"] = 2
            overall["policy_lane"] = max(left_lane, right_lane) + 1

        ordered_diameters = sorted(
            diameters,
            key=lambda item: -float(item.get("nominal_value") or 0.0),
        )
        for index, rule in enumerate(ordered_diameters):
            side = "LEFT" if index % 2 == 0 else "RIGHT"
            rule["side"] = side
            rule["sides"] = [side]
            rule["rank"] = 1
            rule["baseline"] = "NONE"
            rule["policy_lane"] = index // 2


def apply_experimental_family_policies(
    rules: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply composable policies without changing detector output."""
    prepared = [dict(rule) for rule in rules]
    _apply_shaft_policy(prepared)
    return prepared
