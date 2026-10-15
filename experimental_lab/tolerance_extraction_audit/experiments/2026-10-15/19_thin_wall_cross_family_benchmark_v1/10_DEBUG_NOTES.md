# Experiment 19 DEBUG notes

## Legend

- White: original STEP HLR projection or actual B-Rep section.
- Orange/yellow: section boundaries actually selected by the radial detector.
- Red/cyan: accepted thickness anchors when a candidate exists.
- Green summary: accepted candidate.
- Red summary: rejected candidate and the failed contracts.

## Findings

### Cylindrical wall

The front projection clearly contains concentric inner and outer circles, but
all four section stations report `pattern=0`. The planar fallback also reports
zero candidates. This confirms a missing coaxial-cylinder surface-pair path;
it is not a projection visibility problem.

### Complete radial blade assembly

The section detector finds 7–10 repeated boundaries depending on the station,
and the orange lines show those boundaries on the real section. However, the
paired-boundary thickness stage returns `LOCAL_BOUNDARY_PAIR_UNSTABLE` at every
station. The failure occurs after feature discovery, during companion-boundary
pairing and station consistency.

### Low-count radial blade component

`BLADE-AC08_1` is one assembly-tree component, but its geometry contains a hub
and seven blades. The detector finds seven repeated boundaries at the later
stations, yet their measured companion gaps collapse to zero/unstable values.
It therefore fails for the same pairing reason as the complete assembly, not
because no blades are visible.

### Cover candidate

The periodic path correctly remains inactive. The planar face-pair path finds
one candidate and produces a 0.400000 mm `REVIEW_CANDIDATE`. The red/cyan
anchor is visible on the thin side projection.

### FQ6H success baseline

All four section stations find 61 repeated boundaries and stable local pairs.
Their medians remain approximately 0.2505 mm, so the result is accepted as
`thin_wall.normal_thickness` with `AUTO_VERIFIED` status. This confirms the
benchmark failures are coverage/pairing gaps rather than a regression in the
verified FQ6H path.
