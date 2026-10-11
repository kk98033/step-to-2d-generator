"""Render FNKW feature-link failures on the original annotated DXF sheets."""

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

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components  # noqa: E402
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher  # noqa: E402
from auto_2d_drawing.tolerance.ingest_historical_data import ExtractedDimension  # noqa: E402
from auto_2d_drawing.tolerance.projection_registration_v2 import (  # noqa: E402
    AdaptiveProjectionCandidateGenerator,
    SimilarityTransform2D,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from generate_projection_hit_review import _draw_overlay  # noqa: E402
from generate_tolerance_edge_review import (  # noqa: E402
    BACKGROUND,
    MUTED,
    WHITE,
    _font,
    _render_native_dxf,
)
from run_projection_pairing_v2 import bounds_of_primitives  # noqa: E402


DIMENSION_FIELDS = {field.name for field in fields(ExtractedDimension)}
RED = "#ef4444"
AMBER = "#f59e0b"
BLUE_RGB = (37, 99, 235)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "item"


def _transform(payload: dict) -> SimilarityTransform2D:
    return SimilarityTransform2D(
        scale=float(payload["scale"]),
        rotation_degrees=float(payload["rotation_degrees"]),
        translation=(float(payload["translation"][0]), float(payload["translation"][1])),
        mirrored=bool(payload.get("mirrored", False)),
    )


def _add_projection_crosses(source: Path, points, output: Path, marker_size: float) -> None:
    doc = ezdxf.readfile(str(source))
    if "AUDIT_STEP_PROJECTION" not in doc.layers:
        doc.layers.add(name="AUDIT_STEP_PROJECTION", color=5)
    msp = doc.modelspace()
    for x, y in points:
        for p1, p2 in (
            ((x - marker_size, y), (x + marker_size, y)),
            ((x, y - marker_size), (x, y + marker_size)),
        ):
            entity = msp.add_line(
                p1,
                p2,
                dxfattribs={"layer": "AUDIT_STEP_PROJECTION", "lineweight": 45},
            )
            entity.rgb = BLUE_RGB
    doc.saveas(str(output))


def run(feature_report: Path, extraction_report: Path, pair_manifest: Path, output_dir: Path) -> dict:
    report = json.loads(feature_report.read_text(encoding="utf-8"))
    extraction = json.loads(extraction_report.read_text(encoding="utf-8"))
    manifest = json.loads(pair_manifest.read_text(encoding="utf-8"))
    fnkw = [row for row in report["tolerances"] if str(row.get("family", "")).startswith("FNKW-")]
    failed_dxfs = {
        str(Path(row["dxf_path"]).resolve())
        for row in fnkw
        if row.get("reason") == "NO_3D_NOMINAL_CANDIDATE_AFTER_VERIFICATION"
    }
    global_rows = [
        row
        for row in manifest["global_geometry_manifest"]["verified_pairs"]
        if str(Path(row["dxf_path"]).resolve()) in failed_dxfs
    ]
    # A drawing can have a direct assembly pair and a global component pair.  The
    # global row is retained because it contains the auditable registration.
    best_by_dxf = {}
    for row in global_rows:
        key = str(Path(row["dxf_path"]).resolve())
        if key not in best_by_dxf or float(row.get("global_geometry_score", 0.0)) > float(
            best_by_dxf[key].get("global_geometry_score", 0.0)
        ):
            best_by_dxf[key] = row

    raw_dimensions = {
        str(Path(drawing["dxf_path"]).resolve()): drawing.get("dimensions") or []
        for drawing in extraction.get("drawings") or []
        if drawing.get("is_canonical_drawing_occurrence")
    }
    searcher = GlobalGeometryPairSearcher()
    records, _ = searcher.build_dxf_index(sorted(best_by_dxf))
    records_by_key = {
        (str(Path(record.source_path).resolve()), record.view_id): record for record in records
    }
    component_cache = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    boards = []
    summaries = []
    for index, (dxf_key, row) in enumerate(sorted(best_by_dxf.items()), start=1):
        step_path = str(Path(row["step_path"]).resolve())
        if step_path not in component_cache:
            component_cache[step_path] = extract_leaf_components(step_path)
        components = component_cache[step_path]
        component = next(
            (
                item
                for item in components
                if item.label_entry == row.get("component_label_entry")
                or item.name == row.get("component_name")
            ),
            None,
        )
        if component is None:
            continue
        projections = AdaptiveProjectionCandidateGenerator(max_views=6).project_all(component.shape)
        step_view = row["matched_step_view"]
        dxf_view = row["matched_dxf_view_ids"][0]
        step_points = searcher._reduce_points(searcher._step_view_points(projections[step_view]))
        registration = row["geometry_search_evidence"]["registration"]
        transformed = _transform(registration["transform"]).apply(step_points)
        record = records_by_key[(dxf_key, dxf_view)]
        extractor = DxfProjectionViewExtractorV2(dxf_key)
        sheet_bounds = bounds_of_primitives(extractor.all_primitives)
        diagonal = max(
            ((sheet_bounds[2] - sheet_bounds[0]) ** 2 + (sheet_bounds[3] - sheet_bounds[1]) ** 2) ** 0.5,
            1.0,
        )
        stem = f"{index:02d}_{_safe(Path(dxf_key).stem)}"
        overlay_dxf = output_dir / f"{stem}_registered_projection.dxf"
        _add_projection_crosses(Path(dxf_key), transformed, overlay_dxf, diagonal * 0.00065)
        raw = raw_dimensions[dxf_key][0]
        dim = ExtractedDimension(**{key: value for key, value in raw.items() if key in DIMENSION_FIELDS})
        original_png = output_dir / f"{stem}_original_dxf_with_projection.png"
        _render_native_dxf(
            overlay_dxf,
            dim,
            {},
            [],
            sheet_bounds,
            sheet_bounds,
            original_png,
            (1750, 1120),
        )
        point_png = output_dir / f"{stem}_registration_points.png"
        _draw_overlay(
            point_png,
            row.get("component_name") or "component",
            "FNKW VERIFIED DRAWING PAIR / FEATURE LINK FAILED",
            Path(dxf_key).name,
            step_view,
            dxf_view,
            registration,
            record.points,
            transformed,
        )
        rows = [item for item in fnkw if str(Path(item["dxf_path"]).resolve()) == dxf_key]
        board = Image.new("RGB", (3000, 1360), BACKGROUND)
        draw = ImageDraw.Draw(board)
        draw.text((35, 20), f"FNKW | {Path(dxf_key).name}", font=_font(30), fill=WHITE)
        draw.text(
            (35, 62),
            f"2D tolerances extracted: {len(rows)} | usable feature links: 0 | failure: NO_3D_NOMINAL_CANDIDATE_AFTER_VERIFICATION",
            font=_font(19),
            fill=RED,
        )
        draw.text(
            (35, 96),
            f"paired component: {row.get('component_name')} | score={float(row.get('global_geometry_score', 0.0)):.4f} | blue crosses = registered STEP HLR",
            font=_font(18),
            fill=AMBER,
        )
        draw.text(
            (35, 126),
            "The projection pair is plausible, but none of the extracted nominal dimensions map to this component's verified BRep feature graph.",
            font=_font(17),
            fill=MUTED,
        )
        with Image.open(original_png) as image:
            board.paste(image.convert("RGB"), (25, 205))
        with Image.open(point_png) as image:
            board.paste(image.convert("RGB"), (1840, 205))
        board_path = output_dir / f"{stem}_failure_review.png"
        board.save(board_path)
        boards.append(board_path)
        summaries.append(
            {
                "drawing": Path(dxf_key).name,
                "extracted_tolerance_count": len(rows),
                "usable_feature_link_count": 0,
                "component_name": row.get("component_name"),
                "step_path": step_path,
                "step_view": step_view,
                "dxf_view": dxf_view,
                "registration_score": row.get("global_geometry_score"),
                "failure": "NO_3D_NOMINAL_CANDIDATE_AFTER_VERIFICATION",
                "review_image": board_path.name,
            }
        )

    contact = Image.new("RGB", (1500, max(1, len(boards)) * 680), BACKGROUND)
    for index, board_path in enumerate(boards):
        with Image.open(board_path) as source:
            preview = source.convert("RGB")
            preview.thumbnail((1500, 680), Image.Resampling.LANCZOS)
        contact.paste(preview, ((1500 - preview.width) // 2, index * 680))
    contact_path = output_dir / "00_fnkw_feature_link_failure_contact_sheet.png"
    contact.save(contact_path)
    reason_counts = {}
    for row in fnkw:
        reason = row.get("reason") or "UNKNOWN"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    result = {
        "family": "FNKW-單出風-上蓋組合(側牆)_雙胞胎",
        "total_tolerances": len(fnkw),
        "usable_tolerances": 0,
        "reason_counts": reason_counts,
        "paired_failure_boards": summaries,
        "contact_sheet": contact_path.name,
    }
    (output_dir / "fnkw_debug_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-report", required=True, type=Path)
    parser.add_argument("--extraction-report", required=True, type=Path)
    parser.add_argument("--pair-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.feature_report.resolve(),
                args.extraction_report.resolve(),
                args.pair_manifest.resolve(),
                args.output_dir.resolve(),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
