"""Render a focused FLBN comparison between the engineer drawing and experiment 13."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BACKGROUND = "#111111"
PANEL = "#171717"
GRID = "#404040"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
CYAN = "#22d3ee"
ORANGE = "#f97316"
RED = "#ef4444"
GREEN = "#22c55e"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _fit(canvas: Image.Image, source: Image.Image, box: tuple[int, int, int, int]) -> None:
    image = source.copy()
    width, height = box[2] - box[0], box[3] - box[1]
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas.paste(image, (box[0] + (width - image.width) // 2, box[1] + (height - image.height) // 2))


def render(original_path: Path, current_path: Path, output_path: Path) -> None:
    with Image.open(original_path) as source:
        original = source.convert("RGB")
    with Image.open(current_path) as source:
        current = source.convert("RGB")

    # The upper crop preserves both circular engineer views and the native callouts.
    engineer_views = original.crop((1050, 300, 2920, 1320))
    # Notes 17 and 21 carry the actual blade-angle and blade-count intent.
    engineer_notes = original.crop((260, 760, 1110, 1450))

    canvas = Image.new("RGB", (4400, 2200), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 28), "FLBN historical annotation review", font=_font(42, True), fill=WHITE)
    draw.text(
        (45, 85),
        "Original: 1FL0N5000H-R00.dxf  |  Experiment: curved_radial_feature_v1",
        font=_font(23),
        fill=MUTED,
    )

    left = (35, 140, 2190, 2145)
    right = (2210, 140, 4365, 2145)
    draw.rectangle(left, fill=PANEL, outline=GRID, width=3)
    draw.rectangle(right, fill=PANEL, outline=GRID, width=3)
    draw.text((65, 170), "ENGINEER-AUTHORED DRAWING", font=_font(27, True), fill=GREEN)
    draw.text((2240, 170), "CURRENT EXPERIMENT 13 OUTPUT", font=_font(27, True), fill=ORANGE)

    draw.rectangle((65, 225, 2160, 1375), fill="#202a33", outline=GRID, width=2)
    _fit(canvas, engineer_views, (85, 245, 2140, 1355))
    draw.text((85, 1395), "Native blade-related notes", font=_font(23, True), fill=CYAN)
    draw.rectangle((65, 1440, 1120, 2110), fill="#202a33", outline=GRID, width=2)
    _fit(canvas, engineer_notes, (85, 1460, 1100, 2090))

    findings = [
        ("Note 17", "Inlet angle 40 deg; outlet angle 108 deg", GREEN),
        ("Note 21", "Blade count: 61", GREEN),
        ("Pitch", "No 5 deg / 10 deg blade-pitch callout", RED),
        ("Style", "Angles are specification notes, not ring-wide arcs", WHITE),
    ]
    y = 1460
    for label, text, color in findings:
        draw.text((1160, y), label, font=_font(22, True), fill=color)
        draw.text((1160, y + 35), text, font=_font(20), fill=WHITE)
        y += 135

    draw.rectangle((2240, 225, 4335, 1500), fill="#0b0b0b", outline=GRID, width=2)
    _fit(canvas, current, (2260, 245, 4315, 1480))
    draw.text((2260, 1530), "AUDIT RESULT", font=_font(24, True), fill=RED)
    audit_lines = [
        "GEOMETRY TRIGGER: PASS (repeated curved radial boundaries found)",
        "ANNOTATION SEMANTICS: FAIL (pitch was selected without intent evidence)",
        "144 boundaries -> 72 elements conflicts with the drawing's 61-blade note",
        "5 deg / 10 deg must remain suppressed; do not count this as a correct render",
        "Next detector: blade profile inlet/outlet tangent angles + pattern count note",
    ]
    y = 1580
    for index, line in enumerate(audit_lines):
        color = GREEN if index == 0 else RED if index in (1, 3) else WHITE
        draw.text((2260, y), line, font=_font(19, index in (0, 1)), fill=color)
        y += 92

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.original, args.current, args.output)


if __name__ == "__main__":
    main()
