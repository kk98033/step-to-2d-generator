# 工程圖與標註交換 API 文件

本文件描述外部系統如何和 STEP-to-2D Generator 對接：

1. 上傳 STEP/STP。
2. 等待工程圖產生完成。
3. 取得合圖、三視圖與特徵標註候選資料。
4. 外部系統完成標註判讀或公差配置後，將標註資訊回傳本系統。

Base URL：

```text
http://localhost:8000
```

## 一、整體流程

```text
外部系統
  │
  ├─ 1. POST /api/upload
  │      上傳 STEP/STP，取得 job_id
  │
  ├─ 2. GET /api/status/{job_id}
  │      輪詢直到 status = completed
  │
  ├─ 3. GET /api/results/{job_id}
  │      取得 output_dir，也就是 model_id
  │
  ├─ 4. GET /api/drawings/{model_id}
  │      取得合圖、三視圖、STL、特徵標註 JSON URL
  │
  ├─ 5. GET /api/drawings/{model_id}/parts/{part_id}/features
  │      取得指定零件/組合件的特徵標註候選 records
  │
  └─ 6. POST /api/drawings/{model_id}/parts/{part_id}/annotations
         回傳外部標註結果
```

## 二、名詞定義

### `job_id`

每次上傳 STEP/STP 後產生的背景任務 ID。只用於查詢當次產圖進度與結果。

### `model_id`

產圖完成後的輸出資料夾 ID，等同 `/api/results/{job_id}` 回傳的 `output_dir`。

範例：

```text
BLADE_ASSY-1AC085000H-R01_batch
```

### `part_id`

零件 ID。可從 `/api/drawings/{model_id}` 的 `parts` 物件取得。

常見值：

```text
_full_assembly
Part_1
Part_2
```

實際零件 ID 會依 STEP 組合件拆解結果而定。

### 三視圖

系統目前會依零件類型輸出部分或全部視圖：

- `front`：前視圖。
- `top`：俯視圖。
- `right`：右側視圖。
- `left`：左側視圖，部分風扇外框類才會有。
- `back`：背面視圖，部分沖壓底座類才會有。

外部系統應以 API 回傳的 `views` 內容為準，不要假設每個零件一定都有所有視圖。

## 三、上傳 STEP/STP

### `POST /api/upload`

上傳單一 STEP/STP 檔案並啟動背景產圖任務。

Request：

```http
POST /api/upload
Content-Type: multipart/form-data
```

Form data：

| 欄位 | 類型 | 必填 | 說明 |
| --- | --- | --- | --- |
| `file` | file | 是 | `.step` 或 `.stp` 檔案 |

Response：

```json
{
  "job_id": "4e1179f6-69f0-4d16-8c60-3d54539fa4e2"
}
```

## 四、查詢產圖進度

### `GET /api/status/{job_id}`

Response：

```json
{
  "status": "processing",
  "message": "正在處理 Part_1 (1/5)",
  "progress": {
    "current": 1,
    "total": 6
  },
  "logs": []
}
```

`status` 可能值：

| 狀態 | 說明 |
| --- | --- |
| `processing` | 任務處理中 |
| `completed` | 任務完成 |
| `error` | 任務失敗，請看 `message` |

## 五、取得產圖結果與 `model_id`

### `GET /api/results/{job_id}`

任務完成後呼叫。

Response 範例：

```json
{
  "tree": {
    "name": "Assembly",
    "children": []
  },
  "parts_map": {
    "_full_assembly": {
      "pdf": "/api/files/BLADE_batch/BLADE_assembly.pdf",
      "dxf": "/api/files/BLADE_batch/BLADE_assembly.dxf",
      "stl": "/api/files/BLADE_batch/_parts/_full_assembly.stl",
      "front_pdf": "/api/files/BLADE_batch/BLADE_assembly_front.pdf",
      "top_pdf": "/api/files/BLADE_batch/BLADE_assembly_top.pdf",
      "right_pdf": "/api/files/BLADE_batch/BLADE_assembly_right.pdf",
      "features_json": "/api/files/BLADE_batch/BLADE_assembly_feature_records.json"
    }
  },
  "output_dir": "BLADE_batch"
}
```

這裡的 `output_dir` 就是後續 API 的 `model_id`。

## 六、取得工程圖交換套件

### `GET /api/drawings/{model_id}`

這是建議外部系統主要使用的讀取 API。它會把原本分散在 `parts_map` 裡的合圖、三視圖與特徵資料整理成較穩定的交換格式。

Response：

```json
{
  "model_id": "BLADE_batch",
  "output_dir": "BLADE_batch",
  "tree": {
    "name": "Assembly",
    "children": []
  },
  "parts": {
    "_full_assembly": {
      "part_id": "_full_assembly",
      "main": {
        "pdf": "/api/files/BLADE_batch/BLADE_assembly.pdf",
        "png": "/api/files/BLADE_batch/BLADE_assembly.png",
        "dxf": "/api/files/BLADE_batch/BLADE_assembly.dxf",
        "stl": "/api/files/BLADE_batch/_parts/_full_assembly.stl"
      },
      "views": {
        "front": {
          "pdf": "/api/files/BLADE_batch/BLADE_assembly_front.pdf"
        },
        "top": {
          "pdf": "/api/files/BLADE_batch/BLADE_assembly_top.pdf"
        },
        "right": {
          "pdf": "/api/files/BLADE_batch/BLADE_assembly_right.pdf"
        }
      },
      "feature_layer": {
        "pdf": "/api/files/BLADE_batch/BLADE_assembly_features_view.pdf",
        "json": "/api/files/BLADE_batch/BLADE_assembly_feature_records.json"
      }
    }
  }
}
```

### 欄位說明

| 欄位 | 說明 |
| --- | --- |
| `parts` | 每個組合件或零件的輸出資料 |
| `main` | 合圖與 3D STL |
| `views` | 分開輸出的視圖，例如 `front`、`top`、`right` |
| `feature_layer` | 系統產生的特徵標註候選圖層與 JSON |
| `external_annotations` | 若外部標註已回傳，這裡會出現標註 JSON URL |

## 七、取得特徵標註候選資料

### `GET /api/drawings/{model_id}/parts/{part_id}/features`

取得指定零件/組合件的特徵標註候選 records。外部系統可以依這份資料決定要補哪些標註、公差或工程規格。

Response：

```json
{
  "model_id": "BLADE_batch",
  "part_id": "_full_assembly",
  "features_url": "/api/files/BLADE_batch/BLADE_assembly_feature_records.json",
  "records": [
    {
      "id": "front_circle_1",
      "type": "projected_circle",
      "name": "front 圓/孔候選 1",
      "view": "front",
      "role": "feature",
      "nominal": {
        "diameter": 12.5
      },
      "tolerance_key": "projected_circle",
      "geometry": {
        "kind": "circle",
        "center": [10.0, 20.0],
        "radius": 6.25
      },
      "source": {
        "extractor": "ViewProjector.visible.circle",
        "confidence": 0.7
      }
    }
  ]
}
```

### `record` 欄位說明

| 欄位 | 說明 |
| --- | --- |
| `id` | 特徵唯一 ID，外部回傳標註時建議用它對應 |
| `type` | 特徵類型，例如 `hole`、`shaft_or_boss`、`projected_circle` |
| `name` | 顯示名稱 |
| `view` | 建議對應視圖 |
| `role` | 特徵角色，例如 `datum`、`functional`、`reference` |
| `nominal` | 名目尺寸資料 |
| `tolerance_key` | 建議公差分類 key |
| `geometry` | 幾何資訊，例如圓心、半徑、bbox |
| `source` | 來源 extractor 與信心分數 |

## 八、外部標註結果回傳

### `POST /api/drawings/{model_id}/parts/{part_id}/annotations`

外部系統完成標註判讀後，可將結果回傳本系統。後端會把資料存到：

```text
auto_2d_drawing/output/{model_id}/_annotations/{part_id}_annotations.json
```

Request：

```http
POST /api/drawings/BLADE_batch/parts/_full_assembly/annotations
Content-Type: application/json
```

建議 body 格式：

```json
{
  "source": "external-tolerance-service",
  "version": "1.0.0",
  "model_id": "BLADE_batch",
  "part_id": "_full_assembly",
  "annotations": [
    {
      "feature_id": "front_circle_1",
      "view": "front",
      "type": "diameter",
      "label": "Ø12.50 ±0.02",
      "nominal": 12.5,
      "tolerance": {
        "type": "symmetric",
        "value": 0.02,
        "text": "±0.02"
      },
      "quality": {
        "confidence": 0.92,
        "needs_review": false
      },
      "notes": "由外部公差模型判定"
    }
  ]
}
```

Response：

```json
{
  "status": "success",
  "message": "Annotations saved.",
  "model_id": "BLADE_batch",
  "part_id": "_full_assembly",
  "annotations_url": "/api/files/BLADE_batch/_annotations/_full_assembly_annotations.json"
}
```

### 讀回外部標註結果

### `GET /api/drawings/{model_id}/parts/{part_id}/annotations`

Response：

```json
{
  "model_id": "BLADE_batch",
  "part_id": "_full_assembly",
  "annotations": {
    "source": "external-tolerance-service",
    "version": "1.0.0",
    "annotations": []
  }
}
```

## 九、錯誤碼

| HTTP 狀態 | 常見原因 |
| --- | --- |
| `400` | `model_id` 或 `part_id` 格式不合法 |
| `404` | 找不到模型輸出、零件、特徵 JSON 或標註 JSON |
| `500` | 後端處理例外 |

## 十、對接注意事項

- 外部系統應先呼叫 `/api/drawings/{model_id}`，再決定要處理哪些 `part_id`。
- 三視圖不保證每個零件都有 `front/top/right` 全部檔案，請依 `views` 實際回傳為準。
- `feature records` 是候選標註資料，不代表所有尺寸都已人工確認。
- 回傳 annotations 的 JSON schema 目前採寬鬆格式，只要是 JSON object 即可儲存。
- 若未來要讓回傳標註自動寫回 DXF/PDF，需要再新增「套用 annotations 重新產圖」的 API。
