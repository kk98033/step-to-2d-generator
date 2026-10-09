# 帳號、資料治理與公差推薦分區 API

本文件說明公司 Email 登入、帳號批次建立、初始密碼交付、回收桶，以及公差案例標籤分區。管理頁位於 `/admin/accounts`；互動式 OpenAPI 位於 `/docs`。

## 1. 身分與公司 Email

帳號的唯一識別是完整公司 Email。登入畫面只要求輸入 `@` 前面的 local-part，再由使用者選擇公司域名。可用域名由環境變數設定：

```env
CAD_COMPANY_EMAIL_DOMAINS=forcecon.com.tw,forcecon-group.com
CAD_ADMIN_EMAIL=admin@forcecon.com.tw
```

前端呼叫 `GET /api/auth/config` 取得域名。登入成功後，後端寫入：

- `cad_session`：HttpOnly、SameSite=Strict 的工作階段 Cookie。
- `cad_email_domain`：保存最近選取域名一年，只包含非敏感的域名值。

登入：

```http
POST /api/auth/login
Content-Type: application/json

{
  "email_local": "engineer.chen",
  "email_domain": "forcecon.com.tw",
  "password": "current-password"
}
```

系統不提供「忘記密碼自行重設」API。知道目前密碼的使用者可呼叫 `PUT /api/auth/password` 主動改密；忘記密碼則只能由管理員重設。

## 2. 初始密碼生命週期

管理員建立帳號時不輸入密碼。伺服器使用密碼學安全亂數產生初始密碼，PBKDF2 雜湊用於登入驗證，另以 Fernet 加密保存可交付的初始密碼。

```env
CAD_CREDENTIAL_ENCRYPTION_KEY=<Fernet key>
```

正式環境必須明確設定此金鑰並交由 secrets manager 管理。若開發環境未設定，系統會以資料庫 URL 與管理員密碼衍生穩定金鑰；此回退方式不應用於正式部署。

生命週期如下：

1. 建立或重設帳號後，`must_change_password=true`。
2. 管理員的 `GET /api/admin/users` 可看到 `initial_password`。
3. 使用者以初始密碼登入後仍會被介面要求改密。
4. 成功呼叫 `PUT /api/auth/password` 後，初始密碼密文立即刪除、所有既有 Session 撤銷。
5. 此後管理員只能再次重設，不能讀取使用者的新密碼。

管理員重設：

```http
POST /api/admin/users/{user_id}/reset-password
Content-Type: application/json

{}
```

回傳的 `initial_password` 只應透過受控管道交付。

## 3. 單筆與 Excel／CSV 批次建立

單筆建立：

```http
POST /api/admin/users
Content-Type: application/json

{
  "email": "engineer.chen@forcecon.com.tw",
  "display_name": "陳工程師",
  "role": "ENGINEER"
}
```

批次匯入：

```http
POST /api/admin/users/import
Content-Type: multipart/form-data

file=@accounts.xlsx
```

支援 `.xlsx` 與 UTF-8 `.csv`。欄位：

| 欄位 | 必填 | 說明 |
|---|---:|---|
| `email` | 是 | 完整公司 Email，域名必須在允許清單 |
| `display_name` 或 `name` | 是 | 顯示名稱 |
| `role` | 否 | `ENGINEER`（預設）或 `ADMIN` |

中文欄名 `電子郵件`、`信箱`、`姓名`、`名稱`、`角色` 也可辨識。系統先檢查整批的空值、角色、域名、檔內重複與資料庫重複，再開始建立。回應包含每個帳號的初始密碼。

## 4. 公司 Email 寄信整合

每次建立或重設密碼都會建立待送通知。公司郵件服務以獨立 API key 讀取：

```env
CAD_EMAIL_DELIVERY_API_KEY=<random secret>
```

```http
GET /api/integrations/email/credential-notifications?limit=100
X-API-Key: <CAD_EMAIL_DELIVERY_API_KEY>
```

每筆通知包含 `id`、`recipient_email`、`initial_password`、狀態與嘗試次數。寄送後必須回報：

```http
POST /api/integrations/email/credential-notifications/{notification_id}/ack
X-API-Key: <CAD_EMAIL_DELIVERY_API_KEY>
Content-Type: application/json

{
  "status": "SENT",
  "provider_message_id": "mail-provider-id"
}
```

失敗時使用 `status=FAILED` 與 `error`。FAILED 項目仍可再次拉取。公司郵件服務不應記錄或長期保存初始密碼。

## 5. 可復原刪除與保留期限

工程師刪除標註成品或個人公差案例時，系統保存可還原快照。刪除成品會一併保存它衍生的案例與標籤關聯；輸出檔案不會在軟刪除階段移除。

工程師 API：

- `GET /api/engineer/trash`
- `POST /api/engineer/trash/{trash_id}/restore`

管理員 API：

- `GET /api/admin/trash?owner_user_id={id}`：查看全部或指定工程師。
- `POST /api/admin/trash/{trash_id}/restore`：跨帳號復原。
- `DELETE /api/admin/trash/{trash_id}`：永久刪除快照。
- `POST /api/admin/trash/purge-expired`：清除已超過期限的快照。
- `GET|PUT /api/admin/settings/trash-retention`：讀寫保留天數，範圍 1–3650。

預設值由 `CAD_TRASH_RETENTION_DAYS=30` 設定，資料庫中的管理員設定優先於環境預設。

## 6. 公差推薦標籤

標籤用來把歷史案例切成可組合的資料範圍。支援維度：

- `COMPANY_DATABASE`：公司共用資料庫或資料來源層級。
- `CUSTOMER`：客戶或客戶群。
- `PART_TYPE`：軸、孔、風扇、殼體等零件種類。
- `ENGINEER`：工程師群組；個人案例仍會額外受 owner 隔離。
- `PROJECT`、`MATERIAL`、`PROCESS`、`CUSTOM`。

查詢與管理：

- `GET /api/recommendation-tags`：所有登入者取得可用標籤。
- `GET /api/admin/recommendation-tags`：包含停用標籤。
- `POST /api/admin/recommendation-tags`：建立標籤。
- `PATCH /api/admin/recommendation-tags/{tag_id}`：改名、描述、啟停。
- `PUT /api/admin/company-cases/{case_id}/tags`：為公司歷史案例指定標籤。
- `GET /api/admin/company-cases/tags`：取得公司案例與標籤關係。

標註成品儲存時：

```json
{
  "model_id": "model_batch",
  "part_id": "shaft-01",
  "feature_records": [],
  "storage_tag_ids": ["customer-tag-uuid", "shaft-tag-uuid"]
}
```

`POST /api/annotation/render` 會把標籤同時寫到成品與本次衍生的每筆工程師公差案例。

推薦時：

```json
{
  "model_id": "model_batch",
  "part_id": "shaft-01",
  "candidate_rules": [],
  "recommendation_tag_ids": ["customer-tag-uuid", "shaft-tag-uuid"],
  "recommendation_tag_match": "ALL"
}
```

- `ANY`：案例符合任一選取標籤即可。
- `ALL`：案例必須同時具有全部標籤。
- 不選標籤：維持原有行為，搜尋所有可用案例。

篩選同時作用於公司 CAD-RAG 案例與目前登入工程師的個人案例；工程師永遠無法讀取其他工程師的私人案例。

## 7. 部署檢查

正式環境至少設定：

```env
CAD_COMPANY_EMAIL_DOMAINS=company.example,subsidiary.example
CAD_ADMIN_EMAIL=admin@company.example
CAD_ADMIN_PASSWORD=<unique secret>
CAD_CREDENTIAL_ENCRYPTION_KEY=<Fernet key>
CAD_EMAIL_DELIVERY_API_KEY=<integration secret>
CAD_COOKIE_SECURE=1
CAD_TRASH_RETENTION_DAYS=30
```

建議在反向代理後使用 HTTPS，並將 PostgreSQL、加密金鑰與 API key 交由公司 secrets 管理平台維護。備份必須同時包含主資料庫與私人輸出 Volume，否則回收桶雖能還原中繼資料，對應 PDF/DXF 仍可能遺失。
