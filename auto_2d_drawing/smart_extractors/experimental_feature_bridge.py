"""Bridge Experimental Main drawing intents to production 3D/FRG features.

The drawing rule ID remains the UI/rendering identity.  A confidently matched
production feature contributes its canonical ID and 3D geometry, which is the
same identity consumed by :class:`CanonicalFeatureGraphExtractor`.

Relationship dimensions (wall thickness and pattern angles) intentionally do
not claim a single canonical feature.  They receive a dynamically derived
virtual group geometry for 3D highlighting and are marked tolerance-ineligible
until the FRG has first-class relationship nodes.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Iterable, Mapping, Sequence

from auto_2d_drawing.canonical_features import (
    CANONICAL_FEATURE_SCHEMA,
    extract_canonical_features,
)


BRIDGE_SCHEMA_VERSION = 1
_OVERALL_TYPES = {"overall", "overall_bounds", "overall_size"}
_LOCAL_TYPES = {
    "hole",
    "shaft_or_boss",
    "groove_or_slot",
    "step",
    "cone_or_chamfer",
    "fillet_or_round",
    "wall_thickness",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _metric_values(record: Mapping[str, Any], kind: str) -> list[float]:
    nominal = record.get("nominal") or {}
    geometry = record.get("geometry") or {}
    if kind == "diameter":
        keys = ("diameter", "outer_diameter", "groove_diameter", "major_diameter")
    else:
        keys = ("length", "height", "width", "thickness", "depth", "step_height")
    values: list[float] = []
    for source in (nominal, geometry):
        for key in keys:
            value = _number(source.get(key))
            if value is not None and value > 0.0:
                values.append(value)
        if kind == "diameter":
            radius = _number(source.get("radius"))
            if radius is not None and radius > 0.0:
                values.append(radius * 2.0)
    if str(record.get("type") or "") in _OVERALL_TYPES:
        for key in ("W", "H", "D"):
            value = _number(nominal.get(key))
            if value is not None and value > 0.0:
                values.append(value)
        bbox = nominal.get("bounding_box") or {}
        for key in ("W", "H", "D"):
            value = _number(bbox.get(key))
            if value is not None and value > 0.0:
                values.append(value)
    return values


def _expected_types(rule: Mapping[str, Any]) -> tuple[set[str], str, float]:
    role = str(rule.get("measurement_role") or rule.get("semantic") or "").upper()
    kind = str(rule.get("dim_type") or "").upper()
    family = str(rule.get("feature_family") or "").upper()
    candidate_kind = str(rule.get("candidate_kind") or "")

    if candidate_kind == "RECTANGULAR_HOLE_RELATION" and kind == "DIAMETER":
        return {"hole"}, "diameter", 0.68
    if "INNER_DIAMETER" in role:
        return {"hole", "groove_or_slot"}, "diameter", 0.65
    if "OUTER_DIAMETER" in role:
        # Annular extraction also reports the outside diameter of shaft
        # grooves. Preserve that semantic instead of forcing it onto a journal.
        return {"shaft_or_boss", "groove_or_slot"}, "diameter", 0.68
    if kind == "DIAMETER":
        return {"hole", "shaft_or_boss", "groove_or_slot"}, "diameter", 0.55
    if "OVERALL" in role:
        return _OVERALL_TYPES, "length", 0.70
    if "AXIAL" in role or "SEGMENT" in role or "LENGTH" in role:
        if "SHAFT" in family:
            return {"shaft_or_boss", "step", "groove_or_slot"}, "length", 0.53
        return {"hole", "shaft_or_boss", "step", "wall_thickness"}, "length", 0.46
    return set(), "length", 0.0


def _similarity(target: float, values: Iterable[float]) -> tuple[float, float | None]:
    best_value = None
    best_delta = math.inf
    for value in values:
        delta = abs(value - target)
        if delta < best_delta:
            best_delta = delta
            best_value = value
    if best_value is None:
        return 0.0, None
    tolerance = max(0.12, abs(target) * 0.025)
    return max(0.0, 1.0 - best_delta / tolerance), best_value


def _match_record(
    rule: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    expected, metric_kind, type_weight = _expected_types(rule)
    target = _number(rule.get("nominal_value"))
    if not expected or target is None or target <= 0.0:
        return None, {"status": "UNSUPPORTED", "reason": "NO_SINGLE_FEATURE_CONTRACT"}

    scored: list[tuple[float, float, str, Mapping[str, Any], float | None]] = []
    for record in records:
        record_type = str(record.get("type") or "")
        if record_type not in expected:
            continue
        similarity, matched_value = _similarity(target, _metric_values(record, metric_kind))
        if similarity <= 0.0:
            continue
        exact_type_bonus = 0.0
        role = str(rule.get("measurement_role") or "").upper()
        if "INNER_DIAMETER" in role and record_type == "hole":
            exact_type_bonus = 0.07
        elif "OUTER_DIAMETER" in role and record_type == "shaft_or_boss":
            exact_type_bonus = 0.07
        score = min(1.0, type_weight + (1.0 - type_weight) * similarity + exact_type_bonus)
        delta = abs((matched_value or 0.0) - target)
        scored.append((score, delta, str(record.get("id") or ""), record, matched_value))

    if not scored:
        return None, {"status": "UNRESOLVED", "reason": "NO_NOMINAL_AND_TYPE_MATCH"}
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    best = scored[0]
    if best[0] < 0.78:
        return None, {
            "status": "UNRESOLVED",
            "reason": "MATCH_SCORE_BELOW_THRESHOLD",
            "score": round(best[0], 6),
        }
    same_quality = [
        item for item in scored
        if abs(item[0] - best[0]) < 1e-6 and abs(item[1] - best[1]) < 1e-6
    ]
    return best[3], {
        "status": "MATCHED",
        "score": round(best[0], 6),
        "matched_nominal": best[4],
        "ambiguous_equivalent_count": len(same_quality),
        "strategy": "TYPE_AND_NOMINAL",
    }


def _bounds(records: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    used = 0
    for record in records:
        geometry = record.get("geometry") or {}
        center = geometry.get("center")
        size = geometry.get("size")
        if not (isinstance(center, (list, tuple)) and len(center) >= 3):
            continue
        if not (isinstance(size, (list, tuple)) and len(size) >= 3):
            continue
        numeric_center = [_number(center[index]) for index in range(3)]
        numeric_size = [_number(size[index]) for index in range(3)]
        if any(value is None for value in numeric_center + numeric_size):
            continue
        for index in range(3):
            half = abs(float(numeric_size[index])) / 2.0
            minimum[index] = min(minimum[index], float(numeric_center[index]) - half)
            maximum[index] = max(maximum[index], float(numeric_center[index]) + half)
        used += 1
    if not used:
        return None
    return {
        "kind": "virtual_relation_group",
        "center": [(minimum[i] + maximum[i]) / 2.0 for i in range(3)],
        "size": [max(0.3, maximum[i] - minimum[i]) for i in range(3)],
        "derived_from_feature_count": used,
    }


def _wall_pair(
    target: float,
    records: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    inner = [record for record in records if str(record.get("type") or "") == "hole"]
    outer = [record for record in records if str(record.get("type") or "") == "shaft_or_boss"]
    pairs: list[tuple[float, str, Mapping[str, Any], Mapping[str, Any]]] = []
    for inner_record in inner:
        for outer_record in outer:
            inner_values = _metric_values(inner_record, "diameter")
            outer_values = _metric_values(outer_record, "diameter")
            for inner_diameter in inner_values:
                for outer_diameter in outer_values:
                    if outer_diameter <= inner_diameter:
                        continue
                    delta = abs((outer_diameter - inner_diameter) / 2.0 - target)
                    key = f"{inner_record.get('id')}:{outer_record.get('id')}"
                    pairs.append((delta, key, inner_record, outer_record))
    if not pairs:
        return []
    pairs.sort(key=lambda item: (item[0], item[1]))
    tolerance = max(0.12, abs(target) * 0.025)
    return list(pairs[0][2:]) if pairs[0][0] <= tolerance else []


def _relation_geometry(
    rule: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    role = str(rule.get("measurement_role") or "").upper()
    if "RADIAL_WALL_THICKNESS" in role:
        target = _number(rule.get("nominal_value")) or 0.0
        related = _wall_pair(target, records)
    elif str(rule.get("candidate_kind") or "") == "RADIAL_PATTERN_RELATION":
        axis_record = next(
            (
                record for record in records
                if str((record.get("geometry") or {}).get("kind") or "") == "axis"
            ),
            None,
        )
        overall_record = next(
            (record for record in records if str(record.get("type") or "") in _OVERALL_TYPES),
            None,
        )
        axis_geometry = (axis_record or {}).get("geometry") or {}
        overall_geometry = (overall_record or {}).get("geometry") or {}
        origin = axis_geometry.get("center") or overall_geometry.get("center")
        axis = str(axis_geometry.get("axis") or "Z").upper()
        axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 2)
        overall_size = overall_geometry.get("size") or [0.0, 0.0, 0.0]
        transverse_extent = (
            max(
                (_number(overall_size[index]) or 0.0)
                for index in range(3)
                if index != axis_index
            )
            if len(overall_size) >= 3
            else 0.0
        )
        reference_radius = _number(rule.get("pattern_reference_radius"))
        if (
            reference_radius is not None
            and reference_radius > 0.0
            and isinstance(origin, (list, tuple))
            and len(origin) >= 3
        ):
            axial_extent = (
                abs(_number(overall_size[axis_index]) or 0.0)
                if len(overall_size) >= 3
                else 0.0
            )
            axial_highlight = max(
                0.3,
                min(axial_extent, max(0.6, transverse_extent * 0.05)),
            )
            size = [reference_radius * 2.0] * 3
            size[axis_index] = axial_highlight
            return {
                "kind": "virtual_relation_group",
                "center": [float(origin[index]) for index in range(3)],
                "size": size,
                "axis": axis,
                "reference_radius": reference_radius,
                "derived_from_feature_count": 0,
            }, []
        related = []
        if isinstance(origin, (list, tuple)) and len(origin) >= 3:
            threshold = max(0.5, transverse_extent * 0.05)
            for record in records:
                if str(record.get("type") or "") not in _LOCAL_TYPES:
                    continue
                center = (record.get("geometry") or {}).get("center")
                if not isinstance(center, (list, tuple)) or len(center) < 3:
                    continue
                radial_sq = sum(
                    (float(center[index]) - float(origin[index])) ** 2
                    for index in range(3)
                    if index != axis_index
                )
                if math.sqrt(radial_sq) <= threshold:
                    related.append(record)
        if not related and overall_record:
            related = [overall_record]
        if not related:
            related = [
                record for record in records
                if str(record.get("type") or "") in _LOCAL_TYPES
            ]
    else:
        related = [
            record for record in records
            if str(record.get("type") or "") in _LOCAL_TYPES
        ]
        if not related:
            related = [
                record for record in records
                if str(record.get("type") or "") in _OVERALL_TYPES
            ]
    return _bounds(related), [str(record.get("id") or "") for record in related]


def bridge_experimental_candidate_records(
    rules: Sequence[Mapping[str, Any]],
    canonical_records: Sequence[Mapping[str, Any]],
    *,
    part_type: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enrich rules with the production 3D/FRG identity contract."""
    enriched: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for source_rule in rules:
        rule = copy.deepcopy(dict(source_rule))
        old_feature_id = rule.get("canonical_feature_id") or rule.get("feature_id")
        if old_feature_id:
            rule["experimental_feature_id"] = old_feature_id
        rule.pop("canonical_feature_id", None)
        rule.pop("feature_id", None)

        role = str(rule.get("measurement_role") or "").upper()
        candidate_kind = str(rule.get("candidate_kind") or "")
        is_relation = (
            "RADIAL_WALL_THICKNESS" in role
            or candidate_kind == "RADIAL_PATTERN_RELATION"
            or (
                candidate_kind == "RECTANGULAR_HOLE_RELATION"
                and str(rule.get("dim_type") or "").upper() != "DIAMETER"
            )
        )

        if is_relation:
            geometry, related_ids = _relation_geometry(rule, canonical_records)
            bridge = {
                "status": "VIRTUAL_RELATION" if geometry else "UNRESOLVED",
                "strategy": "DYNAMIC_CANONICAL_GROUP_BOUNDS",
                "reason": None if geometry else "NO_3D_GROUP_GEOMETRY",
            }
            if geometry:
                rule["geometry"] = geometry
                rule["canonical_related_feature_ids"] = related_ids
            rule["tolerance_eligible"] = False
            rule["tolerance_block_reason"] = "RELATION_NODE_NOT_AVAILABLE_IN_FRG"
        else:
            record, bridge = _match_record(rule, canonical_records)
            if record is not None:
                canonical_id = str(record.get("id") or "")
                rule["canonical_feature_id"] = canonical_id
                rule["feature_id"] = canonical_id
                rule["canonical_feature_type"] = record.get("type")
                rule["canonical_feature_role"] = record.get("role")
                rule["canonical_nominal"] = copy.deepcopy(record.get("nominal") or {})
                rule["geometry"] = copy.deepcopy(record.get("geometry") or {})
                rule["tolerance_eligible"] = True
                rule["tolerance_block_reason"] = None
            else:
                rule["tolerance_eligible"] = False
                rule["tolerance_block_reason"] = "NO_CONFIDENT_CANONICAL_FEATURE_MATCH"

        rule["feature_bridge"] = {
            **bridge,
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "canonical_schema": CANONICAL_FEATURE_SCHEMA,
            "part_type": part_type,
        }
        status = str(bridge.get("status") or "UNRESOLVED")
        status_counts[status] = status_counts.get(status, 0) + 1
        enriched.append(rule)

    summary = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "canonical_schema": CANONICAL_FEATURE_SCHEMA,
        "part_type": part_type,
        "canonical_feature_count": len(canonical_records),
        "rule_count": len(enriched),
        "status_counts": status_counts,
        "matched_count": status_counts.get("MATCHED", 0),
        "virtual_relation_count": status_counts.get("VIRTUAL_RELATION", 0),
        "unresolved_count": status_counts.get("UNRESOLVED", 0) + status_counts.get("UNSUPPORTED", 0),
        "tolerance_eligible_count": sum(bool(rule.get("tolerance_eligible")) for rule in enriched),
        "geometry_ready_count": sum(bool(rule.get("geometry")) for rule in enriched),
    }
    return enriched, summary


def bridge_experimental_candidate_rules(
    shape: Any,
    rules: Sequence[Mapping[str, Any]],
    *,
    part_hint: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    feature_set = extract_canonical_features(shape, part_hint=part_hint)
    return bridge_experimental_candidate_records(
        rules,
        feature_set.records,
        part_type=feature_set.part_type,
    )
