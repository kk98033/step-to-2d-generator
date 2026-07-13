# STEP-to-2D Generator

FORCECON Auto 2D Drawing System 是一套將 STEP/STP 3D CAD 模型轉成 2D 工程圖的原型系統。專案核心目標是讀取力致 FORCECON 的軸流扇、零件與組合件資料，自動產生可檢視、可下載的 DXF/PDF/PNG 工程圖，並透過 Web 介面提供上傳、模型樹瀏覽、STL 3D 檢視與新舊模型快速比對。

目前這份專案比較接近「可展示的工程圖自動化 PoC」，不是已完全產品化的 CAD 系統。文件以下內容以目前程式碼實際狀態為準。

## 專案做什麼

- 讀取 STEP/STP 3D 模型與組合件。
- 拆解組合件，輸出各零件 STEP/STL 與 `assembly_tree.json`。
- 以 OpenCASCADE HLR 產生前視、俯視、右視等 2D 投影。
- 依零件類型提取幾何特徵與尺寸標註任務。
- 輸出 FORCECON A3 圖框工程圖：DXF、PDF、PNG。
- 額外輸出單一視圖 PDF/PNG/DXF 與特徵圖層資料。
- 提供 FastAPI 後端與 React 前端，支援上傳處理、結果瀏覽與模型比對。
- 提供綠色版批次腳本，方便在展示電腦上啟動。

## 目前主要功能

### 自動 2D 工程圖

核心流程位於 `auto_2d_drawing/`：

1. `step_reader.py` 讀取 STEP，必要時拆解組合件。
2. `feature_extractor.py` 提取 bounding box、孔、軸、圓弧等特徵。
3. `part_classifier.py` 判斷零件類型。
4. `view_projector.py` 產生 HLR 投影。
5. `dimension_engine.py` 派發尺寸標註任務。
6. `layout_engine.py` 負責標註排版與 DXF 渲染。
7. `pdf_exporter.py` 將 DXF 轉成 PDF/PNG。

目前已支援的零件分類包含：

- `FAN`：風扇、圓盤、葉片類。
- `FAN_HOUSING`：風扇外框類。
- `STAMPED_FAN_BASE`：沖壓底座類。
- `SHAFT`：軸類。
- `GENERIC`：一般零件 fallback。

### Web 操作介面

Web 系統位於 `web_app/`：

- 後端：FastAPI，入口是 `web_app/backend/server.py`。
- 前端：React + TypeScript + Vite，入口是 `web_app/frontend/src/App.tsx`。
- 前端 build 後，後端會掛載 `web_app/frontend/dist`，可用單一 `http://localhost:8000` 開啟。
- 開發模式仍可分別啟動後端 `8000` 與前端 Vite `5173`。

### 新舊模型比對

`POST /api/compare` 目前採用快速視覺比對模式：

- 舊模型整體輸出為紅色 STL。
- 新模型整體輸出為綠色 STL。
- 回傳 volume、area、bbox 與 assembly tree 差異輔助判讀。
- 不做精準布林差集，因此紅/綠色不等於真正的新增或移除實體。

這是刻意取捨：複雜工業模型做 OpenCASCADE 布林差集容易耗時過長或失敗。

## 資料夾結構

```text
step-to-2d-generator/
├── auto_2d_drawing/          # STEP 解析、投影、標註、DXF/PDF 輸出核心
│   ├── main.py               # 單一模型工程圖入口
│   ├── batch_generate.py     # 組合件批次處理入口，Web 上傳會呼叫這裡
│   ├── step_reader.py        # STEP 讀取、組合件拆解、STL 匯出
│   ├── feature_extractor.py  # 幾何特徵提取
│   ├── view_projector.py     # HLR 視圖投影
│   ├── dimension_engine.py   # 標註任務派發
│   ├── layout_engine.py      # 標註排版與渲染
│   └── output/               # 產圖輸出目錄
├── web_app/
│   ├── backend/server.py     # FastAPI 後端
│   ├── frontend/             # React 前端
│   └── 一鍵啟動系統.bat
├── docs/
│   ├── api_reference.md
│   ├── drawing_exchange_api.md
│   └── project_report.md
├── models/                   # Web 上傳與測試用模型資料夾
├── environment.yml           # Conda 環境
├── 製作綠色版環境.bat
└── 綠色版一鍵啟動.bat
```

注意：外層 `F:\School\力致\app\models` 也有大量業主資料；程式設定中的 `MODELS_DIR` 指向內層 `step-to-2d-generator/models`。兩者用途需在後續整理時再統一。

## 環境需求

- Windows 環境優先。
- Conda。
- Python 3.10+。
- `pythonocc-core`，建議透過 conda-forge 安裝。
- Node.js 18+，供前端開發與 build 使用。

建立 Python 環境：

```bash
conda env create -f environment.yml
conda activate pyoccenv
```

安裝或確認前端套件：

```bash
cd web_app/frontend
npm install
```

## 啟動方式

### 展示或一般使用

先 build 前端，讓 FastAPI 能掛載靜態頁：

```bash
cd web_app/frontend
npm run build
```

再啟動後端：

```bash
cd ../backend
python server.py
```

開啟：

```text
http://localhost:8000
```

### 前後端分離開發

後端：

```bash
cd web_app/backend
python server.py
```

前端：

```bash
cd web_app/frontend
npm run dev
```

前端開發網址通常是：

```text
http://localhost:5173
```

### 單純跑核心產圖

```bash
cd auto_2d_drawing
python main.py
```

或批次模式。此入口會從 `step-to-2d-generator/models` 尋找檔案名稱：

```bash
cd auto_2d_drawing
python batch_generate.py your-model.stp
```

## 主要 API

完整 API 請見 `docs/api_reference.md`。常用端點：

- `POST /api/upload`：上傳單一 STEP/STP，產生工程圖。
- `POST /api/compare`：上傳新舊 STEP/STP，做快速視覺比對。
- `GET /api/status/{job_id}`：查詢背景任務狀態。
- `GET /api/results/{job_id}`：取得產圖結果。
- `GET /api/models`：列出已產生模型。
- `GET /api/model/{model_id}`：載入既有輸出。
- `GET /api/examples`：列出本機參考圖資料。
- `GET /api/processed/fan-20260625`：列出特定批次處理結果。
- `GET/POST /api/tolerances`：取得或更新公差設定。

若要和外部標註/公差系統對接，請優先看 `docs/drawing_exchange_api.md`。該文件說明如何取得三視圖、特徵標註 JSON，以及如何把外部標註結果 POST 回本系統。

## 目前限制與待整理項目

- 自動標註仍大量依賴 heuristic，特殊零件需要逐步補 extractor。
- 公差 API 已存在，但尚未完整串入所有標註任務。
- 比對模式不是精準幾何差集。
- 後端 job 狀態只存在記憶體，server 重啟後會消失。
- 前端主要邏輯集中在 `App.tsx`，長期應拆 component 與 API service。
- 輸出風格目前偏暗色 CAD 檢視，白底列印版仍需補強。
- 外層資料目錄與內層 `models/` 路徑策略尚未完全收斂。

## 建議接手順序

1. 先讀 `專案觀察報告.md` 與本 README，理解外層資料與內層程式邊界。
2. 再讀 `docs/api_reference.md`，確認 Web 後端能力。
3. 若要改產圖品質，從 `auto_2d_drawing/dimension_engine.py`、`extractors/`、`layout_engine.py` 開始。
4. 若要改 Web 介面，先拆 `web_app/frontend/src/App.tsx`。
5. 若要產品化，優先處理 job 持久化、路徑設定、錯誤日誌與 smoke test。
