# Status and how to run everything

Updated 2026-10-25 (W1-04 layer assembler). Run from the current worktree.
The native osx-arm64 toolchain is shared under `~/turku3d/tools` and `~/turku3d/envs/pipeline`.
Inputs live under `$TURKU3D_DATA/raw`; build products under `$TURKU3D_WORK`.

```sh
make pipeline-bootstrap && make pipeline-versions
make fetch-aoi AREA=work-3x3              # initial shared input preparation / explicit refresh
make fetch-checksums AREA=work-3x3
# Publish the Wave 1 layer manifests under $TURKU3D_WORK/build/layers first.
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-show                           # sandbox port base + 1
make qa-refs                             # regenerate the six heading-corrected references
```

`make tile` runs in order even under `make -j`: published layer manifest/hash checks → local assembly
→ decoded precision, budget and bitwise seam checks → Rapier validation → Chrome WebGPU/WebGL2 e2e.
It writes `$TURKU3D_WORK/dist/tiles`; validation reports go to `$TURKU3D_WORK/reports`.
The e2e suite checks the textured render, walking, QA projection/ID passes, and a route across
all four L0 tiles. Screenshots are in `$TURKU3D_WORK/reports/screenshots/tile-*`.

## Data readiness

- `work-3x3`: E 23459000–23460500, N 6703500–6705000 (1.5 × 1.5 km).
- All nine existing point-cloud LAZ files were checksum-verified and registered without downloading.
  Nine maanpinta files provide the coarse ground heights.
- All configured WFS layers fetched; two known restricted layers return expected HTTP 401:
  `kanta:LuokittelematonViiva` and `kanta:Sahkoverkonosa`.
- 2022 true ortho and 2025 ortho: 64/64 chunks each, 100% coverage.
- Drone: 209/900 chunks contain imagery, 17.5% coverage; no fetch errors.
- OSM: dated Finland snapshot 2026-10-24, working-area clip and GeoParquet (33,312 rows).
- Small coverage/provenance reports and checksums are committed in `data-manifests/checksums`.
- The old Mac data mirror was consolidated: 319 files / 245,925,086 bytes verified, metadata
  paths relocated, retired root removed. See `data-manifests/checksums/CONSOLIDATION.json`.

## Playable entry point and playtest evidence

```sh
make play                      # cached AOI + game; prints the sandbox URL
make playtest                  # virtual Xbox on WebGPU and WebGL2
open "${TURKU3D_WORK:-$HOME/turku3d/data}/reports/playtest/index.html"
make playtest-test              # typecheck + cache/report tests
```

The launcher verifies/reuses the four AOI tiles and the production game. The start screen
shows controller status and Xbox bindings. The baseline covers real-AOI terrain seams and
a short office drive, plus synthetic curb/stair/controller routes. Videos, screenshots and
measurements are retained in the report, with earlier runs archived by commit and timestamp.
**Real-AOI follow-up after the layers land:** the real street loop, stairs, five building shells,
curb strike and quay-wall routes require Wave 1 geometry and subsequent route calibration.
See [playtest coverage](../game/tests/e2e-playtest/README.md).

## Verification

```sh
make pipeline-test                       # full native environment, offline synthetic fixtures
make col-test                            # Python/TS codec round trips + Rapier fixtures
make foundation-test qa-test-py game-dev-tools-test
make tile-test
```

The native lock includes PDAL, GDAL, scipy, rasterio, geopandas, pyarrow, laspy/lazrs, shapely,
pyproj, osmium and pytest-xdist. Roofer 1.0.0 and KTX-Software 4.4.2 run natively on ARM64;
Node tools and their transitive dependencies are locked. See `pipeline/env/README.md`.
Measurements and the final acceptance run are recorded in [local-pipeline.md](reports/local-pipeline.md).

## Remaining Phase 1 work

Production packaging now consumes Wave 1 layers; the Phase 0 builder is test-fixture-only.
The previously shipped ground was the Phase 0 heightfield, with the river interpolated across.
Curbs, structures, building shells and water belong to the Wave 1 layers. Accordingly the
real-data validator skips buildings/curbs/stairs, while those checks are exercised on fixtures.
The four-tile seams are bitwise identical; `AOI_BORDER` is set only on the exterior of the
Phase 1 visual envelope. WMS blank results expire after one hour instead of persisting forever.

Rapier 0.21 trimesh ray hits still need `game/src/physics/triLocator.ts` for triangle material
lookup. The reference camera poses have medium confidence and are not lidar-registered.

## Buildings layer (W1-03)

The current playable-tile inventory and skyline extension are measured in
[the W1-20b report](reports/w1-20b-all-buildings.md). The W1-03 numbers below
describe the earlier office-block-plus-margin producer scope.

The buildings layer and [facade contract](specs/facades.md) cover all 81 source footprints
with validated closed shells, including 19 explicit footprint fallbacks. Exact shell-distance
scoring fixes three of the five former residual failures; 63/81 now meet the unchanged
10 cm / 25 cm limits. Two retained roofer parts and 16 extrusions remain accuracy-degraded.
All 137 exported seam comparisons pass, and the maximum clipped shell is 1,800 triangles.
Per-building collision provenance allows nested parts to be tested independently.
See [the measured report](reports/buildings.md) for scope, fallback evidence and verification.
The 19-stage gate passed in 261.019 s; the final coverage-policy gate passed in 5.394 s.
This layer is not yet assembled into the game.
