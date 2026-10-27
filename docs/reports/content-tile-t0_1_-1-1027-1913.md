# Playable tile `t0_1_-1`

Built on 2026-10-27 in sandbox `content-tile-t0_1_-1-1027-1913`. The L0 tile covers E 23459750–23460000, N 6704500–6704750. Its input area is `work-3x3`; all inputs were already cached (0 missing files, 0 fetched bytes, 6,028,610,119 registered input bytes). No data coverage gap blocked the build. The tile is listed in `$TURKU3D_DATA/playable-tiles.json` for the next integration.

## Content and size

The new tile has ground, 43 distinct building shells, 174 prop objects, vegetation, and 52 parked cars. The roads producer supplies the playable-area road graph; three car-road edges cross this tile's southern boundary. Its assembled visual has 115,000 triangles and is 3,268,904 bytes as a GLB. Brotli visual bytes plus ground textures total **5,643,916 bytes on the wire**, below the 8,000,000-byte cap. The published visual layers are `ground`, `buildings`, `props`, `vegetation`, and `traffic_static`; roads are in the separate graph asset.

## Build wall times

| Stage | Seconds |
|---|---:|
| Inputs | 0.224 |
| Seed | 1.222 |
| Ground | 1,490.503 |
| Buildings | 2,444.800 |
| Structures | 46.970 |
| Facades | 50.067 |
| Bridges | 101.279 |
| Roads | 4.315 |
| Outer buildings | 577.004 |
| Props | 487.724 |
| Vegetation | 343.685 |
| Parked cars | 82.680 |
| Ground textures | 2,885.470 |
| Bridge textures | 0.001 (cached) |
| Refresh wall time (overlapping stages) | 5,380.362 |
| Assemble | 3,962.368 |
| **Total** | **9,344.190** |

The shared host load exceeded 300 during parts of the run. The first structures pass reported a missing sandbox validator dependency, `@dimforge/rapier3d-compat`; `make tile-add` now installs `col-deps` before refresh. A direct structures rebuild with the dependency installed passed in 46.7 s.

## Verification

- Assembly published 21 visual tiles and 124 collision cells. Its source-layer and decoded `.col` seam checks passed. A focused decoded check of the new tile's 16 collision cells plus four southern neighbour cells found **0 seam errors**. The road graph has **3 car-road edges** crossing the south boundary.
- The focused Playwright drive passed on WebGPU and WebGL2 against both the packaged server (4.0 min) and this sandbox's Vite dev server (4.2 min). It starts on the office-side tile `t0_1_0` at E 23459838.3, N 6704479.6, drives across N 6704500, and asserts `t0_1_-1` is visible and the car is inside it. The end poses were approximately E 23459821.5, N 6704504.3 (WebGPU) and E 23459819.5, N 6704507.1 (WebGL2) on the packaged run. The route begins at the northern approach of the office area, not at the office address.
- Vite dev-server screenshots contain only game-rendered assets, no street imagery: `$TURKU3D_WORK/reports/content-tile-t0_1_-1-1027-1913/{webgpu,webgl}-{south-boundary,north-tile}.png`. Packaged-server copies are `{webgpu,webgl}-dist-north-tile.png`; drive poses are `{webgpu,webgl}-drive.json`.

Exact commands:

```sh
make tile-add T=t0_1_-1
make structures-build ARGS='--area office-block'
cd game && E2E_TILE_DEV=1 E2E_TILE_PORT=5290 TILE_DIST="$TURKU3D_WORK/dist/tiles" python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.tile.config.ts north-tile.spec.ts --workers=1 --retries=0
bash pipeline/env/run.sh python -m turku3d.region list-tile t0_1_-1
```

The full office-address-to-northern-tile road drive remains a separate route check; this run validates the southern tile transition and streaming on both backends.
