# New chat handoff: experimental 2D drawing engine

Updated: 2026-10-15

## Start here

This file is the compact continuation point for a new Codex conversation.
Do not reconstruct progress from chat history. Read this file, then read:

1. `DRAWING_CURRENT_FINAL/README.md`
2. `DRAWING_CURRENT_FINAL/ENGINE_MANIFEST.json`
3. `experiments/2026-10-15/20_experimental_main_render_smoke_v1/00_README.md`
4. `experiments/2026-10-15/19_thin_wall_cross_family_benchmark_v1/09_RESULTS.md`

The mandatory workspace knowledge files in `project_knowledge/` still apply.

## Current engine state

- Engine version: `drawing-experimental-v0.18.0`
- Entrypoint: `DRAWING_CURRENT_FINAL/experimental_main.py`
- Deployment: `EXPERIMENT_ONLY`
- Production integrated: no
- Main mode: `ANALYSIS_ONLY`
- Legacy `auto_2d_drawing/dimension_engine.py`: unchanged
- Legacy `extractors/`: unchanged

Main currently performs STEP/B-Rep analysis, detector dispatch and unified
`DimensionIntent` construction. Type-specific renderers can draw several
feature families, but Main does not yet produce one complete automatically
selected and collision-free drawing.

## Active Main capabilities

- annular composite features;
- solid stepped-shaft features;
- rectangular-hole relations;
- radial/hub angular relations measured from detected boundaries;
- generic paired-surface thickness;
- unified dimension intents.

Important angular rule: radial angle annotations measure actual detected
boundary/feature spacing. They are not calculated as `360 / blade count`.

## Verified rendering paths

- Solid shaft audit rendering: 5 segments, 4 shoulders, 11/11 intents
  `AUTO_VERIFIED` on the current smoke case.
- Unknown radial model angular rendering: geometry-trigger benchmark has
  3 true positives, 2 true negatives, no false positives/negatives.
- Thin-wall rendering: 6 decisions correct, 5 resolved, 5 DXF files with
  real `DIMENSION` entities.
- Historical tolerance values remain separate from STEP nominal geometry.
  Geometry-only cases do not invent tolerances.

## Latest experiments

### Experiment 19: thin-wall cross-family benchmark

- Accuracy: 66.7%
- Precision: 100%
- Recall: 50%
- False positives: 0
- False negatives: 3

Missing geometry paths:

1. coaxial cylinder/cone surface-pair thickness;
2. low-count/freeform paired-surface thickness;
3. stable section coverage for another complete radial blade assembly.

Historical drawing audit correction:

- The synthetic cylindrical control has no historical drawing.
- `1AC085000H-R02` does not directly dimension blade thickness.
- `R0.20` is a radius, not blade thickness.
- The two real blade misses are geometric-feature recall failures, not proven
  automatic dimension-selection failures.

### Experiment 20: current Main render smoke

Smoke models:

1. stepped solid shaft;
2. `BLADE_ASSY-1AC085000H-R01`;
3. `DISH-2FS0D3010H_A01`.

Result: each type-specific renderer produced a useful visual result. Remaining
integration work is automatic view selection, intent filtering/ranking,
renderer dispatch, collision layout and complete DXF orchestration.

The DISH case also activates unrelated annular, shaft and rectangular-hole
detectors. Treat this as an intent-ranking/exclusivity problem before emitting
a full drawing.

## Next recommended experiment

Build `21_intent_selection_and_view_orchestration_v1` without modifying the
production engine:

1. separate geometry detection confidence from drawing-selection confidence;
2. rank intents using feature semantics, selected view visibility and
   historical evidence;
3. introduce detector compatibility/exclusivity rules without part-name
   special cases;
4. dispatch only selected intents to verified renderers;
5. produce a combined preview and DXF for the three Experiment 20 smoke cases;
6. compare against the same fixed smoke cases and record regressions.

Do not start by adding more part-name special cases. Feature geometry and
semantic evidence must remain the primary trigger.

## Commands

Run Main analysis:

```powershell
cd D:\School\力致\app\step-to-2d-generator
.\pyoccenv\python.exe .\experimental_lab\tolerance_extraction_audit\DRAWING_CURRENT_FINAL\experimental_main.py `
  --step <STEP_PATH> `
  --output .\scratch\drawing_current_final
```

Run experimental regression:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\experimental_lab\tolerance_extraction_audit\DRAWING_CURRENT_FINAL\run_regression.ps1
```

## Suggested first message in a new chat

```text
請先讀取 experimental_lab/tolerance_extraction_audit/NEW_CHAT_HANDOFF.md 和
DRAWING_CURRENT_FINAL/ENGINE_MANIFEST.json，延續實驗版 2D 繪圖引擎。
目前從 Experiment 21 開始：實作通用的 intent selection、view orchestration
與 renderer dispatch，不要修改正式舊引擎，也不要使用零件名稱硬編碼。
```

## Token-saving workflow

- Treat files and reports as the source of truth; do not replay old chat.
- Read only required JSON fields and write full logs to disk.
- Rerun affected cases only; use the fixed benchmark for milestone checks.
- Return concise conclusions and artifact paths instead of full tool output.
