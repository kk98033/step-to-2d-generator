# 開發現況、已知限制與路線圖

更新日期：2026-09-23

## 1. 當前定位

系統已由單純 STEP 轉 2D PoC 發展為包含 3D 特徵、智慧標註、向量工程圖、公司歷史公差證據與 CAD-RAG 推薦的整合平台。但不同子系統成熟度不一：

| 子系統 | 狀態 | 說明 |
| --- | --- | --- |
| STEP/組合件讀取 | 可使用 | 可讀 STEP、拆件、輸出 tree/STL |
| HLR 多視圖 | 可使用 | 已支援主要正投影視圖 |
| DXF/PDF/PNG/SVG 輸出 | 可使用 | 仍需更多 CAD 軟體相容性回歸 |
| 3D 特徵查找 | 持續擴充 | 軸類較成熟，通用件仍有 heuristic |
| 智慧標註 | 可試用 | 支援規則選取、多視角、樣板、客製出圖 |
| 2D 公差提取 | 已完成第一版全量化 | 原生 dimension 與明確公差可大量提取 |
| 2D 特徵定位 | 實驗中 | 覆蓋率低，保持拒答策略 |
| CAD-RAG 公差推薦 | 開發中 | trace 與隔離完成，合格案例仍少 |
| Docker | 定義完成、待實機 build | 本機若無 Docker CLI 無法做 runtime 驗證 |
| 外部 API | 已有完整路由 | 仍需版本化、schema model、認證與契約測試 |

## 2. 最近完成

### 公差資料可信化

- 移除種子／假案例依賴，重建真實 DXF 證據庫。
- 未核實尺寸不參與推薦。
- FeatureGraph taxonomy 對齊。
- 公差推薦明確區分採用案例、候選案例與 fallback。
- 來源案例可直接開啟 PDF/SVG/details。

### 2D 特徵推定

- 建立 rule-based 可解釋推定，不假裝是 AI vision 模型。
- 尺寸 definition point 與圖面 primitive 幾何附著。
- 稀疏視圖 clustering。
- 圓端視圖與另一視圖可見／隱藏線對 correlation。
- 結果仍標示非 3D identity、不可自動進 RAG。

### 歷史版次與重複資料

- `-RNN` 最高版篩選。
- 一張圖面一張 Inspector 卡片。
- 只合併同 category、nominal、公差及量測端點的真正重複 dimension。

### 文件與部署

- 首頁加入公差案例檢視器入口。
- README 改為文件入口。
- 新增架構、特徵、公差、繪圖、頁面、API、Docker 文件。
- 建立 Miniforge/pythonocc-core 容器與 Compose。
- 新增 `/api/health`。
- 路徑改為 environment configurable。
- 新增版本化外部神經網路公差預測 API，並在標註 UI 提供逐規則來源選擇。

## 3. 最新公差資料基準

| 指標 | 結果 |
| --- | ---: |
| 原始 DXF | 1,744 |
| 最新版 DXF | 1,697 |
| 排除舊版／同版重複 | 47 |
| 最新版 STEP | 223 |
| 完整同名同版 STEP/DXF pair | 10 |
| 原生尺寸候選 | 34,385 |
| 有效明確公差 | 23,518 |
| 真正重複 dimension 排除 | 241 |
| 最終證據 | 23,516 |
| STEP 核實可推薦案例 | 14 |
| 2D 高信心類型推定 | 37 |
| 2D review candidate | 833 |
| 2D unresolved | 22,648 |
| DXF parse error | 0 |

這些數字是覆蓋統計，不是模型準確率。

## 4. 現在正在做的核心問題

### 尺寸到特徵 identity

已能讀出大量公差，但多數尺寸尚不能唯一對應到 STEP feature。主要原因：

- 很多歷史圖面沒有同版 STEP。
- 2D 視圖座標與 STEP HLR 尚未完成穩定 registration。
- 同一 nominal 在同一模型內可能出現多次。
- 組合件圖、剖視、局部放大與 reference dimension 增加歧義。

### 公差推薦證據量

目前只有 14 筆 `AUTO_VERIFIED`，推薦系統雖能防止幻覺，但多數規則會落入 Tier 2/3。下一階段重點是增加合格證據，不是放寬 gate。

### API 產品化

- Route body 多使用 `Dict[str, Any]`，OpenAPI schema 不夠嚴格。
- Job、全域公差與部分狀態是記憶體。
- 沒有 API authentication、audit、idempotency key。
- 外部神經預測接口可設定 API key；其他寫入接口仍待統一認證與角色權限。
- 部分 response 含本機 path，不適合跨組織。

## 5. 短期路線圖

### P0：可靠對接

- 為所有 request/response 建立 Pydantic model。
- 加 `/api/v1` 與 schema version。
- 建立 OpenAPI snapshot 與 contract test。
- 加 request ID、統一 error code、結構化 log。
- Job 持久化並提供取消／重試。

### P0：公差驗證

- 建立工程師 ground truth 工具與抽樣規範。
- 顯示 accepted/rejected/ambiguous 狀態。
- 保存 engine version、source handle 與人工決策。
- 產生 precision/recall/coverage 報告。

### P1：2D/3D registration

- 用 STEP HLR 產生與公司 DXF 可對齊的視圖。
- 尺度、旋轉、平移與鏡射候選配準。
- DXF dimension endpoint 對到 HLR edge，再回到 B-Rep face。
- 用多視圖一致性解除同值特徵歧義。

### P1：GD&T 與 VLM/OCR

- 原生 DXF INSERT/MTEXT/LEADER/GD&T frame parser。
- 掃描 PDF 的 OCR/VLM 作候選提取。
- 幾何與標準規則驗證模型輸出。
- 保存 bounding box、原始 crop 與 token evidence。

### P1：容器與部署

- 在有 Docker Engine 的 CI 實際 build。
- 加 image vulnerability scan 與 SBOM。
- non-root runtime、read-only root filesystem。
- 將 cache、case DB 與 template 移到受控 data volume。

## 6. 中長期路線圖

- 與 PLM/BOM 串接料號、revision、材料、配合件與功能需求。
- 建立風扇、馬達、軸、外框等 product-family 專用 feature profile。
- 納入製程能力、量具、供應商與成本資料。
- 公差 stack-up 與裝配鏈分析。
- 工程師審核工作流、電子簽核與版本回滾。
- 模型／規則 A/B 評估與 drift 監控。
- 多使用者權限、專案隔離與稽核。

## 7. 已知技術債

- `App.tsx` 過大，需拆元件與 API client。
- `server.py` 集中太多 route 與檔案邏輯，需拆 router/service/repository。
- JSON 案例庫接近大型檔案極限，讀寫與 Git diff 成本高。
- 公司圖面 index 保留 Windows absolute path，不利容器可攜性。
- `open-local` 與外部 reference 路徑屬桌面／環境特定能力。
- CORS 開放、上傳未限流、無認證。
- `global_tolerances` 相容 API 語意容易被誤用。

## 8. 完成定義

一個功能不是「畫面看起來有結果」就算完成。最低要求：

- 有明確輸入／輸出與失敗模式。
- 可追溯到來源 entity/feature/case。
- 不以猜測冒充核實。
- 有單元或整合測試。
- API 與文件同步。
- 重要資料寫入可回復、可稽核。
- 容器／本機至少一個支援環境完成 smoke test。
