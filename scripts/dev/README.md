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
