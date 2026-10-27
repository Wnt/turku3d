# Gate routing and execution profile (2026-10-25)

Status: **DONE**. All stages have green verification after rebasing. Timing targets remain
unverified under severe shared-host contention; the orchestrator accepted this limitation.

Baseline: `04037fc`. Sandbox: `fix-gate-speed`, ports 5360–5369. No main merge/landing.
Raw JSON and stage logs are in this sandbox's `repo/.cache/gate-speed/`.

## Findings and changes

A package edit selected `policy`, `generated`, `python-lint`, `pipeline-tests`, and the entire
`game-e2e` stage. It did **not** itself select texture/tooling/validator tests. Changes to gate routing
force every stage; several incoming branches carry routing changes, explaining the near-full landings.
That safety rule remains. Unmapped modules retain conservative full-package fallback.

Package edits no longer select browsers. Physics selection now includes all physics modules and the
shared collision code, closing gaps in the former worker/client-only trigger. Related Vitest tests use
Vite's dependency graph. QA report routing follows the direct report/history callers and `loop.main`
integration. Incoming lidar, region and Codex benchmark paths have explicit rows. Preserve the separate
`watch-tests` row from `tool-watch` when the orchestrator combines branches.

Browser suites use four workers and full file parallelism, one build per gate, and shared, hash-verified
synthetic tiles. Cache invalidation covers all Python/JavaScript producer sources/data, fixture generators, codec and
assembler lockfiles, synthetic input files, and NumPy/SciPy/Pillow versions. Corrupt output hashes force reconstruction. Game input pages
emulate focus, and the analog-speed test samples within animation frames rather than missing transient
velocity through slow protocol polling. Its speed bounds and five-second sampling window remain. Controller specs now inherit the shared
120-second functional deadline instead of a local 25-second cap; measured frame budgets stay in
`make render-perf`.
`test:all` no longer builds twice.

Validator cases run in two separate WASM workers. Negative fixtures execute their expected checks;
the good fixture still runs every check. Gate sampling is 32 seeded walkers, standalone fixtures 150,
real `make tile-validate` 2,000. All status, threshold, detailed outcome and CLI assertions remain.
The KTX2 test retains 128×128 size/eight mip levels/all header assertions with a two-colour input;
image-quality tests retain noisy inputs. Albedo, ORM and normal encoding run concurrently.

Stop now excludes zombies from process-group liveness. An ESRCH/EPERM is benign only if the original
live identities have disappeared; permission failures against live members still fail. Escalation
verifies individual identities rather than signalling a possibly reused group ID. Tests inject exit,
EPERM, zombie and PID-reuse cases; the fake CLI installs its TERM handler before reporting readiness
and reaps its child. Functional CLI subprocess waits allow 60 seconds under contention; the
explicit `here < 2 s` performance assertion is unchanged. Worktree removal checks cheap Git refusals first, scans cwd separately, and only
scans sockets when wildcard IPv4/IPv6 binding detects occupancy. Listener-only processes remain protected.

## Measurements

These representative-route probes were measured before the final rebase; the rebased full gate is
reported separately below. These are elapsed observations, not controlled speedup claims. During the run, this 10-core host had
load averages **433 / 457 / 478**, later **402 / 436 / 464**. Other agents independently reported the
unchanged 2-second `here` assertion and 20-second removal timeout failing. No other processes were stopped.
A single synthetic tile profile took 44.1 seconds, including 14.1 seconds in imports and 16.7 seconds
writing 16 collision tiles. This is a material limit on timing acceptance under the current load.

| Representative route | Before (s) | After (s) | Observation |
|---|---:|---:|---|
| `docs/STATUS.md` | 1.859 | 2.117 | Green; after narrowly misses the 2 s target |
| `pipeline/turku3d/package/manifest.py` | 158.996 | 73.689 | After green; package suite alone 86.667 s before; unrelated e2e removed |
| `game/src/render/*.ts` | 114.000 | 142.625 | After green; render/QA browser subset retained |
| `game/src/physics/worker.ts` | 246.847 | 200.045 | Final corrective gate green; cold synthetic fixture build included |
| `qa/report/report.py` | 29.860 | 10.727 | Green; report/history/loop dependency subset meets 20 s target |

The initial render probe used the former `render/pipeline.ts` path (same render tag route);
the after probe uses existing `render/renderer.ts`. Initial render/physics reports also recorded missing
root ESLint/Knip tools while dependencies were being installed; their browser timings remain recorded,
but these are not green before-gate benchmarks. The initial package browser run failed its analog-speed
poll. The first after physics run (64.883 s) reproduced that poll failure, prompting frame-local sampling.
The raw reports retain these failures rather than treating a red run as a successful acceptance.

Stage observations: validator 133.774 → 23.816 seconds (22 assertions/tests pass); texture suite
95.05 → 29.75 seconds after fixture simplification (32 pass, one pre-existing optional DeepBump skip).
With concurrent encoding and increased host contention the texture suite measured 64.30 seconds.
Tooling baseline was 84.05 seconds; the final focused tooling gate passed in 78.43 seconds, including its
gate/policy tests. Thus the requested 20-second single-area and 60-second runtime targets are **not yet
established** on this host; increasing worker counts alone does not overcome this contention.

## Reproduce

```sh
source ../sandbox.env
scripts/dev/gate --paths docs/STATUS.md --json
scripts/dev/gate --paths pipeline/turku3d/package/manifest.py --json
scripts/dev/gate --paths game/src/render/renderer.ts --json
scripts/dev/gate --paths game/src/physics/worker.ts --json
scripts/dev/gate --paths qa/report/report.py --json
scripts/dev/gate --all --json
```

`--paths` is an explicit diagnostic override; normal gates and landing still use the Git diff.
The full suite always runs all assertions, irrespective of the related-test rows.

## Follow-ups

Run the same five probes with a quiet host to establish timing acceptance. Keep full real-data sampling
in `make tile-validate`. The orchestrator requested dependency-complete per-stage GREEN result caching, incremental re-gating
when main advances, fail-fast landing precondition checks, and batched landing with one combined gate and culprit isolation. These need a
separate follow-up with cache invalidation and landing-race tests. Synthetic asset reuse in this
change is not a substitute for a stage-result cache. Existing landing already checks
unacknowledged requests before the gate and again before updating main.

## Verification of the final changes

The full gate ran all 19 stages in 283.943 seconds: 17 passed. Its tooling process had loaded the old
20-second CLI deadline before the standalone tooling commit; removal timed out. One WebGPU stairs
case completed its movement but hit its local 25-second test cap while retrieving the final samples.
Both were corrected without removing functional assertions: the committed CLI deadline is 60 seconds
and controller tests inherit the existing 120-second suite deadline. Only affected stages were rerun;
other stage assertions and thresholds were not changed. The post-routing regression gate is green
in 35.700 seconds and includes cache invalidation, incoming producer rows, and QA caller coverage.

The final corrective tooling gate is GREEN in 95.972 seconds (20 tooling/environment/stop cases
plus the gate/policy/cache regressions). The unchanged here latency assertion passes.

The final physics gate is GREEN in 200.045 seconds, including the full selected walk/drive browser
subset and pipeline tile assertions. Historical timing targets remain unmet: package 73.689 s, render 142.625 s
and physics 200.045 s exceed the requested budgets under severe host contention. No blanket GREEN stage
cache or incremental landing rewrite is claimed.

The custom render-controls context also passed both WebGPU/WebGL cases (2 passed, 1.2 minutes),
including the unchanged KeyH/hidden-slider regression. A direct second fixture invocation printed
`Reusing hash-verified synthetic browser tile`; no producer rebuild was performed.


## Final integration verification

Rebased onto main `aaf46d1` without landing or merging main. The stop/scanner fix was already
present as `77d7353`, so Git skipped its duplicate. Preserved the newly landed four-tile production
assembler browser fixture, building validator cases, and producer test rows. Fixture reuse now
hashes both assembler and codec JavaScript/lockfiles and the synthetic fixture generator; the
JavaScript assembler bridge explicitly selects assembler Python integration tests.

The rebased full gate ran all 19 stages in **423.926 s**. Seventeen passed, including game-e2e
**343.413 s**, validator **167.747 s** (24 tests), and tooling **116.499 s** (20 tests).
The only failures were missing sandbox dependencies introduced by the new main: `mapbox_earcut`,
`matplotlib`, and `streetlevel`. Installed the current pipeline extras and documented Streetlevel
setup; no assertions changed. Reran both affected complete suites plus gate regressions:

```sh
source ../sandbox.env
uv pip install --python .venv/bin/python -e 'pipeline[dev,fetch,lidar,buildings]'
uv pip install --python .venv/bin/python --no-deps streetlevel==0.12.13
uv pip install --python .venv/bin/python -r textures/requirements-streetlevel.txt
scripts/dev/gate --paths pipeline/pyproject.toml textures/pyproject.toml scripts/dev/tests/test_gate.py scripts/dev/tests/test_gate_fixtures.py --json
```

That corrective gate is **GREEN, 21.837 s**: pipeline 21.785 s, textures 11.047 s,
gate regressions 10.443 s; policy, generated files, and lint also pass. All 19 stages therefore
have passing verification across the full and corrective runs; this is not a claim that the
initial full invocation was green. Raw results: `.cache/gate-speed/rebased-gate.json` and
`.cache/gate-speed/rebased-corrective-gate.json`. Load averages ranged from about 230 to 460
during the full run and fell to about 165 during the corrective run. The unchanged timing
budgets cannot be compared reliably at this contention level.

Both backends pass the four-L0-seam route (previously skipped with a one-tile fixture).
The two coarse-terrain cases remain intentionally skipped because synthetic fixtures do not
include real coarse terrain. Direct cache verification prints `Reusing hash-verified synthetic
browser tile`. Per-stage result caching, fail-fast landing preconditions, and batched landing
remain the separate next task described above.
