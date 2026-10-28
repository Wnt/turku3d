## Result: DONE

Built and listed `t0_2_3` (E 23460000–23460250, N 6703500–6703750). The per-tile store entry is `95a30e8df23c36acb02b303ff0c6ac3e79fef6aea5fb34bb84b7e388c059f814`.

## What changed

The tile has ground, buildings, structures, props, vegetation and parked-car visual layers, road records and 16 collision cells. The quality ledger counts 87 buildings, 197 trees, 37 parked cars and 198 fast street-facade maps. The layer records contain 115 road segments, 64 structures and no bridge deck, consistent with the 2025 source ortho's dense housing and roads without a water crossing. Input coverage was complete: the `tile-t0_2_3` input area had 1,214,314,158 registered bytes and required no fetch.

The first build stopped after ground when the buildings producer sampled roof colour outside the fixed `work-3x3` ortho extent. It now reads origin-aligned ortho chunks from every listed tile's source area. The second build reached outer scenery and stopped on a source roof entirely below its building base. The successful tile build skipped that empty outer shell; a subsequent main landing fixed the source extrusion to retain the building as a flat closed shell, so this branch uses that shared fix. Both failed attempts left reusable sandbox products; no raw data was removed.

The successful `tile-add` took 2,094.276 s: inputs 0.085 s, outer ring 2.167 s, seed 0.0 s, refresh 208.990 s, fast facades 435.109 s, assembly 1,445.145 s and tile-store put 2.778 s. Refresh rebuilt buildings (84.695 s), outer buildings (18.403 s), props (35.546 s), vegetation (53.509 s) and parked cars (11.629 s). Ground textures were cached in 6.806 s; ground, structures, facades, bridges, roads, landmarks and bridge textures were also cached. The first two failed attempts took 915.3 s and 1,029.4 s respectively, including their earlier build work and host-slot waits.

The shipped L0 visual is 5,278,896 bytes (4,068,439 Brotli bytes), with 3,528,350 ground-texture bytes. The total wire size is 7,596,789 bytes, below the 8,000,000-byte cap. Shipped collision seams have zero mismatches. Road records share exactly the same eight endpoints at the north edge with `t0_2_2` and 13 endpoints at the east edge with `t0_3_3`, including road material IDs.

Headless software renders of the generated layer GLBs are retained at `$TURKU3D_WORK/qa/t0_2_3/software-top.png` and `software-oblique.png`, with the 2025 ortho at `source-ortho2025.png`. The top-down overlay shows building roofs following the source footprints and parked cars along the road and parking areas. The oblique view shows the dense housing, walls and tree rows without an obvious duplicated or floating object. A placement check found zero of 197 tree, 37 parked-car and 134 prop centres inside a building footprint. The tile has no source water crossing or generated bridge deck. The shared Chrome admission remained occupied by an integration advisory run, so these geometry renders used a lightweight headless software projection of the real generated meshes and instance records.

## How to verify

```sh
source ../sandbox.env
cat "$TURKU3D_WORK/reports/tile-add/t0_2_3.json"
bash pipeline/env/run.sh python -c 'import os; from pathlib import Path; from turku3d.package.seams import verify_seams; print(verify_seams(Path(os.environ["TURKU3D_WORK"])/"dist/tiles"))'
bash pipeline/env/run.sh python -m turku3d.region quality --json
cat "$TURKU3D_DATA/playable-tiles.json"
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_assemble_outer.py pipeline/tests/test_coarse_roofs.py
ls "$TURKU3D_WORK/qa/t0_2_3/"{software-top,software-oblique,source-ortho2025}.png
```

## Gate output

The focused outer/roof suite passed: 8 tests in 38.07 s under host load before the main rebase. `scripts/dev/gate --fast --base main` was GREEN in 10.847 s: env-sync 9.074 s, policy 0.762 s and Python lint 0.581 s passed; other stages were cached or skipped by fast-tier/path rules.

## Follow-ups

Jonni will assess play feel in the running game. The quality ledger marks the tile `unvalidated`; the deep validator and drive test are outside this brief. The fast facade description step encountered rate limits on 43 of 114 calls, but the procedural render/publish steps completed all 200 planned rough maps.
