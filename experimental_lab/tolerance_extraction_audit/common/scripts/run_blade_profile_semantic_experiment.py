"""Run experiment 14: blade count and endpoint tangent semantics."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from blade_profile_semantics import (  # noqa: E402
    DenseCurveViewProjector, extract_blade_profile_semantics,
)
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from unknown_model_angular_orchestrator import orchestrate_radial_views  # noqa: E402


METHOD = "BLADE_PROFILE_SEMANTIC_AUDIT_V1"
STANDARD_VIEWS = ("front", "top", "right", "left", "back")
BACKGROUND = "#0b0b0b"
MAGENTA = "#f472b6"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower() or "case"


def _expanded_bbox(bbox: Sequence[float], fraction: float = 0.24) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    pad = max(x1 - x0, y1 - y0) * fraction
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _angle(point: Sequence[float], center: Sequence[float]) -> float:
    return math.degrees(math.atan2(float(point[1]) - center[1], float(point[0]) - center[0])) % 360.0


def _pick_representative(profile: dict[str, Any], preferred_angle: float = 45.0) -> dict[str, Any] | None:
    measurements = profile.get("measurements") or []
    if not measurements:
        return None
    return min(
        measurements,
        key=lambda item: abs(((_angle(item["inner_point"], profile["center"]) - preferred_angle + 180.0) % 360.0) - 180.0),
    )


def _draw_edge_reference(
    draw: ImageDraw.ImageDraw,
    view: dict[str, Any],
    mapper,
    reference: str,
    colour: str,
    width: int,
) -> None:
    if ":" not in reference:
        return
    collection_name, index_text = reference.split(":", 1)
    collection = view.get(collection_name) or []
    index = int(index_text)
    if not (0 <= index < len(collection)):
        return
    points = _edge_points(collection[index])
    if len(points) >= 2:
        draw.line([mapper(point) for point in points], fill=colour, width=width)


def _extend_vector(start: Sequence[float], sample: Sequence[float], scale: float) -> tuple[float, float]:
    vx, vy = float(sample[0]) - float(start[0]), float(sample[1]) - float(start[1])
    length = math.hypot(vx, vy)
    if length <= 1e-12:
        return float(start[0]), float(start[1])
    return float(start[0]) + vx / length * scale, float(start[1]) + vy / length * scale


def _fit_image(canvas: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = box[2] - box[0], box[3] - box[1]
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas.paste(image, (box[0] + (width - image.width) // 2, box[1] + (height - image.height) // 2))


def _render_case(
    output: Path,
    case: dict[str, Any],
    view: dict[str, Any],
    orchestration: dict[str, Any],
    profile: dict[str, Any],
    historical_status: str,
) -> None:
    canvas = Image.new("RGB", (4400, 2450), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((50, 25), f"Experiment 14 | {case['name']}", font=_font(40, True), fill=WHITE)
    draw.text((50, 85), "Pattern count + rotation-invariant endpoint tangent audit; no historical label enters geometry measurement.", font=_font(19), fill=MUTED)
    drawing_box = (50, 155, 3020, 2310)
    draw.rectangle(drawing_box, fill=BACKGROUND, outline=GRID, width=2)
    mapped_view = dict(view)
    mapped_view["bbox"] = _expanded_bbox(view["bbox"])
    mapper = _mapper(mapped_view["bbox"], (80, 185, 2990, 2280))
    for layer, colour, width in (("hidden", "#3f3f46", 1), ("visible", "#d4d4d8", 2)):
        for edge in view.get(layer) or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill=colour, width=width)

    representative = _pick_representative(profile)
    if representative:
        family_colour = CYAN if representative["boundary_family"] == "left" else MAGENTA
        for reference in representative.get("chain_edge_refs") or [representative["edge_ref"]]:
            _draw_edge_reference(draw, view, mapper, reference, family_colour, 10)
        center = profile["center"]
        span = max(view["bbox"][2] - view["bbox"][0], view["bbox"][3] - view["bbox"][1])
        for point, sample, label, colour, value in (
            (representative["inner_point"], representative["inner_sample_point"], "INNER", GREEN, representative["inner_relative_to_radial"]),
            (representative["outer_point"], representative["outer_sample_point"], "OUTER", ORANGE, representative["outer_relative_to_radial"]),
        ):
            radial_end = (
                point[0] + (point[0] - center[0]) / max(math.dist(point, center), 1e-9) * span * 0.16,
                point[1] + (point[1] - center[1]) / max(math.dist(point, center), 1e-9) * span * 0.16,
            )
            tangent_start, tangent_sample = point, sample
            if label == "OUTER":
                tangent_sample = (
                    point[0] + (point[0] - sample[0]),
                    point[1] + (point[1] - sample[1]),
                )
            tangent_end = _extend_vector(tangent_start, tangent_sample, span * 0.20)
            draw.line((mapper(point), mapper(radial_end)), fill=YELLOW, width=5)
            draw.line((mapper(point), mapper(tangent_end)), fill=colour, width=8)
            mark = mapper(point)
            draw.ellipse((mark[0] - 12, mark[1] - 12, mark[0] + 12, mark[1] + 12), fill=colour, outline=WHITE, width=2)
            label_at = mapper(tangent_end)
            draw.text((label_at[0] + 10, label_at[1]), f"{label} radial offset {abs(value):.2f} deg", font=_font(22, True), fill=colour)

    panel_x = 3090
    draw.rounded_rectangle((panel_x, 155, 4350, 1180), radius=14, fill=BOX, outline=CYAN if profile.get("resolved") else RED, width=3)
    inner = profile.get("summaries", {}).get("inner_relative_to_radial", {})
    outer = profile.get("summaries", {}).get("outer_relative_to_radial", {})
    lines = [
        ("GEOMETRY RESULT", CYAN, True),
        (f"selected view: {orchestration['best_view']}", WHITE, False),
        (f"detector: {orchestration['selected_detector']}", WHITE, False),
        (f"pattern count from STEP: {profile.get('pattern_count', 0)}", GREEN, True),
        (f"inner tangent radial offset: {abs(float(inner.get('median', 0.0))):.3f} deg", GREEN, False),
        (f"inner q10-q90 spread: {float(inner.get('spread', 0.0)):.3f} deg", MUTED, False),
        (f"outer tangent radial offset: {abs(float(outer.get('median', 0.0))):.3f} deg", ORANGE, False),
        (f"outer q10-q90 spread: {float(outer.get('spread', 0.0)):.3f} deg", MUTED, False),
        (f"chain segments per boundary: {representative.get('chain_segment_count', 0) if representative else 0}", WHITE, False),
        ("status: REVIEW_CANDIDATE", ORANGE, True),
    ]
    y = 190
    for text, colour, bold in lines:
        draw.text((panel_x + 22, y), text, font=_font(19, bold), fill=colour)
        y += 91

    historical = case.get("historical") or {}
    draw.rounded_rectangle((panel_x, 1220, 4350, 2310), radius=14, fill=BOX, outline=ORANGE if historical else GRID, width=3)
    draw.text((panel_x + 22, 1250), "HISTORICAL DRAWING AUDIT", font=_font(22, True), fill=ORANGE if historical else MUTED)
    if historical:
        evidence_lines = [
            f"drawing: {Path(str(historical['dxf_path'])).name}",
            f"drawing blade count: {historical['blade_count']}",
            f"note inlet angle: {historical['inlet_angle_degrees']:.1f} deg",
            f"note outlet angle: {historical['outlet_angle_degrees']:.1f} deg",
            f"comparison: {historical_status}",
            "Angle datum is not graphically defined in the DXF note.",
            "Do not fit STEP measurements to historical numbers.",
        ]
        y = 1310
        for index, text in enumerate(evidence_lines):
            colour = RED if index in (4, 6) else WHITE
            draw.text((panel_x + 22, y), text, font=_font(18, index == 4), fill=colour)
            y += 82
        image_path = historical.get("image_path")
        if image_path and _resolve(str(image_path)).exists():
            _fit_image(canvas, _resolve(str(image_path)), (panel_x + 20, 1880, 4330, 2285))
    else:
        draw.text((panel_x + 22, 1320), "No engineer-authored angle ground truth configured.", font=_font(18), fill=MUTED)
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, case in enumerate(config["cases"], start=2):
        started = time.perf_counter()
        step_path = _resolve(case["step_path"])
        component_label = case.get("component_label")
        shape = load_component_shape(str(step_path), str(component_label)) if component_label else load_step(str(step_path))
        if shape is None:
            raise RuntimeError(f"Component {component_label} was not found in {step_path}")
        views = DenseCurveViewProjector().project_all_views(shape, view_names=list(STANDARD_VIEWS))
        orchestration = orchestrate_radial_views(views)
        profile = extract_blade_profile_semantics(
            views[orchestration["best_view"]], orchestration["best_detection"]
        ) if orchestration["decision"] != "NO_RENDER" else {
            "resolved": False,
            "reason": "RADIAL_PATTERN_TRIGGER_NOT_ELIGIBLE",
        }
        historical = case.get("historical") or {}
        if historical and profile.get("resolved"):
            count_matches = int(profile["pattern_count"]) == int(historical["blade_count"])
            historical_status = "COUNT_MATCH_ANGLE_DATUM_UNRESOLVED" if count_matches else "GEOMETRY_REVISION_MISMATCH"
        else:
            historical_status = "NO_HISTORICAL_GROUND_TRUTH"
        case_dir = output_dir / f"{index:02d}_{_slug(case['name'])}_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        _render_case(
            case_dir / "01_profile_semantic_review.png", case,
            views[orchestration["best_view"]], orchestration, profile, historical_status,
        )
        public_profile = {key: value for key, value in profile.items() if key != "measurements"}
        report = {
            "name": case["name"],
            "step_path": str(step_path),
            "component_label": component_label,
            "expected_pattern": bool(case["expected_pattern"]),
            "pattern_triggered": orchestration["decision"] != "NO_RENDER",
            "best_view": orchestration["best_view"],
            "selected_detector": orchestration["selected_detector"],
            "profile": public_profile,
            "historical_comparison_status": historical_status,
            "elapsed_seconds": time.perf_counter() - started,
        }
        reports.append(report)
        (case_dir / "profile_semantic_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    metrics = {
        "case_count": len(reports),
        "trigger_match_count": sum(item["pattern_triggered"] == item["expected_pattern"] for item in reports),
        "profile_resolved_count": sum(bool(item["profile"].get("resolved")) for item in reports),
        "historical_geometry_mismatch_count": sum(item["historical_comparison_status"] == "GEOMETRY_REVISION_MISMATCH" for item in reports),
    }
    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "historical_values_used_by_detector": False,
        "metrics": metrics,
        "cases": reports,
    }
    (output_dir / "01_experiment_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
