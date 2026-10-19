"""End-to-end authenticated concurrency test for the CAD web API.

Credentials are supplied through CAD_SYSTEM_STRESS_SCENARIO and are never
written to the output report. The test covers light API traffic, annotation
burst/backpressure, annotation retry throughput, and optional analysis queueing.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener


@dataclass
class HttpResult:
    status: int
    body: Any
    elapsed: float


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 600,
    ) -> HttpResult:
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {**(headers or {}), "Content-Type": "application/json"}
        request = Request(f"{self.base_url}{path}", data=data, method=method, headers=headers or {})
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=timeout) as response:
                content = response.read()
                body = json.loads(content.decode("utf-8")) if content else {}
                return HttpResult(response.status, body, time.perf_counter() - started)
        except HTTPError as exc:
            content = exc.read()
            try:
                body = json.loads(content.decode("utf-8")) if content else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"detail": content.decode("utf-8", errors="replace")}
            return HttpResult(exc.code, body, time.perf_counter() - started)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 6)


def latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "minimum": round(min(values), 6) if values else 0.0,
        "mean": round(statistics.fmean(values), 6) if values else 0.0,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "maximum": round(max(values), 6) if values else 0.0,
    }


def login_and_rules(base_url: str, config: dict[str, Any], timeout: float) -> tuple[ApiClient, list[dict[str, Any]]]:
    client = ApiClient(base_url)
    login = client.request("POST", "/api/auth/login", {
        "email_local": config["email_local"],
        "email_domain": config["email_domain"],
        "password": config["password"],
    }, timeout=timeout)
    if login.status != 200:
        raise RuntimeError(f"Login failed for {config['name']}: HTTP {login.status}")
    model_id = quote(str(config["model_id"]), safe="")
    part_id = quote(str(config["part_id"]), safe="")
    candidates = client.request(
        "GET", f"/api/annotation/candidate-rules/{model_id}/{part_id}", timeout=timeout
    )
    if candidates.status != 200:
        raise RuntimeError(f"Candidate rules failed for {config['name']}: HTTP {candidates.status}")
    rules = list(candidates.body.get("rules") or [])
    selected = rules[:max(1, int(config.get("selection_limit") or 3))]
    if not selected:
        raise RuntimeError(f"No candidate rules for {config['name']}")
    return client, selected


def annotation_payload(config: dict[str, Any], rules: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    return {
        "model_id": config["model_id"],
        "part_id": config["part_id"],
        "feature_records": rules,
        "title_info": {
            "part_name": f"{config['part_id']} STRESS TEST",
            "drawing_no": f"STRESS-{phase}-{config['name']}-{uuid.uuid4().hex[:8]}",
        },
    }


def classify_annotation(result: HttpResult) -> str:
    detail = result.body.get("detail") if isinstance(result.body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    if result.status == 200:
        return "success"
    if result.status == 409 and code == "ANNOTATION_ALREADY_RUNNING":
        return "same_user_conflict"
    if result.status == 429 and code == "ANNOTATION_CAPACITY_BUSY":
        return "capacity_busy"
    return "error"


def run_light_load(
    prepared: list[tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]],
    requests_per_client: int,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()

    def worker(item: tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]) -> list[HttpResult]:
        _, client, _ = item
        return [client.request("GET", "/api/models", timeout=timeout) for _ in range(requests_per_client)]

    results: list[HttpResult] = []
    with ThreadPoolExecutor(max_workers=len(prepared)) as executor:
        for future in as_completed([executor.submit(worker, item) for item in prepared]):
            results.extend(future.result())
    wall = time.perf_counter() - started
    success = sum(item.status == 200 for item in results)
    return {
        "clients": len(prepared),
        "requests_per_client": requests_per_client,
        "request_count": len(results),
        "success": success,
        "errors": len(results) - success,
        "wall_seconds": round(wall, 6),
        "requests_per_second": round(len(results) / wall, 4) if wall else 0.0,
        "latency_seconds": latency_summary([item.elapsed for item in results]),
        "status_counts": {
            str(code): sum(item.status == code for item in results)
            for code in sorted({item.status for item in results})
        },
    }


def run_annotation_burst(
    prepared: list[tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]], timeout: float
) -> dict[str, Any]:
    barrier = threading.Barrier(len(prepared))
    started = time.perf_counter()

    def worker(item: tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]) -> dict[str, Any]:
        config, client, rules = item
        payload = annotation_payload(config, rules, "BURST")
        barrier.wait(timeout=30)
        result = client.request("POST", "/api/annotation/render", payload, timeout=timeout)
        return {
            "client": config["name"],
            "status": result.status,
            "outcome": classify_annotation(result),
            "elapsed": round(result.elapsed, 6),
        }

    with ThreadPoolExecutor(max_workers=len(prepared)) as executor:
        results = [future.result() for future in as_completed([
            executor.submit(worker, item) for item in prepared
        ])]
    wall = time.perf_counter() - started
    outcomes = {
        name: sum(item["outcome"] == name for item in results)
        for name in sorted({item["outcome"] for item in results})
    }
    return {
        "clients": len(prepared),
        "wall_seconds": round(wall, 6),
        "outcomes": outcomes,
        "latency_seconds": latency_summary([item["elapsed"] for item in results]),
        "results": sorted(results, key=lambda item: item["client"]),
    }


def run_annotation_retry(
    prepared: list[tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]],
    timeout: float,
    retry_interval: float,
) -> dict[str, Any]:
    barrier = threading.Barrier(len(prepared))
    started = time.perf_counter()

    def worker(item: tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]) -> dict[str, Any]:
        config, client, rules = item
        payload = annotation_payload(config, rules, "RETRY")
        barrier.wait(timeout=30)
        attempts = 0
        busy = 0
        client_started = time.perf_counter()
        deadline = client_started + timeout
        while time.perf_counter() < deadline:
            attempts += 1
            result = client.request("POST", "/api/annotation/render", payload, timeout=timeout)
            outcome = classify_annotation(result)
            if outcome == "success":
                return {
                    "client": config["name"],
                    "outcome": outcome,
                    "attempts": attempts,
                    "busy_responses": busy,
                    "completion_seconds": round(time.perf_counter() - client_started, 6),
                }
            if outcome != "capacity_busy":
                return {
                    "client": config["name"],
                    "outcome": outcome,
                    "attempts": attempts,
                    "busy_responses": busy,
                    "completion_seconds": round(time.perf_counter() - client_started, 6),
                }
            busy += 1
            time.sleep(retry_interval)
        return {
            "client": config["name"],
            "outcome": "timeout",
            "attempts": attempts,
            "busy_responses": busy,
            "completion_seconds": round(time.perf_counter() - client_started, 6),
        }

    with ThreadPoolExecutor(max_workers=len(prepared)) as executor:
        results = [future.result() for future in as_completed([
            executor.submit(worker, item) for item in prepared
        ])]
    wall = time.perf_counter() - started
    outcomes = {
        name: sum(item["outcome"] == name for item in results)
        for name in sorted({item["outcome"] for item in results})
    }
    return {
        "clients": len(prepared),
        "retry_interval_seconds": retry_interval,
        "wall_seconds": round(wall, 6),
        "completed_per_second": round(outcomes.get("success", 0) / wall, 4) if wall else 0.0,
        "outcomes": outcomes,
        "completion_seconds": latency_summary([item["completion_seconds"] for item in results]),
        "total_attempts": sum(item["attempts"] for item in results),
        "total_busy_responses": sum(item["busy_responses"] for item in results),
        "results": sorted(results, key=lambda item: item["client"]),
    }


def multipart_file(path: Path) -> tuple[bytes, str]:
    boundary = f"----cad-stress-{uuid.uuid4().hex}"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    return prefix + path.read_bytes() + suffix, boundary


def run_analysis_queue(
    prepared: list[tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]],
    step_file: Path,
    analysis_clients: int,
    timeout: float,
) -> dict[str, Any]:
    selected = prepared[:min(max(1, analysis_clients), len(prepared))]
    data, boundary = multipart_file(step_file)
    barrier = threading.Barrier(len(selected))
    started = time.perf_counter()

    def worker(item: tuple[dict[str, Any], ApiClient, list[dict[str, Any]]]) -> dict[str, Any]:
        config, client, _ = item
        barrier.wait(timeout=30)
        upload = client.request(
            "POST", "/api/upload", data=data,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=timeout,
        )
        if upload.status != 200:
            return {
                "client": config["name"], "upload_status": upload.status,
                "outcome": "upload_error", "elapsed": round(upload.elapsed, 6),
            }
        job_id = str(upload.body["job_id"])
        initial_queue = int(upload.body.get("queue_position") or 0)
        history: list[str] = []
        deadline = time.perf_counter() + timeout
        worker_limit = None
        while time.perf_counter() < deadline:
            status = client.request("GET", f"/api/status/{quote(job_id, safe='')}", timeout=30)
            if status.status != 200:
                return {
                    "client": config["name"], "upload_status": upload.status,
                    "outcome": "status_error", "status_code": status.status,
                    "job_id": job_id, "initial_queue_position": initial_queue,
                }
            state = str(status.body.get("status"))
            worker_limit = status.body.get("worker_limit")
            if not history or history[-1] != state:
                history.append(state)
            if state in {"completed", "error"}:
                return {
                    "client": config["name"], "upload_status": upload.status,
                    "outcome": state, "job_id": job_id,
                    "initial_queue_position": initial_queue,
                    "worker_limit": worker_limit, "state_history": history,
                    "completion_seconds": round(time.perf_counter() - started, 6),
                    "final_message": status.body.get("message"),
                }
            time.sleep(1.0)
        return {
            "client": config["name"], "upload_status": upload.status,
            "outcome": "timeout", "job_id": job_id,
            "initial_queue_position": initial_queue,
            "worker_limit": worker_limit, "state_history": history,
        }

    with ThreadPoolExecutor(max_workers=len(selected)) as executor:
        results = [future.result() for future in as_completed([
            executor.submit(worker, item) for item in selected
        ])]
    wall = time.perf_counter() - started
    outcomes = {
        name: sum(item["outcome"] == name for item in results)
        for name in sorted({item["outcome"] for item in results})
    }
    return {
        "clients": len(selected),
        "step_filename": step_file.name,
        "step_bytes": step_file.stat().st_size,
        "wall_seconds": round(wall, 6),
        "outcomes": outcomes,
        "results": sorted(results, key=lambda item: item["client"]),
    }


class HealthMonitor:
    def __init__(self, base_url: str, interval: float = 0.25):
        self.client = ApiClient(base_url)
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: list[HttpResult] = []
        self.thread = threading.Thread(target=self._run, name="health-monitor", daemon=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.samples.append(self.client.request("GET", "/api/health", timeout=5))
            except Exception:  # noqa: BLE001 - transport failures are represented as status 0
                self.samples.append(HttpResult(0, {}, 5.0))
            self.stop_event.wait(self.interval)

    def start(self) -> None:
        self.thread.start()

    def finish(self) -> dict[str, Any]:
        self.stop_event.set()
        self.thread.join(timeout=10)
        ok = sum(sample.status == 200 for sample in self.samples)
        return {
            "sample_count": len(self.samples),
            "success": ok,
            "failures": len(self.samples) - ok,
            "availability_percent": round(100 * ok / len(self.samples), 3) if self.samples else 0.0,
            "latency_seconds": latency_summary([sample.elapsed for sample in self.samples]),
        }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    light = report["light_api"]
    burst = report["annotation_burst"]
    retry = report["annotation_retry"]
    health = report["health_during_heavy_load"]
    analysis = report.get("analysis_queue")
    lines = [
        "# CAD Web System Concurrency Stress Report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Executive summary",
        "",
        f"- Authenticated test users: {report['client_count']}",
        f"- Light API: {light['success']}/{light['request_count']} successful; "
        f"{light['requests_per_second']} requests/s; p95 {light['latency_seconds']['p95']} s.",
        f"- Annotation burst: {burst['outcomes']}.",
        f"- Annotation retry-to-completion: {retry['outcomes']}; "
        f"{retry['wall_seconds']} s wall time; {retry['completed_per_second']} completed/s.",
        f"- Health during heavy load: {health['success']}/{health['sample_count']} successful "
        f"({health['availability_percent']}%); p95 {health['latency_seconds']['p95']} s.",
    ]
    if analysis:
        lines.append(
            f"- Analysis queue: {analysis['outcomes']} for {analysis['clients']} uploads; "
            f"{analysis['wall_seconds']} s wall time."
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- The user count is not capped by the heavy-worker count. Light authenticated requests are measured separately.",
        "- Annotation generation currently uses immediate backpressure: one request runs and excess requests receive HTTP 429, then clients must retry.",
        "- Analysis uploads use a server queue and remain observable through queued, processing, finalizing, completed, or error states.",
        "- This workstation result is a point-in-time safety/throughput measurement, not a production SLA.",
        "",
        "## Raw phase metrics",
        "",
        "```json",
        json.dumps({
            "light_api": light,
            "annotation_burst": burst,
            "annotation_retry": retry,
            "analysis_queue": analysis,
            "health_during_heavy_load": health,
        }, ensure_ascii=False, indent=2),
        "```",
        "",
        "Passwords and session cookies are intentionally excluded from this report.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the authenticated CAD web system stress test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario-env", default="CAD_SYSTEM_STRESS_SCENARIO")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--light-requests", type=int, default=20)
    parser.add_argument("--retry-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--step-file", type=Path)
    parser.add_argument("--analysis-clients", type=int, default=2)
    args = parser.parse_args()

    raw = os.environ.get(args.scenario_env, "")
    if not raw:
        parser.error(f"Set {args.scenario_env} to scenario JSON")
    configs = list(json.loads(raw).get("clients") or [])
    if not configs:
        parser.error("Scenario must contain clients")
    if args.step_file and not args.step_file.is_file():
        parser.error(f"STEP file does not exist: {args.step_file}")

    prepared = [
        (config, *login_and_rules(args.base_url, config, args.timeout))
        for config in configs
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "method": "CAD_WEB_SYSTEM_STRESS_V1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": args.base_url,
        "client_count": len(configs),
        "light_api": run_light_load(prepared, max(1, args.light_requests), args.timeout),
    }
    monitor = HealthMonitor(args.base_url)
    monitor.start()
    try:
        report["annotation_burst"] = run_annotation_burst(prepared, args.timeout)
        report["annotation_retry"] = run_annotation_retry(
            prepared, args.timeout, max(0.05, args.retry_interval)
        )
        if args.step_file:
            report["analysis_queue"] = run_analysis_queue(
                prepared, args.step_file, args.analysis_clients, args.timeout
            )
    finally:
        report["health_during_heavy_load"] = monitor.finish()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, args.output.with_suffix(".md"))
    print(json.dumps({
        "client_count": report["client_count"],
        "light_api": report["light_api"],
        "annotation_burst": report["annotation_burst"]["outcomes"],
        "annotation_retry": report["annotation_retry"]["outcomes"],
        "analysis_queue": (report.get("analysis_queue") or {}).get("outcomes"),
        "health": report["health_during_heavy_load"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
