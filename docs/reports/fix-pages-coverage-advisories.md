# Pages coverage and eac45d3b advisory repairs

Measured against release `eac45d3b76ee` (2026-10-28), using the
`fix-pages-coverage-advisories` sandbox. No shared raw inputs were changed.

## Pages region

The tile ledger already counted fast maps, in `facades.fast` and
`rough_coverage`. Pages selected on `facade_coverage`, which deliberately counts
only generated and derived maps. Its percentage rounded to zero for tiles
whose coverage consisted almost entirely of fast maps. Selection and release
notes now use rough coverage, falling back to the old field for older ledgers.
The quality ledger keeps its distinction between fast and generated maps.
Pages now follows the playable tile planner's edge-or-corner adjacency; otherwise
the corner-adjacent t0_2_2 expansion would still be excluded.

The next publish plan from the current release includes:

| Tile | Shippable facade coverage |
|---|---:|
| t0_0_0 | 77% |
| t0_0_1 | 61% |
| t0_1_0 | 93% |
| t0_1_1 | 85% |
| t0_2_2 | 66% |

`t0_1_-1` (0.82%) and `t0_2_0` (0%) still fail the existing 20% threshold.
This is a measured publish plan; the runner/integrator publishes after landing.

```sh
scripts/dev/publish-pages --plan --release "$TURKU3D_DATA/live"
python3 -m unittest discover -s scripts/dev/tests -p test_pages.py
```

## Facade footing

The new fast map exposed a geometry gap: wall `1034470398_32` started at
25.423 m while its adjacent terrain was at 24.959 m. Its map already spanned
v=1..0. Building publication now samples nearby ground and extends only vertices
shared by the footing and its walls. Roofs stay in place, collision stays closed,
and normal facade migration rebinds maps to the corrected wall dimensions.
The fit uses at most 0.75 m, matching the rebind's base-correction range. Ground
is now an explicit building publication dependency and refresh finishes it first.
The cached selected shell remains the input, avoiding cumulative extension.

For this building the required footing extension is 0.588 m; the audited wall's
signed residual becomes -0.124 m. A sandbox clone of the full release inventory
and facade assembly passed the unchanged audit with all **771** maps retained:
538 kept and 233 reprojected, zero failed bindings. The fit affected 61 buildings.
This check regenerated facade geometry from cached shells and exercised the real
rebind; it did not rebuild the full city tile package. Evidence is retained in
`$TURKU3D_WORK/reports/ground-audit.json` and `ground-target.json`.

```sh
bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_buildings_grounding.py pipeline/tests/test_buildings.py \
  pipeline/tests/test_refresh.py
bash pipeline/env/run.sh python -m turku3d.package.facade_ground_audit
```

## Coarse terrain

The t0_2_2 expansion refines parent t1_1_1 into its L0 children. The coarse
sibling t0_3_2 is visibly drawn at the test camera; the old assertion accepted
only the replaced parent's ID. No renderer fix was needed. The corrected check
requires visible coarse ground within that parent's subtree, excluding playable
ground. WebGPU passed in 4.4 s with visible coarse tile t0_3_2; total browser
usage was 0.2 minutes. No new browser spec was added.

```sh
TURKU3D_JOBS=1 E2E_TILE_DEV=1 python3 scripts/dev/host_slots.py browser -- \
  game/node_modules/.bin/playwright test -c game/playwright.tile.config.ts \
  --project webgpu --workers 1 -g 'coarse terrain remains visible'
```

## Reference cache test

The metadata-only resume test constructed a legacy full-feature cache key but
omitted the terrain slot introduced by the N2000 camera calibration. Adding the
Google-only `None` terrain slot restores the intended migration fixture without
changing the production cache's geometry or terrain invalidation rules.
All 25 tests in `test_facade_batch.py` pass.

```sh
(cd textures && ../pipeline/env/run.sh python -m pytest -q tests/test_facade_batch.py)
```

Each slice passed `scripts/dev/gate --fast --base main`. Mid-turn
`codex-task finish --keep-sandbox` refused the active Codex runner; the separate
commits and slice result files were reported to the orchestrator. No manual merge
of main was attempted. Turn-end landing and publication remain runner-owned.
