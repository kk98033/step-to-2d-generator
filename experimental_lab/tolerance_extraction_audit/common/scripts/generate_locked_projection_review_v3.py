"""Build readable projection review boards from confirmed projection_pairing_v2 hits.

This renderer does not rerun pairing.  It treats the component, DXF file and
the local ``view_v2_*`` region selected by projection_pairing_v2 as immutable.
The left panel shows the local projection overlay; the right panel keeps the
complete original 2D drawing with the matched region highlighted.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
BLUE = "#60a5fa"
ORANGE = "#f97316"
GREEN = "#22c55e"


def _font(size: int) -> ImageFont.ImageFont:
    for path in (Path("C:/Windows/Fonts/consola.ttf"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "component"


def _fit(image: Image.Image, size):
    fitted = image.convert("RGB").copy()
    fitted.thumbnail(size, Image.Resampling.LANCZOS)
    return fitted


def _matching_hit(component: dict) -> dict:
    expected = str(component.get("expected_dxf") or "")
    hit = next(
        (
            item for item in component.get("detected_hits") or []
            if str(item.get("dxf") or "").lower() == expected.lower()
        ),
        None,
    )
    if hit is None:
        raise RuntimeError(
            f"No projection_pairing_v2 hit for {component.get('component_name')} -> {expected}"
        )
    return hit


def _render_board(component: dict, hit: dict, source_root: Path, output: Path) -> dict:
    relative_board = Path(str(hit["review_board"]).replace("\\", "/"))
    artifact_dir = (source_root / relative_board).parent
    overlay_path = artifact_dir / "03_projection_overlay.png"
    original_path = artifact_dir / "02_original_dxf_and_hit_region.png"
    if not overlay_path.is_file() or not original_path.is_file():
        raise FileNotFoundError(f"Missing V2 review artifacts in {artifact_dir}")

    width, height = 2200, 900
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=16, fill=PANEL)
    component_name = str(component["component_name"])
    draw.text((42, 32), component_name, font=_font(30), fill=WHITE)
    draw.text(
        (42, 75),
        (
            f"DXF={hit['dxf']}  STEP={hit['step_view']}  region={hit['dxf_view']}  "
            f"raw={float(hit['raw_score']):.4f}  score={float(hit['score']):.4f}  "
            f"scale={float(hit['scale']):.4f}"
        ),
        font=_font(18),
        fill=MUTED,
    )
    draw.text((42, 118), "LOCAL PROJECTION OVERLAY", font=_font(22), fill=WHITE)
    draw.text((1120, 118), "ORIGINAL 2D DRAWING REVIEW", font=_font(22), fill=WHITE)

    with Image.open(overlay_path) as overlay_source, Image.open(original_path) as original_source:
        overlay = _fit(overlay_source, (1030, 690))
        original = _fit(original_source, (1030, 690))
    left_box = (35, 158, 1085, 858)
    right_box = (1115, 158, 2165, 858)
    draw.rectangle(left_box, fill=BACKGROUND, outline="#404040", width=2)
    draw.rectangle(right_box, fill=BACKGROUND, outline="#404040", width=2)
    image.paste(
        overlay,
        (
            left_box[0] + (left_box[2] - left_box[0] - overlay.width) // 2,
            left_box[1] + (left_box[3] - left_box[1] - overlay.height) // 2,
        ),
    )
    image.paste(
        original,
        (
            right_box[0] + (right_box[2] - right_box[0] - original.width) // 2,
            right_box[1] + (right_box[3] - right_box[1] - original.height) // 2,
        ),
    )

    draw.rectangle((52, 815, 70, 833), fill=BLUE)
    draw.text((78, 811), "DXF selected-region geometry", font=_font(16), fill=WHITE)
    draw.rectangle((350, 815, 368, 833), fill=ORANGE)
    draw.text((376, 811), "registered STEP HLR", font=_font(16), fill=WHITE)
    draw.text((1135, 811), "Orange box = selected local view", font=_font(16), fill=ORANGE)
    draw.text((1135, 835), "Source: projection_pairing_v2 confirmed hit", font=_font(15), fill=GREEN)
    image.save(output)
    return {
        "component_name": component_name,
        "drawing": hit["dxf"],
        "review_verdict": hit.get("review_verdict"),
        "hit_type": hit.get("hit_type"),
        "step_view": hit["step_view"],
        "dxf_view": hit["dxf_view"],
        "raw_score": hit["raw_score"],
        "adjusted_score": hit["score"],
        "scale": hit["scale"],
        "source_overlay": str(overlay_path.resolve()),
        "source_original_2d_review": str(original_path.resolve()),
        "review_board": output.name,
    }


def run(report_path: Path, output_dir: Path) -> dict:
    source_report = json.loads(report_path.read_text(encoding="utf-8"))
    source_root = report_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    boards = []
    for component in source_report.get("components") or []:
        hit = _matching_hit(component)
        output = output_dir / f"{_safe_name(component['component_name'])}_02_projection_with_original_review.png"
        rows.append(_render_board(component, hit, source_root, output))
        boards.append(output)

    contact_width, row_height = 2200, 900
    contact = Image.new("RGB", (contact_width, row_height * len(boards)), BACKGROUND)
    for index, board_path in enumerate(boards):
        with Image.open(board_path) as board:
            contact.paste(board.convert("RGB"), (0, index * row_height))
    contact_path = output_dir / "00_projection_with_original_2d_review_contact_sheet.png"
    contact.save(contact_path)

    report = {
        "schema_version": 1,
        "method": "PROJECTION_PAIRING_V2_CONFIRMED_REGION_REVIEW_BOARD_V3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "web_ui_updated": False,
        "source_report": str(report_path.resolve()),
        "component_and_local_dxf_region_locked": True,
        "components": rows,
        "contact_sheet": contact_path.name,
    }
    (output_dir / "projection_with_original_2d_review_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Projection Review V3\n\n"
        "- Left: the confirmed local projection overlay from projection_pairing_v2.\n"
        "- Right: the original complete 2D drawing with the selected region highlighted.\n"
        "- The component, DXF file and local DXF region are not searched again.\n"
        "- This experiment does not update the web UI or production case database.\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairing-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.pairing_report.resolve(), args.output_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
