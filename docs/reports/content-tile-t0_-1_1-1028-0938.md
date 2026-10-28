# Playable tile `t0_-1_1`

## Result: DONE

The tile at E/N `[23459250, 6704000, 23459500, 6704250]` was built, stored, and added to the shared playable-tile list. It joins the built `t0_0_1` edge neighbour.

## What changed

The tile contains 30 buildings, 126 road records, 99 structure objects (13 stairs, 18 walls, 68 fences), three small bridge crossings, 161 props, 88 trees, and 36 parked cars. Its visual layers are ground, buildings, structures, bridges, props, vegetation, and parked traffic; roads and all required collision cells are present. The 2022 ortho covers the tile, and no required raw inputs were missing.

| Stage | Wall time |
| --- | ---: |
| Input check | 0.4 s |
| Massing ring check | 6.3 s |
| Seed published products | 4.3 s |
| Refresh | 1,555.5 s |
| Fast facades | 346.3 s |
| Assemble | 1,140.6 s |
| Put in tile store | 2.6 s |
| **Total** | **3,056.0 s** |

Within refresh, ground took 261.1 s, buildings 302.6 s, structures 229.4 s, bridges 82.5 s, vegetation 123.5 s, and ground textures 759.1 s. The assembled visual GLB is 4,689,528 bytes (3,507,384 Brotli); total reported wire size is **6,503,953 bytes**, below the 8 MB cap. It has 156,900 visual triangles. Packaged collision seam verification found zero mismatches. The top-down render shows the road and ground continuing across the `t0_0_1` boundary without a visible break.

I inspected headless top-down and southwest/northeast oblique renders against a crop of the 2022 true ortho. Buildings, road alignment, river edge, and tree rows agree at tile scale. The three short OSM bridge crossings have one generated deck each; I saw no obvious stacked deck, floating object, or barrier crossing the river. The source crop and renders are retained at `$TURKU3D_WORK/qa/t0_-1_1/` and are not committed.

The fast facade description service returned HTTP 429 for 58 of 125 calls. The fallback rendered all 134 planned maps: 54 reference-based, 54 building-derived, and 26 register-based. Listing published 127 maps into the shared approved inventory; seven wall IDs were outside that inventory. This limited facade detail, but did not block geometry or data coverage.

## How to verify

```sh
cat "$TURKU3D_WORK/reports/tile-add/t0_-1_1.json"
bash pipeline/env/run.sh python - <<'PY'
import os
from pathlib import Path
from turku3d.package.seams import verify_seams
print(verify_seams(Path(os.environ['TURKU3D_WORK']) / 'dist/tiles'))
PY
scripts/dev/gate --fast --base main
```

## Gate output

`scripts/dev/gate --fast --base main`: **GREEN**, 10.409 s total. `env-sync` passed in 9.359 s, policy in 0.397 s, and generated materials were cached green. Unchanged and deep stages were skipped.

## Follow-ups

Jonni's play session will identify any driving or walking defects. The 58 facade descriptions that hit HTTP 429 can be retried when the service accepts calls.
