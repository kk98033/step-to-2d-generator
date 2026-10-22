"""Adapt Experimental Main analysis records for the smart-annotation UI."""

from __future__ import annotations

from typing import Any, Mapping

from auto_2d_drawing.smart_extractors.experimental_family_policy import (
    apply_experimental_family_policies,
)


ENGINE_ID = "experimental_v026"
CANDIDATE_SCHEMA_VERSION = 3
EXPECTED_METHOD = "DRAWING_EXPERIMENTAL_MAIN_V0_26"


def _rule_name(role: str, nominal: float, unit: str) -> str:
    readable = role.replace("_", " ").title()
    return f"{readable} {nominal:.2f} {unit}"


def _category(family: str, role: str, kind: str) -> str:
    family_key = family.lower()
    role_key = role.lower()
    if "shaft" in family_key:
        return "shaft"
    if "thin_wall" in family_key or "thickness" in role_key:
        return "thickness"
    if "diameter" in role_key or kind == "DIAMETER":
        return "annular_diameter"
    if "overall" in role_key:
        return "overall"
    if "length" in role_key:
        return "step"
    return family_key or "experimental"


def _intent_rule(intent: Mapping[str, Any]) -> dict[str, Any]:
    feature = dict(intent.get("feature") or {})
    measurement = dict(intent.get("measurement") or {})
    projection = dict(intent.get("projection") or {})
    verification = dict(intent.get("verification") or {})
    renderer = dict(intent.get("renderer_contract") or {})
    relationships = dict(intent.get("relationships") or {})
    intent_id = str(intent.get("intent_id") or "")
    source_candidates = [dict(item) for item in intent.get("source_candidates") or []]
    source = source_candidates[0] if source_candidates else {}
    extractor = str(source.get("extractor") or "")
    source_dimension_id = str(source.get("dimension_id") or "")
    role = str(measurement.get("role") or "UNKNOWN")
    family = str(feature.get("family") or "UNKNOWN")
    kind = str(measurement.get("kind") or "LINEAR").upper()
    nominal = float(measurement.get("nominal_value") or 0.0)
    unit = str(measurement.get("unit") or "mm")
    view = str(projection.get("preferred_view") or "front")
    anchors = projection.get("anchors") or []
    geometry_payload: dict[str, Any] = {}
    if len(anchors) == 2:
        geometry_payload = {
            "start_proj": [float(value) for value in anchors[0][:2]],
            "end_proj": [float(value) for value in anchors[1][:2]],
        }
    if kind == "DIAMETER":
        geometry_payload["diameter"] = nominal
        geometry_payload["radius"] = nominal / 2.0

    selection_group = {
        "ANNULAR_COMPOSITE_V1": "annular_selections",
        "SOLID_SHAFT_V1": "shaft_dimension_selections",
        "PAIRED_SURFACE_THICKNESS_V1": "thickness_selections",
    }.get(extractor)
    selection_id = intent_id if selection_group == "thickness_selections" else source_dimension_id
    render_eligible = bool(selection_group and selection_id and len(anchors) == 2)
    parent_feature_id = relationships.get("parent_feature_id")
    shaft_chain_id = None
    if family.upper() == "SOLID_SHAFT":
        shaft_chain_id = source_dimension_id.split(".segment_", 1)[0]
        shaft_chain_id = shaft_chain_id.split(".overall_length", 1)[0]
        shaft_chain_id = shaft_chain_id or str(parent_feature_id or "")

    return {
        "id": intent_id,
        "rule_id": intent_id,
        "name": _rule_name(role, nominal, unit),
        "category": _category(family, role, kind),
        "dim_type": kind,
        "semantic": measurement.get("semantic"),
        "nominal_value": nominal,
        "unit": unit,
        "preferred_view": view,
        "target_views": [view],
        "views": [view],
        "side": "BOTTOM",
        "sides": ["BOTTOM"],
        "rank": 1,
        "baseline": "NONE",
        "default_prefix": "%%c" if kind == "DIAMETER" else "",
        "default_tolerance": "",
        "geometry_payload": geometry_payload,
        "view_geometry": {view: geometry_payload} if geometry_payload else {},
        "canonical_feature_id": feature.get("feature_id"),
        "feature_id": feature.get("feature_id"),
        "feature_family": family,
        "measurement_role": role,
        "topology_refs": feature.get("topology_refs") or [],
        "related_feature_ids": relationships.get("related_feature_ids") or [],
        "parent_feature_id": parent_feature_id,
        "shaft_chain_id": shaft_chain_id,
        "source_candidates": source_candidates,
        "verification_status": verification.get("status"),
        "verification_confidence": float(verification.get("confidence") or 0.0),
        "verification_issues": verification.get("issues") or [],
        "experimental_renderer_contract": renderer,
        "source_engine": ENGINE_ID,
        "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidate_kind": "DIMENSION_INTENT",
        "experimental_selection_group": selection_group,
        "experimental_selection_id": selection_id,
        "render_eligible": render_eligible,
        "render_block_reason": None if render_eligible else "UNSUPPORTED_EXPERIMENTAL_SELECTION_ROUTE",
        "enabled": False,
    }


def _radial_rules(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    best_view = (
        (report.get("detectors") or {})
        .get("radial_pattern_bands", {})
        .get("best_view")
        or {}
    )
    patterns_by_id = {
        str(pattern.get("id") or ""): pattern
        for pattern in best_view.get("patterns") or []
        if pattern.get("id")
    }
    rules = []
    for candidate in best_view.get("candidate_rules") or []:
        rule = dict(candidate)
        rule_id = str(rule.get("rule_id") or rule.get("id") or "")
        if not rule_id:
            continue
        pattern = patterns_by_id.get(str(rule.get("pattern_id") or "")) or {}
        rule.update({
            "id": rule_id,
            "rule_id": rule_id,
            "source_engine": ENGINE_ID,
            "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate_kind": "RADIAL_PATTERN_RELATION",
            "verification_confidence": float(best_view.get("confidence") or 0.0),
            "experimental_selection_group": "radial_band_angle_selections",
            "experimental_selection_id": rule_id,
            "render_eligible": True,
            "render_block_reason": None,
            "enabled": False,
        })
        if pattern.get("reference_radius") is not None:
            rule["pattern_reference_radius"] = float(pattern["reference_radius"])
        if pattern.get("reference_radius_ratio") is not None:
            rule["pattern_reference_radius_ratio"] = float(pattern["reference_radius_ratio"])
        rules.append(rule)
    return rules


def _rectangular_hole_rules(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    detector = (
        (report.get("detectors") or {})
        .get("rectangular_hole_relations")
        or {}
    )
    if not detector.get("resolved"):
        return []
    view = str(detector.get("view") or "front")
    rules = []
    for index, intent in enumerate(detector.get("intents") or [], start=1):
        semantic = str(intent.get("semantic") or f"relation_{index}")
        nominal = float(intent.get("nominal_value") or 0.0)
        is_diameter = semantic.endswith("hole_diameter")
        selection_id = {
            "mounting_hole_pattern.hole_diameter": "hole_pattern.diameter",
            "mounting_hole_pattern.horizontal_center_distance": "hole_pattern.horizontal_center_distance",
            "mounting_hole_pattern.vertical_center_distance": "hole_pattern.vertical_center_distance",
        }.get(semantic)
        rule_id = f"experimental.{semantic}"
        rules.append({
            "id": rule_id,
            "rule_id": rule_id,
            "name": _rule_name(semantic.split(".")[-1], nominal, str(intent.get("unit") or "mm")),
            "category": "hole",
            "dim_type": "DIAMETER" if is_diameter else "LINEAR",
            "semantic": semantic,
            "nominal_value": nominal,
            "unit": intent.get("unit") or "mm",
            "preferred_view": view,
            "target_views": [view],
            "views": [view],
            "side": "BOTTOM",
            "sides": ["BOTTOM"],
            "baseline": "NONE",
            "topology_refs": intent.get("topology_refs") or [],
            "verification_status": "AUTO_VERIFIED",
            "verification_confidence": float(detector.get("confidence") or 0.0),
            "source_engine": ENGINE_ID,
            "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate_kind": "RECTANGULAR_HOLE_RELATION",
            "experimental_selection_group": "rectangular_hole_selections" if selection_id else None,
            "experimental_selection_id": selection_id,
            "render_eligible": bool(selection_id),
            "render_block_reason": None if selection_id else "UNSUPPORTED_EXPERIMENTAL_SELECTION_ROUTE",
            "enabled": False,
        })
    return rules


def build_experimental_candidate_rules(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return stable flat rules for the existing candidate-rule UI."""
    if report.get("mode") != "ANALYSIS_ONLY":
        raise ValueError("Experimental candidates require an ANALYSIS_ONLY report")
    if report.get("method") != EXPECTED_METHOD:
        raise ValueError(f"Unsupported Experimental Main method: {report.get('method')}")

    rules = [
        _intent_rule(intent)
        for intent in (report.get("dimension_intents") or {}).get("intents") or []
        if str((intent.get("verification") or {}).get("status") or "") != "INVALID"
    ]
    rules.extend(_radial_rules(report))
    rules.extend(_rectangular_hole_rules(report))

    seen: set[str] = set()
    unique_rules = []
    for rule in rules:
        rule_id = str(rule["rule_id"])
        if rule_id in seen:
            continue
        seen.add(rule_id)
        unique_rules.append(rule)
    return apply_experimental_family_policies(unique_rules)


def build_experimental_selection_config(
    authoritative_rules: list[Mapping[str, Any]],
    submitted_rules: list[Mapping[str, Any]],
    *,
    part_hint: str,
    include_half_section: bool = False,
) -> dict[str, Any]:
    """Translate enabled UI rules into Experimental Main's selection contract.

    Renderer routes always come from a fresh analysis.  The client may only
    override presentation fields such as view, side, lane, label and tolerance.
    """
    submitted_by_id = {
        str(item.get("rule_id") or item.get("id") or ""): item
        for item in submitted_rules
        if item.get("enabled")
    }
    config: dict[str, Any] = {
        "part_hint": part_hint,
        "include_half_section": bool(include_half_section),
        "selections": [],
        "relational_selections": [],
        "pattern_angle_selections": [],
        "radial_band_angle_selections": [],
        "annular_selections": [],
        "rectangular_hole_selections": [],
        "shaft_dimension_selections": [],
        "thickness_selections": [],
    }
    rejected = []
    selected_ids = set(submitted_by_id)
    authoritative_ids = {
        str(rule.get("rule_id") or rule.get("id") or "") for rule in authoritative_rules
    }
    for missing_id in sorted(selected_ids - authoritative_ids):
        rejected.append({"rule_id": missing_id, "reason": "UNKNOWN_EXPERIMENTAL_RULE"})

    for rule in authoritative_rules:
        rule_id = str(rule.get("rule_id") or rule.get("id") or "")
        submitted = submitted_by_id.get(rule_id)
        if submitted is None:
            continue
        group = str(rule.get("experimental_selection_group") or "")
        feature_id = str(rule.get("experimental_selection_id") or "")
        if not rule.get("render_eligible") or group not in config or not feature_id:
            rejected.append({"rule_id": rule_id, "reason": "EXPERIMENTAL_RULE_NOT_RENDERABLE"})
            continue
        item: dict[str, Any] = {
            "feature_id": feature_id,
            "view": str(
                submitted.get("preferred_view")
                or rule.get("preferred_view")
                or "front"
            ),
            "side": str(submitted.get("side") or rule.get("side") or "BOTTOM").upper(),
            "lane": int(submitted.get("lane", rule.get("policy_lane", 0)) or 0),
        }
        label = submitted.get("label")
        if not label:
            prefix = str(submitted.get("prefix") or rule.get("default_prefix") or "")
            nominal = float(rule.get("nominal_value") or 0.0)
            tolerance = str(submitted.get("tolerance") or "").strip()
            label = f"{prefix}{nominal:.2f}{tolerance}"
        item["label"] = str(label)
        config[group].append(item)

    config["selection_summary"] = {
        "requested": len(submitted_by_id),
        "routed": sum(
            len(config[key]) for key in config if key.endswith("_selections")
        ),
        "rejected": rejected,
    }
    return config


def candidate_summary(rules: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rules),
        "render_eligible": sum(bool(rule.get("render_eligible")) for rule in rules),
        "review_candidates": sum(
            str(rule.get("verification_status") or "") == "REVIEW_CANDIDATE"
            for rule in rules
        ),
        "by_kind": {
            kind: sum(str(rule.get("candidate_kind")) == kind for rule in rules)
            for kind in sorted({str(rule.get("candidate_kind")) for rule in rules})
        },
    }
