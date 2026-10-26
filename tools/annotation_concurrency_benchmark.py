"""Concurrent smart-annotation API benchmark.

The scenario file contains real test accounts and the model/part each account
may access. Passwords are read only at runtime and are never copied into the
JSON or Markdown report.

Example scenario:
{
  "clients": [
    {
      "name": "engineer-1",
      "email_local": "loadtest-1",
      "email_domain": "forcecon.com.tw",
      "password": "...",
      "model_id": "example_batch",
      "part_id": "Part_1",
      "selection_limit": 3
    }
  ]
}
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
from dataclasses import asdict, dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener


@dataclass
class RequestResult:
    client: str
    round: int
    status_code: int
    outcome: str
    started_offset_seconds: float
    ended_offset_seconds: float
    latency_seconds: float
    cached: bool = False
    error: str | None = None


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(self, method: str, path: str, payload: Any | None = None, timeout: float = 300) -> tuple[int, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                content = response.read()
                return response.status, json.loads(content.decode("utf-8")) if content else {}
        except HTTPError as exc:
            content = exc.read()
            try:
                body = json.loads(content.decode("utf-8")) if content else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"detail": content.decode("utf-8", errors="replace")}
            return exc.code, body


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def max_overlap(results: list[RequestResult]) -> int:
    events: list[tuple[float, int]] = []
    for result in results:
        if result.outcome != "success":
            continue
        events.append((result.started_offset_seconds, 1))
        events.append((result.ended_offset_seconds, -1))
    active = maximum = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def prepare_client(base_url: str, config: dict[str, Any], timeout: float) -> tuple[ApiClient, list[dict[str, Any]]]:
    client = ApiClient(base_url)
    status, body = client.request("POST", "/api/auth/login", {
        "email_local": config["email_local"],
        "email_domain": config["email_domain"],
        "password": config["password"],
    }, timeout)
    if status != 200:
        raise RuntimeError(f"Login failed for {config.get('name') or config['email_local']}: HTTP {status}")
    model_id = quote(str(config["model_id"]), safe="")
    part_id = quote(str(config["part_id"]), safe="")
    status, body = client.request(
        "GET", f"/api/annotation/candidate-rules/{model_id}/{part_id}", timeout=timeout
    )
    if status != 200:
        raise RuntimeError(f"Candidate rules failed for {config.get('name')}: HTTP {status}")
    rules = list(body.get("rules") or [])
    limit = max(1, int(config.get("selection_limit") or len(rules) or 1))
    selected = rules[:limit]
    if not selected:
        raise RuntimeError(f"No candidate rules for {config.get('name') or config['email_local']}")
    return client, selected


def run_request(
    client: ApiClient,
    config: dict[str, Any],
    rules: list[dict[str, Any]],
    round_index: int,
    barrier: threading.Barrier,
    benchmark_started: float,
    timeout: float,
) -> RequestResult:
    name = str(config.get("name") or config["email_local"])
    barrier.wait(timeout=30)
    started = time.perf_counter()
    payload = {
        "model_id": config["model_id"],
        "part_id": config["part_id"],
        "feature_records": rules,
        "title_info": {
            "part_name": f"{config['part_id']} CONCURRENCY TEST",
            "drawing_no": f"LOAD-{uuid.uuid4().hex[:10]}",
            "benchmark_round": round_index,
        },
    }
    try:
        status, body = client.request("POST", "/api/annotation/render", payload, timeout)
        ended = time.perf_counter()
        detail = body.get("detail") if isinstance(body, dict) else None
        code = detail.get("code") if isinstance(detail, dict) else None
        if status == 200:
            outcome = "success"
        elif status == 409 and code == "ANNOTATION_ALREADY_RUNNING":
            outcome = "same_user_conflict"
        elif status == 429 and code == "ANNOTATION_CAPACITY_BUSY":
            outcome = "capacity_busy"
        else:
            outcome = "error"
        message = detail.get("message") if isinstance(detail, dict) else detail
        return RequestResult(
            client=name,
            round=round_index,
            status_code=status,
            outcome=outcome,
            started_offset_seconds=round(started - benchmark_started, 6),
            ended_offset_seconds=round(ended - benchmark_started, 6),
            latency_seconds=round(ended - started, 6),
            cached=bool(body.get("cached")) if isinstance(body, dict) else False,
            error=str(message) if outcome == "error" and message else None,
        )
    except Exception as exc:  # noqa: BLE001 - benchmark must report transport failures
        ended = time.perf_counter()
        return RequestResult(
            client=name,
            round=round_index,
            status_code=0,
            outcome="transport_error",
            started_offset_seconds=round(started - benchmark_started, 6),
            ended_offset_seconds=round(ended - benchmark_started, 6),
            latency_seconds=round(ended - started, 6),
            error=str(exc),
        )


def build_report(results: list[RequestResult], wall_seconds: float, client_count: int, rounds: int) -> dict[str, Any]:
    latencies = [item.latency_seconds for item in results]
    counts: dict[str, int] = {}
    for item in results:
        counts[item.outcome] = counts.get(item.outcome, 0) + 1
    successes = counts.get("success", 0)
    return {
        "schema_version": 1,
        "method": "CONCURRENT_ANNOTATION_API_BENCHMARK_V1",
        "client_count": client_count,
        "rounds": rounds,
        "request_count": len(results),
        "wall_seconds": round(wall_seconds, 6),
        "successful_requests_per_second": round(successes / wall_seconds, 4) if wall_seconds else 0.0,
        "maximum_overlapping_successful_requests": max_overlap(results),
        "outcomes": counts,
        "latency_seconds": {
            "minimum": min(latencies) if latencies else 0.0,
            "mean": round(statistics.fmean(latencies), 6) if latencies else 0.0,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "maximum": max(latencies) if latencies else 0.0,
        },
        "results": [asdict(item) for item in results],
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    outcomes = report["outcomes"]
    latency = report["latency_seconds"]
    lines = [
        "# Smart Annotation Concurrency Benchmark",
        "",
        f"- Clients: {report['client_count']}",
        f"- Rounds: {report['rounds']}",
        f"- Requests: {report['request_count']}",
        f"- Wall time: {report['wall_seconds']} s",
        f"- Successful requests/s: {report['successful_requests_per_second']}",
        f"- Maximum overlapping successful requests: {report['maximum_overlapping_successful_requests']}",
        f"- Success: {outcomes.get('success', 0)}",
        f"- Same-user conflicts: {outcomes.get('same_user_conflict', 0)}",
        f"- Capacity busy: {outcomes.get('capacity_busy', 0)}",
        f"- Errors: {outcomes.get('error', 0) + outcomes.get('transport_error', 0)}",
        f"- Latency p50 / p95 / max: {latency['p50']} / {latency['p95']} / {latency['maximum']} s",
        "",
        "## Request details",
        "",
        "| Client | Round | HTTP | Outcome | Latency (s) | Cached |",
        "|---|---:|---:|---|---:|---|",
    ]
    for item in report["results"]:
        lines.append(
            f"| {item['client']} | {item['round']} | {item['status_code']} | "
            f"{item['outcome']} | {item['latency_seconds']} | {item['cached']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark concurrent smart-annotation API requests")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument(
        "--scenario-env",
        default="CAD_ANNOTATION_BENCHMARK_SCENARIO",
        help="Environment variable containing scenario JSON when --scenario is omitted",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.scenario:
        scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    else:
        raw_scenario = os.environ.get(args.scenario_env, "")
        if not raw_scenario:
            parser.error(f"Provide --scenario or set {args.scenario_env}")
        scenario = json.loads(raw_scenario)
    configs = list(scenario.get("clients") or [])
    if not configs:
        parser.error("Scenario must contain at least one client")
    prepared = [prepare_client(args.base_url, config, args.timeout) for config in configs]
    tasks = [(config, client, rules, round_index)
             for round_index in range(1, max(1, args.rounds) + 1)
             for config, (client, rules) in zip(configs, prepared)]
    barrier = threading.Barrier(len(tasks))
    benchmark_started = time.perf_counter()
    results: list[RequestResult] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [executor.submit(
            run_request, client, config, rules, round_index,
            barrier, benchmark_started, args.timeout,
        ) for config, client, rules, round_index in tasks]
        for future in as_completed(futures):
            results.append(future.result())
    wall_seconds = time.perf_counter() - benchmark_started
    results.sort(key=lambda item: (item.started_offset_seconds, item.client, item.round))
    report = build_report(results, wall_seconds, len(configs), max(1, args.rounds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, args.output.with_suffix(".md"))
    print(json.dumps({key: report[key] for key in (
        "client_count", "request_count", "wall_seconds", "outcomes",
        "maximum_overlapping_successful_requests", "successful_requests_per_second",
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
