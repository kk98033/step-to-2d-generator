"""Render one auditable 2D/3D feature-link example per database family."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import fields
from pathlib import Path

import ezdxf
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for root in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from auto_2d_drawing.tolerance.ingest_historical_data import ExtractedDimension  # noqa: E402
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from generate_tolerance_edge_review import (  # noqa: E402
    BACKGROUND, PANEL, WHITE, MUTED, _focus_bounds, _font, _format_tolerance,
    _render_native_dxf,
)
from run_projection_pairing_v2 import bounds_of_primitives  # noqa: E402


DIMENSION_FIELDS = {field.name for field in fields(ExtractedDimension)}
GREEN = "#22c55e"
AMBER = "#facc15"
BLUE_RGB = (37, 99, 235)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "item"


def _rgb(entity, value):
    entity.rgb = value
    return entity


def _find_overlay(report, row):
    pair = row.get("pair") or {}
    registration = (row.get("geometry_verification") or {}).get("view_registration") or {}
    for pair_result in report.get("pair_results") or []:
        if pair_result.get("pair_id") != pair.get("pair_id"):
            continue
        for overlay in (pair_result.get("projection_overlays") or {}).values():
            if (
                overlay.get("step_view") == registration.get("step_view")
                and overlay.get("dxf_view_id") == registration.get("dxf_view_id")
            ):
                return overlay
    return None


def _projection_dxf(source: Path, overlay: dict | None, output: Path):
    doc = ezdxf.readfile(str(source))
    if overlay:
        msp = doc.modelspace()
        if "AUDIT_STEP_PROJECTION" not in doc.layers:
            doc.layers.add(name="AUDIT_STEP_PROJECTION", color=5)
        for line in overlay.get("lines") or []:
            p1, p2 = line.get("p1"), line.get("p2")
            if p1 and p2:
                entity = msp.add_line(p1[:2], p2[:2], dxfattribs={"layer": "AUDIT_STEP_PROJECTION", "lineweight": 45})
                _rgb(entity, BLUE_RGB)
        for circle in overlay.get("circles") or []:
            if circle.get("center") and circle.get("radius"):
                entity = msp.add_circle(
                    circle["center"][:2], float(circle["radius"]),
                    dxfattribs={"layer": "AUDIT_STEP_PROJECTION", "lineweight": 45},
                )
                _rgb(entity, BLUE_RGB)
    doc.saveas(str(output))


def run(feature_report: Path, extraction_report: Path, output_dir: Path):
    report = json.loads(feature_report.read_text(encoding="utf-8"))
    extraction = json.loads(extraction_report.read_text(encoding="utf-8"))
    raw_dimensions = {
        (str(drawing["dxf_path"]).casefold(), str(dim.get("entity_handle") or "").upper()): dim
        for drawing in extraction.get("drawings") or []
        if drawing.get("is_canonical_drawing_occurrence")
        for dim in drawing.get("dimensions") or []
    }
    selected = []
    for family in [row["family"] for row in report.get("families") or []]:
        candidates = [
            row for row in report.get("tolerances") or []
            if row["family"] == family
            and row["status"] in {"VERIFIED", "CANDIDATE_ONLY"}
            and not row.get("specialized_current_final_source")
            and (row.get("geometry_verification") or {}).get("association")
        ]
        candidates.sort(key=lambda row: (
            row["status"] != "VERIFIED",
            -float(row.get("confidence", 0.0) or 0.0),
        ))
        if candidates:
            selected.append(candidates[0])

    output_dir.mkdir(parents=True, exist_ok=True)
    boards = []
    for index, row in enumerate(selected, start=1):
        payload = raw_dimensions[(str(row["dxf_path"]).casefold(), str(row["entity_handle"]).upper())]
        dim = ExtractedDimension(**{key: value for key, value in payload.items() if key in DIMENSION_FIELDS})
        verification = row.get("geometry_verification") or {}
        association = verification.get("association") or {}
        edges = list(association.get("dimension_target_edges") or [])
        overlay = _find_overlay(report, row)
        stem = f"{index:02d}_{_safe(row['family'])}_{_safe(row['drawing'])}_{_safe(row['entity_handle'])}"
        derived_dxf = output_dir / f"{stem}_projection_overlay.dxf"
        _projection_dxf(Path(row["dxf_path"]), overlay, derived_dxf)
        extractor = DxfProjectionViewExtractorV2(str(row["dxf_path"]))
        sheet_bounds = bounds_of_primitives(extractor.all_primitives)
        focus = _focus_bounds(dim, edges) or sheet_bounds
        full = output_dir / f"{stem}_full.png"
        detail = output_dir / f"{stem}_detail.png"
        edge_rgb = (34, 197, 94) if row["status"] == "VERIFIED" else (250, 204, 21)
        label = f"{row.get('feature_type')}.{row.get('nominal_field')} | {row['status']}"
        _render_native_dxf(
            derived_dxf, dim, association, edges, sheet_bounds, sheet_bounds,
            full, (1750, 1120), edge_rgb=edge_rgb, span_rgb=(34, 211, 238), label=label,
        )
        _render_native_dxf(
            derived_dxf, dim, association, edges, sheet_bounds, focus,
            detail, (1150, 1120), edge_rgb=edge_rgb, span_rgb=(34, 211, 238), label=label,
        )
        board = Image.new("RGB", (3000, 1360), BACKGROUND)
        draw = ImageDraw.Draw(board)
        status_color = GREEN if row["status"] == "VERIFIED" else AMBER
        draw.text((35, 22), f"{row['family']} | {row['drawing']} | {_format_tolerance(dim)}", font=_font(28), fill=WHITE)
        draw.text((35, 64), f"{row['status']} | {label} | component {(row.get('pair') or {}).get('component_name') or 'whole STEP'}", font=_font(20), fill=status_color)
        draw.text((35, 98), "blue: registered STEP projection | status color: attached 2D target edge | cyan: dimension anchor", font=_font(17), fill=MUTED)
        with Image.open(full) as image:
            board.paste(image.convert("RGB"), (25, 160))
        with Image.open(detail) as image:
            board.paste(image.convert("RGB"), (1825, 160))
        board_path = output_dir / f"{stem}_review.png"
        board.save(board_path)
        boards.append(board_path)

    contact = Image.new("RGB", (1500, max(1, len(boards)) * 680), BACKGROUND)
    for index, board_path in enumerate(boards):
        with Image.open(board_path) as source:
            preview = source.convert("RGB")
            preview.thumbnail((1500, 680), Image.Resampling.LANCZOS)
        contact.paste(preview, ((1500 - preview.width) // 2, index * 680))
    contact_path = output_dir / "00_database_feature_link_debug_contact_sheet.png"
    contact.save(contact_path)
    summary = {"selected_family_count": len(boards), "boards": [path.name for path in boards], "contact_sheet": contact_path.name}
    (output_dir / "debug_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-report", required=True, type=Path)
    parser.add_argument("--extraction-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.feature_report.resolve(), args.extraction_report.resolve(), args.output_dir.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
