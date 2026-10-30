"""Run blocking model-output filesystem operations outside the API process."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


def replace_directory(source: Path, destination: Path) -> dict[str, str]:
    source = source.resolve()
    destination = destination.resolve()
    if source.parent != destination.parent:
        raise ValueError("Directory swap paths must share the same parent")
    if not source.is_dir():
        raise FileNotFoundError(f"Swap source does not exist: {source}")
    if destination.exists():
        raise FileExistsError(f"Swap destination already exists: {destination}")
    os.replace(source, destination)
    return {"status": "ok", "source": str(source), "destination": str(destination)}


def promote_reanalysis(staging: Path, current: Path, backup: Path) -> dict[str, str]:
    """Preserve user data and promote a completed staging directory with rollback."""
    staging = staging.resolve()
    current = current.resolve()
    backup = backup.resolve()
    if len({staging.parent, current.parent, backup.parent}) != 1:
        raise ValueError("Promotion paths must share the same parent")
    if not staging.is_dir():
        raise FileNotFoundError(f"Reanalysis staging directory does not exist: {staging}")
    if not current.is_dir():
        raise FileNotFoundError(f"Current model directory does not exist: {current}")
    if backup.exists():
        raise FileExistsError(f"Reanalysis backup already exists: {backup}")

    current_users = current / "_users"
    if current_users.is_dir():
        shutil.copytree(current_users, staging / "_users", dirs_exist_ok=True)

    os.replace(current, backup)
    try:
        os.replace(staging, current)
    except BaseException:
        if backup.is_dir() and not current.exists():
            os.replace(backup, current)
        raise
    return {
        "status": "ok",
        "staging": str(staging),
        "current": str(current),
        "backup": str(backup),
    }


def remove_directory(target: Path) -> dict[str, str]:
    target = target.resolve()
    if target.is_dir():
        shutil.rmtree(target)
    return {"status": "ok", "target": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("replace", "promote", "remove"), default="replace")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--staging", type=Path)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "replace":
            if args.source is None or args.destination is None:
                raise ValueError("replace requires --source and --destination")
            payload = replace_directory(args.source, args.destination)
        elif args.operation == "promote":
            if args.staging is None or args.current is None or args.backup is None:
                raise ValueError("promote requires --staging, --current, and --backup")
            payload = promote_reanalysis(args.staging, args.current, args.backup)
        else:
            if args.target is None:
                raise ValueError("remove requires --target")
            payload = remove_directory(args.target)
        exit_code = 0
    except Exception as exc:
        payload = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
        exit_code = 1
    args.result.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
