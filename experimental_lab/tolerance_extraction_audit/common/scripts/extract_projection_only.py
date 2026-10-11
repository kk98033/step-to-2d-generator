"""Run OpenCASCADE HLR directly on STEP and save annotation-free views.

This experiment stops before DxfDrawer, DimensionEngine, LayoutEngine and the
title-block pipeline.  Its outputs contain only ViewProjector's visible and
hidden edge arrays.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ezdxf
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPControl import STEPControl_Reader
from PIL import Image, ImageDraw, ImageFont

from view_projector_snapshot import ViewProjector

LAB_ROOT = Path(__file__).resolve().parents[2]
FONT = Path("C:/Windows/Fonts/msjh.ttc")
FONT_BOLD = Path("C:/Windows/Fonts/msjhbd.ttc")
INK, BLUE, HIDDEN, GREY, BORDER = "#172033", "#23466d", "#8b98a8", "#667085", "#cbd3dc"


def font(size: int, bold: bool = False):
    path = FONT_BOLD if bold else FONT
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_step(path: Path):
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Cannot read STEP: {path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise RuntimeError(f"STEP produced a null shape: {path}")
    return shape


def edge_points(edge):
    if edge["type"] == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(point) for point in edge.get("points", [])]


def save_projection_dxf(view_data, path: Path):
    doc = ezdxf.new("R2010")
    doc.layers.add("VISIBLE", color=7, linetype="CONTINUOUS")
    doc.layers.add("HIDDEN", color=8, linetype="DASHED")
    msp = doc.modelspace()
    for layer, key in (("VISIBLE", "visible"), ("HIDDEN", "hidden")):
        for edge in view_data[key]:
            points = edge_points(edge)
            if len(points) == 2 and edge["type"] == "line":
                msp.add_line(points[0], points[1], dxfattribs={"layer": layer})
            elif len(points) >= 2:
                msp.add_lwpolyline(points, dxfattribs={"layer": layer})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)


def render_projection(view_data, output: Path, model: str, view: str):
    paths = []
    for layer, key in (("VISIBLE", "visible"), ("HIDDEN", "hidden")):
        paths.extend((layer, edge_points(edge)) for edge in view_data[key] if len(edge_points(edge)) >= 2)
    points = [point for _, path in paths for point in path]
    if not points:
        raise RuntimeError(f"HLR returned no geometry for {view}")

    xs, ys = [point[0] for point in points], [point[1] for point in points]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    width, height = 1500, 1000
    scale = min(1360 / max(maxx - minx, 1e-9), 790 / max(maxy - miny, 1e-9))
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

    def project(point):
        return width / 2 + (point[0] - cx) * scale, 555 - (point[1] - cy) * scale

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((55, 30), f"{model} / {view.upper()} / raw HLR projection", fill=INK, font=font(34, True))
    draw.text((57, 82), "STEP -> OpenCASCADE HLR -> visible/hidden edges; annotation pipeline not executed", fill=GREY, font=font(18))
    draw.line((55, 122, width - 55, 122), fill=BORDER, width=2)
    for layer, path in paths:
        draw.line([project(point) for point in path], fill=BLUE if layer == "VISIBLE" else HIDDEN, width=2 if layer == "VISIBLE" else 1)
    draw.text(
        (57, height - 42),
        f"visible={len(view_data['visible'])} | hidden={len(view_data['hidden'])} | bbox={tuple(round(v, 3) for v in view_data['bbox'])}",
        fill=GREY,
        font=font(15),
    )
    image.save(output)


def make_contact_sheet(images, output: Path):
    canvas = Image.new("RGB", (1800, 1120), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 30), "02  Direct STEP-to-2D HLR projection", fill=INK, font=font(35, True))
    draw.text((57, 84), "No DimensionEngine, LayoutEngine, title block, centerline or artificial bbox outline", fill=GREY, font=font(18))
    draw.line((55, 125, 1745, 125), fill=BORDER, width=2)
    for index, (view, path) in enumerate(images):
        x0 = 45 + index * 585
        draw.rounded_rectangle((x0, 155, x0 + 550, 1040), 8, fill="white", outline=BORDER, width=2)
        preview = Image.open(path).convert("RGB")
        preview.thumbnail((520, 810), Image.Resampling.LANCZOS)
        canvas.paste(preview, (x0 + (550 - preview.width) // 2, 205 + (790 - preview.height) // 2))
        draw.text((x0 + 20, 175), view.upper(), fill=INK, font=font(23, True))
    canvas.save(output)


def main():
    parser = argparse.ArgumentParser(description="Generate raw HLR projections directly from STEP")
    parser.add_argument("--config", type=Path, default=LAB_ROOT / "common/config/local.json")
    args = parser.parse_args()
    # PowerShell 5.1 may write UTF-8 JSON with a BOM.  utf-8-sig accepts both
    # BOM and non-BOM files, keeping the experiment runner portable.
    config = json.loads(args.config.resolve().read_text(encoding="utf-8-sig"))
    model = config["model"]
    step_path = Path(config["step_path"])
    output_root = Path(config.get("output_dir", LAB_ROOT / "outputs" / model / "latest"))
    output_dir = output_root / "projection_only"
    output_dir.mkdir(parents=True, exist_ok=True)

    shape = load_step(step_path)
    projector = ViewProjector()
    projected = projector.project_all_views(shape, cut_half_right=False, view_names=["front", "top", "right"])
    report = {
        "model": model,
        "method": "OpenCASCADE HLRBRep_Algo via frozen ViewProjector snapshot",
        "source_step": str(step_path),
        "source_step_sha256": sha256(step_path),
        "annotation_pipeline_executed": False,
        "artificial_bbox_outline_added": False,
        "views": {},
    }
    previews = []
    for view in ("front", "top", "right"):
        data = projected[view]
        dxf_path = output_dir / f"{model}_{view}_projection_only.dxf"
        png_path = output_dir / f"{model}_{view}_projection_only.png"
        save_projection_dxf(data, dxf_path)
        render_projection(data, png_path, model, view)
        report["views"][view] = {
            "visible_edge_count": len(data["visible"]),
            "hidden_edge_count": len(data["hidden"]),
            "bbox": list(data["bbox"]),
            "size": list(data["size"]),
            "dxf_sha256": sha256(dxf_path),
            "png_sha256": sha256(png_path),
        }
        previews.append((view, png_path))
    make_contact_sheet(previews, output_root / "02_projection_only_views.png")
    (output_dir / "projection_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_dir)


if __name__ == "__main__":
    main()
