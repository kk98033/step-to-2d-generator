# Experiment 19 results

## Result

The current thin-wall detector is conservative but not yet broad enough for
production deployment across unknown part families.

- Total cases: 13
- Labelled cases: 9
- Audit-only real components: 4
- True positives: 3
- True negatives: 3
- False positives: 0
- False negatives: 3
- Accuracy: 66.7%
- Precision: 100.0%
- Recall: 50.0%
- Errors: 0

The 100% precision means that the labelled set did not contain a solid part
incorrectly promoted to a wall-thickness feature. The 50% recall means that
half of the known thin-wall families were still missed.

## Confirmed successes

- Previously verified FQ6H selected component: 0.250536 mm,
  `AUTO_VERIFIED`.
- Synthetic planar plate: 0.800000 mm, `REVIEW_CANDIDATE`.
- Real DISH planar wall: 0.400000 mm, `REVIEW_CANDIDATE`.
- Solid box, solid cylinder, and real shaft were all correctly rejected.

## Confirmed gaps

1. **Concentric cylindrical wall** was missed because the current fallback
   only pairs planar faces; it does not pair coaxial cylindrical surfaces.
2. **Low-count radial blade component** was missed. The STEP node called
   `BLADE-AC08_1` contains a hub and seven blades rather than one isolated
   blade. Repeated boundaries were detected, but their companion gaps were not
   stable enough for the thickness contract.
3. **A different complete radial blade assembly** was missed because its STEP
   assembly/section representation did not produce three stable section
   stations. The previously selected FQ6H component still passes, so this is a
   model/component selection and section-coverage gap rather than a regression
   in the FQ6H implementation.

## Audit-only observations

- One cover component produced a 0.400000 mm planar-wall candidate and remains
  `REVIEW_CANDIDATE`.
- Motor casing, secondary cover, and housing components produced no candidate.
- These four cases are excluded from accuracy until their intended engineering
  wall semantics are confirmed.

## Decision

Do not promote generic thin-wall detection to production yet. Keep the
Experiment 18 implementation in experimental Main and add two geometry paths:

1. coaxial cylinder/cone surface-pair thickness;
2. low-count/freeform paired-surface thickness using sampled closest-point and
   local-normal consistency without requiring dozens of repeated blades.

After those paths are added, rerun this exact benchmark. The target should be
zero false positives and materially higher recall before production merge.
