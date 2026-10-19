"""Stable experimental entrypoint for unknown-model drawing analysis.

This module composes tested experiment modules.  It intentionally does not
modify or replace the legacy production dimension engine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


AUDIT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AUDIT_ROOT.parents[1]
SCRIPT_ROOT = AUDIT_ROOT / "common" / "scripts"
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from annular_composite_features import extract_annular_composites  # noqa: E402
from dimension_intents import build_dimension_intent_set  # noqa: E402
from minimal_multiview_drawing import render_minimal_multiview_drawing  # noqa: E402
from relational_dimension_intents import (  # noqa: E402
    detect_hub_radial_partitions,
    detect_rectangular_hole_pattern,
)
from solid_shaft_features import extract_solid_shaft_features  # noqa: E402
from thin_wall_dimension_pipeline import extract_thin_wall_dimension  # noqa: E402


METHOD = "DRAWING_EXPERIMENTAL_MAIN_V0_24"
STANDARD_VIEWS = ("front", "top", "right", "left", "back")


def _radial_quality(result: dict[str, Any]) -> tuple[int, int, float]:
    return (
        int(bool(result.get("resolved"))),
        len(result.get("boundary_angles") or []),
        float(result.get("confidence") or 0.0),
    )


def _summarise_radial_view(view_name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "view": view_name,
        "resolved": bool(result.get("resolved")),
        "reason": result.get("reason"),
        "confidence": result.get("confidence"),
        "center": result.get("center"),
        "hub_radius": result.get("hub_radius"),
        "boundary_angles": result.get("boundary_angles") or [],
        "element_relations": result.get("elements") or [],
        "gap_relations": result.get("gaps") or [],
        "center_pitch_relations": result.get("center_pitches") or [],
        "pairwise_center_relations": result.get("pairwise_center_relations") or [],
    }


def analyse_step(step_path: Path) -> dict[str, Any]:
    shape = load_step(str(step_path))
    annular = extract_annular_composites(shape)
    shaft = extract_solid_shaft_features(shape)
    hole_pattern = detect_rectangular_hole_pattern(shape)
    thin_wall = extract_thin_wall_dimension(shape)

    views = ViewProjector().project_all_views(shape, view_names=list(STANDARD_VIEWS))
    radial_by_view: list[dict[str, Any]] = []
    for view_name, view in views.items():
        radial_by_view.append(_summarise_radial_view(view_name, detect_hub_radial_partitions(view)))
    best_radial = max(radial_by_view, key=_radial_quality) if radial_by_view else None

    intents = build_dimension_intent_set(
        annular_result=annular,
        solid_shaft_result=shaft,
        thin_wall_result=thin_wall if thin_wall.get("resolved") else None,
    )
    active_detectors = []
    if annular.get("annular_feature_count", 0):
        active_detectors.append("annular_composite_features")
    if shaft.get("shaft_chain_count", 0):
        active_detectors.append("solid_shaft_features")
    if hole_pattern.get("resolved"):
        active_detectors.append("rectangular_hole_relations")
    if best_radial and best_radial.get("resolved"):
        active_detectors.append("hub_radial_relations")
    if thin_wall.get("resolved"):
        active_detectors.append("paired_surface_thickness")

    return {
        "schema_version": 1,
        "method": METHOD,
        "mode": "ANALYSIS_ONLY",
        "source_step": str(step_path.resolve()),
        "full_drawing_render_ready": False,
        "active_detectors": active_detectors,
        "detectors": {
            "annular_composite_features": annular,
            "solid_shaft_features": shaft,
            "rectangular_hole_relations": hole_pattern,
            "hub_radial_relations": {
                "best_view": best_radial,
                "all_views": radial_by_view,
            },
            "paired_surface_thickness": thin_wall,
        },
        "dimension_intents": intents,
        "next_stage": {
            "name": "unknown_model_orchestration",
            "required": True,
            "reason": (
                "Detected candidates still need automatic view/section selection, "
                "historical selection ranking, rendering dispatch and layout."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the current experimental drawing analysis.")
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--selection-config",
        type=Path,
        help="Optional engineer-selection JSON for separate front/top/right drawings.",
    )
    args = parser.parse_args()
    if not args.step.is_file():
        parser.error(f"STEP file does not exist: {args.step}")

    args.output.mkdir(parents=True, exist_ok=True)
    report = analyse_step(args.step)
    if args.selection_config:
        if not args.selection_config.is_file():
            parser.error(f"Selection config does not exist: {args.selection_config}")
        selection_config = json.loads(args.selection_config.read_text(encoding="utf-8"))
        drawing_report = render_minimal_multiview_drawing(
            args.step,
            selection_config.get("selections") or [],
            args.output / "selected_drawings",
            part_hint=str(selection_config.get("part_hint") or args.step.stem),
            include_half_section=bool(selection_config.get("include_half_section")),
            relational_selections=selection_config.get("relational_selections") or [],
            pattern_angle_selections=selection_config.get("pattern_angle_selections") or [],
            annular_selections=selection_config.get("annular_selections") or [],
            rectangular_hole_selections=selection_config.get("rectangular_hole_selections") or [],
            shaft_dimension_selections=selection_config.get("shaft_dimension_selections") or [],
            thickness_selections=selection_config.get("thickness_selections") or [],
            half_section_selection=selection_config.get("half_section_selection"),
        )
        report["mode"] = "ANALYSIS_AND_ENGINEER_SELECTED_DRAWING"
        report["selected_multiview_drawing"] = drawing_report
        report["selected_drawing_render_ready"] = drawing_report["success"]
    report_path = args.output / "drawing_engine_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "method": report["method"],
        "report": str(report_path.resolve()),
        "active_detectors": report["active_detectors"],
        "unified_intent_count": report["dimension_intents"]["unified_intent_count"],
        "selected_drawing_render_ready": report.get("selected_drawing_render_ready"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
