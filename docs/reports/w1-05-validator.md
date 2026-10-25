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

## Final landing verification

The final full `scripts/dev/gate` passed **19/19 stages in 382.956 s** on the
renderer integration (`04037fc`), including validator tests (60.133 s), tooling
(52.936 s), and browser end-to-end tests (343.871 s). Earlier intermittent WebGPU
input/HUD assertions passed unchanged in a focused four-test rerun and this full
gate; no input behavior, assertion or timeout was changed to obtain green.

The final rebase includes main `662f646`, which adds only two W2 Markdown briefs;
the implementation tree is identical to the full-gate-tested tree. The final
report edit receives the prose gate (`scripts/dev/gate --base HEAD`).
Curbs are deferred for the current playable-block iteration; the existing
`--checks format,seams,holes,belowGround --require format,seams,holes,belowGround`
subset is available to the plain-ground producer. No new checks were added during
landing closeout.

## Batch-1 landing repair

Rebased onto main `aaf46d1` after buildings, texture binding, facades, plain ground
and the car ghost fix landed. Conflict resolution retains the buildings-layer
README and `buildings.test.ts` gate entry alongside W1-05's shared-vehicle
acceptance suite. Main already includes the worktree removal optimization and
newer process scanning/stop fixes; those implementations are retained unchanged.

The local Python environment was synced with:

```sh
uv pip install --python .venv/bin/python -e 'pipeline[dev,fetch,lidar]' \
  -e 'textures[post,dev]' -r scripts/dev/requirements.txt
```

The only npm lockfile delta was the removal of `textures/node/package-lock.json`
and its package; no active npm package lock changed or required installation.

Per the orchestrator's explicit scope, **no full gate or browser suite was run**.
Validator and shared-game TypeScript checks, targeted ESLint, Python Ruff check
and format check passed. Area tests passed **50/50, zero skipped, 22.68 s wall**:

```sh
mkdir -p "$TURKU3D_WORK/cache/validator-tests"
PIPELINE_PY="$PWD/.venv/bin/python" TMPDIR="$TURKU3D_WORK/cache/validator-tests" \
  OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  node --test --test-concurrency=1 \
  tools/validator/test/*.test.ts game/tests/headless/vehicle.test.ts
```

Node suites now run one at a time, with two physics workers inside fixture tests
(at most three test workers total). The npm/Make/gate validator commands preserve
this limit; walker counts and every assertion are unchanged. The test log is
`$TURKU3D_WORK/reports/batch1-validator-tests.log`. Earlier AOI timings/findings
above are historical; the new batch-1 layer outputs were not rerun in this closeout.
The orchestrator runs the full landing gate.

Main advanced to `8c1f9a5` during closeout with building producer fixes. The final
rebase is clean; validator sources/tests, shared vehicle/physics integration and
gate routing are byte-for-byte identical to the area-tested tree. No dependency
lock or Python dependency declaration changed in this last main advance.

## Batch-2 landing repair

Rebased onto main `1626ead`, including gate-speed improvements, lidar tooling,
props and vegetation. Conflict resolution retains main's two-worker fixture
runner, selective fixture checks and gate sampling, together with the shared
vehicle acceptance suite, buildings tests and validator Python lint routing.
Standalone fixture tests still use 150 walkers; assertions and physics thresholds
are unchanged. Both README sections are retained.

Python dependencies were synced with the editable-install command above. No npm
lockfile changed. Validator and game TypeScript checks, targeted ESLint, Ruff lint
and format checks, and gate TOML parsing passed. Only area tests were run, with
one Node test file at a time and at most two additional physics workers. The full
gate remains the orchestrator's landing step, as explicitly requested.

The area-test command is the same as in the batch-1 section. Its current log is
`$TURKU3D_WORK/reports/batch2-validator-tests.log`. Real AOI evidence above remains
historical; this closeout does not add checks or rerun the newly landed layers.
All **50/50 tests passed, zero skipped**, in **393.39 s wall** (31.01 s user and
2.43 s system CPU) on the shared host. This is the standalone 150-walker suite;
the gate's landed 32-walker configuration was preserved but not rerun here.

## Batch-3 landing repair

Rebased onto main `637bdfb`, retaining the landed integration, spawn, parked-car
and audio changes. The two README conflicts retain both the audio collision
telemetry and parked-layer probe instructions alongside validator acceptance
and bounded-worker instructions. Vehicle/physics changes merged cleanly; no
runtime interfaces, assertions or thresholds were changed in this closeout.

Synced Python dependencies with the editable-install command above; no npm lock
changed. Validator/game TypeScript, targeted ESLint, Ruff lint/format and gate
TOML parsing passed. Area verification uses the batch-1 command with
`TURKU3D_JOBS=3`, one Node test-file worker and two fixture physics workers.
The log is `$TURKU3D_WORK/reports/batch3-validator-tests.log`.
All **50/50 tests passed, zero skipped**, in **39.28 s wall** (24.76 s user,
1.63 s system CPU), including the unchanged standalone 150-walker fixtures.
The full landing gate and real AOI rerun remain outside this explicitly limited
rebase task; the orchestrator runs the landing gate.

## Batch-4 landing repair

Rebased onto main `8112bf0`, retaining structures, ground textures, facades,
player and subsequent landed changes. The package script conflict keeps both
main's `structures` command and the validator's single-file test execution.
Gate routing retains every main stage and all six declared tags, with the
validator dependency triggers and acceptance suite preserved.

Synced Python environment with the current pipeline region/buildings/lidar and
textures ground extras; no npm lockfile changed. Targeted TypeScript, ESLint,
Ruff, gate TOML parsing and main stage/tag coverage checks passed. Area tests:
**63/63 passed, zero skipped, 18.52 s wall**; `TURKU3D_JOBS=3` bounds the runner
and the Node suite runs one file worker at a time. The log is
`$TURKU3D_WORK/reports/batch4-validator-tests.log`. Full gate remains for landing.

## Batch-5 landing repair

Rebased onto main `09d264b`, retaining the facade binding, routing, playtest,
placement, structures and curbside-car changes. The collision Make target now
retains the complete validator acceptance suite under one Node test-file worker;
the validator package also retains main's structures command. Gate TOML contains
all 21 main stages and all six main tags, plus validator-specific acceptance
routing.

Synced Python environment including the updated dev and Street View extras; no
npm lockfile changed. Only validator tests were run, with one Node test-file
worker and `TURKU3D_JOBS=2` (maximum three concurrent test/physics workers):
**50/50 passed, zero skipped, 14.76 s wall**. The log is
`$TURKU3D_WORK/reports/batch5-validator-tests.log`. The orchestrator runs the
full landing gate.

## Batch-6 integration gate

Rebased onto main `58965ff`, which includes facade binding, cost routing, task
watch, and L0 texture budget updates. The `scripts/dev/here` overlap was only a
local variable name; root discovery and main's behavior are preserved. All 22
main gate stages and six tags remain in `gate.toml`.

The first full gate run found an unused `runJobs` export in the validator worker
pool. Since it has only an internal call site, the export was removed. Main
advanced during that first run, so the branch was rebased again and the full gate
was run on the final base. It passed all stages in **226.607 s**. The run used
`TURKU3D_JOBS=3 scripts/dev/gate`; unchanged stage results came from the gate's
PASS-only cache. No tests or checks were weakened.

Main advanced once more during integration with the curbside spawn change
(`ddf38a1`). The branch was rebased onto that tip and the full gate rerun there:
all **22 stages passed in 154.420 s** with `TURKU3D_JOBS=3`. This is the final
landing verification.
