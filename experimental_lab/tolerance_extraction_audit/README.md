# 公差自動提取與 2D/3D 特徵對應實驗室

> Git 正式基準：Tolerance Extractor V1（2026-10-11）。版本範圍、正式入口與驗證數據請先閱讀 [V1_RELEASE.md](V1_RELEASE.md)。

## 2D 繪圖實驗唯一目前基準

繪圖實驗 01–11 已整理到 [`DRAWING_CURRENT_FINAL/`](DRAWING_CURRENT_FINAL/)；後續未知模型
自動化、視角／剖面選擇、尺寸語意與排版實驗，都以該目錄的 manifest、固定入口與 regression
為主，不再從單一編號實驗複製出另一套引擎。這套繪圖主線仍是實驗版，尚未部署到正式網頁。

## 正式案例資料庫全量重建與部署

### 重建資料來源與目錄角色

正式案例資料庫重建時，**不是以 `experiments/` 內的模型或產物作為全量輸入資料源**。
重建腳本會讀取 [`CURRENT_FINAL/database_full_extract_config.json`](CURRENT_FINAL/database_full_extract_config.json)
所指定的外部公司資料路徑：

- `source_root`：原始產品資料夾，提供 STEP／STP、DWG 與相關來源檔。
- `dxf_root`：DWG 轉換後的 DXF 快取，提供原生 `DIMENSION` 與公差證據。
- `families`：本次允許掃描與重建的產品家族清單。

各目錄的用途如下：

- `CURRENT_FINAL/`：目前核准用於重建的演算法入口與設定，不是模型資料庫。
- `experiments/YYYY-MM-DD/`：每次執行產生的報告、staging、checkpoint、圖片與稽核證據，不應反過來當成下一次全量重建的原始模型來源。
- `benchmarks/`：固定測試集與回歸基準，只用於驗證演算法是否退步。
- `auto_2d_drawing/tolerance/data/feature_case_base.json`：驗證與部署成功後，正式 Web 後端載入的案例資料庫。

因此，新增或更新公司模型時，應先放入設定檔指向的受控來源目錄，必要時更新
`source_root`、`dxf_root` 或 `families`，再執行重建。不得只把模型複製到
`experiments/` 後就視為已納入正式資料庫。

每次 `CURRENT_FINAL` 公差／特徵提取器完成一版後，使用根目錄的
`rebuild_production_case_base.ps1` 重新掃描設定檔中的所有工程圖、執行 3D 特徵連結，
再轉換成正式網頁直接使用的 `ToleranceCase[]` 格式。

首次或規則變更後，先執行不寫入正式資料庫的完整驗證：

```powershell
cd D:\School\力致\app\step-to-2d-generator\experimental_lab\tolerance_extraction_audit
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\rebuild_production_case_base.ps1 `
  -DryRun `
  -Workers 4 `
  -ModelTimeoutSeconds 900 `
  -StageTimeoutSeconds 43200
```

確認 `deployment_summary.json` 為 `DRY_RUN_VALIDATED` 後，正式部署：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\rebuild_production_case_base.ps1 `
  -Workers 4 `
  -ModelTimeoutSeconds 900 `
  -StageTimeoutSeconds 43200
```

執行規則與安全界線：

1. 每張 DXF 與每個 STEP／DXF 特徵驗證工作都有獨立的
   `ModelTimeoutSeconds`；超時工作會被終止並寫入 checkpoint 與 LOG。
2. 全域配對／manifest 等非單模型階段受 `StageTimeoutSeconds` 保護。
3. 預設只要任一圖面或配對工作失敗／超時就不部署；只有人工確認後明確傳入
   `-AllowPartial` 才允許部分結果繼續。
4. staging JSON 會先以正式 FastAPI 使用的 `FeatureCaseBase` loader 重讀驗證，
   驗證成功才可能切換正式檔案。
5. 部署前會將舊的 `feature_case_base.json`、rebuild report 和 pair manifest
   複製到 `auto_2d_drawing/tolerance/data/backups/<timestamp>/`，不刪除舊版。
6. 原本的 `ENGINEER_VERIFIED`／其他非自動案例會被保留；舊的自動提取案例由新批次取代。
7. `VERIFIED` 轉為 `AUTO_VERIFIED`；`CANDIDATE_ONLY` 轉為
   `REVIEW_CANDIDATE`，只作低信賴搜尋證據並在網頁要求工程師確認；
   `UNRESOLVED` 仍保存為 `AUTO_EXTRACTED`，但不能成為自動決策證據。
8. 正式後端在啟動時將案例庫載入記憶體，因此成功部署後必須重啟後端服務。

每次執行會建立：

```text
experiments/<date>/production_rebuild_<timestamp>/
├─ pipeline.log                 # 即時階段、模型進度、耗時、失敗與超時
├─ deployment_summary.json     # 最終提取／驗證／候選／未解析／備份統計
├─ 01_extraction/              # 全 DXF 原生公差結果
├─ 02_feature_link/            # STEP 投影與特徵連結、checkpoint
└─ 03_staging/                 # 部署前的正式 schema 案例庫
```

新版案例 schema 會保留 `feature_type`、`canonical_feature_id`、
`matched_nominal_field`、複合特徵、環形特徵、旋轉包絡與跨零件特徵。
正式案例檢索已支援這些名稱與新的直徑／長度 nominal 欄位；候選證據會在網頁標示
「低信賴候選・需確認」。

## 目前可合併最終版（唯一入口）

實驗中只有 [`CURRENT_FINAL/`](CURRENT_FINAL/) 代表「目前準備日後合併到 main 的版本」。其他 `experiments/YYYY-MM-DD/` 都是歷史證據，不可直接當成最新演算法。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\CURRENT_FINAL\run.ps1
```

維護規則：

1. 使用者在實驗過程明確說「合併」時，才把已驗證規則納入 `CURRENT_FINAL`。
2. 每次合併必須同時執行針對性測試及既有 benchmark，記錄 before/after。
3. 新規則不得降低既有案例結果；沒有工程師簽署的 GOLD label 時，只能回報 SILVER/針對性指標。
4. `AUTO_VERIFIED` 必須同時通過公差解析、2D 附著、投影定位、名目尺寸及 3D topology/feature gate。
5. `dimension_engine.py` 與舊 `extractors/` 保持零改動；最終版仍先留在實驗區。

### 風扇完整組件中央剖面規則（2026-10-08 納入）

當 3D 幾何呈現近似等徑的兩個徑向跨度、明顯較薄的軸向跨度，而且中央剖面候選比一般正投影有明顯更好的圖面符合度時：

- 使用完整 STEP assembly，不只投影 Blade leaf component，因此軸心、輪轂與葉片都會保留。
- 動態由 `Bnd_Box` 判斷中心與軸向，不寫死座標。
- 同時測試一般 HLR、正負半空間 Boolean cut、兩個徑向中央剖面 `BRepAlgoAPI_Section`。
- 依 contour、extent、scale gate 自動選擇；目前 `1AL0W5000H-R03` 是中央剖面勝出。
- 剖面對準只代表 drawing-view evidence 通過，不能取代名目尺寸與 B-Rep feature 驗證。

### 風扇輪轂徑向結構的角度尺寸語意（2026-10-13 註記）

風扇圖面中靠近中心軸、呈一條一條放射狀的肋、槽或邊界，其角度標註代表的是**圖面所指定兩條實際徑向基準之間的夾角**，不是用葉片數推導的平均節距角。

- 禁止以 `360° / 葉片數` 直接產生、恢復或驗證這類角度數值。
- 必須先找出角度尺寸的兩條延伸線／引線所指向的徑向邊，再以動態求得的輪轂中心建立兩條射線並量測夾角。
- 同一個零件可同時存在多組不相等的徑向間隔角；葉片數只能作為重複圖樣的輔助證據，不能取代尺寸附著關係。
- 此語意記為 `hub_radial_feature.included_angle`；只有圖面明確標註等分節距時，才可使用 `repeated_radial_pattern.pitch_angle`。
- 它也不同於葉片輪廓的彎曲角、攻角、扭轉角，以及 `R2.15` 之類的圓角／曲率半徑。
- 若無法確認兩條被標註的徑向邊，結果只能保留為 `REVIEW_CANDIDATE`／`UNRESOLVED`，不可退回 `360° / N` 猜測。

本次證據：一般投影 contour `0.5842`、半空間切半 `0.6263`、中央剖面 `0.7938`；中央剖面通過自動視圖門檻。

> 狀態：實驗用，不屬於 main 產品管線。  
> Git：整個 `experimental_lab/` 已被專案 `.gitignore` 排除，不會被上傳。  
> 安全原則：不改動 `dimension_engine.py`、舊版 `extractors/` 或正式公差資料庫。

這個目錄的目的不是產生一張「看起來很像」的對照圖，而是把公差自動提取的每一個主張分成可稽核證據：

1. 這個數值真的來自原始 DXF 嗎？
2. 尺寸端點真的連到某個 2D 實體嗎？
3. 這個 2D 實體真的屬於某個投影視圖嗎？
4. 投影視圖真的能與 STEP 投影對位嗎？
5. 對位後真的能回溯到 B-Rep face/edge 和 FeatureGraph 特徵嗎？
6. 只要上述任一步缺乏證據，系統是否會拒絕把它放入 RAG？

## 目錄結構

```text
tolerance_extraction_audit/
├─ README.md                         # 方法、限制與總入口
├─ STANDARD_OPERATING_PROCEDURE.md   # 所有實驗共用的 SOP
├─ run.ps1                          # 依日期執行投影與證據實驗
├─ run_benchmark.ps1                # 依日期執行 benchmark
├─ common/                          # 可重複使用，不屬於任一天
│  ├─ config/                      # 本機設定與範例
│  ├─ scripts/                     # 投影、證據、benchmark 標準腳本
│  ├─ templates/                   # 每日實驗記錄範本
│  └─ requirements-experiment.txt
├─ benchmarks/
│  ├─ README.md                     # benchmark 定義、指標與限制
│  ├─ datasets/                     # 凍結的 SILVER/GOLD 資料集
│  ├─ snapshots/                    # 執行用案例庫快照
│  └─ baselines/                    # 已命名、不覆寫的比較基準
└─ experiments/
   └─ YYYY-MM-DD/                     # 一天一個實驗單位
      ├─ README.md                     # 當日做了什麼、結果、結論
      ├─ resolved_config.json          # 當日實際使用的設定
      ├─ run_summary.json              # 執行時間與狀態
      ├─ results/                      # 模型輸出與證據圖
      └─ benchmark/                    # 當日 benchmark 完整報告
```

`experiments/YYYY-MM-DD/` 是不可跨日混用的執行紀錄。同一天重跑會更新當日的 `latest`；跨日執行必須建立新日期目錄。

## 我們的核心想法

公差提取其實包含兩個不同問題，必須分開測量：

- **數值提取**：從 DXF `DIMENSION` 實體取得 nominal value、upper/lower deviation、dimension category 和 entity handle。
- **語意定位**：證明這組尺寸/公差是在描述哪一個 3D face、edge 或 FeatureGraph 特徵。

目前數值提取通常比較可靠，最大瓶頸是語意定位。所以不應因為「有讀到 ±0.04」就宣稱「已知道它屬於 shaft segment」。

實驗採取以下設計：

- 用硬證據逐層縮小候選，不用 LLM/VLM 直接猜最終尺寸屬性。
- VLM 可用於視圖類型、圖紙區域、OCR 異常或候選重排，但不能單獨解除驗證閘門。
- 每筆 AUTO_VERIFIED 必須能回溯到 STEP、DXF、dimension handle、geometry handle、transform 和 B-Rep topology ID。
- 沒有共同附著實體、對位品質不足或候選並列時，結果必須保留為 unresolved/review candidate。
- 原始檔與輸出都用 SHA-256 記錄，確保之後看到的還是同一份資料。

## 完整公差提取流程

### 0. 型號、版本與檔案配對

1. 從檔名拆出 part number 與 revision，例如 `1AL0W5000H-R03`。
2. 只使用相同型號的最新版本，避免 R00/R01/R03 同時進入推薦庫。
3. 先將同名 STEP 與 DXF 視為最強配對，組立圖再另外建立 component candidate set。
4. 小零件只有 2D 圖、不在 3D assembly 出現時，應分類為 `2D_ONLY_COMPONENT`，不強迫做錯誤 3D 映射。

### 1. STEP/B-Rep 讀取

1. OpenCASCADE 讀取 STEP assembly 與 component topology。
2. 由 `Bnd_Box` 取得動態邊界與中心，不寫死座標。
3. 列舉 face/edge，辨識 cylinder、plane、cone、torus 等幾何。
4. 產生 FeatureGraph 候選：`shaft_segment`、`hole`、`locating_shoulder`、`retaining_ring_groove`、`pilot_chamfer` 等。
5. 保留 feature ID 與 B-Rep face/edge ID 的對應。

### 2. 3D 標準視圖投影

1. 對 front/back/top/bottom/left/right 進行 HLR（Hidden Line Removal）。
2. 分開 visible edge 與 hidden edge。
3. 將每條 2D 投影線保留 `projected segment → original B-Rep edge/face` 身分。
4. 建立圓、同軸線、平行輪廓、孔與階差的 projection signature。

關鍵是第 3 點。如果只保存最後的 PNG/DXF，後面雖然看得到線，卻無法穩定回溯到 B-Rep topology。

#### 投影與標註必須分開

正式管線的順序是：

```text
STEP
  → ViewProjector / OpenCASCADE HLR
  → view_data.visible + view_data.hidden       # 純投影
  → DxfDrawer 寫入 VISIBLE/HIDDEN layer   # 純投影 DXF 實體
  → DimensionEngine / LayoutEngine            # 後續才加入尺寸、文字與圖框
```

因此，`acceptance_*.png` 是完成排版後的工程圖，不能當作純 HLR 投影截圖。只從完成的 DXF
過濾 `VISIBLE/HIDDEN` 也不夠嚴謹，因為風扇的人工 bbox 外框曾被寫入 `VISIBLE` 圖層。

本實驗改為直接從 STEP 重新執行：

1. `STEPControl_Reader` 讀取原始 STEP。
2. `view_projector_snapshot.py` 建立 front/top/right `HLRAlgo_Projector`。
3. `HLRBRep_Algo.Update()` 與 `Hide()` 計算可見/隱藏邊。
4. `HLRBRep_HLRToShape` 取出 `VCompound/HCompound/OutLineVCompound/OutLineHCompound`。
5. 直接將 `view_data.visible` 與 `view_data.hidden` 輸出獨立 DXF/PNG。
6. 不實例化 `DimensionEngine`、`LayoutEngine` 或圖框繪製程式。

因此輸出只有：

- `VISIBLE`：HLR 可見輪廓。
- `HIDDEN`：HLR 隱藏線。

不會產生：

- `DIM`：尺寸線、延伸線與尺寸文字。
- `TEXT`：標題、說明與其他文字。
- `CENTER`：後加的中心線/中心記號。
- `BORDER`：圖框與欄位。
- 人工 bbox 外框、視圖名稱或特徵疊圖。

投影後會同時儲存純投影 DXF、PNG 與 `projection_report.json`，記錄 STEP SHA-256、HLR 方法、
visible/hidden edge 數量和 bbox，並明確記錄 `annotation_pipeline_executed: false`。

### 3. DXF 公差數值提取

1. 優先讀取 DXF 原生 `DIMENSION` entity，不先用 OCR。
2. 擷取 nominal value、dimension type、DIMSTYLE、upper/lower deviation、prefix/suffix、layer 與 handle。
3. 記錄 `defpoint/defpoint2/defpoint3/defpoint4/text_mid`。
4. 合併轉檔產生的重複尺寸 entity，但保留 duplicate handles 以供稽核。
5. 只要 nominal 或 tolerance 解析異常，就不允許進入推薦庫。

### 4. DXF 尺寸端點與 2D 實體關聯

1. 若 DXF 保留 native associative dependency，直接使用它。
2. 否則計算每個 definition point 到 line/arc/circle/polyline 的距離。
3. 區分 `EXACT_ATTACHMENT` 與 `NEARBY_GEOMETRY`。
4. 檢查兩個端點是否命中同一 handle 或同一 geometry group。
5. 將分割成多個 ARC 的同一個圓合併為 circular profile，避免把轉檔結果誤判為多個特徵。

成功範例 `7ADD`：兩個定義點都落在 circle `7106`，直徑 35 mm。  
失敗範例 `7AB1`：兩個端點命中不同線段，沒有共同 handle/profile，所以不能直接宣稱特徵類型。

### 5. DXF 工程圖視圖分群

1. 排除 title block、note、text、dimension 等註解圖層。
2. 對剩餘幾何依空間連通性與 bbox 分群。
3. 識別主視圖、側視圖、局部放大圖與剖視圖。
4. 每個尺寸只與所屬視圖的投影候選比較，不與整張圖面所有線段暴力混在一起。

### 6. STEP 投影與 DXF 視圖對位

對每個 STEP 標準視圖和 DXF view cluster 測試：

1. 平移、縮放、旋轉。
2. 必要時測試鏡射，因為工程圖視圖方向可能不同。
3. 粗對位後再用密度平衡的 mutual ICP 細對位。
4. 同時測量 normalized Chamfer distance、inlier ratio、source/target coverage、ranked Hausdorff 與第一/第二名 margin。
5. 分數不能只看單一指標；例如 contour score 不足時，即使總分看起來高也不應自動通過。

### 7. 2D 尺寸端點回映射到 3D topology

1. 使用最終 similarity transform 將 DXF 座標轉回 STEP projection space。
2. 尋找最接近的 projected edge/face，但不只看最短距離。
3. 加入尺寸類型限制：直徑優先 circular/cylindrical profile，線性尺寸優先兩個可解釋的平行或軸向邊界。
4. 加入 nominal consistency：投影距離或圓徑必須與尺寸值在動態容差內。
5. 透過 `projected edge → B-Rep edge/face → FeatureGraph node` 得到最終 feature type。

### 8. 特徵語意判定

最終類別不應是一個沒有來源的分數，而應組合：

- dimension category 先驗；
- 2D attachment signature；
- STEP projection signature；
- nominal dimension agreement；
- B-Rep surface type 與 topology neighborhood；
- FeatureGraph 中的相鄰關係與功能角色。

當 `shaft_segment / hole / groove / shoulder / chamfer` 都得到相同低分時，正確行為是 `UNRESOLVED`，不是隨機選一個。

### 9. 驗證閘門

建議只在下列條件全部成立時設為 `AUTO_VERIFIED`：

- 原生尺寸數值和公差可解析。
- 尺寸端點附著關係可靠。
- 局部特徵語意和 nominal 值一致。
- STEP 投影有對應 signature。
- DXF view 與 STEP view 通過對位品質門檻。
- 尺寸所在局部位置能對到投影特徵。
- 端點能回溯到 B-Rep topology 與 FeatureGraph node。

其他結果只能是 `AUTO_EXTRACTED`、`REVIEW_CANDIDATE` 或 `UNRESOLVED`，不應進入自動推薦案例集。

### 10. 推薦使用方式

RAG 查找時不只用數值接近，而是用：

- 特徵類型與功能角色；
- nominal 尺寸區間；
- topology neighborhood；
- 零件/產品家族；
- 材料、製程、配合關係（有資料時）；
- 工程師個人偏好（只能做重排，不能將未驗證案例變成真實案例）。

推薦結果必須顯示參考圖面、entity handle、特徵 ID、相似理由、差異和證據狀態。

## 如何執行

### 先決條件

- Windows PowerShell。
- 專案根目錄已有 `pyoccenv/python.exe`。
- `config/local.json` 的 STEP 與 DXF 路徑存在。
- 該 Python 環境能匯入 `OCC`、`ezdxf`、`PIL`、`numpy`、`vtk`。

### 建議執行方式

在 PowerShell 中：

```powershell
cd D:\School\力致\app\step-to-2d-generator\experimental_lab\tolerance_extraction_audit
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

指定日期並一起執行 benchmark：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Date 2026-10-07 -WithBenchmark
```

使用其他設定檔：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Config .\common\config\another-model.json -Date 2026-10-08
```

也可直接執行 Python：

```powershell
..\..\pyoccenv\python.exe .\common\scripts\extract_projection_only.py --config .\common\config\local.json
..\..\pyoccenv\python.exe .\common\scripts\generate_raw_tolerance_evidence.py --config .\common\config\local.json
```

### 更換測試模型

1. 複製 `common/config/local.example.json`。
2. 修改 `model`、`step_path`、`dxf_path`、成功/失敗 case ID。
3. 確認 `benchmarks/snapshots/feature_case_base_snapshot.json` 含有設定檔中的兩筆 case ID。
4. 執行 `run.ps1 -Config ...`。

## 輸出說明

`experiments/<date>/results/<model>/latest/` 會產生：

| 檔案 | 證明內容 | 是否可當稽核證據 |
|---|---|---|
| `01_actual_step_3d_render.png` | STEP 經 OpenCASCADE triangulation 後的真實網格 | 可，但是重新渲染 |
| `01_actual_model.stl` | 本次 STEP 導出的網格 | 可 |
| `02_projection_only_views.png` | 只含 VISIBLE/HIDDEN 的 front/top/right 投影聯絡表 | 可，聯絡表僅縮放/排版 |
| `02_projection_only_evidence.png` | 稽核包內的純投影聯絡表 | 可，來源為下方純投影 DXF/PNG |
| `projection_only/*_projection_only.dxf` | 直接由 STEP HLR visible/hidden edges 寫出的向量投影 | 可 |
| `projection_only/projection_report.json` | STEP 雜湊、HLR 方法、各視圖 visible/hidden 邊數量 | 可 |
| `03_actual_dxf_entities.png` | 依原始 DXF entity 座標繪製的端點附著 | 可，圖中保留 handle |
| `04_persisted_registration_json.png` | 案例庫已保存的最終對位數值 | 可 |
| `05_actual_success_*` | 成功案例的附著、topology 和 gate | 可 |
| `06_actual_failure_*` | 失敗案例原始候選 + 本次重算關聯 | 可，重算項有明確標示 |
| `07_evidence_gap.*` | 已保存/未保存證據矩陣 | 可 |
| `manifest.json` | 來源路徑、SHA-256、輸出雜湊與重算說明 | 可 |

## Benchmark 已納入實驗區

Benchmark 現在和每日實驗使用同一個日期目錄。固定資料集、案例庫快照和基準值放在 `benchmarks/`；每次執行結果放在 `experiments/<date>/benchmark/`，因此可以重跑，也不會把不同日期的結果混在一起。

每次 benchmark 還會產生 `benchmark_run_manifest.json`，記錄資料集、案例庫、baseline、runner 的 SHA-256，以及 Git commit、工作區狀態、是否執行 source replay。只看報表而沒有這份 manifest，不能視為完整的可重現實驗。

2026-10-07 對 20 筆 SILVER 樣本的重跑結果如下：

| 階段 | 指標 | 結果 |
|---|---|---:|
| DXF 數值提取 | 公差完全一致率 | 100% |
| 2D 特徵連結 | 特徵類型正確率 | 5% |
| 2D 特徵連結 | 自動決策率 | 5% |
| 案例檢索 | 可評估覆蓋率 | 55% |
| 案例檢索 | Recall@1 | 81.82% |
| 公差推薦 | 非 abstain 覆蓋率 | 0% |
| 公差推薦 | 完全一致率 | 0% |

這表示「讀到公差數值」已能通過目前的 SILVER 回歸集，但「判斷公差屬於哪個工程特徵」仍是主要瓶頸；推薦層因證據不足而 20 筆全部回傳 `REVIEW_REQUIRED`。SILVER 是代理真值，不能用這 20 筆宣稱正式生產準確率，詳細邊界見 `benchmarks/README.md`。

## 目前已知瓶頸

1. **當次 ICP 點雲沒有保存**：只能看到最後 transform，無法忠實重播每輪對位。
2. **HLR 線段到 B-Rep topology 的身分沒有完整持久化**：會使後處理只能依距離猜 topology。
3. **DXF 轉檔後幾何被分割**：同一個圓可能變成多個 ARC，同一條邊可能被分成多段。
4. **視圖對位可能有對稱解**：風扇、圓盤等高對稱模型可能產生多個近似同分 transform。
5. **組立 STEP 含多零件**：一張局部零件圖未必對應整個 assembly 外觀，必須先篩 component。
6. **2D-only 小零件**：螺絲、標籤、墊片等可能只有 2D 圖，需要單獨類別與不同驗證策略。
7. **尺寸端點本身可能不直接落在實體上**：延伸線、剖視線或補助線會使單純 nearest geometry 失敗。

## 下一階段實驗建議

1. 在 HLR 輸出時直接存 `projected_segment_id → BRep edge/face ID`。
2. 對每次 registration 儲存輸入點雲、取樣 seed、候選排名、每輪 transform 與最終 overlay。
3. 對 assembly 先進行 component-level projection retrieval，再對入選 component 做精對位。
4. 建立按 dimension category 分層的 benchmark，分開測量 value extraction、attachment、view match、topology match 和 feature classification。
5. 使用 precision/coverage 曲線選擇 AUTO_VERIFIED 閘檻，不只看整體 accuracy。
6. 所有會進入 RAG 的案例都必須通過 evidence completeness 檢查。

## 重要界線

- `previous_runs/` 中的圖是歷史實驗記錄，不代表當次真實執行中間畫面。
- 本實驗的 `latest/` 是「原始證據的可視化」，仍不等於完整重跑全資料庫抽取。
- 目前能證明 `7ADD` 是成功案例；`7AB1` 沒有足夠證據定位特徵，必須保留為 unresolved。
- 任何沒有寫進 manifest 的圖或分數，都不得當作系統正確性證明。
