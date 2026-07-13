"""
批次繪製工程圖 — 從 STEP 組合件中提取所有零件，各自生成工程圖
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from step_reader import load_step, extract_assembly
from feature_extractor import FeatureExtractor
from view_projector import ViewProjector
from dxf_drawer import DrawingLayout, DxfDrawer
from dimension_engine import DimensionEngine
from title_block import setup_document, TitleBlock
from pdf_exporter import export_pdf, export_png
from feature_layer import build_feature_records, build_projected_feature_records, draw_feature_overlay
from config import OUTPUT_DIR, MODELS_DIR, VIEW_CONFIG
from main import export_single_views


def generate_single(step_path, output_dir, output_name,
                    part_name="---", drawing_no="---",
                    revision="R00", material="---", model_code="---"):
    """為單一零件生成工程圖"""
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"\n  >> 處理: {part_name} ({drawing_no})")
    
    # 1. 讀取
    shape = load_step(step_path)
    
    # 2. 特徵提取與分類
    features = FeatureExtractor(shape)
    summary = features.summary()
    
    from part_classifier import PartClassifier
    classifier = PartClassifier()
    part_hint = f"{output_name} {part_name} {drawing_no} {model_code}"
    part_type = classifier.classify(features, part_hint)
    summary["part_type"] = part_type
    
    print(f"     BBox: {summary['bounding_box']['W']:.1f} x {summary['bounding_box']['H']:.1f} x {summary['bounding_box']['D']:.1f}")
    print(f"     孔洞: {summary['holes_count']}, 軸: {summary['shafts_count']} (分類: {part_type})")
    
    # 3. 投影
    projector = ViewProjector()
    cut_half_right = part_type in ("FAN", "FAN_HOUSING")
    if part_type == "FAN_HOUSING":
        view_names = ['front', 'top', 'right', 'left']
    elif part_type == "STAMPED_FAN_BASE":
        view_names = ['front', 'back', 'top', 'right']
    else:
        view_names = ['front', 'top', 'right']
    view_data = projector.project_all_views(shape, cut_half_right=cut_half_right, view_names=view_names)
    
    # 4. DXF
    doc = setup_document()
    msp = doc.modelspace()
    
    view_sizes = {vn: view_data[vn]['size'] for vn in ['front', 'top', 'right'] if vn in view_data}
    layout = DrawingLayout(view_sizes)
    
    # 圖框
    title_block = TitleBlock()
    title_block.draw(msp,
                     part_name=part_name,
                     drawing_no=drawing_no,
                     revision=revision,
                     scale_text=layout.get_scale_text(),
                     material=material,
                     model_code=model_code)
    
    # 5. 繪製三視圖
    drawer = DxfDrawer()
    draw_hidden = part_type not in ("FAN_HOUSING", "STAMPED_FAN_BASE")
    for vn in ['front', 'top', 'right']:
        ox, oy = layout.get_view_offset(vn)
        sw, sh = layout.get_scaled_size(vn)
        vd = view_data[vn]
        bbox_x0, bbox_y0 = vd['bbox'][0], vd['bbox'][1]
        drawer.draw_edges(msp, vd['visible'], ox, oy, layout.scale, bbox_x0, bbox_y0, 'VISIBLE')
        if part_type in ("FAN_HOUSING", "STAMPED_FAN_BASE"):
            drawer.draw_bbox_outline(msp, ox, oy, layout.scale, vd['bbox'], 'VISIBLE')
        if draw_hidden:
            drawer.draw_edges(msp, vd['hidden'], ox, oy, layout.scale, bbox_x0, bbox_y0, 'HIDDEN')
        drawer.draw_view_label(msp, ox, oy, VIEW_CONFIG[vn]["label"])
    
    # 6. 標註 (傳入 view_data 讓標註引擎使用正確的投影尺寸)
    dim_engine = DimensionEngine(features, layout, part_hint=part_hint)
    dim_engine.annotate_all_views(msp, view_data=view_data, part_type=part_type)
    
    # 7. 輸出
    dxf_path = os.path.join(output_dir, f"{output_name}.dxf")
    pdf_path = os.path.join(output_dir, f"{output_name}.pdf")
    png_path = os.path.join(output_dir, f"{output_name}.png")
    
    doc.saveas(dxf_path)
    export_pdf(doc, msp, pdf_path, dark_bg=True)
    export_png(doc, msp, png_path, dark_bg=True)

    feature_records = build_projected_feature_records(view_data, part_type)
    feature_records.extend(build_feature_records(features, part_type))
    feature_json_path = os.path.join(output_dir, f"{output_name}_feature_records.json")
    with open(feature_json_path, 'w', encoding='utf-8') as f:
        json.dump(feature_records, f, indent=2, ensure_ascii=False)
    draw_feature_overlay(msp, layout, feature_records, view_data)
    feature_dxf_path = os.path.join(output_dir, f"{output_name}_features_view.dxf")
    feature_pdf_path = os.path.join(output_dir, f"{output_name}_features_view.pdf")
    doc.saveas(feature_dxf_path)
    export_pdf(doc, msp, feature_pdf_path, dark_bg=True)
    
    # 特徵 JSON
    feat_path = os.path.join(output_dir, f"{output_name}_features.json")
    with open(feat_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"     ✓ DXF: {dxf_path}")
    
    # 額外生成三視圖的獨立檔案
    print(f"     >> 生成獨立視圖...")
    export_single_views(shape, features, view_data, output_dir, output_name, part_type=part_type, part_hint=part_hint)
    
    return dxf_path, pdf_path, png_path


def batch_generate(step_path, output_dir=None, assembly_info=None, progress_cb=None):
    """
    批次生成工程圖: 從組合件拆解出所有零件並各自生成工程圖
    
    Args:
        step_path: STEP 檔案路徑
        output_dir: 輸出目錄
        assembly_info: 組件資訊 dict {part_file_prefix: {name, drawing_no, material, ...}}
        progress_cb: callback 函數 progress_cb(total_parts, current_part, message)
    """
    model_name = os.path.splitext(os.path.basename(step_path))[0]
    if output_dir is None:
        output_dir = os.path.join(OUTPUT_DIR, model_name)
    os.makedirs(output_dir, exist_ok=True)
    
    if assembly_info is None:
        assembly_info = {}
    
    print("=" * 60)
    print("  自動 2D 工程圖繪製系統 — 批次模式")
    print(f"  輸入: {step_path}")
    print("=" * 60)
    
    # 1. 拆解組合件
    print("\n[Step 1] 拆解組合件...")
    if progress_cb: progress_cb(1, 0, "正在拆解組合件 (提取 STL / STP)...")
    parts_dir = os.path.join(output_dir, "_parts")
    result = extract_assembly(step_path, parts_dir)
    parts = result['parts']
    print(f"  ✓ 拆解出 {len(parts)} 個零件")
    
    total_tasks = len(parts) + 1 # +1 for assembly
    
    # 2. 也為整個組合件繪圖
    print("\n[Step 2] 為整個組合件繪圖...")
    if progress_cb: progress_cb(total_tasks, 0, "正在繪製組合件總圖...")
    info = assembly_info.get('_assembly', {})
    generate_single(
        step_path=step_path,
        output_dir=output_dir,
        output_name=f"{model_name}_assembly",
        part_name=info.get('name', model_name),
        drawing_no=info.get('drawing_no', model_name.split('-')[-2] if '-' in model_name else model_name),
        revision=info.get('revision', 'R00'),
        material=info.get('material', '---'),
        model_code=info.get('model_code', '---'),
    )
    
    
    # 3. 為每個拆解零件繪圖
    print(f"\n[Step 3] 為 {len(parts)} 個零件各自繪圖...")
    results = []
    for idx, (part_name, part_path) in enumerate(parts.items()):
        info = assembly_info.get(part_name, {})
        display_name = info.get('name', f'Part_{idx+1}')
        drawing_no = info.get('drawing_no', part_name)
        
        if progress_cb: progress_cb(total_tasks, idx + 1, f"正在處理 {display_name} ({idx+1}/{len(parts)})")
        
        try:
            r = generate_single(
                step_path=part_path,
                output_dir=output_dir,
                output_name=f"{model_name}_part{idx+1}_{part_name}",
                part_name=display_name,
                drawing_no=drawing_no,
                revision=info.get('revision', 'R00'),
                material=info.get('material', '---'),
                model_code=info.get('model_code', '---'),
            )
            results.append((part_name, r))
        except Exception as e:
            print(f"     ✗ 錯誤: {e}")
            results.append((part_name, None))
    
    # 4. 摘要
    success = sum(1 for _, r in results if r is not None)
    print(f"\n{'='*60}")
    print(f"  批次完成!")
    print(f"  總共: {len(parts) + 1} 張工程圖 ({success + 1} 成功)")
    print(f"  輸出目錄: {output_dir}")
    print(f"{'='*60}")
    
    if progress_cb: progress_cb(total_tasks, total_tasks, "處理完成")
    return results


def main():
    """測試批次拆解+繪圖 (支援多模型)"""
    import sys
    
    # 預設使用新的模型
    step_filename = "M24122-3D-R00.stp"
    
    # 若有帶參數，則使用參數
    if len(sys.argv) > 1:
        step_filename = sys.argv[1]
        
    step_file = os.path.join(MODELS_DIR, step_filename)
    
    if not os.path.exists(step_file):
        print(f"ERROR: 找不到模型: {step_file}")
        return
    
    # 基本的組件資訊 (未知的零件會使用預設值)
    assembly_info = {
        '_assembly': {
            'name': '組合件',
            'drawing_no': step_filename.split('.')[0],
            'revision': 'R00',
            'material': '---',
            'model_code': '---',
        }
    }
    
    output_dir_name = step_filename.split('.')[0] + "_batch"
    output_dir = os.path.join(OUTPUT_DIR, output_dir_name)
    batch_generate(step_file, output_dir, assembly_info)


if __name__ == "__main__":
    main()
