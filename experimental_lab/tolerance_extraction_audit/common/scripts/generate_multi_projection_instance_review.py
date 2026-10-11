"""Render the drawing-level multi projection instance audit board."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from run_projection_pairing_v2 import bounds_of_primitives, make_mapper


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
BLUE = "#60a5fa"
ORANGE = "#f97316"
YELLOW = "#facc15"
GREEN = "#22c55e"


def font(size):
    for path in (Path("C:/Windows/Fonts/consola.ttf"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _nearest_candidate(extractor, bbox):
    def error(candidate):
        return sum(abs(float(candidate.bbox[index]) - float(bbox[index])) for index in range(4))
    return min(extractor.view_candidates, key=error)


def _render_full(extractor, views, size=(1700, 760)):
    image = Image.new("RGB", size, PANEL)
    draw = ImageDraw.Draw(image)
    mapper = make_mapper(bounds_of_primitives(extractor.all_primitives), (25, 25, size[0]-25, size[1]-25))
    for primitive in extractor.all_primitives:
        points = [mapper(point) for point in primitive.points]
        if len(points) >= 2:
            draw.line(points, fill="#666666", width=1)
    colors = (BLUE, ORANGE, YELLOW, GREEN)
    for index, view in enumerate(views):
        bbox = view["dxf_bbox"]
        p1, p2 = mapper((bbox[0], bbox[1])), mapper((bbox[2], bbox[3]))
        box = (min(p1[0],p2[0]), min(p1[1],p2[1]), max(p1[0],p2[0]), max(p1[1],p2[1]))
        draw.rectangle(box, outline=colors[index % len(colors)], width=5)
        draw.text((box[0]+6, box[1]+6), f"V{index+1}", font=font(24), fill=colors[index % len(colors)])
    return image


def _render_instance(extractor, view, hypotheses, bindings, overlays, size=(820, 660)):
    image = Image.new("RGB", size, PANEL)
    draw = ImageDraw.Draw(image)
    best = max(hypotheses, key=lambda item: float(item.get("contour_score", 0.0) or 0.0))
    candidate = _nearest_candidate(extractor, view["dxf_bbox"])
    best_binding = next(item for item in bindings if item.get("instance_id") == best["instance_id"])
    overlay = overlays.get(best_binding.get("registered_step_projection_id"), {})
    points = list(candidate.points)
    for line in overlay.get("lines") or []:
        points.extend((tuple(line["p1"]), tuple(line["p2"])))
    bbox = view["dxf_bbox"]
    mapper = make_mapper(tuple(bbox), (30, 70, size[0]-30, size[1]-70))
    for point in candidate.points:
        x, y = mapper(point)
        draw.ellipse((x-2, y-2, x+2, y+2), fill=BLUE)
    for line in overlay.get("lines") or []:
        draw.line((mapper(line["p1"]), mapper(line["p2"])), fill=ORANGE, width=2)
    handles = ", ".join(view.get("dimension_handles") or [])
    draw.text((20, 15), f"{view['drawing_view_instance_id']} | {handles}", font=font(18), fill=WHITE)
    draw.text(
        (20, 40),
        f"best={best.get('step_view')} rot={best.get('transform',{}).get('rotation_degrees',0):.2f} "
        f"mirror={best.get('transform',{}).get('mirrored')} contour={best.get('contour_score',0):.4f}",
        font=font(16), fill=MUTED,
    )
    draw.text((20, size[1]-42), f"BLUE DXF | ORANGE STEP HLR | hypotheses={len(hypotheses)}", font=font(16), fill=YELLOW)
    return image


def run(index_path: Path, radial_path: Path, output: Path):
    index = json.loads(index_path.read_text(encoding="utf-8"))
    radial = json.loads(radial_path.read_text(encoding="utf-8"))
    extractor = DxfProjectionViewExtractorV2(index["drawing"])
    views = index["drawing_view_instances"]
    hypotheses = {item["instance_id"]: item for item in index["projection_instances"]}
    bindings = index["dimension_bindings"]
    board = Image.new("RGB", (1740, 1580), BACKGROUND)
    draw = ImageDraw.Draw(board)
    draw.text((25, 18), "AL0W BLADE | MULTI PROJECTION INSTANCE REVIEW", font=font(30), fill=WHITE)
    draw.text((25, 58), "One drawing keeps multiple view instances; each tolerance binds locally.", font=font(20), fill=YELLOW)
    board.paste(_render_full(extractor, views), (20, 95))
    for index_number, view in enumerate(views):
        view_hypotheses = [hypotheses[item] for item in view["projection_hypothesis_ids"]]
        image = _render_instance(extractor, view, view_hypotheses, bindings, index["projection_overlays"])
        board.paste(image, (20 + index_number*850, 875))
        draw.text((35 + index_number*850, 1545), f"V{index_number+1}: {', '.join(view['dimension_handles'])}", font=font(17), fill=GREEN)
    output.parent.mkdir(parents=True, exist_ok=True)
    board.save(output)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--radial", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(run(args.index.resolve(), args.radial.resolve(), args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
