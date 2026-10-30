"""Persistent single-host administrator stress-test orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.025 * (2 ** attempt))


def _append_event(run_dir: Path, event: str, message: str, **details: Any) -> None:
    payload = {"at": _utc_iso(), "event": event, "message": message, **details}
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_events(run_dir: Path, limit: int = 100) -> list[dict[str, Any]]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _pid_is_running(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


class StressTestService:
    """Launch one isolated capacity sweep and expose filesystem-backed state."""

    def __init__(self, repo_root: str | Path, output_root: str | Path, models_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.output_root = Path(output_root).resolve()
        self.models_root = Path(models_root).resolve()
        self.runs_root = self.output_root / "_stress_tests"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.runner = self.repo_root / "tools" / "admin_stress_test_runner.py"
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._active_run_id: Optional[str] = None
        self._exclusive = False

    def _refresh_process(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is not None:
                exit_code = self._process.returncode
                run_id = self._active_run_id
                if run_id and exit_code:
                    run_dir = self._run_dir(run_id)
                    status_path = run_dir / "status.json"
                    try:
                        status = json.loads(status_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        status = {"run_id": run_id}
                    if status.get("status") in {"starting", "running", "stopping"}:
                        status.update({
                            "status": "error", "phase": "failed", "runner_exit_code": exit_code,
                            "error": f"壓力測試 Runner 非正常結束（exit code {exit_code}）",
                            "message": "Runner 非正常結束，請查看事件紀錄與 runner.log",
                        })
                        _atomic_json(status_path, status)
                        _append_event(run_dir, "RUNNER_EXITED", status["message"], exit_code=exit_code)
                self._process = None
                self._active_run_id = None
                self._exclusive = False

    @property
    def exclusive_mode(self) -> bool:
        self._refresh_process()
        with self._lock:
            return bool(self._process is not None and self._exclusive)

    @property
    def active_run_id(self) -> Optional[str]:
        self._refresh_process()
        with self._lock:
            return self._active_run_id

    def fixture_catalog(self, limit: int = 120) -> list[dict[str, Any]]:
        candidates: list[Path] = []
        if self.models_root.is_dir():
            candidates.extend(self.models_root.glob("*.stp"))
            candidates.extend(self.models_root.glob("*.step"))
            # Uploaded production models live below models/_users/<user>/<job>/.
            # Keep the lookup bounded to the model store and cap the returned list.
            candidates.extend(self.models_root.glob("_users/**/*.stp"))
            candidates.extend(self.models_root.glob("_users/**/*.step"))
        if self.output_root.is_dir():
            candidates.extend(self.output_root.glob("*_batch/_parts/*.stp"))
            candidates.extend(self.output_root.glob("*_batch/_parts/*.step"))

        unique: dict[str, Path] = {}
        allowed_roots = (self.models_root, self.output_root)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if not resolved.is_file() or not any(
                    os.path.commonpath([str(root), str(resolved)]) == str(root)
                    for root in allowed_roots
                ):
                    continue
                unique[str(resolved).lower()] = resolved
            except (OSError, ValueError):
                continue

        def source_info(path: Path) -> tuple[int, str]:
            try:
                relative_to_models = path.relative_to(self.models_root)
                if relative_to_models.parts and relative_to_models.parts[0] == "_users":
                    return 1, "uploaded"
                return 0, "system_model"
            except ValueError:
                return 2, "derived_part"

        fixtures = []
        ordered = sorted(
            unique.values(),
            key=lambda item: (source_info(item)[0], item.stat().st_size, item.name.lower()),
        )
        for path in ordered[:limit]:
            relative = path.relative_to(self.repo_root).as_posix()
            fixture_id = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
            _priority, source = source_info(path)
            fixtures.append({
                "id": fixture_id,
                "name": path.name,
                "relative_path": relative,
                "bytes": path.stat().st_size,
                "source": source,
                "size_class": (
                    "small" if path.stat().st_size < 500_000 else
                    "medium" if path.stat().st_size < 5_000_000 else "large"
                ),
            })
        return fixtures

    def resolve_fixture(self, fixture_id: str) -> dict[str, Any]:
        fixture = next((item for item in self.fixture_catalog() if item["id"] == fixture_id), None)
        if fixture is None:
            raise ValueError("Unknown or unavailable stress-test fixture")
        path = (self.repo_root / fixture["relative_path"]).resolve()
        return {**fixture, "path": str(path)}

    def start(
        self,
        *,
        fixture_id: str,
        engine: str,
        test_mode: str,
        max_concurrency: int,
        rounds: int,
        job_timeout_seconds: int,
        health_url: str,
        settings_before: dict[str, Any],
        exclusive: bool,
        requested_by: str,
    ) -> dict[str, Any]:
        self._refresh_process()
        with self._lock:
            if self._process is not None:
                raise RuntimeError(f"Stress test {self._active_run_id} is already running")
            fixture = self.resolve_fixture(fixture_id)
            if engine not in {"legacy_smart", "experimental_v026"}:
                raise ValueError("Unsupported stress-test engine")
            if test_mode not in {"annotation_capacity", "auto_dual_capacity"}:
                raise ValueError("Unsupported stress-test mode")
            run_id = f"stress-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            run_dir = self.runs_root / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            config = {
                "schema_version": 1,
                "run_id": run_id,
                "requested_at": _utc_iso(),
                "requested_by": requested_by,
                "fixture_id": fixture_id,
                "step_path": fixture["path"],
                "part_id": Path(fixture["name"]).stem,
                "engine": engine,
                "test_mode": test_mode,
                "max_concurrency": int(max_concurrency),
                "rounds": int(rounds),
                "job_timeout_seconds": int(job_timeout_seconds),
                "sample_interval_seconds": 0.5,
                "health_url": health_url,
                "settings_before": settings_before,
                "exclusive": bool(exclusive),
            }
            config_path = run_dir / "config.json"
            _atomic_json(config_path, config)
            _atomic_json(run_dir / "status.json", {
                "run_id": run_id,
                "status": "starting",
                "phase": "launching",
                "level": 0,
                "round": 0,
                "max_concurrency": int(max_concurrency),
                "rounds": int(rounds),
                "message": "正在啟動壓力測試 Runner",
            })
            _append_event(run_dir, "RUN_REQUESTED", "管理員已送出壓力測試", requested_by=requested_by)
            log_handle = (run_dir / "runner.log").open("wb")
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(self.runner),
                    "--config",
                    str(config_path),
                    "--run-dir",
                    str(run_dir),
                ],
                cwd=self.repo_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            log_handle.close()
            self._process = process
            self._active_run_id = run_id
            self._exclusive = bool(exclusive)
            return self.get(run_id)

    def _run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("stress-") or any(token in run_id for token in ("/", "\\", "..")):
            raise ValueError("Invalid stress-test run ID")
        run_dir = (self.runs_root / run_id).resolve()
        if os.path.commonpath([str(self.runs_root), str(run_dir)]) != str(self.runs_root):
            raise ValueError("Invalid stress-test run path")
        return run_dir

    def get(self, run_id: str) -> dict[str, Any]:
        self._refresh_process()
        run_dir = self._run_dir(run_id)
        status_path = run_dir / "status.json"
        if not status_path.is_file():
            raise KeyError("Stress-test run not found")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if (
            status.get("status") in {"starting", "running", "stopping"}
            and self.active_run_id != run_id
            and not _pid_is_running(status.get("runner_pid"))
        ):
            status.update({
                "status": "error", "phase": "failed",
                "error": "壓力測試 Runner 已停止，但未能寫入完成狀態",
                "message": "偵測到失去連線的 Runner，請查看事件紀錄與 runner.log",
            })
            _atomic_json(status_path, status)
            _append_event(run_dir, "RUNNER_ORPHANED", status["message"])
        config_path = run_dir / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        report_path = run_dir / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
        status.update({
            "engine": config.get("engine"),
            "test_mode": config.get("test_mode") or "annotation_capacity",
            "fixture_id": config.get("fixture_id"),
            "fixture_name": Path(str(config.get("step_path") or "")).name,
            "exclusive": bool(config.get("exclusive")),
            "requested_at": config.get("requested_at"),
            "settings_before": config.get("settings_before") or {},
            "report": report,
            "report_json_url": f"/api/files/_stress_tests/{run_id}/report.json" if report else None,
            "report_markdown_url": f"/api/files/_stress_tests/{run_id}/report.md" if report else None,
            "events": _read_events(run_dir),
        })
        return status

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        runs = []
        for path in self.runs_root.glob("stress-*"):
            try:
                item = self.get(path.name)
                if item.get("report"):
                    item["report"] = {
                        key: value for key, value in item["report"].items()
                        if key not in {"resource_samples", "results"}
                    }
                runs.append(item)
            except (KeyError, ValueError, OSError, json.JSONDecodeError):
                continue
        def sort_key(item: dict[str, Any]) -> tuple[float, str]:
            raw = str(
                item.get("requested_at")
                or (item.get("report") or {}).get("generated_at")
                or ""
            )
            try:
                timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                timestamp = 0.0
            return timestamp, str(item.get("run_id") or "")

        runs.sort(key=sort_key, reverse=True)
        return runs[:limit]

    def delete(self, run_id: str) -> dict[str, Any]:
        """Delete one inactive run and all of its isolated report artifacts."""
        self._refresh_process()
        with self._lock:
            if self._active_run_id == run_id:
                raise RuntimeError("Cannot delete the active stress-test run")
            run_dir = self._run_dir(run_id)
            if not run_dir.is_dir():
                raise KeyError("Stress-test run not found")
            status_path = run_dir / "status.json"
            if status_path.is_file():
                status = json.loads(status_path.read_text(encoding="utf-8"))
                if status.get("status") in {"starting", "running", "stopping"}:
                    raise RuntimeError("Cannot delete a running stress-test run")
            shutil.rmtree(run_dir)
            return {"run_id": run_id, "deleted": True}

    def request_stop(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError("Stress-test run not found")
        (run_dir / "stop.requested").touch(exist_ok=True)
        return self.get(run_id)

    def settings_before(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        return dict(config.get("settings_before") or {})

    def recommendation(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        report_path = run_dir / "report.json"
        if not report_path.is_file():
            raise RuntimeError("Stress test has not produced a report")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "completed":
            raise RuntimeError("Only a completed stress test can apply recommendations")
        return dict(report.get("recommendation") or {})
