"""
自動 2D 工程圖繪製系統 — 主程式入口
讀取 3D STEP 模型 → 自動產生符合 FORCECON 標準的 2D 工程圖 (DXF/PDF/PNG)

使用方式:
    conda activate pyoccenv
    python main.py
"""
import os
import sys
import json

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from step_reader import load_step
from feature_extractor import FeatureExtractor
from view_projector import ViewProjector
from dxf_drawer import DrawingLayout, DxfDrawer
from dimension_engine import DimensionEngine
from title_block import setup_document, TitleBlock
from pdf_exporter import export_pdf, export_png
from config import OUTPUT_DIR, MODELS_DIR, VIEW_CONFIG


def generate_drawing(step_path, output_name=None,
                     part_name="---", drawing_no="---",
                     revision="R00", material="---", model_code="---"):
    """
    主流程: STEP → 2D 工程圖
    
    Args:
        step_path: STEP 檔案路徑
        output_name: 輸出檔名 (不含副檔名)
        part_name: 品名 (中文)
        drawing_no: 料號
        revision: 版次
        material: 材質
        model_code: 產品系列碼
    """
    if output_name is None:
        output_name = os.path.splitext(os.path.basename(step_path))[0]

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"{'='*60}")
    print(f"  自動 2D 工程圖繪製系統")
    print(f"  輸入: {step_path}")
    print(f"  品名: {part_name}  料號: {drawing_no}")
    print(f"{'='*60}")

    # === 階段 1: 讀取 STEP ===
    print("\n[1/6] 讀取 STEP 檔案...")
    shape = load_step(step_path)
    print("  ✓ STEP 讀取成功")

    # === 階段 2: 提取幾何特徵 ===
    print("[2/6] 提取幾何特徵...")
    features = FeatureExtractor(shape)
    summary = features.summary()
    
    from part_classifier import PartClassifier
    classifier = PartClassifier()
    part_type = classifier.classify(features)
    
    print(f"  ✓ Bounding Box: {summary['bounding_box']}")
    print(f"  ✓ 規格: {summary['spec']}")
    print(f"  ✓ 孔洞: {summary['holes_count']}個, 軸: {summary['shafts_count']}個, 圓角: {summary['fillets_count']}個 (分類: {part_type})")

    # 儲存特徵資料
    feat_path = os.path.join(OUTPUT_DIR, f"{output_name}_features.json")
    with open(feat_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # === 階段 3: HLR 三視圖投影 ===
    print("[3/6] HLR 三視圖投影...")
    projector = ViewProjector()
    cut_half_right = (part_type == "FAN")
    view_data = projector.project_all_views(shape, cut_half_right=cut_half_right)
    for vn, vd in view_data.items():
        vis_count = len(vd['visible'])
        hid_count = len(vd['hidden'])
        w, h = vd['size']
        print(f"  ✓ {vn}: {vis_count} 可見邊 + {hid_count} 隱藏邊, 尺寸 {w:.1f}×{h:.1f}")

    # === 階段 4: 建立 DXF 文件 ===
    print("[4/6] 建立 DXF 文件...")
    doc = setup_document()
    msp = doc.modelspace()

    # 佈局計算
    view_sizes = {vn: vd['size'] for vn, vd in view_data.items()}
    layout = DrawingLayout(view_sizes)
    print(f"  ✓ 比例: {layout.get_scale_text()}")

    # 圖框
    title_block = TitleBlock()
    title_block.draw(msp,
                     part_name=part_name,
                     drawing_no=drawing_no,
                     revision=revision,
                     scale_text=layout.get_scale_text(),
                     material=material,
                     model_code=model_code)
    print("  ✓ 圖框與標題欄繪製完成")

    # === 階段 5: 繪製三視圖 + 標註 ===
    print("[5/6] 繪製三視圖與標註...")
    drawer = DxfDrawer()
    for vn in ['front', 'top', 'right']:
        ox, oy = layout.get_view_offset(vn)
        sw, sh = layout.get_scaled_size(vn)
        vd = view_data[vn]
        bbox_x0, bbox_y0 = vd['bbox'][0], vd['bbox'][1]

        # 繪製可見邊 (實線)
        drawer.draw_edges(msp, vd['visible'], ox, oy, layout.scale, bbox_x0, bbox_y0, 'VISIBLE')
        # 繪製隱藏邊 (虛線)
        drawer.draw_edges(msp, vd['hidden'], ox, oy, layout.scale, bbox_x0, bbox_y0, 'HIDDEN')
        # 視圖標籤
        drawer.draw_view_label(msp, ox, oy, VIEW_CONFIG[vn]["label"])

    # 尺寸標註
    dim_engine = DimensionEngine(features, layout)
    dim_engine.annotate_all_views(msp, view_data=view_data)
    print("  ✓ 三視圖與標註完成")

    # === 階段 6: 輸出 ===
    print("[6/6] 輸出 DXF/PDF/PNG...")
    dxf_path = os.path.join(OUTPUT_DIR, f"{output_name}.dxf")
    pdf_path = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")
    png_path = os.path.join(OUTPUT_DIR, f"{output_name}.png")

    doc.saveas(dxf_path)
    print(f"  ✓ DXF: {dxf_path}")

    export_pdf(doc, msp, pdf_path, dark_bg=True)
    export_png(doc, msp, png_path, dark_bg=True)

    # === 額外: 獨立視圖輸出 ===
    print("  >> 生成獨立視圖...")
    export_single_views(shape, features, view_data, OUTPUT_DIR, output_name)

    print(f"\n{'='*60}")
    print(f"  完成! 輸出檔案:")
    print(f"    DXF: {dxf_path}")
    print(f"    PDF: {pdf_path}")
    print(f"    PNG: {png_path}")
    print(f"    + 獨立視圖: {output_name}_front/top/right.pdf")
    print(f"{'='*60}")

    return dxf_path, pdf_path, png_path


def export_single_views(shape, features, view_data, output_dir, output_name):
    """
    為每個三視圖 (front, top, right) 各自匯出一份獨立的 PDF/PNG。
    每張只包含該視圖的輪廓 + 標註。
    
    Args:
        shape: TopoDS_Shape (3D 模型)
        features: FeatureExtractor 實例
        view_data: project_all_views() 的回傳值
        output_dir: 輸出目錄
        output_name: 基礎檔名
    """
    os.makedirs(output_dir, exist_ok=True)
    view_labels = {
        'front': '前視圖 FRONT VIEW',
        'top': '俯視圖 TOP VIEW',
        'right': '右側視圖 RIGHT VIEW',
    }
    
    for vn in ['front', 'top', 'right']:
        vd = view_data[vn]
        w, h = vd['size']
        
        # 建立獨立 DXF 文件 (不帶圖框)
        doc = setup_document()
        msp = doc.modelspace()
        
        # 單一視圖佈局: 只有一個視圖
        # 用原始 view_sizes 來計算 scale，但只放一個視圖
        single_view_sizes = {vn: vd['size'], 'front': vd['size'], 'top': (0.01, 0.01), 'right': (0.01, 0.01)}
        if vn == 'top':
            single_view_sizes = {'front': vd['size'], 'top': (0.01, 0.01), 'right': (0.01, 0.01)}
        if vn == 'right':
            single_view_sizes = {'front': vd['size'], 'top': (0.01, 0.01), 'right': (0.01, 0.01)}
        # 使用 front 的大小作為主視圖大小計算 scale
        single_view_sizes = {'front': vd['size'], 'top': (0.01, 0.01), 'right': (0.01, 0.01)}
        layout = DrawingLayout(single_view_sizes)
        
        # 繪製邊緣
        ox, oy = layout.get_view_offset('front')  # 用 front 位置 (因為只有一個)
        sw, sh_scaled = layout.get_scaled_size('front')
        bbox_x0, bbox_y0 = vd['bbox'][0], vd['bbox'][1]
        
        drawer = DxfDrawer()
        drawer.draw_edges(msp, vd['visible'], ox, oy, layout.scale, bbox_x0, bbox_y0, 'VISIBLE')
        drawer.draw_edges(msp, vd['hidden'], ox, oy, layout.scale, bbox_x0, bbox_y0, 'HIDDEN')
        drawer.draw_view_label(msp, ox, oy, view_labels[vn])
        
        # 標註 — 使用單獨的 DimensionEngine 只標這一個視圖
        dim_engine = DimensionEngine(features, layout)
        
        # 由於是單一視圖，LayoutEngine 認為該視圖位於 ox, oy (front 的位置)
        # 呼叫 annotate_view 並覆蓋排版座標
        part_type = dim_engine.classifier.classify(features)
        extractor = dim_engine._get_extractor(part_type)
        dim_engine.annotate_view(msp, vn, vd, extractor, override_offset=(ox, oy))
        
        # 不加圖框 — 白底視圖 + 標註
        
        # 輸出
        view_fname = f"{output_name}_{vn}"
        dxf_path = os.path.join(output_dir, f"{view_fname}.dxf")
        pdf_path = os.path.join(output_dir, f"{view_fname}.pdf")
        png_path = os.path.join(output_dir, f"{view_fname}.png")
        
        doc.saveas(dxf_path)
        export_pdf(doc, msp, pdf_path, dark_bg=True)
        export_png(doc, msp, png_path, dark_bg=True)
        print(f"  ✓ 獨立視圖: {pdf_path}")


def main():
    """測試入口 — 使用 BLADE_ASSY 模型"""
    step_file = os.path.join(MODELS_DIR, "BLADE_ASSY-1AC085000H-R01.stp")

    if not os.path.exists(step_file):
        print(f"ERROR: 找不到測試模型: {step_file}")
        print(f"請確認 models 目錄中有 BLADE_ASSY-1AC085000H-R01.stp")
        return

    generate_drawing(
        step_path=step_file,
        output_name="BLADE_ASSY-1AC085000H-R01_drawing",
        part_name="葉片組合",
        drawing_no="1AC085000H",
        revision="R02",
        material="PBT+30%GF",
        model_code="AC08",
    )


if __name__ == "__main__":
    main()
