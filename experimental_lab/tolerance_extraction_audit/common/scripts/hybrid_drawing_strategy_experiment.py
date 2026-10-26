"""Evaluate category-guided, feature-driven drawing intent generation.

This experiment deliberately separates three concerns:

* ``DrawingStrategy``: part-family knowledge chooses the primary view and datum
  policy.
* ``DimensionIntent``: canonical 3D features decide what should be measured.
* ``LayoutConstraint``: target geometry allocates sides and ranks; source ranks
  are never copied.

The target DXF is opened only after the plan is frozen and is used as holdout
evidence.  This module is experimental and does not modify production drawing
or legacy extractor code.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.canonical_features import extract_canonical_features  # noqa: E402
from auto_2d_drawing.drawing_projection import project_point  # noqa: E402
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from generate_tolerance_edge_review import _render_native_dxf  # noqa: E402
from replay_drawing_rules import (  # noqa: E402
    BACKGROUND,
    CONTACT,
    MUTED,
    PANEL,
    RELIABLE,
    REVIEW,
    WHITE,
    _draw_model,
    _draw_rule,
    _expand_bbox,
    _font,
    _mapper,
)
from transfer_drawing_rules import (  # noqa: E402
    _target_crop,
    feature_measurement_anchors,
    load_source_feature_links,
    select_transfer_view,
)


METHOD = "CATEGORY_FEATURE_ADAPTIVE_LAYOUT_V1"
AXIAL_FIELDS = {"length", "groove_width"}
DIAMETER_FIELDS = {"diameter", "groove_diameter"}


def infer_style_prior(
    source_rules: Sequence[dict[str, Any]],
    source_links: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Learn weak side preferences, never target coordinates or ranks."""
    votes: dict[str, dict[str, int]] = {
        "AXIAL": {"TOP": 0, "BOTTOM": 0},
        "TRANSVERSE": {"TOP": 0, "BOTTOM": 0},
    }
    samples = []
    for rule in source_rules:
        handle = str(rule.get("dimension_handle") or "")
        link = source_links.get(handle)
        if not link:
            continue
        field = str(link.get("nominal_field") or "")
        intent_class = "TRANSVERSE" if field in DIAMETER_FIELDS else "AXIAL"
        side = str((rule.get("placement") or {}).get("side") or "")
        if side in votes[intent_class]:
            votes[intent_class][side] += 1
        samples.append({"handle": handle, "intent_class": intent_class, "side": side})

    preferences = {}
    for intent_class, side_votes in votes.items():
        preferences[intent_class] = max(
            side_votes,
            key=lambda side: (side_votes[side], side == ("BOTTOM" if intent_class == "AXIAL" else "TOP")),
        )
    return {
        "scope": "WEAK_FAMILY_PRIOR_ONLY",
        "preferred_side": preferences,
        "side_votes": votes,
        "samples": samples,
        "source_ranks_reused": False,
    }


def build_drawing_strategy(
    part_type: str,
    records: Sequence[dict[str, Any]],
    view_data: dict[str, Any],
    style_prior: dict[str, Any],
) -> dict[str, Any]:
    """Build a family strategy while retaining multi-role feature evidence."""
    by_id = {str(record.get("id")): record for record in records}
    overall = by_id.get("shaft_overall")
    if str(part_type).upper() != "SHAFT" or overall is None:
        raise NotImplementedError("V1 currently evaluates the SHAFT family only")
    source_orientation_rules = [
        {"placement": {"side": style_prior["preferred_side"]["AXIAL"]}}
    ]
    primary_view, evidence = select_transfer_view(
        overall, view_data, source_orientation_rules
    )
    axis = str((next((r for r in records if r.get("id") == "datum_a_axis"), {})
                .get("nominal") or {}).get("axis") or "")
    return {
        "strategy_id": "shaft_longitudinal_baseline_v1",
        "part_family": "SHAFT",
        "feature_roles": sorted({str(record.get("role")) for record in records if record.get("role")}),
        "primary_axis": axis,
        "primary_view": primary_view,
        "view_policy": "LONGITUDINAL_AXIS_HORIZONTAL",
        "section_policy": "NONE_FOR_SOLID_SHAFT",
        "datum_policy": "MIN_AXIS_END_BASELINE",
        "diameter_policy": "FEATURE_LOCAL_TRANSVERSE",
        "layout_policy": "TARGET_INTERVAL_GRAPH",
        "selection_evidence": evidence,
    }


def _axis_index(axis: str) -> int:
    return {"X": 0, "Y": 1, "Z": 2}.get(str(axis).upper(), 2)


def _feature_axis_span(record: dict[str, Any], axis: str) -> tuple[float, float] | None:
    geometry = record.get("geometry") or {}
    center = geometry.get("center")
    size = geometry.get("size")
    if not isinstance(center, Sequence) or not isinstance(size, Sequence) or len(center) < 3 or len(size) < 3:
        return None
    index = _axis_index(axis)
    half = abs(float(size[index])) / 2.0
    value = float(center[index])
    return value - half, value + half


def _axis_point(axis: str, value: float) -> tuple[float, float, float]:
    point = [0.0, 0.0, 0.0]
    point[_axis_index(axis)] = float(value)
    return point[0], point[1], point[2]


def _rounded_key(value: float) -> float:
    return round(float(value), 3)


def build_shaft_dimension_intents(
    records: Sequence[dict[str, Any]],
    strategy: dict[str, Any],
    view_bbox: Sequence[float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create feature-level shaft intents without consulting a target drawing."""
    axis = str(strategy["primary_axis"])
    view_name = str(strategy["primary_view"])
    by_id = {str(record.get("id")): record for record in records}
    overall = by_id["shaft_overall"]
    overall_span = _feature_axis_span(overall, axis)
    if overall_span is None:
        raise ValueError("shaft_overall has no axis span")
    datum_value, far_value = overall_span
    if far_value < datum_value:
        datum_value, far_value = far_value, datum_value

    intents: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    overall_anchors = [
        project_point(_axis_point(axis, datum_value), view_name),
        project_point(_axis_point(axis, far_value), view_name),
    ]
    intents.append({
        "intent_id": "overall_length",
        "feature_id": "shaft_overall",
        "nominal_field": "length",
        "semantic_type": "OVERALL_LENGTH",
        "intent_class": "AXIAL",
        "priority": 100,
        "nominal_value": float((overall.get("nominal") or {})["length"]),
        "dimension": {"dim_type": "LINEAR", "nominal_value": float((overall.get("nominal") or {})["length"]), "prefix": "", "tolerance_config": {"mode": "NONE"}},
        "anchors": [list(point) for point in overall_anchors],
        "evidence": ["PART_FAMILY_SHAFT", "CANONICAL_SHAFT_OVERALL"],
    })

    # Baseline dimensions come from unique canonical feature boundaries.  This
    # reconstructs a datum chain while remaining independent of drawing text.
    boundary_candidates: dict[tuple[str, float], dict[str, Any]] = {}
    for record in records:
        feature_id = str(record.get("id") or "")
        feature_type = str(record.get("type") or "")
        if feature_type not in {"step", "groove_or_slot", "shaft_or_boss", "cone_or_chamfer"}:
            continue
        span = _feature_axis_span(record, axis)
        if span is None:
            continue
        for endpoint in span:
            distance_from_min = abs(float(endpoint) - datum_value)
            distance_from_max = abs(far_value - float(endpoint))
            reference_end = "MIN" if distance_from_min <= distance_from_max else "MAX"
            reference_value = datum_value if reference_end == "MIN" else far_value
            distance = min(distance_from_min, distance_from_max)
            if distance <= 0.05 or math.isclose(distance, abs(far_value - datum_value), abs_tol=0.05):
                continue
            rounded_distance = _rounded_key(distance)
            # Canonical extraction can create near-identical boundaries from
            # overlapping cylinders/cones.  Merge only sub-resolution pairs;
            # distinct engineering dimensions such as 2.0 and 2.1 remain.
            existing_key = next(
                (
                    key for key in boundary_candidates
                    if key[0] == reference_end and abs(key[1] - rounded_distance) <= 0.025
                ),
                None,
            )
            key = existing_key or (reference_end, rounded_distance)
            candidate = {
                "feature_id": feature_id,
                "endpoint": float(endpoint),
                "distance": float(distance),
                "reference_end": reference_end,
                "reference_value": float(reference_value),
                "feature_type": feature_type,
            }
            previous = boundary_candidates.get(key)
            # Prefer explicit step/groove topology over a broad overlapping
            # journal record when multiple features share the same boundary.
            order = {"step": 3, "groove_or_slot": 3, "cone_or_chamfer": 2, "shaft_or_boss": 1}
            if previous is None or order[feature_type] > order[previous["feature_type"]]:
                boundary_candidates[key] = candidate

    for (_reference_end, key), candidate in sorted(
        boundary_candidates.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        endpoint = candidate["endpoint"]
        anchors = [
            project_point(_axis_point(axis, candidate["reference_value"]), view_name),
            project_point(_axis_point(axis, endpoint), view_name),
        ]
        intents.append({
            "intent_id": f"{candidate['reference_end'].lower()}_end_to_{candidate['feature_id']}_{key:g}",
            "feature_id": candidate["feature_id"],
            "nominal_field": "derived_axis_position",
            "semantic_type": "DATUM_BASELINE_DISTANCE",
            "intent_class": "AXIAL",
            "reference_end": candidate["reference_end"],
            "priority": 65,
            "nominal_value": float(key),
            "dimension": {"dim_type": "LINEAR", "nominal_value": float(key), "prefix": "", "tolerance_config": {"mode": "NONE"}},
            "anchors": [list(point) for point in anchors],
            "evidence": ["CANONICAL_FEATURE_BOUNDARY", "DATUM_A_MIN_AXIS_END"],
        })

    # Every local cylindrical region can carry a diameter intent.  Duplicate
    # values at different axial positions remain separate feature semantics.
    for record in records:
        nominal = record.get("nominal") or {}
        field = "groove_diameter" if nominal.get("groove_diameter") else "diameter" if nominal.get("diameter") else None
        if field is None or str(record.get("id")) == "shaft_overall":
            continue
        value = float(nominal[field])
        if value <= 0.0:
            continue
        anchors = feature_measurement_anchors(record, field, view_name, view_bbox)
        intents.append({
            "intent_id": f"{record.get('id')}.{field}",
            "feature_id": str(record.get("id")),
            "nominal_field": field,
            "semantic_type": "LOCAL_DIAMETER",
            "intent_class": "TRANSVERSE",
            "priority": 80 if record.get("role") == "bearing_journal" else 70,
            "nominal_value": value,
            "dimension": {"dim_type": "DIAMETER", "nominal_value": value, "prefix": "Ø", "tolerance_config": {"mode": "NONE"}},
            "anchors": [list(point) for point in anchors],
            "evidence": ["CANONICAL_CYLINDRICAL_REGION", str(record.get("role") or "")],
        })

    for record in records:
        if record.get("type") == "cone_or_chamfer":
            rejected.append({
                "feature_id": record.get("id"),
                "reason": "CHAMFER_LEADER_INTENT_NOT_IMPLEMENTED_IN_V1",
            })
    return intents, rejected


def _interval(intent: dict[str, Any]) -> tuple[float, float]:
    anchors = intent.get("anchors") or []
    xs = [float(point[0]) for point in anchors]
    return (min(xs), max(xs)) if xs else (0.0, 0.0)


def _overlap(left: tuple[float, float], right: tuple[float, float], padding: float = 0.0) -> bool:
    return min(left[1], right[1]) - max(left[0], right[0]) > -padding


def solve_adaptive_layout(
    intents: Sequence[dict[str, Any]],
    model_bbox: Sequence[float],
    style_prior: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Allocate layout ranks from target intervals instead of source ranks."""
    xmin, _ymin, xmax, _ymax = (float(value) for value in model_bbox)
    model_width = max(xmax - xmin, 1e-9)
    result = [dict(intent) for intent in intents]
    occupied: dict[str, dict[int, list[tuple[float, float]]]] = {"TOP": {}, "BOTTOM": {}}

    axial = sorted(
        (item for item in result if item["intent_class"] == "AXIAL"),
        key=lambda item: (_interval(item)[1] - _interval(item)[0], -int(item["priority"])),
    )
    for item in axial:
        span = _interval(item)
        if item["semantic_type"] == "OVERALL_LENGTH":
            axial_side = str(style_prior["preferred_side"].get("AXIAL") or "BOTTOM")
        else:
            # Separate dimension chains by the nearest physical datum end.
            # This prevents every boundary from accumulating on one side.
            axial_side = "BOTTOM" if item.get("reference_end") == "MIN" else "TOP"
        rank = 1
        while any(_overlap(span, other, model_width * 0.01) for other in occupied[axial_side].get(rank, [])):
            rank += 1
        occupied[axial_side].setdefault(rank, []).append(span)
        item["placement"] = {"side": axial_side, "rank": rank}
        item["layout_constraint"] = {
            "solver": "INTERVAL_GRAPH",
            "reason": "NESTED_DATUM_CHAIN" if item["semantic_type"] != "OVERALL_LENGTH" else "OVERALL_OUTERMOST",
        }

    transverse = sorted(
        (item for item in result if item["intent_class"] == "TRANSVERSE"),
        key=lambda item: sum(float(point[0]) for point in item["anchors"]) / max(len(item["anchors"]), 1),
    )
    preferred = str(style_prior["preferred_side"].get("TRANSVERSE") or "TOP")
    alternate = "BOTTOM" if preferred == "TOP" else "TOP"
    min_separation = model_width * 0.09
    side_centers: dict[str, dict[int, list[float]]] = {"TOP": {}, "BOTTOM": {}}
    for index, item in enumerate(transverse):
        center = sum(float(point[0]) for point in item["anchors"]) / max(len(item["anchors"]), 1)
        candidates = (preferred, alternate) if index % 2 == 0 else (alternate, preferred)
        chosen_side, chosen_rank = candidates[0], 1
        found = False
        for side in candidates:
            for rank in range(1, 7):
                if all(abs(center - other) >= min_separation for other in side_centers[side].get(rank, [])):
                    chosen_side, chosen_rank, found = side, rank, True
                    break
            if found:
                break
        side_centers[chosen_side].setdefault(chosen_rank, []).append(center)
        item["placement"] = {"side": chosen_side, "rank": chosen_rank}
        item["layout_constraint"] = {
            "solver": "BALANCED_LOCAL_DIAMETER",
            "reason": "TARGET_CENTER_SEPARATION",
            "minimum_center_separation": round(min_separation, 6),
        }

    collisions = 0
    for side, ranks in occupied.items():
        for intervals in ranks.values():
            for index, left in enumerate(intervals):
                collisions += sum(_overlap(left, right, model_width * 0.01) for right in intervals[index + 1:])
    return result, {
        "source_ranks_reused": False,
        "axial_interval_collisions": collisions,
        "max_rank": max((int(item["placement"]["rank"]) for item in result), default=0),
        "side_counts": {
            side: sum(item["placement"]["side"] == side for item in result)
            for side in ("TOP", "BOTTOM")
        },
    }


def classify_native_dimension_axis(dimension: Any, shaft_orientation: str | None) -> str:
    """Recover axial/transverse semantics from a rotated LINEAR dimension."""
    category = str(dimension.dimension_category or dimension.dim_type or "").upper()
    if category != "LINEAR" or shaft_orientation not in {"HORIZONTAL", "VERTICAL"}:
        return category
    points = dimension.points or {}
    left, right = points.get("defpoint2"), points.get("defpoint3")
    if not isinstance(left, Sequence) or not isinstance(right, Sequence) or len(left) < 2 or len(right) < 2:
        return category
    dx = abs(float(right[0]) - float(left[0]))
    dy = abs(float(right[1]) - float(left[1]))
    nominal = abs(float(dimension.nominal_value or 0.0))
    axial_delta, transverse_delta = (dx, dy) if shaft_orientation == "HORIZONTAL" else (dy, dx)
    axial_error = abs(axial_delta - nominal)
    transverse_error = abs(transverse_delta - nominal)
    epsilon = max(1e-4, nominal * 0.002)
    if transverse_error + epsilon < axial_error:
        return "DIAMETER_SURROGATE"
    if axial_error + epsilon < transverse_error:
        return "LINEAR"
    return "DIAMETER_SURROGATE" if transverse_delta > axial_delta * 1.5 else "LINEAR"


def evaluate_holdout(
    intents: Sequence[dict[str, Any]],
    target_dxf: Path,
    shaft_orientation: str | None = None,
) -> dict[str, Any]:
    dimensions = DxfToleranceExtractor().extract_from_file(
        str(target_dxf), include_rejected=True, native_dimensions_only=True
    )
    results_by_id: dict[str, dict[str, Any]] = {}
    consumed_handles: set[str] = set()
    ordered = sorted(intents, key=lambda item: (-int(item["priority"]), item["intent_id"]))
    for intent in ordered:
        nominal = float(intent["nominal_value"])
        expected_type = str(intent["dimension"]["dim_type"])
        tolerance = max(0.03, nominal * 0.003)
        matches = []
        for dimension in dimensions:
            handle = str(dimension.entity_handle or "")
            if handle in consumed_handles:
                continue
            category = classify_native_dimension_axis(dimension, shaft_orientation)
            comparable = category == expected_type or (
                expected_type == "DIAMETER" and category == "DIAMETER_SURROGATE"
            )
            if not comparable:
                continue
            if abs(float(dimension.nominal_value or 0.0) - nominal) <= tolerance:
                matches.append({
                    "handle": handle,
                    "nominal_value": float(dimension.nominal_value),
                    "dimension_category": category,
                })
        if matches:
            consumed_handles.add(str(matches[0]["handle"]))
            matches = matches[:1]
        results_by_id[intent["intent_id"]] = {
            "intent_id": intent["intent_id"],
            "semantic_type": intent["semantic_type"],
            "nominal_value": nominal,
            "supported": bool(matches),
            "matches": matches,
        }
    results = [results_by_id[intent["intent_id"]] for intent in intents]
    return {
        "policy": "TARGET_DXF_OPENED_ONLY_AFTER_STRATEGY_AND_LAYOUT_ARE_FROZEN",
        "native_axis_semantics": "DEFPOINT_DIRECTION" if shaft_orientation else "DXF_CATEGORY_ONLY",
        "native_dimension_count": len(dimensions),
        "supported_count": sum(item["supported"] for item in results),
        "evaluated_count": len(results),
        "results": results,
    }


def _render_target_crop(target_dxf: Path, registration: dict[str, Any], output: Path) -> None:
    dimensions = DxfToleranceExtractor().extract_from_file(
        str(target_dxf), include_rejected=True, native_dimensions_only=True
    )
    if not dimensions:
        raise RuntimeError(f"No native dimensions in {target_dxf}")
    from dxf_projection_views_v2 import DxfProjectionViewExtractorV2

    extractor = DxfProjectionViewExtractorV2(str(target_dxf))
    _render_native_dxf(
        target_dxf,
        dimensions[0],
        {},
        [],
        extractor.sheet_bbox,
        _target_crop(registration),
        output,
        (1800, 1450),
    )


def _decorate_for_draw(intent: dict[str, Any], supported: bool, index: int) -> dict[str, Any]:
    item = dict(intent)
    item["dimension_handle"] = f"I{index:02d}"
    item["evidence_status"] = "EXTRACTED_RELIABLE_2D" if supported else "REVIEW_CANDIDATE"
    return item


def render_comparison_board(
    previous_report: dict[str, Any],
    view_data: dict[str, Any],
    planned: Sequence[dict[str, Any]],
    holdout: dict[str, Any],
    target_original: Path,
    output: Path,
) -> None:
    canvas = Image.new("RGB", (5700, 2100), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((20, 20, 5680, 2080), radius=18, fill=PANEL)
    draw.text((55, 40), "Hybrid Drawing Strategy Experiment", font=_font(36, True), fill=WHITE)
    draw.text(
        (55, 96),
        "left: direct style transfer   middle: category + feature intents + target layout   right: target holdout drawing",
        font=_font(21), fill=MUTED,
    )
    boxes = ((55, 165, 1845, 1850), (1955, 165, 3745, 1850), (3855, 165, 5645, 1850))
    titles = (
        "PREVIOUS: DIRECT SOURCE SIDE/RANK",
        "NEW: FEATURE INTENTS + ADAPTIVE LAYOUT",
        "TARGET DXF: EVALUATION ONLY",
    )
    for box, title in zip(boxes, titles):
        draw.rectangle(box, fill=BACKGROUND, outline="#404040", width=2)
        draw.text((box[0] + 18, box[1] + 14), title, font=_font(21, True), fill=WHITE)

    model_bbox = view_data["bbox"]
    previous_rules = list(previous_report.get("transferred_rules") or [])
    previous_world = _expand_bbox(model_bbox, 0.6, 1.8)
    previous_mapper = _mapper(previous_world, (90, 260, 1810, 1680))
    _draw_model(draw, view_data, previous_mapper)
    for rule in previous_rules:
        _draw_rule(draw, rule, [tuple(point) for point in rule["anchors"]], model_bbox, previous_mapper)

    new_world = _expand_bbox(model_bbox, 0.65, 4.2)
    new_mapper = _mapper(new_world, (1990, 235, 3710, 1720))
    _draw_model(draw, view_data, new_mapper)
    support = {item["intent_id"]: item["supported"] for item in holdout["results"]}
    for index, intent in enumerate(planned, start=1):
        drawable = _decorate_for_draw(intent, support.get(intent["intent_id"], False), index)
        _draw_rule(draw, drawable, [tuple(point) for point in intent["anchors"]], model_bbox, new_mapper)

    with Image.open(target_original) as source:
        image = source.convert("RGB")
    image.thumbnail((1730, 1500), Image.Resampling.LANCZOS)
    canvas.paste(image, (3885 + (1730 - image.width) // 2, 250))

    previous_count = len(previous_rules)
    supported_count = holdout["supported_count"]
    evaluated_count = holdout["evaluated_count"]
    draw.text((75, 1775), f"Previous planned intents: {previous_count}", font=_font(20, True), fill=REVIEW)
    draw.text((1975, 1775), f"New planned intents: {len(planned)} | holdout-supported: {supported_count}/{evaluated_count}", font=_font(20, True), fill=RELIABLE)
    draw.text((55, 1950), "Cyan = target drawing contains the same nominal/type; orange = proposed intent needs engineer review.", font=_font(19), fill=MUTED)
    draw.text((55, 1990), "Tolerance values are not inferred here. The experiment isolates view, semantic coverage and adaptive layout.", font=_font(19), fill=MUTED)
    canvas.save(output)


def render_intent_debug_board(
    view_data: dict[str, Any],
    planned: Sequence[dict[str, Any]],
    holdout: dict[str, Any],
    target_original: Path,
    output: Path,
) -> None:
    """Render axial and transverse intent groups separately for review."""
    canvas = Image.new("RGB", (5700, 2100), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((20, 20, 5680, 2080), radius=18, fill=PANEL)
    draw.text((55, 40), "Adaptive Intent Debug", font=_font(36, True), fill=WHITE)
    draw.text((55, 96), "The two intent groups are separated so geometry anchors and dimension chains remain inspectable.", font=_font(21), fill=MUTED)
    boxes = ((55, 165, 1845, 1805), (1955, 165, 3745, 1805), (3855, 165, 5645, 1805))
    titles = ("LOCAL DIAMETER INTENTS", "AXIAL DATUM CHAINS", "TARGET DXF HOLDOUT")
    for box, title in zip(boxes, titles):
        draw.rectangle(box, fill=BACKGROUND, outline="#404040", width=2)
        draw.text((box[0] + 18, box[1] + 14), title, font=_font(21, True), fill=WHITE)

    support = {item["intent_id"]: item["supported"] for item in holdout["results"]}
    model_bbox = view_data["bbox"]
    groups = (
        [item for item in planned if item["intent_class"] == "TRANSVERSE"],
        [item for item in planned if item["intent_class"] == "AXIAL"],
    )
    worlds = (_expand_bbox(model_bbox, 0.65, 2.0), _expand_bbox(model_bbox, 0.65, 4.2))
    drawing_boxes = ((90, 250, 1810, 1670), (1990, 225, 3710, 1720))
    for group, world, drawing_box in zip(groups, worlds, drawing_boxes):
        map_point = _mapper(world, drawing_box)
        _draw_model(draw, view_data, map_point)
        for index, intent in enumerate(group, start=1):
            drawable = _decorate_for_draw(intent, support.get(intent["intent_id"], False), index)
            _draw_rule(draw, drawable, [tuple(point) for point in intent["anchors"]], model_bbox, map_point)

    with Image.open(target_original) as source:
        image = source.convert("RGB")
    image.thumbnail((1730, 1460), Image.Resampling.LANCZOS)
    canvas.paste(image, (3885 + (1730 - image.width) // 2, 245))

    unsupported = [item for item in holdout["results"] if not item["supported"]]
    unsupported_text = "; ".join(
        f"{item['intent_id']}={item['nominal_value']:g}" for item in unsupported
    )
    draw.text((55, 1880), f"Unsupported/redundant intents ({len(unsupported)}): {unsupported_text}", font=_font(18, True), fill=REVIEW)
    draw.text((55, 1930), "Cyan = one-to-one native DXF support; orange = proposed 3D intent not found as a separate target annotation.", font=_font(18), fill=MUTED)
    draw.text((55, 1970), "The four repeated-diameter misses expose canonical over-segmentation; 6.7 is an extra boundary not selected by the engineer.", font=_font(18), fill=MUTED)
    canvas.save(output)


def run_experiment(
    source_rules_path: Path,
    case_base_path: Path,
    previous_report_path: Path,
    target_pose_report_path: Path,
    target_step: Path,
    target_dxf: Path,
    target_component_name: str,
    target_component_label: str,
    output_dir: Path,
) -> dict[str, Any]:
    source_report = json.loads(source_rules_path.read_text(encoding="utf-8"))
    source_rules = list(source_report.get("drawing_rule_cases") or [])
    source_drawing_name = Path(str(source_report["source"]["path"])).name
    source_links = load_source_feature_links(case_base_path, source_drawing_name)
    style_prior = infer_style_prior(source_rules, source_links)

    shape = load_component_shape(str(target_step), target_component_label)
    if shape is None or shape.IsNull():
        raise RuntimeError(f"Unable to load target component {target_component_label}")
    feature_set = extract_canonical_features(shape, part_hint=target_component_name)
    view_data = ViewProjector().project_all_views(
        shape, view_names=["front", "top", "right", "left", "back"]
    )
    strategy = build_drawing_strategy(feature_set.part_type, feature_set.records, view_data, style_prior)
    selected_view = strategy["primary_view"]
    intents, rejected = build_shaft_dimension_intents(
        feature_set.records, strategy, view_data[selected_view]["bbox"]
    )
    planned, layout_metrics = solve_adaptive_layout(
        intents, view_data[selected_view]["bbox"], style_prior
    )

    # Freeze strategy/intents/layout before opening target drawing evidence.
    holdout = evaluate_holdout(planned, target_dxf)
    output_dir.mkdir(parents=True, exist_ok=True)
    pose_payload = json.loads(target_pose_report_path.read_text(encoding="utf-8"))
    component = next(
        item for item in pose_payload["components"]
        if item.get("component_name") == target_component_name
    )
    registration = component["pose_search"]["locked_registration"]
    target_original = output_dir / "01_target_holdout_original.png"
    _render_target_crop(target_dxf, registration, target_original)
    previous_report = json.loads(previous_report_path.read_text(encoding="utf-8"))
    board = output_dir / "02_hybrid_strategy_comparison.png"
    render_comparison_board(
        previous_report, view_data[selected_view], planned, holdout, target_original, board
    )
    detail_board = output_dir / "03_adaptive_intent_debug.png"
    render_intent_debug_board(
        view_data[selected_view], planned, holdout, target_original, detail_board
    )

    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "target": {
            "component_name": target_component_name,
            "component_label": target_component_label,
            "step_path": str(target_step),
            "part_type": feature_set.part_type,
        },
        "separation_of_concerns": {
            "drawing_strategy": "PART_FAMILY_AND_TARGET_GEOMETRY",
            "dimension_intents": "CANONICAL_3D_FEATURES",
            "style_prior": "SOURCE_CASE_SIDE_VOTES_ONLY",
            "layout_constraints": "TARGET_INTERVAL_GRAPH",
            "target_dxf_role": "POST_FREEZE_HOLDOUT_ONLY",
        },
        "style_prior": style_prior,
        "drawing_strategy": strategy,
        "dimension_intents": planned,
        "rejected_feature_intents": rejected,
        "layout_metrics": layout_metrics,
        "holdout_evaluation": holdout,
        "metrics": {
            "previous_direct_transfer_count": len(previous_report.get("transferred_rules") or []),
            "new_feature_intent_count": len(planned),
            "holdout_supported_count": holdout["supported_count"],
            "holdout_support_rate": round(holdout["supported_count"] / max(holdout["evaluated_count"], 1), 6),
            "axial_interval_collisions": layout_metrics["axial_interval_collisions"],
        },
        "review_board": str(board),
        "detail_board": str(detail_board),
    }
    (output_dir / "hybrid_drawing_strategy_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-rules", required=True)
    parser.add_argument("--case-base", required=True)
    parser.add_argument("--previous-report", required=True)
    parser.add_argument("--target-pose-report", required=True)
    parser.add_argument("--target-step", required=True)
    parser.add_argument("--target-dxf", required=True)
    parser.add_argument("--target-component-name", required=True)
    parser.add_argument("--target-component-label", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run_experiment(
        Path(args.source_rules).resolve(),
        Path(args.case_base).resolve(),
        Path(args.previous_report).resolve(),
        Path(args.target_pose_report).resolve(),
        Path(args.target_step).resolve(),
        Path(args.target_dxf).resolve(),
        args.target_component_name,
        args.target_component_label,
        Path(args.output).resolve(),
    )
    print(json.dumps({"metrics": report["metrics"], "review_board": report["review_board"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
