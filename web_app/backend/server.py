import os
import sys
import uuid
import json
import shutil
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Add parent dir to path so we can import original modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from auto_2d_drawing.config import MODELS_DIR, OUTPUT_DIR
from auto_2d_drawing.batch_generate import batch_generate

app = FastAPI(title="FORCECON Auto 2D Drawing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve output files statically
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/api/files", StaticFiles(directory=OUTPUT_DIR), name="files")

jobs = {}
executor = ThreadPoolExecutor(max_workers=2)


def _api_file_url(output_dir_name: str, *parts: str) -> str:
    return "/api/files/" + "/".join([output_dir_name, *parts])


def _add_if_exists(entry: dict, key: str, output_dir_name: str, output_dir: str, *parts: str):
    path = os.path.join(output_dir, *parts)
    if os.path.exists(path):
        entry[key] = _api_file_url(output_dir_name, *parts)


def _build_output_entry(output_dir_name: str, output_dir: str, base: str, stl_name: str) -> dict:
    entry = {}
    _add_if_exists(entry, "png", output_dir_name, output_dir, f"{base}.png")
    _add_if_exists(entry, "pdf", output_dir_name, output_dir, f"{base}.pdf")
    _add_if_exists(entry, "svg", output_dir_name, output_dir, f"{base}.svg")
    _add_if_exists(entry, "dxf", output_dir_name, output_dir, f"{base}.dxf")
    _add_if_exists(entry, "stl", output_dir_name, output_dir, "_parts", stl_name)
    _add_if_exists(entry, "front_pdf", output_dir_name, output_dir, f"{base}_front.pdf")
    _add_if_exists(entry, "back_pdf", output_dir_name, output_dir, f"{base}_back.pdf")
    _add_if_exists(entry, "top_pdf", output_dir_name, output_dir, f"{base}_top.pdf")
    _add_if_exists(entry, "right_pdf", output_dir_name, output_dir, f"{base}_right.pdf")
    _add_if_exists(entry, "left_pdf", output_dir_name, output_dir, f"{base}_left.pdf")
    _add_if_exists(entry, "front_svg", output_dir_name, output_dir, f"{base}_front.svg")
    _add_if_exists(entry, "back_svg", output_dir_name, output_dir, f"{base}_back.svg")
    _add_if_exists(entry, "top_svg", output_dir_name, output_dir, f"{base}_top.svg")
    _add_if_exists(entry, "right_svg", output_dir_name, output_dir, f"{base}_right.svg")
    _add_if_exists(entry, "left_svg", output_dir_name, output_dir, f"{base}_left.svg")
    _add_if_exists(entry, "features_pdf", output_dir_name, output_dir, f"{base}_features_view.pdf")
    _add_if_exists(entry, "features_svg", output_dir_name, output_dir, f"{base}_features_view.svg")
    _add_if_exists(entry, "features_json", output_dir_name, output_dir, f"{base}_feature_records.json")
    return entry


def build_parts_map(output_dir: str, output_dir_name: str) -> dict:
    """Build frontend file links for generated assembly and part drawings."""
    parts_map = {}
    parts_dir = os.path.join(output_dir, "_parts")
    part_prefixes = []
    if os.path.exists(parts_dir):
        part_prefixes = [
            os.path.splitext(filename)[0]
            for filename in os.listdir(parts_dir)
            if filename.lower().endswith(".stp") and filename != "_full_assembly.stp"
        ]

    for filename in os.listdir(output_dir):
        lower_name = filename.lower()
        if not lower_name.endswith((".png", ".pdf", ".svg", ".dxf")):
            continue

        base = os.path.splitext(filename)[0]
        if base.endswith(("_front", "_back", "_top", "_right", "_left", "_features_view")):
            continue

        if base.endswith("_assembly"):
            parts_map["_full_assembly"] = _build_output_entry(
                output_dir_name, output_dir, base, "_full_assembly.stl"
            )
            continue

        for part_prefix in part_prefixes:
            if base.endswith(f"_{part_prefix}"):
                parts_map[part_prefix] = _build_output_entry(
                    output_dir_name, output_dir, base, f"{part_prefix}.stl"
                )
                break

    return parts_map


def _safe_output_dir(model_id: str) -> str:
    if not model_id or model_id != os.path.basename(model_id):
        raise HTTPException(status_code=400, detail="Invalid model_id")

    output_dir = os.path.abspath(os.path.join(OUTPUT_DIR, model_id))
    output_root = os.path.abspath(OUTPUT_DIR)
    if os.path.commonpath([output_root, output_dir]) != output_root:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    if not os.path.isdir(output_dir):
        raise HTTPException(status_code=404, detail="Model output not found")
    return output_dir


def _safe_part_id(part_id: str) -> str:
    if not part_id or part_id != os.path.basename(part_id):
        raise HTTPException(status_code=400, detail="Invalid part_id")
    return part_id


def _view_urls(entry: dict) -> dict:
    views = {}
    for view_name in ("front", "back", "top", "right", "left"):
        view_entry = {}
        for ext in ("pdf", "svg"):
            key = f"{view_name}_{ext}"
            if key in entry:
                view_entry[ext] = entry[key]
        if view_entry:
            views[view_name] = view_entry
    return views


def _annotation_path(output_dir: str, part_id: str) -> str:
    return os.path.join(output_dir, "_annotations", f"{part_id}_annotations.json")


from auto_2d_drawing.step_reader import load_step
from auto_2d_drawing.feature_extractor import FeatureExtractor
from auto_2d_drawing.feature_layer import build_feature_records


@app.get("/api/features/{model_id}/{part_id}")
def get_part_features(model_id: str, part_id: str):
    """
    動態取得或即時提取任何零件的完整 3D 機械特徵清單
    若無快取或為舊版 2D 格式，會自動載入實體 STEP 進行即時提取並更新快取
    """
    output_dir = _safe_output_dir(model_id)
    _safe_part_id(part_id)

    parts_map = build_parts_map(output_dir, model_id)
    part_entry = parts_map.get(part_id, {})

    feature_json_rel = part_entry.get("features_json")
    json_path = None
    if feature_json_rel:
        json_path = os.path.join(output_dir, os.path.basename(feature_json_rel))
    else:
        json_path = os.path.join(output_dir, f"{part_id}_feature_records.json")

    # 1. 檢查快取是否有有效 3D 特徵 (center 必須為 3D 座標)
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            if isinstance(records, list) and len(records) > 0:
                has_3d = any(
                    isinstance(r.get("geometry"), dict) and
                    isinstance(r.get("geometry", {}).get("center"), list) and
                    len(r.get("geometry", {}).get("center", [])) >= 3
                    for r in records
                )
                if has_3d:
                    return {"status": "ok", "records": records, "source": "cache"}
        except Exception:
            pass

    # 2. 若無 3D 快取，即時從實體 STEP 進行 OpenCASCADE 幾何拓撲動態提取
    stp_candidates = [
        os.path.join(output_dir, "_parts", f"{part_id}.stp"),
        os.path.join(output_dir, f"{part_id}.stp"),
        os.path.join(MODELS_DIR, f"{model_id}.stp"),
        os.path.join(MODELS_DIR, f"{model_id.replace('_batch', '')}.stp"),
    ]

    for stp_path in stp_candidates:
        if os.path.exists(stp_path):
            try:
                shape = load_step(stp_path)
                if shape and not shape.IsNull():
                    fe = FeatureExtractor(shape)
                    records = build_feature_records(fe, None)
                    save_path = json_path or os.path.join(output_dir, f"{part_id}_feature_records.json")
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(records, f, indent=2, ensure_ascii=False)
                    return {"status": "ok", "records": records, "source": "dynamic_extracted"}
            except Exception as e:
                print(f"Dynamic feature extraction failed for {stp_path}: {e}")

    # Fallback
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return {"status": "ok", "records": json.load(f), "source": "2d_fallback"}
        except Exception:
            pass

    return {"status": "ok", "records": [], "source": "none"}


def _build_drawing_package(model_id: str) -> dict:
    output_dir = _safe_output_dir(model_id)
    parts_map = build_parts_map(output_dir, model_id)
    parts = {}

    for part_id, entry in parts_map.items():
        package_entry = {
            "part_id": part_id,
            "main": {
                key: entry[key]
                for key in ("pdf", "svg", "png", "dxf", "stl")
                if key in entry
            },
            "views": _view_urls(entry),
            "feature_layer": {
                key.replace("features_", ""): entry[key]
                for key in ("features_pdf", "features_svg", "features_json")
                if key in entry
            },
        }

        annotations_path = _annotation_path(output_dir, part_id)
        if os.path.exists(annotations_path):
            package_entry["external_annotations"] = _api_file_url(
                model_id, "_annotations", os.path.basename(annotations_path)
            )

        parts[part_id] = package_entry

    tree_path = os.path.join(output_dir, "_parts", "assembly_tree.json")
    tree_data = None
    if os.path.exists(tree_path):
        with open(tree_path, "r", encoding="utf-8") as f:
            tree_data = json.load(f)

    return {
        "model_id": model_id,
        "output_dir": model_id,
        "tree": tree_data,
        "parts": parts,
    }


def run_job(job_id: str, file_path: str):
    def progress_callback(total, current, message):
        jobs[job_id]["total"] = total
        jobs[job_id]["current"] = current
        jobs[job_id]["message"] = message
        if total > 0 and total == current:
            jobs[job_id]["status"] = "completed"

    try:
        model_name = os.path.splitext(os.path.basename(file_path))[0]
        output_dir_name = model_name + "_batch"
        output_dir = os.path.join(OUTPUT_DIR, output_dir_name)
        
        assembly_info = {
            '_assembly': {
                'name': '組合件',
                'drawing_no': model_name,
                'revision': 'R00',
                'material': '---',
                'model_code': '---',
            }
        }
        
        batch_generate(file_path, output_dir, assembly_info, progress_cb=progress_callback)
        
        # Read assembly tree to return as result
        tree_path = os.path.join(output_dir, "_parts", "assembly_tree.json")
        if os.path.exists(tree_path):
            with open(tree_path, "r", encoding="utf-8") as f:
                jobs[job_id]["result"] = json.load(f)
        jobs[job_id]["parts_map"] = build_parts_map(output_dir, output_dir_name)
        jobs[job_id]["output_dir"] = output_dir_name
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["message"] = "處理完成"
        
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = f"發生錯誤: {str(e)}"


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    file_path = os.path.join(MODELS_DIR, file.filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    jobs[job_id] = {
        "status": "processing",
        "total": 0,
        "current": 0,
        "message": "已接收檔案，準備開始處理...",
        "filename": file.filename,
        "result": None,
        "output_dir": None
    }
    
    executor.submit(run_job, job_id, file_path)
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    return {
        "status": job["status"],
        "message": job["message"],
        "progress": {"current": job.get("current", 0), "total": job.get("total", 0)},
        "logs": job.get("logs", [])
    }


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")
        
    return {
        "tree": job.get("result"),
        "parts_map": job.get("parts_map", {}),
        "output_dir": job.get("output_dir"),
        "diff_result": job.get("diff_result"),
        "stats": job.get("stats"),
        "tree_old": job.get("tree_old"),
        "tree_new": job.get("tree_new")
    }

# === 模型比對 API ===
def run_compare_job(job_id: str, old_path: str, new_path: str):
    try:
        from auto_2d_drawing.compare_models import compare_step_files
        output_dir_name = f"diff_{job_id[:8]}"
        output_dir = os.path.join(OUTPUT_DIR, output_dir_name)
        
        def progress_cb(msg):
            jobs[job_id]["message"] = msg
            if "logs" not in jobs[job_id]:
                jobs[job_id]["logs"] = []
            jobs[job_id]["logs"].append(msg)
            
        results = compare_step_files(old_path, new_path, output_dir, progress_callback=progress_cb)
        
        # 提取非路徑的資料
        stats = results.pop('stats', None)
        tree_old = results.pop('tree_old', None)
        tree_new = results.pop('tree_new', None)
        
        # 將本地路徑轉換為 URL
        diff_urls = {}
        for key, path in results.items():
            if isinstance(path, str) and path.endswith('.stl'):
                filename = os.path.basename(path)
                diff_urls[key] = f"/api/files/{output_dir_name}/{filename}"
            
        jobs[job_id]["diff_result"] = diff_urls
        jobs[job_id]["stats"] = stats
        jobs[job_id]["tree_old"] = tree_old
        jobs[job_id]["tree_new"] = tree_new
        jobs[job_id]["output_dir"] = output_dir_name
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["message"] = "比對完成"
        
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = f"比對發生錯誤: {str(e)}"
        
@app.post("/api/compare")
async def compare_files(file_old: UploadFile = File(...), file_new: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    old_path = os.path.join(MODELS_DIR, f"old_{job_id[:8]}_{file_old.filename}")
    new_path = os.path.join(MODELS_DIR, f"new_{job_id[:8]}_{file_new.filename}")
    
    with open(old_path, "wb") as buffer:
        shutil.copyfileobj(file_old.file, buffer)
    with open(new_path, "wb") as buffer:
        shutil.copyfileobj(file_new.file, buffer)
        
    jobs[job_id] = {
        "status": "processing",
        "total": 0,
        "current": 0,
        "message": "已接收比對檔案，準備開始分析差異...",
        "filename": f"{file_old.filename} vs {file_new.filename}",
        "result": None,
        "output_dir": None,
        "is_diff": True,
        "logs": []
    }
    
    executor.submit(run_compare_job, job_id, old_path, new_path)
    return {"job_id": job_id}

@app.get("/api/models")
async def list_models():
    """列出所有已生成的模型 (output 目錄下的資料夾)"""
    models = []
    if os.path.exists(OUTPUT_DIR):
        for item in os.listdir(OUTPUT_DIR):
            item_path = os.path.join(OUTPUT_DIR, item)
            if os.path.isdir(item_path) and item.endswith("_batch"):
                model_name = item.replace("_batch", "")
                models.append({
                    "id": item,
                    "name": model_name
                })
    return {"models": models}

@app.get("/api/model/{model_id}")
async def get_model(model_id: str):
    """直接讀取已生成模型的資料"""
    output_dir = os.path.join(OUTPUT_DIR, model_id)
    if not os.path.exists(output_dir):
        raise HTTPException(status_code=404, detail="Model not found")
    
    # 讀取 tree
    tree_path = os.path.join(output_dir, "_parts", "assembly_tree.json")
    tree_data = None
    if os.path.exists(tree_path):
        with open(tree_path, "r", encoding="utf-8") as f:
            tree_data = json.load(f)
            
    parts_map = build_parts_map(output_dir, model_id)
                
    return {
        "tree": tree_data,
        "parts_map": parts_map,
        "output_dir": model_id
    }


@app.get("/api/drawings/{model_id}")
async def get_drawing_package(model_id: str):
    """取得外部對接用的工程圖套件：合圖、三視圖、STL、特徵標註檔。"""
    return _build_drawing_package(model_id)


@app.get("/api/drawings/{model_id}/parts/{part_id}/features")
async def get_part_features(model_id: str, part_id: str):
    """取得指定零件/組合件的特徵標註候選資料。"""
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    parts_map = build_parts_map(output_dir, model_id)
    if part_id not in parts_map:
        raise HTTPException(status_code=404, detail="Part not found")

    features_url = parts_map[part_id].get("features_json")
    if not features_url:
        raise HTTPException(status_code=404, detail="Feature records not found")

    features_path = os.path.join(output_dir, os.path.basename(features_url))
    if not os.path.exists(features_path):
        raise HTTPException(status_code=404, detail="Feature records not found")

    with open(features_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    return {
        "model_id": model_id,
        "part_id": part_id,
        "features_url": features_url,
        "records": records,
    }


@app.get("/api/drawings/{model_id}/parts/{part_id}/annotations")
async def get_part_annotations(model_id: str, part_id: str):
    """取得外部系統回傳並已儲存的標註資訊。"""
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    annotations_path = _annotation_path(output_dir, part_id)
    if not os.path.exists(annotations_path):
        raise HTTPException(status_code=404, detail="Annotations not found")

    with open(annotations_path, "r", encoding="utf-8") as f:
        annotations = json.load(f)

    return {
        "model_id": model_id,
        "part_id": part_id,
        "annotations": annotations,
    }


@app.post("/api/drawings/{model_id}/parts/{part_id}/annotations")
async def save_part_annotations(
    model_id: str,
    part_id: str,
    annotations: Dict[str, Any] = Body(...),
):
    """接收外部系統回傳的標註資訊，儲存在該模型輸出資料夾內。"""
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    parts_map = build_parts_map(output_dir, model_id)
    if part_id not in parts_map:
        raise HTTPException(status_code=404, detail="Part not found")

    annotations_dir = os.path.join(output_dir, "_annotations")
    os.makedirs(annotations_dir, exist_ok=True)
    annotations_path = _annotation_path(output_dir, part_id)
    with open(annotations_path, "w", encoding="utf-8") as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)

    return {
        "status": "success",
        "message": "Annotations saved.",
        "model_id": model_id,
        "part_id": part_id,
        "annotations_url": _api_file_url(model_id, "_annotations", os.path.basename(annotations_path)),
    }

# === 範例圖 API ===
EXAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "auto_2d_drawing", "reference", "example_output")
NEW_EXAMPLE_DIR = r"F:\School\力致\new_data"
PROCESSED_FAN_20260625_DIR_NAME = "fan_20260625_autodraw"
PROCESSED_FAN_20260625_DIR = os.path.join(OUTPUT_DIR, PROCESSED_FAN_20260625_DIR_NAME)

def clean_example_filename(filename):
    """清理檔名作為顯示名稱 — 直接使用原始檔名（去掉副檔名）"""
    return os.path.splitext(filename)[0]

def build_file_tree(current_path, name, base_dir, url_prefix, allowed_exts):
    node = {"name": name, "type": "folder", "children": []}
    if os.path.isdir(current_path):
        for entry in sorted(os.listdir(current_path)):
            entry_path = os.path.join(current_path, entry)
            if os.path.isdir(entry_path):
                child = build_file_tree(entry_path, entry, base_dir, url_prefix, allowed_exts)
                if child["children"]:
                    node["children"].append(child)
            elif entry.lower().endswith(allowed_exts):
                rel_path = os.path.relpath(entry_path, base_dir)
                node["children"].append({
                    "name": clean_example_filename(entry),
                    "display_name": entry,
                    "type": "file",
                    "url": f"{url_prefix}/{rel_path.replace(os.sep, '/')}",
                    "filename": entry
                })
    return node

VIEW_SUFFIX_LABELS = {
    "_features_view": "特徵圖層",
    "_front": "前視圖",
    "_back": "背面視圖",
    "_top": "俯視圖",
    "_right": "右側視圖",
    "_left": "左側視圖",
}
VIEW_ORDER = {
    "main": 0,
    "_features_view": 1,
    "_front": 2,
    "_back": 3,
    "_top": 4,
    "_right": 5,
    "_left": 6,
    "dxf": 7,
}
EXT_PRIORITY = {".pdf": 0, ".svg": 1, ".dxf": 2}

def split_generated_view_name(filename):
    stem, ext = os.path.splitext(filename)
    lower_ext = ext.lower()
    for suffix in VIEW_SUFFIX_LABELS:
        if stem.endswith(suffix):
            return stem[:-len(suffix)], suffix, lower_ext
    return stem, "dxf" if lower_ext == ".dxf" else "main", lower_ext

def build_grouped_processed_tree(current_path, name, base_dir, url_prefix):
    """Group generated variants so each model is one collapsible node in the UI."""
    node = {"name": name, "type": "folder", "children": []}
    if not os.path.isdir(current_path):
        return node

    file_groups = {}
    for entry in sorted(os.listdir(current_path)):
        entry_path = os.path.join(current_path, entry)
        if os.path.isdir(entry_path):
            child = build_grouped_processed_tree(entry_path, entry, base_dir, url_prefix)
            if child["children"]:
                node["children"].append(child)
            continue

        ext = os.path.splitext(entry)[1].lower()
        if ext not in (".pdf", ".svg", ".dxf"):
            continue

        group_name, variant, ext = split_generated_view_name(entry)
        file_groups.setdefault(group_name, {})

        # Keep the most viewable artifact for each variant: PDF, then SVG, then DXF.
        previous = file_groups[group_name].get(variant)
        if previous and EXT_PRIORITY.get(previous["ext"], 99) <= EXT_PRIORITY.get(ext, 99):
            continue

        rel_path = os.path.relpath(entry_path, base_dir)
        file_groups[group_name][variant] = {
            "ext": ext,
            "node": {
                "name": VIEW_SUFFIX_LABELS.get(variant, "DXF" if variant == "dxf" else "合圖"),
                "display_name": VIEW_SUFFIX_LABELS.get(variant, "DXF" if variant == "dxf" else "合圖"),
                "type": "file",
                "url": f"{url_prefix}/{rel_path.replace(os.sep, '/')}",
                "filename": entry,
            },
        }

    for group_name in sorted(file_groups):
        variants = file_groups[group_name]
        children = [
            variants[key]["node"]
            for key in sorted(variants, key=lambda item: VIEW_ORDER.get(item, 99))
        ]
        if len(children) == 1:
            only_child = dict(children[0])
            only_child["name"] = group_name
            only_child["display_name"] = f"{group_name} / {children[0]['display_name']}"
            node["children"].append(only_child)
            continue

        node["children"].append({
            "name": group_name,
            "display_name": group_name,
            "type": "folder",
            "children": children,
        })

    return node

if os.path.exists(EXAMPLE_DIR):
    app.mount("/api/examples/files", StaticFiles(directory=EXAMPLE_DIR), name="example_files")

if os.path.exists(NEW_EXAMPLE_DIR):
    app.mount("/api/examples/new_files", StaticFiles(directory=NEW_EXAMPLE_DIR), name="new_example_files")

@app.get("/api/examples")
async def list_examples():
    """列出範例資料夾下的所有檔案，並維持資料夾結構"""
    def _build_tree(current_path, name, base_dir, url_prefix):
        return build_file_tree(
            current_path,
            name,
            base_dir,
            url_prefix,
            ('.pdf', '.svg', '.dwg', '.xls', '.xlsx', '.7z', '.zip')
        )

    root_node = {"name": "所有公司範例圖", "type": "folder", "children": []}
    
    if os.path.exists(EXAMPLE_DIR):
        old_tree = _build_tree(EXAMPLE_DIR, "公司範例圖 (舊版)", EXAMPLE_DIR, "/api/examples/files")
        if old_tree["children"]:
            root_node["children"].append(old_tree)
            
    if os.path.exists(NEW_EXAMPLE_DIR):
        new_tree = _build_tree(NEW_EXAMPLE_DIR, "公司範例圖 (新版)", NEW_EXAMPLE_DIR, "/api/examples/new_files")
        if new_tree["children"]:
            root_node["children"].append(new_tree)

    return {"example_tree": root_node if root_node["children"] else None}

@app.get("/api/processed/fan-20260625")
async def list_processed_fan_20260625():
    """列出 FAN 20260625 批次輸出的工程圖，維持原始資料夾結構。"""
    if not os.path.exists(PROCESSED_FAN_20260625_DIR):
        return {"processed_tree": None, "manifest": None}

    tree = build_grouped_processed_tree(
        PROCESSED_FAN_20260625_DIR,
        "FAN 20260625 已處理工程圖",
        PROCESSED_FAN_20260625_DIR,
        f"/api/files/{PROCESSED_FAN_20260625_DIR_NAME}"
    )
    manifest = None
    manifest_path = os.path.join(PROCESSED_FAN_20260625_DIR, "batch_index.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    return {"processed_tree": tree if tree["children"] else None, "manifest": manifest}

# === 前端網頁路由 ===
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    print(f"Warning: Frontend dist directory not found at {FRONTEND_DIR}. Please run 'npm run build' in frontend folder.")

# === 公差設定 API (預留給未來機器學習外部模組) ===
from pydantic import BaseModel
from typing import Any, Dict, Optional

class ToleranceConfig(BaseModel):
    default_tolerance: Optional[str] = "±0.1"
    feature_overrides: Optional[Dict[str, str]] = {}

# 在記憶體中暫存公差設定
global_tolerances = ToleranceConfig(
    default_tolerance="±0.1",
    feature_overrides={"shaft": "±0.05", "hole": "±0.02"}
)

@app.get("/api/tolerances")
async def get_tolerances():
    """取得目前的公差設定"""
    return global_tolerances.dict()

@app.post("/api/tolerances")
async def update_tolerances(config: ToleranceConfig):
    """從外部更新公差設定"""
    global global_tolerances
    global_tolerances = config
    return {"status": "success", "message": "Tolerances updated.", "data": global_tolerances.dict()}

if __name__ == "__main__":
    import uvicorn
    # Trigger reload 22 — Force reload for backend stats & tree_diff
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
