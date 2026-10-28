# Developer tools

Python 3.10+, Git, `lsof`, Node and the authenticated Codex CLI are required. No services, API keys or Python
packages are needed for these commands. Policy: [AGENTS.md](../../AGENTS.md).

## Automation browser

Use the pinned Chrome for Testing executable for all project automation.
Install the pinned stable macOS ARM64 release once:

```bash
mkdir -p "$TURKU3D_WORK/cache" "$HOME/turku3d/browsers/156.0.8078.73"
curl -fL --retry 2 https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.73/mac-arm64/chrome-mac-arm64.zip -o "$TURKU3D_WORK/cache/chrome-mac-arm64.zip"
ditto -x -k "$TURKU3D_WORK/cache/chrome-mac-arm64.zip" "$HOME/turku3d/browsers/156.0.8078.73"
```

[Override, launch contract and verification](browser.md).
The fast gate rejects desktop Chrome launch paths and channels.

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
`live` status. It reports the shared streetlevel cache separately and flags any sandbox
`work/cache` above 5 GiB. `prune-landed` applies the ordinary `wt rm` checks to each clean, merged,
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
scripts/dev/codex-task ls                    # running first, then ten recent stopped tasks
scripts/dev/codex-task ls --running          # live tasks only (PID/start identity checked)
scripts/dev/codex-task ls --all              # every task, still compact
scripts/dev/codex-task status stairs
scripts/dev/codex-task log stairs 30
scripts/dev/codex-task result stairs
scripts/dev/codex-task resume stairs --reason unblock 'Fix the failing seam test and rerun the gate.'
scripts/dev/codex-task stop stairs
scripts/dev/codex-task land stairs          # land a recorded DONE result again (same as finish)
scripts/dev/codex-task adopt ext --brief ~/turku3d/briefs/ext.md   # a non-Codex worker's sandbox
scripts/dev/codex-task finish ext --result result.md [--keep-sandbox] [--note "..."]   # lands
```

Use an actual brief path from `docs/briefs/`. `run` creates the sandbox if absent, exports its environment and
starts `codex exec -m gpt-6-astra --sandbox danger-full-access -C <repo>` in a new process group. It returns
immediately. The prompt contains COMMON plus the brief. `resume` uses the recorded `thread.started` session ID,
not the most recent global session. The model and effort remain the same. Codex is responsible for committing work.
`run` reports free disk and shared streetlevel cache size, and refuses to create a sandbox
below 30 GiB free on the sandbox filesystem. Check with
`scripts/dev/wt du`, then remove safe candidates with `scripts/dev/wt prune-landed`.
CLI behavior is checked against the installed CLI and [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

`ls` reads metadata and at most the last 256 KiB of the current event log for each displayed task;
it does not invoke detailed `status`, Git, quota readers or scheduler locks per task. It lists all
live tasks first, then ten recent stopped/inbox tasks, with an omitted count. `--all` lists every
task; `--running` shows only live starting/running attempts. Both use the same compact rows.
Use `status <task>` for commit, unread inbox and browser-budget details.

`resume --reason` names why a stopped task continues: `unblock`, `review`, `main-moved` (after
`landed` messages), `quota`, `follow-up` (default) or `other`. It appends
`{"type": "task.resumed", "reason", "gap_seconds", "time"}` to `events.jsonl`, where the gap counts from the
last `turn.completed`; the worker stamps `time` on each `turn.completed` and `turn.failed`, and
older unstamped logs fall back to the runner exit time. `ls` and `status` show `idle N min` since the recorded worker finish for stopped tasks.
`board` derives idle time from the last turn end and lists only
unlanded tasks whose sandbox still exists. This attributes the 27.2 h between turns measured in
`docs/reports/dev-overhead-study.md` §1.3.

`~/turku3d/tasks/<name>/` holds `prompt.md`, append-only `events.jsonl`, `last.md`, `pid`, `meta.json`,
`runner.log`, follow-up prompts, and `gate.log` after landing. `status` detects interrupted processes and reports
elapsed time, last event and commit. `stop` verifies the supervisor process identity before signalling its group:
TERM, a three-second grace period, then KILL if necessary. No global process-name matching is used.
`stop` also reaps sandbox Chrome profiles; landing reaps them before verification and cleanup.

The quota guard reuses `textures/turkutex/codex/usage.py` (`codex app-server` JSON-RPC, no model call).
It refuses when any usage window reaches 100%, or usage is unknown. `run/resume --force` bypasses a known 100%
reading only. Tests use a fake `codex` on PATH and synthetic repositories; no real calls occur in `tooling-test`.

### Merge into main and deploy (`codex-task finish`)

Use `codex-task agents` for the single active-agent/branch register. Commit often;
git contains the details. Give a short high-level heads-up for a shared format,
data structure or code another active agent is changing. There is no overlap planner.

```sh
source ../sandbox.env
scripts/dev/codex-task agents
scripts/dev/codex-task checks --last 10
scripts/dev/codex-task finish NAME --result "$TURKU3D_WORK/reports/result.md" \
  --keep-sandbox \
  --test 'python3 scripts/dev/tests/run.py test_delivery.py test_pages.py' \
  --async-test 'python3 scripts/dev/tests/run.py test_tooling.py test_caches.py' \
  --async-reason 'Delivery changed; these broader cases were not covered recently'
```

These are examples for delivery changes; pick affected commands and grep filters for
your change. All `--test` commands share a hard 30-second wall deadline, in parallel with the
mandatory uncached repository-wide `policy` and `chrome-launch-policy` stages.
These two stages also run when `--no-tests-reason` is selected.
Use `--no-tests-reason TEXT` when there are no affected quick tests. `--heads-up TEXT`
sends the shared-interface note before rebase. The quick suite stays tiny; new tests
need a strong reason.

Finish rebases, runs the selected checks, merges into main, announces the merge,
then packages/uploads the game to Pages immediately. No test or tile build runs in
deployment, and there is no deploy lock, queue, batch, bisection or rollback. Public
source publication follows Pages rather than delaying it. A git push race is reported
without an automatic publication retry. Pages already has its hosting configuration.

After deployment, curl smoke probes have a five-second total wall budget, no browser.
One agent-owned selective async run starts from frozen deployed source; it has ten
minutes including bulk-slot waits. `checks` shows recent commands and outcomes so the
agent can avoid repeating other agents' evidence. A failing run permits one forward
fix through `finish --fix-for JOB`; later failures go to the orchestrator. Results
live under the task's `checks/` directory and survive sandbox removal.

The register is `wt/.registry/agents.json`, refreshed by `codex-task agents` and lifecycle
changes. Merge events retain the historical `landed.jsonl` filename for current
clients; deployments and test outcomes are in `deployments.jsonl` and `checks.jsonl`.
There is no integrator process or verified-only release selection. The game world is
generated on demand; facade assets and generator source are published independently
of the game bundle. Details: [AGENTS.md](../../AGENTS.md), [verification.md](verification.md).

### Long commands (agents)

Run anything that can take longer than 30 s as one blocking call instead of polling it:

```bash
scripts/dev/codex-task wait-cmd -- scripts/dev/gate
scripts/dev/codex-task wait-cmd --tail 80 -- scripts/dev/codex-task finish ext --result result.md
scripts/dev/codex-task wait-cmd --timeout 1800 -- make tile-build T='t0_0_0'
scripts/dev/codex-task wait-cmd -- make game-e2e
```

`wait-cmd` runs the command with the sandbox environment (`$TURKU3D_SANDBOX`'s `sandbox.env`
overlaid on the caller's), in its own process group, from the current directory. Output streams
to `~/turku3d/tasks/<TURKU3D_SANDBOX>/wait-cmd.log`, or `$TURKU3D_WORK/wait-cmd.log` when the
sandbox has no task, and the file is rewritten by each call, so `tail -f` it to watch a run. It
returns only when the command exits, prints the last `--tail` lines (default 40) and
`wait-cmd: exit <code> after <s>s; full log: <path>`, and exits with the command's code.
`--timeout S` terminates the group after S seconds and exits 124. Leftover descendants are
terminated at exit, as for gate stages. If you poll a backgrounded command instead, use
`write_stdin` with `yield_time_ms: 30000`; never poll at 1000 ms. The transcript study
(`docs/reports/dev-overhead-study.md`) measured 3,188 one-second polls and 28 % of input
tokens spent on polling.

### Live agent monitor

```bash
scripts/dev/watch                         # curses channels for all tasks
scripts/dev/watch w1-01-ground tool-watch # restrict task/inbox channels
scripts/dev/watch --plain                 # stream human sentences; also automatic for pipes
scripts/dev/watch --plain --once          # replay recent task/coordination events and exit
scripts/dev/watch --plain --once --replay 10 # limit startup sentences per file
python3 scripts/dev/tests/test_watch.py   # fast synthetic fixtures, no terminal/model calls
```

The monitor replays recent events and exits with `--once`. Default startup is eight sentences
per file from bounded tails; live mode follows new tasks and appended events every second.
It reads scheduler state without locks or writes. See [monitor details](watch.md) for navigation,
read-only guarantees, token/unread count limits and storage overrides.

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

`run` and `resume` attach `scripts/dev/inbox_hook.py` to Codex. Tool calls reveal requests and warnings
in full (repeated after 15 minutes), notes and interfaces by subject, and landings with a rebase reminder.
Stop blocks once for unread urgent messages; hook errors are logged. Check the board after commits and
before finishing; acknowledge messages you act on. Manual inbox checks remain available. Before changing another area's interface, send its running owner a `request`, or post to `all` when no owner is running. Announce schemas,
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
No message holds a landing: `finish`/`land` list open requests and warnings and the worker decides.

## Verification

Dated verification logs from 2026-10-25 are in `docs/reports/dev-tooling-verification-2026-10-25.md`.

The real-tile specs share one HTTP-caching browser context per worker, with a fresh page per test. Browser suites default to 960×540; pixel-detail and performance checks set their required viewports explicitly.

```bash
make tooling-test game-dev-tools-test
make -j 4 foundation-test game-test qa-test textures-test
make game-e2e
```

`tooling-test` runs up to TURKU3D_JOBS independent synthetic-repository tests at a time and covers concurrent port claims, adoption, shell-safe environment generation, fake run/resume,
quota refusal/override, process-group stop, removal and orientation from both checkouts.
`test_delivery.py` and `test_pages.py` cover the new boundaries: hard deadlines,
explicit selection, merge safety, game-only packaging, a real bare-remote upload
and push-race failure without retry or rollback. The old batch/verified-release/
rollback test suites were deleted with the retired implementation.
Coordination coverage includes two sandbox environments exchanging requests and acknowledgements,
board announcements, concurrent CLI readers/writers, append-only/idempotent acknowledgements,
unread counts and run/resume prompts.
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
# Install the pinned Chrome for Testing release above for QA and game e2e.
scripts/dev/install-hooks
```

```sh
scripts/dev/gate                         # merge-base(main, HEAD) + index + working tree + new files
scripts/dev/gate --base HEAD             # only local changes
scripts/dev/gate --base main --list      # selection, reasons, browser pick and its cost, no run
scripts/dev/gate --all                   # every stage, every spec on both browser backends
scripts/dev/gate --fast --base main      # the lander's pre-merge gate: selective, no browser or deep stages
scripts/dev/gate --fast --base main --list   # what the lander would run, and why each stage is skipped
scripts/dev/gate --deep --base main      # the deep tier for your changes (= make gate-deep; ALL=1 for --all)
scripts/dev/gate --json                  # JSON report, including command output and durations
```

`--fast` (an optional manually selected check) runs lint and format, type checks, policy
and the unit-test stages whose full run stays under 20 s; target under 30 s at host load 100, about
2 s when cached. It skips browser stages (`kind = "e2e"`, `browser = true`; a gate test fails if a
stage that starts Playwright or Chrome is not marked) and the deep tier: stages with `tier = "deep"`
(`pipeline-tests`, `textures-tests`, `gate-tests`, `tooling-tests`,
`watch-tests`, `knip`, `validator-tests`, `dependency-imports`). `--deep` runs only deep stages;
selected async commands may include relevant deep cases; failures are forward only. A stage whose full run exceeds 20 s moves to the deep tier.
`full_triggers` are only the gate's own `gate.toml`, `gate_lib.py`, `gate_fixtures.py` and
`gate_cache.py`; they widen the selection to every stage of the chosen tier.

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

`game-e2e` runs a browser pick, by default the selector's proposal (`spec_selection.select`: related rows
and spec `@tags`) with its estimated browser-minutes; `--specs smoke.spec.ts --project webgl` picks yourself.
Each task's budget (`[browser] budget_minutes` = 5, or a brief assignment `BROWSER_BUDGET_MIN = N`, on its own line or after prose) is debited
by every `host_slots.py browser` run (wall s x workers) in `~/turku3d/tasks/<task>/browser-budget.json`
(`codex-task status`; ledger `browser_minutes`); a spent budget refuses further runs. The pipeline browser fixture publishes four synthetic ground layers with a deterministic RGB texture,
then runs the production assembler into `.cache/gate-tiles`. Its separate empty input root prevents
reading shared raw data. This exercises the four L0 seams as well as compression and rendering.

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

`budgets.lines` caps source lines by extension. Oversized sources must be split, or listed in `budgets.exceptions`
with a concrete reason (for example a vendored runtime). The binary cap is 512 KiB outside the explicitly allowed
runtime asset directories. New untracked files are checked too, so a new 2 MiB PNG cannot slip through. Research
evidence is external, under `~/turku3d/research/w0-gate/`; [the archive
ledger](../../docs/research/ARCHIVED-ASSETS.md) lists removed paths, byte sizes and historical Git blobs for the final
squash. Landings publish main's tree to the public GitHub repo, so the policy stage (`checks.check_published_images`)
fails on an image (`.png .jpg .jpeg .webp .heic .gif`) under `docs/`, `textures/`, `qa/`, `game/public/` or a `tests/`
directory whose path, sidecar (`<image>.json`, `<stem>.txt|.toml|.yaml`) or Markdown alt text has a street-imagery
marker (`streetview`, `street-view`, `lookaround`, `look-around`, `mapillary`, `streetside`, `pano`, `contact-`,
`reference`, `refs/`; `_` and spaces count as `-`) or that exceeds 512 KiB, unless `docs/published-images.toml` lists
it with `path`, `source`, `licence` and a one-line `reason`. Entries must exist; one naming Google, Street View,
Apple, Look Around, Mapillary, Bing or Streetside as source or licence fails. Street-level imagery and raw city data
stay local.

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
Use `SERIAL = True` to isolate shared API mocks (e.g. stop signals). File patterns:

```sh
.venv/bin/python scripts/dev/tests/run.py test_tooling.py test_environment.py
python3 -m unittest scripts/dev/tests/test_delivery.py scripts/dev/tests/test_pages.py
```

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
Unmapped modules still select their full package.
QA report edits run report generation, concurrent history, and the loop's failure/report integration.

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

## Verification and host capacity

See [verification.md](verification.md) for focused checks, host slots, budgets,
environment sync and stage caches. Normal integration runs no Chrome or deep gate;
`finish --async-test COMMAND` selects a bounded investigation.

### Direct delivery receipts

`codex-task finish` records selected command results, merge time, package/upload
metrics, five-second smoke and the async job path. It accepts a running agent's own
slice and keeps that checkout active. A DONE turn-end compatibility call uses the
recorded selection; a new change requires a new explicit selection. Rebase conflicts
and quick-test failures leave main unchanged; failures after merge are forward only.

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

For tile-only publication, see [localhost commands](../../tools/dev/localhost/README.md). Cache and release cleanup: [disk retention](DISK_RETENTION.md).

Waiting build jobs are admitted FIFO; existing resource limits and integration headroom apply.
Restart an already waiting scheduler after updating its code to use the new admission rule.

Private paired draft measurements: [draft-reference.md](draft-reference.md).

Concurrent `wait-cmd` calls use distinct timestamp/PID log files under the task's
`wait-cmd/` directory. `wait-cmd-latest.log` is an atomic latest pointer; the old
fixed `wait-cmd.log` is not reused, so older running clients cannot truncate a new log.
