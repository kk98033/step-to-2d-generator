"""Plan the smallest drawing-view set for engineer-selected features.

V1 evaluates standard orthographic views and dynamic centre half-sections.
Historical drawings are deliberately excluded from geometric coverage; they
may later contribute only weak style and view-composition priors.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from run_fan_half_section_projection_experiment import (  # noqa: E402
    _half_cut,
    _project,
    _shape_bounds,
)


METHOD = "FEATURE_COVERAGE_VIEW_PLAN_V1"
STANDARD_VIEWS = ("front", "top", "right", "left", "back")
AXIS_VIEWS = {0: ("right", "left"), 1: ("top",), 2: ("front", "back")}
BACKGROUND = "#111111"
PANEL = "#171717"
GRID = "#404040"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
MODEL = "#d4d4d4"
HIDDEN = "#525252"
ORANGE = "#f97316"
CYAN = "#22d3ee"
GREEN = "#34d399"
RED = "#f87171"


def _font(size: int, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(point) for point in edge.get("points") or []]


def _mapper(bbox: Sequence[float], box: Sequence[int]):
    x0, y0, x1, y1 = (float(value) for value in bbox)
    left, top, right, bottom = box
    width, height = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    scale = min((right - left) / width, (bottom - top) / height)
    padx = ((right - left) - width * scale) / 2.0
    pady = ((bottom - top) - height * scale) / 2.0

    def map_point(point: Sequence[float]) -> tuple[float, float]:
        return (
            left + padx + (float(point[0]) - x0) * scale,
            bottom - pady - (float(point[1]) - y0) * scale,
        )

    return map_point


def _standard_view_data(projector: ViewProjector, shape: Any) -> dict[str, dict[str, Any]]:
    return projector.project_all_views(shape, view_names=list(STANDARD_VIEWS))


def build_view_candidates(
    shape: Any,
    standard_views: dict[str, dict[str, Any]],
    needs_section: bool,
) -> list[dict[str, Any]]:
    candidates = [
        {
            "id": view_name,
            "kind": "ORTHOGRAPHIC",
            "base_view": view_name,
            "cost": 1.0,
            "view_data": view_data,
            "section_spec": None,
            "quality": 1.0,
        }
        for view_name, view_data in standard_views.items()
    ]
    if not needs_section:
        return candidates

    bounds = _shape_bounds(shape)
    spans = [bounds[index + 3] - bounds[index] for index in range(3)]
    axial_axis = min(range(3), key=lambda index: spans[index])
    projector = ViewProjector()
    for cut_axis in (index for index in range(3) if index != axial_axis):
        for keep_positive in (False, True):
            cut_shape, center, _spans = _half_cut(shape, bounds, cut_axis, keep_positive)
            for base_view in AXIS_VIEWS[cut_axis]:
                view_data = _project(projector, cut_shape, base_view)
                bbox = view_data["bbox"]
                width = max(float(bbox[2]) - float(bbox[0]), 1e-9)
                height = max(float(bbox[3]) - float(bbox[1]), 1e-9)
                balance = min(width, height) / max(width, height)
                edge_factor = min(1.0, math.log1p(len(view_data.get("visible") or [])) / math.log(600.0))
                quality = 0.55 * edge_factor + 0.45 * balance
                side = "POSITIVE" if keep_positive else "NEGATIVE"
                candidates.append({
                    "id": f"half_section_{'XYZ'[cut_axis]}_{side.lower()}_{base_view}",
                    "kind": "HALF_SECTION",
                    "base_view": base_view,
                    "cost": 1.25,
                    "view_data": view_data,
                    "quality": round(quality, 6),
                    "section_spec": {
                        "view_type": "HALF_SECTION",
                        "axial_axis": "XYZ"[axial_axis],
                        "cut_normal_axis": "XYZ"[cut_axis],
                        "plane_origin": [round(float(value), 6) for value in center],
                        "kept_side": side,
                        "view_direction": base_view,
                        "derivation": "BND_BOX_CENTER_AND_SHORTEST_PRINCIPAL_AXIS",
                    },
                })
    return candidates


def _payload_span(payload: dict[str, Any]) -> float | None:
    start, end = payload.get("start_proj"), payload.get("end_proj")
    if not (
        isinstance(start, Sequence) and len(start) >= 2
        and isinstance(end, Sequence) and len(end) >= 2
    ):
        return None
    return math.dist((float(start[0]), float(start[1])), (float(end[0]), float(end[1])))


def score_feature_coverage(
    rule: dict[str, Any],
    selection: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[float, list[str]]:
    """Return a semantic coverage score and auditable reasons."""
    requires_section = bool(selection.get("requires_section"))
    if requires_section and candidate["kind"] != "HALF_SECTION":
        return 0.0, ["INTERNAL_FEATURE_REQUIRES_SECTION"]
    if not requires_section and candidate["kind"] == "HALF_SECTION":
        return 0.15, ["EXTERNAL_FEATURE_SHOULD_REMAIN_UNSECTIONED"]

    view_name = str(candidate["base_view"])
    payload = (rule.get("view_geometry") or {}).get(view_name) or {}
    projected_bbox = payload.get("projected_feature_bbox") or []
    if len(projected_bbox) != 4:
        return 0.0, ["NO_PROJECTED_FEATURE_GEOMETRY"]
    width = abs(float(projected_bbox[2]) - float(projected_bbox[0]))
    height = abs(float(projected_bbox[3]) - float(projected_bbox[1]))
    if max(width, height) <= 1e-6:
        return 0.0, ["DEGENERATE_PROJECTED_FEATURE"]

    if requires_section:
        score = 0.72 + 0.28 * float(candidate.get("quality") or 0.0)
        return min(score, 1.0), ["SECTION_EXPOSES_INTERNAL_FEATURE", "DYNAMIC_CENTER_PLANE"]

    category = str(rule.get("category") or "")
    dim_type = str(rule.get("dim_type") or "").upper()
    allowed = set(rule.get("target_views") or rule.get("views") or [])
    score = 0.72 if view_name in allowed else 0.30
    reasons = ["RULE_RECOMMENDED_VIEW" if view_name in allowed else "GEOMETRY_ONLY_VIEW"]

    if dim_type == "HOLE_PATTERN":
        face_ratio = min(width, height) / max(width, height)
        score = 0.25 + 0.75 * face_ratio
        reasons.append(f"PATTERN_FACE_RATIO={face_ratio:.3f}")
    elif category == "thickness":
        # View planning selects a useful normal direction. Exact paired-face
        # thickness anchors are validated by the downstream render contract.
        score = 0.94 if view_name in allowed else 0.15
        reasons.append("THICKNESS_USES_FEATURE_NORMAL_VIEW")
    elif dim_type in {"LINEAR", "DIAMETER"}:
        nominal = abs(float(rule.get("nominal_value") or 0.0))
        span = _payload_span(payload)
        if span is not None and nominal > 1e-6:
            relative_error = abs(span - nominal) / nominal
            score *= math.exp(-2.5 * min(relative_error, 4.0))
            reasons.append(f"NOMINAL_SPAN_ERROR={relative_error:.3f}")
        if category == "overall":
            score = max(score, 0.90 if view_name in allowed else 0.20)
    elif dim_type == "LEADER":
        score = max(score, 0.72 if view_name in allowed else 0.25)

    return min(max(score, 0.0), 1.0), reasons


def solve_minimum_view_plan(
    rules_by_id: dict[str, dict[str, Any]],
    selections: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    threshold: float = 0.55,
) -> dict[str, Any]:
    matrix: dict[str, dict[str, dict[str, Any]]] = {}
    for selection in selections:
        feature_id = str(selection["feature_id"])
        rule = rules_by_id[feature_id]
        matrix[feature_id] = {}
        for candidate in candidates:
            score, reasons = score_feature_coverage(rule, selection, candidate)
            matrix[feature_id][candidate["id"]] = {"score": round(score, 6), "reasons": reasons}

    best = None
    max_views = min(3, len(candidates))
    for count in range(1, max_views + 1):
        for combo in itertools.combinations(candidates, count):
            assignments = {}
            for selection in selections:
                feature_id = str(selection["feature_id"])
                selected = max(combo, key=lambda item: matrix[feature_id][item["id"]]["score"])
                result = matrix[feature_id][selected["id"]]
                if result["score"] < threshold:
                    break
                assignments[feature_id] = {
                    "view_id": selected["id"],
                    "score": result["score"],
                    "reasons": result["reasons"],
                }
            else:
                total_cost = sum(float(item["cost"]) for item in combo)
                total_score = sum(item["score"] for item in assignments.values())
                objective = (round(total_cost, 6), -round(total_score, 6), tuple(item["id"] for item in combo))
                if best is None or objective < best[0]:
                    best = (objective, combo, assignments)
        if best is not None:
            break

    if best is None:
        unresolved = []
        for selection in selections:
            feature_id = str(selection["feature_id"])
            maximum = max(matrix[feature_id].items(), key=lambda item: item[1]["score"])
            unresolved.append({"feature_id": feature_id, "best_candidate": maximum[0], **maximum[1]})
        return {"status": "UNRESOLVED", "selected_views": [], "assignments": {}, "unresolved": unresolved, "matrix": matrix}

    _objective, combo, assignments = best
    return {
        "status": "PLANNED",
        "selected_views": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "base_view": item["base_view"],
                "cost": item["cost"],
                "quality": item["quality"],
                "section_spec": item["section_spec"],
            }
            for item in combo
        ],
        "assignments": assignments,
        "unresolved": [],
        "matrix": matrix,
    }


def _draw_view_card(
    canvas: Image.Image,
    card: Sequence[int],
    candidate: dict[str, Any],
    assigned: list[tuple[str, dict[str, Any]]],
) -> None:
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = card
    draw.rectangle(card, fill=BACKGROUND, outline=GRID, width=2)
    draw.text((left + 18, top + 15), candidate["id"], font=_font(23, True), fill=WHITE)
    draw.text((left + 18, top + 51), f"{candidate['kind']} | base={candidate['base_view']} | quality={candidate['quality']:.3f}", font=_font(16), fill=MUTED)
    view = candidate["view_data"]
    mapper = _mapper(view["bbox"], (left + 25, top + 90, right - 25, bottom - 210))
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=HIDDEN, width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=MODEL, width=2)
    for index, (feature_id, rule) in enumerate(assigned):
        payload = (rule.get("view_geometry") or {}).get(candidate["base_view"]) or {}
        bbox = payload.get("projected_feature_bbox") or []
        if len(bbox) == 4:
            p0, p1 = mapper((bbox[0], bbox[1])), mapper((bbox[2], bbox[3]))
            draw.rectangle((p0[0], p1[1], p1[0], p0[1]), outline=ORANGE if index % 2 == 0 else CYAN, width=4)
    feature_text = ", ".join(feature_id for feature_id, _rule in assigned) or "none"
    draw.text((left + 18, bottom - 168), "Assigned features:", font=_font(17, True), fill=GREEN)
    draw.text((left + 18, bottom - 132), feature_text[:95], font=_font(16), fill=WHITE)
    spec = candidate.get("section_spec")
    if spec:
        draw.text((left + 18, bottom - 92), f"cut={spec['cut_normal_axis']} center | keep={spec['kept_side']} | axial={spec['axial_axis']}", font=_font(15), fill=MUTED)


def render_case_board(
    output: Path,
    name: str,
    part_type: str,
    plan: dict[str, Any],
    candidates_by_id: dict[str, dict[str, Any]],
    rules_by_id: dict[str, dict[str, Any]],
) -> None:
    selected = plan["selected_views"]
    width, height = 4200, 2100
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 30), f"ViewPlan V1 | {name} | {part_type}", font=_font(36, True), fill=WHITE)
    draw.text((45, 82), "Minimum view set covering fixed engineer-selected features; no historical target drawing used.", font=_font(19), fill=MUTED)
    if not selected:
        draw.text((60, 180), "UNRESOLVED", font=_font(40, True), fill=RED)
        canvas.save(output)
        return
    card_width = (width - 100) // len(selected)
    for index, public_candidate in enumerate(selected):
        candidate = candidates_by_id[public_candidate["id"]]
        assigned_ids = [feature_id for feature_id, item in plan["assignments"].items() if item["view_id"] == candidate["id"]]
        assigned = [(feature_id, rules_by_id[feature_id]) for feature_id in assigned_ids]
        left = 40 + index * card_width
        _draw_view_card(canvas, (left, 135, left + card_width - 20, 1980), candidate, assigned)
    draw.text((45, 2030), f"Selected {len(selected)} view(s): " + " + ".join(item["id"] for item in selected), font=_font(19, True), fill=GREEN)
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    case_reports = []
    cases = list(config.get("cases") or [])
    for case_index, case in enumerate(cases, start=2):
        name = str(case["name"])
        step_path = _resolve_path(str(case["step_path"]))
        shape = load_step(str(step_path))
        projector = ViewProjector()
        standard_views = _standard_view_data(projector, shape)
        engine = SmartAnnotationEngine()
        rules = engine.get_candidate_rules(shape, standard_views, part_hint=str(case.get("part_hint") or name))
        rules_by_id = {str(rule.get("rule_id") or rule.get("id")): rule for rule in rules}
        selections = list(case.get("selections") or [])
        missing = [str(item["feature_id"]) for item in selections if str(item["feature_id"]) not in rules_by_id]
        if missing:
            raise KeyError(f"{name}: missing features {missing}")
        needs_section = any(bool(item.get("requires_section")) for item in selections)
        candidates = build_view_candidates(shape, standard_views, needs_section)
        plan = solve_minimum_view_plan(rules_by_id, selections, candidates)
        artifact_prefix = f"{case_index:02d}_{name}"
        board = output_dir / f"{artifact_prefix}_view_plan.png"
        candidates_by_id = {item["id"]: item for item in candidates}
        render_case_board(board, name, str(rules[0].get("part_type") if rules else "UNKNOWN"), plan, candidates_by_id, rules_by_id)
        public_plan = {key: value for key, value in plan.items() if key != "matrix"}
        report = {
            "name": name,
            "step_path": str(step_path),
            "part_type": rules[0].get("part_type") if rules else "UNKNOWN",
            "selected_features": selections,
            "candidate_count": len(candidates),
            "plan": public_plan,
            "coverage_matrix": plan["matrix"],
            "board": str(board),
        }
        case_reports.append(report)
        (output_dir / f"{artifact_prefix}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "historical_drawing_used": False,
        "cases": case_reports,
        "metrics": {
            "case_count": len(case_reports),
            "planned_count": sum(case["plan"]["status"] == "PLANNED" for case in case_reports),
            "selected_view_counts": {case["name"]: len(case["plan"]["selected_views"]) for case in case_reports},
            "section_view_counts": {
                case["name"]: sum(view["kind"] == "HALF_SECTION" for view in case["plan"]["selected_views"])
                for case in case_reports
            },
        },
    }
    summary_index = len(cases) + 2
    (output_dir / f"{summary_index:02d}_view_plan_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
