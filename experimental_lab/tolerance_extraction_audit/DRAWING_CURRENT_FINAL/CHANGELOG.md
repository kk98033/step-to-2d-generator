# Changelog

## drawing-experimental-v0.26.0 -- 2026-10-20

- Integrated collision-aware radial-band angle placement from Experiment 30.
- Kept related `10 deg`/`20 deg` dimensions on the same detected outer groove
  while distributing independent repeated angles to clearer grooves.
- Integrated native full-span diameter dimensions with two inward-facing
  endpoint arrows and a mandatory DXF direction audit.
- Kept fan section generation explicitly engineer-requested; no automatic
  decision that a fan requires a half-section was added.
- Kept production and legacy drawing engines unchanged.

## Unreleased — production integration planning

- Documented that the web Main, web smart-annotation engine, and experimental Main are currently separate pipelines.
- Defined the target shared analysis, DimensionIntent, view-plan, renderer, cache, and audit contracts.
- Added a four-phase production integration plan with feature-flag rollout and explicit completion gates.
- Kept the production and legacy drawing engines unchanged.

## drawing-experimental-v0.23.0 — 2026-10-19

- Added optional engineer-selection input to the experimental Main.
- Added separate front, top, and right DXF/PDF/PNG engineering drawings.
- Added an optional fan half-section selected from dynamic Bnd_Box-derived candidates.
- Added Experiment 22 engineer-selected fan angles as native DXF angular dimensions.
- Blocked feature selections whose projection validation status is `FAIL`.
- Kept the existing analysis-only mode unchanged when no selection config is supplied.
- Production and legacy drawing engines remain unchanged.

## drawing-experimental-v0.18.0 — 2026-10-15

- Added geometry-only paired-surface thickness detection to the experimental
  entrypoint; no part name or fixed 3D coordinate is used.
- Added `thin_wall.normal_thickness` and `thin_wall.thickness_profile` to the
  unified DimensionIntent contract.
- Added local aligned thickness placement and actual DXF `DIMENSION` output.
- Kept STEP nominal geometry, released drawing display value, tolerance, and
  tolerance provenance as separate fields.
- Variable-thickness profiles and ambiguous planar pairs remain visible as
  `REVIEW_CANDIDATE` instead of being silently promoted.
- Production and legacy drawing engines remain unchanged.

## drawing-experimental-v0.11.0 — 2026-10-14

- 建立固定的實驗繪圖主線與能力 manifest。
- 納入環狀複合特徵、實心軸、統一尺寸意圖。
- 納入由實際徑向邊界量測的角度關係；禁止以 `360° / 葉片數` 代替。
- 納入矩形孔位關係與原生角度標註 renderer。
- 將剖半視圖定義成由幾何證據觸發的能力，不綁定風扇檔名。
- 明列尚未完成的未知模型選標、section plan 與整張 DXF orchestration。
