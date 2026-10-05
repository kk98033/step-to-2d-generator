# 更新日誌

本專案仍在 `0.x` 開發階段。日期採台北時區；commit hash 是追查實作的主要依據。

## [Unreleased] - 2026-09-23

### Added

- 首頁新增「開啟公差案例檢視器」入口。
- 新增 `/api/health` 與 FastAPI `0.4.0-dev` metadata。
- 新增多階段 Dockerfile：Node 建置 React、Miniforge 建立 PythonOCC runtime。
- 新增 `environment.docker.yml`、`.dockerignore` 與可掛載公司資料的 Compose。
- 新增完整文件集：架構、特徵查找、公差推薦、繪圖、頁面、API、Docker 與開發路線圖。
- 新增外部神經網路公差預測 GET/POST/DELETE API、模型 provenance 與 UI 並列選擇。
- 外部預測加入 mode/config 判別驗證、candidate rule 嚴格校驗與正式 OpenAPI response schema。

### Changed

- React API base 預設改成同源，支援 localhost、Docker 與反向代理。
- models、output、reference、template 與中文字型可由環境變數配置。
- 公司 DXF/reference 路徑可由容器環境變數指定。

### Validation

- Python 公差管線與外部預測 API 測試 26 項通過。
- FastAPI 與 config 通過 Python compile check。
- TypeScript 與 Vite production build 已通過；產物已由目前後端實際提供。
- 本機缺少 Docker CLI，因此 container runtime build 仍需在具備 Docker Engine 的環境補做；不可標記為已完成映像實機驗證。

## [0.3.0] - 2026-09-23

### Added

- `DXF_2D_RULES_V2` 可解釋 2D 特徵推定。
- 尺寸附著、視圖 clustering、跨視圖 visible/hidden line pair。
- 公差 Inspector 的 PDF inline、SVG highlight、案例圖面與詳細資料。

### Changed

- 公差案例庫只保留真實公司 DXF 證據，不使用種子案例。
- `AUTO_EXTRACTED` 與 `AUTO_INFERRED_2D` 不參與 RAG。
- 推薦 trace 區分 adopted case、retrieved candidate 與 fallback。
- 歷史 DXF 只保留最高 `R` 版；清單按圖面分組。

### Data

- 1,697 張最新 DXF。
- 23,516 筆最終公差證據。
- 14 筆 STEP 核實可推薦案例。
- 排除 241 筆完全重複 dimension entity。

### Commits

- `e78a7dd` data: rebuild tolerance evidence from latest revisions
- `0e1dd63` fix: keep latest drawing revisions and group evidence
- `f5901ae` feat: correlate dxf dimensions across drawing views
- `ad02100` feat: add explainable 2d feature inference
- `1711dd2` fix: align tolerance evidence with feature taxonomy
- `cf6e9aa` feat: rebuild tolerance database from verified CAD evidence

## [0.2.0] - 2026-08

### Added

- 3D Feature Layer 與動態空間包絡。
- SmartRuleExtractor 與 SmartDimensionEngine。
- 多視角標註、樣板、2D pan/zoom 與返回 3D。
- DXF、向量 PDF、PNG、SVG 輸出。
- 標註控制台與 CAD-RAG 初版 UI。

### Engineering constraints

- 舊版 `dimension_engine.py` 與 legacy extractors 保持不變。
- 所有 3D 特徵位置改由模型包絡與主軸動態計算。
- UI 統一深色 CAD 純色風格與 Lucide 圖示。
