"""Aggregate per-component tolerance semantic review reports."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


STATUS_COLORS = {
    "VERIFIED": "#22c55e",
    "SEMANTICALLY_RECOVERED": "#4ade80",
    "COMPOSITE_RECOVERED": "#10b981",
    "CANDIDATE_ONLY": "#facc15",
    "UNRESOLVED": "#ef4444",
}


def font(size: int):
    for path in ("C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/arial.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def load_reports(root: Path):
    reports = []
    for path in sorted(root.glob("*/feature_semantic_review_report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        report["report_path"] = str(path.resolve())
        report["component_dir"] = str(path.parent.resolve())
        reports.append(report)
    return reports


def representative_image(report):
    priorities = ("COMPOSITE_RECOVERED", "VERIFIED", "SEMANTICALLY_RECOVERED", "CANDIDATE_ONLY", "UNRESOLVED")
    dimensions = report.get("dimensions") or []
    row = next((row for status in priorities for row in dimensions if row.get("status") == status), None)
    if not row:
        return None
    return Path(report["component_dir"]) / row["review_image"]


def render_dashboard(reports, pose_by_component, output: Path):
    width, row_height = 3000, 660
    height = 170 + row_height * len(reports)
    image = Image.new("RGB", (width, height), "#101010")
    draw = ImageDraw.Draw(image)
    draw.text((32, 24), "MULTI-COMPONENT TOLERANCE / 3D FEATURE REVIEW", font=font(34), fill="#f5f5f5")
    totals = {key: 0 for key in STATUS_COLORS}
    for report in reports:
        for key in totals:
            totals[key] += int((report.get("summary") or {}).get(key.lower(), 0))
    dimension_total = sum(totals.values())
    linked = totals["VERIFIED"] + totals["SEMANTICALLY_RECOVERED"] + totals["COMPOSITE_RECOVERED"]
    subtitle = (
        f"components={len(reports)}  tolerances={dimension_total}  trusted_links={linked}  "
        f"candidates={totals['CANDIDATE_ONLY']}  unresolved={totals['UNRESOLVED']}"
    )
    draw.text((32, 76), subtitle, font=font(22), fill="#a3a3a3")

    for index, report in enumerate(reports):
        y0 = 140 + index * row_height
        y1 = y0 + row_height - 16
        draw.rounded_rectangle((20, y0, width - 20, y1), radius=14, fill="#171717", outline="#333333", width=2)
        component = report.get("component_name") or "UNKNOWN"
        pose = pose_by_component.get(component) or {}
        summary = report.get("summary") or {}
        draw.text((42, y0 + 20), component, font=font(28), fill="#f5f5f5")
        draw.text(
            (500, y0 + 27),
            f"projection={pose.get('status', 'UNKNOWN')} score={pose.get('score', 0):.4f}",
            font=font(18), fill="#60a5fa",
        )
        counts = "  ".join(f"{key}={summary.get(key.lower(), 0)}" for key in STATUS_COLORS)
        draw.text((42, y0 + 62), counts, font=font(16), fill="#a3a3a3")

        preview_path = representative_image(report)
        preview_box = (42, y0 + 100, 1080, y1 - 24)
        draw.rectangle(preview_box, fill="#0a0a0a")
        if preview_path and preview_path.exists():
            with Image.open(preview_path) as source:
                preview = source.convert("RGB")
            preview.thumbnail((preview_box[2] - preview_box[0], preview_box[3] - preview_box[1]), Image.Resampling.LANCZOS)
            px = preview_box[0] + (preview_box[2] - preview_box[0] - preview.width) // 2
            py = preview_box[1] + (preview_box[3] - preview_box[1] - preview.height) // 2
            image.paste(preview, (px, py))

        x = 1120
        line_y = y0 + 104
        dimensions = report.get("dimensions") or []
        line_size = 15 if len(dimensions) > 20 else 18
        line_height = 18 if len(dimensions) > 20 else 25
        for row in dimensions:
            status = row.get("status") or "UNRESOLVED"
            tolerance = row.get("tolerance_config") or {}
            if tolerance.get("mode") == "CUSTOM_SYMMETRIC":
                tolerance_text = f"+/-{tolerance.get('dev')}"
            else:
                tolerance_text = f"{tolerance.get('upper_dev', '')}/{tolerance.get('lower_dev', '')}"
            feature = row.get("feature_id") or "--"
            text = (
                f"{row.get('entity_handle', '--'):>6}  {float(row.get('nominal_value') or 0):>8g} "
                f"{tolerance_text:<14} {str(row.get('dimension_category') or ''):<8} "
                f"{status:<24} {feature}"
            )
            draw.text((x, line_y), text, font=font(line_size), fill=STATUS_COLORS.get(status, "#d4d4d4"))
            line_y += line_height
    image.save(output)


def run(root: Path, pose_report: Path):
    reports = load_reports(root)
    pose_data = json.loads(pose_report.read_text(encoding="utf-8"))
    pose_by_component = {
        row["component_name"]: {
            "status": (row.get("pose_search") or {}).get("status"),
            "score": float(((row.get("pose_search") or {}).get("locked_registration") or {}).get("score", 0.0)),
        }
        for row in pose_data.get("components") or []
    }
    rows = []
    totals = {key.lower(): 0 for key in STATUS_COLORS}
    for report in reports:
        component = report.get("component_name")
        for item in report.get("dimensions") or []:
            row = dict(item)
            row["component_name"] = component
            row["drawing"] = report.get("drawing")
            row["projection_status"] = (pose_by_component.get(component) or {}).get("status")
            rows.append(row)
            totals[str(item.get("status") or "UNRESOLVED").lower()] += 1
    linked = totals["verified"] + totals["semantically_recovered"] + totals["composite_recovered"]
    aggregate = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "component_count": len(reports),
        "dimension_count": len(rows),
        "trusted_3d_links": linked,
        "trusted_link_rate": round(linked / max(1, len(rows)), 4),
        "summary": totals,
        "components": [
            {
                "component_name": report.get("component_name"),
                "drawing": report.get("drawing"),
                "projection": pose_by_component.get(report.get("component_name")),
                "summary": report.get("summary"),
                "report_path": report.get("report_path"),
            }
            for report in reports
        ],
        "dimensions": rows,
    }
    (root / "aggregate_feature_semantic_report.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (root / "aggregate_tolerances.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = [
            "component_name", "drawing", "entity_handle", "nominal_value", "dimension_category",
            "status", "feature_id", "nominal_field", "reason", "projection_status",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    render_dashboard(reports, pose_by_component, root / "00_all_components_tolerance_summary.png")
    return aggregate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pose-report", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.root.resolve(), args.pose_report.resolve())
    print(json.dumps({key: result[key] for key in ("component_count", "dimension_count", "trusted_3d_links", "trusted_link_rate", "summary")}, indent=2))


if __name__ == "__main__":
    main()
