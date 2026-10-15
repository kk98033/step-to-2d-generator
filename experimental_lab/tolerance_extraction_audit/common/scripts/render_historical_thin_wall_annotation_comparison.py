from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BG = "#171717"
PANEL = "#101010"
BORDER = "#3f3f46"
WHITE = "#f4f4f5"
MUTED = "#a1a1aa"
BLUE = "#2563eb"
CYAN = "#22d3ee"
YELLOW = "#facc15"
RED = "#ef4444"
GREEN = "#22c55e"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def fit_image(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[Image.Image, tuple[int, int]]:
    x0, y0, x1, y1 = box
    available = (x1 - x0, y1 - y0)
    copy = image.copy()
    copy.thumbnail(available, Image.Resampling.LANCZOS)
    return copy, (x0 + (available[0] - copy.width) // 2, y0 + (available[1] - copy.height) // 2)


def panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str) -> None:
    draw.rounded_rectangle(box, radius=12, fill=PANEL, outline=BORDER, width=2)
    draw.text((box[0] + 18, box[1] + 13), title, fill=WHITE, font=font(25, True))


def paste_with_frame(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(canvas)
    fitted, pos = fit_image(image, box)
    canvas.paste(fitted, pos)
    draw.rectangle(
        (pos[0], pos[1], pos[0] + fitted.width, pos[1] + fitted.height),
        outline="#52525b",
        width=2,
    )


def annular_schematic(size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, PANEL)
    draw = ImageDraw.Draw(image)
    cx, cy = size[0] // 2, size[1] // 2 + 10
    ro, ri = min(size) * 0.30, min(size) * 0.19
    for radius, colour, width in ((ro, WHITE, 5), (ri, WHITE, 5)):
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=colour, width=width)
    y = cy
    draw.line((cx - ro, y, cx + ro, y), fill=CYAN, width=3)
    draw.line((cx - ri, y - 22, cx + ri, y - 22), fill=YELLOW, width=3)
    draw.text((cx + ro + 20, y - 20), "ØD", fill=CYAN, font=font(25, True))
    draw.text((cx + ri + 20, y - 52), "Ød", fill=YELLOW, font=font(25, True))
    draw.line((cx + ri, y + 58, cx + ro, y + 58), fill=GREEN, width=4)
    draw.line((cx + ri, y + 45, cx + ri, y + 72), fill=GREEN, width=3)
    draw.line((cx + ro, y + 45, cx + ro, y + 72), fill=GREEN, width=3)
    draw.text((cx + ri + 5, y + 75), "t = (ØD - Ød) / 2", fill=GREEN, font=font(23, True))
    return image


def historical_crop(sheet: Image.Image) -> Image.Image:
    # The SVG screenshot contains the A4 sheet in the upper-left. Ratios keep
    # the crop stable if the raster resolution changes.
    w, h = sheet.size
    return sheet.crop((int(w * 0.17), int(h * 0.07), int(w * 0.61), int(h * 0.42)))


def render(args: argparse.Namespace) -> dict:
    historical = Image.open(args.historical_sheet).convert("RGB")
    cylindrical = Image.open(args.cylindrical_debug).convert("RGB")
    complete = Image.open(args.complete_debug).convert("RGB")
    low_count = Image.open(args.low_count_debug).convert("RGB")

    canvas = Image.new("RGB", (2400, 1920), BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 28), "Experiment 19｜三類薄壁案例與歷史工程圖標註對照", fill=WHITE, font=font(38, True))
    draw.text(
        (45, 78),
        "左：真實歷史標註／明確無原圖的建議語意　右：目前 STEP 幾何 DEBUG",
        fill=MUTED,
        font=font(23),
    )

    rows = [(125, 670), (700, 1245), (1275, 1820)]
    left = (35, 0, 1177, 0)
    right = (1223, 0, 2365, 0)

    # Row 1: constructed control, therefore no historical drawing exists.
    y0, y1 = rows[0]
    lbox, rbox = (left[0], y0, left[2], y1), (right[0], y0, right[2], y1)
    panel(draw, lbox, "1. 同軸圓柱薄壁｜無歷史原圖（合成控制件）")
    panel(draw, rbox, "目前失敗｜尚未配對同軸圓柱面")
    schematic = annular_schematic((1040, 370))
    canvas.paste(schematic, (85, y0 + 65))
    draw.text((85, y1 - 83), "常見標法：剖視圖標 ØD／Ød，或直接標徑向壁厚 t。", fill=YELLOW, font=font(22, True))
    draw.text((85, y1 - 50), "這是建議工程語意，不是資料庫中的原始工程圖。", fill=RED, font=font(20, True))
    paste_with_frame(canvas, cylindrical, (rbox[0] + 20, y0 + 58, rbox[2] - 20, y1 - 18))

    original = historical_crop(historical)

    # Row 2: full assembly and its actual released drawing.
    y0, y1 = rows[1]
    lbox, rbox = (left[0], y0, left[2], y1), (right[0], y0, right[2], y1)
    panel(draw, lbox, "2. 完整放射葉片組件｜歷史圖 1AC085000H-R02")
    panel(draw, rbox, "目前失敗｜有找到重複葉片，但成對邊界不穩定")
    paste_with_frame(canvas, original, (lbox[0] + 20, y0 + 58, lbox[2] - 20, y1 - 105))
    draw.text((65, y1 - 93), "原圖實際標註：葉片間角度、輪廓直徑、圓角與 E–E 剖面。", fill=GREEN, font=font(21, True))
    draw.text((65, y1 - 58), "原圖沒有直接標葉片厚度；R0.20 是圓角，不是厚度。", fill=RED, font=font(21, True))
    paste_with_frame(canvas, complete, (rbox[0] + 20, y0 + 58, rbox[2] - 20, y1 - 18))

    # Row 3: the assembly-tree subcomponent shares the same released drawing.
    y0, y1 = rows[2]
    lbox, rbox = (left[0], y0, left[2], y1), (right[0], y0, right[2], y1)
    panel(draw, lbox, "3. 低葉片數子組件｜無獨立零件圖")
    panel(draw, rbox, "目前失敗｜子組件實際仍包含輪毂與 7 片葉片")
    paste_with_frame(canvas, original, (lbox[0] + 20, y0 + 58, lbox[2] - 20, y1 - 105))
    draw.text((65, y1 - 93), "BLADE-AC08_1 沒有獨立歷史圖，只能回對整組 1AC085000H-R02。", fill=YELLOW, font=font(21, True))
    draw.text((65, y1 - 58), "所以這筆只能證明幾何是薄壁，不能證明工程師會選擇標厚度。", fill=RED, font=font(21, True))
    paste_with_frame(canvas, low_count, (rbox[0] + 20, y0 + 58, rbox[2] - 20, y1 - 18))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=95)

    report = {
        "schema_version": 1,
        "historical_drawing": str(Path(args.historical_sheet)),
        "cases": [
            {
                "case": "synthetic_annular_cylindrical_wall",
                "historical_annotation": None,
                "reason": "synthetic benchmark control",
                "typical_annotation": "section view with outer/inner diameters or radial wall thickness",
            },
            {
                "case": "real_complete_radial_blade_assembly",
                "historical_annotation": "1AC085000H-R02",
                "direct_blade_thickness_dimension": False,
                "actual_annotations": ["angular partitions", "diameters", "radii", "E-E hub section"],
            },
            {
                "case": "real_low_count_radial_blade_component",
                "historical_annotation": "shared assembly drawing 1AC085000H-R02",
                "independent_part_drawing": False,
                "direct_blade_thickness_dimension": False,
            },
        ],
        "conclusion": "The two real false negatives are geometry-feature recall failures, not proven historical dimension-selection failures.",
    }
    report_path = output.with_name("comparison_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-sheet", required=True)
    parser.add_argument("--cylindrical-debug", required=True)
    parser.add_argument("--complete-debug", required=True)
    parser.add_argument("--low-count-debug", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    render(args)


if __name__ == "__main__":
    main()
