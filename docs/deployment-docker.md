# Docker / Conda / PythonOCC 部署

本文件說明如何把 FastAPI、React 靜態前端、OpenCASCADE/pythonocc-core 與所有 Python 依賴封裝成單一後端容器。

## 1. 為什麼使用 Conda 容器

`pythonocc-core` 依賴 OCCT 與多個原生函式庫，單純 `pip install` 不足。專案本機在 PowerShell 透過：

```powershell
enable-conda
conda activate pyoccenv
```

啟用環境。Docker 不會執行此 PowerShell helper，而是在 image build 時直接建立 `/opt/conda/envs/pyoccenv`，再把環境的 `bin` 放入 PATH。

## 2. 檔案

| 檔案 | 用途 |
| --- | --- |
| `Dockerfile` | Node frontend build + Miniforge runtime 多階段映像 |
| `environment.docker.yml` | Linux 可重建的最小 Conda/Pip 規格 |
| `docker-compose.yml` | port、volume、environment、healthcheck |
| `.dockerignore` | 排除模型、輸出、env、快取與機密研究資料 |

`environment.yml` 是目前 Windows 環境的完整快照，包含 Windows runtime package，不適合直接拿到 Linux container。容器使用獨立的 `environment.docker.yml`。

## 3. 映像階段

### Frontend build

```dockerfile
FROM node:22-bookworm-slim
RUN npm ci
RUN npm run build
```

以 lockfile 重建 React/Vite `dist`。`VITE_API_BASE_URL` 未設定時前端使用同源 API，適合單一容器。

### Runtime

- Base：`condaforge/miniforge3`。
- 建立 Conda `pyoccenv`。
- 安裝 `pythonocc-core=7.9.3` 與 Python web/CAD 套件。
- 安裝 OpenGL/X11 runtime 與 Noto CJK 字型。
- 複製應用程式與前端 dist。
- Uvicorn 以一個 worker 啟動。

## 4. 建置

先安裝 Docker Desktop，確認：

```powershell
docker --version
docker compose version
```

建置：

```powershell
cd D:\School\力致\app\step-to-2d-generator
docker compose build
```

第一次 build 需要下載 Node、Miniforge、OCCT 與 Python package，時間與映像大小都會較高。

## 5. 公司資料掛載

Windows PowerShell：

```powershell
$env:CAD_COMPANY_DATA_DIR = 'D:\School\力致\力致_ref'
docker compose up --build
```

Compose 會把它唯讀掛載為：

```text
/data/company-reference
```

公差 DXF 預設期待：

```text
/data/company-reference/temp_dxf_cache_ref
```

如果實際資料結構不同，覆寫 `CAD_TOLERANCE_DXF_DIR` 或調整 compose volume target。

若不設定 `CAD_COMPANY_DATA_DIR`，Compose 使用 repo 下的 `./company_data`。該目錄不應提交公司資料。

## 6. Volume

| Host | Container | Mode | 說明 |
| --- | --- | --- | --- |
| `./models` | `/data/models` | rw | 上傳 STEP |
| `./auto_2d_drawing/output` | `/data/output` | rw | 產圖 artifact |
| `./auto_2d_drawing/templates` | `/data/templates` | rw | 標註樣板 |
| `./auto_2d_drawing/tolerance/data` | `/data/tolerance` | rw | 公差案例庫與索引資料 |
| `${CAD_COMPANY_DATA_DIR}` | `/data/company-reference` | ro | 公司圖面與參考資料 |

Compose 會將案例庫與樣板掛載到 host，因此容器重建後仍保留。多人／多副本部署仍應移到有 transaction、lock 與 audit 的資料庫；JSON bind mount 只適合單一服務實例。

## 7. 環境變數

| Variable | Default in image | 說明 |
| --- | --- | --- |
| `CAD_MODELS_DIR` | `/data/models` | STEP 上傳目錄 |
| `CAD_OUTPUT_DIR` | `/data/output` | 產圖輸出 |
| `CAD_REFERENCE_DIR` | `/data/reference` | 專案 reference |
| `CAD_TEMPLATES_DIR` | `/data/templates` | 標註樣板持久層 |
| `CAD_TOLERANCE_CASE_DB` | `/data/tolerance/feature_case_base.json` | 公差案例庫 JSON |
| `CAD_EXTERNAL_PREDICTION_API_KEY` | 空 | 外部神經預測 API key；正式環境必須設定 |
| `CAD_NEW_EXAMPLE_DIR` | `/data/company-reference` | 公司範例樹 |
| `CAD_TOLERANCE_DXF_DIR` | `/data/company-reference/temp_dxf_cache_ref` | Inspector DXF fallback |
| `CAD_CN_FONT_PATH` | Noto Sans CJK | Linux 中文字型 |
| `PYTHONUNBUFFERED` | `1` | 即時 log |
| `PORT` | `8000` | 文件用途；CMD 目前固定 8000 |

## 8. 啟動與停止

```powershell
docker compose up -d
docker compose ps
docker compose logs -f backend
docker compose down
```

不要使用 `down -v`，除非確定要移除命名 volume；目前 compose 使用 bind mount，但仍應養成避免破壞性指令的習慣。

## 9. 健康檢查

Dockerfile 與 Compose 都設定：

```text
GET http://localhost:8000/api/health
```

手動：

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

應回：

```json
{
  "status": "ok",
  "service": "forcecon-step-to-2d",
  "version": "0.4.0-dev",
  "models_dir": "/data/models",
  "output_dir": "/data/output"
}
```

## 10. Smoke test

### Web 與 OpenAPI

```powershell
Invoke-WebRequest http://localhost:8000/ -UseBasicParsing
Invoke-WebRequest http://localhost:8000/tolerance-inspector.html -UseBasicParsing
Invoke-RestMethod http://localhost:8000/openapi.json
```

### 上傳 STEP

```powershell
$form = @{ file = Get-Item '.\models\sample.stp' }
$job = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/upload -Form $form
$job
```

Windows PowerShell 版本若不支援 `-Form`，可使用 curl：

```powershell
curl.exe -F "file=@models/sample.stp" http://localhost:8000/api/upload
```

## 11. 一個 Uvicorn worker 的原因

目前：

- Job store 是 Python dict。
- global tolerance 是記憶體物件。
- 部分 cache/service 在 process 初始化。

多 worker 會讓 job 在不同 process 不可見。要水平擴充需先：

1. Redis/RQ/Celery 或資料庫 job store。
2. artifact 共享 storage。
3. case/template transaction 與 lock。
4. request ID 與 centralized log。

## 12. 字型

Windows 使用 Microsoft JhengHei；Linux image 安裝 Noto CJK。字型 metric 不同可能造成文字寬度與排版略有差異。若公司有指定合法字型：

1. 透過受控 build secret 或 private base image 加入。
2. 設定 `CAD_CN_FONT_PATH`。
3. 使用一組 golden drawings 比較 PDF/DXF 文字位置。

不要把未授權的 Windows 字型提交到公開 repository/image。

## 13. OpenCASCADE Headless 注意事項

雖然主要工作不需要 GUI，pythonocc/渲染依賴仍可能載入 GL/X11 library，因此 image 安裝：

- `libgl1`
- `libglib2.0-0`
- `libsm6`
- `libxext6`
- `libxrender1`

如果未來導入真正 off-screen GPU renderer，應另外建立 GPU image；目前不需要 X server。

## 14. 快取與權限

- SVG/PDF cache 預設位於 repo 內的 tolerance data cache；正式容器可再改成 `/data/cache`。
- `/data/output` 與 `/data/models` 必須讓 container user 可寫。
- 目前 base image 預設 user 權限較寬；生產 image 應建立 non-root user，並調整 bind mount ownership。

## 15. 安全

- 公司資料 volume 必須 `:ro`。
- 不把模型、輸出與圖面 COPY 進 image；`.dockerignore` 已排除常見副檔名與資料夾。
- 對外服務應加 TLS reverse proxy、認證、上傳限制及網路 allowlist。
- `/api/tolerance/open-local` 在 Linux 不可用，也不應暴露給遠端使用者。
- CORS 現行為開發開放設定，正式部署需收斂。

## 16. 疑難排解

### `ModuleNotFoundError: OCC`

確認啟動使用：

```text
/opt/conda/envs/pyoccenv/bin/python
```

並檢查：

```powershell
docker compose exec backend python -c "from OCC.Core.STEPControl import STEPControl_Reader; print('OCC OK')"
```

### 首頁 404 或舊版 UI

確認 Docker frontend build 成功，且 `/app/web_app/frontend/dist/index.html` 存在。不要把 host 的舊 dist mount 覆蓋容器 dist。

### Inspector 有資料但圖面 404

案例庫 metadata 可能仍保存 Windows 原始路徑；容器必須掛載對應 DXF 並設定 `CAD_TOLERANCE_DXF_DIR`。長期解法是重建可攜式 source URI，而不是保存絕對路徑。

### 中文亂碼／方框

確認 Noto CJK 已安裝且 `CAD_CN_FONT_PATH` 指向實際檔案。DXF 開啟端也必須有相容字型。

### Job 一直 processing

檢查 container log、模型大小、記憶體與 CPU。Job 沒有跨重啟恢復；container restart 後需重新提交。

## 17. 本次驗證邊界

Docker 定義可做靜態檢查，但實際 build 仍需有 Docker Engine 與網路可下載 base image/Conda package。若執行環境沒有 Docker CLI，不能把「檔案已建立」誤報為「映像已成功建置」。交付時應記錄實際執行過的命令與未執行原因。
# PostgreSQL 與帳號系統

正式部署的 `docker-compose.yml` 會啟動 `database`（PostgreSQL 17）和 `backend`。第一次啟動前必須將 `.env.example` 複製為 `.env` 並設定 `POSTGRES_PASSWORD`、`CAD_ADMIN_PASSWORD`。正式 HTTPS 環境同時設定 `CAD_COOKIE_SECURE=1` 與明確的 `CAD_CORS_ORIGINS`。

PostgreSQL volume 與 `/data/output` 必須一起備份。詳細權限、資料表與個人化推薦流程見 [account-and-personalization-system.md](account-and-personalization-system.md)。
