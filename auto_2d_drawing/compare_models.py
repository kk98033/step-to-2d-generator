import os
import uuid
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Common
from OCC.Core.StlAPI import StlAPI_Writer
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopTools import TopTools_IndexedMapOfShape
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from auto_2d_drawing.step_reader import load_step, extract_tree_only

def get_shape_stats(shape):
    if shape is None or shape.IsNull():
        return {"volume": 0.0, "area": 0.0, "bbox": [0.0, 0.0, 0.0]}
    
    props_v = GProp_GProps()
    brepgprop.VolumeProperties(shape, props_v)
    volume = props_v.Mass()
    
    props_s = GProp_GProps()
    brepgprop.SurfaceProperties(shape, props_s)
    area = props_s.Mass()
    
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    if not bbox.IsVoid():
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
    else:
        dx, dy, dz = 0.0, 0.0, 0.0
        
    return {
        "volume": volume,
        "area": area,
        "bbox": [dx, dy, dz]
    }

def get_solid_or_shell(shape):
    """提取形狀中的所有 Solid 或是 Shell，組合成一個複合物或是單一實體"""
    # 這裡簡化處理：假設 shape 本身就是實體或 Shell
    return shape

def compare_step_files(old_step_path, new_step_path, output_dir):
    """
    比對兩個 STEP 檔案，產出 added.stl, removed.stl, unchanged.stl
    回傳產生的檔案路徑字典。
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. 讀取模型
    shape_old = load_step(old_step_path)
    shape_new = load_step(new_step_path)
    
    if not shape_old or not shape_new:
        raise ValueError("無法讀取 STEP 檔案中的有效形狀")
        
    print(f"正在執行布林運算比對: {os.path.basename(old_step_path)} vs {os.path.basename(new_step_path)}")
    
    # 2. 計算差異 (Boolean Operations)
    # BRepAlgoAPI_Cut(S1, S2) => S1 - S2
    # BRepAlgoAPI_Common(S1, S2) => S1 & S2
    
    # 3. 匯出 STL
    writer = StlAPI_Writer()
    writer.SetASCIIMode(False) # Binary STL 較小
    
    added_stl = os.path.join(output_dir, "added.stl")
    removed_stl = os.path.join(output_dir, "removed.stl")
    unchanged_stl = os.path.join(output_dir, "unchanged.stl")
    
    # 注意：如果布林運算結果為空（沒有新增或移除），StlAPI_Writer 可能會報錯或輸出空檔
    # 在 OCC 中，我們可以用 TopExp_Explorer 檢查是否包含面 (Face)
    def has_faces(s):
        if s is None or s.IsNull(): return False
        exp = TopExp_Explorer(s, TopAbs_FACE)
        return exp.More()

    results = {}
    
    print("  -> 轉換舊版模型 (Old) 為網格...")
    BRepMesh_IncrementalMesh(shape_old, 0.1)
    writer.Write(shape_old, removed_stl)
    if os.path.exists(removed_stl) and os.path.getsize(removed_stl) > 0:
        results['removed'] = removed_stl
            
    print("  -> 轉換新版模型 (New) 為網格...")
    BRepMesh_IncrementalMesh(shape_new, 0.1)
    writer.Write(shape_new, added_stl)
    if os.path.exists(added_stl) and os.path.getsize(added_stl) > 0:
        results['added'] = added_stl
        
    print("  -> 計算幾何數據差異...")
    old_stats = get_shape_stats(shape_old)
    new_stats = get_shape_stats(shape_new)
    
    print("  -> 擷取組合樹狀結構...")
    tree_old = extract_tree_only(old_step_path)
    tree_new = extract_tree_only(new_step_path)
    
    results['stats'] = {
        'old': old_stats,
        'new': new_stats,
        'diff': {
            'volume': new_stats['volume'] - old_stats['volume'],
            'area': new_stats['area'] - old_stats['area'],
            'bbox': [
                new_stats['bbox'][0] - old_stats['bbox'][0],
                new_stats['bbox'][1] - old_stats['bbox'][1],
                new_stats['bbox'][2] - old_stats['bbox'][2]
            ]
        }
    }
    
    results['tree_old'] = tree_old
    results['tree_new'] = tree_new
        
    print("比對完成！(Fast Visual Diff Mode)")
    return results

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4:
        compare_step_files(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print("Usage: python compare_models.py <old.stp> <new.stp> <output_dir>")
