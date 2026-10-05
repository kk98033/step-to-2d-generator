# 公差推薦 Benchmark

本 benchmark 用來分開回答四個問題：DXF 公差是否能正確重讀、2D 幾何是否能定位到 STEP 已驗證特徵、相似案例能否在沒有資料洩漏的前提下被找到，以及最終推薦是否重現歷史核定公差。

## 快速執行

在專案根目錄使用包含 `pythonocc-core` 的 Python：

```powershell
.\pyoccenv\python.exe tools\run_tolerance_benchmark.py --mode all
```

每次公差管線驗收還必須執行真實模型階段；預設模型為 `1AL0W5000H-R03`：

```powershell
.\pyoccenv\python.exe tools\run_real_model_acceptance.py
```

此階段實際輸出 DXF/PDF/PNG、重跑歷史 DXF 到 B-Rep 拓撲驗證、執行公差推薦，並逐筆確認推薦 `case_id` 在案例庫中存在且 `source_metadata.dxf_path` 指向可讀取的原始 DXF。報告輸出於 `auto_2d_drawing/output/acceptance/1AL0W5000H-R03/acceptance_report.json`。版面超出圖框會回報 `PASSED_WITH_WARNINGS`，不能只用「檔案存在」當作繪圖品質通過。

輸出：

- Dataset：`auto_2d_drawing/tolerance/benchmark_data/silver_verified_cases.json`
- Report：`auto_2d_drawing/output/benchmarks/tolerance_benchmark_latest.json`

只重建資料集：

```powershell
.\pyoccenv\python.exe tools\run_tolerance_benchmark.py --mode build
```

只執行既有資料集：

```powershell
.\pyoccenv\python.exe tools\run_tolerance_benchmark.py --mode run
```

來源 DXF 暫時不可用時，可以跳過 extraction 與 2D linkage 重播：

```powershell
.\pyoccenv\python.exe tools\run_tolerance_benchmark.py --mode run --skip-source-replay
```

CI 可加上 `--fail-on-gate`。只要發生料號群組洩漏、來源 entity／公差重播低於 99%，或非 abstain 推薦的過鬆率高於 1%，程式便以 exit code 2 結束。推薦準確率要等 GOLD set 凍結後才適合作為 release gate。

## 資料等級

| 等級 | 定義 | 可否用於正式準確率 |
| --- | --- | --- |
| GOLD | 工程規則或工程簽核的凍結答案 | 可以 |
| SILVER | 唯一 STEP/DXF 配對、原生 DIMENSION、公差與 STEP 幾何唯一匹配 | 可做回歸與代理指標，不可取代 GOLD |
| SYNTHETIC | 已知 3D 特徵產生的投影及標註 | 適合訓練定位器，不代表公司公差政策 |

目前自動建置只接受 `ENGINEER_VERIFIED` 與 `AUTO_VERIFIED`，不會把 `AUTO_EXTRACTED`、`AUTO_INFERRED_2D`、種子或規則案例加入答案集。

## 防止資料洩漏

檔名會先移除 revision，例如 `2FQ6V4030H-R00`、`-R01`、`-R02` 都歸入 `part:2FQ6V4030H`。Dataset 的 train/validation/test 以整個 group 切分；檢索與推薦評估則使用 `LEAVE_ONE_PART_GROUP_OUT`，每次查詢都移除同料號的全部版本和尺寸案例。

這項限制很重要。若只隨機切尺寸列，系統很容易找到同一張圖或另一個 revision，產生不真實的高分。

## 指標定義

### Extraction

- `entity_found_rate`：原 entity handle 是否仍能從來源 DXF 找到。
- `dimension_category_accuracy`：LINEAR、DIAMETER、RADIUS 等類別是否一致。
- `nominal_mae_mm`：重新解析尺寸值的平均絕對誤差。
- `tolerance_exact_accuracy`：模式、配合代號及上下偏差是否一致。

### Feature linkage

- `feature_type_accuracy`：純 2D 推論是否等於 STEP 驗證的特徵類型。
- `coverage`：可重新執行推論的來源比例。
- `auto_decision_rate`：2D 引擎願意自動下判斷的比例。
- `retrieval_eligible_rate`：2D 推論是否具有足夠證據直接進推薦庫。
- `confusion`：例如 `shaft_segment->UNRESOLVED`，用來決定下一批自動化工作的優先順序。

### Retrieval

- Recall@K：Top K 是否至少有一筆相同特徵、尺寸類別與公差設定的案例。
- Precision@K：Top K 中相容案例的比例。
- MRR：第一筆相容案例的平均倒數排名。
- nDCG：相容案例在排序前段的品質。
- `evaluable_coverage`：held-out 後仍有相容歷史答案的查詢比例。

### Recommendation

- `exact_tolerance_accuracy`：公差模式、配合代號及上下偏差完全一致。
- `upper/lower_deviation_mae_mm`：上下偏差誤差。
- `mean_interval_iou`：推薦區間與答案區間的交集比例。
- `unsafe_looser_rate`：推薦區間比答案更寬的比例。
- `over_tight_rate`：推薦區間比答案更窄的比例。
- `abstention_rate`：證據不足而落到保守 fallback 的比例。
- `selective_accuracy`：排除 abstain 後的準確率。
- Brier score / ECE：畫面信心度是否真的對應命中機率。

## 正確使用方式

1. 每次修改 parser、2D linkage、retriever 或 decision service 前先保存基準報告。
2. 修改後使用同一份 dataset 重跑，不可同時改答案。
3. 優先限制 `unsafe_looser_rate`，再提高 selective accuracy，最後才提高 coverage。
4. Silver 指標改善後，才以凍結 GOLD set 做發佈判定。
5. 正式模型的資料切分不得讓同料號或不同 revision 跨 split。

Benchmark report 會保留逐筆 `records`，包含使用的訓練案例數、相關案例數、Top-K 案例 ID、第一個正確排名與實際推薦結果，便於追查失敗原因。

## 全域 3D／2D 幾何搜尋結果（2026-10-05）

本次先將 A/R 版本系列收斂到最高版，再展開所有 STEP/XCAF 葉零件，對全部 DXF 獨立視圖執行描述子 Top-K 與 HLR/ICP 精配準。全域配對仍只是一個候選來源；尺寸必須另外通過完整五項 3D 定位證明才會成為 `AUTO_VERIFIED`。

| 指標 | 結果 |
| --- | ---: |
| 最新 STEP / DXF | 181 / 786 |
| 排除的舊版／重複 DXF | 958 |
| 唯一 3D 元件幾何 | 418 |
| DXF 獨立視圖 | 7,016 |
| STEP 投影視圖 | 2,478 |
| 全域精配準候選 | 1,656 |
| 通過配對層門檻 | 210 |
| 保留為不確定候選 | 1,446 |
| 最終 `AUTO_VERIFIED` 公差案例 | 20 |
| 其中由全域搜尋新增 | 1 |

Silver benchmark 目前共 20 筆：來源 entity 找回率、尺寸種類與公差精確重播均為 100%；retrieval evaluable coverage 為 55%，MRR 為 0.9091，Recall@1 與 Precision@1 均為 81.82%。加入嚴格公稱尺寸轉用門檻後，leave-one-part-group-out 推薦對 20/20 筆 abstain，代表目前跨零件泛化覆蓋率仍為 0%，但也避免把真實、不同尺寸的 CUSTOM_LIMITS 案例直接套用。這仍不是工程師簽核 GOLD accuracy，不能以 20 筆 Silver 樣本宣稱已達量產準確率。

## 料號配對階段結果（2026-10-03）

本次改善只調整歷史 STEP/DXF 的配對證據，不放寬幾何核實條件。除完整檔名一致外，系統現在也接受「檔名內嵌料號一致、版本代號一致、同料號版本下 STEP 與 DXF 各自唯一」的配對。跨版本、一對多與無法讀取的 STEP 仍不會升級為 `AUTO_VERIFIED`。

| 指標 | 改善前 | 改善後 | 解讀 |
| --- | ---: | ---: | --- |
| Silver / `AUTO_VERIFIED` 案例 | 14 | 26 | 增加 12 筆可稽核特徵公差案例 |
| 原始 DXF 公差重播正確率 | 100% | 100% | entity、尺寸種類、名目值與公差皆能重播 |
| 2D feature type accuracy | 7.14% | 3.85% | 正確筆數仍只有 1 筆，新增樣本多數無法單靠 2D 規則定位 |
| Retrieval evaluable coverage | 42.86% | 65.38% | 更多 held-out 查詢有其他料號的相關案例可供評估 |
| MRR | 0.6667 | 0.6392 | 樣本增加後排名品質略降，仍需改善特徵描述與相似度 |
| Recall@1 | 50.00% | 47.06% | 略降 |
| Recall@3 | 100.00% | 76.47% | 新增較難案例後下降 |
| Recall@5 | 100.00% | 94.12% | 多數相關案例仍可在前五名找到 |
| 精確公差推薦正確率 | 0% | 0% | 尚無足夠跨料號共識，系統全部 abstain |
| Abstention rate | 100% | 100% | 26/26 均回傳 `REVIEW_REQUIRED`，未冒充可靠推薦 |

這不是工程師簽核的 GOLD accuracy。Silver 標籤來自相同 CAD 證據管線，適合驗證重播、資料洩漏、配對與檢索退化，但不能證明公差工程決策已達到可上線準確率。正式 release gate 仍需要凍結的工程師簽核 GOLD dataset。

另以原本固定的 14 筆查詢做受控比較，只增加新案例作為候選時，retrieval evaluable coverage 由 42.86% 提升到 64.29%；MRR 由 0.6667 降到 0.6019，Recall@5 由 100% 降到 88.89%，精確公差推薦仍為 0%。這表示新增資料改善了「有案例可比」的覆蓋率，但相似度排序與跨料號公差共識尚未改善。

配對稽核檔為 `auto_2d_drawing/tolerance/data/historical_pair_manifest.json`。其中 `verified_pairs` 才能進入 3D 幾何核實；`candidates` 只供後續人工或 BOM/PLM 關聯，不會成為推薦證據。

## DXF 附著與 STEP 投影門檻結果（2026-10-03）

在料號配對與唯一數值候選之外，`AUTO_VERIFIED` 現在還必須同時通過：

1. DXF 尺寸具有高信心幾何附著，而且只能落在一個主要視圖。
2. 局部幾何符合特徵語意；例如外徑必須能找到跨視圖外輪廓平行線，孔徑則需要隱藏線或孔圓證據。
3. STEP front/top/right HLR 投影存在相同名目尺寸的圓、平行線間距或線段長度。
4. DXF 視圖群組與某個 STEP 投影的長寬比例及等向縮放一致性達門檻。

| 指標 | 僅料號＋數值 | V1：全圖投影門檻 | V2：局部特徵定位 | 解讀 |
| --- | ---: | ---: | ---: | --- |
| `AUTO_VERIFIED` 案例 | 26 | 5 | 4 | V2 不接受「模型其他位置剛好有相同尺寸」 |
| 唯一數值候選 | 29 | 36 | 36 | 候選數不變，改進發生在幾何證據判定 |
| 幾何證據不足 | 未檢查 | 31 | 31 | 不會進入 RAG；另有 5 個來源尺寸合併為 4 個案例 |
| 2D-only feature type accuracy | 3.85% | 20.00% | 0.00% | V2 留下的 4 筆皆為線性尺寸；現行純 2D 規則會誠實 abstain |
| DXF 公差重播正確率 | 100% | 100% | 100% | 原始 entity、名目值與公差仍可完整重播 |
| Retrieval evaluable coverage | 65.38% | 0% | 50% | 4 筆中只有 2 筆存在跨料號 relevant case，MRR 與 NDCG 為 1.0 |
| 精確公差推薦正確率 | 0% | 0% | 0% | 仍全部回傳 `REVIEW_REQUIRED`，不把小樣本猜測當答案 |
| Abstention rate | 100% | 100% | 100% | 沒有用低證據案例冒充可靠推薦 |

V1 的 20% 是 1/5 的 Silver feature-linkage 指標，不是工程師簽核的端到端準確率。V2 將其中 3 筆判定為尺寸附著位置與候選 3D node 不一致，保留 2 筆，並從其他同值候選中找到 2 筆局部位置一致案例。這證明舊門檻存在全圖同值碰撞，但仍不能據此宣稱 production precision；下一階段需要擴充可核實配對，並以工程師簽核 GOLD subset 計算 precision、recall 與 selective accuracy。

若完整資料庫已重建，只需要重新計算配對案例的拒絕原因，可執行：

```powershell
.\pyoccenv\python.exe tools\annotate_projection_rejections.py
```

此工具只重開 manifest 中的安全配對，將 `INSUFFICIENT_GEOMETRY_EVIDENCE`、四項 checks、候選 feature 與視圖配準結果補入案例 metadata，不會重新掃描全部 DXF。
