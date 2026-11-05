# 部署說明

## 準備

安裝 Docker Engine 與 Docker Compose，從乾淨版本取得程式碼。
將 `.env.example` 複製為 `.env`，填寫真正的部署設定；`.env` 不得提交或放進映像。

必要設定：

| 變數 | 用途 |
| --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL 密碼 |
| `CAD_ADMIN_EMAIL` | 首次建立的管理員帳號 |
| `CAD_ADMIN_PASSWORD` | 首次建立的管理員密碼 |
| `CAD_CREDENTIAL_ENCRYPTION_KEY` | Fernet 金鑰，需穩定保存及備份 |
| `CAD_COMPANY_EMAIL_DOMAINS` | 允許的公司 Email 網域 |
| `CAD_CORS_ORIGINS` | 實際前端來源 |
| `CAD_COOKIE_SECURE` | HTTPS 部署設定為 `1` |

Fernet 金鑰可在已安裝 cryptography 的環境中產生：

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 私有資料掛載

| 主機位置／Volume | 容器位置 | 內容 |
| --- | --- | --- |
| `./models` | `/data/models` | 私有上傳模型 |
| `./auto_2d_drawing/output` | `/data/output` | 產圖、標註與系統狀態 |
| `./auto_2d_drawing/templates` | `/data/templates` | 通用繪圖預設與受控自訂設定 |
| `./auto_2d_drawing/tolerance/data` | `/data/tolerance` | 私有公差案例庫 |
| `CAD_COMPANY_DATA_DIR` | `/data/company-reference` | 唯讀公司參考圖面 |
| `forcecon_postgres_data` | PostgreSQL data directory | 帳號、稽核紀錄與公司資料 |

程式碼不附公司案例庫。若需案例推薦，請由公司內部安全管道提供 `feature_case_base.json` 至公差資料掛載目錄。
請勿把模型、資料庫備份或真實資料放進 Docker build context 或 Git。

## 啟動與檢查

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 backend
```

健康檢查：`GET /api/health`。管理入口：`/admin`。
全新環境會建立資料表與設定的管理員帳號；不會匯入原主機的使用者、模型或案例內容。

## 維運

- 以受控方式備份 PostgreSQL、模型、輸出、公差案例與加密金鑰。
- 不要任意更換 `CAD_CREDENTIAL_ENCRYPTION_KEY`，否則既有加密資料可能無法解密。
- Docker 程序重啟使用 `docker compose restart backend`；本機 Windows 模式使用外部 supervisor。
- 對外部署使用 HTTPS，明確設定反向代理信任來源；不要信任任意來源的轉送 IP 標頭。
- 更新前先確認沒有執行中的 CAD 工作，依工作負載規劃維護時間。
- 容量測試結論需以部署主機與實際模型重新驗證。

## 發布檢查

```sh
python tools/check_release_content.py --tree HEAD
python tools/generate_system_api_graph.py --check
```

發布檢查會拒絕模型、圖面、資料庫、私有資料目錄與開發進度紀錄。
已提交過的敏感資料不會因刪除目前檔案而自動從舊提交、其他分支或遠端快取消失。
