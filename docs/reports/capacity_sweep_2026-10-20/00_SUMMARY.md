# CAD 併發容量卡死門檻測試

測試日期：2026-10-20

## 結論

- 目前單一 FastAPI/Python 程序的**持續負載安全值是 1 個重型 CAD 工作**。
- 容量 1 完整通過；容量 2 曾通過短時間測試，但在本次連續突發、重試與前一階段工作累積後，後端程序於兩個智慧標註同時進行時被原生元件直接終止。
- 卡死門檻因此判定為：**2 個同時智慧標註工作在持續負載下不穩定**。測試在第一次程序終止後立即停止，沒有繼續測試 3、4，以免反覆破壞服務。
- 這不是「只能一名使用者登入」。一般 API 與登入可服務多人；限制的是 OpenCASCADE、SVG/PDF/PNG 等重型原生處理同時數量。
- 正式設定已恢復為分析 1、智慧標註 1；其他使用者應排隊或收到忙碌回應。要安全開放 2 個以上，必須先把 CAD 工作移到彼此隔離的 worker process。

## 判定標準

任一情況視為卡住或失敗：

1. Uvicorn/Python 程序退出或無法連線。
2. 健康端點失敗。
3. 工作逾時或長期停在 queued、processing、finalizing。
4. 用戶端收到連線重設或 transport error，而不是受控的 409/429。

## Level 1：通過

測試使用 5 個獨立帳號，重型容量設定為 1。

| 項目 | 結果 |
|---|---:|
| 一般 API | 25/25 成功 |
| 一般 API 吞吐量 | 241.1496 requests/s |
| 標註突發 | 1 成功、4 個受控 429 |
| 標註重試完成 | 5/5 |
| 標註重試總耗時 | 2.881701 秒 |
| STEP 分析 | 2/2 完成 |
| STEP 分析總耗時 | 20.39622 秒 |
| 重型期間健康探測 | 62/62 成功 |
| 健康探測 p95 | 0.405996 秒 |

完整資料：`level_1.json`

## Level 2：後端程序終止

容量切換為 2 後：

1. 5 個帳號登入及候選規則讀取成功。
2. 一般 `/api/models` 請求持續成功。
3. 兩個智慧標註工作同時輸出各自的 SVG、PDF、PNG。
4. 後續重試波次再次同時進入 SVG/PDF/PNG 轉換時，Uvicorn/Python 程序突然退出。
5. 壓測端收到 Windows `ConnectionResetError [WinError 10054]`；後端沒有 Python traceback，符合原生函式庫／轉換元件在程序層級終止的特徵。
6. 原始 stdout 最後停在兩組智慧標註檔案的 PDF/PNG 輸出階段，沒有正常 shutdown 訊息。

因後端已死亡，Level 2 無法產生完整階段 JSON。這次失敗本身就是容量門檻證據，並保留於 `.codex_runtime/backend_20261020_023424.out.log`；runtime log 不納入 Git。

## 安全處置

- 測試在首次程序終止後停止，未執行 Level 3、4。
- 後端已重新啟動，健康檢查恢復 HTTP 200。
- 分析與智慧標註容量均恢復為 1/1。
- 5 個 `sweep-*` 臨時帳號全部停用。
- 壓測報告不包含密碼或 Cookie。

## 要提高到 2 以上的必要工作

1. 將分析與智慧標註移出 Web API 程序，以獨立 worker process 執行。
2. 每個 worker 擁有自己的 OpenCASCADE、PDF 與 PNG 轉換程序狀態；worker crash 不得帶走 API。
3. 使用持久化 FIFO queue 保存 queued、processing、finalizing、completed、error 狀態。
4. 加入 worker heartbeat、逾時終止及工作重試上限。
5. 完成 2、3、4 worker 的相同階梯測試後，才提高部署硬上限。
