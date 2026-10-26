# Developer tools

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
scripts/dev/wt du
scripts/dev/wt prune-landed
scripts/dev/wt path stairs
scripts/dev/wt env stairs
scripts/dev/wt reap stairs # stop only Chrome profiles inside stairs/browser
# Leave the sandbox and stop its processes before removing it:
cd /path/to/main
scripts/dev/wt rm stairs
scripts/dev/wt gc
```

Layout: `~/turku3d/wt/<name>/{repo,work,browser,sandbox.env}`, branch `wt/<name>`.
`work` contains writable `build/`, `cache/`, `dist/`, `reports/`, and `qa/`; raw inputs remain under
`TURKU3D_DATA/raw`. Vite emits `work/build/game`, tiles use `work/dist/tiles`, Playwright artifacts use
`work/reports`, and browser temporary profiles live under `browser/tmp`. Textures use `work/textures`.
Compressed Street View panoramas and content-addressed decoded arrays use
`$TURKU3D_DATA/cache/streetlevel/` across sandboxes. Roofer reconstructions and class-6
point arrays use `$TURKU3D_DATA/cache/buildings/`. Facade crops, KTX2 intermediates,
build products, `dist/` and browser profiles remain sandbox outputs.

`TURKU3D_PORT_BASE` reserves 10 ports. Main uses 5170–5179; allocation starts at 5180, skips existing
reservations and listeners, and is serialized by an advisory file lock under `wt/.registry`.
Offsets: +0 Vite dev; +1 Vite preview/dist/Playwright; +2 QA stub; +3–5 tooling tests. Preview and e2e
in the **same** sandbox are deliberately mutually exclusive; use separate sandboxes for concurrent runs.
`wt new` adopts existing manually created `sandbox.env` reservations automatically.
The registry protects against cooperating tools; it cannot prevent an unrelated process binding a reserved port.

`wt rm` refuses live processes (cwd in the sandbox or listeners on its ports), dirty files and commits not in main.
`--force` permits discarding dirty/unmerged work but still refuses live processes. It never sends signals.
`wt reap <name>` sends TERM only to Chrome processes with a `--user-data-dir` strictly inside that sandbox's
`browser/`; run it before `wt rm` if an interrupted Playwright run left a profile behind.
`gc` releases only entries whose sandbox directory no longer exists; it never deletes files.
`du` lists allocated bytes per sandbox, largest first, with `landed`, `unmerged`, `dirty` and
`live` status. `prune-landed` applies the ordinary `wt rm` checks to each clean, merged,
stopped sandbox.

Disk probe (2026-10-26): a fresh `wt new facade-measure --from main` under this task's work root
started with an empty `work/`. Decoding one existing 16384×8192 Street View panorama and writing
one 1536×768 facade crop used 402,755,584 allocated bytes in the former sandbox cache layout:
402,657,280 bytes were the decoded array. The shared layout left 98,304 bytes in that sandbox's
`work/` for the crop and wrote the array once to the shared cache. The probe sandbox was removed.
The same fresh-worktree probe ran local `turkutex post` on the crop with `--tile none
--size 1024x512 --material facade_mixed --sobel`, producing albedo, ORM and normal KTX2.
With identical post outputs, allocated sandbox `work/` space was 404,434,944 bytes before
and 1,777,664 bytes after the shared cache change. The post outputs themselves used
1,679,360 bytes in both runs. No image-generation call was used.
The historical `w1-09d-facades` sandbox had already been removed, so its exact 128 GB breakdown
could not be recovered. The prior per-sandbox decoded arrays and per-sandbox Roofer/class-6 caches
were repeatable bulk copies. `dist/`, facade outputs and browser profiles remain sandbox-specific
and are reclaimed when their landed sandbox is removed.
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
scripts/dev/codex-task precheck stairs
scripts/dev/codex-task queue status
scripts/dev/codex-task queue run          # one daemon, started by the orchestrator
```

Use an actual brief path from `docs/briefs/`. `run` creates the sandbox if absent, exports its environment and
starts `codex exec -m gpt-6-astra --sandbox danger-full-access -C <repo>` in a new process group. It returns
immediately. The prompt contains COMMON plus the brief. `resume` uses the recorded `thread.started` session ID,
not the most recent global session. The model and effort remain the same. Codex is responsible for committing work.
`run` refuses to create a sandbox below 30 GiB free on the sandbox filesystem. Check with
`scripts/dev/wt du`, then remove safe candidates with `scripts/dev/wt prune-landed`.
CLI behavior is checked against the installed CLI and [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

`~/turku3d/tasks/<name>/` holds `prompt.md`, append-only `events.jsonl`, `last.md`, `pid`, `meta.json`,
`runner.log`, follow-up prompts, and `gate.log` after landing. `status` detects interrupted processes and reports
elapsed time, last event and commit. `stop` verifies the supervisor process identity before signalling its group:
TERM, a three-second grace period, then KILL if necessary. No global process-name matching is used.
`stop` also reaps sandbox Chrome profiles; landing reaps them before verification and cleanup.

The quota guard reuses `textures/turkutex/codex/usage.py` (`codex app-server` JSON-RPC, no model call).
It refuses when any usage window reaches 100%, or usage is unknown. `run/resume --force` bypasses a known 100%
reading only. Tests use a fake `codex` on PATH and synthetic repositories; no real calls occur in `tooling-test`.

Landing is serialized with `wt/.registry/land.lock`. It requires a successful completed task, an exact
`## Result: DONE` line and clean worker/main worktrees. It rebases on main (aborting a conflicting rebase),
runs `scripts/dev/gate --base <old-main-sha>` in the worker environment, then seeds private APFS copies
of the published layers, textures and tile dist. It runs `make refresh`, `make tile-build
T='t0_0_0 t0_0_1 t0_1_0 t0_1_1'`, `make game-build`, `make collision-sweep`, `make collision-audit`,
and the real-tile Playwright suite in Chrome on the sandbox dist port. The suite checks spawn loading on
WebGPU and WebGL2 and fewer than 500 draws. The manifest check requires ground textures on all four L0
tiles and each tile below 8 MB on the wire. The assembler's `facades_bound` count must meet the current
published release's count. A task may report an intentional reduction with `Facade regression reason:
<specific reason>` in `last.md`; the exception is recorded in the landing ledger. Stage times and output
are in `live-build.log` and `meta.json`.
Each gate and live-build command runs in its own process group, which is terminated on exit.
Failures name the failing stage or test and include the host load and log path.
For the first landing, the baseline comes from the legacy `deploy-once.log` assembler result (164 on
2026-10-26); later landings read the previous release's `live-build.json`.

After verifying the tested HEAD/tree and unchanged main, landing fast-forwards main. If main has no
checkout, a compare-and-swap `git update-ref` is used. The verified game, tiles, layers and textures
are APFS-copied into `$TURKU3D_DATA/live-releases/<commit>`; one atomic `$TURKU3D_DATA/live` symlink
swap publishes the release. Stable `$TURKU3D_DATA/build/game` and `dist/tiles` paths resolve through
that link, so the existing :5171 static server serves the verified files without a process restart.
Preparation dereferences sandbox links and rejects any link left inside a release. Publication checks
all four stable paths after the swap and restores the previous `live` link if a path does not resolve
to its directory in the prepared release.
`~/turku3d/logs/live-version.txt` records the published commit. Do not run `deploy-once.sh`,
`autodeploy.sh`, or `make refresh` on main after landing: those rebuild unverified artifacts.
Do not update main outside this orchestrator while landing. A missing gate fails closed; there is no
legacy Make fallback. The landing record goes to
`wt/.registry/landed.jsonl`. A red gate leaves main unchanged; inspect `gate.log`, fix and resume the task.
After a successful fast-forward, `land` and each batch member remove clean, stopped sandboxes
and report allocated bytes freed. A retained sandbox prints the reason; remove it later with
`scripts/dev/wt rm <name>` or `scripts/dev/wt prune-landed`.

### Live agent monitor

```bash
scripts/dev/watch                         # curses channels for all tasks
scripts/dev/watch w1-01-ground tool-watch # restrict task/inbox channels
scripts/dev/watch --plain                 # stream human sentences; also automatic for pipes
scripts/dev/watch --plain --once          # replay all available history and exit
scripts/dev/watch --plain --once --replay 10 # limit startup sentences per file
python3 scripts/dev/tests/test_watch.py   # fast synthetic fixtures, no terminal/model calls
```

Channel 0 aggregates every displayed event. Each task has a channel, with separate `inbox:<name>`,
`board` and `landings` channels. New tasks and `events-*.jsonl` resume logs are discovered live.
Commands, exit results, edits, messages, gate summaries and runner output become short sentences.
The live view starts with eight sentences per file; `--replay N` overrides this. `--once` defaults to
all history. Codex events without timestamps show read time, so replay timestamps are approximate;
coordination and landing records retain their recorded timestamps.

Keys: **left/right** cycle channels, **0–9** jump, **PgUp/PgDn** scroll, **End** follows again,
**l** toggles the selected task's `last.md`, and **q** quits. Stars indicate unseen monitor output,
independently of persisted inbox acknowledgements. The selected channel remains visible in the bar
when there are more channels than fit the terminal. `last.md` refreshes when its contents change.

The task status line shows state, elapsed time for the current attempt, commits ahead of local `main`,
unread inbox messages, and reported tokens. A live attempt **over 30 minutes** displays `OVER 30m`
with a highlighted status bar; the aggregate bar also highlights overdue tasks. Completed elapsed time
stops at `finished`. Liveness uses the runner's PID plus process start time, including compatibility
with existing localized macOS timestamps. New identities use the C locale. Git counts refresh every
five seconds; missing repositories show `+?`. Poll interval defaults to one second (`--interval N`).

Tokens sum input plus output from every completed turn in all event files, including history hidden
by `--replay`. Cached input and reasoning output are subsets and are not added twice. If available,
the W2 ledger (`~/turku3d/ledger/codex-calls.jsonl`, override `TURKU3D_CODEX_LEDGER`) supplies task-job
totals deduplicated by `call_id` and excluding voided calls; event totals remain authoritative while
the ledger is behind.
The source is shown explicitly. Missing usage is marked `+ unknown`; an unfinished first turn can
show zero reported tokens. This is usage reported so far, not an estimate of an in-flight turn.
Board unread counts belong to `TURKU3D_SANDBOX` (or `orchestrator` outside a sandbox).

The monitor is strictly read-only: no locks, acknowledgements, state changes, Python bytecode files,
agent input or signals. Files are followed by byte offset; partial UTF-8/JSON lines wait for a newline,
and truncation/replacement resets the affected reader. Renamed event archives preserve their offsets.
It uses stdlib Python and the shared `sandbox.py`, `task_runner.py` and coordination helpers, without
calling quota APIs. Storage overrides are `TURKU3D_TASKS_ROOT` (or `--root`), `TURKU3D_COORD_ROOT`,
and `TURKU3D_WT_ROOT`; overriding `--root` alone changes only the task source.
Implementation and tests adapt the read-only gta6 watcher reference, dropping dispatch/Linear and
Claude-specific concepts. The registered `watch-tests` gate stage exercises the terminal-free Monitor.

### Coordination (agents and orchestrator)

```bash
# In a sandbox, the sender and default inbox come from TURKU3D_SANDBOX.
scripts/dev/codex-task inbox --unread
scripts/dev/codex-task inbox stairs --json
scripts/dev/codex-task msg stairs --kind request --subject "Stair layer API" \
  --ref docs/briefs/COMMON.md 'Please preserve world-local metres in the layer output.'
scripts/dev/codex-task ack <message-id> --note 'Updated the exporter and verified its output.'
scripts/dev/codex-task msg all --kind interface --subject "Layer API ready" \
  --ref <commit> --ref docs/briefs/COMMON.md 'The layer output contract is ready to use.'
scripts/dev/codex-task board
scripts/dev/codex-task board --since 1792886400 --json
scripts/dev/codex-task ls
scripts/dev/codex-task status stairs
```

Check the inbox and board after major steps/commits and before finishing; acknowledge what you
act on. `run` and `resume` inject these commands into the prompt. This is a polling channel:
messages do not interrupt a running `codex exec`. Before changing another area's interface,
send its running owner a `request`, or post to `all` when no owner is running. Announce schemas,
file layouts and module APIs that others depend on with an `interface` message and commit ref.

`msg` accepts `note`, `request`, `interface`, or `warning`; it prints the new message ID. `--ref`
is repeatable. The sender defaults to `$TURKU3D_SANDBOX`, or `orchestrator` when unset. Recipients
must be existing tasks/sandboxes, or the `orchestrator`/`main` mailboxes. `all` is the board.
Messages can arrive before a sandbox's first task run; `ls` includes these inbox-only entries.
The orchestrator can read any task's inbox, including acknowledgement notes, without switching
sandboxes. A worker can acknowledge its own inbox; `orchestrator`/`main` can acknowledge any inbox.
Board acknowledgements are per sender, so acknowledging an announcement does not clear it for
other agents. Repeating an acknowledgement is a no-op. Reading never marks a message as read.

Storage is append-only `~/turku3d/tasks/<name>/inbox.jsonl` and `~/turku3d/coord/board.jsonl`.
Messages have `{id, time, from, to, kind, subject, body, refs, ack: null}`. IDs are UUID hex strings;
`time` is Unix seconds. Acknowledgements use the same shape with `kind: "ack"`, `ack: <message-id>`,
and the optional note in `body`; the original message is never rewritten. Advisory sibling
`.lock` files serialize reads, writes and acknowledgements; appends are flushed to disk. A
malformed or incomplete log fails closed, rather than hiding unread messages or appending after
a partial record. Preserve the log and diagnose the reported path/line before retrying.

`inbox --json` and `board --json` return arrays of messages with `ack` resolved to the applicable
acknowledgement record (or null); raw JSONL retains every reader's acknowledgement for audit.
`--unread` filters the inbox to messages without an acknowledgement. `board --since N` returns
messages with `time > N` (non-negative Unix seconds, fractions allowed). `TURKU3D_COORD_ROOT`
overrides the board directory. By default it is `coord` beside `TURKU3D_TASKS_ROOT`, so overriding
the tasks root also isolates the board. No board records are copied into individual inboxes.

`ls` and `status` show inbox unread counts; finished/interrupted tasks with unread messages have
an `UNACKNOWLEDGED` marker. A `request` or `warning` sent after a task finishes also creates
"recipient finished; addressed by orchestrator" notices in the orchestrator and sender inboxes.
`land` refuses unacknowledged direct `request`/`warning` messages received before the task's
last turn started, both before the gate and under the inbox lock before updating main.
Later messages are listed in landing output without blocking.
Board messages and direct `note`/`interface` messages do not block landing. The orchestrator may
use `scripts/dev/codex-task land <name> --force` to override only this message check; it neither
acknowledges messages nor bypasses the result, clean-tree or green-gate requirements.

## Verification

The browser spec selector uses the same changed-path to subsystem map as the gate:

```bash
python3 scripts/dev/affected_browser.py --base main
python3 scripts/dev/affected_browser.py game/src/vehicle/vehicle.ts
```

The real-tile specs share one HTTP-caching browser context per worker, with a fresh page per test. Browser suites default to 960×540; pixel-detail and performance checks set their required viewports explicitly. The landing queue can also keep Chrome processes and one loaded tile page per backend in a dedicated service sandbox. The service writes loopback WebSocket endpoints to a mode-0600 state file; its owner stops the process with SIGTERM. Run the check against an integration worktree's built game and tiles, using that worktree's sandbox port and browser profile:

```bash
node scripts/dev/warm_browser.mjs serve --repo "$PWD" --work "$TURKU3D_WORK" --profile "$TURKU3D_BROWSER_DIR" --port "$TURKU3D_PORT_BASE"
node scripts/dev/warm_browser.mjs run --repo "$PWD" --work "$TURKU3D_WORK" --profile "$TURKU3D_BROWSER_DIR" --port "$TURKU3D_PORT_BASE" --service-state /path/to/service/work/cache/warm-browser.json --backend webgpu --spec tests/e2e-tile/tile.spec.ts
```

`run` returns JSON with the checked specs, result counts, elapsed seconds, and report path. Its `--service-state` file can live in a separate queue-service sandbox. The ordinary Playwright configs still launch their own Chrome when no warm endpoint is provided.

```bash
make tooling-test game-dev-tools-test
make -j 4 foundation-test game-test qa-test textures-test
make game-e2e
```

`tooling-test` runs up to TURKU3D_JOBS independent synthetic-repository tests at a time and covers concurrent port claims, adoption, shell-safe environment generation, fake run/resume,
quota refusal/override, process-group stop, landing success/refusals, removal and orientation from both checkouts.
Coordination coverage includes two sandbox environments exchanging requests and acknowledgements,
board announcements, concurrent CLI readers/writers, append-only/idempotent acknowledgements,
unread counts, landing refusal/override (including arrivals during the gate), and run/resume prompts.
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
The English-language policy from COMMON now lives in AGENTS.md. The gate maps tooling changes to
`tooling-tests` and gate changes to `gate-tests`. Changes to `tools/dev/environment.*` also select
game typechecks, browser/QA tests and dist-server tests. Landing calls `gate --base <main-sha>`.

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
The historical engine feasibility probes were subsequently retired by W0-B; their findings and archived
assets remain in the research documentation.

```bash
source ../sandbox.env
make -j 3 tooling-test foundation-test pipeline-test col-test game-test qa-test textures-test game-build game-dev-tools-test
make game-e2e
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

## Quality gate (W0-B)

Run from the repository root. The gate synchronizes changed Python/Node dependency manifests before stages run; it never fetches Git refs, downloads assets or calls paid/model services. Python 3.12+, Node 24+, shfmt and ShellCheck must be installed.
Initial native dependency setup:

```sh
make pipeline-bootstrap       # native GDAL/osmium CLI tools; setup only
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e 'pipeline[dev,fetch,lidar,buildings,region]' -e 'textures[post,ground,dev,streetview]' -r scripts/dev/requirements.txt
uv pip install --python .venv/bin/python --no-deps streetlevel==0.12.13
uv pip install --python .venv/bin/python -r textures/requirements-streetlevel.txt
npm ci --no-audit --no-fund
(cd game && npm ci --no-audit --no-fund)
(cd tools/validator && npm ci --no-audit --no-fund)
(cd qa/render && npm ci --no-audit --no-fund)
(cd textures/node && npm ci --no-audit --no-fund)
# QA renderer uses the locally installed Chrome; game e2e also uses Chrome.
scripts/dev/install-hooks
```

```sh
scripts/dev/gate                         # merge-base(main, HEAD) + index + working tree + new files
scripts/dev/gate --base HEAD             # only local changes
scripts/dev/gate --base main --list      # selection and explicit reasons, without executing
scripts/dev/gate --all                   # every stage, including both browser backends
scripts/dev/gate --json                  # JSON report, including command output and durations
```

Exit codes: **0 green, 1 red, 2 usage/invalid base**. Deleted paths still select their owning
stages. Ignored untracked files do not count. JSON goes to stdout with no progress chatter;
text mode prints each completed stage and all skip reasons, then total wall time.

`scripts/dev/gate.toml` is the single routing table. A stage has a name, kind, path triggers,
and either commands or a language runner. `always` stages check budgets, binaries, flag registry
and generated materials on every run. Changes to the gate routing/execution select the whole suite.
The two lint `configs` tables expand lint scope when its configuration changes. Python package
maps select dependent test files; an unmapped module conservatively selects its whole package.
Vitest uses `related --run`; deleted sources and dependency/config changes use the entire unit suite.
Validator Node tests remain Node tests, preserving their cross-language round-trip coverage.

Playwright paths are explicitly tagged in the table. Specs use `@walk`, `@drive`, `@render`,
`@qa`, `@input` and `@pipeline`; the table's `related.tags` selects the affected subsets. A new/unmapped
browser trigger selects all specs. Both WebGPU and WebGL2 run. The pipeline browser fixture publishes four synthetic ground layers with a deterministic RGB texture,
then runs the production assembler into `.cache/gate-tiles`. Its separate empty input root prevents
reading shared raw data. This exercises the four L0 seams as well as compression and rendering.
The long browser stage is deliberately excluded from ordinary pure-core/unit changes.

All caches live in this checkout: `.cache/ruff`, `.cache/eslint`, each TS package's
`.cache/typecheck.tsbuildinfo`, and package-local pytest caches. Temporary files live under `.cache/tmp`; browser
profiles use `$TURKU3D_BROWSER_DIR/tmp`. The game build uses `$TURKU3D_WORK/build/game`.
`TURKU3D_PORT_BASE` selects e2e port `base+1` (the two browser suites run sequentially);
QA HTTP tests use `base+2`, dev-server tests use `base+3` and `base+6` / `base+7`, and the two
Python fetch test workers use `base+4` / `base+5`. The main default base is 5170; parallel
sandboxes must set their allocated base. One positive integer `TURKU3D_JOBS` bounds pytest, Playwright, Vitest, validator fixture workers, Make and concurrent gate stages. The default is max(2, floor(logical cores / 3)); new sandbox.env files persist it. Numeric-library threads remain limited to one.
`GATE_PYTHON` can select a prepared Python environment instead of `.venv/bin/python`.
The table's `tool_dirs` adds the prepared native pipeline CLI directories to PATH, before the caller PATH, matching pipeline/env/run.sh. Missing native dependencies fail; only Python/Node dependencies are synchronized.

### Git hooks

`install-hooks` sets **worktree-local** `core.hooksPath=.githooks` using Git's worktree config;
sibling worktrees keep their own hook settings. Pre-commit runs only staged-file lint/format
(Python Ruff, JS/TS ESLint, shell shfmt/ShellCheck) in a temporary index snapshot. Partially
staged files are read from the index, never from the working copy. It does not run tests.
Pre-push reads Git's ref update list, checks each pushed commit snapshot against the remote
ancestor (new branches against main), and ignores deletion-only pushes. Unknown remote objects
fail with a diagnostic: fetch them explicitly before retrying. Hooks never fetch automatically.
Unrelated unstaged/untracked files cannot block either hook. Dependencies are linked into the
snapshot; caches, builds, reports and browser profiles stay isolated and are removed afterward.

### A/B flag contract

Default policy: no feature flags. A real temporary A/B experiment must use exactly one literal
name through `featureFlag('name')` (`game/src/core/flags.ts`) or `feature_flag("name")`
(`pipeline/turku3d/flags.py`). Do not alias these functions or construct names dynamically.
Only these adapters may read the reserved settings: browser `?ab.name=1`, Python
`TURKU3D_AB_NAME=1`. Other values are false. Backend selection, CLI options and material bitmasks
are capabilities/data, not experiments, and do not belong in this registry.

Add `[[flags]]` in `docs/flags.toml` with:

```toml
[[flags]]
name = "curb_solver"
owner = "name/team"
reason = "Compare measured curb impulse error between two implementations; remove the loser."
added_at_merge = 42
remove_after_merges = 3
```

Use `git rev-list --first-parent --count main` for `added_at_merge`. It must not be in the future.
The gate expires the flag when that count reaches `added_at_merge + remove_after_merges`;
the duration must be 1–10, and there can be at most five entries. Every entry must have a
production-code call and every call must have an entry. Tests do not keep flags alive.
Remove the losing implementation, flag call and registry entry together.

### Repository budgets and generated files

`budgets.lines` caps source lines by extension. Oversized sources must be split, or listed in
`budgets.exceptions` with a concrete reason (for example a vendored runtime). The binary cap is
512 KiB outside the explicitly allowed runtime asset directories. New untracked files are checked
too, so a new 2 MiB PNG cannot slip through. Research evidence is external, under
`~/turku3d/research/w0-gate/`; [the archive ledger](../../docs/research/ARCHIVED-ASSETS.md)
lists removed paths, byte sizes and historical Git blobs for the final squash.

Generated materials are compared byte-for-byte against the generator without writing files.
Regenerate intentionally with `PYTHONPATH=pipeline .venv/bin/python -m turku3d.col.materials --write`.
Update the generator when removing a dead generated export, then regenerate its outputs.

### Gate tests

```sh
.venv/bin/python -m unittest discover -s scripts/dev/tests -v
```

Disposable Git repos exercise selection, deletion, hook snapshots, usage errors and all six
required planted failures: unused export, unregistered/expired flag, 2 MiB PNG, Ruff and ESLint.
They use the real installed linters and knip; no mock-success lint commands are involved.
Tooling tests land a trivial documentation commit through the real production gate in a temporary
repository. An oversized PNG makes the real policy stage red and leaves that repository’s main unchanged.
The test runner accepts file patterns, for example:

```sh
.venv/bin/python scripts/dev/tests/run.py test_tooling.py test_environment.py
.venv/bin/python -m unittest scripts/dev/tests/test_merge_queue.py
```

### Frozen-main gate verification (2026-10-25)

Integrated W0-A tooling, W1-07 vehicles and W1-11 visual QA on main `58a85d0` (the final
addition after `3431e92` is the W0-D brief). All **19 gate stages passed in 139.799 s**.
The real-gate tooling suite passed 14 tests, including a trivial documentation landing,
a real oversized-binary rejection with main unchanged, and refusal when the gate is missing.
The project main branch was not modified.

Clean-tree samples used `scripts/dev/gate --base HEAD --json`, with each temporary edit
restored byte-for-byte afterward. Wall time includes process startup:

| Sample | File | Wall time |
|---|---|---:|
| Documentation | `README.md` | 0.688 s |
| Game core | `game/src/core/origin.ts` | 3.864 s |
| Pipeline fetch | `pipeline/turku3d/fetch/wfs.py` | 4.602 s |

The complete run passed 71 pipeline, 32 QA Python, 69 game unit, 7 QA render, 22 game
browser, 13 vehicle Node and 25 gate/tooling tests. Existing conditional skips remain:
one optional DeepBump test (not installed in this sandbox), and two multi-tile seam browser
tests (the offline gate builds one synthetic visual tile). No assertions or thresholds were relaxed.
JSON reports, the timing script and tooling output are retained under
`~/turku3d/wt/w0-gate/work/reports/w0-gate-integration/`.

## Gate speed and reproducible routing probes

`--paths` profiles an explicit repository-relative change without editing files. Normal gates,
pre-push, and landing still derive the complete diff from Git; routing edits still force the full gate.

```sh
source ../sandbox.env
scripts/dev/gate --paths docs/STATUS.md --json
scripts/dev/gate --paths pipeline/turku3d/package/manifest.py --json
scripts/dev/gate --paths game/src/render/renderer.ts --json
scripts/dev/gate --paths game/src/physics/worker.ts --json
scripts/dev/gate --paths qa/report/report.py --json
scripts/dev/gate --all
```

Package edits select Python package tests without unrelated browser, texture, or tooling suites.
Unmapped modules still select their full package. Runtime physics/shared collision changes select
walk/drive browser tags; rendering selects render/QA; input and vehicle specs select their own tags.
QA report edits run report generation, concurrent history, and the loop's failure/report integration.
The lidar, region and benchmark producer paths are reserved for their incoming branches.

Browser suites share one Vite build per gate and use TURKU3D_JOBS workers. Gameplay pages emulate focus
through Chrome's protocol so another window cannot suppress controller/keyboard input. Synthetic
browser tiles are reused only when producer sources, codec lockfile and numeric-library versions
match and all manifest hashes verify. The full tile acceptance command remains `make tile-validate`.
`npm --prefix game run test:all` builds once, then runs units and browser tests.

Validator fixtures use two independent WASM workers and 32 deterministic walkers in the gate;
the standalone fixture suite keeps 150, and real tile validation keeps its 2,000-walker default.
Every fixture expectation and assertion remains. Texture header/mipmap tests use a two-colour
128×128 input; image-quality tests retain their noisy fixtures. Independent texture encodes run
concurrently with identical encoding settings.

`stop` ignores zombies, treats ESRCH/EPERM as an exit race only when no original live identity
remains, and escalates by verified individual PID rather than a possibly recycled process group.
Real permission failures remain errors. `wt rm` checks cheap Git refusals first, scans cwd entries
separately, and inspects sockets only when IPv4/IPv6 wildcard port probes detect occupancy.
See [measured results](../../docs/reports/fix-gate-speed.md) for host-load limitations and timings.

## Load-robust gates and environment sync

### Host slots

`make refresh`, `make tile-build`, `make game-build`, the game, tile and render
Playwright suites, playtest, the gate's e2e stage, and landing's live build use
`scripts/dev/host_slots.py`.
The shared state is `~/turku3d/host-slots/` (override with `TURKU3D_SLOT_DIR` only
for synthetic tests). Up to three `build` jobs and two `browser` jobs run at once;
each build gets at most two `TURKU3D_JOBS`, and each browser suite one. A queued
`land` reservation stops new build/browser admissions, then holds exclusive capacity
through refresh, four-tile assembly, game build, collision checks, and Chrome. Because
nothing else runs, the reservation and every command nested in it get
`TURKU3D_JOBS` = all logical cores instead of the shared caps.
Commands started under a running `land` reservation (`TURKU3D_LAND_OWNER`) reuse it,
including nested `land` requests: the merge queue holds one reservation per round, and
the queue-run prechecks and their live builds run inside it instead of waiting on it.
Refresh divides its job budget between independent steps and passes it to Roofer.
Dead owner PIDs are removed on the next admission or status read. Waiting appears
in `codex-task ls` and `watch`, and the command prints when a slot is acquired.

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
scripts/dev/codex-task precheck example
scripts/dev/codex-task queue status
# Explicit budgets, on an unloaded machine:
TURKU3D_JOBS=1 make streaming-perf
TURKU3D_JOBS=1 make tooling-perf
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

Before DONE, workers commit and run `scripts/dev/codex-task precheck <name>`. It rebases on main,
runs the gate and every live build check in the worker sandbox, saves per-check input hashes in
`~/turku3d/tasks/<name>/precheck.json`, and prepares a release under
`$TURKU3D_DATA/live-releases/<tree-input-and-commit-hash>`. A rebase conflict returns to the worker.
The orchestrator starts `scripts/dev/codex-task queue run` once. The daemon polls DONE results.
When main still matches the receipt, it verifies the tree, checks and release bytes, fast-forwards
main, and swaps the live pointer without rebuilding. The landed ledger records merge wall seconds.
When main moved, it rebases and runs only invalidated cached gate/live stages under a 210-second
check deadline. Gate checks have a 120-second default queue budget; live stages have declared
30–150-second budgets. A slow check returns to its worker for a fix and new precheck. Broad
producer changes also return to the worker. Layer
mesh deltas select changed visual tiles, while texture and assembler changes require all tiles.
Disjoint affected domains may precheck cumulative prefixes in parallel with `precheck --base <SHA>`.
Conflicts and failed members return to their workers with route `task_rebase`; the queue keeps
other ready tasks moving. `queue status` shows task timestamps and median DONE-to-live.
Unread messages do not block the queue. The queue uses
`wt/.registry/merge-queue.lock` for single-instance ownership and persists its ledger in
`wt/.registry/merge-queue.json`.
When the warm browser tool is installed on main, the queue creates a dedicated
`merge-queue-service` sandbox and starts its two Chrome backends once. It waits for both
preloaded pages before admitting warm checks. Each candidate's
affected tile specs run through those endpoints on the candidate sandbox ports; a missing
service or selector uses the full local Chrome suite. The queue stops only the service
process it started when it exits. `queue status` remains read-only.

To publish one prechecked task without starting the daemon (for example an orchestrator
integration sandbox), run the same fast path directly. `--dry-run` runs every receipt,
release-byte, main and guard check and prints the head and release without moving anything:

```sh
scripts/dev/codex-task land <name> --prechecked --dry-run
scripts/dev/codex-task land <name> --prechecked
```

The old `codex-task land` command remains for recovery but is no longer the normal path.
The orchestrator's `land.sh` and `land-quiet.sh` wrappers are unnecessary.
The old batch integration helper is retained only for recovery. The queue publishes individual
verified prefixes and records each successful member in `landed.jsonl`.
After each pointer swap, `queue_sweep.py` checks the full gate and live area from its own sandbox
in the background. Its status is in `wt/.registry/queue-sweep.json` and `queue status`; a red
sweep creates a fix task and holds queue members touching the changed source domain until a
green sweep clears it. The sweep does not delay publication of the verified prefix.

Functional browser checks wait for movement, input, grounding, camera or physics state;
button edges wait for rendered frames. Wall-clock streaming, orientation, docs-gate and QA
response budgets are explicit perf tests outside the functional gate.

## Codex token ledger

`codex-task` records each completed/failed turn while streaming events, with distinct identities
across resumes. Texture attempts and QA judge attempts use the same append-only ledger at
`~/turku3d/ledger/codex-calls.jsonl`; tests set `TURKU3D_CODEX_LEDGER` to an isolated file.
Model and effort defaults come from `textures/codex-routing.toml`; explicit task flags override them.

```sh
source ../sandbox.env
scripts/dev/codex-cost --by job_type
scripts/dev/codex-cost --by model --json
scripts/dev/codex-cost --by task --json
scripts/dev/codex-cost --by day
scripts/dev/codex-cost --backfill ~/turku3d/tasks \
  ~/turku3d/wt/w1-09-facades/work/textures ~/turku3d/wt/w1-11-visualqa/work/qa
```

Backfill is idempotent and reads its sources without modifying them. Historical task wall time,
legacy effort and absent usage remain unknown. Structured texture attempts/out and QA attempts
are deduplicated by thread/turn; raw task events use file identity plus terminal-turn ordinal.
Old overwritten retries cannot be recovered unless an attempts record survives.
`total_tokens = input_tokens + output_tokens`; cached input and reasoning output are subsets.
Per-facade/view costs use only attributable calls, include retries in cost, and count a logical
job's units once. These units describe jobs, not globally unique physical facade identities.
The JSON report includes unknown counters; never interpret missing evidence as free work.
No token-to-weekly-percentage conversion is claimed.

Append-only corrections have `record_type: "void"` and `voids: "<call_id>"`; aggregate readers
must exclude both the annotation and its referenced call. `codex-cost` applies these annotations.

### Model routing for task turns

Development tasks use `task` (gpt-6-sol, medium). Use the light route explicitly for
rebase, verification or finish-only work; ordinary resumes retain their previous selection.
The resolved model, effort and route are persisted in task metadata and each ledger turn.

```sh
scripts/dev/codex-task run example --brief docs/briefs/example.md --route task
scripts/dev/codex-task resume example "Rebase, verify and finish" --route task_rebase
scripts/dev/codex-task resume example "Verify only" --route light --model gpt-6-sol --effort low
```

Precedence: explicit `--model` / `--effort`, then `TURKU3D_CODEX_MODEL` /
`TURKU3D_CODEX_EFFORT`, then `textures/codex-routing.toml`. `TURKU3D_CODEX_TASK_ROUTE`
selects the task route when `--route` is omitted. `TURKU3D_CODEX_ROUTING` selects an
alternate TOML file. Explicit route changes reset inherited model/effort to that route's
configuration. Environment model/effort overrides also apply to texture and QA runners.
Set `TURKU3D_JOBS=3` to bound tooling tests to three workers; the shared resource helper
controls the default and all worker pools.
