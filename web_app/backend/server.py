import os
import sys
import uuid
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
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
        # Build parts mapping
        parts_map = {}
        for filename in os.listdir(output_dir):
            if filename.endswith(".png"):
                # Format: {model_name}_part{idx}_{part_name}.png or {model_name}_assembly.png
                base = filename[:-4]
                if "_part" in base:
                    part_name = base.split("_", 2)[-1] # assuming model_name doesn't contain "_part" easily, or part_name is at the end. Actually part_name is Node_...
                    # Better way: find the part_name that matches the end of the filename
                    for tree_part in os.listdir(os.path.join(output_dir, "_parts")):
                        if tree_part.endswith(".stp"):
                            tp_name = tree_part[:-4]
                            if filename.endswith(f"_{tp_name}.png"):
                                parts_map[tp_name] = {
                                    "png": f"/api/files/{output_dir_name}/{filename}",
                                    "stl": f"/api/files/{output_dir_name}/_parts/{tp_name}.stl",
                                    "front_pdf": f"/api/files/{output_dir_name}/{base}_front.pdf",
                                    "top_pdf": f"/api/files/{output_dir_name}/{base}_top.pdf",
                                    "right_pdf": f"/api/files/{output_dir_name}/{base}_right.pdf"
                                }
                elif "assembly" in base:
                    parts_map["_full_assembly"] = {
                        "png": f"/api/files/{output_dir_name}/{filename}",
                        "stl": f"/api/files/{output_dir_name}/_parts/_full_assembly.stl",
                        "front_pdf": f"/api/files/{output_dir_name}/{base}_front.pdf",
                        "top_pdf": f"/api/files/{output_dir_name}/{base}_top.pdf",
                        "right_pdf": f"/api/files/{output_dir_name}/{base}_right.pdf"
                    }
                    
        jobs[job_id]["parts_map"] = parts_map
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
        "status": "running",
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
    return jobs[job_id]


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
        
        results = compare_step_files(old_path, new_path, output_dir)
        
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
        "status": "running",
        "total": 0,
        "current": 0,
        "message": "已接收比對檔案，準備開始分析差異...",
        "filename": f"{file_old.filename} vs {file_new.filename}",
        "result": None,
        "output_dir": None,
        "is_diff": True
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
            
    # 建立 parts_map
    parts_map = {}
    for filename in os.listdir(output_dir):
        if filename.endswith(".png"):
            base = filename[:-4]
            if "_part" in base:
                parts_dir = os.path.join(output_dir, "_parts")
                if os.path.exists(parts_dir):
                    for tree_part in os.listdir(parts_dir):
                        if tree_part.endswith(".stp"):
                            tp_name = tree_part[:-4]
                            if filename.endswith(f"_{tp_name}.png"):
                                parts_map[tp_name] = {
                                    "png": f"/api/files/{model_id}/{filename}",
                                    "stl": f"/api/files/{model_id}/_parts/{tp_name}.stl",
                                    "front_pdf": f"/api/files/{model_id}/{base}_front.pdf",
                                    "top_pdf": f"/api/files/{model_id}/{base}_top.pdf",
                                    "right_pdf": f"/api/files/{model_id}/{base}_right.pdf"
                                }
            elif "assembly" in base:
                parts_map["_full_assembly"] = {
                    "png": f"/api/files/{model_id}/{filename}",
                    "stl": f"/api/files/{model_id}/_parts/_full_assembly.stl",
                    "front_pdf": f"/api/files/{model_id}/{base}_front.pdf",
                    "top_pdf": f"/api/files/{model_id}/{base}_top.pdf",
                    "right_pdf": f"/api/files/{model_id}/{base}_right.pdf"
                }
                
    return {
        "tree": tree_data,
        "parts_map": parts_map,
        "output_dir": model_id
    }

# === 範例圖 API ===
EXAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "auto_2d_drawing", "reference", "example_output")

def clean_example_filename(filename):
    """清理檔名作為顯示名稱 — 直接使用原始檔名（去掉副檔名）"""
    return os.path.splitext(filename)[0]

if os.path.exists(EXAMPLE_DIR):
    app.mount("/api/examples/files", StaticFiles(directory=EXAMPLE_DIR), name="example_files")

@app.get("/api/examples")
async def list_examples():
    """列出 reference/example_output 下的所有 PDF 檔案，並維持資料夾結構"""
    def _build_tree(current_path, name):
        node = {"name": name, "type": "folder", "children": []}
        if os.path.isdir(current_path):
            for entry in sorted(os.listdir(current_path)):
                entry_path = os.path.join(current_path, entry)
                if os.path.isdir(entry_path):
                    child = _build_tree(entry_path, entry)
                    if child["children"]:  # 略過空資料夾
                        node["children"].append(child)
                elif entry.lower().endswith(('.pdf', '.svg')):
                    rel_path = os.path.relpath(entry_path, EXAMPLE_DIR)
                    node["children"].append({
                        "name": clean_example_filename(entry),
                        "display_name": clean_example_filename(entry),
                        "type": "file",
                        "url": f"/api/examples/files/{rel_path.replace(os.sep, '/')}",
                        "filename": entry
                    })
        return node

    tree = _build_tree(EXAMPLE_DIR, "公司範例圖 (Reference)") if os.path.exists(EXAMPLE_DIR) else None
    return {"example_tree": tree}

# === 前端網頁路由 ===
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    print(f"Warning: Frontend dist directory not found at {FRONTEND_DIR}. Please run 'npm run build' in frontend folder.")

# === 公差設定 API (預留給未來機器學習外部模組) ===
from pydantic import BaseModel
from typing import Dict, Optional

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
