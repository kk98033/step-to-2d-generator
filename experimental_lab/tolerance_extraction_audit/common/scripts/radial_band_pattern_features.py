"""Detect repeated radial boundaries as independent concentric pattern bands."""

from __future__ import annotations

from collections import defaultdict
import math
from statistics import median
from typing import Any, Mapping, Sequence

try:
    from .relational_dimension_intents import (
        _projection_center,
        circular_difference,
        classify_radial_intervals,
        cluster_angles,
    )
except ImportError:
    from relational_dimension_intents import (
        _projection_center,
        circular_difference,
        classify_radial_intervals,
        cluster_angles,
    )


METHOD = "CONCENTRIC_RADIAL_PATTERN_BANDS_V1"


def _candidate_radial_lines(view: dict[str, Any]) -> tuple[list[dict[str, Any]], tuple[float, float], float]:
    center, _evidence = _projection_center(view)
    x0, y0, x1, y1 = [float(value) for value in view["bbox"]]
    span = min(x1 - x0, y1 - y0)
    visible = view.get("visible") or []
    edges = visible + (view.get("hidden") or [])
    candidates = []
    for edge_index, edge in enumerate(edges):
        if edge.get("type") != "line":
            continue
        p0, p1 = (tuple(float(value) for value in edge[key]) for key in ("p1", "p2"))
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < span * 0.008 or length > span * 0.20:
            continue
        midpoint = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
        radius = math.dist(midpoint, center)
        if radius < span * 0.04 or radius > span * 0.40:
            continue
        line_distance = abs(dx * (center[1] - p0[1]) - dy * (center[0] - p0[0])) / max(length, 1e-9)
        alignment = abs(
            (dx * (midpoint[0] - center[0]) + dy * (midpoint[1] - center[1]))
            / max(length * radius, 1e-9)
        )
        if line_distance > span * 0.015 or alignment < 0.94:
            continue
        candidates.append({
            "angle": math.degrees(math.atan2(midpoint[1] - center[1], midpoint[0] - center[0])) % 360.0,
            "mid_radius": radius,
            "length": length,
            "line_distance": line_distance,
            "radial_alignment": alignment,
            "edge_ref": f"{'visible' if edge_index < len(visible) else 'hidden'}:{edge_index if edge_index < len(visible) else edge_index - len(visible)}",
            "p0": list(p0),
            "p1": list(p1),
        })
    return candidates, center, span


def _representative_relations(relations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not relations:
        return []
    nominal = median(float(item["angle"]) for item in relations)
    return sorted(
        (dict(item) for item in relations),
        key=lambda item: (abs(float(item["angle"]) - nominal), float(item["start_angle"])),
    )


def detect_radial_pattern_bands(view: dict[str, Any]) -> dict[str, Any]:
    candidates, center, span = _candidate_radial_lines(view)
    radial_bin = max(span * 0.012, 1e-6)
    length_bin = max(span * 0.010, 1e-6)
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[(round(candidate["mid_radius"] / radial_bin), round(candidate["length"] / length_bin))].append(candidate)

    patterns = []
    for group_key, group in grouped.items():
        angles = cluster_angles([item["angle"] for item in group])
        classification = classify_radial_intervals(angles)
        elements = classification.get("elements") or []
        gaps = classification.get("gaps") or []
        pitches = classification.get("center_pitches") or []
        repetition_count = max(len(elements), len(gaps))
        if not classification.get("resolved") or repetition_count < 4:
            continue
        angle_to_edge = {
            str(round(angle, 6)): min(
                group,
                key=lambda item: circular_difference(angle, float(item["angle"])),
            )["edge_ref"]
            for angle in angles
        }
        reference_radius = sum(float(item["mid_radius"]) for item in group) / len(group)
        patterns.append({
            "id": "",
            "group_key": list(group_key),
            "reference_radius": reference_radius,
            "reference_radius_ratio": reference_radius / max(span, 1e-9),
            "boundary_count": len(angles),
            "repetition_count": repetition_count,
            "boundary_angles": angles,
            "boundary_edge_refs": angle_to_edge,
            "element_relations": _representative_relations(elements),
            "gap_relations": _representative_relations(gaps),
            "center_pitch_relations": _representative_relations(pitches),
            "element_angle": median(float(item["angle"]) for item in elements) if elements else None,
            "gap_angle": median(float(item["angle"]) for item in gaps) if gaps else None,
            "center_pitch_angle": median(float(item["angle"]) for item in pitches) if pitches else None,
            "verification_status": "REVIEW_CANDIDATE",
        })

    patterns.sort(key=lambda item: float(item["reference_radius"]))
    for index, pattern in enumerate(patterns, start=1):
        pattern["id"] = f"radial_pattern_band_{index:02d}"
        pattern["radial_role"] = (
            "INNER_HUB_SLOT_PATTERN" if index == 1
            else "OUTER_ANNULAR_SLOT_PATTERN" if index == len(patterns)
            else "INTERMEDIATE_RADIAL_PATTERN"
        )
        if pattern["radial_role"] == "OUTER_ANNULAR_SLOT_PATTERN" and pattern["gap_relations"]:
            gap = pattern["gap_relations"][0]
            start = float(gap["start_angle"])
            span_angle = float(gap["angle"])
            bisector = (start + span_angle / 2.0) % 360.0
            pattern["half_gap_relations"] = [{
                "start_angle": start,
                "end_angle": bisector,
                "angle": span_angle / 2.0,
                "semantic": "circumferential_slot_pattern.half_gap_angle",
                "source_gap": gap,
            }]
            pattern["half_gap_angle"] = span_angle / 2.0
        else:
            pattern["half_gap_relations"] = []
            pattern["half_gap_angle"] = None

    return {
        "schema_version": 1,
        "method": METHOD,
        "resolved": bool(patterns),
        "center": list(center),
        "candidate_line_count": len(candidates),
        "pattern_count": len(patterns),
        "patterns": patterns,
    }


def build_radial_band_candidate_rules(result: dict[str, Any], view_name: str) -> list[dict[str, Any]]:
    rules = []
    for pattern in result.get("patterns") or []:
        role = str(pattern["radial_role"])
        if role == "INNER_HUB_SLOT_PATTERN":
            specifications = (
                ("inner_hub_slots.pitch_angle", "hub_radial_partition.center_pitch_angle", "內圈凹槽中心節距角", "center_pitch_angle"),
                ("inner_hub_slots.element_angle", "hub_radial_partition.element_angle", "內圈單槽角寬", "element_angle"),
            )
        elif role == "OUTER_ANNULAR_SLOT_PATTERN":
            specifications = (
                ("outer_annular_slots.full_angle", "circumferential_slot_pattern.gap_angle", "外圈凹槽角寬", "gap_angle"),
                ("outer_annular_slots.half_angle", "circumferential_slot_pattern.half_gap_angle", "外圈凹槽半角", "half_gap_angle"),
            )
        else:
            continue
        for rule_id, semantic, name, value_key in specifications:
            value = pattern.get(value_key)
            if value is None:
                continue
            prefix = f"{int(pattern['repetition_count'])}-" if rule_id.endswith("full_angle") else ""
            rules.append({
                "rule_id": rule_id,
                "feature_group_id": "outer_annular_slots" if role == "OUTER_ANNULAR_SLOT_PATTERN" else "inner_hub_slots",
                "pattern_id": pattern["id"],
                "pattern_role": role,
                "name": f"{name} {prefix}{float(value):.2f}°",
                "category": "pattern_angle",
                "dim_type": "ANGULAR",
                "semantic": semantic,
                "nominal_value": float(value),
                "pattern_count": int(pattern["repetition_count"]),
                "preferred_view": view_name,
                "target_views": [view_name],
                "views": [view_name],
                "side": "BOTTOM",
                "renderer_contract": "ANGULAR_RELATION_RENDERER",
                "verification_status": pattern["verification_status"],
            })
    return rules


def build_radial_band_render_tasks(
    result: dict[str, Any],
    selections: Sequence[dict[str, Any]],
    *,
    view_name: str,
    view_span: float,
    reserved_sectors: Sequence[Mapping[str, Any]] = (),
    placement_trace: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Group related angles and place independent relations in clear grooves."""
    patterns = {str(item["id"]): item for item in result.get("patterns") or []}
    rules = {str(item["rule_id"]): item for item in build_radial_band_candidate_rules(result, view_name)}
    relation_keys = {
        "hub_radial_partition.center_pitch_angle": "center_pitch_relations",
        "hub_radial_partition.element_angle": "element_relations",
        "circumferential_slot_pattern.gap_angle": "gap_relations",
        "circumferential_slot_pattern.half_gap_angle": "half_gap_relations",
    }
    selected = {
        str(item.get("feature_id") or item.get("rule_id") or item.get("id") or ""): dict(item)
        for item in selections
    }
    tasks: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    occupied = [dict(item) for item in reserved_sectors]
    trace = placement_trace if placement_trace is not None else []
    radius_ratios = {
        "hub_radial_partition.element_angle": 0.24,
        "hub_radial_partition.center_pitch_angle": 0.30,
        "circumferential_slot_pattern.half_gap_angle": 0.37,
        "circumferential_slot_pattern.gap_angle": 0.44,
    }

    def relation_mid_angle(relation: Mapping[str, Any]) -> float:
        return (float(relation["start_angle"]) + float(relation["angle"]) / 2.0) % 360.0

    def angular_distance(first: float, second: float) -> float:
        delta = abs(float(first) - float(second)) % 360.0
        return min(delta, 360.0 - delta)

    def sector_clearance(relation: Mapping[str, Any]) -> float:
        if not occupied:
            return 180.0
        midpoint = relation_mid_angle(relation)
        half_span = float(relation["angle"]) / 2.0
        return min(
            angular_distance(midpoint, float(item["mid_angle"]))
            - half_span
            - float(item["span"]) / 2.0
            - 12.0
            for item in occupied
        )

    def choose_relation(
        relations: Sequence[Mapping[str, Any]],
        preferred_mid_angle: float = 270.0,
    ) -> dict[str, Any]:
        return dict(max(
            relations,
            key=lambda relation: (
                sector_clearance(relation),
                -angular_distance(relation_mid_angle(relation), preferred_mid_angle),
            ),
        ))

    def append_task(
        rule_id: str,
        relation: Mapping[str, Any],
        *,
        placement_group: str,
        paired_with: str | None = None,
    ) -> None:
        rule = rules[rule_id]
        selection = selected[rule_id]
        semantic = str(rule["semantic"])
        pattern = patterns[str(rule["pattern_id"])]
        label = str(selection.get("label") or (
            f"{int(rule['pattern_count'])}-{float(relation['angle']):.2f} deg"
            if rule_id.endswith("full_angle")
            else f"{float(relation['angle']):.2f} deg"
        ))
        tasks.append({
            "id": rule_id,
            "semantic": semantic,
            "start_angle": float(relation["start_angle"]),
            "end_angle": float(relation["end_angle"]),
            "angle": float(relation["angle"]),
            "radius": view_span * radius_ratios[semantic],
            "base_radius_factor": 1.0,
            "extend_to_center": True,
            "lane": len(tasks),
            "label": label,
            "center": list(result["center"]),
            "view": str(selection.get("view") or view_name),
            "pattern_id": pattern["id"],
            "pattern_role": pattern["radial_role"],
            "source_relation": dict(relation),
            "placement_group": placement_group,
            "paired_with": paired_with,
        })
        trace.append({
            "rule_id": rule_id,
            "placement_group": placement_group,
            "paired_with": paired_with,
            "mid_angle": relation_mid_angle(relation),
            "span": float(relation["angle"]),
            "clearance_at_selection": sector_clearance(relation),
            "start_angle": float(relation["start_angle"]),
            "end_angle": float(relation["end_angle"]),
        })

    full_id = "outer_annular_slots.full_angle"
    half_id = "outer_annular_slots.half_angle"
    if full_id in selected or half_id in selected:
        controlling_id = full_id if full_id in selected else half_id
        rule = rules.get(controlling_id)
        if rule is None:
            rejected.append({"feature_id": controlling_id, "reason": "UNKNOWN_RADIAL_BAND_ANGLE_RULE"})
        else:
            pattern = patterns[str(rule["pattern_id"])]
            gap_relations = pattern.get("gap_relations") or []
            if not gap_relations:
                rejected.append({"feature_id": controlling_id, "reason": "NO_MEASURED_RELATION"})
            else:
                full_relation = choose_relation(gap_relations)
                group_id = f"outer_gap_at_{relation_mid_angle(full_relation):.3f}"
                if full_id in selected and full_id in rules:
                    append_task(
                        full_id,
                        full_relation,
                        placement_group=group_id,
                        paired_with=half_id if half_id in selected else None,
                    )
                if half_id in selected and half_id in rules:
                    half_angle = float(full_relation["angle"]) / 2.0
                    half_relation = {
                        **dict(full_relation),
                        "end_angle": (float(full_relation["start_angle"]) + half_angle) % 360.0,
                        "angle": half_angle,
                        "semantic": "circumferential_slot_pattern.half_gap_angle",
                        "derived_from_full_gap": True,
                    }
                    append_task(
                        half_id,
                        half_relation,
                        placement_group=group_id,
                        paired_with=full_id if full_id in selected else None,
                    )
                occupied.append({
                    "mid_angle": relation_mid_angle(full_relation),
                    "span": float(full_relation["angle"]),
                    "kind": group_id,
                })

    remaining = [rule_id for rule_id in selected if rule_id not in {full_id, half_id}]
    remaining.sort(
        key=lambda rule_id: float(rules.get(rule_id, {}).get("nominal_value") or 0.0),
        reverse=True,
    )
    for rule_id in remaining:
        rule = rules.get(rule_id)
        if rule is None:
            rejected.append({"feature_id": rule_id, "reason": "UNKNOWN_RADIAL_BAND_ANGLE_RULE"})
            continue
        pattern = patterns[str(rule["pattern_id"])]
        relations = pattern.get(relation_keys[str(rule["semantic"])]) or []
        if not relations:
            rejected.append({"feature_id": rule_id, "reason": "NO_MEASURED_RELATION"})
            continue
        relation = choose_relation(relations)
        group_id = f"independent_{rule_id}"
        append_task(rule_id, relation, placement_group=group_id)
        occupied.append({
            "mid_angle": relation_mid_angle(relation),
            "span": float(relation["angle"]),
            "kind": group_id,
        })
    return tasks, rejected
