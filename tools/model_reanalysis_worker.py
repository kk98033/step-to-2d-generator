"""Generate a complete reanalysis staging tree in an isolated CAD process."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from auto_2d_drawing.batch_generate import batch_generate


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def generate(source: Path, staging: Path, progress: Path) -> dict:
    source = source.resolve()
    staging = staging.resolve()
    if not source.is_file() or source.suffix.lower() not in {".stp", ".step"}:
        raise FileNotFoundError(f"Reanalysis source STEP does not exist: {source}")
    if staging.exists():
        raise FileExistsError(f"Reanalysis staging directory already exists: {staging}")

    assembly_info = {
        "_assembly": {
            "name": "重新解析組立",
            "drawing_no": source.stem,
            "revision": "R00",
            "material": "---",
            "model_code": "---",
        }
    }

    def progress_callback(total: int, current: int, message: str) -> None:
        _write_json(progress, {
            "total": total,
            "current": current,
            "message": message,
        })

    batch_generate(str(source), str(staging), assembly_info, progress_cb=progress_callback)
    return {"status": "ok", "staging": str(staging)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = generate(args.source, args.staging, args.progress)
        exit_code = 0
    except Exception as exc:
        payload = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
        exit_code = 1
    _write_json(args.result, payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
