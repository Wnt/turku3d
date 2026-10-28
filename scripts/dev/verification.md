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

Admission uses dedicated CPU shares on the ten-core host: two bulk `build` commands
(two jobs each), two short `check` commands (one job each), one `integration` (two
jobs) and one `integration-data` (two jobs). All six fit simultaneously. Game builds
use `check`, so tile builds cannot consume their capacity. Cache hits acquire no slot.
The observed public game build takes 4.044 s including type checking and both bundles;
its hash-verified repeat takes 0.320 s. Keeping three bulk builds would consume the
capacity needed to admit two checks beside both publication lanes; two is the largest
bulk count that leaves those measured short checks independent on ten cores.
GPU capacity remains five workers; explicit integrator Chrome uses one browser slot.
Builds queue FIFO. Short checks and browser admission stop with an actionable error
after 300 s rather than waiting through an entire task. This is a queue bound, not a
promise that bulk builds finish in five minutes.

Shared state keeps checks as `kind: build, purpose: check`; old scheduler processes
conservatively count them as builds and do not crash on an unknown cost-table key.
Existing old processes must exit naturally before the new lane counts apply fully.
Integration reservations cover actual builds only; checkout, Pages and diagnostics
hold no CPU reservation. `tile-add` acquires build slots per local compute stage,
releasing them during source downloads and remote facade-description calls.
Nested commands inherit their integration lane's budget. Dead owner PIDs are
reclaimed on admission/status; waiting is visible in `codex-task ls` and `watch`.

Explicit integrator Chrome acquires one scheduled GPU worker while it runs.
There is no automatic full-suite schedule or two-hour invocation timeout on the
normal publication path. A focused worker browser run still debits its task budget.

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


## Focused tile iteration

```sh
source ../sandbox.env
make tile-reuse                         # only missing products; keeps local work
make refresh REFRESH_TARGET=roads       # only this stage, reusing built prerequisites
make tile T=t0_0_0 AREA=playable-tiles   # assembly + format/seams, no Chrome
make tile-show                         # bundle is reused when intact
```

Single-stage refresh accepts ground, buildings, structures, bridges, roads, landmarks,
buildings_outer, props, vegetation, traffic_static and bridge-textures. Existing
`ground-textures` and `facades` targets keep their prerequisite ordering. Single-stage
calls deliberately use existing prerequisites; if those inputs changed too, refresh
them first or omit REFRESH_TARGET for the complete dependency chain. The producers'
per-tile keys and assembler caches already rebuild only stale tiles. `tile-reuse`
freezes one release's tile list when cloning its tiles, so a new shared tile-list entry
does not unexpectedly turn a focused check into an area expansion. Existing local
products/lists are preserved. Add new coverage with `make tile-add T=...` explicitly.
