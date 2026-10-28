# Playable tile t0_7_7

## Result: DONE

`t0_7_7` (E 23461250–23461500, N 6702500–6702750) is built, in the per-tile store, and listed in `$TURKU3D_DATA/playable-tiles.json`. Its final per-tile store key is `a7f680cfd618e5fa90bf2b1a2348d34c5681bc752197cb0698ffe93709475a49`.

## What changed

The initial `make tile-add T=t0_7_7` fetched 50 missing input files (1,512,813,456 bytes) and assembled a tile without buildings. The building producer read only `work-3x3` WFS and OSM even for distant listed tiles. It now combines each listed tile's source-area WFS features, deduplicates overlapping records, and attaches OSM data from the building owner's area. The merged cache stays in this sandbox. A focused synthetic test covers duplicate features and distinct surfaces. No raw data was deleted.

The corrected tile has 15 closed building shells, 38 rough facade maps, ground, 33 road records, 18 structures (3 stairs, 5 walls, 10 fences), 52 props, 182 trees and 13 parked cars. Source and output both have zero bridge crossings. Three further surveyed stair objects were left out by the existing lidar landing checks. The restricted `kanta_Sahkoverkonosa` register remains unavailable for props; other fetched input coverage was complete. Fast facade descriptions succeeded for 5 of 20 jobs; 15 were rate-limited, but the procedural render/publish step produced all 38 planned maps.

The final L0 visual is 5,103,472 bytes (4,272,410 Brotli bytes). Ground textures are 3,509,043 bytes; total wire size is **7,781,453 bytes**, below the 8,000,000-byte cap. Packaged collision seams have zero mismatches. No other listed full tile shares an edge with `t0_7_7`; its internal collision seams pass, and the road in the top-down render follows the source across the tile. No prop, tree or parked-car centre lies inside any of its 15 building footprints.

I compared headless top-down and two oblique game renders of `t0_7_7` with a 2025 true-ortho crop. I also compared top-down and oblique renders of affected listed tile `t0_2_3` with its source crop. Building roofs, roads and tree areas agree at tile scale; I saw no plainly duplicated or floating geometry or feature crossing water or a building. The generated images and source crops remain under `$TURKU3D_WORK/qa/{t0_7_7,t0_2_3}/` and are not committed. `make gmaps-compare` was attempted at the tile centre; Google consent setup timed out before capture. The tool's consumer-camera calibration is explicitly unverified, so no pose-aligned claim is made.

The first build spent 181.636 s on inputs, 3.990 s on ring inputs, 2.617 s seeding, 1,290.645 s refreshing, 29.982 s on fast facades, 1,228.127 s assembling and 0.726 s putting the tile: **2,737.735 s** of build stages. Within refresh, ground took 126.519 s, buildings 118.614 s, structures 333.819 s, vegetation 84.643 s and ground textures 710.219 s. The corrected cached rebuild spent 0.183 s on inputs, 21.952 s on ring inputs, 573.608 s refreshing, 205.737 s on fast facades, 577.826 s assembling and 0.833 s putting the tile: **1,380.155 s**. Buildings took 487.658 s in that refresh. Host-slot waits are excluded from those stage timings; command wall time was 3,674.1 s and 1,530.0 s respectively.

## How to verify

```sh
source ../sandbox.env
cat "$TURKU3D_WORK/reports/tile-add/t0_7_7.json"
bash pipeline/env/run.sh python -m turku3d.region list-tile t0_7_7
bash pipeline/env/run.sh python -c 'import os; from pathlib import Path; from turku3d.package.seams import verify_seams; print(verify_seams(Path(os.environ["TURKU3D_WORK"])/"dist/tiles"))'
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_buildings.py -k merge_overlapping_wfs_areas_keeps_distinct_surfaces
ls "$TURKU3D_WORK/qa/t0_7_7/renders/"*.png "$TURKU3D_WORK/qa/t0_2_3/renders/"*.png
```

## Gate output

`scripts/dev/gate --fast --base main`: **GREEN in 152.404 s**. Env sync took 105.493 s, Python lint 5.458 s, policy 9.613 s and Chrome launch policy 20.601 s; generated materials were cached. Deep and browser stages were skipped as prescribed. The focused WFS union fixture passed (1 test in 11.44 s); Ruff lint and format passed.

## Follow-ups

Jonni will play the tile and report traversal or driving issues. Retry the 15 rate-limited facade descriptions when model capacity returns. Google comparison needs its consent setup and camera calibration repaired before it can provide aligned evidence.
