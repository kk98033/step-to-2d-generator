# 實驗繪圖引擎目前基準（DRAWING_CURRENT_FINAL）

這個目錄是新版 2D 繪圖實驗的唯一目前基準。`experiments/YYYY-MM-DD/01...11...`
保留每一步的證據與圖片；本目錄只保存已通過測試、準備供下一階段串接的入口與版本清單。

它不會修改舊版 `auto_2d_drawing/dimension_engine.py` 或 `extractors/`，也尚未部署到正式網頁。

## 正式系統整合狀態

目前網頁 Main 初始產圖、網頁智慧標註與本實驗 Main 仍是三條不同管線；共用部分底層元件不代表已完成正式整合。
未來會讓自動模式與人工智慧標註模式共用版本化分析結果、`DimensionIntent`、視圖規劃與 renderer，
並以 adapter、快取及 feature flag 分階段接入，不會直接覆寫舊版引擎。

詳細現況、分階段合併方式與完成條件請見 [`PRODUCTION_INTEGRATION_PLAN.md`](PRODUCTION_INTEGRATION_PLAN.md)。

## 未知模型怎麼自動觸發

不是先猜檔名是不是風扇，也不是為每個料號建立一條分支。入口會依序執行：

1. 以 STEP/B-Rep、`Bnd_Box` 和模型主軸建立標準化幾何。
2. 對候選視角做 HLR 投影。
3. 執行所有已註冊的通用偵測器；每個偵測器都回傳適用性、信心、證據與失敗原因。
4. 將可用結果正規化為 `DimensionIntent`。
5. 由歷史圖面案例決定「哪些尺寸常被工程師選用」，但幾何值仍由 STEP 驗證。
6. 依尺寸語意派送至線性、直徑、半徑、角度等 renderer，再做避讓排版。
7. 高信心結果為 `AUTO_VERIFIED`；幾何合理但證據不足者保留為 `REVIEW_CANDIDATE`。

剖半、環狀輪轂、徑向分隔等仍可有專門演算法，但必須由幾何證據觸發，不能由
`fan`、`blade` 或料號字串觸發。因此它是「通用主線 + 幾何能力插件」，不是一套只會畫風扇的引擎。

## 固定入口

先在任意 STEP 上執行分析與尺寸意圖整合：

```powershell
cd D:\School\力致\app\step-to-2d-generator
.\pyoccenv\python.exe .\experimental_lab\tolerance_extraction_audit\DRAWING_CURRENT_FINAL\experimental_main.py `
  --step <STEP檔案> `
  --output .\scratch\drawing_current_final
```

輸出 `drawing_engine_report.json`。目前這個入口是 analysis-only：它會告訴後續引擎能畫什麼、
為何能畫，以及哪些仍需人工確認；尚不宣稱已完成整張工程圖的自動 DXF 編排。

驗證目前基準：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\experimental_lab\tolerance_extraction_audit\DRAWING_CURRENT_FINAL\run_regression.ps1
```

加上 `-IncludeSmoke -StepPath <STEP檔案>` 可額外跑未知模型入口 smoke test。

## 維護規則

- `ENGINE_MANIFEST.json` 是能力、來源實驗、狀態與已知缺口的唯一清單。
- 新實驗先留在編號目錄；使用者確認「合併」後，才更新這裡的版本、入口與 regression。
- 主線只 import `common/scripts/` 的實作，不複製演算法。
- 每次合併至少要跑單元測試、manifest 驗證，以及一個非原開發模型的 smoke test。
- `CHANGELOG.md` 記錄每次主線吸收了哪些實驗能力。

## 下一階段（實驗 12）

目前最大缺口是 unknown-model orchestration：自動選擇要標註的尺寸關係、視角與 section plan，
並在跨類型模型上量測 false positive。實驗 12 應以這個入口為基準，不再另開一套孤立引擎。
