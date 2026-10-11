"""Extract explicit tolerances from every drawing in configured database families.

This is an experimental staging export.  It deliberately does not write the
production case base and does not claim a 3D feature link merely because a
drawing was found in the same product-family directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.ingest_historical_data import (  # noqa: E402
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)
try:  # package import in tests, direct script import in production runs
    from .timed_process_runner import iter_timed_process_results  # type: ignore
except ImportError:
    from timed_process_runner import iter_timed_process_results  # noqa: E402


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
BLUE = "#60a5fa"
GREEN = "#22c55e"
AMBER = "#facc15"
RED = "#f87171"


def _font(size: int):
    for path in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _normalised_identity(path: Path) -> str:
    identity, _revision, _stem = HistoricalDataIngestor._revision_identity(path.name)
    return identity


def build_manifest(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    source_root = Path(config["source_root"])
    dxf_root = Path(config["dxf_root"])
    dxf_by_stem: dict[str, list[Path]] = {}
    for path in dxf_root.rglob("*.dxf"):
        dxf_by_stem.setdefault(path.stem.casefold(), []).append(path)

    manifest: list[dict[str, Any]] = []
    missing = 0
    ambiguous = 0
    for family in config.get("families") or []:
        family_root = source_root / family
        step_files = sorted(
            (path for path in family_root.rglob("*") if path.is_file() and path.suffix.casefold() in {".stp", ".step"}),
            key=lambda path: str(path).casefold(),
        )
        step_identities = {_normalised_identity(path) for path in step_files}
        drawings = sorted(family_root.rglob("*.dwg"), key=lambda path: str(path).casefold())
        for drawing in drawings:
            candidates = dxf_by_stem.get(drawing.stem.casefold(), [])
            status = "READY"
            dxf_path = None
            if not candidates:
                status = "MISSING_DXF"
                missing += 1
            elif len(candidates) > 1:
                status = "AMBIGUOUS_DXF"
                ambiguous += 1
            else:
                dxf_path = candidates[0]
            drawing_identity = _normalised_identity(drawing)
            manifest.append({
                "family": family,
                "source_dwg": str(drawing),
                "source_relative_path": str(drawing.relative_to(family_root)),
                "drawing": drawing.with_suffix(".dxf").name,
                "dxf_path": str(dxf_path) if dxf_path else None,
                "status": status,
                "step_pair_hint": "EXACT_DOCUMENT_IDENTITY" if drawing_identity in step_identities else "UNPAIRED_2D_ONLY",
                "feature_link_status": "PENDING_3D_LINK",
            })
    return manifest, {
        "requested_drawing_count": len(manifest),
        "missing_dxf_count": missing,
        "ambiguous_dxf_count": ambiguous,
    }


def _serialise_dimension(item: ExtractedDimension) -> dict[str, Any]:
    payload = item.to_dict()
    payload["extraction_status"] = "EXTRACTED_2D"
    payload["feature_link_status"] = "PENDING_3D_LINK"
    return payload


def _consume_result(row: dict[str, Any], payloads: list[dict[str, Any]]) -> dict[str, Any]:
    raw = [ExtractedDimension(**payload) for payload in payloads]
    deduplicated, suppressed = HistoricalDataIngestor._deduplicate_dimensions(raw)
    explicit = [item for item in deduplicated if HistoricalDataIngestor._tolerance_is_plausible(item)]
    result = dict(row)
    result.update({
        "status": "EXTRACTED",
        "raw_dimension_count": len(raw),
        "deduplicated_dimension_count": len(deduplicated),
        "suppressed_duplicate_count": int(suppressed),
        "explicit_tolerance_count": len(explicit),
        "dimensions": [_serialise_dimension(item) for item in explicit],
    })
    return result


def extract_all(
    manifest: list[dict[str, Any]],
    workers: int,
    drawing_timeout_seconds: float = 300.0,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any] | None] = [None] * len(manifest)
    ready = [(index, row) for index, row in enumerate(manifest) if row["status"] == "READY"]
    for index, row in enumerate(manifest):
        if row["status"] != "READY":
            results[index] = {**row, "explicit_tolerance_count": 0, "dimensions": []}

    row_by_task = {f"drawing-{index:06d}": (index, row) for index, row in ready}
    tasks = [
        (task_id, (row["dxf_path"],))
        for task_id, (_index, row) in row_by_task.items()
    ]
    completed = 0
    for timed in iter_timed_process_results(
        _extract_dxf_worker,
        tasks,
        timeout_seconds=drawing_timeout_seconds,
        workers=workers,
    ):
        completed += 1
        index, row = row_by_task[timed.task_id]
        if timed.status == "COMPLETED":
            try:
                _source, payloads = timed.value
                results[index] = _consume_result(row, payloads)
                results[index]["elapsed_seconds"] = timed.elapsed_seconds
            except Exception as exc:
                results[index] = {
                    **row,
                    "status": "EXTRACTION_FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "explicit_tolerance_count": 0,
                    "dimensions": [],
                }
        else:
            results[index] = {
                **row,
                "status": "EXTRACTION_TIMEOUT" if timed.status == "TIMEOUT" else "EXTRACTION_FAILED",
                "error": timed.error,
                "elapsed_seconds": timed.elapsed_seconds,
                "explicit_tolerance_count": 0,
                "dimensions": [],
            }
        print(
            f"DXF extraction {completed}/{len(ready)}: {row['drawing']} "
            f"[{results[index]['status']}] {timed.elapsed_seconds:.2f}s",
            flush=True,
        )
    return [row for row in results if row is not None]


def _family_summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    families: list[str] = []
    for row in results:
        if row["family"] not in families:
            families.append(row["family"])
    summaries = []
    for family in families:
        rows = [row for row in results if row["family"] == family]
        categories = Counter(
            dimension.get("dimension_category", "UNKNOWN")
            for row in rows
            for dimension in row.get("dimensions") or []
        )
        summaries.append({
            "family": family,
            "drawing_count": len(rows),
            "processed_drawing_count": sum(row.get("status") == "EXTRACTED" for row in rows),
            "drawings_with_explicit_tolerance": sum(row.get("explicit_tolerance_count", 0) > 0 for row in rows),
            "drawings_without_explicit_tolerance": sum(
                row.get("status") == "EXTRACTED" and row.get("explicit_tolerance_count", 0) == 0 for row in rows
            ),
            "raw_dimension_count": sum(row.get("raw_dimension_count", 0) for row in rows),
            "explicit_tolerance_count": sum(row.get("explicit_tolerance_count", 0) for row in rows),
            "step_identity_hint_count": sum(row.get("step_pair_hint") == "EXACT_DOCUMENT_IDENTITY" for row in rows),
            "dimension_categories": dict(sorted(categories.items())),
        })
    return summaries


def _mark_canonical_occurrences(results: list[dict[str, Any]]) -> None:
    """Keep shared drawings auditable without learning the same DXF twice."""
    first_occurrence: dict[str, str] = {}
    for index, row in enumerate(results, start=1):
        occurrence_id = f"drawing-occurrence-{index:04d}"
        row["occurrence_id"] = occurrence_id
        key = str(row.get("dxf_path") or row.get("source_dwg") or "").casefold()
        if key not in first_occurrence:
            first_occurrence[key] = occurrence_id
            row["is_canonical_drawing_occurrence"] = True
            row["duplicate_of_occurrence_id"] = None
        else:
            row["is_canonical_drawing_occurrence"] = False
            row["duplicate_of_occurrence_id"] = first_occurrence[key]


def render(report: dict[str, Any], output: Path) -> None:
    rows = report["families"]
    width = 2600
    row_height = 135
    height = 190 + len(rows) * row_height
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    summary = report["summary"]
    draw.text((30, 24), "DATABASE TOLERANCE EXTRACTION | FULL 10-FAMILY STAGING", font=_font(32), fill=WHITE)
    draw.text(
        (30, 72),
        f"drawings {summary['processed_drawing_count']}/{summary['requested_drawing_count']} | "
        f"unique drawings {summary['unique_drawing_count']} | canonical tolerances "
        f"{summary['canonical_staging_tolerance_count']} | failures {summary['failed_drawing_count']}",
        font=_font(23), fill=BLUE,
    )
    draw.text(
        (30, 116),
        "All values are extracted from native DXF dimensions. 3D feature linkage remains a separate verification stage.",
        font=_font(18), fill=MUTED,
    )
    y = 175
    for index, row in enumerate(rows):
        if index % 2 == 0:
            draw.rectangle((18, y, width - 18, y + row_height - 5), fill=PANEL)
        color = GREEN if row["processed_drawing_count"] == row["drawing_count"] else RED
        draw.text((32, y + 12), row["family"], font=_font(21), fill=WHITE)
        draw.text(
            (970, y + 12),
            f"drawings {row['processed_drawing_count']}/{row['drawing_count']} | with tolerance "
            f"{row['drawings_with_explicit_tolerance']} | empty {row['drawings_without_explicit_tolerance']}",
            font=_font(19), fill=color,
        )
        draw.text(
            (32, y + 64),
            f"raw dimensions {row['raw_dimension_count']} | explicit tolerances {row['explicit_tolerance_count']} | "
            f"exact STEP identity hints {row['step_identity_hint_count']} | categories {row['dimension_categories']}",
            font=_font(17), fill=AMBER,
        )
        y += row_height
    image.save(output)


def write_canonical_csv(results: list[dict[str, Any]], output: Path) -> None:
    fields = [
        "family", "drawing", "dxf_path", "entity_handle", "raw_text",
        "nominal_value", "dimension_category", "tolerance_mode", "upper_dev",
        "lower_dev", "symmetric_dev", "fit_class", "feature_link_status",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for drawing in results:
            if not drawing.get("is_canonical_drawing_occurrence"):
                continue
            for dimension in drawing.get("dimensions") or []:
                tolerance = dimension.get("tolerance_config") or {}
                writer.writerow({
                    "family": drawing["family"],
                    "drawing": drawing["drawing"],
                    "dxf_path": drawing["dxf_path"],
                    "entity_handle": dimension.get("entity_handle"),
                    "raw_text": dimension.get("raw_text"),
                    "nominal_value": dimension.get("nominal_value"),
                    "dimension_category": dimension.get("dimension_category"),
                    "tolerance_mode": tolerance.get("mode"),
                    "upper_dev": tolerance.get("upper_dev"),
                    "lower_dev": tolerance.get("lower_dev"),
                    "symmetric_dev": tolerance.get("dev"),
                    "fit_class": tolerance.get("fit_class"),
                    "feature_link_status": dimension.get("feature_link_status"),
                })


def run(
    config_path: Path,
    output_dir: Path,
    workers: int,
    drawing_timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest, inventory = build_manifest(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "database_tolerance_full_manifest.json").write_text(
        json.dumps({"schema_version": 1, "drawings": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    results = extract_all(manifest, workers, drawing_timeout_seconds)
    _mark_canonical_occurrences(results)
    canonical_results = [row for row in results if row["is_canonical_drawing_occurrence"]]
    family_summaries = _family_summaries(results)
    summary = {
        **inventory,
        "processed_drawing_count": sum(row.get("status") == "EXTRACTED" for row in results),
        "failed_drawing_count": sum(row.get("status") == "EXTRACTION_FAILED" for row in results),
        "timed_out_drawing_count": sum(row.get("status") == "EXTRACTION_TIMEOUT" for row in results),
        "drawings_with_explicit_tolerance": sum(row.get("explicit_tolerance_count", 0) > 0 for row in results),
        "drawings_without_explicit_tolerance": sum(
            row.get("status") == "EXTRACTED" and row.get("explicit_tolerance_count", 0) == 0 for row in results
        ),
        "raw_dimension_count": sum(row.get("raw_dimension_count", 0) for row in results),
        "deduplicated_dimension_count": sum(row.get("deduplicated_dimension_count", 0) for row in results),
        "suppressed_duplicate_count": sum(row.get("suppressed_duplicate_count", 0) for row in results),
        "explicit_tolerance_count": sum(row.get("explicit_tolerance_count", 0) for row in results),
        "unique_drawing_count": len(canonical_results),
        "shared_drawing_duplicate_occurrence_count": len(results) - len(canonical_results),
        "unique_drawings_with_explicit_tolerance": sum(
            row.get("explicit_tolerance_count", 0) > 0 for row in canonical_results
        ),
        "canonical_staging_tolerance_count": sum(
            row.get("explicit_tolerance_count", 0) for row in canonical_results
        ),
    }
    report = {
        "schema_version": 1,
        "method": "DATABASE_FULL_NATIVE_DXF_TOLERANCE_EXTRACTION_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "scope_note": "Complete 2D tolerance-value staging extraction for all drawings in the configured families; 3D feature linkage is pending.",
        "summary": summary,
        "families": family_summaries,
        "drawings": results,
        "review_queue": [
            {
                "occurrence_id": row["occurrence_id"],
                "family": row["family"],
                "drawing": row["drawing"],
                "dxf_path": row["dxf_path"],
                "reason": "NO_PLAUSIBLE_NATIVE_EXPLICIT_TOLERANCE",
                "raw_dimension_count": row.get("raw_dimension_count", 0),
            }
            for row in canonical_results
            if row.get("status") == "EXTRACTED" and row.get("explicit_tolerance_count", 0) == 0
        ],
    }
    (output_dir / "database_tolerance_full_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_canonical_csv(results, output_dir / "database_tolerance_canonical_staging.csv")
    render(report, output_dir / "database_tolerance_full_overview.png")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--drawing-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    report = run(
        args.config.resolve(), args.output_dir.resolve(), args.workers,
        args.drawing_timeout_seconds,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if (
        report["summary"]["failed_drawing_count"]
        or report["summary"]["timed_out_drawing_count"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
