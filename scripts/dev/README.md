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
scripts/dev/codex-task ls
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

`resume --reason` names why a stopped task continues: `unblock`, `review`, `main-moved` (after
`landed` messages), `quota`, `follow-up` (default) or `other`. It appends
`{"type": "task.resumed", "reason", "gap_seconds", "time"}` to `events.jsonl`, where the gap counts from the
last `turn.completed`; the worker stamps `time` on each `turn.completed` and `turn.failed`, and
older unstamped logs fall back to the runner exit time. `ls`, `status` and `board` show
`idle N min` since the last turn ended for tasks that are not running; `board` lists only
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

### Landing (`codex-task finish`, `codex-task land`, `landing.land`)

Workers land their own green slices. `finish <task> --result <file>` (external workers) validates the
exact `## Result: DONE` line, records the result (`last.md`, `state: done`, `finished`) and lands; a Codex
runner whose turn ends with a DONE result runs `codex-task land <task>` (main's current lander, log
`tasks/<task>/land.log`); `land` is the same operation for the orchestrator and is a no-op for a result
already landed. Landings are serialised with `wt/.registry/land.lock` (a second finish waits seconds; the
row records `lock_wait_seconds`). The lander needs a clean worker and main worktree on `wt/<task>`, then
runs `git rebase main` in the sandbox. A conflict aborts the rebase and exits non-zero listing the
conflicting files; nothing else changes (no inbox message, no state change): the worker rebases,
resolves, reruns the gate and finishes again. It then runs `scripts/dev/gate --fast --base <main>` in the
sandbox environment (output in `gate.log`); red exits non-zero naming the failing stage, host load and
log, main unchanged. Then `git merge --ff-only` in the main checkout (a compare-and-swap `git update-ref`
when main has no checkout). If main moved during the gate it rebases and gates again, at most three
times. Open requests and warnings in the task inbox are listed in the output and do not hold the
landing. After the merge the lander sends a `landed` message (below), starts the integrator when none
runs (`integrator.ensure`) and removes the sandbox; `--keep-sandbox` keeps it for a task that continues
in a next slice (an external worker becomes `state: external` again). No host slot, live build or
receipt.

**Broadcast.** Every landing sends a `landed` inbox message to every other task that is running or an
active external worker (`state: external`), never to finished ones: commit, task, subject, class,
changed paths (20 shown), the `interface` messages the task posted on the board since its previous
landing (ids in `refs` after the commit) and the `--note` line. An integrator revert sends one too.
`landed` messages need no ack and hold nothing; `inbox --unread` lists one until the task's checkout
contains its commit, and `status`/`ls` show `behind main by N landings since your last rebase`
(landed.jsonl rows on main that HEAD lacks).

The whole landing has a hard budget of 180 s (`landing.BUDGET_SECONDS`) from taking `land.lock`; the gate's process group is terminated at the
deadline. Over budget the landing raises `merge_slow`, records `merge_slow` (seconds, step) in `meta.json`, and leaves
main unchanged. Each landing appends one row per task to `wt/.registry/landed.jsonl`: `name`, `before`, `commit` (the
new main head), `source`, `time`, `merge_wall_seconds`, `merge_slow`, `fast_gate_seconds`, `fast_gate_stages`,
`first_done_time` (the first `finish`, kept in `meta.first_finished` and never overwritten), `done_time` and
`facade_regression_reason` (from the result's `Facade regression reason:` line; the integrator reads it from here),
`lock_wait_seconds`, `browser_minutes`, the landing class fields (`class`, `paths` capped at 40, `changed_paths`,
`data_delta`, `tiles`, `data_parts`; see "Landing classes" below). The sandbox removal reports the bytes freed;
a retained sandbox prints the reason; remove it later with `scripts/dev/wt rm <name>` or `scripts/dev/wt
prune-landed`. Then `push_public` publishes to `origin` (github.com/Wnt/turku3d) best-effort within 60 s: main's tree
becomes one commit on `refs/public/main`, which began as a squashed import (`docs/HISTORY.md`), and that line is
pushed to origin's `main`. Local `main` is never pushed. The row records `push` (`commit`, `seconds`) or `push:
{error}`.

### Live build (`live_build.build`; tile selection in `live_layers.py`)

Live and Pages publish as soon as the build passes: code/full batches run `game-build` with the served tiles;
data batches run refresh, tile-build and format/seam validation, carrying the live game. Only after live and Pages
are published do selected or scheduled full Chrome, collision/walk checks, car reports, facade ground audits and
(code lane) the deep gate run in that build's checkout. Results update the exact release record, ledger advisory
list, inbox warnings and Pages commit notes (a notes-only update protected against newer deployments).
A newer landing cancels advisory command groups and records `advisory_interrupted`; interrupted full Chrome runs
are not marked complete. `advisory_chrome.py` owns Playwright and its dist server in one timed group; stale server
cleanup requires matching PID, birth identity and group in `$TURKU3D_WORK/cache/advisory-chrome-server.json`; `wall_seconds` includes advisory time.

**Build stages and advisory stages** (user decision 2026-10-27 21:00). Only `live_build.BUILD_STAGES`
(refresh, tile-build, tile-validate, game-build) and the manifest check (geometry, ground textures and < 8 MB
on the wire for every listed tile, a spawn) raise `StageFailed`: the batch cannot be built. Of tile-validate's
checks only `format` and `seams` block (`BLOCKING_CHECKS`; read from `reports/validate-aoi.json`). Everything else
is advisory: the car report (failing cars are an accepted known red, validator errors are advisory), the facade
ground audit, tile-validate's `buildings`/`curbs`, the facade baseline
(`facade-baseline`: fewer `facades_bound` than the published release with no `Facade regression reason:` line in
a batch landing's ledger row) and Chrome. Each failure is appended to `report["advisory"]` (`stage`, `detail`,
`log`, Chrome `specs`, tile-validate `checks`), publication stands, and a failed advisory stage is not cached.
`stages_for(kind, checks=False)` runs the build stages only (base check and bisection builds). Every 2 h with a
landing since the last full run (or `integrator.py run --full`) all specs run on both projects on live
(`full_suite: true`, `integration_chrome.py`), as information. Stage times and output are in `live-build.log`.
Each gate and live-build command runs in its own process group, which is terminated on exit.
Failures name the failing stage or test and include the host load and log path. For a Playwright
suite the detail is the first failing test and its first error, so a test timeout reads as
`Test timeout of ... exceeded` rather than the secondary `Target page ... has been closed`.
Each Chrome stage also records `browser_load` in `live-build.json` and the log: the 1/5/15-minute
host load at its start and end, the median and maximum per-test frame p95, and every load retry.
A failed Chrome stage prints the same summary, so a failure under load is distinguishable from a
regression at a glance. Browser specs wait on game time and retry once only above host load 40
(see `game/README.md`, "Character locomotion and verification").
For the first landing, the baseline comes from the legacy `deploy-once.log` assembler result (164 on
2026-10-26); later landings read the previous release's `live-build.json`.

Publication APFS-copies the built game, tiles, layers and textures into
`$TURKU3D_DATA/live-releases/<commit>`; one atomic `$TURKU3D_DATA/live` symlink
swap publishes the release. Stable `$TURKU3D_DATA/build/game` and `dist/tiles` paths resolve through
that link, so static servers on them need no restart (:5171 runs main hot: `tools/dev/localhost/README.md`).
Preparation dereferences sandbox links and rejects any link left inside a release. Publication checks
all four stable paths after the swap and restores the previous `live` link if a path does not resolve
to its directory in the prepared release.
`~/turku3d/logs/live-version.txt` records the published commit; `$TURKU3D_DATA/tile-quality.json` its tiles. Do not run `deploy-once.sh`
or `autodeploy.sh` on main; `make refresh` refuses the published data root. Run refresh in a sandbox.
Do not update main outside the lander while landing. A missing gate fails closed; there is no
legacy Make fallback.

### GitHub Pages (`publish-pages`)

https://wnt.github.io/turku-game/ serves main's game with the served tiles. With `$TURKU3D_DATA/publish/pages.json`
(`{"repo": "Wnt/turku-game", "branch": "gh-pages", "region": {...}}`) the integrator calls `pages.publish` after
every publication (inert batches only when `docs/published-images.toml` changed): one parentless commit of the
release's `game/`, its `tiles/` filtered to the `region` (`pages_region.py`: listed tiles near the centre that meet
the quality thresholds of the release's tile ledger, `turku3d.region quality`) and `pages-site/` is force-pushed
(one release, about 150 MB) and Pages is enabled once. The commit message is the release notes: the commits since
the previous Pages release (`publish/pages-last.json`, else the record's `from`), the advisory failures the release
shipped with and the shipped and excluded tiles with the reasons. The result or `error` goes into the integration
row as `pages`; a failure never rolls back the local publish. It refuses a release binding a facade map that is not
generated-style, derived or fast (`pages.generated`). By hand: `scripts/dev/codex-task wait-cmd --
scripts/dev/publish-pages` (`--from-served`, the default: live as it is when it already is main's game on the served
tiles, else main's `make game-build` in the `pages-served` sandbox composed with them by `served.compose`;
`total_seconds` in the output) or `--release DIR`; `--plan` prints the selection and publishes nothing. The game
build uses a relative base, so it runs under `/turku-game/` without COOP/COEP.

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
`scripts/dev/tests/test_lander.py` (in `tooling-tests`) covers the lander on synthetic repositories:
a branch lands after main moved in under 5 s without importing the live builder; slices with
`--keep-sandbox` keep `first_done_time`; open requests are reported, not held; DONE/dirty/red-gate/
missing-gate refusals, main moving during the gate, data-delta attribution and the `merge_slow` budget.
`test_finish.py` covers the conflict stop, the `landed` broadcast as a second task receives it, the
Codex turn-end landing and two concurrent finishes serialised on `land.lock`.
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
# QA renderer uses the locally installed Chrome; game e2e also uses Chrome.
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

`--fast` (workers before `finish`, the lander after rebasing) runs lint and format, type checks, policy
and the unit-test stages whose full run stays under 20 s; target under 30 s at host load 100, about
2 s when cached. It skips browser stages (`kind = "e2e"`, `browser = true`; a gate test fails if a
stage that starts Playwright or Chrome is not marked) and the deep tier: stages with `tier = "deep"`
(`pipeline-tests`, `textures-tests`, `gate-tests`, `tooling-tests`, `integrator-tests`,
`watch-tests`, `knip`, `validator-tests`, `dependency-imports`). `--deep` runs only deep stages;
the integrator runs `gate --deep --all` (cached per stage input hash) after it publishes a code
batch; red stages are advisory (`deep-gate` with the failing stage names in the ledger row, warned
to the batch's authors when new). A stage whose full run exceeds 20 s moves to the deep tier.
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
Each task's budget (`[browser] budget_minutes` = 5, or a brief line `BROWSER_BUDGET_MIN = N`) is debited
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
.venv/bin/python -m unittest scripts/dev/tests/test_integrator.py scripts/dev/tests/test_advisory.py
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

## Load-robust gates and environment sync

### Host slots

`make refresh`, `make tile-build`, `make game-build`, the game, tile and render
Playwright suites, playtest, the gate's e2e stage and the integrator's live build use
`scripts/dev/host_slots.py`. The shared state is `~/turku3d/host-slots/` (override with
`TURKU3D_SLOT_DIR` only for synthetic tests).

Admission is budgeted, not exclusive. The host has 10 CPU job units and 6 browser (GPU)
workers (`BUDGET`). A `build` costs 2 job units (at most three at once, each with
`TURKU3D_JOBS` ≤ 2), a `browser` suite one GPU worker (one agent suite at a time, `TURKU3D_JOBS` = 1), the
single `integration` reservation (the integrator's code lane) 6 job units plus 4 browser
workers, and the single `integration-data` reservation (its data lane, no Chrome; stored as kind
`integration` with `lane`, so older tooling counts it) 4 job units (`COST`). Builds and browser suites keep being admitted beside a running integration within the
remaining capacity. A waiting `integration` is not starved: later admissions are taken only if
it still fits after them, so at most the builds already running finish first. The integration
and every build or browser command nested in it (`TURKU3D_INTEGRATION_OWNER`) use its lane's
budget: `TURKU3D_JOBS` = 6 (code) or 4 (data) for builds. Nested commands are not admissions.
Refresh divides its job budget between independent steps and passes it to Roofer.
Dead owner PIDs are removed on the next admission or status read. Waiting appears
in `codex-task ls` and `watch`, and the command prints when a slot is acquired.

Browser share: the integrator's Chrome stage gets 4 workers while the 1-minute load average is
under 60 (`BROWSER_LOAD`) and 2 above it (`browser_workers`; the same 32 tests took 251 s at load
50 and 1086 s at load 150–250; ten workers timed out). While it runs, `host_slots.phase("chrome")`
marks the integration entry (`phase: chrome` in `state.json`) and agents' `browser` admissions
wait behind it; suites already running finish. They are admitted again when the stage ends.

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

### Landing and integration

Main only requires the fast gate; the live build and Chrome run once per batch after the merge.
Live and Pages point to a release built from exactly the commit it names; its advisory results ride along.

```
worker: commit → gate --fast → codex-task finish (no precheck, no polling, no daemon)
lander (in finish, serial under land.lock, seconds, no host slot): validate → rebase (conflict →
        stop) → gate --fast → git merge --ff-only → landed.jsonl row → `landed` message → ensure
integrator code lane (asynchronous, one run at a time, `integration` host reservation):
        batch = landings from the published commit G up to main M
        class by paths (landing_class.classify): inert → live_build.repoint(M), no build, no slot
        else → live_build.build(M, code): game-build + served tiles; joins only while free
        builds: publish(M) under publish.lock, recomposed on a data-lane release published meanwhile
               → Pages → Chrome/audits/deep gate (cancelled for newer landings) → new advisory failures: warning to the batch's authors
        build red: base check: live_build.build(G, build stages)
               G red:   data-red, revert nothing, warning to all, live stays at G until main moves
               G green: bisect G..M (build stages) → revert the culprit landing as one
                        commit through land.lock + gate --fast → request to its author
                        → live stays at G; the next run integrates the rest plus the revert
data lane: build(main, data) on a changed data root → <G>-<key12>, live game;
        `refresh-stale` (`make tiles-refresh-stale`, sentinel, load < 40): build(main) → <G>-<key12>-<main12>
```

```sh
python3 scripts/dev/integrator.py status  # running integration and last ledger row
python3 scripts/dev/integrator.py ensure  # start the lanes that have work (a landing does this)
# One integration by hand (add --lane data for the data lane), e.g. against a copy of the published root:
python3 scripts/dev/integrator.py run --published /path/to/data-copy --main-repo /path/to/clone --sandbox integration-measure
```

Nothing polls. `integrator.ensure` (after each landing, and after each finished run) starts
`integrator.py run` as a detached child (log `wt/.registry/integration.log`) when no code lane runs and
main moved past both the live commit and the last integration's `to`, or the full suite is due; and
`--lane data` (log `integration-data.log`, beside the code lane) when the data root has parts the live release
lacks and no data run took this (G, data key) (`data_due`). Nothing starts without a published live release. A run that recorded a row calls `ensure` again
when it ends, so landings after its join window start the next run at once (a manual run with
`--sandbox` or another `--main-repo` chains nothing). A data-root change or a due full suite without a
landing waits for the next landing, finished run or `integrator.py ensure`.

**Landing classes** (`landing_class.py`): `classify(repo, base, head, data_delta)` sorts `git diff --name-only` and
the data-root delta; a path outside both allowlists makes it `code`. `inert`: `docs/*`, `*.md`, `LICENSE`. `data`:
`game/public/assets/{voice,content}/*` or a changed `data_parts` part (JSON of `facade-approvals/assembly` and
`build/layers`, the tile list), compared with the last ledger row's (else the live release's). `tiles`: none (inert),
all listed (code, approvals or layers), else the newly listed. `here` shows the class.

`integrator.run_once(published, main_repo)` reads G from `published/live/live-build.json`, M from main, and
classifies G..M by paths (`batch_class`). `inert` takes no slot: `live_build.repoint` APFS-clones
the live release to `live-releases/<M>` (record `commit` M, `verified_commit`, `repointed_from`), swaps the link
(Pages only when `docs/published-images.toml` changed) and records `result: green`, `skipped: true`. Other classes
run `live_build.build(M, "code")` (`full` for the scheduled suite) in the `integration` sandbox and slot; the data
lane's car report runs only when `layers/traffic_static` changed (else `carried_from`). **Join** only while free:
the build re-reads main once, before its first stage that is not a cache hit; a newer head (at most `JOIN_LIMIT` =
3) is checked out, reclassified and built instead (`batch_joined`), repeating only cached stages, each at most once. After a stage has run for real the batch is frozen; later landings wait for the next run. A batch that
builds publishes under
`wt/.registry/publish.lock`; if the data lane published meanwhile (live parts changed), M is recomposed on that
release (`rebuilt_on`; game-build is a cache hit), at most once; live changing again records `failed` for the next
run. The data lane (`run_data`) builds with main's tile code (`tiles_commit`), so data rebuilds include landed pipeline
fixes even when the live tiles were built by older code. A code-only landing still carries served tiles.
The data lane publishes `<G>-<data key 12>` (record `commit` G); if a code release moved live meanwhile on the same tiles it ships that release's game, else it records `superseded`. After
publishing, both lanes deploy Pages and send the advisory failures
that the replaced release did not have (`failing` labels: `chrome <project:spec>`, `tile-validate <check>`, a stage)
to the authors of the batch's landings as one `warning` (the orchestrator for the data lane and in-place full runs;
`warned` in the row); known failures stay in the row, the release record (`advisory`, `from`) and the notes.

A head that does not build is never blamed on a commit before the base check: the integrator
builds G's build stages against the current data root (mostly cache hits). G built when it was
published, so a red G means the data root changed under it. The run is then `data-red`: it
records G's failing stage (`failure`), the head's failure (`head_failure`), `data_cause` (`approval-root-changed` when the SHA-256 of
`facade-approvals/assembly/MANIFEST.json` differs from the published release's
`textures/assembly/MANIFEST.json`, else `data-root`) and both hashes (`approval_manifest`). It
reverts nothing, posts a `warning` to the board and keeps live at G until main moves (`settled`).

Only a green G bisects the landed heads in G..M (≤ ⌈log₂ n⌉ further build-stage builds) and reverts the
culprit landing `parent..culprit` on current main as one commit under `wt/.registry/land.lock`
after `gate --fast` in the integration sandbox. A run makes at most one revert; a second culprit
is only blamed by a later run, which first builds the reverted main and checks the base again.
The revert message names the integration row id and the failing stage and lists every reverted
commit. It appends a `revert-<sha8>` row to `landed.jsonl` (`integration`, `reverts`,
`reverted`) and posts a `request` with the row id, stage and log to the culprit's task. A revert
conflict, an empty revert or a red revert gate is reported in the same request; main stays.

Each run appends one row to `wt/.registry/integrations.jsonl`: `id` (`<to sha12>.<n>`), `published`,
`from`, `to`, the class fields (`class`, `paths`, `changed_paths`, `data_delta`, `tiles`),
`full_suite`, `chrome_plan`, `chrome`, `advisory`, `warned`, `pages`, `batch_joined`, `joins`, `restarts` (per stage), `stage_seconds` (per stage,
summed over all builds of the run), `rebuilt_on`, `tiles_from`, `lane` (`data`; with `data_key`, `tiles_commit`, `refresh_stale`), `skipped` (inert), `result` (`green`,
`red`, `data-red`, `failed`, `superseded`), `seconds`, `cache_hits`, `facades_bound`, `builds`,
`wall_seconds`, `culprit`, `culprit_task`, `failure`, `head_failure`, `data_cause`, `approval_manifest`,
`revert`. `integration.json`/`integration-data.json` name each lane's running integration and log.

Deleted on 2026-10-27 (a branch waited a median 80 min; `~/turku3d/logs/merge-latency-study/report.md`):
precheck receipts, the live build inside `land`, queue rechecks, speculative prefixes, queue budgets, Codex
resume, the queue sweep and the warm browser service; later that day the polling merge-queue daemon
(`queue run|status`, `merge-queue.json`, held/returned states, `land --batch`, the landing message
guard). Main may be red for one integration; live never is.

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

For tile-only publication, see [localhost commands](../../tools/dev/localhost/README.md).
