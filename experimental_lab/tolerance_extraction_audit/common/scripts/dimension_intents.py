"""Unified experimental DimensionIntent contract for 06 and 07 outputs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any, Sequence


STATUS_PRIORITY = {
    "AUTO_VERIFIED": 3,
    "REVIEW_CANDIDATE": 2,
    "REVIEW_CONFLICT": 1,
    "INVALID": 0,
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "dimension"


def _axis_signature(axis: Sequence[float] | None) -> list[float]:
    values = [float(value) for value in (axis or (0.0, 0.0, 1.0))[:3]]
    length = math.sqrt(sum(value * value for value in values))
    if length <= 1e-12:
        values = [0.0, 0.0, 1.0]
    else:
        values = [value / length for value in values]
    for value in values:
        if abs(value) > 1e-9:
            if value < 0:
                values = [-item for item in values]
            break
    return [round(value, 6) for value in values]


def _intent_id(concept: dict[str, Any]) -> str:
    payload = json.dumps(concept, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"dimint_{_slug(str(concept['measurement_role']))}_{digest}"


def _renderer_contract(semantic: str, dim_type: str) -> dict[str, Any]:
    if semantic == "annular_section.radial_wall_thickness" or semantic.startswith("thin_wall."):
        renderer = "THICKNESS_RENDERER"
        placement = "RADIAL"
    elif dim_type == "DIAMETER":
        renderer = "DIAMETER_RENDERER"
        placement = "PROFILE_OR_END_DIAMETER"
    else:
        renderer = "LINEAR_DIMENSION_RENDERER"
        placement = "AXIAL_OR_LINEAR"
    return {
        "renderer": renderer,
        "placement_family": placement,
        "prefix": "Ø" if dim_type == "DIAMETER" else "",
        "unit": "mm",
    }


def _base_intent(
    dimension: dict[str, Any],
    source_extractor: str,
    source_feature_id: str,
    feature_family: str,
    measurement_role: str,
    solid_index: int,
    topology_refs: Sequence[str],
    axis: Sequence[float],
    material_relation: str,
    related_feature_ids: Sequence[str],
) -> dict[str, Any]:
    concept = {
        "solid_index": int(solid_index),
        "measurement_role": measurement_role,
        "topology_refs": sorted(str(value) for value in topology_refs),
        "axis": _axis_signature(axis),
        "view": str(dimension.get("view") or ""),
    }
    intent = {
        "schema_version": 1,
        "intent_id": _intent_id(concept),
        "source_candidates": [{
            "extractor": source_extractor,
            "dimension_id": str(dimension["id"]),
            "feature_id": source_feature_id,
        }],
        "feature": {
            "family": feature_family,
            "feature_id": source_feature_id,
            "solid_index": int(solid_index),
            "topology_refs": sorted(str(value) for value in topology_refs),
            "axis": _axis_signature(axis),
            "material_relation": material_relation,
        },
        "measurement": {
            "semantic": str(dimension["semantic"]),
            "role": measurement_role,
            "kind": str(dimension["dim_type"]),
            "nominal_value": float(dimension["nominal_value"]),
            "unit": "mm",
        },
        "projection": {
            "preferred_view": str(dimension.get("view") or ""),
            "anchors": [[float(value) for value in point[:2]] for point in dimension.get("anchors") or []],
            "anchor_space": "MODEL_VIEW_2D_MM",
        },
        "renderer_contract": _renderer_contract(str(dimension["semantic"]), str(dimension["dim_type"])),
        "relationships": {
            "parent_feature_id": source_feature_id,
            "parent_feature_ids": [source_feature_id],
            "related_feature_ids": sorted(str(value) for value in related_feature_ids),
        },
        "verification": {
            "status": str(dimension.get("verification_status") or "REVIEW_CANDIDATE"),
            "confidence": float(dimension.get("confidence") or 0.0),
            "checks": [],
            "issues": [],
        },
        "deduplication": {
            "concept_key": concept,
            "merged_source_count": 1,
            "conflict": False,
        },
    }
    for key in ("display_value", "display_text", "tolerance"):
        if key in dimension and dimension[key] is not None:
            intent["measurement"][key] = copy.deepcopy(dimension[key])
    if dimension.get("tolerance_source"):
        intent["measurement"]["tolerance_source"] = str(dimension["tolerance_source"])
    _validate_intent(intent)
    return intent


def normalise_annular_intents(result: dict[str, Any]) -> list[dict[str, Any]]:
    features = {str(item["id"]): item for item in result.get("features") or []}
    intents = []
    for dimension in result.get("dimensions") or []:
        feature_id = str(dimension["composite_feature_id"])
        feature = features[feature_id]
        semantic = str(dimension["semantic"])
        if semantic.endswith("inner_diameter"):
            role, refs, relation = "INNER_DIAMETER", [f"face:{feature['inner_face_index']}"], "INNER_MATERIAL_BOUNDARY"
        elif semantic.endswith("outer_diameter"):
            role, refs, relation = "OUTER_DIAMETER", [f"face:{feature['outer_face_index']}"], "OUTER_MATERIAL_BOUNDARY"
        elif semantic.endswith("radial_wall_thickness"):
            role = "RADIAL_WALL_THICKNESS"
            refs = [f"face:{feature['inner_face_index']}", f"face:{feature['outer_face_index']}"]
            relation = "PAIRED_INNER_OUTER_BOUNDARIES"
        else:
            role = "AXIAL_THICKNESS"
            refs = [f"face:{feature['inner_face_index']}", f"face:{feature['outer_face_index']}"]
            relation = "COMMON_AXIAL_OVERLAP"
        intents.append(_base_intent(
            dimension, "ANNULAR_COMPOSITE_V1", feature_id, "ANNULAR_SECTION", role,
            int(feature["solid_index"]), refs, feature["axis"], relation, refs,
        ))
    return intents


def normalise_solid_shaft_intents(result: dict[str, Any]) -> list[dict[str, Any]]:
    chains = {str(item["id"]): item for item in result.get("chains") or []}
    segment_lookup = {
        str(segment["id"]): (chain, segment)
        for chain in chains.values()
        for segment in chain.get("segments") or []
    }
    intents = []
    for dimension in result.get("dimensions") or []:
        chain = chains[str(dimension["shaft_chain_id"])]
        segment_id = dimension.get("segment_id")
        if segment_id:
            _, segment = segment_lookup[str(segment_id)]
            refs = [f"face:{segment['face_index']}"]
            feature_id = str(segment_id)
            if str(dimension["semantic"]).endswith("outer_diameter"):
                role, relation = "OUTER_DIAMETER", "OUTER_MATERIAL_BOUNDARY"
            else:
                role, relation = "AXIAL_SEGMENT_LENGTH", "CYLINDRICAL_INTERVAL"
            related = [
                shoulder["id"] for shoulder in chain.get("shoulders") or []
                if segment_id in {shoulder["left_segment_id"], shoulder["right_segment_id"]}
            ]
        else:
            refs = [f"solid:{chain['solid_index']}"]
            feature_id = str(chain["id"])
            role, relation, related = "AXIAL_OVERALL_LENGTH", "SOLID_VERTEX_EXTENT", [item["id"] for item in chain.get("segments") or []]
        intents.append(_base_intent(
            dimension, "SOLID_SHAFT_V1", feature_id, "SOLID_SHAFT", role,
            int(chain["solid_index"]), refs, chain["axis"], relation, related,
        ))
    return intents


def normalise_thin_wall_intents(result: dict[str, Any]) -> list[dict[str, Any]]:
    features = {str(item["id"]): item for item in result.get("features") or []}
    intents = []
    for dimension in result.get("dimensions") or []:
        feature_id = str(dimension["feature_id"])
        feature = features[feature_id]
        is_profile = str(dimension["semantic"]).endswith("thickness_profile")
        role = "THICKNESS_PROFILE" if is_profile else "NORMAL_WALL_THICKNESS"
        relation = "MULTI_STATION_PAIRED_BOUNDARIES" if is_profile else "PAIRED_MATERIAL_BOUNDARIES"
        refs = [str(value) for value in feature.get("topology_refs") or []]
        intents.append(_base_intent(
            dimension,
            "PAIRED_SURFACE_THICKNESS_V1",
            feature_id,
            "THIN_WALL",
            role,
            int(feature.get("solid_index") or 0),
            refs,
            feature.get("axis") or (0.0, 0.0, 1.0),
            relation,
            refs,
        ))
    return intents


def _validate_intent(intent: dict[str, Any]) -> None:
    checks = []
    issues = []
    measurement = intent["measurement"]
    nominal = float(measurement["nominal_value"])
    nominal_ok = math.isfinite(nominal) and nominal > 0.0
    checks.append({"name": "positive_finite_nominal", "passed": nominal_ok})
    if not nominal_ok:
        issues.append("INVALID_NOMINAL")

    view_ok = bool(intent["projection"]["preferred_view"])
    checks.append({"name": "preferred_view_present", "passed": view_ok})
    if not view_ok:
        issues.append("MISSING_VIEW")

    anchors = intent["projection"]["anchors"]
    anchors_ok = len(anchors) == 2 and all(len(point) == 2 and all(math.isfinite(float(value)) for value in point) for point in anchors)
    checks.append({"name": "two_finite_anchors", "passed": anchors_ok})
    anchor_span = math.dist(anchors[0], anchors[1]) if anchors_ok else 0.0
    span_ok = anchors_ok and abs(anchor_span - nominal) <= max(0.001, nominal * 0.001)
    checks.append({"name": "anchor_span_matches_nominal", "passed": span_ok, "measured_span": anchor_span})
    if not anchors_ok:
        issues.append("INVALID_ANCHORS")
    elif not span_ok:
        issues.append("ANCHOR_NOMINAL_MISMATCH")

    topology_ok = bool(intent["feature"]["topology_refs"])
    checks.append({"name": "topology_reference_present", "passed": topology_ok})
    if not topology_ok:
        issues.append("MISSING_TOPOLOGY_REFERENCE")

    renderer_ok = intent["renderer_contract"]["renderer"] in {
        "DIAMETER_RENDERER", "LINEAR_DIMENSION_RENDERER", "THICKNESS_RENDERER",
    }
    checks.append({"name": "supported_renderer_contract", "passed": renderer_ok})
    if not renderer_ok:
        issues.append("UNSUPPORTED_RENDERER")

    intent["verification"]["checks"] = checks
    intent["verification"]["issues"] = issues
    if issues:
        intent["verification"]["status"] = "INVALID"
        intent["verification"]["confidence"] = 0.0


def deduplicate_dimension_intents(intents: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge the same topological measurement; preserve and flag conflicts."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for intent in intents:
        key = json.dumps(intent["deduplication"]["concept_key"], sort_keys=True, separators=(",", ":"))
        grouped.setdefault(key, []).append(copy.deepcopy(intent))
    merged = []
    duplicates = []
    conflicts = []
    for key, group in grouped.items():
        best = max(group, key=lambda item: (
            STATUS_PRIORITY.get(item["verification"]["status"], -1),
            float(item["verification"]["confidence"]),
        ))
        values = [float(item["measurement"]["nominal_value"]) for item in group]
        tolerance = max(0.001, max(values) * 0.0001)
        is_conflict = max(values) - min(values) > tolerance
        if is_conflict:
            conflict_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
            for item in group:
                source_id = item["source_candidates"][0]["dimension_id"]
                source_digest = hashlib.sha1(source_id.encode("utf-8")).hexdigest()[:8]
                item["intent_id"] = f"{item['intent_id']}_conflict_{source_digest}"
                item["verification"]["status"] = "REVIEW_CONFLICT"
                item["verification"]["issues"].append("CONFLICTING_NOMINAL_FOR_SAME_TOPOLOGY")
                item["deduplication"]["conflict"] = True
                item["deduplication"]["conflict_group_id"] = conflict_id
                merged.append(item)
            conflicts.append({
                "conflict_group_id": conflict_id,
                "concept_key": group[0]["deduplication"]["concept_key"],
                "nominal_values": values,
                "intent_ids": [item["intent_id"] for item in group],
            })
            continue
        all_sources = [source for item in group for source in item["source_candidates"]]
        best["source_candidates"] = all_sources
        best["relationships"]["parent_feature_ids"] = sorted({
            item["relationships"]["parent_feature_id"] for item in group
        })
        best["relationships"]["related_feature_ids"] = sorted({
            related
            for item in group
            for related in item["relationships"]["related_feature_ids"]
        })
        best["deduplication"]["merged_source_count"] = len(all_sources)
        merged.append(best)
        if len(group) > 1:
            duplicates.append({
                "kept_intent_id": best["intent_id"],
                "merged_count": len(group),
                "source_dimension_ids": [item["source_candidates"][0]["dimension_id"] for item in group],
            })
    merged.sort(key=lambda item: (
        item["feature"]["solid_index"], item["projection"]["preferred_view"],
        item["measurement"]["role"], item["intent_id"],
    ))
    return merged, duplicates, conflicts


def apply_projection_bounds_validation(
    intents: Sequence[dict[str, Any]],
    bounds_by_solid: dict[int, dict[str, Sequence[float]]],
) -> None:
    """Require both anchors to lie inside their owning solid projection."""
    for intent in intents:
        solid_index = int(intent["feature"]["solid_index"])
        view_name = str(intent["projection"]["preferred_view"])
        bounds = (bounds_by_solid.get(solid_index) or {}).get(view_name)
        anchors = intent["projection"].get("anchors") or []
        if bounds and len(bounds) == 4:
            x0, y0, x1, y1 = [float(value) for value in bounds]
            diagonal = math.hypot(x1 - x0, y1 - y0)
            margin = max(0.02, diagonal * 0.002)
            passed = len(anchors) == 2 and all(
                x0 - margin <= float(point[0]) <= x1 + margin
                and y0 - margin <= float(point[1]) <= y1 + margin
                for point in anchors
            )
        else:
            margin = None
            passed = False
        intent["verification"]["checks"].append({
            "name": "anchors_inside_owning_solid_projection",
            "passed": passed,
            "solid_index": solid_index,
            "view": view_name,
            "solid_projection_bounds": list(bounds) if bounds else None,
            "margin": margin,
        })
        if not passed:
            issue = "ANCHORS_OUTSIDE_OWNING_SOLID_PROJECTION" if bounds else "MISSING_OWNING_SOLID_PROJECTION"
            if issue not in intent["verification"]["issues"]:
                intent["verification"]["issues"].append(issue)
            intent["verification"]["status"] = "INVALID"
            intent["verification"]["confidence"] = 0.0


def build_dimension_intent_set(
    annular_result: dict[str, Any] | None = None,
    solid_shaft_result: dict[str, Any] | None = None,
    thin_wall_result: dict[str, Any] | None = None,
    projection_bounds_by_solid: dict[int, dict[str, Sequence[float]]] | None = None,
) -> dict[str, Any]:
    raw = []
    if annular_result:
        raw.extend(normalise_annular_intents(annular_result))
    if solid_shaft_result:
        raw.extend(normalise_solid_shaft_intents(solid_shaft_result))
    if thin_wall_result:
        raw.extend(normalise_thin_wall_intents(thin_wall_result))
    if projection_bounds_by_solid is not None:
        apply_projection_bounds_validation(raw, projection_bounds_by_solid)
    intents, duplicates, conflicts = deduplicate_dimension_intents(raw)
    status_counts = {
        status: sum(item["verification"]["status"] == status for item in intents)
        for status in ("AUTO_VERIFIED", "REVIEW_CANDIDATE", "REVIEW_CONFLICT", "INVALID")
    }
    return {
        "schema_version": 1,
        "method": "UNIFIED_DIMENSION_INTENT_V1",
        "raw_intent_count": len(raw),
        "unified_intent_count": len(intents),
        "deduplicated_count": len(raw) - len(intents),
        "status_counts": status_counts,
        "intents": intents,
        "duplicate_groups": duplicates,
        "conflict_groups": conflicts,
    }
