"""Build and run the leakage-safe tolerance recommendation benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.benchmark import (
    BenchmarkDataset,
    ToleranceBenchmarkRunner,
    build_silver_dataset,
    save_report,
)
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase


DEFAULT_CASE_DB = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "data" / "feature_case_base.json"
DEFAULT_DATASET = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "benchmark_data" / "silver_verified_cases.json"
DEFAULT_REPORT = PROJECT_ROOT / "auto_2d_drawing" / "output" / "benchmarks" / "tolerance_benchmark_latest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and run a leakage-safe benchmark for the tolerance pipeline."
    )
    parser.add_argument("--case-db", type=Path, default=DEFAULT_CASE_DB)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--mode",
        choices=("build", "run", "all"),
        default="all",
        help="build the silver dataset, run an existing dataset, or do both",
    )
    parser.add_argument(
        "--skip-source-replay",
        action="store_true",
        help="skip reopening source DXFs; retrieval and recommendation are still evaluated",
    )
    parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="return exit code 2 when a leakage, source-replay or safety gate fails",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    case_base = FeatureCaseBase(str(args.case_db))

    if args.mode in {"build", "all"}:
        dataset = build_silver_dataset(case_base.cases)
        dataset.save(args.dataset)
        print(f"dataset={args.dataset}")
        print(f"records={len(dataset.records)}")
        if args.mode == "build":
            return 0
    else:
        dataset = BenchmarkDataset.load(args.dataset)

    runner = ToleranceBenchmarkRunner(case_base.cases, dataset)
    report = runner.run(replay_sources=not args.skip_source_replay)
    save_report(report, args.report)
    summary = {
        "report": str(args.report),
        "records": report["dataset"]["record_count"],
        "leakage_audit_passed": report["leakage_audit"]["passed"],
        "extraction": report["extraction"],
        "feature_linkage_2d": {
            key: value for key, value in report["feature_linkage_2d"].items() if key != "rows"
        },
        "retrieval": report["retrieval"],
        "recommendation": report["recommendation"],
        "quality_gates": report["quality_gates"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_gate and not report["quality_gates"]["passed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
