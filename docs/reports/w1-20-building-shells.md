# W1-20: Four-tile building coverage

The requested L0 tiles are `t0_0_0`, `t0_0_1`, `t0_1_0`, and `t0_1_1`.
The source inventory has 60 kantakartta footprints intersecting these tiles.
The prior buildings layer contained shells for all 60, but 10 crossing buildings
were owned by neighboring visual tiles and absent from this four-tile delivery.
The coverage audit lists each missing ID, its source choice, reason, and OSM address
when present in `$TURKU3D_WORK/reports/buildings-before/coverage.json`.

| Delivered state | Before | After |
|---|---:|---:|
| Selected CityGML/roofer model | 41 | 48 |
| Lidar-derived fallback shell | 9 | 12 |
| Work-in-progress placeholder | 0 | 0 |
| Missing visual building | 10 | 0 |

The building producer now accepts `--tiles` and owns each cross-boundary shell in
the selected tile with the largest footprint overlap. It caches native roofer runs
by footprint geometry, source LAZ identity, and roofer implementation. Lidar
fallbacks can form flat, gable, hip, and two-level closed roofs on supported
four-corner footprints. If a model and lidar shell are both unusable, a closed
box with ID and address artwork is published. This last path has synthetic
coverage; no current footprint required a placeholder.

The after-build layer audit passed: 60 closed collision shells, 32 collision
tiles, four visual tiles, and no errors. The map files are under
`$TURKU3D_WORK/reports/buildings-{before,after}/coverage.png`.
The synthetic building suite passed 28 Python and two Rapier Node tests in
17.9 seconds. The scoped gate passed in 28.0 seconds after the address update.
The four-tile assembler completed with nine visual and 64 collision tiles and
261 current facade bindings. An example placeholder artwork PNG is under
`$TURKU3D_WORK/reports/building-coverage/placeholder-preview.png`; it is a
synthetic preview because no current footprint needed a placeholder.
The assembled Chrome aerial screenshot is
`$TURKU3D_WORK/reports/buildings-after/aerial.png`.
The exported-collision Rapier run passed all 2,000 ten-second walkers: zero
entries, zero final positions inside, no missing shells, and no untested
buildings. Under concurrent host load it took 618.3 seconds.

## Reproduce

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.buildings.coverage \
  --layer "$TURKU3D_DATA/build/layers/buildings" \
  --out "$TURKU3D_WORK/reports/buildings-before"
bash pipeline/env/run.sh python -m turku3d.buildings \
  --roofer-output /Users/wnt/turku3d/wt/w1-03-buildings/work/cache/buildings/roofer
bash pipeline/env/run.sh python -m turku3d.buildings.coverage \
  --out "$TURKU3D_WORK/reports/buildings-after"
bash pipeline/env/run.sh python -m turku3d.buildings.audit
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
```

The `--roofer-output` path above reuses the earlier read-only reconstruction
for this measurement. A normal `make buildings-build` creates a region-keyed
reconstruction in the current sandbox cache.
