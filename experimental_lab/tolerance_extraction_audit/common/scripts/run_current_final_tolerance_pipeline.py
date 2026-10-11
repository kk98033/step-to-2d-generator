"""Run the experimental merge candidate without touching production code."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from run_blade_curve_tolerance_experiment import GREEN, _font, run as run_blade
from run_fan_half_section_projection_experiment import run as run_fan_section


def _fan_section_gate(section_report: dict) -> dict:
    baseline = section_report["best_uncut_baseline"]
    selected = section_report["recommended_section_method"]
    ordered = sorted(float(value) for value in section_report["assembly_spans"])
    radial_similarity = ordered[1] / max(ordered[2], 1e-9)
    axial_ratio = ordered[0] / max(ordered[2], 1e-9)
    geometry_fan_like = radial_similarity >= 0.85 and axial_ratio <= 0.50
    section_advantage = float(selected["contour_score"]) - float(baseline["contour_score"])
    return {
        "geometry_fan_like": geometry_fan_like,
        "radial_similarity": round(radial_similarity, 6),
        "axial_ratio": round(axial_ratio, 6),
        "selected_kind": selected["kind"],
        "section_advantage": round(section_advantage, 6),
        "passed": bool(geometry_fan_like and section_report["automatic_match"] and selected["kind"] == "CENTRAL_SECTION_CURVES" and section_advantage >= 0.08),
    }


def _render_final_envelope_status(output_dir: Path, row: dict) -> str:
    source = output_dir / "blade_tolerance" / "01_outer_envelope_75_19_review.png"
    target = output_dir / "01_outer_envelope_75_19_current_final.png"
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    # The child review can emit its fourth note slightly below the nominal
    # 190 px header, so clear the unused top drawing margin as well.
    draw.rectangle((0, 0, image.width, 235), fill="#171717")
    phase = row.get("projection_phase") or {}
    draw.text(
        (24, 14),
        "Blade envelope tolerance: 75.19 +0.20/-0.00 mm",
        font=_font(32),
        fill="#f5f5f5",
    )
    notes = [
        ((255, 43, 214), "magenta: native DXF dimension endpoints"),
        ((34, 211, 238), "cyan: STEP projection selected by drawing-local geometry"),
        ((250, 204, 21), f"phase {float(phase.get('rotation_degrees', 0.0)):.3f} deg; dimension is tie-breaker only"),
    ]
    for index, (color, note) in enumerate(notes):
        y = 60 + 30 * index
        draw.ellipse((26, y + 5, 40, y + 19), fill=color)
        draw.text((50, y), note, font=_font(21), fill="#f5f5f5")
    draw.ellipse((26, 155, 42, 171), fill=GREEN)
    draw.text(
        (52, 145),
        (
            f"CURRENT_FINAL VERIFIED: STEP span {float(row['step_projected_span_mm']):.3f} mm "
            f"within {float(row['lower_limit_mm']):.2f}..{float(row['upper_limit_mm']):.2f} mm; "
            "central section passed"
        ),
        font=_font(22),
        fill="#86efac",
    )
    image.save(target)
    return target.name


def run(step: Path, dxf: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    blade_report = run_blade(step, dxf, output_dir / "blade_tolerance")
    section_report = run_fan_section(step, dxf, output_dir / "fan_projection")
    route = _fan_section_gate(section_report)

    merged = []
    for test in blade_report["tests"]:
        row = dict(test)
        if row.get("dimension_handle") == "7ADF":
            row["original_status"] = row.get("status")
            phase = row.get("projection_phase") or {}
            phase_metric_verified = bool(
                phase.get("method") == "DRAWING_LOCAL_GEOMETRY_CONSTRAINED_PHASE_V1"
                and row.get("metric_matches_tolerance_interval")
            )
            row["fan_section_projection"] = {
                "status": "VERIFIED" if route["passed"] else "FAILED",
                "method": section_report["recommended_section_method"]["kind"],
                "contour_score": section_report["recommended_section_method"]["contour_score"],
                "extent_score": section_report["recommended_section_method"]["extent_score"],
                "scale": section_report["recommended_section_method"]["transform"]["scale"],
            }
            if route["passed"] and phase_metric_verified:
                row["status"] = "VERIFIED"
                row["reason"] = None
                row["verification_basis"] = [
                    "full_assembly_central_section_matches_drawing_view",
                    "drawing_local_geometry_selects_projection_phase",
                    "step_envelope_span_matches_native_tolerance_interval",
                ]
            elif route["passed"] and row.get("status") != "VERIFIED":
                row["status"] = "PROJECTION_VERIFIED_FEATURE_PENDING"
                row["reason"] = "Full-assembly central section matches the drawing view, but the projected Blade span is outside the native tolerance interval; the case is not eligible for automatic database ingestion."
        merged.append(row)

    envelope_row = next(
        (item for item in merged if item.get("dimension_handle") == "7ADF"),
        None,
    )
    if envelope_row and envelope_row.get("status") == "VERIFIED":
        envelope_row["review_image"] = _render_final_envelope_status(output_dir, envelope_row)

    old_test = next(item for item in blade_report["tests"] if item.get("dimension_handle") == "7ADF")
    new_projection = section_report["recommended_section_method"]
    targeted = {
        "schema_version": 1,
        "case": "1AL0W5000H-R03 / DXF 7ADF / 75.19 diameter",
        "before": {"method": "leaf_component_side_projection", "contour_score": old_test["contour_score"], "extent_score": old_test["extent_score"], "projection_gate": False, "feature_auto_verified": old_test["status"] == "VERIFIED"},
        "after": {"method": "CENTRAL_SECTION_PLUS_DRAWING_LOCAL_PHASE", "contour_score": new_projection["contour_score"], "extent_score": new_projection["extent_score"], "projection_gate": route["passed"], "feature_auto_verified": any(item.get("dimension_handle") == "7ADF" and item.get("status") == "VERIFIED" for item in merged), "step_projected_span_mm": next((item.get("step_projected_span_mm") for item in merged if item.get("dimension_handle") == "7ADF"), None)},
        "delta": {"contour_score": round(float(new_projection["contour_score"]) - float(old_test["contour_score"]), 6), "extent_score": round(float(new_projection["extent_score"]) - float(old_test["extent_score"]), 6), "new_projection_qualified_dimensions": 1 if route["passed"] else 0, "new_auto_ingestable_cases": 1 if any(item.get("dimension_handle") == "7ADF" and item.get("status") == "VERIFIED" for item in merged) else 0},
        "tolerance_parse_regression": False,
        "note": "The section identifies the drawing representation; the drawing-local geometry selects projection phase before the native tolerance interval is checked."
    }

    report = {
        "schema_version": 1,
        "version": "2026-10-10.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "fan_route": route,
        "summary": {
            "native_tolerances_tested": sum("dimension_handle" in item for item in merged),
            "projection_verified_feature_pending": sum(item.get("status") == "PROJECTION_VERIFIED_FEATURE_PENDING" for item in merged),
            "fully_verified": sum(item.get("status") == "VERIFIED" for item in merged),
            "candidate_only": sum(item.get("status") == "CANDIDATE_ONLY" for item in merged),
            "auto_ingestable": sum(item.get("status") == "VERIFIED" for item in merged),
        },
        "tolerances": merged,
        "artifacts": {"fan_projection_report": "fan_projection/fan_half_section_report.json", "blade_tolerance_report": "blade_tolerance/blade_curve_tolerance_report.json", "targeted_benchmark": "targeted_benchmark.json"},
    }
    (output_dir / "targeted_benchmark.json").write_text(json.dumps(targeted, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "final_tolerance_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.step.resolve(), args.dxf.resolve(), args.output_dir.resolve())
    print(json.dumps({"fan_route": report["fan_route"], "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
