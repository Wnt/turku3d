# W1-03 buildings layer: measured report

Date: 2026-10-25. All 81 AOI footprints have valid collision shells under the requested
explicit-fallback policy. **Full reconstruction accuracy remains unmet**; fallback
coverage is not presented as a successful lidar fit. The 0.10 m median / 0.25 m p75
limits, manifold checks and triangle budget are unchanged.

## Coverage and validation

The inventory includes 71 kantakartta buildings (66000) and 10 building parts (66001),
including complete footprints crossing the four-tile envelope plus 50 m margin.

- 81/81 closed shells: 62 roofer models and 19 footprint extrusions.
- 63/81 meet the measured residual limits. There are 21 explicit fallback/degradation
  records: 19 extrusions replacing invalid/missing geometry and two retained roofer
  shells whose fit is better than a flat extrusion but still misses the limits.
- 50 collision tiles, 12 visual tiles, 7,479 visual meshes and 6,370 facade polygons.
- Whole-object collision ownership requires no clipped surface seams. Every exported
  building is closed, consistently oriented and positive-volume; the maximum is 1,444 triangles.
- Facade UVs, stable indices, material metadata and the [facade contract](../specs/facades.md)
  remain available to texturing and assembly.

Outputs are under `$TURKU3D_WORK/build/layers/buildings`. The
[detailed metrics](buildings-metrics.json) preserve every rejected source candidate,
selected model and height-evidence record. [Artifact checks](buildings-checks.json)
audit the exported arrays, rather than just the source shells.

## Residual correction and remaining degraded models

Class-6 lidar includes facades and roof-step walls. Vertical distance to the highest
roof incorrectly treated those returns as roof errors. Selection now uses exact
Euclidean point-to-triangle distance to the complete shell. The previous vertical
statistics remain under `roof_vertical`; no points are trimmed or downsampled.
Analytic fixtures and comparison against exhaustive triangle queries test the metric.

Three of the five previously rejected fits satisfy the same limits with the corrected
metric: `103277355K`, `1032959060` and the nested part
`kanta-f4a8db243ddae25e0cfde199c3707460892a1cf4`.

Two parts remain degraded:

| Building part | Median m | p75 m |
|---|---:|---:|
| `kanta-97e1d0fd0320a859d69b69fbb8293fcfc83fbfdd` | 0.0550 | 0.2700 |
| `kanta-ace494b85c84b8d3f10bdf05e3026e9ed096286c` | 0.2267 | 0.5959 |

A native roofer refinement trial used complexity 0.95, plane epsilon 0.1 m and a
60 points/m² ceiling. It took 63.80 s. Its residuals were 0.0352/0.2974 m and
0.1224/0.4100 m respectively: neither passed. These experimental outputs were not
substituted for the production candidates. The command, outputs and metrics remain
in `$TURKU3D_WORK/cache/buildings/refined-probe/`.

## Fallback evidence

All 12 previously non-manifold reconstructions and all seven missing LoD2.2 records
now have footprint shells subjected to the same geometry checks. Height evidence
uses class-6 returns inside the footprint where available. When these are absent,
a broadly supported local plane in overlap-class lidar can supply a provisional
height; otherwise a nearby class-6 neighbourhood supplies an explicitly marked proxy.
Overlap returns may contain vegetation and never count as class-6 residual evidence.

Each fallback records the reason, whether it was selected, roof/base heights,
support count, height range, source class, support distance and any plane support
metrics. No-data residuals stay null and fail accuracy acceptance. In particular,
four footprints have no class-6 returns; their height proxies are not certified.

## Physics scope

Per-triangle provenance permits each building to be tested independently using its
actual exported collision triangles. This gives nested parts valid outside starts
and prevents an enclosing building from masking an open inner wall. The synthetic
nested-part regression fails when that inner wall is removed.

The real check uses 2,000 seeded ten-second Rapier walkers, covering every footprint,
with a temporary horizontal pad at the shell base. This isolates wall blocking;
integrated terrain, vehicle and streaming checks remain the assembler/game validator's
responsibility. The final run passed: **2,000 walkers, zero entries, zero final positions inside, no
missing or untested buildings**, in 158.533 s. Exact per-building counts
are in `buildings-metrics.json.walker_validation`.

## Verification

```sh
source ../sandbox.env
make -j2 buildings-test
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 bash pipeline/env/run.sh python -m turku3d.buildings \
  --roofer-output "$TURKU3D_WORK/cache/buildings/roofer"
bash pipeline/env/run.sh python -m turku3d.buildings.audit
make buildings-validate
scripts/dev/gate
```

Under the user's coverage-first release scope, the build exits 0 for complete valid
shell coverage. Manifest `params.geometry_acceptance` is true; `params.accuracy_acceptance` and
`metrics.json.pass` remain false while any measured residual limit fails. The CLI prints
both coverage `pass` and `accuracy_pass`. No numerical accuracy check was relaxed.
`make buildings-build` also runs native roofer rather than reusing its cached output.

## Build and area-test timing

The final real-data build took 254.47 s. The pre-rebase area suite passed 24 Python
tests, TypeScript typechecking and both positive/negative Rapier regressions; the Python
portion took 25.77 s under heavy shared machine load. The gate result after rebasing is
recorded below.

`scripts/dev/gate`: **GREEN**, all 19 stages, **261.019 s**. The routing edit
intentionally selected the full suite, including both browser backends. Ruff formatting,
explicit paired-iteration length checks and buildings test routing were added after
rebasing onto `04037fc`. No test limits were relaxed.

A quick read-only city LoD3 check matched 71/81 footprints; residual comparison is
deferred under the release timebox.

The final coverage-first exit/manifest adjustment passed `scripts/dev/gate --base HEAD`
in **5.394 s** (24 buildings tests plus lint, policy and generated-file checks). The
regression checks that complete geometry can pass while accuracy remains explicitly false.

## Follow-ups

Improve the two remaining part reconstructions and the 16 other accuracy-failing
fallbacks using verified building-part associations or newer source data. Four of
these footprints have no class-6 returns. W1-04 must bind materials, use complete
object bounds across visual tile boundaries and run integrated terrain/vehicle checks.

## Shared publisher integration

Rebased onto batch main `aaf46d1` and replaced the custom manifest with
`turku3d.layers.write_manifest` (turku3d-layer v1). The shared reader verifies
every collision/visual filename, byte count and SHA256. Visual output uses
`write_visual`; collision arrays pass shared `CollisionLayer` validation while
retaining the optional per-triangle building ownership used by isolated walkers.
Acceptance and revision live in `params`; detailed metrics and facade inventories
remain sidecars. Walker validation republishes the same shared schema and preserves
tile hashes. Rebuilding clears stale walker evidence.

Focused Python suite: **25 passed, 2.02 s**, including shared-reader compatibility,
corrupt-tile rejection, stale-owned-file removal and walker manifest publication.
Ruff passes. The full gate is deliberately deferred to the orchestrator's batch,
as requested for this urgent integration.

The first assembly check exposed grid-clipping slivers below the delivery format's
1e-6 m² minimum. Collision publication now follows the shared object contract:
whole shells in footprint-centroid tiles with `params.seams = "objects"`.
Selection applies the unchanged delivery `check_tile` checks after float32 conversion;
unshippable candidates use the existing explicitly flagged fallback. The regression
requires unchanged triangle counts, exact source vertices, total area and closed
tile meshes. No delivery check or tolerance was weakened.

Final cached reconstruction rebuild: **128.55 s**, 81/81 shells and unchanged 63/81
accuracy passes. Shared manifest reader: **62 verified files** (50 collision,
12 visual). Exported shell audit: **PASS**, max 1,444 triangles per building.
After the final batch rebase, all **25 Python tests passed in 49.46 s** under
shared host load above 300. The historical 2,000-walker result above predates
the collision ownership change; integrated physics should be rerun on assembly.
