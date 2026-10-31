# Status snapshots and verification notes

> **Historical measurements, not a live status dashboard.** The data inventory, tile counts, build measurements and known gaps below were recorded during **25–28 October 2026**. They are valuable evidence of work completed at that time, but they do **not** define the current Pages release, public service availability, or an obligation to prebuild four starting tiles. For current entry points see [the project README](../README.md), [player guide](PLAYING.md), [development guide](DEVELOPING.md), and [documentation index](README.md).

## Code state inspected 1 November 2026

- The published client is a multiplayer-first public variant (`VITE_PUBLIC_RELEASE=1`); local builds expose solo and save/load menus.
- The three major active components are the WebGPU/WebGL2 browser game, Node multiplayer service and on-demand city generator. Pages carries the browser app, and external services serve world content and presence.
- Real-world source inputs and generated world data are outside Git. For local browser changes use `make game-dev` and `?scene=dev`; see [development instructions](DEVELOPING.md).
- No live uptime, benchmark or current city coverage measurement is asserted by this document. Check the current deployment/services and newly recorded reports for those claims.

## Dated October 2026 data and QA inventory

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

## Publication region and tile quality (2026-10-27)

```sh
bash pipeline/env/run.sh python -m turku3d.region quality   # per-tile quality table (data root ledger)
scripts/dev/publish-pages --plan                              # which listed tiles the next Pages release ships
```

The live release `3544a2c` lists the four core tiles; all have orthophoto + splat ground and at most 7.56 MB on
the wire. Street-facing wall area with generated-style facade maps: t0_0_0 23 %, t0_0_1 39 %, t0_1_0 32 %,
t0_1_1 0 %. With the default region (1 km around Läntinen Rantakatu 15, at least 20 % facade coverage) the next
Pages release ships t0_0_0, t0_0_1 and t0_1_0 playable and keeps t0_1_1 as visual-only scenery.
