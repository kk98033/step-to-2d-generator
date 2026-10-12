"""Run one frozen hybrid drawing strategy across multiple shaft holdouts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.canonical_features import extract_canonical_features  # noqa: E402
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from generate_tolerance_edge_review import _render_native_dxf  # noqa: E402
from hybrid_drawing_strategy_experiment import (  # noqa: E402
    BACKGROUND,
    MUTED,
    PANEL,
    RELIABLE,
    REVIEW,
    WHITE,
    _font,
    build_drawing_strategy,
    build_shaft_dimension_intents,
    evaluate_holdout,
    infer_style_prior,
    render_intent_debug_board,
    solve_adaptive_layout,
)
from transfer_drawing_rules import load_source_feature_links  # noqa: E402


METHOD = "CATEGORY_FEATURE_ADAPTIVE_LAYOUT_BATCH_HOLDOUT_V1"


def dimension_evidence_crop(dimensions: Sequence[Any]) -> tuple[float, float, float, float]:
    """Estimate a review crop from native dimension points only."""
    points: list[tuple[float, float]] = []
    for dimension in dimensions:
        for point in (dimension.points or {}).values():
            if not isinstance(point, Sequence) or len(point) < 2:
                continue
            x, y = float(point[0]), float(point[1])
            if abs(x) < 1e-9 and abs(y) < 1e-9:
                continue
            points.append((x, y))
    if not points:
        raise ValueError("Native dimensions contain no usable review points")
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    width, height = max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)
    return (
        min(xs) - max(width * 0.18, 2.0),
        min(ys) - max(height * 0.32, 2.0),
        max(xs) + max(width * 0.18, 2.0),
        max(ys) + max(height * 0.32, 2.0),
    )


def render_native_dimension_crop(dxf_path: Path, output: Path) -> None:
    dimensions = DxfToleranceExtractor().extract_from_file(
        str(dxf_path), include_rejected=True, native_dimensions_only=True
    )
    if not dimensions:
        raise RuntimeError(f"No native dimensions in {dxf_path}")
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    _render_native_dxf(
        dxf_path,
        dimensions[0],
        {},
        [],
        extractor.sheet_bbox,
        dimension_evidence_crop(dimensions),
        output,
        (1800, 1450),
    )


def _failure_groups(holdout: dict[str, Any]) -> dict[str, int]:
    groups: dict[str, int] = {}
    for item in holdout["results"]:
        if item["supported"]:
            continue
        key = str(item["semantic_type"])
        groups[key] = groups.get(key, 0) + 1
    return groups


def render_batch_summary(results: Sequence[dict[str, Any]], output: Path) -> None:
    width = 5700
    card_width = 1800
    canvas = Image.new("RGB", (width, 1900), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((20, 20, width - 20, 1880), radius=18, fill=PANEL)
    draw.text((55, 40), "Hybrid Drawing Strategy: Multi-model Holdout", font=_font(36, True), fill=WHITE)
    draw.text((55, 96), "One frozen SHAFT strategy; target DXFs are opened only after STEP planning.", font=_font(21), fill=MUTED)
    for index, result in enumerate(results):
        left = 55 + index * 1875
        right = left + card_width
        draw.rectangle((left, 160, right, 1800), fill=BACKGROUND, outline="#404040", width=2)
        draw.text((left + 20, 180), result["component_name"], font=_font(26, True), fill=WHITE)
        if result.get("error"):
            draw.text((left + 20, 240), result["error"], font=_font(18), fill=REVIEW)
            continue
        metrics = result["metrics"]
        rate = 100.0 * float(metrics["intent_holdout_precision"])
        coverage = 100.0 * float(metrics["native_dimension_coverage_proxy"])
        color = RELIABLE if rate >= 70.0 else REVIEW
        draw.text(
            (left + 20, 235),
            f"view={result['selected_view']}  intents={metrics['intent_count']}  supported={metrics['supported_count']}  precision={rate:.1f}%",
            font=_font(19, True), fill=color,
        )
        draw.text(
            (left + 20, 275),
            f"native dimensions={metrics['native_dimension_count']}  coverage proxy={coverage:.1f}%  layout collisions={metrics['axial_interval_collisions']}",
            font=_font(17), fill=MUTED,
        )
        with Image.open(result["target_original"]) as source:
            image = source.convert("RGB")
        image.thumbnail((card_width - 50, 1270), Image.Resampling.LANCZOS)
        canvas.paste(image, (left + (card_width - image.width) // 2, 340))
        failure_text = ", ".join(f"{key}={value}" for key, value in result["failure_groups"].items()) or "none"
        draw.text((left + 20, 1660), "Unsupported: " + failure_text, font=_font(17), fill=REVIEW)
        draw.text((left + 20, 1700), f"Debug: {Path(result['debug_board']).name}", font=_font(16), fill=MUTED)
    draw.text((55, 1830), "Support is strict one-to-one nominal/type evidence, not final drawing correctness.", font=_font(18), fill=MUTED)
    canvas.save(output)


def run_batch(
    cases_path: Path,
    source_rules_path: Path,
    case_base_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cases_payload = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = list(cases_payload.get("cases") or [])
    source_report = json.loads(source_rules_path.read_text(encoding="utf-8"))
    source_rules = list(source_report.get("drawing_rule_cases") or [])
    source_name = Path(str(source_report["source"]["path"])).name
    source_links = load_source_feature_links(case_base_path, source_name)
    style_prior = infer_style_prior(source_rules, source_links)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for case in cases:
        name = str(case["component_name"])
        case_dir = output_dir / name
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            step_path = Path(case["step_path"])
            dxf_path = Path(case["dxf_path"])
            shape = load_component_shape(str(step_path), str(case["component_label"]))
            if shape is None or shape.IsNull():
                raise RuntimeError("COMPONENT_SHAPE_NOT_FOUND")
            feature_set = extract_canonical_features(shape, part_hint=name)
            view_data = ViewProjector().project_all_views(
                shape, view_names=["front", "top", "right", "left", "back"]
            )
            strategy = build_drawing_strategy(feature_set.part_type, feature_set.records, view_data, style_prior)
            selected_view = str(strategy["primary_view"])
            intents, rejected = build_shaft_dimension_intents(
                feature_set.records, strategy, view_data[selected_view]["bbox"]
            )
            planned, layout_metrics = solve_adaptive_layout(
                intents, view_data[selected_view]["bbox"], style_prior
            )

            # The target drawing enters only after planning is complete.
            holdout = evaluate_holdout(
                planned,
                dxf_path,
                str(strategy["selection_evidence"]["source_layout_orientation"]),
            )
            target_original = case_dir / "01_target_holdout_original.png"
            render_native_dimension_crop(dxf_path, target_original)
            debug_board = case_dir / "02_adaptive_intent_debug.png"
            render_intent_debug_board(
                view_data[selected_view], planned, holdout, target_original, debug_board
            )
            metrics = {
                "intent_count": len(planned),
                "supported_count": holdout["supported_count"],
                "holdout_support_rate": round(holdout["supported_count"] / max(len(planned), 1), 6),
                "intent_holdout_precision": round(holdout["supported_count"] / max(len(planned), 1), 6),
                "native_dimension_count": holdout["native_dimension_count"],
                "native_dimension_coverage_proxy": round(
                    holdout["supported_count"] / max(holdout["native_dimension_count"], 1), 6
                ),
                "axial_interval_collisions": layout_metrics["axial_interval_collisions"],
            }
            result = {
                "component_name": name,
                "component_label": case["component_label"],
                "step_path": str(step_path),
                "dxf_path": str(dxf_path),
                "part_type": feature_set.part_type,
                "selected_view": selected_view,
                "strategy": strategy,
                "dimension_intents": planned,
                "rejected_feature_intents": rejected,
                "layout_metrics": layout_metrics,
                "holdout_evaluation": holdout,
                "failure_groups": _failure_groups(holdout),
                "metrics": metrics,
                "target_original": str(target_original),
                "debug_board": str(debug_board),
            }
            (case_dir / "case_report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            results.append(result)
        except Exception as exc:
            results.append({"component_name": name, "error": f"{type(exc).__name__}: {exc}"})

    completed = [item for item in results if not item.get("error")]
    total_intents = sum(item["metrics"]["intent_count"] for item in completed)
    total_supported = sum(item["metrics"]["supported_count"] for item in completed)
    total_native = sum(item["metrics"]["native_dimension_count"] for item in completed)
    summary_board = output_dir / "00_batch_holdout_summary.png"
    render_batch_summary(results, summary_board)
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "policy": {
            "shared_strategy": True,
            "per_model_rule_overrides": False,
            "target_dxf_role": "POST_FREEZE_HOLDOUT_ONLY",
            "holdout_matching": "ONE_TO_ONE_NOMINAL_AND_DIMENSION_TYPE",
        },
        "metrics": {
            "requested_models": len(cases),
            "completed_models": len(completed),
            "total_intents": total_intents,
            "total_supported": total_supported,
            "total_native_dimensions": total_native,
            "micro_holdout_support_rate": round(total_supported / max(total_intents, 1), 6),
            "micro_intent_holdout_precision": round(total_supported / max(total_intents, 1), 6),
            "micro_native_dimension_coverage_proxy": round(total_supported / max(total_native, 1), 6),
            "macro_holdout_support_rate": round(
                sum(item["metrics"]["holdout_support_rate"] for item in completed) / max(len(completed), 1), 6
            ),
            "macro_intent_holdout_precision": round(
                sum(item["metrics"]["intent_holdout_precision"] for item in completed) / max(len(completed), 1), 6
            ),
            "macro_native_dimension_coverage_proxy": round(
                sum(item["metrics"]["native_dimension_coverage_proxy"] for item in completed) / max(len(completed), 1), 6
            ),
        },
        "cases": results,
        "summary_board": str(summary_board),
    }
    (output_dir / "batch_holdout_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--source-rules", required=True)
    parser.add_argument("--case-base", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run_batch(
        Path(args.cases).resolve(),
        Path(args.source_rules).resolve(),
        Path(args.case_base).resolve(),
        Path(args.output).resolve(),
    )
    print(json.dumps({"metrics": report["metrics"], "summary_board": report["summary_board"]}, ensure_ascii=False, indent=2))
    return 0 if report["metrics"]["completed_models"] == report["metrics"]["requested_models"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
