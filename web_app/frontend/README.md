# STEP-to-2D Frontend

這是 FORCECON STEP-to-2D Generator 的 React 前端。它不是 Vite 預設模板，而是工程圖自動化系統的操作介面。

## 技術

- React 19
- TypeScript
- Vite
- Three.js
- `@react-three/fiber`
- `@react-three/drei`
- Axios
- Lucide React

## 常用指令

```bash
npm install
npm run dev
npm run build
npm run lint
```

開發伺服器預設：

```text
http://localhost:5173
```

正式展示時請先執行：

```bash
npm run build
```

build 後的 `dist/` 會由 FastAPI 後端掛載，使用者可直接開啟：

```text
http://localhost:8000
```

## 主要程式

目前主要 UI 與狀態集中在：

```text
src/App.tsx
```

它負責：

- STEP/STP 單檔上傳。
- 新舊 STEP/STP 比對上傳。
- 任務狀態輪詢。
- 模型樹與範例資料樹顯示。
- PDF/SVG/DXF 結果檢視。
- STL 3D 檢視。
- 新舊模型紅綠疊圖、線框與 clipping slider。

## API 假設

前端假設後端在：

```text
http://localhost:8000
```

重要回傳格式：

- `GET /api/status/{job_id}` 回傳 `status`、`message`、`progress.current`、`progress.total`。
- `GET /api/results/{job_id}` 回傳 `tree`、`parts_map`、`output_dir`，比對任務另有 `diff_result`、`stats`、`tree_old`、`tree_new`。

## 維護建議

`App.tsx` 已承載太多功能。若要繼續開發，建議優先拆出：

- `api.ts`
- `UploadPanel`
- `DrawingWorkspace`
- `DiffWorkspace`
- `ModelTree`
- `ReferenceTree`
- `StlViewer`
- `PdfViewer`
- `StatusPanel`

這會降低後續改比對模式、改工程圖檢視或改任務狀態時互相影響的機率。
