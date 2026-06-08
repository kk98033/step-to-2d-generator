"""
STEP 讀取模組 — 讀取 3D STEP 檔案，支援單一零件與組合件拆解
使用 python-occ (pythonocc-core)
"""
import os
import json
import shutil

from OCC.Core.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.TDF import TDF_LabelSequence, TDF_Label, TDF_AttributeIterator, TDF_Tool
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TCollection import TCollection_AsciiString
from OCC.Core.StlAPI import StlAPI_Writer
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh


def load_step(filepath):
    """讀取 STEP 檔案並回傳 TopoDS_Shape"""
    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != 1:
        raise RuntimeError(f"無法讀取 STEP 檔案: {filepath}")
    reader.TransferRoots()
    return reader.OneShape()


def get_label_id(label):
    """取得 TDF_Label 的 Entry ID 字串"""
    entry_str = TCollection_AsciiString()
    TDF_Tool.Entry(label, entry_str)
    return entry_str.ToCString()


def get_label_name(label):
    """嘗試從 TDF_Label 取得名稱"""
    fallback = f"Node_{get_label_id(label).replace(':', '_')}"
    try:
        it = TDF_AttributeIterator(label)
        while it.More():
            attr = it.Value()
            if "TDataStd_Name" in attr.DynamicType().Name():
                name_attr = TDataStd_Name.cast(attr)
                if name_attr:
                    val = name_attr.Get().ToExtString().strip()
                    if val:
                        return "".join(c for c in val if c.isalnum() or c in (' ', '_', '-')).rstrip()
            it.Next()
    except Exception:
        pass
    return fallback


def extract_assembly(step_filepath, output_dir=None):
    """
    拆解 STEP 組合件，回傳組合樹結構與各零件的 STEP 檔案路徑。
    
    Returns:
        dict: {
            "tree": 組合樹結構 dict,
            "parts": {part_name: stp_filepath, ...},
            "output_dir": 輸出目錄
        }
    """
    model_name = os.path.splitext(os.path.basename(step_filepath))[0]
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(step_filepath), model_name)
    os.makedirs(output_dir, exist_ok=True)

    # 複製完整組件並匯出完整 STL (供全覽)
    full_step = os.path.join(output_dir, "_full_assembly.stp")
    full_stl = os.path.join(output_dir, "_full_assembly.stl")
    shutil.copy(step_filepath, full_step)
    
    try:
        full_shape = load_step(step_filepath)
        BRepMesh_IncrementalMesh(full_shape, 0.1)
        stl_writer = StlAPI_Writer()
        stl_writer.Write(full_shape, full_stl)
    except Exception as e:
        print(f"Warning: 無法匯出完整 STL: {e}")

    # XDE 解析
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("MDTV-XCAF")
    app.NewDocument("MDTV-XCAF", doc)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    if reader.ReadFile(step_filepath) != 1:
        raise RuntimeError(f"XDE 讀取失敗: {step_filepath}")
    reader.Transfer(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)

    count_dict = {}
    parts = {}
    cache = {}

    def _extract_part(label):
        base_name = get_label_name(label)
        if not base_name:
            base_name = "Part"
        count_dict[base_name] = count_dict.get(base_name, 0) + 1
        part_name = f"{base_name}_{count_dict[base_name]}"
        shape = shape_tool.GetShape(label)
        if shape.IsNull():
            return None
        stp_path = os.path.join(output_dir, f"{part_name}.stp")
        stl_path = os.path.join(output_dir, f"{part_name}.stl")
        
        # 匯出 STEP
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(stp_path)
        
        # 匯出 STL (用於 Web 3D Viewer)
        BRepMesh_IncrementalMesh(shape, 0.1)
        stl_writer = StlAPI_Writer()
        stl_writer.Write(shape, stl_path)
        
        parts[part_name] = stp_path
        return part_name

    def _process(label):
        name = get_label_name(label)
        node_id = get_label_id(label)
        info = {"name": name, "children": []}

        if shape_tool.IsReference(label):
            ref_label = TDF_Label()
            try:
                shape_tool.GetReferredShape(label, ref_label)
            except TypeError:
                res = shape_tool.GetReferredShape(label)
                ref_label = res[1] if isinstance(res, tuple) else res
            info["name"] = f"[實例] {name}"
            child = _process(ref_label)
            info["children"].append(child)
            info["file_prefix"] = child.get("file_prefix")
        elif shape_tool.IsAssembly(label):
            info["name"] = f"[組件] {name}"
            comps = TDF_LabelSequence()
            shape_tool.GetComponents(label, comps)
            for i in range(1, comps.Length() + 1):
                info["children"].append(_process(comps.Value(i)))
        else:
            info["name"] = f"[零件] {name}"
            if node_id in cache:
                info["file_prefix"] = cache[node_id]
            else:
                fp = _extract_part(label)
                if fp:
                    info["file_prefix"] = fp
                    cache[node_id] = fp
        return info

    tree = {"name": f"[根組件] {model_name}", "file_prefix": "_full_assembly", "children": []}
    for i in range(1, free_shapes.Length() + 1):
        tree["children"].append(_process(free_shapes.Value(i)))

    # 儲存組合樹
    tree_path = os.path.join(output_dir, "assembly_tree.json")
    with open(tree_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=4, ensure_ascii=False)

    return {"tree": tree, "parts": parts, "output_dir": output_dir}


def extract_tree_only(step_filepath):
    """
    僅解析 STEP 組合件樹狀結構，不寫入任何 STL/STEP 實體檔案。
    """
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("MDTV-XCAF")
    app.NewDocument("MDTV-XCAF", doc)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    if reader.ReadFile(step_filepath) != 1:
        return {"name": "Root", "children": [{"name": "[零件] Single Part"}]}
    reader.Transfer(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)

    count_dict = {}
    cache = {}

    def _get_part_name(label):
        base_name = get_label_name(label)
        if not base_name:
            base_name = "Part"
        count_dict[base_name] = count_dict.get(base_name, 0) + 1
        part_name = f"{base_name}_{count_dict[base_name]}"
        return part_name

    def _process(label):
        name = get_label_name(label)
        node_id = get_label_id(label)
        info = {"name": name, "children": []}

        if shape_tool.IsReference(label):
            ref_label = TDF_Label()
            try:
                shape_tool.GetReferredShape(label, ref_label)
            except TypeError:
                res = shape_tool.GetReferredShape(label)
                ref_label = res[1] if isinstance(res, tuple) else res
            info["name"] = f"[實例] {name}"
            child = _process(ref_label)
            info["children"].append(child)
            info["file_prefix"] = child.get("file_prefix")
        elif shape_tool.IsAssembly(label):
            info["name"] = f"[組件] {name}"
            comps = TDF_LabelSequence()
            shape_tool.GetComponents(label, comps)
            for i in range(1, comps.Length() + 1):
                info["children"].append(_process(comps.Value(i)))
        else:
            info["name"] = f"[零件] {name}"
            if node_id in cache:
                info["file_prefix"] = cache[node_id]
            else:
                fp = _get_part_name(label)
                info["file_prefix"] = fp
                cache[node_id] = fp
        return info

    tree_root = {"name": "Root", "children": []}
    for i in range(1, free_shapes.Length() + 1):
        tree_root["children"].append(_process(free_shapes.Value(i)))

    return tree_root
