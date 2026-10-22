"""Run one CAD annotation operation in an isolated operating-system process.

This is a validation worker, not a production queue.  It deliberately imports
the CAD stack only inside the child process so a native-library crash cannot
terminate the web API process.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTAL_MAIN = (
    REPO_ROOT
    / "experimental_lab"
    / "tolerance_extraction_audit"
    / "DRAWING_CURRENT_FINAL"
    / "experimental_main.py"
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _project(step_path: Path):
    from auto_2d_drawing.step_reader import load_step
    from auto_2d_drawing.view_projector import ViewProjector

    shape = load_step(str(step_path))
    views = ViewProjector().project_all_views(
        shape,
        view_names=["front", "top", "right", "left"],
    )
    return shape, views


def _load_shape(step_path: Path):
    from auto_2d_drawing.step_reader import load_step

    return load_step(str(step_path))


def _load_experimental_main_module():
    """Load the versioned experimental entrypoint only inside this worker."""
    if not EXPERIMENTAL_MAIN.is_file():
        raise FileNotFoundError(f"Experimental drawing entrypoint not found: {EXPERIMENTAL_MAIN}")
    spec = importlib.util.spec_from_file_location(
        "drawing_current_final_experimental_main",
        EXPERIMENTAL_MAIN,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load experimental drawing entrypoint: {EXPERIMENTAL_MAIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_experimental_shadow_analysis(
    job: dict[str, Any],
    step_path: Path,
    started: float,
) -> dict[str, Any]:
    """Run analysis-only Experimental Main and persist its versioned report."""
    output_dir = Path(job["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "drawing_engine_report.json"
    module = _load_experimental_main_module()
    report = module.analyse_step(step_path)
    if report.get("mode") != "ANALYSIS_ONLY":
        raise RuntimeError("Experimental shadow analysis unexpectedly requested drawing output")
    if not str(report.get("method") or "").startswith("DRAWING_EXPERIMENTAL_MAIN_"):
        raise RuntimeError("Experimental shadow analysis returned an unknown method")
    _write_json(report_path, report)
    return {
        "status": "ok",
        "action": "experimental_shadow_analysis",
        "pid": os.getpid(),
        "elapsed_seconds": time.perf_counter() - started,
        "report_path": str(report_path),
        "method": report["method"],
        "schema_version": report.get("schema_version"),
        "active_detectors": report.get("active_detectors") or [],
        "unified_intent_count": int(
            (report.get("dimension_intents") or {}).get("unified_intent_count") or 0
        ),
        "full_drawing_render_ready": bool(report.get("full_drawing_render_ready")),
    }


def _run_experimental_candidates(
    step_path: Path,
    started: float,
) -> dict[str, Any]:
    """Adapt analysis-only results for the smart-annotation candidate UI."""
    from auto_2d_drawing.smart_extractors.experimental_candidate_adapter import (
        ENGINE_ID,
        CANDIDATE_SCHEMA_VERSION,
        build_experimental_candidate_rules,
        candidate_summary,
    )
    from auto_2d_drawing.smart_extractors.experimental_feature_bridge import (
        bridge_experimental_candidate_rules,
    )

    module = _load_experimental_main_module()
    report = module.analyse_step(step_path)
    rules = build_experimental_candidate_rules(report)
    rules, feature_bridge = bridge_experimental_candidate_rules(
        _load_shape(step_path),
        rules,
        part_hint=str(step_path.stem),
    )
    summary = candidate_summary(rules)
    summary["feature_bridge"] = feature_bridge
    return {
        "status": "ok",
        "action": "experimental_candidates",
        "pid": os.getpid(),
        "elapsed_seconds": time.perf_counter() - started,
        "candidate_engine": ENGINE_ID,
        "candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
        "analysis_method": report.get("method"),
        "rules": rules,
        "rule_count": len(rules),
        "summary": summary,
        "feature_bridge": feature_bridge,
    }


def _run_experimental_render(
    job: dict[str, Any],
    step_path: Path,
    started: float,
) -> dict[str, Any]:
    """Render engineer-selected Experimental Main candidates as separate views."""
    from auto_2d_drawing.smart_extractors.experimental_candidate_adapter import (
        ENGINE_ID,
        build_experimental_candidate_rules,
        build_experimental_selection_config,
    )

    module = _load_experimental_main_module()
    analysis = module.analyse_step(step_path)
    authoritative_rules = build_experimental_candidate_rules(analysis)
    selection_config = build_experimental_selection_config(
        authoritative_rules,
        list(job.get("feature_records") or []),
        part_hint=str(job.get("part_id") or step_path.stem),
        include_half_section=bool(job.get("include_half_section")),
    )
    selection_summary = selection_config["selection_summary"]
    if selection_summary["rejected"]:
        raise ValueError(
            "Experimental selection validation failed: "
            + json.dumps(selection_summary["rejected"], ensure_ascii=False)
        )

    output_dir = Path(job["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    drawing_report = module.render_minimal_multiview_drawing(
        step_path,
        selection_config["selections"],
        output_dir,
        part_hint=selection_config["part_hint"],
        include_half_section=selection_config["include_half_section"],
        relational_selections=selection_config["relational_selections"],
        pattern_angle_selections=selection_config["pattern_angle_selections"],
        radial_band_angle_selections=selection_config["radial_band_angle_selections"],
        annular_selections=selection_config["annular_selections"],
        rectangular_hole_selections=selection_config["rectangular_hole_selections"],
        shaft_dimension_selections=selection_config["shaft_dimension_selections"],
        thickness_selections=selection_config["thickness_selections"],
    )
    if not drawing_report.get("success"):
        diagnostic_dir = Path(str(job.get("diagnostic_dir") or output_dir)).resolve()
        failure_audit_path = diagnostic_dir / "experimental_renderer_failure_audit.json"
        _write_json(
            failure_audit_path,
            {
                "schema_version": 1,
                "status": "error",
                "stage": "experimental_renderer_audit",
                "operation_id": job.get("operation_id"),
                "step_path": str(step_path),
                "part_id": str(job.get("part_id") or step_path.stem),
                "include_half_section": selection_config["include_half_section"],
                "selection_summary": selection_summary,
                "submitted_feature_records": list(job.get("feature_records") or []),
                "selection_config": selection_config,
                "drawing_report": drawing_report,
            },
        )
        raise RuntimeError(
            "Experimental renderer audit failed: "
            + json.dumps({
                "rejected_feature_selections": drawing_report.get("rejected_feature_selections"),
                "rejected_radial_band_angle_selections": drawing_report.get("rejected_radial_band_angle_selections"),
                "rejected_annular_selections": drawing_report.get("rejected_annular_selections"),
                "rejected_specialized_selections": drawing_report.get("rejected_specialized_selections"),
                "radial_band_angle_placement": drawing_report.get("radial_band_angle_placement"),
            }, ensure_ascii=False)
        )

    selected_views = [
        str(item.get("view") or "front")
        for key, values in selection_config.items()
        if key.endswith("_selections")
        for item in values
    ]
    primary_view = selected_views[0] if selected_views else "front"
    drawings = list(drawing_report.get("orthographic_drawings") or [])
    primary = next(
        (item for item in drawings if item.get("view") == primary_view),
        drawings[0] if drawings else None,
    )
    if primary is None:
        raise RuntimeError("Experimental renderer returned no orthographic drawings")
    files = dict(primary.get("files") or {})
    for name in ("dxf", "pdf", "png"):
        path = Path(str(files.get(name) or ""))
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"Experimental render missing {name} output for {primary_view}")

    return {
        "status": "ok",
        "action": "experimental_render",
        "pid": os.getpid(),
        "elapsed_seconds": time.perf_counter() - started,
        "candidate_engine": ENGINE_ID,
        "analysis_method": analysis.get("method"),
        "selection_summary": selection_summary,
        "drawing_report": drawing_report,
        "primary_view": primary_view,
        "primary_files": files,
        "multiview_drawings": drawings,
        "half_section_drawing": drawing_report.get("half_section_drawing"),
        "report_path": str((output_dir / "minimal_multiview_report.json").resolve()),
    }


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    step_path = Path(job["step_path"]).resolve()
    if not step_path.is_file():
        raise FileNotFoundError(f"STEP file not found: {step_path}")

    action = job.get("action", "render")
    if action == "experimental_shadow_analysis":
        return _run_experimental_shadow_analysis(job, step_path, started)
    if action == "experimental_candidates":
        return _run_experimental_candidates(step_path, started)
    if action == "experimental_render":
        return _run_experimental_render(job, step_path, started)

    from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine

    shape, view_data = _project(step_path)
    engine = SmartAnnotationEngine()

    if action == "candidates":
        rules = engine.get_candidate_rules(
            shape,
            view_data,
            part_hint=job.get("part_id") or step_path.stem,
        )
        return {
            "status": "ok",
            "action": action,
            "pid": os.getpid(),
            "elapsed_seconds": time.perf_counter() - started,
            "rules": rules,
            "rule_count": len(rules),
        }

    if action != "render":
        raise ValueError(f"Unsupported worker action: {action}")

    output_dir = Path(job["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_stem = job.get("output_stem") or "annotation"
    paths = {
        "dxf": output_dir / f"{output_stem}.dxf",
        "pdf": output_dir / f"{output_stem}.pdf",
        "png": output_dir / f"{output_stem}.png",
        "svg": output_dir / f"{output_stem}.svg",
    }
    render_result = engine.render_custom_drawing(
        dxf_path=str(paths["dxf"]),
        pdf_path=str(paths["pdf"]),
        png_path=str(paths["png"]),
        feature_records=job.get("feature_records") or [],
        view_data=view_data,
        title_info=job.get("title_info") or {},
    )
    files = {
        name: {
            "path": str(path),
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in paths.items()
    }
    missing = [name for name, info in files.items() if not info["exists"] or info["bytes"] <= 0]
    if missing:
        raise RuntimeError(f"Render completed without valid outputs: {', '.join(missing)}")
    return {
        "status": "ok",
        "action": action,
        "pid": os.getpid(),
        "elapsed_seconds": time.perf_counter() - started,
        "files": files,
        "engine_result": render_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        job = json.loads(args.job.read_text(encoding="utf-8"))
        result = run_job(job)
        _write_json(args.result, result)
        return 0
    except BaseException as exc:
        result = {
            "status": "error",
            "pid": os.getpid(),
            "elapsed_seconds": time.perf_counter() - started,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        _write_json(args.result, result)
        print(result["traceback"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
