"""Geometry-first intent selection and view orchestration for experiment 21."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence


METHOD = "INTENT_SELECTION_AND_VIEW_ORCHESTRATION_V1"
SELECTION_THRESHOLD = 0.68
AUTO_DISPATCH_THRESHOLD = 0.75

ROLE_PRIOR = {
    "AXIAL_OVERALL_LENGTH": 1.0,
    "NORMAL_WALL_THICKNESS": 1.0,
    "OUTER_DIAMETER": 0.9,
    "INNER_DIAMETER": 0.9,
    "RADIAL_WALL_THICKNESS": 0.9,
    "AXIAL_SEGMENT_LENGTH": 0.8,
    "AXIAL_THICKNESS": 0.75,
    "THICKNESS_PROFILE": 0.55,
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _historical_support(
    intent: Mapping[str, Any],
    historical_evidence: Mapping[str, Mapping[str, Any]],
) -> tuple[float, dict[str, Any]]:
    semantic = str(intent["measurement"]["semantic"])
    role = str(intent["measurement"]["role"])
    evidence = historical_evidence.get(semantic) or historical_evidence.get(role) or {}
    case_count = max(0, int(evidence.get("case_count") or 0))
    selection_frequency = _clamp(float(evidence.get("selection_frequency") or 0.0))
    verified = bool(evidence.get("verified"))
    support = selection_frequency
    if case_count:
        support *= min(1.0, case_count / 5.0)
    if verified:
        support = max(support, 0.65)
    return support, {
        "semantic": semantic,
        "matched": bool(evidence),
        "case_count": case_count,
        "selection_frequency": selection_frequency,
        "verified": verified,
    }


def _dominant_thin_skin(detectors: Mapping[str, Any]) -> dict[str, Any]:
    result = detectors.get("paired_surface_thickness") or {}
    candidates = []
    for feature in result.get("features") or []:
        detector = feature.get("detector_candidate") or {}
        candidates.append({
            "feature_id": feature.get("id"),
            "score": _clamp(float(detector.get("score") or 0.0)),
            "area_ratio": _clamp(float(detector.get("area_ratio") or 0.0)),
            "area_coverage": _clamp(float(detector.get("area_coverage") or 0.0)),
        })
    best = max(candidates, key=lambda item: (
        item["score"], item["area_coverage"], item["area_ratio"],
    ), default=None)
    active = bool(
        result.get("resolved")
        and best
        and best["score"] >= 0.90
        and best["area_ratio"] >= 0.60
        and best["area_coverage"] >= 0.25
    )
    return {"active": active, "best_candidate": best}


def _shaft_family_quality(detectors: Mapping[str, Any]) -> dict[str, Any]:
    result = detectors.get("solid_shaft_features") or {}
    chains = result.get("chains") or []
    metrics = []
    for chain in chains:
        metrics.append({
            "chain_id": chain.get("id"),
            "coverage_ratio": _clamp(float(chain.get("coverage_ratio") or 0.0)),
            "confidence": _clamp(float(chain.get("confidence") or 0.0)),
            "segment_count": len(chain.get("segments") or []),
            "shoulder_count": len(chain.get("shoulders") or []),
        })
    best = max(metrics, key=lambda item: (
        item["coverage_ratio"], item["segment_count"], item["confidence"],
    ), default=None)
    strong = bool(
        best
        and best["coverage_ratio"] >= 0.50
        and (best["segment_count"] >= 2 or best["shoulder_count"] >= 1)
    )
    return {"strong": strong, "best_chain": best}


def _family_adjustment(
    family: str,
    context: Mapping[str, Any],
    historical_support: float,
) -> tuple[float, list[str]]:
    adjustment = 0.0
    reasons = []
    if family == "SOLID_SHAFT" and not context["shaft_quality"]["strong"]:
        adjustment -= 0.30
        reasons.append("WEAK_SHAFT_CHAIN_COVERAGE_OR_CONTINUITY")
    if context["dominant_thin_skin"]["active"] and family != "THIN_WALL":
        if historical_support < 0.65:
            adjustment -= 0.35
            reasons.append("DOMINANT_THIN_SKIN_REQUIRES_NON_WALL_SELECTION_EVIDENCE")
    return adjustment, reasons


def _selection_score(
    intent: Mapping[str, Any],
    context: Mapping[str, Any],
    historical_evidence: Mapping[str, Mapping[str, Any]],
) -> tuple[float, dict[str, Any]]:
    verification = intent.get("verification") or {}
    geometry_confidence = _clamp(float(verification.get("confidence") or 0.0))
    status = str(verification.get("status") or "")
    status_support = {
        "AUTO_VERIFIED": 1.0,
        "REVIEW_CANDIDATE": 0.55,
        "REVIEW_CONFLICT": 0.20,
    }.get(status, 0.0)
    role = str(intent["measurement"]["role"])
    role_support = ROLE_PRIOR.get(role, 0.60)
    view_support = 1.0 if intent.get("projection", {}).get("preferred_view") else 0.0
    history_support, history = _historical_support(intent, historical_evidence)
    base = (
        0.50 * geometry_confidence
        + 0.15 * status_support
        + 0.15 * role_support
        + 0.05 * view_support
        + 0.15 * history_support
    )
    adjustment, adjustment_reasons = _family_adjustment(
        str(intent["feature"]["family"]), context, history_support,
    )
    score = _clamp(base + adjustment)
    return score, {
        "geometry_confidence": geometry_confidence,
        "geometry_status_support": status_support,
        "semantic_role_support": role_support,
        "view_support": view_support,
        "historical_support": history_support,
        "historical_evidence": history,
        "family_adjustment": adjustment,
        "adjustment_reasons": adjustment_reasons,
    }


def _unique_radial_relations(best_view: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected = []
    represented_angles = []
    for relation in sorted(
        best_view.get("center_pitch_relations") or [],
        key=lambda item: (float(item.get("angle") or 0.0), float(item.get("start_angle") or 0.0)),
    ):
        angle = float(relation.get("angle") or 0.0)
        if angle <= 0.0 or angle > 180.0:
            continue
        if any(abs(angle - existing) <= 0.35 for existing in represented_angles):
            continue
        selected.append(dict(relation))
        represented_angles.append(angle)
    if len(selected) >= 2:
        pairwise = best_view.get("pairwise_center_relations") or []
        compound_target = sum(float(item["angle"]) for item in selected[:2])
        compound = min(
            pairwise,
            key=lambda item: abs(float(item.get("angle") or 0.0) - compound_target),
            default=None,
        )
        if compound and abs(float(compound["angle"]) - compound_target) <= 0.70:
            selected.append(dict(compound))
    return selected[:3]


def _supplemental_dispatch(
    detectors: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    dispatch = []
    radial = (detectors.get("hub_radial_relations") or {}).get("best_view") or {}
    if radial.get("resolved"):
        relations = _unique_radial_relations(radial)
        if relations:
            dispatch.append({
                "renderer": "ANGULAR_DIMENSION_RENDERER",
                "view": str(radial.get("view") or ""),
                "selection_status": "REVIEW_DISPATCH",
                "reason": "MEASURED_RADIAL_RELATIONS_REQUIRE_DRAWING_INTENT_CONFIRMATION",
                "relations": relations,
            })
    holes = detectors.get("rectangular_hole_relations") or {}
    if holes.get("resolved"):
        if context["dominant_thin_skin"]["active"]:
            selection_status = "SUPPRESSED_BY_COMPATIBILITY"
            reason = "DOMINANT_THIN_SKIN_REQUIRES_HOLE_SELECTION_EVIDENCE"
        else:
            selection_status = "BLOCKED_UNSUPPORTED_RENDERER"
            reason = "GEOMETRY_VERIFIED_BUT_NO_SHARED_RENDERER_CONTRACT"
        dispatch.append({
            "renderer": "UNSUPPORTED_RECTANGULAR_HOLE_RENDERER",
            "view": str(holes.get("view") or ""),
            "selection_status": selection_status,
            "reason": reason,
            "intents": holes.get("intents") or [],
        })
    return dispatch


def build_orchestration_plan(
    analysis_report: Mapping[str, Any],
    historical_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rank geometry intents and group only selected records by view/renderer."""
    detectors = analysis_report.get("detectors") or {}
    historical = historical_evidence or {}
    context = {
        "dominant_thin_skin": _dominant_thin_skin(detectors),
        "shaft_quality": _shaft_family_quality(detectors),
    }
    selected = []
    suppressed = []
    for intent in analysis_report.get("dimension_intents", {}).get("intents") or []:
        score, evidence = _selection_score(intent, context, historical)
        status = str(intent.get("verification", {}).get("status") or "")
        renderer = str(intent.get("renderer_contract", {}).get("renderer") or "")
        reasons = list(evidence["adjustment_reasons"])
        if status == "INVALID":
            decision = "SUPPRESSED"
            reasons.append("INVALID_GEOMETRY_INTENT")
        elif not renderer:
            decision = "SUPPRESSED"
            reasons.append("MISSING_RENDERER_CONTRACT")
        elif score < SELECTION_THRESHOLD:
            decision = "SUPPRESSED"
            reasons.append("SELECTION_SCORE_BELOW_THRESHOLD")
        elif status == "AUTO_VERIFIED" and score >= AUTO_DISPATCH_THRESHOLD:
            decision = "AUTO_DISPATCH"
        else:
            decision = "REVIEW_DISPATCH"
        record = {
            "intent_id": str(intent["intent_id"]),
            "feature_family": str(intent["feature"]["family"]),
            "measurement_role": str(intent["measurement"]["role"]),
            "semantic": str(intent["measurement"]["semantic"]),
            "view": str(intent["projection"].get("preferred_view") or ""),
            "renderer": renderer,
            "geometry_status": status,
            "selection_status": decision,
            "selection_confidence": round(score, 6),
            "selection_evidence": evidence,
            "reasons": reasons,
            "intent": intent,
        }
        (selected if decision != "SUPPRESSED" else suppressed).append(record)

    dispatch_by_view: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in selected:
        dispatch_by_view[record["view"]][record["renderer"]].append(record["intent_id"])
    view_plan = [
        {
            "view": view,
            "renderer_dispatch": [
                {"renderer": renderer, "intent_ids": intent_ids}
                for renderer, intent_ids in sorted(renderers.items())
            ],
        }
        for view, renderers in sorted(dispatch_by_view.items())
    ]
    supplemental = _supplemental_dispatch(detectors, context)
    counts = Counter(record["selection_status"] for record in selected + suppressed)
    renderer_dispatch_ready = bool(selected) and not any(
        item["selection_status"].startswith("BLOCKED") for item in supplemental
    )
    remaining_stages = ["COMBINED_DXF_COMPOSITION"]
    if not renderer_dispatch_ready:
        remaining_stages.insert(0, "MISSING_RENDERER_CONTRACT")
    return {
        "schema_version": 1,
        "method": METHOD,
        "source_method": analysis_report.get("method"),
        "source_step": analysis_report.get("source_step"),
        "thresholds": {
            "selection": SELECTION_THRESHOLD,
            "auto_dispatch": AUTO_DISPATCH_THRESHOLD,
        },
        "context": context,
        "selection_counts": dict(sorted(counts.items())),
        "selected_intents": selected,
        "suppressed_intents": suppressed,
        "view_plan": view_plan,
        "supplemental_dispatch": supplemental,
        "renderer_dispatch_ready": renderer_dispatch_ready,
        "full_drawing_render_ready": False,
        "remaining_stages": remaining_stages,
    }
