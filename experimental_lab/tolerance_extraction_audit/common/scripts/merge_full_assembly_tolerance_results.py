"""Build one retrieval-ready audit for every component in a STEP assembly.

The output is experimental.  It does not write the production case database
or update the web application.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
GREEN = "#22c55e"
AMBER = "#facc15"
RED = "#ef4444"
BLUE = "#60a5fa"

STRICT_STATUSES = {"VERIFIED", "SEMANTICALLY_RECOVERED", "COMPOSITE_RECOVERED"}


def _font(size: int):
    for path in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_component_row(component: str, row: dict, report_path: Path) -> dict:
    result = dict(row)
    original_status = str(result.get("status") or "UNRESOLVED").upper()
    status = "VERIFIED" if original_status in STRICT_STATUSES else original_status
    if status not in {"VERIFIED", "CANDIDATE_ONLY", "UNRESOLVED"}:
        status = "UNRESOLVED"
    result["component"] = component
    result["original_status"] = original_status
    result["status"] = status
    result["source_report"] = str(report_path)
    if result.get("feature_id") and not result.get("semantic_path"):
        field = result.get("nominal_field") or "value"
        result["semantic_path"] = f"component[{component}].{result['feature_id']}.{field}"

    if status == "VERIFIED":
        policy = {
            "eligible": True,
            "tier": "TRUSTED",
            "confidence_label": "HIGH",
            "engineer_review_required": False,
        }
    elif status == "CANDIDATE_ONLY":
        policy = {
            "eligible": True,
            "tier": "REVIEW_REQUIRED",
            "confidence_label": "MEDIUM" if result.get("semantic_path") else "LOW",
            "engineer_review_required": True,
        }
    else:
        policy = {
            "eligible": False,
            "tier": "EXCLUDED_UNRESOLVED",
            "confidence_label": "INSUFFICIENT",
            "engineer_review_required": True,
        }
    result["case_retrieval"] = policy
    result["recommendation_eligible"] = policy["eligible"]
    result["recommendation_label"] = policy["tier"]
    result["engineer_review_required"] = policy["engineer_review_required"]
    return result


def _component_payload(component: str, report_path: Path, blade: bool = False) -> dict:
    report = _read(report_path)
    source_rows = report.get("explicit_tolerances") if blade else report.get("dimensions")
    rows = [normalize_component_row(component, row, report_path) for row in (source_rows or [])]
    summary = {
        "explicit_tolerance_count": len(rows),
        "verified_3d_feature_links": sum(row["status"] == "VERIFIED" for row in rows),
        "candidate_only": sum(row["status"] == "CANDIDATE_ONLY" for row in rows),
        "unresolved_3d_feature": sum(row["status"] == "UNRESOLVED" for row in rows),
        "searchable_cases": sum(bool(row["recommendation_eligible"]) for row in rows),
    }
    summary["verified_feature_link_rate"] = round(
        summary["verified_3d_feature_links"] / max(len(rows), 1), 6
    )
    summary["recommendation_coverage"] = round(
        summary["searchable_cases"] / max(len(rows), 1), 6
    )
    return {
        "component": component,
        "drawing": report.get("drawing"),
        "source_report": str(report_path),
        "summary": summary,
        "explicit_tolerances": rows,
        "supplemental_nominal_annotations": report.get("supplemental_nominal_annotations") or [],
    }


def merge(blade_report: Path, shaft_report: Path, motor_report: Path) -> dict:
    components = [
        _component_payload("AL0W_BLADE_H-R01", blade_report, blade=True),
        _component_payload("2AL0W5010H-R01", shaft_report),
        _component_payload("2AL0W4010H-R01", motor_report),
    ]
    rows = [row for component in components for row in component["explicit_tolerances"]]
    summary = {
        "component_count": len(components),
        "explicit_tolerance_count": len(rows),
        "tolerance_value_extracted": len(rows),
        "verified_3d_feature_links": sum(row["status"] == "VERIFIED" for row in rows),
        "candidate_only": sum(row["status"] == "CANDIDATE_ONLY" for row in rows),
        "unresolved_3d_feature": sum(row["status"] == "UNRESOLVED" for row in rows),
        "searchable_cases": sum(bool(row["recommendation_eligible"]) for row in rows),
        "trusted_searchable_cases": sum(row["recommendation_label"] == "TRUSTED" for row in rows),
        "review_required_searchable_cases": sum(
            row["recommendation_label"] == "REVIEW_REQUIRED" for row in rows
        ),
    }
    count = max(len(rows), 1)
    summary["tolerance_extraction_rate"] = round(summary["tolerance_value_extracted"] / count, 6)
    summary["verified_feature_link_rate"] = round(summary["verified_3d_feature_links"] / count, 6)
    summary["recommendation_coverage"] = round(summary["searchable_cases"] / count, 6)
    return {
        "schema_version": 1,
        "method": "FULL_STEP_ASSEMBLY_MULTI_DRAWING_TOLERANCE_AUDIT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "assembly": "BLADE-ASSY-1AL0W000H-R01_ASM",
        "source_step": "1AL0W5000H-R03.stp",
        "summary": summary,
        "components": components,
        "explicit_tolerances": rows,
    }


def _tolerance_label(row: dict) -> str:
    cfg = row.get("tolerance_config") or {}
    nominal = float(row.get("nominal_value") or 0.0)
    prefix = "DIA " if str(row.get("dimension_category", "")).upper() == "DIAMETER" else ""
    if "dev" in cfg:
        return f"{prefix}{nominal:g}  +/-{float(cfg['dev']):g} mm"
    upper = float(cfg.get("upper_dev") or 0.0)
    lower = float(cfg.get("lower_dev") or 0.0)
    return f"{prefix}{nominal:g}  +{upper:g}/{lower:g} mm"


def render(report: dict, output: Path) -> None:
    row_height = 58
    component_header = 62
    header_height = 170
    total_rows = sum(len(item["explicit_tolerances"]) for item in report["components"])
    height = header_height + len(report["components"]) * component_header + total_rows * row_height + 45
    width = 3200
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    summary = report["summary"]
    draw.text((30, 24), "1AL0W5000H-R03 | FULL ASSEMBLY TOLERANCE EXTRACTION", font=_font(34), fill=WHITE)
    draw.text(
        (30, 72),
        f"components {summary['component_count']} | extracted {summary['explicit_tolerance_count']} | "
        f"3D verified {summary['verified_3d_feature_links']} | candidate {summary['candidate_only']} | "
        f"unresolved {summary['unresolved_3d_feature']} | searchable {summary['searchable_cases']}",
        font=_font(24), fill=BLUE,
    )
    draw.text(
        (30, 112),
        "Candidates remain searchable and are explicitly marked ENGINEER REVIEW REQUIRED.",
        font=_font(19), fill=MUTED,
    )
    y = header_height
    for component in report["components"]:
        comp_summary = component["summary"]
        draw.rectangle((20, y, width - 20, y + component_header - 6), fill="#202020")
        draw.text((35, y + 10), component["component"], font=_font(25), fill=WHITE)
        draw.text(
            (650, y + 14),
            f"drawing {component['drawing']} | total {comp_summary['explicit_tolerance_count']} | "
            f"verified {comp_summary['verified_3d_feature_links']} | candidate {comp_summary['candidate_only']} | "
            f"unresolved {comp_summary['unresolved_3d_feature']} | searchable {comp_summary['searchable_cases']}",
            font=_font(18), fill=BLUE,
        )
        y += component_header
        for index, row in enumerate(component["explicit_tolerances"]):
            if index % 2 == 0:
                draw.rectangle((20, y, width - 20, y + row_height - 3), fill=PANEL)
            status = row["status"]
            color = GREEN if status == "VERIFIED" else AMBER if status == "CANDIDATE_ONLY" else RED
            draw.text((35, y + 13), str(row.get("entity_handle") or "-"), font=_font(18), fill=WHITE)
            draw.text((180, y + 13), _tolerance_label(row), font=_font(18), fill=WHITE)
            draw.text((620, y + 13), status, font=_font(18), fill=color)
            label = "TRUSTED" if status == "VERIFIED" else (
                "SEARCHABLE | ENGINEER REVIEW REQUIRED" if status == "CANDIDATE_ONLY" else "EXCLUDED"
            )
            draw.text((900, y + 13), label, font=_font(17), fill=color)
            semantic = row.get("semantic_path") or row.get("reason") or "no 3D semantic"
            draw.text((1470, y + 13), str(semantic)[:115], font=_font(16), fill=MUTED)
            y += row_height
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blade", required=True, type=Path)
    parser.add_argument("--shaft", required=True, type=Path)
    parser.add_argument("--motor", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    args = parser.parse_args()
    report = merge(args.blade.resolve(), args.shaft.resolve(), args.motor.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    render(report, args.image)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
