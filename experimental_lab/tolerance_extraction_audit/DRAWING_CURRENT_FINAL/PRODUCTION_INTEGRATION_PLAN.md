# 實驗 Main 與網頁正式系統整合規劃

更新日期：2026-10-20
目前狀態：`PLANNED_NOT_INTEGRATED`

## 現況

目前三條流程仍彼此分開：

| 使用情境 | 現行入口 | 狀態 |
|---|---|---|
| 網頁上傳 STEP、拆件與初始產圖 | `auto_2d_drawing.batch_generate.batch_generate` | 正式系統使用中 |
| 網頁中由工程師選取特徵後產生智慧標註 | `auto_2d_drawing.smart_annotation_engine.SmartAnnotationEngine` | 正式系統使用中 |
| 新版幾何偵測、Dimension Intent、多視圖與選取式繪圖 | `DRAWING_CURRENT_FINAL/experimental_main.py` | 實驗基準，尚未接入正式網頁 |

三者會共用部分 STEP 載入與投影元件，但尚未共用完整的分析結果、標註候選、視圖規劃與 renderer orchestration。因此，網頁 Main 的初始圖面與後續智慧標註目前可能由不同判斷邏輯產生。

## 目標架構

正式合併後，網頁 Main 與智慧標註應共用同一套新版核心資料流：

```text
STEP／B-Rep
  -> 統一幾何與特徵分析
  -> 統一 Dimension Intent／候選規則
  -> 統一視圖與 section plan
     -> Main 自動模式：由 orchestration 自動選取
     -> 智慧標註模式：由工程師檢視、增刪或調整
  -> 統一多視圖 renderer、layout 與 DXF audit
```

「合併」不代表把所有邏輯塞進同一個類別。正式系統可保留自動產圖與互動標註兩個 API／操作入口，但兩者必須消費同一份版本化分析結果與標註資料契約，並使用同一套 renderer。

## 分階段整合

### Phase 1：只讀分析接軌

- 在正式後端建立 adapter 呼叫實驗 Main 的分析函數。
- 對每個模型／零件保存版本化 `drawing_engine_report.json`。
- 網頁先顯示新版候選與證據，不取代既有正式產圖。
- 相同 STEP 雜湊直接讀取分析快取，避免每次智慧標註都重跑 OpenCASCADE。

### Phase 2：智慧標註共用候選

- 將實驗 Main 的 `DimensionIntent` 轉成正式智慧標註可編輯的穩定 DTO。
- 智慧標註改為讀取已保存的候選；工程師只提交選取、取消與必要參數。
- 後端驗證選取仍對應同一個模型版本，拒絕過期或 `FAIL` 的候選。

### Phase 3：統一 renderer

- Main 自動模式與智慧標註模式都派送至 `minimal_multiview_drawing` 及其 feature renderer bridge。
- 統一 DXF/PDF/PNG/SVG 輸出位置、artifact ownership、快取鍵與 audit 報告。
- 在這個階段前，不移除或修改舊版 `dimension_engine.py` 與 `extractors/`。

### Phase 4：受控切換正式 Main

- 以 feature flag 對管理員或指定模型啟用新版產圖。
- 比較舊版與新版的特徵選取、標註實體、視圖位置、DXF audit 及處理時間。
- 通過跨類型模型 regression、多人併發與失敗回復測試後，才將新版設為預設。
- 舊管線先保留為可回復 fallback；完成觀察期後再另行決定退場方式。

## 必須維持的系統行為

- 每個模型版本只建立一份可重用的幾何分析結果。
- Main 自動選取與智慧標註人工選取必須可追溯到相同 `DimensionIntent` ID。
- 智慧標註調整不得無條件重跑完整 STEP 分析；只有模型版本或會影響幾何的輸入變更時才失效。
- 長時間 CAD 工作必須走受限 worker／queue，並能在頁面重新整理後恢復進度。
- 所有輸出都必須驗證選取特徵、原生標註實體、視圖位置與 audit 結果，不能只以檔案存在判定成功。

## 正式切換完成條件

- `ENGINE_MANIFEST.json` 的 `production_integrated` 改為 `true`。
- 網頁上傳與智慧標註都能回報相同的 analysis/version ID。
- 相同模型的重複智慧標註能命中分析快取；只重畫必要輸出。
- 自動模式與人工模式使用相同 renderer contract，且選取結果可重現。
- 通過至少一組非原開發模型的 regression、DXF audit、API 整合測試與多人併發測試。
- 正式系統保有可觀測的 queued／processing／finalizing／completed／error 狀態及安全回復路徑。

在以上條件完成前，`experimental_main.py` 仍是實驗基準，不應宣稱已成為網頁正式 Main。
