# Experiment 19 historical annotation comparison

This supplement checks whether the three thin-wall false negatives in
Experiment 19 are also dimensions selected by engineers in the released
drawing.

## Findings

1. `synthetic_annular_cylindrical_wall` is a constructed control and has no
   historical drawing. A sectional `outer diameter + inner diameter` or a
   direct radial thickness is shown only as the expected annotation semantic.
2. `real_complete_radial_blade_assembly` maps to released drawing
   `1AC085000H-R02`. The drawing dimensions blade spacing angles, envelope/hub
   diameters, radii, and an E-E hub section. It does not directly dimension
   blade thickness.
3. `real_low_count_radial_blade_component` has no independent released part
   drawing. It is an assembly-tree component containing a hub and seven blades
   and can only be compared with `1AC085000H-R02`, which again has no direct
   blade-thickness dimension.

`R0.20` in the historical drawing is a radius annotation, not a blade
thickness.

## Interpretation

The two real cases remain valid false negatives for **geometric thin-wall
feature recall**. They are not proven false negatives for **automatic drawing
dimension selection**, because the released drawing does not show that the
engineer selected blade thickness for annotation.

## Artifacts

- `00_historical_full_sheet.png`: rasterized released drawing
  `1AC085000H-R02`.
- `01_three_case_historical_annotation_comparison.png`: visual comparison.
- `comparison_report.json`: machine-readable findings.

The production drawing engine and legacy extractors are not modified.
