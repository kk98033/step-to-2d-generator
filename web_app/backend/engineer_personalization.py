"""Engineer-specific tolerance and dimension-placement recommendation layer."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from web_app.backend.identity_store import CurrentUser, IdentityStore


def _canonical_feature(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "shaft": "shaft_segment",
        "journal": "shaft_segment",
        "bore": "hole",
        "diameter": "cylinder",
    }
    return aliases.get(text, text)


def _nominal_value(payload: Any) -> float:
    if isinstance(payload, (int, float)):
        return abs(float(payload))
    if not isinstance(payload, dict):
        return 0.0
    for key in ("diameter", "length", "groove_diameter", "groove_width", "value", "radius"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and value:
            result = abs(float(value))
            return result * 2.0 if key == "radius" else result
    return 0.0


def _case_score(
    rule: Dict[str, Any],
    case: Dict[str, Any],
    part_type: Optional[str],
    product_family: Optional[str],
) -> Tuple[float, Dict[str, float]]:
    rule_feature = _canonical_feature(rule.get("category") or rule.get("feature_type") or rule.get("type"))
    case_feature = _canonical_feature(case.get("feature_type"))
    feature_score = 1.0 if rule_feature == case_feature else 0.0

    rule_role = str(rule.get("inferred_role") or rule.get("role") or "").upper()
    case_role = str(case.get("inferred_role") or "").upper()
    role_score = 0.5 if not rule_role or not case_role else (1.0 if rule_role == case_role else 0.0)

    query_nominal = _nominal_value(rule.get("nominal") or rule.get("nominal_value") or rule.get("value"))
    case_nominal = _nominal_value(case.get("nominal"))
    if query_nominal > 0 and case_nominal > 0:
        relative_error = abs(query_nominal - case_nominal) / max(query_nominal, case_nominal)
        nominal_score = math.exp(-8.0 * relative_error)
    else:
        nominal_score = 0.3

    part_score = 0.5 if not part_type or not case.get("part_type") else (
        1.0 if str(part_type).upper() == str(case.get("part_type")).upper() else 0.2
    )
    family_score = 0.5 if not product_family or not case.get("product_family") else (
        1.0 if str(product_family).upper() == str(case.get("product_family")).upper() else 0.1
    )
    breakdown = {
        "feature_type": feature_score,
        "functional_role": role_score,
        "nominal": round(nominal_score, 4),
        "part_type": part_score,
        "product_family": family_score,
    }
    total = (
        0.35 * feature_score
        + 0.20 * role_score
        + 0.30 * nominal_score
        + 0.10 * part_score
        + 0.05 * family_score
    )
    return round(total, 4), breakdown


def _placement_consensus(cases: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    placements = [case.get("placement") or {} for case in cases if case.get("placement")]
    if not placements:
        return None
    consensus: Dict[str, Any] = {}
    support: Dict[str, int] = {}
    for key in ("preferred_view", "side", "baseline"):
        values = [str(item[key]) for item in placements if item.get(key) is not None]
        if values:
            value, count = Counter(values).most_common(1)[0]
            consensus[key] = value
            support[key] = count
    view_values: List[str] = []
    for item in placements:
        for key in ("views", "target_views"):
            if isinstance(item.get(key), list):
                view_values.extend(str(value) for value in item[key])
    if view_values:
        view, count = Counter(view_values).most_common(1)[0]
        consensus["preferred_view"] = consensus.get("preferred_view", view)
        support["preferred_view"] = max(support.get("preferred_view", 0), count)
    offsets = [float(item["offset"]) for item in placements if isinstance(item.get("offset"), (int, float))]
    if offsets:
        consensus["offset"] = round(sum(offsets) / len(offsets), 3)
        support["offset"] = len(offsets)
    if not consensus:
        return None
    return {
        **consensus,
        "support_count": len(placements),
        "field_support": support,
        "source": "ENGINEER_HISTORY",
    }


def personalize_recommendations(
    store: IdentityStore,
    user: CurrentUser,
    candidate_rules: List[Dict[str, Any]],
    recommendation_result: Dict[str, Any],
    part_type: Optional[str],
    product_family: Optional[str],
    tag_ids: Optional[Iterable[str]] = None,
    tag_match_mode: str = "ANY",
) -> Dict[str, Any]:
    """Blend private engineer history after the company evidence engine.

    Company cases remain untouched and auditable.  Personal history may replace
    the selected tolerance only in PERSONAL_FIRST mode and above the engineer's
    configured similarity threshold; BALANCED mode presents the personal option
    and placement recommendation without silently overriding company evidence.
    """

    preferences = store.get_preferences(user.id)
    mode = str(preferences.get("recommendation_mode") or "BALANCED").upper()
    personal_cases = (
        store.personal_cases(user.id, tag_ids=tag_ids, match_mode=tag_match_mode)
        if mode != "COMPANY_ONLY" else []
    )
    minimum = float(preferences.get("personal_case_min_similarity", 0.82))
    rule_map = {
        str(rule.get("rule_id") or rule.get("id")): rule
        for rule in candidate_rules
    }
    recommendations = recommendation_result.get("recommendations") or {}
    personalized_count = 0

    for rule_id, recommendation in recommendations.items():
        rule = rule_map.get(str(rule_id), {"rule_id": rule_id, **recommendation})
        ranked: List[Dict[str, Any]] = []
        for case in personal_cases:
            score, breakdown = _case_score(rule, case, part_type, product_family)
            if score <= 0.0:
                continue
            ranked.append({"case": case, "similarity": score, "score_breakdown": breakdown})
        ranked.sort(key=lambda item: item["similarity"], reverse=True)
        top = ranked[0] if ranked else None
        relevant = [item["case"] for item in ranked[:5] if item["similarity"] >= max(0.60, minimum - 0.15)]
        placement = _placement_consensus(relevant)
        if not placement and preferences.get("dimension_placement"):
            placement = {
                **preferences["dimension_placement"],
                "support_count": 0,
                "field_support": {},
                "source": "ENGINEER_SETTINGS",
            }
        if placement:
            recommendation["engineer_placement_recommendation"] = placement

        recommendation["personalization"] = {
            "mode": mode,
            "owner_user_id": user.id,
            "personal_case_count": len(personal_cases),
            "matched_case_count": len(ranked),
            "minimum_similarity": minimum,
            "personal_case_weight": preferences.get("personal_case_weight", 0.35),
            "adopted": False,
        }
        if not top:
            continue

        case = top["case"]
        evidence = {
            "case_id": case["case_id"],
            "source_scope": "PERSONAL_ENGINEER",
            "owner_user_id": user.id,
            "model_id": case["model_id"],
            "part_id": case["part_id"],
            "feature_type": case["feature_type"],
            "inferred_role": case.get("inferred_role"),
            "nominal_dimensions": case["nominal"],
            "tolerance_config": case["tolerance_config"],
            "placement": case["placement"],
            "similarity": top["similarity"],
            "score_breakdown": top["score_breakdown"],
            "verification_status": "ENGINEER_CONFIRMED_PRIVATE",
            "has_source_drawing": True,
            "drawing_urls": {
                "pdf": (case.get("artifact_output_files") or {}).get("pdf_url"),
                "svg": (case.get("artifact_output_files") or {}).get("svg_url"),
            },
            "used_for_decision": False,
            "evidence_role": "PERSONAL_PREFERENCE_CANDIDATE",
        }
        recommendation.setdefault("evidence_cases", []).insert(0, evidence)
        recommendation["personalization"]["top_case"] = evidence

        if mode != "PERSONAL_FIRST" or top["similarity"] < minimum:
            continue
        tolerance = case.get("tolerance_config") or {}
        if str(tolerance.get("mode") or "NONE").upper() == "NONE":
            continue
        evidence["used_for_decision"] = True
        evidence["evidence_role"] = "ADOPTED_PERSONAL_PREFERENCE"
        recommendation["tolerance_config"] = tolerance
        recommendation["recommended_mode"] = tolerance.get("mode", "NONE")
        recommendation["fit_class"] = tolerance.get("fit_class")
        recommendation["upper_dev"] = float(tolerance.get("upper_dev", tolerance.get("dev", 0.0)) or 0.0)
        recommendation["lower_dev"] = float(tolerance.get("lower_dev", -abs(float(tolerance.get("dev", 0.0) or 0.0))) or 0.0)
        recommendation["tier_level"] = "TIER_0_ENGINEER_PREFERENCE"
        recommendation["decision_status"] = "PERSONALIZED_RECOMMENDATION"
        recommendation["confidence"] = round(min(0.95, 0.70 + top["similarity"] * 0.25), 2)
        recommendation["confidence_basis"] = "ENGINEER_PRIVATE_HISTORY_UNCALIBRATED"
        recommendation["personalization"]["adopted"] = True
        recommendation["personalization"]["adopted_case_id"] = case["case_id"]
        personalized_count += 1

    recommendation_result["engineer_personalization"] = {
        "user_id": user.id,
        "mode": mode,
        "personal_case_count": len(personal_cases),
        "personalized_recommendation_count": personalized_count,
        "preferences": preferences,
        "selected_tag_ids": list(tag_ids or []),
        "tag_match_mode": str(tag_match_mode).upper(),
    }
    return recommendation_result
