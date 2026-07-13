# STEP-to-2D Web Application

這個資料夾包含 STEP-to-2D Generator 的 Web 操作介面。後端負責 STEP 解析、工程圖產生、模型比對與檔案服務；前端負責上傳、進度顯示、PDF/3D 檢視、模型樹與比對 UI。

## 架構

```text
web_app/
├── backend/
│   └── server.py          # FastAPI 後端入口
├── frontend/
│   ├── src/App.tsx        # 目前主要前端邏輯
│   └── package.json       # React/Vite 專案
├── requirements.txt       # 後端 pip 補充套件
└── 一鍵啟動系統.bat
```

後端會把專案根目錄加入 `sys.path`，直接呼叫 `auto_2d_drawing.batch_generate.batch_generate()` 產生圖面。

## 使用模式

### 模式 A：單一服務展示模式

這是展示與一般使用建議模式。先把前端 build 成靜態檔，再由 FastAPI 掛載：

```bash
cd web_app/frontend
npm install
npm run build
```

啟動後端：

```bash
cd ../backend
conda activate pyoccenv
python server.py
```

開啟：

```text
http://localhost:8000
```

若 `frontend/dist` 不存在，後端仍會啟動 API，但首頁不會有前端畫面，終端機會提示需要執行 `npm run build`。

### 模式 B：前後端分離開發

後端：

```bash
cd web_app/backend
conda activate pyoccenv
python server.py
```

前端：

```bash
cd web_app/frontend
npm install
npm run dev
```

開發網址通常是：

```text
http://localhost:5173
```

後端 API 預設在：

```text
http://localhost:8000
```

## 後端能力

主要 API：

- `POST /api/upload`：上傳單一 STEP/STP 並產生工程圖。
- `POST /api/compare`：上傳新舊 STEP/STP，做快速視覺比對。
- `GET /api/status/{job_id}`：查詢背景任務。
- `GET /api/results/{job_id}`：取得完成結果。
- `GET /api/files/{dir}/{file}`：讀取輸出檔。
- `GET /api/models`：列出已生成模型。
- `GET /api/model/{model_id}`：載入已生成模型。
- `GET /api/examples`：列出參考範例圖。
- `GET /api/processed/fan-20260625`：列出特定批次輸出。
- `GET/POST /api/tolerances`：公差設定 API。

完整格式請見 `../docs/api_reference.md`。

## 前端能力

目前前端集中在 `frontend/src/App.tsx`，包含：

- 單一模型上傳。
- 新舊模型比對上傳。
- 任務進度輪詢。
- 已生成模型載入。
- 參考圖資料樹。
- 2D PDF/SVG/DXF 檢視連結。
- STL 3D 模型檢視。
- 紅綠疊圖、線框與 X 光滑桿比對模式。

## 注意事項

- `pythonocc-core` 請用 conda-forge 安裝，單靠 `pip install -r requirements.txt` 不足以執行產圖核心。
- `jobs` 狀態存在後端記憶體，重啟 server 後任務狀態會消失。
- 比對模式是快速視覺疊圖，不是精準布林差集。
- 上傳檔案會存到 `step-to-2d-generator/models`。
- 產生結果會存到 `auto_2d_drawing/output`。
- `server.py` 內有本機參考資料路徑 `F:\School\力致\new_data`，若該路徑不存在，相關範例資料只會略過。
