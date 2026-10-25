# W1-02 structures implementation and acceptance

Status: **BLOCKED on the W1-01 ground layer**, 2026-10-25.
Branch: `wt/w1-02-structures`. The implementation and synthetic acceptance tests
are committed; a complete real office-AOI structures layer is not yet produced.

## Implemented

- City survey/OSM gap adapters and lidar-backed stair/wall/fence measurements.
- Grouped stair flights, smooth solid slopes, query-only visual treads, and cheeks.
- Closed mitered retaining walls, fence/railing profiles, preserved ground ramps,
  and ground-classified quay stone walls with railing collision.
- Shared material classes, deterministic float64 seam clipping, world-local GLBs
  with material keys, hash-checked manifests, caching and JSON/Markdown reports.
- Explicit blocking diagnostics for absent ground/quay classification, missing
  lidar, unsupported stair geometry, landing/contact errors and uncovered ramps.

The [package README](../../pipeline/turku3d/structures/README.md) documents exact
commands, output schemas, material keys and the proposed W1-01 quay handoff.
The quay profile interface still needs confirmation against W1-01's landed output.
No W1-01 code or shared raw input was changed.

## Verification

```sh
make -j2 structures-test
make structures-build
```

The synthetic suite covers flight grouping, independent lidar samples, exact ramp
contacts, ground/OSM adapters, actual referenced collision seam bits, four-tile
corners, surface-area conservation, NPZ/GLB contracts and damaged-cache recovery.
Rapier uses the generated meshes: 10/25/39/40-degree stairs climb and descend;
0.31/0.35/1.2 m walls, fences and quays block the capsule; ramps climb and descend;
foot IK hits query-only treads; a CCD 1,500 kg car proxy stops at the quay at 10 m/s.
The car proxy is not a substitute for W1-05's assembled raycast-vehicle checks.

## Gate output

`make -j2 structures-test`: **PASS**, 9.33 seconds wall time (5.03 s user,
1.18 s system). Python: **17 passed** in 5.27 s. Rapier: **12 passed, 0 failed**
in 4.77 s. Validator TypeScript typecheck: **PASS**. `git diff --cached --check`:
**PASS**. `scripts/dev/gate` is not present at this branch base, so the area target
is the required gate. Full captured output remains at
`$TURKU3D_WORK/reports/structures/gate.txt`.

## Real input run

`make structures-build` exits 2 (the module exits 1) after the read-only survey
inventory, in **2.991 seconds**. Its blocking output is:

```text
W1-01 ground layer is required: /Users/wnt/turku3d/wt/w1-02-structures/work/build/layers/ground/MANIFEST.json
make: *** [structures-build] Error 1
```

The [JSON report](structures-metrics.json) records the full source counts. Runtime
reports live at `$TURKU3D_WORK/reports/structures/{metrics.json,report.md}`. Missing
inputs do not publish a structures manifest or pretend to satisfy lidar accuracy.

## Remaining acceptance

1. Stage the completed W1-01 ground layer in the sandbox and confirm its quay
   classifications and ramp material coverage match the documented consumer.
2. Run `make structures-build`; resolve every object-level issue and inspect the
   real flight/wall height measurements, default fence heights and ground joins.
3. Assemble with W1-04, bind the structure material textures, and run W1-05's
   character routes and full car checks across the real office block.
