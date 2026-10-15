# Experiment 19 — Thin-wall cross-family benchmark

This experiment tests the Experiment 18 detector outside the original FQ6H
case. It separates labelled controls from audit-only real components so that
unknown parts do not create a misleading accuracy value.

## Labelled controls

- Positive: planar thin plate, annular cylindrical wall, DISH wall, complete
  radial blade assembly, and a single known blade component.
- Negative: thick solid box, solid cylinder, and real solid shaft.

## Audit-only real components

Motor casing, cover components, and housing components are reported but are
not counted as correct/incorrect until an engineer confirms their intended
wall-thickness semantics.

## Run

```powershell
.\pyoccenv\python.exe experimental_lab/tolerance_extraction_audit/common/scripts/run_thin_wall_cross_family_benchmark.py `
  --config experimental_lab/tolerance_extraction_audit/experiments/2026-10-15/19_thin_wall_cross_family_benchmark_v1/01_experiment_config.json `
  --output-dir experimental_lab/tolerance_extraction_audit/experiments/2026-10-15/19_thin_wall_cross_family_benchmark_v1
```

The experiment does not change either the production engine or the
experimental Main detector.
