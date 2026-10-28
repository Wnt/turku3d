# Playable tile `t0_-2_2`

## Result: DONE

The tile at E/N `[23459000, 6703750, 23459250, 6704000]` is built, stored and listed in the shared playable tile inventory. The existing adjacency rule initially rejected this telemetry-requested, disconnected tile. The planner now accepts any L0 tile with published lidar coverage; it still rejects non-L0 IDs, duplicates and missing lidar coverage.

## What changed

The tile has ground, buildings, structures, bridges, props, vegetation and parked-car visual layers, plus roads and collision geometry. Source records include 324 building shells, 17 stair, 31 wall and 35 fence objects, four bridge crossings (Martinsilta, Myllysilta and two small OSM crossings), 126 road records, 214 props, 109 trees and 10 parked cars. The 41 fast facade maps include 30 reference-based, eight building-derived and three register-based maps.

The source area fetched 822 MB of missing raw inputs (48 files). Both 2022 and 2025 orthophotos cover the area fully; the optional drone layer covers 1.9%. No required data coverage blocked the tile.

| Stage | Wall time |
| --- | ---: |
| Input fetch/check | 271.8 s |
| Massing ring check | 3.9 s |
| Seed published products | 2.9 s |
| Refresh | 827.7 s |
| Fast facades | 180.7 s |
| Assemble | 151.9 s |
| Put in tile store | 0.8 s |
| **Total** | **1439.6 s** |

Within refresh, ground took 111.0 s, buildings 117.1 s, structures 153.2 s, bridges 18.1 s, vegetation 36.7 s and ground textures 444.1 s. The other layer times are in `$TURKU3D_WORK/reports/tile-add/t0_-2_2.json`.

The assembled visual tile is 4,724,772 bytes (3,487,183 Brotli); its reported wire size is **6,741,562 bytes**, below the 8 MB cap. It contains 209,587 visual triangles. The packaged collision seam verifier found zero mismatches. There is no listed edge neighbour, so roads and ground cannot yet be compared to a separately built adjacent playable tile.

I compared headless top-down and two oblique game renders with a 2022 true-ortho crop of the same bbox. The two road bridges, banks, tree rows, streets and building massing align at this scale; no obvious duplicated deck, floating object or object cutting through water/buildings was visible. The orthophoto crop and renders remain under `$TURKU3D_WORK/qa/t0_-2_2/` and are not committed.

## How to verify

```sh
cat "$TURKU3D_WORK/reports/tile-add/t0_-2_2.json"
bash pipeline/env/run.sh python -m turku3d.region quality --json
bash pipeline/env/run.sh python - <<'PY'
import os
from pathlib import Path
from turku3d.package.seams import verify_seams
print(verify_seams(Path(os.environ['TURKU3D_WORK']) / 'dist/tiles'))
PY
scripts/dev/gate --fast --base main
```

## Gate output

`scripts/dev/gate --fast --base main`: **GREEN**, 16.906 s total. `env-sync`, generated-materials, policy and Python lint passed; unrelated and deep stages were skipped by the fast gate. The focused planner test passed (1 test, 1.81 s), and packaged collision seams passed with zero mismatches. Headless Chrome rendered all three tile views successfully.

## Follow-ups

Jonni's play session will report any driving or walking defects. A separately built edge neighbour will allow a direct cross-tile ground and road seam check.
