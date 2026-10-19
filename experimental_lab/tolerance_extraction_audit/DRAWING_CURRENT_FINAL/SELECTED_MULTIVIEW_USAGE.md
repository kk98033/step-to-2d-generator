# Engineer-selected multiview output

The experimental Main keeps analysis-only behavior by default. Pass an
engineer-selection JSON file to additionally generate independent front, top,
and right DXF/PDF/PNG drawings. Fan fixtures may request one dynamic centre
half-section by setting `include_half_section` to `true`.

```powershell
.\pyoccenv\python.exe .\experimental_lab\tolerance_extraction_audit\DRAWING_CURRENT_FINAL\experimental_main.py `
  --step .\models\BLADE_ASSY-1AC085000H-R01.stp `
  --selection-config .\experimental_lab\tolerance_extraction_audit\experiments\2026-10-19\23_minimal_selected_multiview_drawing_v1\01_experiment_config.json `
  --output .\scratch\drawing_current_final_selected
```

This path is experimental and does not modify the production or legacy drawing
engines.
