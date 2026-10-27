# Playable tile t0_2_0

Tile `t0_2_0` covers E 23460000–23460250, N 6704250–6704500. It is now in `$TURKU3D_DATA/playable-tiles.json` for the next integration build. The sandbox build used the existing `work-3x3` raw input area (6,028,610,119 bytes); no input was missing or fetched.

## Content and budget

The assembled L0 visual tile contains 279,194 triangles, with ground, building shells, props, vegetation, and parked-car visual layers. Its roads layer contributes 174 clipped ways to the playable road graph. The source footprints include 36 buildings centred in the tile; the vegetation inventory has 162 trees, the props layer has 127 objects, and the parked-car inventory has 16 cars in the tile. The shipped visual GLB is 5,067,564 bytes (3,369,692 Brotli bytes); ground maps add 4,266,227 bytes. **Wire total: 7,635,919 bytes, below the 8,000,000-byte cap.**

The initial `make tile-add T=t0_2_0` built the layers but failed in assembly: `content.addresses.publish` still read `roads/MANIFEST.json` `inputs.osm.path`, removed when roads became per-tile. The address publisher now reads each listed tile's OSM clip. An equivalent fix landed on main while this task was building; this branch keeps the matching README correction. The successful retry reused every layer and completed assembly. Main subsequently added a fast facade pass to `tile-add`; that pass was not part of this sandbox build. The tile has the published buildings layer and its existing facade bindings; no fast facade approval was written to the data root.

| Stage | First build wall time | Successful retry wall time |
| --- | ---: | ---: |
| Inputs and seed | No missing data | 0.4 s |
| Ground | 1,083.1 s | 0.1 s cached |
| Buildings | 1,330.2 s | 2.6 s cached |
| Structures | 61.4 s | 0.5 s cached |
| Facades | 16.9 s | 0.4 s cached |
| Bridges | 34.4 s | 0.3 s cached |
| Roads | 3.8 s | 0.0 s cached |
| Outer buildings | 237.7 s | 2.2 s cached |
| Ground textures | 1,575.8 s | 4.9 s cached |
| Entire refresh | 2,975.9 s | 11.7 s |
| Assembly | failed at address publication | 3,546.1 s |
| Successful retry total | — | 3,558.1 s |

The host was heavily loaded during both runs. Assembly repacked facade density on existing tiles to satisfy their wire budgets; this dominates the retry time.

## Checks

- `node tools/validator/src/cli.ts --checks format,seams` on the shipped `t0_1_0` and `t0_2_0` collision directories: **format PASS, seams PASS** (60 cells checked). Report: `$TURKU3D_WORK/reports/content-tile-t0_2_0-1027-2220/seams.json`.
- Road records on the shared x=500 m edge: 18 endpoints from each tile, exactly equal, with no unmatched endpoint.
- Sandbox Vite dev server on port 5211: Chrome requested `visual/0/t0_2_0.glb` and captured an aerial view after the scene was ready on **WebGPU and WebGL2**. Screenshots: `$TURKU3D_WORK/reports/content-tile-t0_2_0-1027-2220/{webgpu,webgl}-aerial.png`. These are game renders, not street imagery.
- A WebGPU car drove from x=494 m in adjacent tile `t0_1_0` to x=510.27 m in `t0_2_0`, with collision cell `c_8_2` resident and no game errors. The start was moved to the seam in the sandbox test; this was a short streaming and collision crossing, not a continuous drive from the office. Evidence: `$TURKU3D_WORK/reports/content-tile-t0_2_0-1027-2220/webgpu-drive.{png,json}`. The browser budget was 5 minutes; 4.6 minutes were used.

## Data coverage

Required ground, buildings and roads were present. The optional `kanta_Sahkoverkonosa.parquet` prop source remains restricted; the props producer reported it missing, and omitted several unsupported traffic-sign codes. Neither blocked the tile. No raw data was deleted or copied into the repository.
