# 智慧工程圖繪製規則規格

> 狀態：V1（`feature/drawing-rule-foundation`）  
> 適用範圍：新版智慧標註與未來的歷史 2D 圖面規則學習  
> 不適用範圍：舊版 `dimension_engine.py` 與舊有 `extractors/`

## 1. 目的

本文件定義「3D 特徵如何轉換成可繪製的 2D 標註規則」。它是智慧繪圖規則的權威契約，用來確保：

- 網頁特徵圖層、智慧標註、公差推薦與歷史圖面學習使用同一組 3D 特徵 ID。
- 投影、規則選擇、版面配置與實際 DXF 繪製各自只有一個明確責任。
- 從舊工程圖學到的是可套用到新模型的繪圖意圖，而不是舊圖上的絕對座標。
- 新零件、新視角與鏡射情況不需要寫死模型中心或特定零件座標。

## 2. 不變的出圖方式

實際產生工程圖的下游管線維持不變：

```text
STEP / TopoDS Shape
  -> ViewProjector（OpenCASCADE HLR）
  -> SmartRuleExtractor（候選繪圖規則）
  -> 模板／歷史案例／工程師選擇（啟用與樣式）
  -> SmartDimensionEngine
  -> DimensionTask
  -> DrawingLayout / LayoutEngine
  -> DxfDrawer
  -> DXF / PDF / PNG / SVG
```

新版只改善出圖前的「零件分類、特徵語意、各視圖投影錨點與規則設定」。`LayoutEngine` 與 `DxfDrawer` 仍負責避讓、排版及實際繪製，不應在其中加入零件辨識邏輯。

## 3. 模組責任邊界

| 階段 | 唯一責任 | 不應處理 |
| --- | --- | --- |
| `extract_canonical_features` | 建立穩定、唯一的 3D 特徵紀錄 | 2D 標註位置與排版 |
| `ViewProjector` | 產生各方向的 HLR 幾何 | 決定哪些尺寸值得標註 |
| `drawing_projection` | 使用與 HLR 完全一致的座標基底投影特徵 | 猜測公差或排版位置 |
| `SmartRuleExtractor` | 將特徵轉成候選規則及每視圖錨點 | 直接畫 DXF entity |
| 模板／歷史案例層 | 決定啟用、視圖、樣式、優先順序及公差 | 改寫幾何事實 |
| `SmartDimensionEngine` | 將設定後規則轉為 `DimensionTask` | 重新辨識零件種類 |
| `LayoutEngine` | 標註避讓及最終位置 | 改變名目尺寸或特徵 ID |
| `DxfDrawer` | 依任務繪製圖元 | 推論特徵與公差 |

## 4. 核心不變條件

1. 所有新版功能必須經由 `extract_canonical_features()` 取得特徵，不得另建第二套特徵 ID。
2. `canonical_feature_id` 與 `feature_id` 必須指向主網頁可辨識的同一筆特徵。
3. 禁止寫死世界座標、模型中心或特定料號位置；3D 範圍必須來自 OpenCASCADE 幾何與 `Bnd_Box`。
4. 每個視圖必須有獨立的 `view_geometry`；不可把 front 的座標直接拿去 top、right、left 或 back 使用。
5. `view_geometry[view]` 是該視圖的權威幾何。`geometry_payload` 只保留為舊呼叫端的相容 fallback。
6. 歷史案例只能保存正規化位置、相對側向、視圖及語意，不得把原 DXF 的紙面絕對座標當成新模型規則。
7. 沒有可信名目值或幾何錨點時，規則必須停用或標為候選，不得製造假尺寸。
8. 禁止修改舊版 `auto_2d_drawing/dimension_engine.py` 與舊有 `extractors/`；新版能力放在 `smart_extractors/` 或獨立模組。

## 5. 標準零件語意分類

目前標準類型如下：

| 類型 | 說明 |
| --- | --- |
| `SHAFT` | 軸、階梯軸及軸向回轉零件 |
| `FAN_IMPELLER` | 扇葉、葉輪；舊別名 `FAN`、`BLADE`、`IMPELLER` 均正規化到此類 |
| `FAN_HOUSING` | 風扇外框、外殼、導流框 |
| `STAMPED_FAN_BASE` | 沖壓底座、盤件及相關薄板件 |
| `MOTOR_RING` | 環、套筒、襯套類回轉零件 |
| `GENERAL_MECHANICAL` | 尚未落入上述類別的一般機械零件 |

檔名或 `part_hint` 只能作為輔助訊號；最後仍需以實際幾何條件驗證，不能把特定料號寫成規則。

## 6. 視圖座標契約

投影規則必須與 `ViewProjector` 建立 `gp_Ax2` 的方式一致。對每個視圖：

```text
n  = normalize(VIEW_CONFIG[view].direction)
ex = normalize(VIEW_CONFIG[view].up)       # OpenCASCADE projector local X
ey = normalize(cross(n, ex))               # projector local Y
u  = dot(P, ex)
v  = dot(P, ey)
```

不得改用一般相機常見的 right/up 算法，否則可能造成旋轉或左右鏡射。特徵包絡框必須投影動態 3D box 的八個角點，再取 2D 最小／最大範圍。

## 7. 候選規則資料契約

每筆候選規則至少應具有以下欄位：

```json
{
  "rule_id": "shaft_overall_length",
  "canonical_feature_id": "shaft_overall",
  "feature_id": "shaft_overall",
  "part_type": "SHAFT",
  "category": "overall",
  "dim_type": "LINEAR",
  "nominal_value": 22.1,
  "canonical_nominal_field": "length",
  "target_views": ["front", "top"],
  "views": ["front"],
  "side": "BOTTOM",
  "rank": 2,
  "baseline": "NONE",
  "default_prefix": "",
  "default_tolerance": "",
  "enabled": true,
  "view_geometry": {
    "front": {
      "start_proj": [-11.05, 0.0],
      "end_proj": [11.05, 0.0],
      "axis": "x",
      "projected_feature_bbox": [-11.05, -1.5, 11.05, 1.5]
    }
  }
}
```

欄位語意：

- `target_views`：幾何上可承載此規則的視圖。
- `views`：目前設定實際要畫的視圖，必須是 `target_views` 的子集。
- `canonical_nominal_field`：名目值來自特徵的哪一個語意欄位，例如 `length`、`diameter` 或 `radius`。
- `rank`：同側尺寸的優先／層級資訊；不是可信度分數。
- `enabled`：是否進入出圖流程；候選存在不代表必須繪製。
- `default_tolerance`：只有已知規則或案例支持時才能填入；未知時保留空值。

## 8. 每視圖幾何 `view_geometry`

`view_geometry` 以視圖名稱為 key。每個 payload 依尺寸型態使用下列欄位：

| 欄位 | 用途 |
| --- | --- |
| `projected_feature_bbox` | 該特徵在目前視圖的 2D footprint |
| `center` | 投影後中心；圓、孔群與 leader 常用 |
| `start_proj`, `end_proj` | 線性、直徑與中心線的實際幾何錨點 |
| `axis` | `x` 或 `y`，表示量測方向 |
| `diameter`, `radius` | 圓柱、孔、環形特徵的名目幾何 |
| `angle` | 角度或 leader 顯示方向 |

所有座標均為模型視圖座標，尚未加上紙張 placement、scale 或 layout offset。這些轉換只能由後續排版與繪圖層處理。

## 9. 尺寸型態與錨點規則

| `dim_type` | 必要幾何 | 用途 |
| --- | --- | --- |
| `LINEAR` | `start_proj`, `end_proj`, `axis` | 長、寬、厚、階差及兩特徵間距 |
| `DIAMETER` | `start_proj`, `end_proj`, `center`, `diameter` | 軸徑、孔徑、環的內外徑 |
| `RADIAL` | `center`, `radius`，以及曲線目標 | 圓角與圓弧半徑 |
| `ANGLE` | 兩方向或 `p1`、`center`、`p2` | 斜面、葉片與夾角 |
| `LEADER` | 目標點及文字方向 | 倒角、表面註記等引線標註 |
| `HOLE_PATTERN` | 中心、孔數、孔徑及節圓語意 | 孔群標註 |
| `CENTERLINES` | 投影 footprint 或中心／軸 | 中心線與中心標記 |

複合尺寸不得硬塞成單一邊。它應明確保存兩個以上的端點特徵、量測語意（例如 overall、between-faces、annular outer/inner diameter）及其拓撲來源。

## 10. 模板與歷史案例的作用

模板可以設定：

- `enabled`
- `preferred_view`、`views` 或 `target_views`
- `dim_type`
- `side` 或 `sides`
- `baseline`
- `rank`
- `prefix`
- `tolerance`
- `offset`
- `leader_angle`

模板只能選擇與修飾候選規則，不能改寫 3D 名目幾何或製造不存在的特徵。

未來從既有 2D 工程圖學習時，每一筆 `DrawingRuleCase` 至少應記錄：

- 來源圖面及可追溯的標註 handle。
- 已對應的 canonical 3D feature 或複合 feature。
- 尺寸／公差語意與單位。
- 視圖方向、旋轉、鏡射及剖視狀態。
- 相對於投影 footprint 的正規化錨點和標註位置。
- side、rank、baseline、leader 方向與群組關係。
- 幾何驗證、OCR／DXF 解析與拓撲對應的個別可信度。
- `AUTO_VERIFIED`、`REVIEW_CANDIDATE` 或工程師確認狀態。

如此學到的規則才能縮放、旋轉或鏡射後套用到新模型；不能直接複製舊圖座標。

## 11. 驗證門檻

每次新增特徵或繪圖規則，至少驗證：

1. 零件類型正規化結果正確。
2. 規則引用的 feature ID 可在 canonical feature records 找到。
3. 每個啟用視圖都有自己的 `view_geometry`。
4. 投影 footprint 與同視圖 HLR 輪廓方向一致，沒有非預期鏡射。
5. 錨點位於目標特徵 footprint 上或在定義清楚的容許距離內。
6. 未知尺寸不產生虛構的 1 mm、0 mm 或預設公差。
7. 模板套用後，名目值、特徵 ID 與幾何錨點保持不變。
8. `DimensionTask` 到 DXF/PDF 的顯示文字包含已設定的公差。
9. 軸、扇葉、外殼、沖壓底座及一般零件的回歸測試均通過。

建議基準模型至少包含：軸類、葉輪、風扇外殼、沖壓底座，以及一個未見過的一般機械零件。單一風扇案例通過不能代表規則具有通用性。

## 12. 新規則的導入流程

```text
新增 canonical feature 語意
  -> 為每個可用視圖建立投影 footprint
  -> 產生預設停用或保守啟用的 CandidateRule
  -> 加入模板／歷史案例選擇
  -> 驗證 DimensionTask 與實際輸出
  -> 執行跨零件 benchmark
  -> 才能合併到正式流程
```

特定零件的例外只能在它代表可重用的幾何語意時加入，例如「環形特徵外徑」或「旋轉葉片包絡」，不能用料號判斷。

## 13. 目前仍待完成的工作

- 從歷史 2D 圖面學習「哪些候選規則應啟用」的選擇器。
- 複合特徵、剖視圖、局部視圖與斷裂視圖的正式 schema。
- 尺寸群組、基準鏈與 GD&T feature control frame 的規則契約。
- 更完整的標註碰撞求解與跨視圖最佳化。
- 對全資料庫執行繪圖規則 benchmark，建立可比較的版本指標。

## 14. 相關文件

- [`drawing-engine.md`](drawing-engine.md)：既有繪圖引擎與輸出管線說明。
- [`architecture.md`](architecture.md)：系統整體架構。
- [`tolerance-recommendation.md`](tolerance-recommendation.md)：公差推薦資料流。
- [`tolerance-benchmark.md`](tolerance-benchmark.md)：公差抽取與推薦驗證方式。
