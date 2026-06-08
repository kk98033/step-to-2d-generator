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
Uploads two STEP files for 3D geometric and structural diffing.
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
      "unchanged": "/api/files/.../unchanged.stl",
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

### 6. `POST /api/tolerances`
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

### 7. `GET /api/tolerances`
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
