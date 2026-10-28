# Roof algorithm corrections, 2026-10-28

The tested slice fixes the nine reported draft roofs and the three reported light office roofs.
No per-building exceptions or external map data were used. Roof work was stopped at the
orchestrator's priority change after the real builds and paired renders passed.

## Causes and selection

Drafts used **CityGML LOD2 only**: projected-area-weighted mean roof vertex height.
Register floors and OSM levels supplied facade semantics; neither supplied a competing draft
height. Classified roof lidar was never consulted. Overlapping upper/lower roof decks,
terraces and ridges were flattened into one low mean. For example Building_790295 mixes
25.5–27.7 m upper roofs with 7.8–8.3 m lower decks; its extrusion was 23.059 m.

The replacement partitions the footprint into the upper surveyed roof planes, preserving
slopes and terraces. Folded/non-planar surveyed rings are triangulated at their original
vertex heights. This last rule corrects Building_825814: rejecting its folded face had left
360.9 m² at the old flat fallback, with a 1.48 m median error; retaining it reduces that error
to 0.027 m. Unsupported areas retain the LOD2 fallback rather than inventing register heights.

Available cached class-6 lidar refines each plane using 1 m cell residual medians, a 0.75 m
interior inset, at least eight supported cells, 50% coverage and residual MAD <= 0.35 m.
Sparse/mixed evidence cannot move an entire face. Source arrival/mtime changes invalidate
prepared data and service identities. No classified-LAZ downloads are added to draft builds.
`manifest.roof_heights` records support and offsets. Source class-6 cache reads remain isolated.

Roof partitions form individual solids under one building owner. Passage ceilings remove
lower parts; 5 mm boundary simplification and source/export-precision checks remove collinear
ears before both roof/floor triangulations are exported. Real collision checks stay unchanged.

## Height measurements

The completed broad sweep covers all **72 cached classified-LAZ L0 cells with prepared LOD2**:
**2,054 buildings, 1,960 with eligible held-out roof returns**, 5,884,945 returns. A deterministic
coordinate hash withholds 20% from fitting. Evaluation uses the same class-6 returns inside a
0.75 m footprint inset and above base + 2 m for both variants. These are vertical roof errors,
not closed-shell distances; wall/fixture returns and unsupported source mismatches remain in
the tails. Buildings without eligible class-6 returns are counted but excluded from residuals.

This complete sweep predates the final folded-ring correction. Its envelope/offset results
below are conservative measured evidence for the core change. The final nine-building table,
three tile builds and all paired screenshots include that correction. The final whole-area
repeat was still running when roof work was deprioritized and is not used as completed evidence.

| Absolute held-out error | Original flat mean | Roof envelope | Envelope + lidar |
|---|---:|---:|---:|
| Median m | 1.355 | 0.074 | 0.031 |
| p75 m | 3.562 | 0.188 | 0.098 |
| p90 m | 8.096 | 0.985 | 0.742 |
| p95 m | 10.912 | 2.238 | 2.009 |
| Mean m | 2.842 | 0.512 | 0.432 |
| Share over 2 m | 38.49% | 5.59% | 5.02% |

The hand-reviewed magnitudes locate errors; this independent held-out metric has a different
sampling/masking definition, so its before magnitudes need not equal the private list.

| Building | Old flat height m | Before median error m | Final median error m | Roof parts |
|---|---:|---:|---:|---:|
| Building_815497 | 27.153 | 5.459 | 0.035 | 23 |
| Building_790295 | 23.059 | 4.264 | 0.042 | 5 |
| Building_790292 | 22.833 | 6.888 | 0.049 | 22 |
| Building_790283 | 22.397 | 1.931 | 0.018 | 15 |
| Building_790154 | 27.811 | 1.688 | 0.059 | 12 |
| Building_790269 | 24.496 | 2.247 | 0.088 | 31 |
| Building_825878 | 21.786 | 3.621 | 0.041 | 14 |
| Building_825814 | 26.337 | 3.220 | 0.027 | 49 |
| Building_827239 | 43.391 | 2.795 | 0.014 | 7 |

## Office colour

The full producer already sampled the true ortho, but packaging mapped all four roof classes
to `roof_standing_seam_metal` and reset their albedo to that texture (median RGB 64/64/64).
Its correct `roof_metal_gray` classifications therefore still became dark roofs.

Sampling now insets the footprint, rejects nodata/vegetation and deep relative shadows, and
trims highlights. Measured sRGB is carried as `roof_rgb`; packaging converts it to linear
vertex colours, bypasses generic albedo, and retains normal/ORM detail. Distinct colours share
one material batch. RGB alone does not establish physical roofing composition.

The identical existing full-office shells were repackaged with and without this metadata:
**57 roofs, mean per-roof RGB MAE 85.94 -> 33.11** against interior ortho pixels, before
lighting. This is a material-colour fit statistic, not an independent image-recognition score.
Facades and selected shells are unchanged.

| Flagged full-pipeline building | Old RGB | Measured RGB | RGB MAE before -> after |
|---|---|---|---|
| gis-1032865794 | 64,64,64 | 208,214,214 | 129.92 -> 24.99 |
| gis-103295897P | 64,64,64 | 215,217,216 | 138.33 -> 15.04 |
| gis-103304664H | 64,64,64 | 206,207,203 | 106.33 -> 43.61 |

## Real output and visual inspection

All three affected draft tiles build with unchanged object counts and valid collision files.
The office comparison preserves all 57 full-pipeline roof owners. The local gallery retains
**64 PNGs**: before/after, top-down/oblique for four tile overviews and all twelve findings.
Every draft building in the three tile overviews and every office roof is visible in those
views; focused views inspect all reported spots. No missing building or misplaced roof was
found. The final viewer applies the game's facade-kit shader and common lighting in both
phases. Office views isolate full building geometry without a ground layer.

| Tile | Buildings before -> after | Visual triangles before -> after | Brotli bytes before -> after |
|---|---:|---:|---:|
| t0_-1_1 | 23 -> 23 | 59725 -> 62597 | 382065 -> 437853 |
| t0_-2_2 | 10 -> 10 | 74081 -> 75439 | 426246 -> 455347 |
| t0_-2_3 | 16 -> 16 | 103689 -> 105628 | 379507 -> 422809 |

Evidence is retained under `$TURKU3D_WORK/reports/roofs/`: `index.html`, `render.json`,
`height-audit-before-folded-fix.json` (complete 72-tile sweep), `height-audit-selected.json`
(final nine-building sweep), `colour-audit.json`, and `{before,after}/<tile>/` GLBs/collision.
Browser budget used: 1.7 / 15 browser-minutes. No Google data was opened or sought.

## Reproduce

```sh
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.roof_audit
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.roof_audit --build --tiles t0_-1_1 t0_-2_2 t0_-2_3
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.buildings.roof_audit
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE" --strictPort
python3 scripts/dev/host_slots.py browser -- node qa/render/roof-sanity.mjs
bash pipeline/env/run.sh python -m pytest pipeline/tests/test_coarse_roofs.py -q
node --test pipeline/turku3d/package/facade_kit.test.mjs
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

Five selection/geometry/masking unit tests pass; two JS batching/colour tests pass. Existing
building/coarse tests passed 37/37; tunnel plus selection fixtures passed 8/8 before the final
folded-ring fixture was added. Final gate results are in the task result. No slow/browser
specs were added to the gate. The partial final broad repeat is advisory follow-up only,
consistent with the orchestrator's instruction to wrap this lower-priority work now.
