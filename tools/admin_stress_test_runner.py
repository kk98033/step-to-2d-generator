"""Run an administrator-requested isolated CAD capacity sweep.

The browser starts this process through the admin API.  Each simulated heavy
user is a separate ``cad_annotation_worker.py`` process, so the test measures
the native CAD/rendering ceiling without writing engineer history or tolerance
cases.  Progress and reports are persisted below the dedicated stress-test
run directory.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "tools" / "cad_annotation_worker.py"
SAFE_CPU_PEAK_PERCENT = 85.0


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
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
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "message": message,
        **details,
    }
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(float(ordered[index]), 6)


def _health(url: str, timeout: float = 2.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "latency_seconds": round(time.perf_counter() - started, 6),
            }
    except (OSError, urllib.error.URLError) as exc:
        return {
            "ok": False,
            "status": 0,
            "latency_seconds": round(time.perf_counter() - started, 6),
            "error": f"{type(exc).__name__}: {exc}",
        }


class _WindowsSystemSampler:
    """Small dependency-free Windows CPU and memory sampler."""

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("memory_load", ctypes.c_uint32),
            ("total_physical", ctypes.c_uint64),
            ("available_physical", ctypes.c_uint64),
            ("total_page_file", ctypes.c_uint64),
            ("available_page_file", ctypes.c_uint64),
            ("total_virtual", ctypes.c_uint64),
            ("available_virtual", ctypes.c_uint64),
            ("available_extended_virtual", ctypes.c_uint64),
        ]

    def __init__(self) -> None:
        self._previous: tuple[int, int, int] | None = None

    @staticmethod
    def _ticks(value: "_WindowsSystemSampler._FileTime") -> int:
        return (int(value.high) << 32) | int(value.low)

    def sample(self) -> dict[str, Any]:
        cpu_percent: float | None = None
        if os.name == "nt":
            idle = self._FileTime()
            kernel = self._FileTime()
            user = self._FileTime()
            if ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                current = (self._ticks(idle), self._ticks(kernel), self._ticks(user))
                if self._previous is not None:
                    idle_delta = current[0] - self._previous[0]
                    total_delta = (current[1] - self._previous[1]) + (current[2] - self._previous[2])
                    if total_delta > 0:
                        cpu_percent = round(100.0 * (1.0 - idle_delta / total_delta), 3)
                self._previous = current

            memory = self._MemoryStatus()
            memory.length = ctypes.sizeof(self._MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            memory_percent = float(memory.memory_load)
            available_memory = int(memory.available_physical)
            total_memory = int(memory.total_physical)
        else:
            memory_percent = 0.0
            available_memory = 0
            total_memory = 0

        disk = shutil.disk_usage(REPO_ROOT)
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "memory_available_bytes": available_memory,
            "memory_total_bytes": total_memory,
            "disk_free_bytes": int(disk.free),
        }


def _invoke_worker(job: dict[str, Any], directory: Path, timeout_seconds: int) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    job_path = directory / "job.json"
    result_path = directory / "result.json"
    log_path = directory / "worker.log"
    _atomic_json(job_path, job)
    with log_path.open("wb") as log_handle:
        completed = subprocess.run(
            [sys.executable, str(WORKER), "--job", str(job_path), "--result", str(result_path)],
            cwd=REPO_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout_seconds,
        )
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return {"exit_code": completed.returncode, **result}


def _prepare_rules(config: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    action = "experimental_candidates" if config["engine"] == "experimental_v026" else "candidates"
    result = _invoke_worker(
        {"action": action, "step_path": config["step_path"], "part_id": config["part_id"]},
        run_dir / "prepare_rules",
        int(config["job_timeout_seconds"]),
    )
    rules = [dict(item) for item in result.get("rules") or []]
    if result.get("status") != "ok" or not rules:
        raise RuntimeError(result.get("error") or "Candidate-rule preparation returned no rules")

    if config["engine"] == "experimental_v026":
        renderable = [rule for rule in rules if rule.get("render_eligible") is not False]
        preferred = [rule for rule in renderable if rule.get("default_selected")]
        selected_ids = {
            str(rule.get("rule_id") or rule.get("id"))
            for rule in (preferred or renderable[:12])
        }
        for rule in rules:
            rule["enabled"] = str(rule.get("rule_id") or rule.get("id")) in selected_ids
    return rules


def _worker_job(config: dict[str, Any], rules: list[dict[str, Any]], worker_dir: Path, index: int) -> dict[str, Any]:
    if config["engine"] == "experimental_v026":
        return {
            "action": "experimental_render",
            "step_path": config["step_path"],
            "part_id": config["part_id"],
            "operation_id": f"{config['run_id']}-{index}",
            "output_dir": str(worker_dir / "outputs"),
            "diagnostic_dir": str(worker_dir / "diagnostics"),
            "feature_records": rules,
            "include_half_section": False,
            "title_info": {"drawing_title": "Administrator stress test"},
        }
    return {
        "action": "render",
        "step_path": config["step_path"],
        "part_id": config["part_id"],
        "output_dir": str(worker_dir / "outputs"),
        "output_stem": f"stress_{index}",
        "feature_records": rules,
        "title_info": {
            "drawing_title": "Administrator stress test",
            "part_number": config["part_id"],
        },
    }


def _analysis_job(config: dict[str, Any], worker_dir: Path) -> dict[str, Any]:
    return {
        "action": "analysis",
        "step_path": config["step_path"],
        "output_dir": str(worker_dir / "outputs"),
    }


def _run_round(
    config: dict[str, Any],
    rules: list[dict[str, Any]],
    run_dir: Path,
    level: int,
    round_number: int,
    status_path: Path,
    stop_path: Path,
    all_resource_samples: list[dict[str, Any]],
    workload: str = "annotation",
    analysis_workers: int | None = None,
    annotation_workers: int | None = None,
) -> dict[str, Any]:
    if workload == "analysis":
        analysis_count, annotation_count = level, 0
    elif workload == "mixed":
        analysis_count = int(analysis_workers or 0)
        annotation_count = int(annotation_workers or 0)
    else:
        analysis_count, annotation_count = 0, level
    job_kinds = ["analysis"] * analysis_count + ["annotation"] * annotation_count
    total_workers = len(job_kinds)
    round_dir = run_dir / workload / f"level_{level}" / f"round_{round_number}"
    round_dir.mkdir(parents=True, exist_ok=True)
    _append_event(
        run_dir, "ROUND_STARTED",
        f"開始 {workload} 測試：分析 {analysis_count}、標註 {annotation_count}，第 {round_number} 輪",
        workload=workload, level=level, round=round_number,
        analysis_workers=analysis_count, annotation_workers=annotation_count,
    )
    processes: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, job_kind in enumerate(job_kinds, start=1):
        worker_dir = round_dir / f"worker_{index}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        job_path = worker_dir / "job.json"
        result_path = worker_dir / "result.json"
        log_path = worker_dir / "worker.log"
        job = _analysis_job(config, worker_dir) if job_kind == "analysis" else _worker_job(
            config, rules, worker_dir, index
        )
        _atomic_json(job_path, job)
        log_handle = log_path.open("wb")
        process = subprocess.Popen(
            [sys.executable, str(WORKER), "--job", str(job_path), "--result", str(result_path)],
            cwd=REPO_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        processes.append({
            "index": index,
            "process": process,
            "result_path": result_path,
            "log_handle": log_handle,
            "workload": job_kind,
        })

    sampler = _WindowsSystemSampler()
    health_samples: list[dict[str, Any]] = []
    resource_samples: list[dict[str, Any]] = []
    timed_out = False
    stop_requested = False
    safety_abort_reason: str | None = None
    timeout_seconds = int(config["job_timeout_seconds"])
    interval = max(0.25, float(config.get("sample_interval_seconds") or 0.5))
    while any(item["process"].poll() is None for item in processes):
        elapsed = time.perf_counter() - started
        health = _health(config["health_url"])
        resources = sampler.sample()
        resources["elapsed_seconds"] = round(elapsed, 3)
        health["elapsed_seconds"] = round(elapsed, 3)
        health_samples.append(health)
        resource_samples.append(resources)
        all_resource_samples.append({"level": level, "round": round_number, **resources})
        _atomic_json(status_path, {
            "run_id": config["run_id"],
            "status": "stopping" if stop_path.exists() else "running",
            "phase": f"{workload}_capacity_sweep",
            "level": level,
            "round": round_number,
            "max_concurrency": config["max_concurrency"],
            "rounds": config["rounds"],
            "elapsed_seconds": round(elapsed, 3),
            "active_workers": sum(item["process"].poll() is None for item in processes),
            "latest_health": health,
            "latest_resources": resources,
            "runner_pid": os.getpid(),
            "message": f"{workload}：分析 {analysis_count}、標註 {annotation_count}；{sum(item['process'].poll() is None for item in processes)} 個 Worker 執行中",
        })
        if stop_path.exists():
            stop_requested = True
        if elapsed >= timeout_seconds:
            timed_out = True
            for item in processes:
                if item["process"].poll() is None:
                    item["process"].terminate()
            break
        if float(resources.get("memory_percent") or 0.0) >= 90.0:
            safety_abort_reason = "MEMORY_PRESSURE_ABOVE_90"
        elif int(resources.get("disk_free_bytes") or 0) < 1024 ** 3:
            safety_abort_reason = "DISK_FREE_BELOW_1_GB"
        if safety_abort_reason:
            _append_event(
                run_dir, "SAFETY_ABORT", f"資源安全線觸發：{safety_abort_reason}",
                level=level, round=round_number, reason=safety_abort_reason,
            )
            for item in processes:
                if item["process"].poll() is None:
                    item["process"].terminate()
            break
        time.sleep(interval)

    workers = []
    for item in processes:
        process = item["process"]
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        item["log_handle"].close()
        result_path = item["result_path"]
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        workers.append({
            "worker": item["index"],
            "workload": item["workload"],
            "exit_code": process.returncode,
            "status": result.get("status") or "missing_result",
            "elapsed_seconds": result.get("elapsed_seconds"),
            "error_type": result.get("error_type"),
            "error": result.get("error"),
        })

    wall_seconds = time.perf_counter() - started
    successes = sum(item["exit_code"] == 0 and item["status"] == "ok" for item in workers)
    analysis_successes = sum(
        item["workload"] == "analysis" and item["exit_code"] == 0 and item["status"] == "ok"
        for item in workers
    )
    annotation_successes = sum(
        item["workload"] == "annotation" and item["exit_code"] == 0 and item["status"] == "ok"
        for item in workers
    )
    health_successes = sum(bool(item.get("ok")) for item in health_samples)
    cpu_values = [float(item["cpu_percent"]) for item in resource_samples if item.get("cpu_percent") is not None]
    memory_values = [float(item["memory_percent"]) for item in resource_samples]
    result = {
        "level": level,
        "workload": workload,
        "analysis_workers": analysis_count,
        "annotation_workers": annotation_count,
        "analysis_successes": analysis_successes,
        "annotation_successes": annotation_successes,
        "round": round_number,
        "wall_seconds": round(wall_seconds, 6),
        "successes": successes,
        "failures": total_workers - successes,
        "timed_out": timed_out,
        "stop_requested": stop_requested,
        "safety_abort_reason": safety_abort_reason,
        "throughput_jobs_per_second": round(successes / wall_seconds, 6) if wall_seconds else 0.0,
        "health": {
            "samples": len(health_samples),
            "successes": health_successes,
            "availability_percent": round(100.0 * health_successes / len(health_samples), 3)
            if health_samples else 0.0,
            "latency_p95_seconds": _percentile(
                [float(item["latency_seconds"]) for item in health_samples], 0.95
            ),
            "latency_max_seconds": round(
                max((float(item["latency_seconds"]) for item in health_samples), default=0.0), 6
            ),
        },
        "resources": {
            "cpu_average_percent": round(sum(cpu_values) / len(cpu_values), 3) if cpu_values else None,
            "cpu_peak_percent": round(max(cpu_values), 3) if cpu_values else None,
            "memory_peak_percent": round(max(memory_values), 3) if memory_values else None,
            "minimum_memory_available_bytes": min(
                (int(item["memory_available_bytes"]) for item in resource_samples), default=0
            ),
            "minimum_disk_free_bytes": min(
                (int(item["disk_free_bytes"]) for item in resource_samples), default=0
            ),
        },
        "workers": workers,
    }
    _append_event(
        run_dir, "ROUND_COMPLETED",
        f"{workload} 測試完成：成功 {successes}、失敗 {total_workers - successes}",
        workload=workload, level=level, round=round_number,
        successes=successes, failures=total_workers - successes,
        wall_seconds=round(wall_seconds, 3),
    )
    for worker in workers:
        if worker["exit_code"] != 0 or worker["status"] != "ok":
            _append_event(
                run_dir, "WORKER_FAILED",
                f"Worker {worker['worker']} 失敗：{worker.get('error') or worker.get('status')}",
                level=level, round=round_number, worker=worker["worker"],
                error_type=worker.get("error_type"),
            )
    return result


def _recommend(results: list[dict[str, Any]], max_tested: int) -> dict[str, Any]:
    by_level: dict[int, list[dict[str, Any]]] = {}
    for result in results:
        by_level.setdefault(int(result["level"]), []).append(result)
    level_assessments = []
    for level in sorted(by_level):
        rounds = by_level[level]
        reasons = []
        if any(item["failures"] for item in rounds):
            reasons.append("WORKER_FAILURE")
        if any(item["timed_out"] for item in rounds):
            reasons.append("WORKER_TIMEOUT")
        for item in rounds:
            safety_reason = item.get("safety_abort_reason")
            if safety_reason and safety_reason not in reasons:
                reasons.append(safety_reason)
        minimum_availability = min(item["health"]["availability_percent"] for item in rounds)
        maximum_health_p95 = max(item["health"]["latency_p95_seconds"] for item in rounds)
        maximum_memory = max(float(item["resources"]["memory_peak_percent"] or 0.0) for item in rounds)
        maximum_cpu = max(float(item["resources"]["cpu_peak_percent"] or 0.0) for item in rounds)
        if minimum_availability < 99.5:
            reasons.append("API_AVAILABILITY_BELOW_99_5")
        if maximum_health_p95 > 2.0:
            reasons.append("HEALTH_P95_ABOVE_2S")
        if maximum_memory >= 90.0:
            reasons.append("MEMORY_PRESSURE_ABOVE_90")
        if maximum_cpu >= SAFE_CPU_PEAK_PERCENT:
            reasons.append("CPU_PRESSURE_ABOVE_85")
        passed = not reasons
        level_assessments.append({
            "level": level,
            "workload": rounds[0].get("workload", "annotation"),
            "passed": passed,
            "reasons": reasons,
            "minimum_api_availability_percent": minimum_availability,
            "maximum_health_p95_seconds": maximum_health_p95,
            "maximum_cpu_percent": maximum_cpu,
            "maximum_memory_percent": maximum_memory,
            "average_throughput_jobs_per_second": round(
                sum(item["throughput_jobs_per_second"] for item in rounds) / len(rounds), 6
            ),
        })
    failed_levels = [item["level"] for item in level_assessments if not item["passed"]]
    passing_levels = [item["level"] for item in level_assessments if item["passed"]]
    first_failed = min(failed_levels, default=None)
    verified_ceiling = max(
        (level for level in passing_levels if first_failed is None or level < first_failed),
        default=0,
    )
    if verified_ceiling <= 0:
        recommended = 0
        headroom_policy = "NO_SAFE_LEVEL_VERIFIED"
    elif verified_ceiling >= max_tested and verified_ceiling > 1:
        recommended = verified_ceiling - 1
        headroom_policy = "ONE_LEVEL_BELOW_TESTED_CEILING"
    else:
        recommended = verified_ceiling
        headroom_policy = "HIGHEST_CONTIGUOUS_PASS"
    capacity_boundary_found = bool(failed_levels)
    return {
        "verified_active_heavy_user_ceiling": verified_ceiling,
        "recommended_active_heavy_users": recommended,
        "capacity_boundary_found": capacity_boundary_found,
        "tested_upper_bound": max(by_level, default=0),
        "headroom_policy": headroom_policy,
        "level_assessments": level_assessments,
        "note": (
            "The first failing capacity level was found; the recommendation is for active CAD workers, "
            "not logged-in or read-only users."
            if capacity_boundary_found else
            "No failing capacity level was found within the selected probe range. The verified value is "
            "a lower bound, not the system limit; raise the probe range to continue."
        ),
    }


def _result_passed(result: dict[str, Any]) -> bool:
    return not (
        result["failures"]
        or result["timed_out"]
        or result["health"]["availability_percent"] < 99.5
        or result["health"]["latency_p95_seconds"] > 2.0
        or float(result["resources"]["memory_peak_percent"] or 0.0) >= 90.0
        or float(result["resources"]["cpu_peak_percent"] or 0.0) >= SAFE_CPU_PEAK_PERCENT
    )


def _adaptive_sweep(
    config: dict[str, Any], rules: list[dict[str, Any]], run_dir: Path,
    status_path: Path, stop_path: Path, resource_samples: list[dict[str, Any]],
    workload: str,
) -> tuple[list[dict[str, Any]], bool]:
    results: list[dict[str, Any]] = []
    tested: dict[int, bool] = {}
    maximum = int(config["max_concurrency"])

    def test_level(level: int) -> tuple[bool, bool]:
        level_passed = True
        for round_number in range(1, int(config["rounds"]) + 1):
            if stop_path.exists():
                return False, True
            result = _run_round(
                config, rules, run_dir, level, round_number, status_path, stop_path,
                resource_samples, workload=workload,
            )
            results.append(result)
            if result["stop_requested"]:
                return False, True
            if not _result_passed(result):
                level_passed = False
                break
        tested[level] = level_passed
        return level_passed, False

    last_pass = 0
    first_fail: int | None = None
    level = 1
    while level <= maximum:
        passed, stopped = test_level(level)
        if stopped:
            return results, True
        if not passed:
            first_fail = level
            break
        last_pass = level
        if level == maximum:
            break
        level = min(maximum, level * 2)

    if first_fail is not None:
        low, high = last_pass, first_fail
        while high - low > 1:
            middle = (low + high) // 2
            passed, stopped = test_level(middle)
            if stopped:
                return results, True
            if passed:
                low = middle
            else:
                high = middle
    return results, False


def _mixed_verification(
    config: dict[str, Any], rules: list[dict[str, Any]], run_dir: Path,
    status_path: Path, stop_path: Path, resource_samples: list[dict[str, Any]],
    analysis_limit: int, annotation_limit: int,
) -> tuple[list[dict[str, Any]], int, int, bool]:
    results: list[dict[str, Any]] = []
    analysis_count, annotation_count = analysis_limit, annotation_limit
    while analysis_count > 0 and annotation_count > 0:
        pair_passed = True
        for round_number in range(1, int(config["rounds"]) + 1):
            if stop_path.exists():
                return results, analysis_count, annotation_count, True
            result = _run_round(
                config, rules, run_dir, analysis_count + annotation_count, round_number,
                status_path, stop_path, resource_samples, workload="mixed",
                analysis_workers=analysis_count, annotation_workers=annotation_count,
            )
            results.append(result)
            if result["stop_requested"]:
                return results, analysis_count, annotation_count, True
            if not _result_passed(result):
                pair_passed = False
                analysis_failed = result["analysis_successes"] < analysis_count
                annotation_failed = result["annotation_successes"] < annotation_count
                if analysis_failed:
                    analysis_count -= 1
                if annotation_failed:
                    annotation_count -= 1
                if not analysis_failed and not annotation_failed:
                    if analysis_count >= annotation_count:
                        analysis_count -= 1
                    else:
                        annotation_count -= 1
                break
        if pair_passed:
            return results, analysis_count, annotation_count, False
    return results, max(0, analysis_count), max(0, annotation_count), False


def _capacity_planning(
    results: list[dict[str, Any]], recommendation: dict[str, Any],
    scenario_users: tuple[int, ...] = (10, 20, 50, 100),
) -> dict[str, Any]:
    """Convert measured worker duration into an explicit deterministic queue model."""
    definitions = []
    if recommendation.get("recommended_analysis_concurrency") is not None:
        definitions.append((
            "analysis",
            int(recommendation.get("recommended_analysis_concurrency") or 0),
            int(recommendation.get("verified_analysis_ceiling") or 0),
        ))
    definitions.append((
        "annotation",
        int(
            recommendation.get("recommended_annotation_concurrency")
            or recommendation.get("recommended_active_heavy_users")
            or 0
        ),
        int(
            recommendation.get("verified_annotation_ceiling")
            or recommendation.get("verified_active_heavy_user_ceiling")
            or 0
        ),
    ))
    pools = []
    for workload, slots, verified_ceiling in definitions:
        if slots <= 0:
            continue
        source = [
            item for item in results
            if item.get("workload") == workload
            and int(item.get("level") or 0) == slots
            and not item.get("failures")
        ]
        if not source:
            worker_key = f"{workload}_workers"
            source = [
                item for item in results
                if item.get("workload") == "mixed"
                and int(item.get(worker_key) or 0) == slots
                and not item.get("failures")
            ]
        durations = [
            float(worker["elapsed_seconds"])
            for item in source
            for worker in (item.get("workers") or [])
            if worker.get("workload") == workload
            and float(worker.get("elapsed_seconds") or 0.0) > 0.0
        ]
        if durations:
            average_seconds = sum(durations) / len(durations)
            p95_seconds = _percentile(durations, 0.95)
        elif source:
            round_seconds = [float(item.get("wall_seconds") or 0.0) for item in source]
            average_seconds = sum(round_seconds) / len(round_seconds)
            p95_seconds = _percentile(round_seconds, 0.95)
        else:
            average_seconds = 0.0
            p95_seconds = 0.0

        scenarios = []
        for users in scenario_users:
            waits = [math.floor(index / slots) * average_seconds for index in range(users)]
            scenarios.append({
                "submitted_users": users,
                "active_immediately": min(users, slots),
                "queued_initially": max(0, users - slots),
                "batch_count": math.ceil(users / slots),
                "average_wait_seconds": round(sum(waits) / users, 3),
                "p95_wait_seconds": _percentile(waits, 0.95),
                "last_user_starts_after_seconds": round(max(waits, default=0.0), 3),
                "estimated_queue_drain_seconds": round(max(waits, default=0.0) + average_seconds, 3),
            })
        pools.append({
            "workload": workload,
            "recommended_active_slots": slots,
            "verified_individual_ceiling": verified_ceiling,
            "measured_average_job_seconds": round(average_seconds, 3),
            "measured_p95_job_seconds": round(p95_seconds, 3),
            "modeled_jobs_per_second": round(slots / average_seconds, 6) if average_seconds else 0.0,
            "queue_scenarios": scenarios,
        })

    return {
        "scope": "ACTIVE_HEAVY_CAD_JOBS_ON_THIS_HOST",
        "recommended_simultaneous_heavy_users": sum(
            int(item["recommended_active_slots"]) for item in pools
        ),
        "verified_individual_pool_ceiling_total": sum(
            int(item["verified_individual_ceiling"]) for item in pools
        ),
        "logged_in_user_limit": None,
        "pools": pools,
        "model": "MEASURED_SERVICE_TIME_DETERMINISTIC_FIFO",
        "assumptions": [
            "Each submitted user contributes one heavy CAD job to the selected pool.",
            "FIFO queueing and the report's recommended active slots are used.",
            "Wait estimates extrapolate measured service time; they are not a 100-job soak test.",
            "Different STEP complexity and background load will change actual wait time.",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    recommendation = report["recommendation"]
    capacity = report.get("capacity_planning") or {}
    lines = [
        "# 管理員 CAD 壓力測試報告",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Engine: `{report['engine']}`",
        f"- Fixture: `{report['fixture']['name']}` ({report['fixture']['bytes']} bytes)",
        f"- Candidate rules: `{report['candidate_rule_count']}`",
        f"- Verified active-heavy-user ceiling: `{recommendation['verified_active_heavy_user_ceiling']}`",
        f"- Recommended active-heavy-user setting: `{recommendation['recommended_active_heavy_users']}`",
        f"- Capacity boundary found: `{recommendation['capacity_boundary_found']}`",
        "",
    ]
    if recommendation.get("recommended_analysis_concurrency") is not None:
        lines.extend([
            f"- Recommended analysis concurrency: `{recommendation['recommended_analysis_concurrency']}`",
            f"- Recommended annotation concurrency: `{recommendation['recommended_annotation_concurrency']}`",
            f"- Mixed workload verified: `{recommendation['mixed_verified']}`",
            "",
        ])
    lines.extend([
        "",
        "## 機器同時使用量與排隊估算",
        "",
        f"- Recommended simultaneous heavy CAD users: `{capacity.get('recommended_simultaneous_heavy_users', 0)}`",
        f"- Verified individual-pool ceiling total: `{capacity.get('verified_individual_pool_ceiling_total', 0)}`",
        "- Logged-in/read-only users are not limited by this number.",
        "- Queue estimates use measured job duration and deterministic FIFO; they are estimates, not a 100-job soak test.",
        "",
    ])
    for pool in capacity.get("pools") or []:
        lines.extend([
            f"### {pool['workload']}",
            "",
            f"- Recommended active slots: `{pool['recommended_active_slots']}`",
            f"- Measured average job time: `{pool['measured_average_job_seconds']:.3f}s`",
            f"- Measured p95 job time: `{pool['measured_p95_job_seconds']:.3f}s`",
            "",
            "| Submitted users | Active now | Initially queued | Average wait | P95 wait | Last starts | Queue drains |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for scenario in pool.get("queue_scenarios") or []:
            lines.append(
                f"| {scenario['submitted_users']} | {scenario['active_immediately']} | "
                f"{scenario['queued_initially']} | {scenario['average_wait_seconds']:.1f}s | "
                f"{scenario['p95_wait_seconds']:.1f}s | "
                f"{scenario['last_user_starts_after_seconds']:.1f}s | "
                f"{scenario['estimated_queue_drain_seconds']:.1f}s |"
            )
        lines.append("")
    lines.extend([
        "## 容量階梯",
        "",
        "| Pool | Level | Pass | Throughput | CPU peak | Memory peak | API availability | Health p95 |",
        "|:---|---:|:---:|---:|---:|---:|---:|---:|",
    ])
    for item in recommendation["level_assessments"]:
        lines.append(
            f"| {item.get('workload', 'annotation')} | {item['level']} | {'PASS' if item['passed'] else 'FAIL'} | "
            f"{item['average_throughput_jobs_per_second']:.3f} | "
            f"{item['maximum_cpu_percent']:.1f}% | {item['maximum_memory_percent']:.1f}% | "
            f"{item['minimum_api_availability_percent']:.1f}% | "
            f"{item['maximum_health_p95_seconds']:.3f}s |"
        )
    lines.extend([
        "",
        "## 判讀",
        "",
        "- 建議值只適用於本報告記錄的電腦、程式版本、引擎與測試模型。",
        "- 登入或瀏覽使用者不受此數字限制；此數字代表同時執行重型 CAD 工作的人數。",
        "- 自動套用前仍應由管理員確認，並保留壓測前設定以便還原。",
    ])
    return "\n".join(lines) + "\n"


def run(config: dict[str, Any], run_dir: Path, status_path: Path, stop_path: Path) -> dict[str, Any]:
    _append_event(run_dir, "PREPARING", "正在讀取 STEP 並提取候選特徵")
    _atomic_json(status_path, {
        "run_id": config["run_id"], "status": "running", "phase": "preparing",
        "level": 0, "round": 0, "max_concurrency": config["max_concurrency"],
        "rounds": config["rounds"], "runner_pid": os.getpid(),
        "message": "正在讀取 STEP 並提取候選特徵",
    })
    rules = _prepare_rules(config, run_dir)
    _append_event(run_dir, "FEATURES_READY", f"候選特徵準備完成，共 {len(rules)} 項", candidate_rule_count=len(rules))
    results: list[dict[str, Any]] = []
    resource_samples: list[dict[str, Any]] = []
    stopped = False
    test_mode = str(config.get("test_mode") or "annotation_capacity")
    if test_mode == "auto_dual_capacity":
        _append_event(run_dir, "AUTO_CALIBRATION", "開始自動校準分析池容量")
        analysis_results, stopped = _adaptive_sweep(
            config, rules, run_dir, status_path, stop_path, resource_samples, "analysis"
        )
        results.extend(analysis_results)
        analysis_recommendation = _recommend(analysis_results, int(config["max_concurrency"]))

        annotation_results: list[dict[str, Any]] = []
        if not stopped:
            _append_event(run_dir, "AUTO_CALIBRATION", "開始自動校準標註池容量")
            annotation_results, stopped = _adaptive_sweep(
                config, rules, run_dir, status_path, stop_path, resource_samples, "annotation"
            )
            results.extend(annotation_results)
        annotation_recommendation = _recommend(annotation_results, int(config["max_concurrency"]))

        recommended_analysis = int(analysis_recommendation["recommended_active_heavy_users"])
        recommended_annotation = int(annotation_recommendation["recommended_active_heavy_users"])
        mixed_results: list[dict[str, Any]] = []
        if not stopped and recommended_analysis > 0 and recommended_annotation > 0:
            _append_event(
                run_dir, "MIXED_VERIFICATION",
                f"開始混合驗證：分析 {recommended_analysis}、標註 {recommended_annotation}",
            )
            mixed_results, recommended_analysis, recommended_annotation, stopped = _mixed_verification(
                config, rules, run_dir, status_path, stop_path, resource_samples,
                recommended_analysis, recommended_annotation,
            )
            results.extend(mixed_results)
        mixed_verified = bool(mixed_results and _result_passed(mixed_results[-1]))
        recommendation = {
            "recommended_analysis_concurrency": recommended_analysis,
            "recommended_annotation_concurrency": recommended_annotation,
            "verified_analysis_ceiling": analysis_recommendation["verified_active_heavy_user_ceiling"],
            "verified_annotation_ceiling": annotation_recommendation["verified_active_heavy_user_ceiling"],
            "analysis_capacity_boundary_found": analysis_recommendation["capacity_boundary_found"],
            "annotation_capacity_boundary_found": annotation_recommendation["capacity_boundary_found"],
            "mixed_verified": mixed_verified,
            "recommended_active_heavy_users": recommended_analysis + recommended_annotation,
            "verified_active_heavy_user_ceiling": (
                analysis_recommendation["verified_active_heavy_user_ceiling"]
                + annotation_recommendation["verified_active_heavy_user_ceiling"]
            ),
            "capacity_boundary_found": (
                analysis_recommendation["capacity_boundary_found"]
                and annotation_recommendation["capacity_boundary_found"]
            ),
            "tested_upper_bound": int(config["max_concurrency"]),
            "headroom_policy": "INDEPENDENT_POOLS_PLUS_MIXED_VERIFICATION",
            "level_assessments": [
                *analysis_recommendation["level_assessments"],
                *annotation_recommendation["level_assessments"],
            ],
            "analysis": analysis_recommendation,
            "annotation": annotation_recommendation,
            "note": "分析池與標註池分別自動搜尋容量邊界，最後以兩者同時執行的混合負載驗證建議組合。",
        }
    else:
        for level in range(1, int(config["max_concurrency"]) + 1):
            for round_number in range(1, int(config["rounds"]) + 1):
                if stop_path.exists():
                    stopped = True
                    break
                result = _run_round(
                    config, rules, run_dir, level, round_number, status_path, stop_path, resource_samples
                )
                results.append(result)
                if result["stop_requested"]:
                    stopped = True
                    break
                if not _result_passed(result):
                    break
            if stopped or (results and not _result_passed(results[-1])):
                break
        recommendation = _recommend(results, int(config["max_concurrency"]))
        recommendation["recommended_annotation_concurrency"] = recommendation["recommended_active_heavy_users"]

    report = {
        "schema_version": 3,
        "method": "ADMIN_AUTO_DUAL_CAPACITY_V3" if test_mode == "auto_dual_capacity" else "ADMIN_ISOLATED_CAD_CAPACITY_SWEEP_V2",
        "test_mode": test_mode,
        "run_id": config["run_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "stopped" if stopped else "completed",
        "engine": config["engine"],
        "fixture": {
            "name": Path(config["step_path"]).name,
            "path": config["step_path"],
            "bytes": Path(config["step_path"]).stat().st_size,
        },
        "candidate_rule_count": len(rules),
        "configured_max_concurrency": config["max_concurrency"],
        "rounds_per_level": config["rounds"],
        "settings_before": config.get("settings_before") or {},
        "results": results,
        "resource_samples": resource_samples,
        "recommendation": recommendation,
        "capacity_planning": _capacity_planning(results, recommendation),
    }
    _atomic_json(run_dir / "report.json", report)
    (run_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    _atomic_json(status_path, {
        "run_id": config["run_id"],
        "status": report["status"],
        "phase": "complete",
        "level": results[-1]["level"] if results else 0,
        "round": results[-1]["round"] if results else 0,
        "max_concurrency": config["max_concurrency"],
        "rounds": config["rounds"],
        "recommendation": report["recommendation"],
        "message": "容量階梯測試已完成" if not stopped else "容量階梯測試已停止",
    })
    _append_event(run_dir, "RUN_COMPLETED", "容量階梯測試已完成" if not stopped else "容量階梯測試已停止")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    status_path = args.run_dir / "status.json"
    stop_path = args.run_dir / "stop.requested"
    try:
        run(config, args.run_dir, status_path, stop_path)
        return 0
    except BaseException as exc:
        _append_event(args.run_dir, "RUN_FAILED", f"測試失敗：{type(exc).__name__}: {exc}")
        _atomic_json(status_path, {
            "run_id": config.get("run_id"),
            "status": "error",
            "phase": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "runner_pid": os.getpid(),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
