## Verification scope (2026-10-28)

Normal integration runs no Chrome or deep gate, including after a tile-list change.
Cheap data audits remain advisory. Use the routed fast gate and one focused behavior
check. Reuse built tiles and render only changed instances; see AGENTS.md rule 11.
The complete runtime/failure inventory is in
[iteration measurements](../../docs/reports/fix-iteration-pace.md).

```sh
python3 scripts/dev/iteration_metrics.py --days 3
# Explicit investigation only, not a worker completion requirement:
python3 scripts/dev/integrator.py run --full
scripts/dev/gate --deep --all
```

## Load-robust gates and environment sync

### Host slots

`make refresh`, `make tile-build`, `make game-build`, the game, tile and render
Playwright suites, playtest, the gate's e2e stage and the integrator's live build use
`scripts/dev/host_slots.py`. The shared state is `~/turku3d/host-slots/` (override with
`TURKU3D_SLOT_DIR` only for synthetic tests).

Admission uses dedicated CPU shares on the ten-core host: at most three agent `build`
commands (two jobs each), one `integration` (two jobs) and one `integration-data`
(two jobs). Both integration lanes fit beside all agent builds, so publication never
waits for an agent's tile build. GPU capacity remains five workers: three single-worker
agent suites and two integrator workers. Builds queue FIFO within their own lane.
Integration reservations cover `Run.build` only; checkout/dependency sync, release
preparation, Pages upload and diagnostics hold no CPU reservation. Nested build commands
inherit their lane's two-job budget through `TURKU3D_INTEGRATION_OWNER`.
Dead owner PIDs are removed on the next admission or status read. Waiting appears
in `codex-task ls` and `watch`, and commands print when a slot is acquired.

Browser share: Chrome gets 2 workers below load 60 (`BROWSER_LOAD`), else 1 (`browser_workers`). The 20-test real-tile suite took 1.9 min with 4 workers: estimate 3.8 min with 2 or 7.6 min with 1,
before load overhead, within `advisory_chrome.TIMEOUT` (2 h per invocation). Playwright's 120 s limit is per test.
Agent suites use 1 worker, including playtest's two backends serially. During integrator Chrome,
`host_slots.phase("chrome")` marks `phase: chrome` in `state.json` for status tools; agent `browser`
admissions continue within the GPU budget (5 workers, at most 2 for the integrator).

Every admission appends `{"time", "kind", "name", "wait_seconds"}` (name is the sandbox) to
`~/turku3d/host-slots/waits.jsonl`.
`python3 scripts/dev/host_slots.py stats [--hours 24]` prints admissions, median and p90 wait
seconds per kind. Raise agent concurrency only while the median wait stays low (study R6/M4).

Direct heavy commands should use the same wrapper, for example:

```sh
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.refresh
cd game && python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test
```

```sh
source ../sandbox.env
export TURKU3D_JOBS=3
scripts/dev/wt sync-env                 # current checkout
scripts/dev/wt sync-env example         # named sandbox (or --all)
scripts/dev/gate --all
scripts/dev/gate --paths game/src/core/origin.ts --json
python3 scripts/dev/integrator.py status
# Explicit budgets, on an unloaded machine:
TURKU3D_JOBS=1 make streaming-perf
TURKU3D_JOBS=1 make render-perf
```

`env-sync` hashes pyproject/requirements files and each npm package/lock pair into
`.cache/env-sync/stamp.json`. It serializes installs, writes stamps only after success,
and detects missing environments. Python uses editable packages with declared extras;
Node uses npm ci. It does not mutate linked hook dependencies: sync the source checkout
first. A fresh-environment import stage checks dependency declarations when manifests change.
Native test tools resolve from the same prepared directories as pipeline/env/run.sh.

Successful stages are stored by content hash under `$TURKU3D_DATA/cache/tool-stages/gate`.
The key includes
routed source/dependency domains, inventory (including deletions), selected test/lint scope,
configuration, tools and installed Python versions. Sandbox paths and ports are excluded. Only GREEN stages
can report `cached PASS`; failures are never cached. Policy runs every time because flag expiry
depends on main's merge count. Live build stages keep APFS-cloned verified artifacts under
`$TURKU3D_DATA/cache/tool-stages/live`; keys include declared source paths, input manifests
and the preceding stage key.

