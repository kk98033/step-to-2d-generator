"""Generate blind-vs-expected STEP/DXF projection overlay evidence.

The blind ranking never uses the expected DXF filename.  Expected filenames
are read only after ranking so a reviewer can compare the algorithm's first
choice with the best projection hit inside the known drawing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher
from auto_2d_drawing.tolerance.projection_registration_v2 import (
    AdaptiveProjectionCandidateGenerator,
    SimilarityTransform2D,
)


BACKGROUND = "#171717"
PANEL = "#262626"
DXF_COLOR = "#60a5fa"
STEP_COLOR = "#f97316"
TEXT_COLOR = "#f5f5f5"
MUTED_COLOR = "#a3a3a3"


def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "component"


def _transform_from_dict(payload: dict) -> SimilarityTransform2D:
    return SimilarityTransform2D(
        scale=float(payload["scale"]),
        rotation_degrees=float(payload["rotation_degrees"]),
        translation=(float(payload["translation"][0]), float(payload["translation"][1])),
        mirrored=bool(payload.get("mirrored", False)),
    )


def _draw_overlay(
    path: Path,
    component_name: str,
    label: str,
    dxf_name: str,
    step_view: str,
    dxf_view: str,
    registration: dict,
    dxf_points,
    transformed_step_points,
) -> None:
    width, height = 1100, 760
    margin_left, margin_right = 70, 40
    plot_top, plot_bottom = 155, height - 55
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, width - 20, height - 20), radius=16, fill=PANEL)

    title_font = _font(28)
    body_font = _font(20)
    small_font = _font(17)
    draw.text((48, 40), component_name, font=title_font, fill=TEXT_COLOR)
    draw.text((48, 78), f"{label}: {dxf_name}", font=body_font, fill=TEXT_COLOR)
    score = float(registration.get("score", 0.0) or 0.0)
    coverage = float(registration.get("source_coverage", 0.0) or 0.0)
    target_coverage = float(registration.get("target_coverage", 0.0) or 0.0)
    scale_value = float(registration.get("scale", 0.0) or 0.0)
    metrics = (
        f"STEP {step_view} -> DXF {dxf_view}   score={score:.4f}   "
        f"coverage={coverage:.4f}/{target_coverage:.4f}   scale={scale_value:.4f}"
    )
    draw.text((48, 112), metrics, font=small_font, fill=MUTED_COLOR)

    all_points = list(dxf_points) + list(transformed_step_points)
    if not all_points:
        image.save(path)
        return
    xs = [float(point[0]) for point in all_points]
    ys = [float(point[1]) for point in all_points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    span_x = max(xmax - xmin, 1e-9)
    span_y = max(ymax - ymin, 1e-9)
    plot_width = width - margin_left - margin_right
    plot_height = plot_bottom - plot_top
    factor = min(plot_width / span_x, plot_height / span_y) * 0.92
    offset_x = margin_left + (plot_width - span_x * factor) / 2.0
    offset_y = plot_top + (plot_height - span_y * factor) / 2.0

    def canvas(point):
        return (
            offset_x + (float(point[0]) - xmin) * factor,
            plot_bottom - (offset_y - plot_top) - (float(point[1]) - ymin) * factor,
        )

    for point in dxf_points:
        x, y = canvas(point)
        draw.ellipse((x - 2.0, y - 2.0, x + 2.0, y + 2.0), fill=DXF_COLOR)
    for point in transformed_step_points:
        x, y = canvas(point)
        draw.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=STEP_COLOR)

    draw.rectangle((48, height - 42, 66, height - 26), fill=DXF_COLOR)
    draw.text((74, height - 46), "DXF vector points", font=small_font, fill=TEXT_COLOR)
    draw.rectangle((260, height - 42, 278, height - 26), fill=STEP_COLOR)
    draw.text((286, height - 46), "transformed STEP HLR", font=small_font, fill=TEXT_COLOR)
    image.save(path)


def _best_expected_match(searcher, projections, records):
    best = None
    for step_view, projection in projections.items():
        step_points = searcher._reduce_points(searcher._step_view_points(projection))
        for record in records:
            registration = searcher.registration.register(record.points, step_points)
            candidate = {
                "dxf_path": record.source_path,
                "dxf_view": record.view_id,
                "step_view": step_view,
                "registration": registration,
                "dxf_points": record.points,
                "step_points": step_points,
            }
            if best is None or registration.get("score", 0.0) > best["registration"].get("score", 0.0):
                best = candidate
    return best


def _manifest_row(item: dict) -> dict:
    registration = dict(item["geometry_search_evidence"]["registration"])
    return {
        "dxf_path": item["dxf_path"],
        "dxf_view": item["matched_dxf_view_ids"][0],
        "step_view": item["matched_step_view"],
        "status": item["status"],
        "global_geometry_score": item["global_geometry_score"],
        "reciprocal_margin": item["reciprocal_margin"],
        "registration": registration,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir = Path(config["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    step_paths = [str(Path(value).resolve()) for value in config["step_paths"]]
    dxf_dir = Path(config["dxf_dir"]).resolve()
    prefixes = tuple(config.get("dxf_prefixes") or ())
    dxf_paths = [
        str(path.resolve())
        for path in sorted(dxf_dir.glob("*.dxf"))
        if not prefixes or path.stem.startswith(prefixes)
    ]

    components = [
        component
        for step_path in step_paths
        for component in extract_leaf_components(step_path)
    ]
    component_by_name = {component.name: component for component in components}
    expected = dict(config["expected_dxf_by_component"])
    selected_components = [component_by_name[name] for name in expected]

    searcher = GlobalGeometryPairSearcher(
        retrieval_top_k=int(config.get("retrieval_top_k", 12)),
        refine_top_drawings=int(config.get("refine_top_drawings", 27)),
    )
    manifest = searcher.search(
        step_paths,
        dxf_paths,
        pre_extracted_components=selected_components,
    )
    grouped = defaultdict(list)
    for item in manifest["verified_pairs"] + manifest["candidates"]:
        grouped[item["component_name"]].append(item)
    for rows in grouped.values():
        rows.sort(key=lambda item: float(item.get("global_geometry_score", 0.0)), reverse=True)

    all_records, dxf_errors = searcher.build_dxf_index(dxf_paths)
    records_by_path = defaultdict(list)
    for record in all_records:
        records_by_path[str(Path(record.source_path).resolve())].append(record)

    summaries = []
    contact_rows = []
    for component in selected_components:
        projections = AdaptiveProjectionCandidateGenerator(max_views=6).project_all(component.shape)
        records_by_key = {
            (str(Path(record.source_path).resolve()), record.view_id): record
            for record in all_records
        }

        blind_item = grouped[component.name][0]
        blind = _manifest_row(blind_item)
        blind_record = records_by_key[(str(Path(blind["dxf_path"]).resolve()), blind["dxf_view"])]
        blind_step_points = searcher._reduce_points(
            searcher._step_view_points(projections[blind["step_view"]])
        )
        blind_transform = _transform_from_dict(blind["registration"]["transform"])
        blind_transformed = blind_transform.apply(blind_step_points)

        expected_path = str((dxf_dir / expected[component.name]).resolve())
        expected_match = _best_expected_match(
            searcher,
            projections,
            records_by_path[expected_path],
        )
        expected_transform = _transform_from_dict(expected_match["registration"]["transform"])
        expected_transformed = expected_transform.apply(expected_match["step_points"])

        stem = _safe_name(component.name)
        blind_png = output_dir / f"{stem}_01_blind_top_hit.png"
        expected_png = output_dir / f"{stem}_02_expected_best_hit.png"
        _draw_overlay(
            blind_png,
            component.name,
            "BLIND TOP HIT",
            Path(blind["dxf_path"]).name,
            blind["step_view"],
            blind["dxf_view"],
            blind["registration"],
            blind_record.points,
            blind_transformed,
        )
        _draw_overlay(
            expected_png,
            component.name,
            "EXPECTED DRAWING BEST HIT",
            Path(expected_match["dxf_path"]).name,
            expected_match["step_view"],
            expected_match["dxf_view"],
            expected_match["registration"],
            expected_match["dxf_points"],
            expected_transformed,
        )
        summaries.append({
            "component_name": component.name,
            "component_fingerprint": component.fingerprint,
            "blind_top_hit": {
                **{key: value for key, value in blind.items() if key != "registration"},
                "dxf_file": Path(blind["dxf_path"]).name,
                "registration": blind["registration"],
                "overlay": blind_png.name,
            },
            "expected_drawing_best_hit": {
                "dxf_file": Path(expected_match["dxf_path"]).name,
                "dxf_view": expected_match["dxf_view"],
                "step_view": expected_match["step_view"],
                "registration": expected_match["registration"],
                "overlay": expected_png.name,
            },
            "blind_hit_is_expected_file": Path(blind["dxf_path"]).name == expected[component.name],
        })
        contact_rows.append((blind_png, expected_png))

    panel_width, panel_height = 1100, 760
    contact = Image.new("RGB", (panel_width * 2, panel_height * len(contact_rows)), BACKGROUND)
    for row, (blind_png, expected_png) in enumerate(contact_rows):
        contact.paste(Image.open(blind_png), (0, row * panel_height))
        contact.paste(Image.open(expected_png), (panel_width, row * panel_height))
    contact_path = output_dir / "00_projection_hit_contact_sheet.png"
    contact.save(contact_path)

    report = {
        "method": "BLIND_GLOBAL_SEARCH_VS_EXPECTED_DRAWING_EXHAUSTIVE_LOCAL_MATCH",
        "score_input_uses_filename": False,
        "expected_filename_used_only_for_post_ranking_review": True,
        "candidate_dxf_count": len(dxf_paths),
        "indexed_dxf_view_count": len(all_records),
        "dxf_index_errors": dxf_errors,
        "components": summaries,
        "contact_sheet": contact_path.name,
    }
    (output_dir / "projection_hit_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        "# Projection Hit Review\n\n"
        "- Blue points: source DXF view cluster.\n"
        "- Orange points: transformed STEP HLR projection.\n"
        "- Left column: filename-blind top hit from the current global search.\n"
        "- Right column: exhaustive best hit inside the known drawing, used only for review.\n"
        "- Point overlap is diagnostic evidence, not an automatic correctness claim.\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
