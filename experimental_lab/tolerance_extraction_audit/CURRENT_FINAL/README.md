# 公差提取實驗最終候選版本

這個入口代表目前實驗室中準備日後合併到 Main 的公差提取候選流程；不會修改正式系統或正式案例資料庫。

目前包含：

- 原生 DXF 公差數值提取。
- 2D 尺寸附著、投影與 BRep 拓撲驗證。
- 整體長度、單件複合跨度與直徑語意恢復。
- 扇葉曲線公差與完整風扇組立中央剖面投影。
- XCAF 組件實際裝配位置保留與剖面輪廓來源追蹤。
- 單件特徵、單件複合跨度、跨零件組立跨度三層分類。

組立件流程不使用檔名或固定座標判斷。它沿用完整組立中央剖面的註冊結果，分別投影每個 placed occurrence，再將 DXF 尺寸端點歸屬到來源零件。只有兩端輪廓歸屬與 STEP 量測同時通過時才升級為 `VERIFIED`；推測出的端點只保留為 `CANDIDATE_ONLY`。

執行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

輸出位於 `experiments/<date>/current_final/`。每次合併新規則時必須執行 targeted tests 與 benchmark；只有完整幾何證據的案例可進入 `AUTO_VERIFIED`。

最近一次驗證摘要記錄在 `validation_latest.json`。

## 資料庫批次公差提取

10 個產品資料夾的全量 2D 公差 staging 提取使用：

```powershell
.\pyoccenv\python.exe experimental_lab/tolerance_extraction_audit/common/scripts/run_database_tolerance_full_extract.py `
  --config experimental_lab/tolerance_extraction_audit/CURRENT_FINAL/database_full_extract_config.json `
  --output-dir experimental_lab/tolerance_extraction_audit/experiments/2026-10-10/database_full_v1 `
  --workers 4
```

此流程會掃描資料夾內所有 DWG，使用唯一同名 DXF 快取提取原生尺寸公差，並將跨產品資料夾重複出現的共用圖紙標為非 canonical occurrence，避免同一案例被重複學習。輸出仍是實驗 staging：所有記錄皆為 `PENDING_3D_LINK`，不得因此宣稱已找到 3D 特徵，也不會寫入正式案例資料庫。

完成 2D staging 後，批次 3D 特徵查找入口為：

```powershell
.\pyoccenv\python.exe experimental_lab/tolerance_extraction_audit/common/scripts/run_database_feature_link_batch.py `
  --config experimental_lab/tolerance_extraction_audit/CURRENT_FINAL/database_full_extract_config.json `
  --extraction-report experimental_lab/tolerance_extraction_audit/experiments/2026-10-10/database_full_v1/database_tolerance_full_report.json `
  --output-dir experimental_lab/tolerance_extraction_audit/experiments/2026-10-10/database_feature_link_current_final_v1 `
  --workers 6
```

此流程依序使用嚴格 STEP／DXF 名稱證據、XCAF 葉零件配對及全域投影幾何搜尋，並以主視圖優先、失敗項才進入多視圖恢復的方式執行環形、單件複合跨度與一般 BRep 特徵查找。全域幾何配對若沒有尺寸端點到 BRep 的證據，只能列為 `CANDIDATE_ONLY`。AL0W 已完成的剖半風扇、旋轉包絡與跨零件組立結果，最後由 `finalize_database_feature_link_report.py` 以較高優先級合併；候選案例可搜尋但必須標示工程師覆核。
