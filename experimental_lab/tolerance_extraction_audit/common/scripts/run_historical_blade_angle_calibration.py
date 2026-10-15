"""Experiment 15: calibrate blade-angle semantics from paired STEP/DXF evidence.

The experiment deliberately separates three questions:

1. Which anonymous STEP leaf contains a credible repeated radial pattern?
2. Does the engineer drawing contain a geometrically matching projected view?
3. Which rotation-invariant endpoint-angle expression best explains the
   engineer-authored inlet/outlet values across more than one product?

Names and expected angle values never participate in component selection or
projection registration.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components  # noqa: E402
from adaptive_curved_radial_features import detect_adaptive_curved_radial_partitions  # noqa: E402
from blade_profile_semantics import DenseCurveViewProjector, extract_blade_profile_semantics  # noqa: E402
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE,
    _edge_points, _font, _mapper,
)
from run_historical_drawing_comparison import (  # noqa: E402
    _match_views, _render_native_region,
)
from unknown_model_angular_orchestrator import orchestrate_radial_views  # noqa: E402


METHOD = "HISTORICAL_BLADE_ANGLE_CALIBRATION_V1"
STANDARD_VIEWS = ("front", "top", "right", "left", "back")
BACKGROUND = "#0b0b0b"
MODEL = "#d4d4d8"
MAGENTA = "#f472b6"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _slug(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "_" for character in value).strip("_")


def _profile_spread(profile: dict[str, Any]) -> float:
    summaries = profile.get("summaries") or {}
    return sum(float((summaries.get(key) or {}).get("spread") or 180.0) for key in (
        "inner_relative_to_radial", "outer_relative_to_radial",
    ))


def _analyse_component(component) -> dict[str, Any]:
    views = DenseCurveViewProjector().project_all_views(component.shape, view_names=list(STANDARD_VIEWS))
    orchestration = orchestrate_radial_views(views)
    adaptive_candidates = []
    for view_name, view in views.items():
        detection = detect_adaptive_curved_radial_partitions(view)
        adaptive_candidates.append({
            "view": view_name,
            "detection": detection,
            "score": (
                int(detection.get("resolved", False)),
                len(detection.get("elements") or []),
                -float((detection.get("center_evidence") or {}).get("confidence") or 0.0),
            ),
        })
    adaptive = max(adaptive_candidates, key=lambda item: item["score"])
    if adaptive["detection"].get("resolved") and (
        orchestration["decision"] == "NO_RENDER"
        or len(adaptive["detection"].get("elements") or []) > len(orchestration["best_detection"].get("elements") or [])
    ):
        detection = adaptive["detection"]
        orchestration = {
            **orchestration,
            "decision": "REVIEW_RENDER",
            "verification_status": "REVIEW_CANDIDATE",
            "best_view": adaptive["view"],
            "selected_detector": detection["method"],
            "best_detection": detection,
            "best_assessment": {
                "eligible": True,
                "score": min(0.91, 0.78 + len(detection.get("elements") or []) / 1000.0),
                "reason": detection["reason"],
            },
        }
    profile = {"resolved": False, "reason": "RADIAL_PATTERN_TRIGGER_NOT_ELIGIBLE"}
    if orchestration["decision"] != "NO_RENDER":
        profile = extract_blade_profile_semantics(
            views[orchestration["best_view"]], orchestration["best_detection"]
        )
    return {
        "component": component,
        "views": views,
        "orchestration": orchestration,
        "profile": profile,
    }


def _select_blade_component(step_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analyses = [_analyse_component(component) for component in extract_leaf_components(str(step_path))]
    if not analyses:
        raise RuntimeError(f"No measurable STEP leaf component: {step_path}")
    selected = max(
        analyses,
        key=lambda item: (
            bool(item["profile"].get("resolved")),
            bool(item["orchestration"]["best_assessment"].get("eligible")),
            float(item["orchestration"]["best_assessment"].get("score") or 0.0),
            int(item["profile"].get("pattern_count") or 0),
            -_profile_spread(item["profile"]),
        ),
    )
    return selected, analyses


def _compact_component(item: dict[str, Any], selected: bool) -> dict[str, Any]:
    component = item["component"]
    profile = item["profile"]
    return {
        "selected": selected,
        "label_entry": component.label_entry,
        "name": component.name,
        "fingerprint": component.fingerprint,
        "trigger_decision": item["orchestration"]["decision"],
        "trigger_score": item["orchestration"]["best_assessment"].get("score"),
        "best_view": item["orchestration"]["best_view"],
        "profile_resolved": bool(profile.get("resolved")),
        "pattern_count": int(profile.get("pattern_count") or 0),
        "profile_spread": _profile_spread(profile),
    }


def _match_drawing(dxf_path: Path, views: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, DxfProjectionViewExtractorV2]:
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    matches, _ = _match_views(extractor, views, STANDARD_VIEWS, maximum_views=1)
    return (matches[0] if matches else None), extractor


def _absolute(value: float) -> float:
    value = abs(float(value)) % 180.0
    return min(value, 180.0 - value)


TRANSFORMS: dict[str, Callable[[float], float]] = {
    "ABS_RADIAL_OFFSET": lambda value: _absolute(value),
    "COMPLEMENT_TO_90": lambda value: abs(90.0 - _absolute(value)),
    "SUPPLEMENT_TO_90": lambda value: 90.0 + _absolute(value),
    "SUPPLEMENT_TO_180": lambda value: 180.0 - _absolute(value),
}


def _semantic_candidates(case: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
    profile = case["profile"]
    result = []
    for family in ("left", "right"):
        summary = ((profile.get("family_summaries") or {}).get(family) or {}).get(
            f"{endpoint}_relative_to_radial"
        ) or {}
        if "median" not in summary:
            continue
        measured = float(summary["median"])
        for transform_name, transform in TRANSFORMS.items():
            result.append({
                "endpoint": endpoint,
                "boundary_family": family,
                "transform": transform_name,
                "measured_radial_offset": measured,
                "predicted_angle": transform(measured),
            })
    return result


def _rank_angle_semantics(cases: Sequence[dict[str, Any]], target_name: str) -> list[dict[str, Any]]:
    eligible = [case for case in cases if case["eligible_for_calibration"]]
    signatures = [
        (endpoint, family, transform)
        for endpoint in ("inner", "outer")
        for family in ("left", "right")
        for transform in TRANSFORMS
    ]
    rows = []
    for endpoint, family, transform in signatures:
        errors = []
        samples = []
        for case in eligible:
            candidates = _semantic_candidates(case, endpoint)
            candidate = next((item for item in candidates if item["boundary_family"] == family and item["transform"] == transform), None)
            if candidate is None:
                continue
            actual = float(case["historical"][target_name])
            error = abs(float(candidate["predicted_angle"]) - actual)
            errors.append(error)
            samples.append({
                "case": case["name"],
                "predicted": round(float(candidate["predicted_angle"]), 4),
                "actual": actual,
                "absolute_error": round(error, 4),
            })
        if errors:
            rows.append({
                "target": target_name,
                "endpoint": endpoint,
                "boundary_family": family,
                "transform": transform,
                "sample_count": len(errors),
                "mean_absolute_error": round(sum(errors) / len(errors), 6),
                "maximum_absolute_error": round(max(errors), 6),
                "samples": samples,
            })
    return sorted(rows, key=lambda item: (-item["sample_count"], item["mean_absolute_error"], item["maximum_absolute_error"]))


def _expanded_bbox(bbox: Sequence[float], fraction: float = 0.15) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    pad = max(x1 - x0, y1 - y0) * fraction
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _render_projection(view: dict[str, Any], output: Path) -> None:
    image = Image.new("RGB", (1800, 1500), BACKGROUND)
    draw = ImageDraw.Draw(image)
    mapper = _mapper(_expanded_bbox(view["bbox"]), (40, 40, 1760, 1460))
    for layer, colour, width in (("hidden", "#3f3f46", 1), ("visible", MODEL, 3)):
        for edge in view.get(layer) or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill=colour, width=width)
    image.save(output)


def _fit(canvas: Image.Image, path: Path, box: Sequence[int]) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = int(box[2] - box[0]), int(box[3] - box[1])
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas.paste(image, (int(box[0] + (width - image.width) / 2), int(box[1] + (height - image.height) / 2)))


def _render_board(case: dict[str, Any], projection: Path, drawing: Path, output: Path) -> None:
    image = Image.new("RGB", (4000, 2100), PANEL)
    draw = ImageDraw.Draw(image)
    status_colour = GREEN if case["eligible_for_calibration"] else RED
    draw.text((45, 25), f"Experiment 15 | {case['name']}", font=_font(38, True), fill=WHITE)
    draw.text((45, 82), case["calibration_reason"], font=_font(20, True), fill=status_colour)
    panels = ((45, 145, 1930, 1980), (1970, 145, 3955, 1980))
    for box, label, path in zip(panels, ("SELECTED STEP LEAF PROJECTION", "ORIGINAL ENGINEER DXF"), (projection, drawing)):
        draw.rectangle(box, fill=BACKGROUND, outline=GRID, width=2)
        draw.text((box[0] + 20, box[1] + 18), label, font=_font(20, True), fill=CYAN)
        _fit(image, path, (box[0] + 20, box[1] + 65, box[2] - 20, box[3] - 20))
    profile = case["profile"]
    match = case.get("projection_match") or {}
    summary = (
        f"detected count={profile.get('pattern_count', 0)} | candidates={profile.get('pattern_count_candidates', [])} | "
        f"DXF note count={case['historical']['blade_count']} ({case.get('count_match_kind', 'NONE')}) | "
        f"view={case['selected_view']} | registration={float(match.get('comparison_score') or 0.0):.3f} | "
        f"rotation={float(match.get('rotation_degrees') or 0.0):.1f} deg | mirrored={bool(match.get('mirrored'))}"
    )
    draw.rectangle((45, 1995, 3955, 2070), fill=BOX, outline=status_colour, width=2)
    draw.text((70, 2015), summary, font=_font(18, True), fill=status_colour)
    image.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for case_index, case_config in enumerate(config["cases"], start=2):
        started = time.perf_counter()
        step_path = _resolve(case_config["step_path"])
        dxf_path = _resolve(case_config["historical"]["dxf_path"])
        selected, analyses = _select_blade_component(step_path)
        match, extractor = _match_drawing(dxf_path, selected["views"])
        profile = selected["profile"]
        historical = dict(case_config["historical"])
        observed_count = int(profile.get("pattern_count") or 0)
        count_candidates = {int(value) for value in profile.get("pattern_count_candidates") or []}
        exact_count_match = observed_count == int(historical["blade_count"])
        harmonic_count_match = int(historical["blade_count"]) in count_candidates
        count_matches = exact_count_match or harmonic_count_match
        registration_score = float((match or {}).get("comparison_score") or 0.0)
        registration_passed = registration_score >= float(config.get("minimum_registration_score", 0.45))
        eligible = bool(profile.get("resolved")) and count_matches and registration_passed
        if not profile.get("resolved"):
            reason = "REJECTED_PROFILE_UNRESOLVED"
        elif not count_matches:
            reason = "REJECTED_BLADE_COUNT_REVISION_MISMATCH"
        elif not registration_passed:
            reason = "REJECTED_DRAWING_VIEW_REGISTRATION_WEAK"
        else:
            reason = "CALIBRATION_PAIR_ACCEPTED"
        case_dir = output_dir / f"{case_index:02d}_{_slug(case_config['name'])}_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        projection_path = case_dir / "01_selected_step_projection.png"
        drawing_path = case_dir / "02_original_engineer_drawing.png"
        board_path = case_dir / "03_pair_acceptance_review.png"
        selected_view = selected["orchestration"]["best_view"]
        _render_projection(selected["views"][selected_view], projection_path)
        _render_native_region(dxf_path, extractor.sheet_bbox, drawing_path, (2200, 1600))
        report = {
            "name": case_config["name"],
            "step_path": str(step_path),
            "dxf_path": str(dxf_path),
            "selected_component": _compact_component(selected, True),
            "component_candidates": [
                _compact_component(item, item is selected) for item in analyses
            ],
            "selected_view": selected_view,
            "profile": {key: value for key, value in profile.items() if key != "measurements"},
            "historical": historical,
            "projection_match": match,
            "count_matches": count_matches,
            "count_match_kind": "EXACT" if exact_count_match else ("HARMONIC" if harmonic_count_match else "NONE"),
            "registration_passed": registration_passed,
            "eligible_for_calibration": eligible,
            "calibration_reason": reason,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        _render_board(report, projection_path, drawing_path, board_path)
        (case_dir / "pair_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)

    inlet_ranking = _rank_angle_semantics(reports, "inlet_angle_degrees")
    outlet_ranking = _rank_angle_semantics(reports, "outlet_angle_degrees")
    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "historical_labels_used_for_component_selection": False,
        "minimum_registration_score": float(config.get("minimum_registration_score", 0.45)),
        "metrics": {
            "case_count": len(reports),
            "accepted_calibration_pair_count": sum(item["eligible_for_calibration"] for item in reports),
            "count_mismatch_rejection_count": sum(not item["count_matches"] for item in reports),
            "registration_rejection_count": sum(not item["registration_passed"] for item in reports),
        },
        "best_inlet_semantic": inlet_ranking[0] if inlet_ranking else None,
        "best_outlet_semantic": outlet_ranking[0] if outlet_ranking else None,
        "inlet_semantic_ranking": inlet_ranking,
        "outlet_semantic_ranking": outlet_ranking,
        "cases": reports,
    }
    (output_dir / "01_experiment_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps({
        "metrics": report["metrics"],
        "best_inlet_semantic": report["best_inlet_semantic"],
        "best_outlet_semantic": report["best_outlet_semantic"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
