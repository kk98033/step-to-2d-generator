# STEP-to-2D API Reference

## Base URL
`http://localhost:8000`

後端入口為 `web_app/backend/server.py`。所有背景任務狀態目前只存在記憶體中的 `jobs` dict，server 重啟後不會保留。

## Endpoints

外部系統若要取得三視圖與回傳標註資訊，建議優先閱讀 `docs/drawing_exchange_api.md`。該文件描述完整的工程圖交換流程。

### 1. `POST /api/upload`
Uploads a single STEP/STP file for 2D drawing generation.
- **Content-Type**: `multipart/form-data`
- **Body Parameters**:
  - `file`: The `.step` or `.stp` file.
- **Returns**:
  ```json
  { "job_id": "uuid-string" }
  ```

### 2. `POST /api/compare`
Uploads two STEP/STP files for fast visual comparison and structural tree diffing.

Current implementation uses **Fast Visual Diff Mode**:
- `added` is the full new model STL shown in green.
- `removed` is the full old model STL shown in red.
- It does not currently compute exact Boolean added/removed/unchanged solids.
- Volume, surface area, bounding box, and assembly tree differences are still reported as numerical/contextual aids.

- **Content-Type**: `multipart/form-data`
- **Body Parameters**:
  - `file_old`: The older version of the `.step` file.
  - `file_new`: The newer version of the `.step` file.
- **Returns**:
  ```json
  { "job_id": "uuid-string" }
  ```

### 3. `GET /api/status/{job_id}`
Checks the status of a background processing job.
- **Path Parameters**:
  - `job_id`: The UUID returned by the upload/compare APIs.
- **Returns**:
  ```json
  {
    "status": "processing|completed|error",
    "message": "Status description...",
    "progress": { "current": 1, "total": 10 },
    "logs": []
  }
  ```

### 4. `GET /api/results/{job_id}`
Fetches the detailed results of a completed job.
- **Path Parameters**:
  - `job_id`: The UUID of a completed job.
- **Returns (Single File Job)**:
  ```json
  {
    "tree": { "name": "Assembly", "children": [...] },
    "parts_map": { "Part_1": { "pdf": "...", "dxf": "...", "stl": "..." } },
    "output_dir": "dir_name"
  }
  ```
- **Returns (Compare Job)**:
  ```json
  {
    "diff_result": {
      "added": "/api/files/.../added.stl",
      "removed": "/api/files/.../removed.stl"
    },
    "stats": {
      "old": { "volume": 100, "area": 50, "bbox": [10, 10, 10] },
      "new": { "volume": 120, "area": 60, "bbox": [12, 10, 10] },
      "diff": { "volume": 20, "area": 10, "bbox": [2, 0, 0] }
    },
    "tree_old": { "name": "Root", "children": [...] },
    "tree_new": { "name": "Root", "children": [...] }
  }
  ```

### 5. `GET /api/files/{dir_name}/{filename}`
Serves statically generated files (STL, PDF, DXF, PNG).
- **Path Parameters**:
  - `dir_name`: The output directory of the job.
  - `filename`: The specific file. Nested paths such as `_parts/model.stl` are also served by the mounted static route.
- **Returns**: The binary file content.

---

### 6. `GET /api/models`
Lists previously generated model output folders under the output directory.
- **Returns**:
  ```json
  {
    "models": [
      { "id": "model_batch", "name": "model" }
    ]
  }
  ```

### 7. `GET /api/model/{model_id}`
Loads an already generated model without re-running STEP processing.
- **Path Parameters**:
  - `model_id`: The output folder id returned by `/api/models`.
- **Returns**:
  ```json
  {
    "tree": { "name": "Assembly", "children": [...] },
    "parts_map": {
      "_full_assembly": {
        "png": "/api/files/model_batch/model_assembly.png",
        "pdf": "/api/files/model_batch/model_assembly.pdf",
        "dxf": "/api/files/model_batch/model_assembly.dxf",
        "stl": "/api/files/model_batch/_parts/_full_assembly.stl",
        "front_pdf": "/api/files/model_batch/model_assembly_front.pdf",
        "top_pdf": "/api/files/model_batch/model_assembly_top.pdf",
        "right_pdf": "/api/files/model_batch/model_assembly_right.pdf"
      }
    },
    "output_dir": "model_batch"
  }
  ```

### 8. `GET /api/examples`
Lists reference example files if local example directories exist.

The response may include:
- old examples from `auto_2d_drawing/reference/example_output`
- new examples from the machine-local path configured in `server.py`

Allowed extensions include PDF, SVG, DWG, XLS/XLSX, 7z, and ZIP.
- **Returns**:
  ```json
  {
    "example_tree": {
      "name": "所有公司範例圖",
      "type": "folder",
      "children": [...]
    }
  }
  ```

If no configured example folder exists or no supported files are found:

```json
{ "example_tree": null }
```

---

### 9. `GET /api/drawings/{model_id}`
Returns an exchange-friendly drawing package for a generated model. This includes main drawing files, split view files, feature layer files, and previously returned external annotations when present.

- **Path Parameters**:
  - `model_id`: The output folder id, usually the `output_dir` returned by `/api/results/{job_id}`.
- **Returns**:
  ```json
  {
    "model_id": "model_batch",
    "output_dir": "model_batch",
    "tree": { "name": "Assembly", "children": [] },
    "parts": {
      "_full_assembly": {
        "part_id": "_full_assembly",
        "main": {
          "pdf": "/api/files/model_batch/model_assembly.pdf",
          "png": "/api/files/model_batch/model_assembly.png",
          "dxf": "/api/files/model_batch/model_assembly.dxf",
          "stl": "/api/files/model_batch/_parts/_full_assembly.stl"
        },
        "views": {
          "front": { "pdf": "/api/files/model_batch/model_assembly_front.pdf" },
          "top": { "pdf": "/api/files/model_batch/model_assembly_top.pdf" },
          "right": { "pdf": "/api/files/model_batch/model_assembly_right.pdf" }
        },
        "feature_layer": {
          "pdf": "/api/files/model_batch/model_assembly_features_view.pdf",
          "json": "/api/files/model_batch/model_assembly_feature_records.json"
        }
      }
    }
  }
  ```

### 10. `GET /api/drawings/{model_id}/parts/{part_id}/features`
Returns feature records for a generated drawing part. These records are intended as annotation and tolerance candidates for an external service.

- **Returns**:
  ```json
  {
    "model_id": "model_batch",
    "part_id": "_full_assembly",
    "features_url": "/api/files/model_batch/model_assembly_feature_records.json",
    "records": [
      {
        "id": "front_circle_1",
        "type": "projected_circle",
        "view": "front",
        "nominal": { "diameter": 12.5 },
        "tolerance_key": "projected_circle",
        "geometry": { "kind": "circle", "center": [10, 20], "radius": 6.25 }
      }
    ]
  }
  ```

### 11. `POST /api/drawings/{model_id}/parts/{part_id}/annotations`
Receives external annotation results for a generated part and stores them under the model output folder.

- **Content-Type**: `application/json`
- **Body**: Any JSON object. Recommended schema is documented in `docs/drawing_exchange_api.md`.
- **Returns**:
  ```json
  {
    "status": "success",
    "message": "Annotations saved.",
    "model_id": "model_batch",
    "part_id": "_full_assembly",
    "annotations_url": "/api/files/model_batch/_annotations/_full_assembly_annotations.json"
  }
  ```

### 12. `GET /api/drawings/{model_id}/parts/{part_id}/annotations`
Returns annotations previously stored through the POST endpoint.

- **Returns**:
  ```json
  {
    "model_id": "model_batch",
    "part_id": "_full_assembly",
    "annotations": {}
  }
  ```

---

### 13. `GET /api/processed/fan-20260625`
Lists the preprocessed FAN 20260625 batch outputs under `auto_2d_drawing/output/fan_20260625_autodraw`.

Generated variants are grouped by model name. For each model, the API prefers PDF over SVG over DXF when multiple formats exist for the same view.

- **Returns**:
  ```json
  {
    "processed_tree": {
      "name": "FAN 20260625 已處理工程圖",
      "type": "folder",
      "children": [...]
    },
    "manifest": {}
  }
  ```

If the processed output directory does not exist:

```json
{ "processed_tree": null, "manifest": null }
```

---

### 14. `POST /api/tolerances`
Updates the global tolerance configuration for 2D dimension generation. Designed to be called by external ML prediction models.

Current limitation: this configuration is stored in memory and is not yet fully wired into every generated `DimensionTask`.

- **Content-Type**: `application/json`
- **Body Schema**:
  ```json
  {
    "default_tolerance": "±0.1",
    "feature_overrides": {
      "shaft": "±0.05",
      "hole": "±0.02"
    }
  }
  ```
- **Returns**:
  ```json
  {
    "status": "success",
    "message": "Tolerances updated.",
    "data": {
      "default_tolerance": "±0.1",
      "feature_overrides": {
        "shaft": "±0.05",
        "hole": "±0.02"
      }
    }
  }
  ```

### 15. `GET /api/tolerances`
Retrieves the current global tolerance configuration.
- **Returns**:
  ```json
  {
    "default_tolerance": "±0.1",
    "feature_overrides": {
      "shaft": "±0.05",
      "hole": "±0.02"
    }
  }
  ```

## Notes

- `POST /api/upload` writes uploaded files to `step-to-2d-generator/models`.
- Generated drawings and STL files are served from `auto_2d_drawing/output`.
- `POST /api/compare` is intentionally visual/statistical. It does not promise exact geometric difference bodies.
- For production use, the in-memory job store should be replaced with JSON/SQLite/Redis or another persistent queue.
