# Load-robust tooling verification

Date: 2026-10-25. Sandbox: `tool-load-robust-gate`, ports 5490–5499.
The project main branch was neither merged nor landed by this task. All landing tests use
small disposable Git repositories, including their own synthetic main branch.

## Implementation

- `TURKU3D_JOBS` defaults to max(2, logical cores / 3 rounded down). It controls gate stage
  concurrency, pytest, Playwright, Vitest, tooling test scheduling, validator fixture workers
  and Make. Validator test files run serially so their internal worker pool owns the budget.
- Functional checks observe state, browser frames, or synchronization events. Streaming,
  orientation, documentation-gate and QA response budgets remain explicit performance checks.
  The car smoke route brakes on the frame that reaches the target, so protocol/screenshot
  latency cannot extend the drive onto a different ground surface.
- `env-sync` hashes dependency manifests, uses editable Python installs and npm ci, stamps only
  successful installs, and runs before gate stages. `wt sync-env [name|--all]` exposes the same
  implementation. The fresh import stage builds a separate environment from declared extras.
  Tests resolve native tools in the same directories and order as `pipeline/env/run.sh`.
- Stage caches retain only PASS results. Their keys include routed input contents, inventory,
  config, test/lint selection, tool versions and sandbox settings. Policy always reruns.
  One checkout lock serializes gates that share browser ports and build output.
- Single and batch landing check preconditions before the gate, then retry at most three main
  advances. Batches replay commits in order into an isolated integration checkout, gate once,
  and fast-forward main only on green. Red batches bisect failing prefixes, preserving earlier
  members needed to reproduce interactions. Each successful member gets a ledger record.

## Verification commands

```sh
source ../sandbox.env
scripts/dev/wt sync-env
scripts/dev/gate --all
scripts/dev/gate --paths docs/STATUS.md --json
scripts/dev/gate --paths pipeline/turku3d/package/manifest.py --json
.venv/bin/python scripts/dev/tests/run.py test_gate_state.py test_tooling.py
# Separate, unloaded-host budgets:
TURKU3D_JOBS=1 make streaming-perf
TURKU3D_JOBS=1 make tooling-perf
TURKU3D_JOBS=1 make render-perf
```

Unit coverage includes worker defaults/invalid overrides, cache input/deletion/scope/tool
invalidation, failure non-caching, dependency drift and failed-install retries, checkout locking,
batch success and per-member records, red-batch bisection without changing main, fail-fast
landing guards, and a concurrent-main retry. Production Python/JS checks and both browser
backends are exercised by the full gate.

## Host conditions and timing

At 23:41 local time the host reported load averages **341.89 / 292.33 / 259.16** and
**2,748 MiB swap used**. Other sandboxes were running browser acceptance concurrently.
The task registry showed many interrupted/completed entries, so it could not establish an
exact eight-active-agent workload. An eight-agent measurement window was requested from the
orchestrator; current measurements must not be described as an exact eight-agent benchmark.

Initial tooling verification: **33 passed in 180.57 s** under contention. The focused cache,
environment and lock contracts subsequently passed **5/5 in 1.37 s**. A warm documentation
probe completed in **1.495 s**, including `cached PASS` for generated materials.

The first full run was RED in 490.674 s. It exposed an import-format error, missing Knip
registration for the explicit perf config, the car's screenshot-time coasting, and an overlapping
local timing probe that rebuilt assets while the browser suite was running. These were fixed;
the overlapping probe was stopped, and checkout serialization now prevents that race.

The corrected full gate passed all **21 reported steps in 396.998 s**. The final-tree run
then passed all **21 steps in 242.093 s**. Its browser stage took 187.874 s: 34 game tests and
6 tile tests passed on WebGPU/WebGL2, with the two existing coarse-terrain skips. Python checks
passed 125 pipeline, 61 texture (one optional DeepBump skip), and 32 QA tests; game units passed
79 tests. Tooling and gate contracts passed 23 and 19 tests respectively.

| Probe | Gate seconds | Cached stages | Result |
|---|---:|---:|---|
| Packaging manifest, first selected scope | 23.935 | 1 | GREEN |
| Same packaging scope, repeat | 3.785 | 3 | GREEN |
| Documentation, final warm probe | 4.443 | 1 | GREEN |
| Full gate after README/test-scope refresh | 22.131 | 17 | GREEN |
| Identical full-gate repeat | 2.993 | 19 | GREEN |

The two noncached steps on the identical full repeat are env-sync and policy. The first
packaging probe exceeded the 5–20 s target under host contention; the repeat reused all three
selected cacheable stages. These are measured gate-report wall times, excluding time queued
behind another gate's checkout lock. The exact eight-agent sample remains unverified.
Raw logs and JSON are retained in this checkout's `.cache/` (`full-gate.log`, `final-gate.log`,
`verified-gate.log`, `tooling2.log`, and `timing-*.json`).

## Remaining measurement

Repeat the typical-change probes during an orchestrator-confirmed eight-active-agent window.
Performance budgets must be measured separately on an unloaded host, not in the functional gate.
