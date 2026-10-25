# W1-05 validator acceptance run — 2026-10-25

The validator now checks bitwise collision seams and their edge partitions,
runs the game's shared W1-07 `Vehicle` implementation, accepts independent
layer footprints/curb lines/stair lists, partitions physics checks across
collision-tile workers with neighboring halos, and emits JSON plus an offline
orthophoto failure map. `make tile-validate` requires the Phase 1 feature checks;
a ground-only build cannot pass by skipping them.

## Original pre-rebase regression gate

* `make col-test-ts`: typecheck and **33/33 tests passed**. Final Node test time
  82.123 s during shared-host contention (previous serial-fixture run: 163.890 s).
  Independent fixture scenes now use a bounded four-worker pool, without reducing
  walker counts or changing any expected failure. The usual 5–20 s gate target
  could not be established under this host load.
* `make game-test`: **44/44 tests passed**, 25.24 s.
* Game TypeScript check passed; the vehicle-specific 20-test subset also passed.
* `git diff --check`: passed.
* At the original run, `AGENTS.md` and `scripts/dev/gate` were not yet present;
  these were the area checks required by the pre-Wave-0 brief.

## Current real build

Inputs were copied, without modification, from `~/turku3d/data/dist/tiles` into
`$TURKU3D_WORK/dist/tiles`. They are the current Phase 0 heightfield build, not
Wave 1 layered collision. No Wave 1 layer outputs were present in this sandbox.
The complete four-visual-tile envelope contains 64 collision tiles.

| Check | Result |
|---|---|
| Format | PASS |
| Seams | PASS: all 112 shared edges |
| Holes | FAIL: 1 miss among 495,616 rays |
| Below ground | PASS: 9,187 drops, no below-ground samples |
| Buildings, curbs, stairs, cars | FAIL: required coverage has no testable layer inputs |

The missed ray is in `c_2_0` at engine coordinates
`x=147.35090878073146, z=26.24999842508378`. It reproduces with the same seed
in a separate six-tile neighborhood run. This is a **Rapier query miss**;
its underlying cause (geometry versus query precision) has not been determined.
No jitter, tolerance, ray density or success threshold was relaxed to remove it.

Full run: **54,418 ms wall time**, four workers; **33,661.587 ms summed thread
CPU time**. The earlier run took 207,535 ms during extreme machine contention
(load average >600). The second run meets the <60 s target for the current AOI.
Performance on the denser, complete Wave 1 layer build still requires measurement.

Reports in this sandbox:

* `$TURKU3D_WORK/reports/validate-aoi.json`
* `$TURKU3D_WORK/reports/validate-aoi.html` (embedded 2022 orthophoto)
* `$TURKU3D_WORK/reports/hole-recheck.json`

The HTML was opened in headless Chrome using the sandbox's browser profile;
the orthophoto, tile grid and failed-ray marker were visually inspected.

## Reproduce

From the repository root with `sandbox.env` exported:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make col-test-ts \
  PIPELINE_PY=$HOME/turku3d/envs/pipeline/bin/python \
  COL_RT_DIR=$TURKU3D_WORK/roundtrip
make game-test
bash pipeline/env/run.sh python tools/validator/ortho.py \
  --root "$TURKU3D_DATA/raw/ortho/ortho2022to/work-3x3" \
  --bounds 0 0 500 500 --out "$TURKU3D_WORK/reports/ortho.png"
make tile-validate T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" \
  PIPELINE_PY=$HOME/turku3d/envs/pipeline/bin/python \
  VALIDATOR_ARGS="--ortho $TURKU3D_WORK/reports/ortho.png --ortho-bounds 0,0,500,500"
```

The final command is expected to exit non-zero on these inputs. Its report says
`format=pass, seams=pass, holes=fail, belowGround=pass`, with missing required
building/curb/stair/car coverage. This is a data acceptance failure, not a waived
validator regression. The validator CLI returns 1; Make propagates failure.

## Follow-ups for the layer owners

* W1-01 / W1-04: investigate the reproducible `c_2_0` ground ray miss above.
* W1-01, W1-02, W1-03 / W1-04: publish validation sidecars alongside the layer
  manifests and assemble the real AOI. The exact schemas and commands are in
  `tools/validator/README.md`. Rerun all checks and the timing measurement.

## Wave 0 / W1-07 integration

Rebased onto main `04037fc`, preserving Wave 0 policy, coordination tooling, native pipeline, rendering, input,
vehicle and visual QA changes. The earlier `GameVehicle` extraction was removed;
the validator imports W1-07's `Vehicle` and shares the game's `tileCurbs.ts`
registration adapter. The validator context and orchestration were split to meet
the 600-line source budget without exceptions. Gate routing includes the new
Python helpers, acceptance suite and shared vehicle dependencies.

All nine 7/12/15 cm × 3/6/10 m/s routes passed through the validator:
**pitch 2.48–8.77°, speed loss 7.32–18.26%**. ARCHITECTURE 1.14 limits are now
mandatory, including strike events and successful crossing. The 26/30/40 cm steps
block at 2 m/s under continued throttle; the 30 m/s thin-wall test passes.
Deliberately missing curb registration fails response/blocking checks, and a
12 cm decoded `.col` platform passes all twelve face/speed runs through the same
adapter used by `PhysicsWorld`.

The W1-07 follow-up from the original run is resolved. Complete real-layer inputs
and investigation of the recorded ground ray miss remain the layer-owner follow-ups.

Two existing tooling performance failures were fixed without changing tests or
limits: `wt rm` now rejects dirty/unmerged work before scanning host processes,
and `here` obtains repository/branch in one Git call and imports the task runner
only when task records exist. The live-process removal guard remains mandatory.

## Rebased AOI rerun

The shared W1-07 validator rerun took **43,520 ms wall time** and
**28,606.650 ms summed thread CPU time**, four workers / 64 collision tiles.
It reproduced exactly the recorded results: 112 seams pass, 1 miss among 495,616
rays at the same `c_2_0` coordinates, and 9,187 drops pass. Required building,
curb, stair and car coverage correctly fails on the available ground-only build.
The validator exits 1 and `make tile-validate` exits 2; no data failures are waived.

The JSON and embedded-ortho HTML are in
`$TURKU3D_WORK/reports/rebased/validate-aoi.{json,html}`. Reproduce with:

```sh
make tile-validate T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" \
  PIPELINE_PY="$HOME/turku3d/envs/pipeline/bin/python" \
  TILE_REPORTS="$TURKU3D_WORK/reports/rebased" \
  VALIDATOR_ARGS="--ortho $TURKU3D_WORK/reports/ortho.png --ortho-bounds 0,0,500,500"
```

The first complete post-integration `scripts/dev/gate` run passed all **19/19
stages in 164.836 s**, including validator tests (51.910 s), vehicle Node tests
(7.741 s), tooling tests (41.751 s), and game end-to-end tests (130.502 s).
Editing the routing table selects the full repository gate. Shared-host load
was high; no timeouts, assertions or physics thresholds were loosened.
