# 公差提取實驗標準作業程序

## 1. 實驗單位

- 一天一個 `experiments/YYYY-MM-DD/`。
- 當日可重跑 `latest`，但不得把不同日期的結果放在同一目錄。
- 跨日必須指定新 `-Date`，不覆寫舊日期證據。

## 2. 執行前

1. 確認 STEP/DXF 型號與 revision。
2. 確認使用的 dataset 與 case DB 快照。
3. 在當日 README 先寫假設、成功條件與預計指標。
4. 不為了讓當日 benchmark 變好而偷看 test label 調參。

## 3. 標準執行

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Date YYYY-MM-DD -WithBenchmark
```

此指令會：

1. 依日期建立目錄。
2. 將實際設定寫入 `resolved_config.json`。
3. 從 STEP 執行純 HLR 投影。
4. 產生證據包與 SHA-256 manifest。
5. 在當日 `benchmark/` 儲存完整報告與 baseline delta。
6. 寫入 `run_summary.json`。

## 4. 當日 README 必填欄位

- 今日假設。
- 每項實驗的目的、輸入、方法、輸出與成功條件。
- Benchmark dataset 與是否 source replay。
- 指標與前次 baseline delta。
- 成功成果、失敗案例、證據缺口。
- 結論、不能推導的事情與下一步。

## 5. Benchmark 最低規範

- 固定 dataset 後才比較前後演算法。
- 以 part number 分組，同型號歷史 revision 不得跨 split。
- Extraction、feature linkage、retrieval、recommendation 分開報告。
- 同時報告 accuracy 與 coverage/abstention，禁止只顯示已回答案例的 accuracy。
- 任何新自動驗證規則都要加入失敗案例測試。
- 必須保存 `benchmark_run_manifest.json`。資料集、案例庫、baseline 或 runner 的 SHA-256 不同時，不得只比較結果數字而忽略輸入版本差異。

## 6. 證據分級

- **RAW**：原始 STEP/DXF entity、handle、B-Rep topology。
- **PERSISTED**：當次執行時儲存的 transform、point set、candidate 與檢查。
- **RECOMPUTED**：後續使用固定腳本重算，必須標明時間與方法。
- **EXPLANATORY**：說明性重畫，不可用來證明正確性。

## 7. 實驗完成條件

- 腳本離開 IDE 仍可從 PowerShell 重跑。
- 輸入、參數、輸出和雜湊已保存。
- 當日 README 已填寫實際結果，不只留範本。
- Benchmark 可與前次 baseline 比較。
- 失敗也要保留，不得只挑成功圖片。
