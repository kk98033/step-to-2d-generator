# 公差系統 Benchmark

## 目前是否已有 Benchmark？

有。本目錄保留一份凍結的 SILVER 資料集、案例庫快照、前次基準報告，並使用
`../run_benchmark.ps1` 將新結果寫入當日 `experiments/YYYY-MM-DD/benchmark/`。

## 資料組成

- `datasets/silver_verified_cases_snapshot.json`：20 筆 SILVER 案例。
- `snapshots/feature_case_base_snapshot.json`：執行 retrieval/recommendation 使用的案例庫快照。
- `baselines/baseline_2026-10-05.json`：2026-10-05 的已知基準。

SILVER 標籤來自嚴格 STEP/DXF 對應或已驗證案例，但不等於工程師簽核的 GOLD ground truth。

## 四個獨立問題

Benchmark 不把所有錯誤混成一個 accuracy，而是分為：

1. **Extraction**：能否從原始 DXF entity 重新讀到尺寸類型、nominal 與 tolerance。
2. **Feature linkage**：2D 推斷能否回到 STEP 驗證的 feature type。
3. **Retrieval**：排除同 part group 泄漏後，能否找到相容歷史案例。
4. **Recommendation**：最終公差是否等於 held-out expected tolerance，以及系統是否 abstain。

## 核心指標

- Extraction：`entity_found_rate`、`dimension_category_accuracy`、`nominal_mae_mm`、`tolerance_exact_accuracy`。
- Feature linkage：`coverage`、`feature_type_accuracy`、`auto_decision_rate`、`retrieval_eligible_rate`。
- Retrieval：`evaluable_coverage`、`MRR`、`NDCG`、`Recall@K`、`Precision@K`。
- Recommendation：`exact_tolerance_accuracy`、deviation MAE、interval IoU、`abstention_rate`、`selective_accuracy`。
- Safety：版本 group leakage、unsafe-looser rate、over-tight rate。

## 執行

```powershell
cd D:\School\力致\app\step-to-2d-generator\experimental_lab\tolerance_extraction_audit
.\run_benchmark.ps1 -Date 2026-10-07
```

如果外部 DXF 不可用，可只測 retrieval/recommendation：

```powershell
.\run_benchmark.ps1 -Date 2026-10-07 -SkipSourceReplay
```

輸出：

- `tolerance_benchmark.json`：完整逐筆報告。
- `benchmark_summary.json`：穩定機器可讀指標與 baseline delta。
- `benchmark_summary.md`：人類可讀比較表。

## 目前基準的正確解讀

2026-10-05 的 20 筆 SILVER 基準：

- DXF tolerance exact accuracy：100%。
- 2D feature linkage accuracy：5%。
- Feature linkage coverage：100%，但多數結果是 unresolved/review candidate。
- Retrieval MRR：0.909091，但只有 55% query 可評估。
- Recommendation exact accuracy：0%。
- Abstention rate：100%，20 筆都是 `REVIEW_REQUIRED`。

這代表「公差數值重讀」已有好結果，但「自動特徵定位和最終推薦」仍不可當作生產可用。

## 版本原則

1. Dataset 一旦用於比較就不覆寫，新資料用新檔名與 SHA-256。
2. 不允許同 part number 的不同 revision 分散到 train/test。
3. 每份報告必須記錄 dataset、case DB、source replay 與程式版本。
4. SILVER 不得對外宣稱生產 accuracy；需建立獨立工程師簽核 GOLD set。
5. 開發時不可查看 test expected tolerance 後再調整規則，否則會造成 benchmark overfitting。
