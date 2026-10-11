"""Extract explicit tolerances from a small, family-stratified DXF pilot set."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.ingest_historical_data import (  # noqa: E402
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
BLUE = "#60a5fa"
GREEN = "#22c55e"
AMBER = "#facc15"


def _font(size: int):
    for path in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _format_tolerance(row: dict) -> str:
    nominal = float(row.get("nominal_value") or 0.0)
    cfg = row.get("tolerance_config") or {}
    prefix = "DIA " if str(row.get("dimension_category", "")).upper() == "DIAMETER" else ""
    mode = str(cfg.get("mode") or "")
    if "dev" in cfg:
        return f"{prefix}{nominal:g} +/-{float(cfg['dev']):g}"
    if "upper_dev" in cfg or "lower_dev" in cfg:
        return f"{prefix}{nominal:g} +{float(cfg.get('upper_dev') or 0):g}/{float(cfg.get('lower_dev') or 0):g}"
    if mode:
        return f"{prefix}{nominal:g} ({mode})"
    return f"{prefix}{nominal:g}"


def extract_sample(dxf_path: Path, family: str, pair_evidence: str) -> dict:
    _source, payloads = _extract_dxf_worker(str(dxf_path))
    raw = [ExtractedDimension(**item) for item in payloads]
    deduplicated, suppressed = HistoricalDataIngestor._deduplicate_dimensions(raw)
    explicit = [
        item for item in deduplicated
        if HistoricalDataIngestor._tolerance_is_plausible(item)
    ]
    dimensions = []
    for item in explicit:
        dimensions.append({
            "entity_handle": item.entity_handle,
            "nominal_value": item.nominal_value,
            "dimension_category": item.dimension_category,
            "tolerance_config": item.tolerance_config,
            "points": item.points,
            "extraction_status": "EXTRACTED",
        })
    strict_pair = pair_evidence in {"EXACT_FILENAME", "EMBEDDED_PART_EXACT_REVISION"}
    return {
        "family": family,
        "drawing": dxf_path.name,
        "dxf_path": str(dxf_path),
        "pair_evidence": pair_evidence,
        "eligible_for_3d_auto_verification": strict_pair,
        "raw_dimension_count": len(raw),
        "deduplicated_dimension_count": len(deduplicated),
        "suppressed_duplicate_count": int(suppressed),
        "explicit_tolerance_count": len(dimensions),
        "dimensions": dimensions,
    }


def run(config_path: Path, output_dir: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    dxf_root = Path(config["dxf_root"])
    results = []
    for sample in config.get("samples") or []:
        dxf_path = dxf_root / sample["drawing"]
        if not dxf_path.is_file():
            results.append({
                **sample,
                "status": "MISSING_DXF",
                "explicit_tolerance_count": 0,
                "dimensions": [],
            })
            continue
        result = extract_sample(dxf_path, sample["family"], sample["pair_evidence"])
        result["status"] = "EXTRACTED"
        results.append(result)

    summary = {
        "requested_family_count": len(config.get("samples") or []),
        "processed_family_count": sum(row.get("status") == "EXTRACTED" for row in results),
        "missing_dxf_count": sum(row.get("status") == "MISSING_DXF" for row in results),
        "strict_step_pair_count": sum(row.get("eligible_for_3d_auto_verification", False) for row in results),
        "review_pair_count": sum(
            row.get("status") == "EXTRACTED" and not row.get("eligible_for_3d_auto_verification", False)
            for row in results
        ),
        "raw_dimension_count": sum(row.get("raw_dimension_count", 0) for row in results),
        "explicit_tolerance_count": sum(row.get("explicit_tolerance_count", 0) for row in results),
    }
    report = {
        "schema_version": 1,
        "method": "DATABASE_FAMILY_STRATIFIED_DXF_TOLERANCE_PILOT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "scope_note": "Tolerance-value extraction only; 3D feature verification is a separate next stage.",
        "summary": summary,
        "samples": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "database_tolerance_pilot_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    render(report, output_dir / "database_tolerance_pilot_overview.png")
    return report


def render(report: dict, output: Path) -> None:
    width = 3000
    row_height = 150
    height = 175 + len(report["samples"]) * row_height + 35
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    summary = report["summary"]
    draw.text((28, 22), "DATABASE TOLERANCE EXTRACTION | 10-FAMILY PILOT", font=_font(34), fill=WHITE)
    draw.text(
        (28, 70),
        f"processed {summary['processed_family_count']}/{summary['requested_family_count']} | "
        f"explicit tolerances {summary['explicit_tolerance_count']} | "
        f"strict STEP pairs {summary['strict_step_pair_count']} | review pairs {summary['review_pair_count']}",
        font=_font(23), fill=BLUE,
    )
    draw.text(
        (28, 112),
        "This board validates DXF tolerance-value extraction. 3D feature linkage is intentionally not claimed here.",
        font=_font(18), fill=MUTED,
    )
    y = 175
    for index, sample in enumerate(report["samples"]):
        if index % 2 == 0:
            draw.rectangle((18, y, width - 18, y + row_height - 5), fill=PANEL)
        status = sample.get("status")
        color = GREEN if status == "EXTRACTED" else AMBER
        draw.text((32, y + 13), sample.get("family", ""), font=_font(22), fill=WHITE)
        draw.text((720, y + 13), sample.get("drawing", ""), font=_font(20), fill=WHITE)
        pair = sample.get("pair_evidence", "")
        pair_color = GREEN if sample.get("eligible_for_3d_auto_verification") else AMBER
        draw.text((1180, y + 13), pair, font=_font(17), fill=pair_color)
        draw.text(
            (2020, y + 13),
            f"raw {sample.get('raw_dimension_count', 0)} | explicit tolerance {sample.get('explicit_tolerance_count', 0)}",
            font=_font(19), fill=color,
        )
        labels = [_format_tolerance(row) for row in (sample.get("dimensions") or [])[:8]]
        preview = " | ".join(labels) if labels else "No explicit tolerance found"
        draw.text((32, y + 66), preview[:230], font=_font(17), fill=MUTED)
        y += row_height
    image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.config.resolve(), args.output_dir.resolve())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
