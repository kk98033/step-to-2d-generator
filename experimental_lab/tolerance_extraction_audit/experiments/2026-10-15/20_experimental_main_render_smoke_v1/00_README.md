# Experiment 20: Experimental Main render smoke

## Baseline

- Experimental engine: `drawing-experimental-v0.18.0`
- Main entrypoint: `DRAWING_CURRENT_FINAL/experimental_main.py`
- Main mode: `ANALYSIS_ONLY`
- Production engine modified: no
- Legacy extractor modified: no

The Main entrypoint currently runs geometry detectors and builds unified
`DimensionIntent` records. It does not yet dispatch every intent into one
complete drawing. This smoke therefore runs Main first and then invokes the
already verified type-specific renderers.

## Cases

1. Stepped solid shaft
   - Main activated `solid_shaft_features`.
   - 11 unified intents.
   - Shaft renderer: 11/11 dimensions `AUTO_VERIFIED`.
2. `BLADE_ASSY-1AC085000H-R01`
   - Main activated annular, shaft, rectangular-hole, and radial-relation
     detectors.
   - 35 unified intents.
   - Angular renderer produced measured 20, 30, and 50 degree relations.
3. `DISH-2FS0D3010H_A01`
   - Main activated paired-surface thickness and produced a 0.4 mm thickness.
   - Thickness remains `REVIEW_CANDIDATE` because no historical tolerance was
     supplied.
   - Main also activated annular, shaft, and rectangular-hole detectors. These
     require intent ranking/filtering before a complete drawing is emitted.

## Result

The type-specific renderers can draw the selected features. The remaining
integration gap is not basic line rendering; it is automatic view selection,
intent filtering/ranking, renderer dispatch, collision layout, and complete
DXF orchestration.

See `01_render_smoke_gallery.png` for the visual comparison.
