"""Run a complete feature/rule/view/renderer classification audit.

Every canonical feature and every generated candidate rule is included in the
union.  Drawable rules are grouped by semantic category and selected base view
and rendered in paginated projection reviews with numbered leader lines.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.canonical_features import extract_canonical_features  # noqa: E402
from auto_2d_drawing.part_classifier import normalize_part_semantic_type  # noqa: E402
from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BACKGROUND,
    BLUE,
    BOX,
    CYAN,
    GREEN,
    GRID,
    MUTED,
    ORANGE,
    PANEL,
    RED,
    WHITE,
    YELLOW,
    _edge_points,
    _font,
    _mapper,
    classification_confidence,
    count_solids,
    renderer_for_rule,
    structure_label,
)
from run_view_plan_experiment import _standard_view_data, score_feature_coverage  # noqa: E402


METHOD = "FULL_CLASSIFICATION_AUDIT_V1"
PAGE_SIZE = 12
STANDARD_VIEWS = ("front", "top", "right", "left", "back")
STATUS_COLOURS = {
    "ROUTED": GREEN,
    "REVIEW_LOW_VIEW_SCORE": YELLOW,
    "REVIEW_OUTSIDE_VIEW": ORANGE,
    "REVIEW_OVERSIZED_BBOX": ORANGE,
    "REVIEW_GENERIC_RENDERER": ORANGE,
    "UNRESOLVED_NO_PROJECTION": RED,
    "NO_DRAWING_RULE": RED,
}


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return cleaned or "unknown"


def _bbox_metrics(feature_bbox: Sequence[float], view_bbox: Sequence[float]) -> dict[str, float]:
    fx0, fy0, fx1, fy1 = (float(value) for value in feature_bbox)
    vx0, vy0, vx1, vy1 = (float(value) for value in view_bbox)
    fw, fh = max(fx1 - fx0, 0.0), max(fy1 - fy0, 0.0)
    vw, vh = max(vx1 - vx0, 1e-9), max(vy1 - vy0, 1e-9)
    ix = max(0.0, min(fx1, vx1) - max(fx0, vx0))
    iy = max(0.0, min(fy1, vy1) - max(fy0, vy0))
    feature_area = fw * fh
    return {
        "area_ratio": feature_area / (vw * vh),
        "intersection_ratio": (ix * iy / feature_area) if feature_area > 1e-12 else 0.0,
        "width_ratio": fw / vw,
        "height_ratio": fh / vh,
    }


def route_rule(rule: dict[str, Any], standard_views: dict[str, dict[str, Any]]) -> dict[str, Any]:
    renderer = renderer_for_rule(rule)
    candidates = []
    preferred = set(rule.get("target_views") or rule.get("views") or [])
    for view_name in STANDARD_VIEWS:
        view = standard_views.get(view_name)
        payload = (rule.get("view_geometry") or {}).get(view_name) or {}
        bbox = payload.get("projected_feature_bbox") or []
        if view is None or len(bbox) != 4:
            continue
        candidate = {
            "id": view_name,
            "kind": "ORTHOGRAPHIC",
            "base_view": view_name,
            "cost": 1.0,
            "quality": 1.0,
            "section_spec": None,
        }
        score, reasons = score_feature_coverage(rule, {}, candidate)
        metrics = _bbox_metrics(bbox, view["bbox"])
        candidates.append({
            "view": view_name,
            "score": float(score),
            "preferred": view_name in preferred,
            "reasons": reasons,
            "bbox": [float(value) for value in bbox],
            **metrics,
        })
    if not candidates:
        return {
            "status": "UNRESOLVED_NO_PROJECTION",
            "view": None,
            "view_score": 0.0,
            "renderer": renderer,
            "projected_bbox": None,
            "reasons": ["NO_VIEW_HAS_PROJECTED_FEATURE_BBOX"],
        }
    best = max(candidates, key=lambda item: (item["score"], item["preferred"], item["intersection_ratio"], -STANDARD_VIEWS.index(item["view"])))
    status = "ROUTED"
    reasons = list(best["reasons"])
    if renderer == "GENERIC_RENDERER":
        status = "REVIEW_GENERIC_RENDERER"
        reasons.append("NO_SPECIALIZED_RENDERER")
    elif best["intersection_ratio"] < 0.50:
        status = "REVIEW_OUTSIDE_VIEW"
        reasons.append("LESS_THAN_HALF_OF_FEATURE_BBOX_INSIDE_VIEW")
    elif (
        str(rule.get("category") or "").lower() in {"hole", "fillet", "chamfer", "step", "groove", "thickness"}
        and best["width_ratio"] > 0.75
        and best["height_ratio"] > 0.75
    ):
        status = "REVIEW_OVERSIZED_BBOX"
        reasons.append("LOCAL_FEATURE_BBOX_COVERS_MOST_OF_VIEW")
    elif best["score"] < 0.35:
        status = "REVIEW_LOW_VIEW_SCORE"
        reasons.append("VIEW_SCORE_BELOW_0.35")
    return {
        "status": status,
        "view": best["view"],
        "view_score": round(best["score"], 6),
        "renderer": renderer,
        "projected_bbox": best["bbox"],
        "bbox_area_ratio": round(best["area_ratio"], 6),
        "bbox_intersection_ratio": round(best["intersection_ratio"], 6),
        "bbox_width_ratio": round(best["width_ratio"], 6),
        "bbox_height_ratio": round(best["height_ratio"], 6),
        "reasons": reasons,
    }


def build_full_case(case: dict[str, Any]) -> dict[str, Any]:
    step_path = _resolve_path(str(case["step_path"]))
    shape = load_step(str(step_path))
    standard_views = _standard_view_data(ViewProjector(), shape)
    geometry_features = extract_canonical_features(shape, part_hint=None)
    production_features = extract_canonical_features(shape, part_hint=str(case.get("part_hint") or ""))
    rules = SmartAnnotationEngine().get_candidate_rules(shape, standard_views, part_hint=str(case.get("part_hint") or case["name"]))
    records_by_id = {str(record.get("id")): record for record in production_features.records}
    rules_by_id = {str(rule.get("rule_id") or rule.get("id")): rule for rule in rules}
    all_ids = list(records_by_id)
    all_ids.extend(rule_id for rule_id in rules_by_id if rule_id not in records_by_id)
    rows = []
    for feature_id in all_ids:
        record = records_by_id.get(feature_id)
        rule = rules_by_id.get(feature_id)
        source_kind = "BOTH" if record is not None and rule is not None else ("CANONICAL_ONLY" if record is not None else "RULE_ONLY")
        if rule is None:
            rows.append({
                "feature_id": feature_id,
                "source_kind": source_kind,
                "name": str((record or {}).get("name") or feature_id),
                "semantic": str((record or {}).get("role") or (record or {}).get("type") or "UNKNOWN").upper(),
                "dim_type": None,
                "nominal_value": (record or {}).get("nominal"),
                "status": "NO_DRAWING_RULE",
                "view": None,
                "view_score": 0.0,
                "renderer": None,
                "projected_bbox": None,
                "reasons": ["CANONICAL_FEATURE_HAS_NO_CANDIDATE_RULE"],
            })
            continue
        route = route_rule(rule, standard_views)
        rows.append({
            "feature_id": feature_id,
            "source_kind": source_kind,
            "name": str(rule.get("name") or (record or {}).get("name") or feature_id),
            "semantic": str(rule.get("category") or (record or {}).get("role") or rule.get("dim_type") or "UNKNOWN").upper(),
            "dim_type": str(rule.get("dim_type") or "UNKNOWN").upper(),
            "nominal_value": rule.get("nominal_value"),
            **route,
        })

    geometry_family = normalize_part_semantic_type(geometry_features.part_type)
    production_family = production_features.part_type
    confidence, reason = classification_confidence(geometry_family, production_family)
    expected_family = str(case.get("expected_family") or "")
    return {
        "name": str(case["name"]),
        "step_path": str(step_path.resolve()),
        "part_hint": str(case.get("part_hint") or ""),
        "solid_count": count_solids(shape),
        "structure": structure_label(count_solids(shape)),
        "geometry_family": geometry_family,
        "production_family": production_family,
        "expected_family": expected_family,
        "family_correct": not expected_family or production_family == expected_family,
        "classification_confidence": confidence,
        "classification_reason": reason,
        "canonical_feature_count": len(records_by_id),
        "candidate_rule_count": len(rules_by_id),
        "union_feature_count": len(rows),
        "rows": rows,
        "_standard_views": standard_views,
    }


def _draw_projection(draw: ImageDraw.ImageDraw, view: dict[str, Any], box: Sequence[int]) -> Any:
    draw.rectangle(box, fill="#0b0b0b", outline=GRID, width=2)
    mapper = _mapper(view["bbox"], (box[0] + 30, box[1] + 30, box[2] - 30, box[3] - 30))
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#454545", width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#d4d4d4", width=3)
    return mapper


def render_audit_page(output: Path, case: dict[str, Any], semantic: str, view_name: str, rows: list[dict[str, Any]], page: int, pages: int) -> None:
    width, height = 4200, 2400
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 32), f"Full Classification Audit | {case['name']}", font=_font(36, True), fill=WHITE)
    draw.text((55, 86), f"semantic={semantic} | view={view_name} | page {page}/{pages} | every row is included in JSON", font=_font(19), fill=MUTED)
    projection_box = (55, 155, 2720, 2305)
    mapper = _draw_projection(draw, case["_standard_views"][view_name], projection_box)
    list_left = 2785
    row_height = 174
    for index, row in enumerate(rows):
        y = 175 + index * row_height
        colour = STATUS_COLOURS.get(row["status"], ORANGE)
        bbox = row.get("projected_bbox")
        number = index + 1
        if bbox:
            p0, p1 = mapper((bbox[0], bbox[1])), mapper((bbox[2], bbox[3]))
            raw_left, raw_right = sorted((p0[0], p1[0]))
            raw_top, raw_bottom = sorted((p0[1], p1[1]))
            left, right = max(projection_box[0] + 3, raw_left), min(projection_box[2] - 3, raw_right)
            top, bottom = max(projection_box[1] + 3, raw_top), min(projection_box[3] - 3, raw_bottom)
            if left <= right and top <= bottom:
                if right - left < 10:
                    left = max(projection_box[0] + 3, left - 5)
                    right = min(projection_box[2] - 3, right + 5)
                if bottom - top < 10:
                    top = max(projection_box[1] + 3, top - 5)
                    bottom = min(projection_box[3] - 3, bottom + 5)
                draw.rectangle((left, top, right, bottom), outline=colour, width=5)
                anchor = ((left + right) / 2.0, (top + bottom) / 2.0)
            else:
                raw_center = ((raw_left + raw_right) / 2.0, (raw_top + raw_bottom) / 2.0)
                anchor = (
                    min(max(raw_center[0], projection_box[0] + 12), projection_box[2] - 12),
                    min(max(raw_center[1], projection_box[1] + 12), projection_box[3] - 12),
                )
                draw.line((anchor[0] - 13, anchor[1] - 13, anchor[0] + 13, anchor[1] + 13), fill=colour, width=5)
                draw.line((anchor[0] - 13, anchor[1] + 13, anchor[0] + 13, anchor[1] - 13), fill=colour, width=5)
            draw.ellipse((anchor[0] - 17, anchor[1] - 17, anchor[0] + 17, anchor[1] + 17), fill=colour)
            draw.text((anchor[0] - 7, anchor[1] - 14), str(number), font=_font(15, True), fill=BACKGROUND)
            draw.line((anchor, (list_left - 30, y + 38), (list_left, y + 38)), fill=colour, width=3)
        draw.rounded_rectangle((list_left, y, 4140, y + 148), radius=10, fill=BOX, outline=colour, width=3)
        draw.text((list_left + 14, y + 10), f"{number:02d}  {row['feature_id']}", font=_font(17, True), fill=colour)
        draw.text((list_left + 14, y + 42), f"{row['semantic']} | {row.get('dim_type')} | nominal={row.get('nominal_value')}", font=_font(14), fill=WHITE)
        draw.text((list_left + 14, y + 70), f"{row['status']} | score={row.get('view_score')} | {row.get('renderer')}", font=_font(14), fill=WHITE)
        draw.text((list_left + 14, y + 99), f"source={row['source_kind']} | " + ", ".join(row.get("reasons") or [])[:100], font=_font(13), fill=MUTED)
    canvas.save(output)


def render_unresolved_page(output: Path, case: dict[str, Any], rows: list[dict[str, Any]], page: int, pages: int) -> None:
    width, height = 3600, 2200
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), f"Unresolved / Review Queue | {case['name']} | {page}/{pages}", font=_font(36, True), fill=WHITE)
    draw.text((55, 91), "These items need classifier, projection, or renderer work before production use.", font=_font(19), fill=MUTED)
    for index, row in enumerate(rows):
        y = 155 + index * 160
        colour = STATUS_COLOURS.get(row["status"], ORANGE)
        draw.rounded_rectangle((55, y, 3545, y + 135), radius=10, fill=BOX, outline=colour, width=3)
        draw.text((75, y + 10), row["feature_id"], font=_font(18, True), fill=colour)
        draw.text((75, y + 43), f"{row['semantic']} | {row.get('dim_type')} | {row['status']} | view={row.get('view')} | renderer={row.get('renderer')}", font=_font(15), fill=WHITE)
        draw.text((75, y + 76), f"source={row['source_kind']} | " + ", ".join(row.get("reasons") or [])[:180], font=_font(14), fill=MUTED)
    canvas.save(output)


def render_case_summary(output: Path, case: dict[str, Any]) -> None:
    width, height = 3600, 1900
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), f"Full Audit Summary | {case['name']}", font=_font(38, True), fill=WHITE)
    status_counts = Counter(row["status"] for row in case["rows"])
    category_counts = Counter(row["semantic"] for row in case["rows"])
    view_counts = Counter(str(row.get("view") or "NONE") for row in case["rows"])
    renderer_counts = Counter(str(row.get("renderer") or "NONE") for row in case["rows"])
    cards = [
        ("MODEL", [Path(case["step_path"]).name, case["structure"], f"solids={case['solid_count']}"], BLUE),
        ("FAMILY", [f"geometry={case['geometry_family']}", f"production={case['production_family']}", f"expected={case['expected_family']}", f"confidence={case['classification_confidence']:.0%}"], GREEN if case["family_correct"] else RED),
        ("COVERAGE", [f"canonical={case['canonical_feature_count']}", f"rules={case['candidate_rule_count']}", f"union={case['union_feature_count']}", f"routed={status_counts.get('ROUTED', 0)}"], CYAN),
    ]
    for index, (title, lines, colour) in enumerate(cards):
        left = 55 + index * 1165
        draw.rounded_rectangle((left, 135, left + 1090, 480), radius=16, fill=BOX, outline=colour, width=4)
        draw.text((left + 20, 155), title, font=_font(23, True), fill=colour)
        for line_index, line in enumerate(lines):
            draw.text((left + 20, 215 + line_index * 48), line, font=_font(19), fill=WHITE)
    sections = [
        ("STATUS", status_counts, 55),
        ("SEMANTIC CATEGORY", category_counts, 930),
        ("SELECTED VIEW", view_counts, 1805),
        ("RENDERER", renderer_counts, 2680),
    ]
    for title, counter, left in sections:
        draw.text((left, 555), title, font=_font(23, True), fill=YELLOW)
        y = 610
        for key, count in counter.most_common(18):
            colour = STATUS_COLOURS.get(key, WHITE)
            draw.text((left, y), f"{key[:34]:34s} {count:4d}", font=_font(16), fill=colour)
            y += 55
    canvas.save(output)


def render_overall(output: Path, cases: list[dict[str, Any]]) -> None:
    width, height = 4200, 560 + len(cases) * 470
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), "Full Classification Audit V1 | Complete Coverage", font=_font(40, True), fill=WHITE)
    draw.text((55, 96), "Union = every canonical feature ID plus every candidate-rule ID.", font=_font(20), fill=MUTED)
    headers = ((55, "MODEL"), (750, "FAMILY"), (1500, "CANONICAL / RULE / UNION"), (2450, "ROUTED"), (3100, "REVIEW / UNRESOLVED"))
    for x, title in headers:
        draw.text((x, 180), title, font=_font(21, True), fill=MUTED)
    for index, case in enumerate(cases):
        y = 245 + index * 470
        status_counts = Counter(row["status"] for row in case["rows"])
        routed = status_counts.get("ROUTED", 0)
        unresolved = case["union_feature_count"] - routed
        draw.rounded_rectangle((45, y, 4145, y + 365), radius=16, fill=BOX, outline=GRID, width=3)
        draw.text((65, y + 35), case["name"], font=_font(24, True), fill=BLUE)
        draw.text((750, y + 35), case["production_family"], font=_font(22, True), fill=GREEN if case["family_correct"] else RED)
        draw.text((750, y + 78), f"geometry={case['geometry_family']} | {case['classification_confidence']:.0%}", font=_font(16), fill=MUTED)
        draw.text((1500, y + 35), f"{case['canonical_feature_count']} / {case['candidate_rule_count']} / {case['union_feature_count']}", font=_font(24, True), fill=WHITE)
        draw.text((2450, y + 35), str(routed), font=_font(30, True), fill=GREEN)
        draw.text((3100, y + 35), str(unresolved), font=_font(30, True), fill=RED if unresolved else GREEN)
        y2 = y + 130
        for status, count in status_counts.most_common():
            draw.text((3100, y2), f"{status}: {count}", font=_font(16), fill=STATUS_COLOURS.get(status, WHITE))
            y2 += 38
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = [build_full_case(case) for case in config.get("cases") or []]
    render_overall(output_dir / "02_full_audit_overview.png", cases)
    for case_index, case in enumerate(cases, start=3):
        case_dir = output_dir / f"{case_index:02d}_{_slug(case['name'])}_full_audit"
        case_dir.mkdir(parents=True, exist_ok=True)
        render_case_summary(case_dir / "01_model_summary.png", case)
        artifact_index = 2
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        unresolved = []
        for row in case["rows"]:
            if row.get("view") and row.get("projected_bbox"):
                grouped[(row["semantic"], str(row["view"]))].append(row)
            if row["status"] != "ROUTED":
                unresolved.append(row)
        page_files = []
        for (semantic, view_name), rows in sorted(grouped.items()):
            page_count = math.ceil(len(rows) / PAGE_SIZE)
            for page_index in range(page_count):
                chunk = rows[page_index * PAGE_SIZE:(page_index + 1) * PAGE_SIZE]
                filename = f"{artifact_index:02d}_{_slug(semantic)}_{view_name}_page_{page_index + 1:02d}.png"
                render_audit_page(case_dir / filename, case, semantic, view_name, chunk, page_index + 1, page_count)
                page_files.append(str((case_dir / filename).resolve()))
                artifact_index += 1
        unresolved_files = []
        page_count = math.ceil(len(unresolved) / PAGE_SIZE) if unresolved else 0
        for page_index in range(page_count):
            chunk = unresolved[page_index * PAGE_SIZE:(page_index + 1) * PAGE_SIZE]
            filename = f"{artifact_index:02d}_review_queue_page_{page_index + 1:02d}.png"
            render_unresolved_page(case_dir / filename, case, chunk, page_index + 1, page_count)
            unresolved_files.append(str((case_dir / filename).resolve()))
            artifact_index += 1
        case["audit_directory"] = str(case_dir.resolve())
        case["projection_page_count"] = len(page_files)
        case["review_queue_page_count"] = len(unresolved_files)
        case.pop("_standard_views", None)

    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "page_size": PAGE_SIZE,
        "cases": cases,
        "metrics": {
            "case_count": len(cases),
            "canonical_feature_count": sum(case["canonical_feature_count"] for case in cases),
            "candidate_rule_count": sum(case["candidate_rule_count"] for case in cases),
            "union_feature_count": sum(case["union_feature_count"] for case in cases),
            "routed_count": sum(row["status"] == "ROUTED" for case in cases for row in case["rows"]),
            "review_or_unresolved_count": sum(row["status"] != "ROUTED" for case in cases for row in case["rows"]),
        },
    }
    (output_dir / "06_full_classification_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
