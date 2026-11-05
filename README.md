# STEP-to-2D Generator

STEP 模型分析、組件瀏覽、特徵標註、2D 工程圖輸出與公差推薦系統。

## 系統入口

- 主系統：`http://localhost:8000/`
- 管理中心：`http://localhost:8000/admin`
- 健康檢查：`http://localhost:8000/api/health`
- OpenAPI：`http://localhost:8000/docs`

管理中心包含帳號管理、即時人流、資源監控、服務控制、容量測試與系統紀錄。
主系統與管理中心共用服務狀態元件；一般使用者僅可查看彙總人數。

## 部署

需安裝 Docker Engine 與 Docker Compose。完整設定請見 [部署說明](docs/deployment-docker.md)。

1. 將 `.env.example` 複製為 `.env`。
2. 設定資料庫密碼、管理員密碼與 Fernet 加密金鑰，勿使用範例字串。
3. 執行 `docker compose up -d --build`。
4. 確認 `/api/health` 回傳 `ok`，再從管理中心建立使用者。

公司 STEP、DWG、DXF、案例資料庫與使用者資料，均須保存在受控主機或私有 Volume。
全新部署不附任何公司模型、帳號資料庫或公差案例；公司案例庫未掛載時，案例型推薦保持空庫。

## 程式與私有資料邊界

此儲存庫只發布程式碼、通用設定、合成測試與部署說明。
`experiments/`、`output/`、模型、資料庫內容、快取、日誌、進度文件與本機密鑰均不得提交。
`experimental_lab/tolerance_extraction_audit/common/scripts/` 與 `DRAWING_CURRENT_FINAL/` 中的 Python 模組是目前新版引擎的執行依賴；保留這些來源程式不代表發布實驗資料。

提交與推送前檢查：

```sh
python tools/check_release_content.py --staged
python tools/generate_system_api_graph.py --check
```

Docker 建置情境採允許清單，執行映像不會複製公司案例庫。
`.gitignore` 不會清除既有 Git 歷史；歷史中曾出現的私有資料須另行清理。

## 執行條件

- Python 環境需提供 pythonocc-core/OpenCASCADE；版本依 `environment.docker.yml`。
- 前端使用 React、TypeScript 與 Vite；依 `package-lock.json` 安裝依賴。
- 後端使用 FastAPI、SQLAlchemy；部署預設使用 PostgreSQL。
- CAD 工作採隔離程序；同時工作數可由管理中心設定。
- Docker 部署由容器引擎管理程序生命週期；Windows 本機後端可由 `tools/backend_supervisor.py` 管理。

正式環境請使用 HTTPS，設定受信任的反向代理與 Cookie 安全選項。
