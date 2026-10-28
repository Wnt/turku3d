## Result: DONE

Built and listed `t0_3_3` (E/N 23460250–23460500, 6703500–6703750). The tile-store entry is `ee3edc54cb954a27207dbb1afd5df71c3a2a4b2a728f0615e3ddc447746b0094`.

## What changed

The tile has ground, buildings, structures, props, vegetation and parked-car visual layers, plus road records and 16 collision cells. Its quality ledger counts 72 buildings, 270 trees and 5 parked cars. The fast facade pass published 172 maps; 27 used a wall reference, 35 used another wall of the building and 110 used register descriptions. It found no usable reference for 67 walls. No required raw input was missing; the tile input area already held 1,134,409,342 registered bytes.

The first `tile-add` run built the layers but stopped after the facade reference command printed an `aiohttp` cleanup warning following its JSON result. The tile-add wrapper now reads the last JSON object despite trailing diagnostics. The cached retry completed. The first attempt took 3,031.7 s before stopping; its ground, buildings, structures and ground-texture producers took 156.8 s, 589.6 s, 771.6 s and 936.6 s respectively. Under host contention, the successful retry took 544.6 s of pipeline time (959.5 s including the build-slot queue): inputs 0.2 s, ring 4.5 s, seed 0.0 s, refresh 45.7 s, fast facades 274.8 s, assembly 218.5 s and tile-store put 0.9 s. The retry's layer producers were cached.

The L0 visual is 4,989,864 bytes (3,948,570 Brotli bytes), with 3,565,699 ground-texture bytes. Total wire size is 7,514,269 bytes, below the 8,000,000-byte cap.

## How to verify

```sh
cat "$TURKU3D_WORK/reports/tile-add/t0_3_3.json"
bash pipeline/env/run.sh python -m turku3d.region quality --json
bash pipeline/env/run.sh python -c 'import os; from pathlib import Path; from turku3d.package.seams import verify_seams; print(verify_seams(Path(os.environ["TURKU3D_WORK"])/"dist/tiles"))'
cat "$TURKU3D_DATA/playable-tiles.json"
```

Shipped collision seam validation found zero mismatches. The only listed neighbour, `t0_2_2`, meets this tile at a corner. The four collision cells around that corner all contain the identical ground vertex `(750, 16.723539352416992, 750)` in engine coordinates. Neither tile's road records have an endpoint at that corner, so there is no road edge crossing to compare. The roads producer wrote 78 records for `t0_3_3`.

## Gate output

The focused tile-add unit suite passed: 9 tests in 20.02 s. `scripts/dev/gate --fast --base main` was GREEN in 26.713 s: env-sync 25.468 s, policy 0.341 s and Python lint 0.575 s passed; the remaining stages were cached or skipped by fast-tier/path rules.

## Follow-ups

Jonni will assess play feel and visual issues in the running game. The tile's quality ledger currently marks its validator status `unvalidated`; this brief calls for the wire and seam checks above, not the deep validator or a drive test.
