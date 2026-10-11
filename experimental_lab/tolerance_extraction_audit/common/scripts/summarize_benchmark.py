"""Create a stable, compact metric summary and baseline delta."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


METRICS = {
    "record_count": ("dataset", "record_count"),
    "entity_found_rate": ("extraction", "entity_found_rate"),
    "tolerance_exact_accuracy": ("extraction", "tolerance_exact_accuracy"),
    "feature_linkage_coverage": ("feature_linkage_2d", "coverage"),
    "feature_linkage_accuracy": ("feature_linkage_2d", "feature_type_accuracy"),
    "feature_auto_decision_rate": ("feature_linkage_2d", "auto_decision_rate"),
    "retrieval_eligible_rate": ("feature_linkage_2d", "retrieval_eligible_rate"),
    "retrieval_evaluable_coverage": ("retrieval", "evaluable_coverage"),
    "retrieval_mrr": ("retrieval", "mrr"),
    "retrieval_recall_at_1": ("retrieval", "recall_at_1"),
    "retrieval_recall_at_3": ("retrieval", "recall_at_3"),
    "recommendation_exact_accuracy": ("recommendation", "exact_tolerance_accuracy"),
    "recommendation_abstention_rate": ("recommendation", "abstention_rate"),
    "recommendation_non_abstained_coverage": ("recommendation", "non_abstained_coverage"),
}


def read_metric(payload, path):
    value = payload
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8-sig")) if args.baseline and args.baseline.exists() else None
    current = {name: read_metric(report, path) for name, path in METRICS.items()}
    previous = {name: read_metric(baseline, path) for name, path in METRICS.items()} if baseline else {}
    delta = {}
    for name, value in current.items():
        old = previous.get(name)
        delta[name] = round(value - old, 6) if isinstance(value, (int, float)) and isinstance(old, (int, float)) else None

    summary = {
        "report": str(args.report),
        "baseline": str(args.baseline) if args.baseline else None,
        "label_policy": report.get("dataset", {}).get("label_policy"),
        "current": current,
        "baseline_metrics": previous,
        "delta": delta,
        "quality_gates": report.get("quality_gates"),
        "limitations": report.get("limitations", []),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "benchmark_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Benchmark Summary", "",
        f"- Report: `{args.report}`",
        f"- Baseline: `{args.baseline}`" if args.baseline else "- Baseline: none",
        f"- Quality gates: **{'PASS' if report.get('quality_gates', {}).get('passed') else 'FAIL'}**", "",
        "| Metric | Current | Baseline | Delta |", "|---|---:|---:|---:|",
    ]
    for name in METRICS:
        lines.append(f"| `{name}` | {current.get(name)} | {previous.get(name)} | {delta.get(name)} |")
    lines.extend(["", "## Interpretation boundary", ""])
    lines.extend(f"- {item}" for item in report.get("limitations", []))
    (args.output_dir / "benchmark_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
