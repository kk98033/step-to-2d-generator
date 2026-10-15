"""Compare legacy synthetic feature boxes with topology-aware anchors."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.canonical_features import extract_canonical_features  # noqa: E402
from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from feature_anchor_repair import repair_rules  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from run_full_classification_audit import route_rule  # noqa: E402
from run_view_plan_experiment import _standard_view_data  # noqa: E402


METHOD = "FEATURE_ANCHOR_REPAIR_V1"
STATUS_COLOURS = {
    "ROUTED": GREEN,
    "REVIEW_LOW_VIEW_SCORE": YELLOW,
    "REVIEW_OUTSIDE_VIEW": ORANGE,
    "REVIEW_OVERSIZED_BBOX": ORANGE,
    "REVIEW_GENERIC_RENDERER": ORANGE,
    "UNRESOLVED_NO_PROJECTION": RED,
}


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _route_rows(rules: Sequence[dict[str, Any]], views: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(rule.get("rule_id") or rule.get("id")): {
            "feature_id": str(rule.get("rule_id") or rule.get("id")),
            "semantic": str(rule.get("category") or "UNKNOWN").upper(),
            "nominal_value": rule.get("nominal_value"),
            **route_rule(rule, views),
        }
        for rule in rules
    }


def build_case(case: dict[str, Any]) -> dict[str, Any]:
    step_path = _resolve_path(str(case["step_path"]))
    shape = load_step(str(step_path))
    views = _standard_view_data(ViewProjector(), shape)
    features = extract_canonical_features(shape, part_hint=str(case.get("part_hint") or ""))
    rules = SmartAnnotationEngine().get_candidate_rules(shape, views, part_hint=str(case.get("part_hint") or case["name"]))
    repaired_rules, repair_audit = repair_rules(rules, features.records, features.feature_extractor, views)
    before = _route_rows(rules, views)
    after = _route_rows(repaired_rules, views)
    rows = []
    for feature_id in before:
        old, new = before[feature_id], after[feature_id]
        rows.append({
            "feature_id": feature_id,
            "semantic": old["semantic"],
            "nominal_value": old["nominal_value"],
            "before": old,
            "after": new,
            "repair": repair_audit.get(feature_id, {"status": "NOT_ATTEMPTED"}),
            "changed": old.get("projected_bbox") != new.get("projected_bbox") or old["status"] != new["status"],
            "improved": old["status"] != "ROUTED" and new["status"] == "ROUTED",
            "regressed": old["status"] == "ROUTED" and new["status"] != "ROUTED",
        })
    return {
        "name": str(case["name"]),
        "step_path": str(step_path.resolve()),
        "part_hint": str(case.get("part_hint") or ""),
        "part_family": features.part_type,
        "feature_count": len(features.records),
        "rule_count": len(rules),
        "rows": rows,
        "before_status": dict(Counter(row["before"]["status"] for row in rows)),
        "after_status": dict(Counter(row["after"]["status"] for row in rows)),
        "repaired_count": sum(row["repair"].get("status") == "REPAIRED" for row in rows),
        "improved_count": sum(row["improved"] for row in rows),
        "regressed_count": sum(row["regressed"] for row in rows),
        "_views": views,
    }


def _draw_projection(draw: ImageDraw.ImageDraw, view: dict[str, Any], box: Sequence[int]):
    draw.rectangle(box, fill="#0b0b0b", outline=GRID, width=2)
    mapper = _mapper(view["bbox"], (box[0] + 25, box[1] + 25, box[2] - 25, box[3] - 25))
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#404040", width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#e5e5e5", width=3)
    return mapper


def _draw_bbox(draw: ImageDraw.ImageDraw, mapper, bbox: Sequence[float] | None, colour: str, width: int) -> None:
    if not bbox or len(bbox) != 4:
        return
    p0, p1 = mapper((bbox[0], bbox[1])), mapper((bbox[2], bbox[3]))
    left, right = sorted((p0[0], p1[0]))
    top, bottom = sorted((p0[1], p1[1]))
    if right - left < 8:
        left, right = left - 4, right + 4
    if bottom - top < 8:
        top, bottom = top - 4, bottom + 4
    draw.rectangle((left, top, right, bottom), outline=colour, width=width)


def render_case_review(path: Path, case: dict[str, Any]) -> None:
    candidates = sorted(
        [row for row in case["rows"] if row["changed"]],
        key=lambda row: (not row["improved"], row["regressed"], row["feature_id"]),
    )[:12]
    width, height = 4200, 2450
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 32), f"Feature Anchor Repair | {case['name']}", font=_font(38, True), fill=WHITE)
    draw.text((55, 88), "Red = old synthetic bbox | Cyan = topology-aware bbox | same routing thresholds", font=_font(20), fill=MUTED)
    if not candidates:
        draw.text((55, 180), "No repairable topology match changed this case.", font=_font(25), fill=YELLOW)
        canvas.save(path)
        return
    view_names = []
    for row in candidates:
        name = str(row["after"].get("view") or row["before"].get("view") or "front")
        if name not in view_names:
            view_names.append(name)
    view_names = view_names[:2]
    boxes = [(55, 155, 2660, 1210), (55, 1280, 2660, 2335)]
    mappers = {}
    for index, view_name in enumerate(view_names):
        draw.text((75, boxes[index][1] + 15), view_name.upper(), font=_font(18, True), fill=CYAN)
        mappers[view_name] = _draw_projection(draw, case["_views"][view_name], (boxes[index][0], boxes[index][1] + 50, boxes[index][2], boxes[index][3]))
    list_left = 2725
    for index, row in enumerate(candidates):
        y = 165 + index * 180
        view_name = str(row["after"].get("view") or row["before"].get("view") or "front")
        mapper = mappers.get(view_name)
        if mapper:
            _draw_bbox(draw, mapper, row["before"].get("projected_bbox"), RED, 5)
            _draw_bbox(draw, mapper, row["after"].get("projected_bbox"), CYAN, 4)
        colour = GREEN if row["improved"] else (RED if row["regressed"] else ORANGE)
        draw.rounded_rectangle((list_left, y, 4140, y + 150), radius=10, fill=BOX, outline=colour, width=3)
        draw.text((list_left + 15, y + 10), row["feature_id"], font=_font(17, True), fill=colour)
        draw.text((list_left + 15, y + 43), f"{row['semantic']} nominal={row['nominal_value']} view={view_name}", font=_font(14), fill=WHITE)
        draw.text((list_left + 15, y + 72), f"{row['before']['status']} -> {row['after']['status']}", font=_font(14, True), fill=colour)
        methods = ", ".join(row["repair"].get("methods") or [])
        draw.text((list_left + 15, y + 103), methods[:95], font=_font(13), fill=MUTED)
    canvas.save(path)


def render_overview(path: Path, cases: Sequence[dict[str, Any]]) -> None:
    canvas = Image.new("RGB", (3600, 650 + len(cases) * 380), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), "Feature Anchor Repair V1 | Before / After", font=_font(40, True), fill=WHITE)
    draw.text((55, 98), "A gain only counts when the same strict router changes from review to ROUTED.", font=_font(19), fill=MUTED)
    headers = ((65, "MODEL"), (700, "REPAIRED"), (1250, "ROUTED BEFORE"), (1950, "ROUTED AFTER"), (2600, "GAIN / REGRESSION"))
    for x, label in headers:
        draw.text((x, 190), label, font=_font(20, True), fill=MUTED)
    for index, case in enumerate(cases):
        y = 260 + index * 380
        before = int(case["before_status"].get("ROUTED", 0))
        after = int(case["after_status"].get("ROUTED", 0))
        draw.rounded_rectangle((45, y, 3545, y + 285), radius=15, fill=BOX, outline=GRID, width=3)
        draw.text((65, y + 40), case["name"], font=_font(23, True), fill=CYAN)
        draw.text((700, y + 40), str(case["repaired_count"]), font=_font(30, True), fill=WHITE)
        draw.text((1250, y + 40), f"{before}/{case['rule_count']}", font=_font(30, True), fill=YELLOW)
        draw.text((1950, y + 40), f"{after}/{case['rule_count']}", font=_font(30, True), fill=GREEN if after >= before else RED)
        draw.text((2600, y + 40), f"+{case['improved_count']} / -{case['regressed_count']}", font=_font(30, True), fill=GREEN if not case["regressed_count"] else ORANGE)
        draw.text((65, y + 105), f"before: {case['before_status']}", font=_font(15), fill=MUTED)
        draw.text((65, y + 155), f"after:  {case['after_status']}", font=_font(15), fill=MUTED)
    canvas.save(path)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = [build_case(case) for case in config.get("cases") or []]
    render_overview(output_dir / "02_anchor_repair_overview.png", cases)
    for index, case in enumerate(cases, start=3):
        image_path = output_dir / f"{index:02d}_{case['name']}_anchor_before_after.png"
        render_case_review(image_path, case)
        case["review_image"] = str(image_path.resolve())
        case.pop("_views", None)
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "cases": cases,
        "metrics": {
            "case_count": len(cases),
            "rule_count": sum(case["rule_count"] for case in cases),
            "repaired_count": sum(case["repaired_count"] for case in cases),
            "routed_before": sum(case["before_status"].get("ROUTED", 0) for case in cases),
            "routed_after": sum(case["after_status"].get("ROUTED", 0) for case in cases),
            "improved_count": sum(case["improved_count"] for case in cases),
            "regressed_count": sum(case["regressed_count"] for case in cases),
        },
    }
    (output_dir / "06_anchor_repair_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
