# 公差提取器正式版 V1

本目錄是歷史 2D 工程圖公差提取、3D 特徵連結及正式案例庫部署流程的 V1 Git 基準版本。

## V1 資料流

1. 掃描設定產品系列中的工程圖，從 DXF 原生尺寸實體提取公稱值及上下公差。
2. 去除跨資料夾重複出現的共用圖紙，只保留 canonical drawing occurrence。
3. 將 STEP 零件及組立件投影到工程圖的各個候選視圖。
4. 使用尺寸端點、投影輪廓及 BRep 拓撲連結 2D 公差與 3D 特徵。
5. 依證據分為 `AUTO_VERIFIED`、`REVIEW_CANDIDATE` 與 `AUTO_EXTRACTED`。
6. 驗證正式 `FeatureCaseBase` 可以讀取後，先備份舊案例庫，再以 atomic replace 部署。

## V1 特徵範圍

- 一般孔、軸段、定位肩、倒角、圓角與環槽。
- 整體尺寸與單件複合平面跨度。
- 環形輪轂／外圈及徑向材料區域。
- 扇葉旋轉包絡直徑與中央剖半投影。
- 組件內零件特徵、零件複合特徵與跨零件組立關係。
- 多視圖、多方向、鏡射及同圖多個相似投影實例。

所有幾何位置由 STEP/BRep、模型包絡及投影註冊動態計算，不依賴特定模型的固定座標。

## 正式全庫執行入口

在專案根目錄執行：

```powershell
.\experimental_lab\tolerance_extraction_audit\rebuild_production_case_base.ps1 `
  -Workers 4 `
  -ModelTimeoutSeconds 900 `
  -StageTimeoutSeconds 604800
```

每張 DXF 與每組 STEP-DXF 配對在獨立子程序中執行。單件超時會終止該子程序並記錄，不會鎖死整批。每次執行都建立新的 `experiments/<date>/production_rebuild_*` 目錄、`pipeline.log` 與 `deployment_summary.json`。

## 推薦政策

- `AUTO_VERIFIED`：可進入正式自動推薦。
- `REVIEW_CANDIDATE`：可被查詢與顯示，但必須標記低信賴並由工程師確認。
- `AUTO_EXTRACTED`：只保留於稽核資料，不進入正式推薦。

完整操作、debug 與 benchmark 說明請參閱 [README.md](README.md) 及 [CURRENT_FINAL/README.md](CURRENT_FINAL/README.md)。

## V1 驗證基準

- 發行日期：2026-10-11
- 單元測試：79 tests passed
- 全庫測試：346/346 圖面完成，0 failure，0 timeout
- canonical 圖面：325
- 正式案例：2,181
- `AUTO_VERIFIED`：112
- `REVIEW_CANDIDATE`：279
- 可搜尋案例：391
