"""Run one CAD annotation operation in an isolated operating-system process.

This is a validation worker, not a production queue.  It deliberately imports
the CAD stack only inside the child process so a native-library crash cannot
terminate the web API process.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
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


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine

    started = time.perf_counter()
    step_path = Path(job["step_path"]).resolve()
    if not step_path.is_file():
        raise FileNotFoundError(f"STEP file not found: {step_path}")

    shape, view_data = _project(step_path)
    engine = SmartAnnotationEngine()
    action = job.get("action", "render")

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
