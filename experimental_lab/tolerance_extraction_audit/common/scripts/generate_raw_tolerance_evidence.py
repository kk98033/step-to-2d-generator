"""Create an evidence-only audit pack for one STEP/DXF tolerance match.

Unlike the older presentation diagrams, this script does not invent candidate
scores or intermediate registration frames.  Every panel is derived from an
input file, a persisted pipeline artefact, or an explicitly labelled fresh
recalculation.
"""

from __future__ import annotations

import hashlib
import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import numpy as np
from PIL import Image, ImageDraw, ImageFont

LAB_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dxf_structure_2d_snapshot import DxfStructure2DAnalyzer

MODEL = "1AL0W5000H-R03"
SUCCESS_ID = "HIST2_1AL0W5000H-R03_789b2fee_feat_cyl_01_diameter"
FAILURE_ID = "DXF2_1AL0W5000H-R03_789b2fee_7AB1"
CASE_DB = LAB_ROOT / "benchmarks/snapshots/feature_case_base_snapshot.json"
OUT = LAB_ROOT / "outputs" / MODEL / "latest"
PROJECTION_ONLY = OUT / "projection_only"
STEP = Path()
DXF = Path()
FONT = Path("C:/Windows/Fonts/msjh.ttc")
FONT_BOLD = Path("C:/Windows/Fonts/msjhbd.ttc")
INK, BLUE, GREY, LIGHT, BORDER, RED = "#172033", "#23466d", "#596779", "#f4f6f8", "#cbd3dc", "#a33a32"


def font(size: int, bold: bool = False):
    path = FONT_BOLD if bold else FONT
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def page(title: str, subtitle: str, size=(1800, 1100)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.text((55, 35), title, fill=INK, font=font(35, True))
    draw.text((57, 88), subtitle, fill=GREY, font=font(19))
    draw.line((55, 130, size[0] - 55, 130), fill=BORDER, width=2)
    return image, draw


def fit_image(source: Image.Image, box):
    x0, y0, x1, y1 = box
    copy = source.convert("RGB")
    copy.thumbnail((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    return copy, (x0 + (x1 - x0 - copy.width) // 2, y0 + (y1 - y0 - copy.height) // 2)


def save_json_image(path: Path, title: str, subtitle: str, data):
    image, draw = page(title, subtitle, (1800, 1250))
    text = json.dumps(data, ensure_ascii=False, indent=2)
    y = 155
    mono = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 17)
    for line in text.splitlines():
        while len(line) > 145:
            draw.text((65, y), line[:145], fill=INK, font=mono)
            line = "    " + line[145:]
            y += 23
        draw.text((65, y), line, fill=INK, font=mono)
        y += 23
        if y > 1210:
            break
    image.save(path)


def export_true_step_render(stl_path: Path, png_path: Path):
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.STEPControl import STEPControl_Reader
    from OCC.Core.StlAPI import StlAPI_Writer
    import vtk

    reader = STEPControl_Reader()
    if reader.ReadFile(str(STEP)) != IFSelect_RetDone:
        raise RuntimeError(f"Cannot read STEP: {STEP}")
    reader.TransferRoots()
    shape = reader.OneShape()
    BRepMesh_IncrementalMesh(shape, 0.25, False, 0.35, True).Perform()
    if not StlAPI_Writer().Write(shape, str(stl_path)):
        raise RuntimeError("STL export failed")

    stl = vtk.vtkSTLReader()
    stl.SetFileName(str(stl_path))
    stl.Update()
    poly = stl.GetOutput()
    points = [poly.GetPoint(i) for i in range(poly.GetNumberOfPoints())]
    triangles = []
    for i in range(poly.GetNumberOfCells()):
        cell = poly.GetCell(i)
        if cell.GetNumberOfPoints() == 3:
            triangles.append([cell.GetPointId(j) for j in range(3)])
    # Orthographic isometric view computed only from the exported mesh vertices.
    # Keep this scalar: some Windows VTK builds load a BLAS runtime that clashes
    # with NumPy's matrix-multiply DLL in the same process.
    az, el = math.radians(35), math.radians(28)
    caz, saz, cel, sel = math.cos(az), math.sin(az), math.cos(el), math.sin(el)
    center = tuple(sum(p[axis] for p in points) / len(points) for axis in range(3))
    transformed = []
    for point in points:
        x, y, z = (point[i] - center[i] for i in range(3))
        x1, y1 = caz * x - saz * y, saz * x + caz * y
        transformed.append((x1, cel * y1 - sel * z, sel * y1 + cel * z))
    xs, ys = [p[0] for p in transformed], [p[1] for p in transformed]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    scale = min(1420 / max(span_x, 1e-9), 790 / max(span_y, 1e-9))
    screen = [(900 + p[0] * scale, 590 - p[1] * scale) for p in transformed]
    order = sorted(range(len(triangles)), key=lambda i: sum(transformed[j][2] for j in triangles[i]) / 3)
    image, draw = page("01  原始 STEP 實體的真實網格渲染", "證據來源：STEP → OpenCASCADE triangulation → STL；未使用手工幾何圖形")
    stride = max(1, len(order) // 70000)
    rendered = 0
    for idx in order[::stride]:
        ids = triangles[idx]
        a, b, c = (transformed[i] for i in ids)
        u, v = tuple(b[i] - a[i] for i in range(3)), tuple(c[i] - a[i] for i in range(3))
        normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        norm = math.sqrt(sum(value * value for value in normal))
        light = 0.55 if norm == 0 else 0.35 + 0.55 * abs(normal[2] / norm)
        base = tuple(int(a*light + b*(1-light)) for a, b in zip((75,105,135), (160,165,170)))
        draw.polygon([screen[i] for i in ids], fill=base, outline=(72, 82, 92))
        rendered += 1
    draw.text((58, 1040), f"STEP: {STEP} | mesh points={len(points):,}, triangles={len(triangles):,}, rendered={rendered:,}", fill=GREY, font=font(16))
    image.save(png_path)
    return {"mesh_points": len(points), "mesh_triangles": len(triangles), "rendered_triangles": rendered}


def make_hlr_contact_sheet(path: Path):
    image, draw = page("02  純 3D-to-2D 投影幾何", "只讀取 VISIBLE/HIDDEN；排除 DIM、TEXT、CENTER、BORDER 與特徵疊圖")
    names = ["front", "top", "right"]
    for i, name in enumerate(names):
        source = PROJECTION_ONLY / f"{MODEL}_{name}_projection_only.png"
        x0 = 45 + i * 585
        draw.rounded_rectangle((x0, 155, x0 + 550, 1000), 8, fill=LIGHT, outline=BORDER, width=2)
        draw.text((x0 + 20, 175), name.upper(), fill=INK, font=font(24, True))
        preview, pos = fit_image(Image.open(source), (x0 + 20, 225, x0 + 530, 930))
        image.paste(preview, pos)
        draw.text((x0 + 20, 950), source.name, fill=GREY, font=font(14))
    image.save(path)


def primitive_points(item, samples=180):
    if item.center is not None and item.radius > 0:
        return [(item.center[0] + item.radius * math.cos(2 * math.pi * i / samples), item.center[1] + item.radius * math.sin(2 * math.pi * i / samples)) for i in range(samples + 1)]
    return list(item.points)


def draw_entity_audit(path: Path, analyzer, success, failure, failed_association):
    image, draw = page("03  DXF 尺寸端點與原始實體", "所有線段/圓形都由原始 DXF 實體座標繪製；標籤為 DXF handle")
    success_association = success["source_metadata"]["geometry_verification"]["association"]
    panels = [
        ("PASS: Ø35 +0.2/-0.0", success_association),
        ("FAIL: 17.3 ±0.04", failed_association),
    ]
    for pi, (label, association) in enumerate(panels):
        box = (60 + pi * 870, 165, 860 + pi * 870, 1000)
        draw.rounded_rectangle(box, 8, fill="white", outline=BORDER, width=2)
        draw.text((box[0] + 22, box[1] + 20), label, fill=INK, font=font(25, True))
        handles = association["attached_geometry_handles"]
        primitives = [analyzer.primitives[analyzer.handle_index[h]] for h in handles if h in analyzer.handle_index]
        geometry = [primitive_points(p) for p in primitives]
        refs = [(item["definition_point"], tuple(item["point"])) for item in association["definition_point_links"]]
        allpts = [p for line in geometry for p in line] + [point for _, point in refs]
        minx, miny = np.min(allpts, axis=0); maxx, maxy = np.max(allpts, axis=0)
        padx, pady = max((maxx-minx)*.25, 2), max((maxy-miny)*.25, 2)
        minx -= padx; maxx += padx; miny -= pady; maxy += pady
        scale = min(700 / max(maxx-minx, 1e-9), 620 / max(maxy-miny, 1e-9))
        def project(p):
            return (box[0]+400+(p[0]-(minx+maxx)/2)*scale, box[1]+450-(p[1]-(miny+maxy)/2)*scale)
        for primitive, line in zip(primitives, geometry):
            draw.line([project(p) for p in line], fill=BLUE, width=4)
            anchor = project(line[len(line)//2])
            draw.text((anchor[0]+8, anchor[1]+4), primitive.handle, fill=BLUE, font=font(16, True))
        for i, (name, p) in enumerate(refs):
            q = project(p)
            draw.ellipse((q[0]-7,q[1]-7,q[0]+7,q[1]+7), fill=RED)
            draw.text((q[0]+10,q[1]-24), f"{name} {p}", fill=RED, font=font(14))
        status = f"{association['association_status']} / " + ("same profile" if association["common_attachment_geometry_groups"] else "no common handle/profile")
        draw.text((box[0]+22, box[3]-55), status, fill=GREY, font=font(17))
    image.save(path)


def load_config(path: Path):
    # Accept JSON written by both modern PowerShell and Windows PowerShell 5.1.
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    required = ("model", "step_path", "dxf_path", "success_case_id", "failure_case_id")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"Missing config fields: {', '.join(missing)}")
    return config


def main():
    parser = argparse.ArgumentParser(description="Generate an evidence-only tolerance extraction audit pack")
    parser.add_argument(
        "--config",
        type=Path,
        default=LAB_ROOT / "common/config/local.json",
        help="Experiment JSON config (default: config/local.json)",
    )
    args = parser.parse_args()
    config = load_config(args.config.resolve())

    global MODEL, SUCCESS_ID, FAILURE_ID, CASE_DB, OUT, PROJECTION_ONLY, STEP, DXF
    MODEL = config["model"]
    SUCCESS_ID = config["success_case_id"]
    FAILURE_ID = config["failure_case_id"]
    STEP = Path(config["step_path"])
    DXF = Path(config["dxf_path"])
    CASE_DB = Path(config.get("case_database", LAB_ROOT / "benchmarks/snapshots/feature_case_base_snapshot.json"))
    OUT = Path(config.get("output_dir", LAB_ROOT / "outputs" / MODEL / "latest"))
    PROJECTION_ONLY = OUT / "projection_only"

    if not STEP.exists() or not DXF.exists():
        raise FileNotFoundError(f"Original pair required. STEP={STEP.exists()} DXF={DXF.exists()}")
    OUT.mkdir(parents=True, exist_ok=True)
    cases = json.loads(CASE_DB.read_text(encoding="utf-8"))
    success = next(item for item in cases if item["case_id"] == SUCCESS_ID)
    failure = next(item for item in cases if item["case_id"] == FAILURE_ID)

    doc = ezdxf.readfile(DXF)
    analyzer = DxfStructure2DAnalyzer(doc.modelspace())
    fm = failure["source_metadata"]
    failed_dimension = SimpleNamespace(nominal_value=17.3, dimension_category="LINEAR", points=fm["points"], association_metadata={})
    failed_association = analyzer.analyze_dimension(failed_dimension)

    mesh_stats = export_true_step_render(OUT / "01_actual_model.stl", OUT / "01_actual_step_3d_render.png")
    make_hlr_contact_sheet(OUT / "02_projection_only_evidence.png")
    draw_entity_audit(OUT / "03_actual_dxf_entities.png", analyzer, success, failure, failed_association)

    sm = success["source_metadata"]
    geo = sm["geometry_verification"]
    save_json_image(OUT / "04_persisted_registration_json.png", "04  持久化的投影對位結果", "這是資料庫中的實際數值；當次 ICP 點雲與每輪畫面未被保存，因此不補畫 overlay", geo["view_registration"])
    success_excerpt = {
        "case_id": success["case_id"], "verification_status": success["verification_status"],
        "feature_type": success["feature_type"], "matched_feature_id": sm["matched_feature_id"],
        "entity_handle": sm["entity_handle"], "nominal_dimensions": success["nominal_dimensions"],
        "association": geo["association"], "local_feature_evidence": geo["local_feature_evidence"],
        "topology_mapping": geo.get("topology_mapping"), "checks": geo["checks"], "score": geo["score"],
    }
    save_json_image(OUT / "05_actual_success_json.png", "05  成功案例：資料庫原始證據", "無自行配分；只擷取 feature_case_base.json 的字段", success_excerpt)
    failure_excerpt = {
        "case_id": failure["case_id"], "verification_status": failure["verification_status"],
        "feature_type": failure["feature_type"], "entity_handle": fm["entity_handle"],
        "nominal_dimensions": failure["nominal_dimensions"], "feature_inference_2d": fm["feature_inference_2d"],
        "association_recomputed_from_current_DXF": failed_association,
    }
    save_json_image(OUT / "06_actual_failure_json.png", "06  失敗案例：原始配分 + 本次重算", "0.20 候選分數來自資料庫；association 來自現行 DxfStructure2DAnalyzer 重算", failure_excerpt)

    (OUT / "05_actual_success_evidence.json").write_text(
        json.dumps(success_excerpt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "06_actual_failure_evidence.json").write_text(
        json.dumps(failure_excerpt, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    gaps = {
        "evidence_matrix": [
            {"artifact": "source STEP", "available": True, "origin": str(STEP)},
            {"artifact": "3D mesh", "available": True, "origin": "regenerated from STEP with OpenCASCADE", **mesh_stats},
            {"artifact": "projection-only views", "available": True, "origin": "direct OpenCASCADE HLR visible/hidden edges"},
            {"artifact": "source DXF entities", "available": True, "origin": str(DXF)},
            {"artifact": "final registration transform", "available": True, "origin": "feature_case_base.json"},
            {"artifact": "exact ICP input point set", "available": False, "origin": "not persisted"},
            {"artifact": "ICP iteration images", "available": False, "origin": "not persisted"},
            {"artifact": "success endpoint association", "available": True, "origin": "feature_case_base.json"},
            {"artifact": "failure endpoint association", "available": True, "origin": "recomputed; labelled as recomputed"},
            {"artifact": "success topology mapping", "available": geo.get("topology_mapping") is not None, "origin": "feature_case_base.json"},
            {"artifact": "failure topology mapping", "available": False, "origin": "feature unresolved; mapping not executed"},
        ]
    }
    save_json_image(OUT / "07_evidence_gap.png", "07  證據完整性與真正斷點", "available=false 表示系統當時沒有儲存；稽核圖不會自行補畫", gaps)
    (OUT / "07_evidence_gap.json").write_text(
        json.dumps(gaps, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    projection_outputs = sorted(PROJECTION_ONLY.glob("*projection_only.*"))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "model": MODEL,
        "policy": "evidence-only; no invented score or synthetic registration overlay",
        "sources": [{"path": str(p), "sha256": sha256(p), "bytes": p.stat().st_size} for p in (STEP, DXF, CASE_DB)],
        "projection_only_outputs": [{"path": str(p.relative_to(OUT)), "sha256": sha256(p), "bytes": p.stat().st_size} for p in projection_outputs],
        "recomputed": {"step_mesh": mesh_stats, "failure_association_method": analyzer.METHOD},
        "not_persisted": ["exact ICP input point set", "ICP iteration frames"],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(
        "# 公差自動提取：可稽核原始證據\n\n"
        "此目錄只放置原始檔、系統實際儲存輸出、資料庫原始 JSON，以及明確標示為本次重算的結果。\n\n"
        "- `01_actual_step_3d_render.png`：由 STEP 實體重新三角網格化，不是手畫外觀。\n"
        "- `02_projection_only_evidence.png`：直接由 STEP 執行 OpenCASCADE HLR 的純投影聯絡表。\n"
        "- `03_actual_dxf_entities.png`：由原始 DXF handle 座標繪製的成功/失敗附著關係。\n"
        "- `04_persisted_registration_json.png`：已保存的最終對位數值；不假裝是當次 overlay。\n"
        "- `05_actual_success_json.png`、`06_actual_failure_json.png`：成功/失敗案例的原始證據。\n"
        "- 同名 `*_evidence.json`：完整機器可讀數值，避免 PNG 版面截斷。\n"
        "- `07_evidence_gap.png`：列出系統有保存和未保存的中間產物。\n"
        "- `manifest.json`：來源路徑、SHA-256 與重算說明。\n",
        encoding="utf-8",
    )
    print(OUT)


if __name__ == "__main__":
    main()
