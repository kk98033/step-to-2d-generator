# API Reference Documentation

## Base URL
`http://localhost:8000`

## Endpoints

### 1. `POST /api/upload`
Uploads a single STEP file for 2D drawing generation.
- **Content-Type**: `multipart/form-data`
- **Body Parameters**:
  - `file`: The `.step` or `.stp` file.
- **Returns**:
  ```json
  { "job_id": "uuid-string" }
  ```

### 2. `POST /api/compare`
Uploads two STEP files for fast visual comparison and structural tree diffing.

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
    "progress": { "current": 1, "total": 10 }
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
  - `filename`: The specific file.
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
Lists reference example PDF/SVG files if the local example directory exists.
- **Returns**:
  ```json
  {
    "example_tree": {
      "name": "公司範例圖 (Reference)",
      "type": "folder",
      "children": [...]
    }
  }
  ```

---

### 9. `POST /api/tolerances`
Updates the global tolerance configuration for 2D dimension generation. Designed to be called by external ML prediction models.
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
  { "status": "success", "message": "Tolerances updated." }
  ```

### 10. `GET /api/tolerances`
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
