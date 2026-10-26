# Playable-tile building coverage

The default building scope is `config.PLAYABLE_TILES`: E 23459500–23460000,
N 6704000–6704500. The immutable `GIS_Rakennukset` cache contains 115 building
records intersecting this square: 112 polygon footprints and three point-only
records. Existing kantakartta building IDs and their independent Roofer reconstruction
remain stable; additional GIS polygons fill the missing blocks. Each point-only
record receives a labelled 6 m placeholder, not a claimed surveyed footprint.

The delivered visual-layer audit is **115/115**: 93 city/Roofer models, 19 lidar
shells and three point-only placeholders. The coverage map is written to
`$TURKU3D_WORK/reports/building-coverage/coverage.png`, with exact
source record and delivered building IDs in `coverage.json`.

The surrounding `work-3x3` area has 1,070 additional GIS building records outside
the playable square. The visual-only `buildings_outer` layer publishes 1,035
lidar-height extrusions and 35 visible fallback boxes across 32 L0 source tiles.
The assembler uses those visuals in eight T1 and four T2 skyline parents:

| Parent | Outer buildings | Parent | Outer buildings |
|---|---:|---|---:|
| `t1_-1_-1` | 153 | `t2_-1_-1` | 153 |
| `t1_-1_0` | 126 | `t2_-1_0` | 177 |
| `t1_-1_1` | 51 | `t2_0_-1` | 211 |
| `t1_0_-1` | 112 | `t2_0_0` | 529 |
| `t1_0_1` | 78 | | |
| `t1_1_-1` | 99 | | |
| `t1_1_0` | 192 | | |
| `t1_1_1` | 259 | | |

The detailed
counts and unsupported lidar records are in
`$TURKU3D_WORK/build/layers/buildings_outer/metrics.json`.

The available shared approved facade export has 316 bound maps. Rebinding the new
geometry produces 318 valid maps: 82 kept and 236 reprojected. The migration
archives 14 old approvals with no safe successor and lists 253 new unbound walls;
new buildings use library materials. No new imagery was generated. The requested
344-map inventory is not present in the current shared export.

The point-only placeholder bases use nearby class-2 ground returns at 4.339,
29.173 and 3.711 m N2000. Their outlines remain explicitly inferred.
The exported collision layer passed 2,000 seeded ten-second Rapier walkers with
zero entries, zero final positions inside, no missing shells and no untested
buildings in 214.650 s. Walker starts use the sampled wall's local facade base,
so elevated walls are tested at their actual height; placeholders use their
recorded fallback base.

The assembled L0 tile wire sizes, including ground textures and the required
bridge layer, are 7,021,314,
7,960,895, 7,956,153 and 7,941,351 bytes for `t0_0_0`, `t0_0_1`,
`t0_1_0` and `t0_1_1` respectively. All are below the 8,000,000-byte limit.
Packing the existing traffic sign faces into a shared tile atlas and sharing
the identical furniture palette reduces the office view to 464 draws on both
WebGPU and WebGL2 at 2560×1440. Chrome WebGPU colour screenshots
from the spawn facing north, east, south and west, plus the southeast corner,
are in `$TURKU3D_WORK/reports/building-screenshots/`.

## Verification

```sh
source ../sandbox.env
make buildings-build
make buildings-outer
bash pipeline/env/run.sh python -m turku3d.buildings.coverage
make refresh
make buildings-test
make buildings-validate
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make game-build
make tile-show # in another terminal, then render with qa/render/render.mjs
make tile-e2e
scripts/dev/gate --base main
```
