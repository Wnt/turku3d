# Playable tile `t0_-1_2`

## Result: DONE

The tile at E/N `[23459250, 6703750, 23459500, 6704000]` was built, put in the per-tile store, and added to the shared playable list. The input area was the existing `work-3x3` cache: zero missing files, zero fetched bytes, and no required data-coverage blocker.

## What changed

The tile has ground, buildings, structures, props, vegetation, and parked-car visual layers, road records, and 16 collision cells. Its layer records contain 81 roads, 56 structures (11 stairs, 20 walls, 25 fences), 111 props, 145 trees, one parked car, and no bridge deck, matching the source ortho's shoreline without a crossing. The building visual has 16 object roots. The fast facade pass produced 125 maps (114 register-based, nine building-derived, two reference-based); 121 walls had no usable photo reference. This limits facade detail, not geometry coverage.

| Stage | Wall time |
| --- | ---: |
| Input check | 0.2 s |
| Massing ring check | 5.0 s |
| Seed published products | 4.4 s |
| Refresh | 1,872.9 s |
| Fast facades | 615.4 s |
| Assemble | 1,542.1 s |
| Put in tile store | 5.9 s |
| **Build total** | **4,046.0 s** |

Within refresh, ground took 151.3 s, buildings 435.9 s, structures 353.6 s, bridges 36.4 s, props 40.1 s, vegetation 104.8 s, parked traffic 32.6 s, and ground textures 930.5 s. The host-slot queue added about 627 s before the build began.

The assembled visual GLB is 5,269,008 bytes (4,169,269 Brotli). Ground textures add 3,382,426 bytes, for **7,551,695 wire bytes** under the 8,000,000-byte cap. It has 269,392 visual triangles. Decoded packaged collision seams have zero mismatches. The eight ground collision-cell joins with west `t0_-2_2` and north `t0_-1_1` are bitwise equal, and road endpoint sets match exactly on both boundaries (five west, six north).

I compared headless top-down and oblique software renders of the generated layer GLBs with the same-bbox 2025 source ortho. Building footprints, shoreline, road alignments, park paths, and small objects align at tile scale. No duplicate deck, floating shell, water crossing, or placement inside a building was apparent. A polygon check found zero of 111 props, 145 trees, and one parked car centred inside building ground footprints. The riverfront construction site has a different roof shape in the 2025 ortho than the older source-derived building shell; this is a source-epoch difference. The QA images stay under `$TURKU3D_WORK/qa/t0_-1_2/` and are not committed.

## How to verify

```sh
source ../sandbox.env
cat "$TURKU3D_WORK/reports/tile-add/t0_-1_2.json"
bash pipeline/env/run.sh python -m turku3d.region quality --json
bash pipeline/env/run.sh python - <<'PY'
import os
from pathlib import Path
from turku3d.package.seams import verify_seams
print(verify_seams(Path(os.environ['TURKU3D_WORK']) / 'dist/tiles'))
PY
ls "$TURKU3D_WORK/qa/t0_-1_2/"{source-ortho2025,top-layers,oblique-layers}.png
scripts/dev/gate --fast --base main
```

## Gate output

`scripts/dev/gate --fast --base main`: **GREEN**, 2.0–2.3 s cached on the report (20.817 s initial uncached run). `env-sync`, generated materials, policy, and Chrome launch policy passed; unchanged and deep stages were skipped. Packaged seam verification returned `[]`.

## Follow-ups

Jonni's play session will report any walking or driving defects. The riverfront construction block should be revisited when current building geometry is available.
