"""Benchmark simultaneous smart-annotation renders in isolated processes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "tools" / "cad_annotation_worker.py"
DEFAULT_STEP = (
    REPO_ROOT
    / "auto_2d_drawing"
    / "output"
    / "BLADE_ASSY-1AC085000H-R01_37f6697b_batch"
    / "_parts"
    / "SHAFT-2AC085010H_1.stp"
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def backend_health(url: str, timeout: float = 1.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "latency_seconds": time.perf_counter() - started,
            }
    except (OSError, urllib.error.URLError) as exc:
        return {
            "ok": False,
            "status": 0,
            "latency_seconds": time.perf_counter() - started,
            "error": str(exc),
        }


def invoke_worker(python: Path, job_path: Path, result_path: Path, log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            [str(python), str(WORKER), "--job", str(job_path), "--result", str(result_path)],
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return completed.returncode


def prepare_rules(python: Path, step_path: Path, run_dir: Path, part_id: str) -> list[dict[str, Any]]:
    job_path = run_dir / "prepare_rules.job.json"
    result_path = run_dir / "prepare_rules.result.json"
    write_json(job_path, {"action": "candidates", "step_path": str(step_path), "part_id": part_id})
    exit_code = invoke_worker(python, job_path, result_path, run_dir / "prepare_rules.log")
    if exit_code != 0 or not result_path.exists():
        raise RuntimeError("Candidate-rule preparation worker failed; inspect prepare_rules.log")
    result = read_json(result_path)
    if result.get("status") != "ok" or not result.get("rules"):
        raise RuntimeError(f"Candidate-rule preparation failed: {result.get('error', 'no rules')}")
    return result["rules"]


def run_level(
    *,
    python: Path,
    step_path: Path,
    rules: list[dict[str, Any]],
    level: int,
    round_number: int,
    run_dir: Path,
    health_url: str,
    part_id: str,
) -> dict[str, Any]:
    level_dir = run_dir / f"level_{level}" / f"round_{round_number}"
    level_dir.mkdir(parents=True, exist_ok=True)
    processes: list[tuple[subprocess.Popen[bytes], Path, Any]] = []
    started = time.perf_counter()

    for worker_number in range(1, level + 1):
        worker_dir = level_dir / f"worker_{worker_number}"
        job_path = worker_dir / "job.json"
        result_path = worker_dir / "result.json"
        log_path = worker_dir / "worker.log"
        worker_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            job_path,
            {
                "action": "render",
                "step_path": str(step_path),
                "part_id": part_id,
                "output_dir": str(worker_dir / "outputs"),
                "output_stem": f"{part_id}_isolated_{worker_number}",
                "feature_records": rules,
                "title_info": {
                    "drawing_title": f"Process isolation benchmark {level}x",
                    "part_number": part_id,
                },
            },
        )
        log_handle = log_path.open("wb")
        process = subprocess.Popen(
            [str(python), str(WORKER), "--job", str(job_path), "--result", str(result_path)],
            cwd=REPO_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        processes.append((process, result_path, log_handle))

    health_samples: list[dict[str, Any]] = []
    while any(process.poll() is None for process, _, _ in processes):
        sample = backend_health(health_url)
        sample["elapsed_seconds"] = time.perf_counter() - started
        health_samples.append(sample)
        time.sleep(0.2)
    health_samples.append({**backend_health(health_url), "elapsed_seconds": time.perf_counter() - started})

    workers = []
    for worker_number, (process, result_path, log_handle) in enumerate(processes, start=1):
        log_handle.close()
        result = read_json(result_path) if result_path.exists() else {"status": "missing_result"}
        workers.append(
            {
                "worker": worker_number,
                "exit_code": process.returncode,
                "status": result.get("status"),
                "pid": result.get("pid"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "files": result.get("files"),
                "error_type": result.get("error_type"),
                "error": result.get("error"),
            }
        )

    wall_seconds = time.perf_counter() - started
    successes = sum(worker["exit_code"] == 0 and worker["status"] == "ok" for worker in workers)
    health_successes = sum(sample["ok"] for sample in health_samples)
    return {
        "level": level,
        "round": round_number,
        "wall_seconds": wall_seconds,
        "successes": successes,
        "failures": level - successes,
        "throughput_jobs_per_second": successes / wall_seconds if wall_seconds else 0,
        "backend_health": {
            "samples": len(health_samples),
            "successes": health_successes,
            "failures": len(health_samples) - health_successes,
            "availability_percent": 100.0 * health_successes / len(health_samples),
            "max_latency_seconds": max(sample["latency_seconds"] for sample in health_samples),
        },
        "workers": workers,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    rows = []
    for result in summary["results"]:
        rows.append(
            f"| {result['level']} | {result['round']} | {result['successes']}/{result['level']} | "
            f"{result['wall_seconds']:.2f} | {result['throughput_jobs_per_second']:.2f} | "
            f"{result['backend_health']['availability_percent']:.1f}% | "
            f"{result['backend_health']['max_latency_seconds']:.3f} |"
        )
    passed = summary["passed"]
    highest = summary["highest_passing_level"]
    conclusion = (
        f"All requested rounds passed through {highest} isolated workers while the API remained available."
        if passed
        else f"The highest fully passing isolated-worker level was {highest}; inspect the first failing level before integration."
    )
    return "\n".join(
        [
            "# CAD Annotation Process-Isolation Benchmark",
            "",
            f"- Generated: `{summary['generated_at']}`",
            f"- STEP fixture: `{summary['step_path']}`",
            f"- Candidate rules: `{summary['rule_count']}`",
            f"- Python: `{summary['python']}`",
            f"- Overall: `{'PASS' if passed else 'FAIL'}`",
            "",
            "This benchmark runs each OpenCASCADE projection and smart-annotation render in its own OS process. It does not change the production API or the legacy dimension engine.",
            "",
            "| Concurrent workers | Round | Successful jobs | Wall time (s) | Throughput (jobs/s) | API availability | Max API latency (s) |",
            "|---:|---:|---:|---:|---:|---:|---:|",
            *rows,
            "",
            "## Conclusion",
            "",
            conclusion,
            "",
            "Passing this controlled fixture proves crash containment and basic parallel execution, not the final user-capacity limit. Production rollout should begin below the measured ceiling and add a persistent queue, timeouts, cancellation, and larger-model soak tests.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=REPO_ROOT / "pyoccenv" / "python.exe")
    parser.add_argument("--step", type=Path, default=DEFAULT_STEP)
    parser.add_argument("--part-id", default="SHAFT-2AC085010H_1")
    parser.add_argument("--levels", nargs="+", type=int, default=[2, 3, 4])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--health-url", default="http://127.0.0.1:8000/api/health")
    parser.add_argument("--run-dir", type=Path, default=REPO_ROOT / "scratch" / "process_isolation_benchmark")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPO_ROOT / "docs" / "reports" / "process_isolation_2026-10-20",
    )
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.run_dir / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    if not args.python.is_file():
        raise FileNotFoundError(f"Python executable not found: {args.python}")
    if not args.step.is_file():
        raise FileNotFoundError(f"STEP fixture not found: {args.step}")

    initial_health = backend_health(args.health_url)
    if not initial_health["ok"]:
        raise RuntimeError(f"Backend health check failed before benchmark: {initial_health}")

    rules = prepare_rules(args.python, args.step, run_dir, args.part_id)
    results = []
    stop = False
    for level in args.levels:
        if stop:
            break
        for round_number in range(1, args.rounds + 1):
            result = run_level(
                python=args.python,
                step_path=args.step,
                rules=rules,
                level=level,
                round_number=round_number,
                run_dir=run_dir,
                health_url=args.health_url,
                part_id=args.part_id,
            )
            results.append(result)
            write_json(run_dir / f"level_{level}" / f"round_{round_number}" / "summary.json", result)
            if result["failures"] or result["backend_health"]["failures"]:
                stop = True
                break

    passing_levels = [
        level
        for level in args.levels
        if len([result for result in results if result["level"] == level]) == args.rounds
        and all(
            result["failures"] == 0 and result["backend_health"]["failures"] == 0
            for result in results
            if result["level"] == level
        )
    ]
    passed = len(passing_levels) == len(args.levels)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "highest_passing_level": max(passing_levels, default=0),
        "levels_requested": args.levels,
        "rounds_requested": args.rounds,
        "python": str(args.python.resolve()),
        "step_path": str(args.step.resolve()),
        "part_id": args.part_id,
        "rule_count": len(rules),
        "initial_backend_health": initial_health,
        "results": results,
        "raw_run_directory": str(run_dir.resolve()),
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.report_dir / "00_SUMMARY.json", summary)
    (args.report_dir / "00_SUMMARY.md").write_text(render_markdown(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
