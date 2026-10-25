# Agent tooling

Python 3.10+, Git, `lsof`, Node and the authenticated Codex CLI are required. No services, API keys or Python
packages are needed for these commands. Policy: [AGENTS.md](../../AGENTS.md).

## Worktrees

Run from any checkout (the commands resolve their own repository):

```bash
scripts/dev/wt new stairs --from main
source ~/turku3d/wt/stairs/sandbox.env
cd ~/turku3d/wt/stairs/repo
scripts/dev/here
make game-e2e
scripts/dev/wt ls
scripts/dev/wt path stairs
scripts/dev/wt env stairs
# Leave the sandbox and stop its processes before removing it:
cd /path/to/main
scripts/dev/wt rm stairs
scripts/dev/wt gc
```

Layout: `~/turku3d/wt/<name>/{repo,work,browser,sandbox.env}`, branch `wt/<name>`.
`work` contains writable `build/`, `cache/`, `dist/`, `reports/`, and `qa/`; raw inputs remain under
`TURKU3D_DATA/raw`. Vite emits `work/build/game`, tiles use `work/dist/tiles`, Playwright artifacts use
`work/reports`, and browser temporary profiles live under `browser/tmp`. Textures use `work/textures`;
new Street View downloads use `work/cache/streetview`, never shared raw data.

`TURKU3D_PORT_BASE` reserves 10 ports. Main uses 5170–5179; allocation starts at 5180, skips existing
reservations and listeners, and is serialized by an advisory file lock under `wt/.registry`.
Offsets: +0 Vite dev; +1 Vite preview/dist/Playwright; +2 QA stub; +3–5 tooling tests. Preview and e2e
in the **same** sandbox are deliberately mutually exclusive; use separate sandboxes for concurrent runs.
`wt new` adopts existing manually created `sandbox.env` reservations automatically.
The registry protects against cooperating tools; it cannot prevent an unrelated process binding a reserved port.

`wt rm` refuses live processes (cwd in the sandbox or listeners on its ports), dirty files and commits not in main.
`--force` permits discarding dirty/unmerged work but still refuses live processes. It never sends signals.
`gc` releases only entries whose sandbox directory no longer exists; it never deletes files.
`TURKU3D_WT_ROOT` and `TURKU3D_TASKS_ROOT` override tooling storage for tests or an alternate installation.
Names are lowercase letters, digits, hyphens and underscores; `main` is reserved.

## Tasks (orchestrator)

```bash
scripts/dev/codex-task run stairs --brief docs/briefs/W1-02-structures.md --from main --effort high
scripts/dev/codex-task ls
scripts/dev/codex-task status stairs
scripts/dev/codex-task log stairs 30
scripts/dev/codex-task result stairs
scripts/dev/codex-task resume stairs 'Fix the failing seam test and rerun the gate.'
scripts/dev/codex-task stop stairs
scripts/dev/codex-task land stairs
```

Use an actual brief path from `docs/briefs/`. `run` creates the sandbox if absent, exports its environment and
starts `codex exec -m gpt-6-astra --sandbox danger-full-access -C <repo>` in a new process group. It returns
immediately. The prompt contains COMMON plus the brief. `resume` uses the recorded `thread.started` session ID,
not the most recent global session. The model and effort remain the same. Codex is responsible for committing work.
CLI behavior is checked against the installed CLI and [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

`~/turku3d/tasks/<name>/` holds `prompt.md`, append-only `events.jsonl`, `last.md`, `pid`, `meta.json`,
`runner.log`, follow-up prompts, and `gate.log` after landing. `status` detects interrupted processes and reports
elapsed time, last event and commit. `stop` verifies the supervisor process identity before signalling its group:
TERM, a three-second grace period, then KILL if necessary. No global process-name matching is used.

The quota guard reuses `textures/turkutex/codex/usage.py` (`codex app-server` JSON-RPC, no model call).
It refuses when any usage window reaches 100%, or usage is unknown. `run/resume --force` bypasses a known 100%
reading only. Tests use a fake `codex` on PATH and synthetic repositories; no real calls occur in `tooling-test`.

Landing is serialized with `wt/.registry/land.lock`. It requires a successful completed task, an exact
`## Result: DONE` line and clean worker/main worktrees. It rebases on main (aborting a conflicting rebase),
runs `scripts/dev/gate --base <old-main-sha>` in the worker environment, verifies the tested HEAD/tree and
unchanged main, then fast-forwards main. If main has no checkout, a compare-and-swap `git update-ref` is used.
Do not update main outside this orchestrator while landing. Before W0-B supplies the gate, touched areas map to
`make *-test` targets in `task_runner.py`; unknown non-doc paths fail closed. The landing record goes to
`wt/.registry/landed.jsonl`. A red gate leaves main unchanged; inspect `gate.log`, fix and resume the task.

## Verification

```bash
make tooling-test game-dev-tools-test
make -j 4 foundation-test game-test qa-test textures-test
make game-e2e
```

`tooling-test` runs four independent synthetic-repository tests at a time and covers concurrent port claims, adoption, shell-safe environment generation, fake run/resume,
quota refusal/override, process-group stop, landing success/refusals, removal and orientation from both checkouts.
`game-dev-tools-test` starts two dist servers simultaneously with distinct environment ports and output roots.
To check complete game stacks, create two worktrees from the tooling commit, source each environment in separate
shells, and run `make game-e2e` concurrently. Each installs its own npm dependencies on first use.

## Wave 0 integration

The branch includes W0-C's native local pipeline, W1-06's controller/character work, W1-07's vehicle and W1-11's visual QA. `mk/tile.mk` keeps
its ordered local build chain and serves `$(GAME_DIST)` (`work/build/game`) for `tile-show` and `tile-qa`.
`raw_dir()` reads shared inputs; `work_root()` / `build_dir()` / cache/dist helpers use sandbox outputs.
Remote and sync commands are removed. Controller and vehicle screenshots use the sandbox report root.
QA retains its parallel judge, manifest provenance, immutable runs, calibration and regression checks.
`QA_DATA` overrides the sandbox QA root; the local stub defaults to port base +2 and keeps `--port` overrides.
The English-language policy from COMMON now lives in AGENTS.md. W0-B should map `scripts/dev/` to `make tooling-test` and include `tools/dev/environment.mjs`
in the game/QA configuration dependency scope. Its gate CLI is already used as `gate --base <main-sha>`.

Initial acceptance before the integration rebase (macOS, 2026-10-25): two new worktrees from implementation commit `1f9b1d9`, ports
5260–5269 and 5270–5279, ran `make game-e2e` concurrently: **10/10 each**, 83.01 s and 84.09 s including
first npm installs and builds. The real `gpt-6-astra` runner smoke call finished in 13.2 s with the correct
sandbox environment and `## Result: DONE`. Synthetic tests verified a trivial fast-forward to their own
main branch, red-gate/result/dirty-tree refusals, and a concurrent-main-change refusal; the project main
branch was not landed. Temporary acceptance worktrees were removed after retaining logs in
`~/turku3d/wt/w0-tooling/work/reports/` and the real task record in `~/turku3d/tasks/w0-verify-a/`.

Pre-rebase area checks: 119 passed, 2 optional texture tests skipped (KTX2 npm dependencies and DeepBump are not
installed in this sandbox). Tooling tests: 9.87 s; foundation: 0.57 s; game units: 1.77 s; dist server: 0.39 s;
QA Python: 0.97 s; QA render: 12.56 s; texture tests: 5.28 s. `here`: 0.16 s in main, 0.33 s in the sandbox.

## Rebase verification (2026-10-25)

Rebased onto `7218b14` (including W1-06 `9523b80` and W0-C), preserving the native local pipeline,
controller tests and sandbox environment. Keyboard routes now hide host gamepads; virtual-controller
routes still inject their own inputs. No movement thresholds or timing limits were weakened.
Engine feasibility probes also use sandbox ports, browser profiles, build/cache directories and screenshots.

```bash
source ../sandbox.env
make -j 3 tooling-test foundation-test pipeline-test col-test game-test qa-test textures-test game-build game-dev-tools-test
make game-e2e
make engine-probe-test
# Complete concurrent stacks, from this implementation branch:
scripts/dev/wt new verify-a --from wt/w0-tooling
scripts/dev/wt new verify-b --from wt/w0-tooling
# Terminal A:
source ~/turku3d/wt/verify-a/sandbox.env
cd ~/turku3d/wt/verify-a/repo
make game-e2e
# Terminal B, while A is running:
source ~/turku3d/wt/verify-b/sandbox.env
cd ~/turku3d/wt/verify-b/repo
make game-e2e
```

All requested area targets passed: tooling 12, foundation 21, pipeline 54, collision 38,
game 44, QA 23, textures 31 (2 optional dependency skips). Dist-server checks: 6 passed.
The area batch took 55.56 s; individual elapsed times were tooling 11.07 s, foundation 1.19 s,
pipeline 9.52 s, collision 52.52 s, game 2.41 s, QA 44.49 s, textures 4.58 s,
game build 1.36 s and dist-server tests 0.37 s. The final tooling rerun passed in 17.64 s.
A tooling run overlapping the browser stress checks exceeded the existing orientation/removal timing
limits; it was repeated successfully after the browser work finished, with those limits unchanged.

The final standalone game suite passed 16/16 in 154.22 s. Two complete stacks at `066d712`,
ports 5330–5339 and 5340–5349, passed 16/16 each concurrently in 220.72 s and 213.25 s.
Logs are retained in `~/turku3d/wt/w0-tooling/work/reports/rebase/`; temporary acceptance
sandboxes are removed. The project main branch was not modified. Main continued advancing during
verification; the tested integration base is explicitly `7218b14`.

Engine-probe build/Node checks passed in 38.52 s under concurrent load; its Chrome WebGPU
headless route passed in 27.37 s. Final `scripts/dev/here` completed in 0.37 s.

## Frozen-main integration verification (2026-10-25)

Rebased onto frozen main `ea0509d`, including W1-07 vehicle (`2fd13b0`) and W1-11 visual QA
(`8e8d2f9` through `ea0509d`). The QA loop and path-precedence implementation match main;
parallel judging, immutable evidence, calibration, regression and manifest provenance remain intact.
Sandbox integration retains the explicit `TILE_QA_PORT` / render `--port` overrides, defaults to
base +1 / +2 respectively, and uses the shared browser-profile helper. Vehicle screenshots now
use the sandbox reports directory. Active examples no longer reference the other workers' ports.

```bash
source ../sandbox.env
make tooling-test qa-test game-test game-build game-vehicle-test
make game-e2e
```

| Target | Result | Elapsed |
|---|---|---|
| `tooling-test` | 13 passed | 18.45 s |
| `qa-test` | 32 Python + 7 render tests passed | 28.09 s |
| `game-test` | 67 passed | 5.90 s |
| `game-build` | typecheck and production build passed | 21.04 s |
| `game-vehicle-test` | 22 game-WASM + 13 Node tests passed | 22.95 s |
| `game-e2e` | 18 passed, WebGPU and WebGL2; one run | 124.64 s |

An initial parallel batch exceeded the unchanged 2 ms streaming assertion and a 20 s tooling
removal timeout. Both affected targets passed when rerun separately; no checks or limits were
weakened. The build/vehicle timings include contention from that batch. Logs and machine-readable
results are under `~/turku3d/wt/w0-tooling/work/reports/rebase-ea0509d/`.
Main remains at `ea0509d`; this branch has not been merged into main.
