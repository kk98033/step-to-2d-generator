from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "msjhbd.ttc" if bold else "msjh.ttc"
    path = Path("C:/Windows/Fonts") / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    return copy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shaft", required=True)
    parser.add_argument("--impeller", required=True)
    parser.add_argument("--dish", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    cases = [
        (
            "1. 階梯實心軸",
            "Main: solid_shaft_features｜11 intents｜Renderer: 11/11 AUTO_VERIFIED",
            Image.open(args.shaft).convert("RGB"),
            "#22c55e",
        ),
        (
            "2. 1AC085000H 放射葉片組件",
            "Main: radial/annular relations｜35 intents｜Renderer: 20°、30°、50°",
            Image.open(args.impeller).convert("RGB"),
            "#f97316",
        ),
        (
            "3. DISH 非風扇薄壁件",
            "Main: paired_surface_thickness｜27 intents｜Renderer: 0.4 mm REVIEW_CANDIDATE",
            Image.open(args.dish).convert("RGB"),
            "#22d3ee",
        ),
    ]

    canvas = Image.new("RGB", (2400, 2140), "#171717")
    draw = ImageDraw.Draw(canvas)
    draw.text((42, 28), "Experimental Main v0.18｜繪圖 Smoke Gallery", fill="#f4f4f5", font=load_font(42, True))
    draw.text(
        (42, 82),
        "Main 負責幾何辨識與 DimensionIntent；圖面由目前已驗證的類型 renderer 輸出。",
        fill="#a1a1aa",
        font=load_font(24),
    )

    top = 135
    panel_h = 635
    for index, (title, subtitle, source, colour) in enumerate(cases):
        y0 = top + index * (panel_h + 30)
        box = (32, y0, 2368, y0 + panel_h)
        draw.rounded_rectangle(box, radius=13, fill="#101010", outline="#3f3f46", width=2)
        draw.text((55, y0 + 18), title, fill="#f4f4f5", font=load_font(30, True))
        draw.text((55, y0 + 59), subtitle, fill=colour, font=load_font(22, True))
        picture = fit(source, (2250, 520))
        px = 1200 - picture.width // 2
        py = y0 + 100 + (520 - picture.height) // 2
        canvas.paste(picture, (px, py))
        draw.rectangle((px, py, px + picture.width, py + picture.height), outline="#52525b", width=2)

    footer_y = 2100
    draw.text(
        (42, footer_y),
        "結論：各類型 renderer 已可輸出；統一視圖選擇、意圖篩選與完整 DXF 編排尚未整合。",
        fill="#facc15",
        font=load_font(22, True),
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=95)


if __name__ == "__main__":
    main()
