# W0-A: Agent tooling MVP (isolation, Codex task runner, AGENTS.md)

Read `docs/briefs/COMMON.md` first. You run in your own git worktree, set up by hand by the orchestrator.

## Why

From now on, many Codex agents work on this repo **in parallel**, each free to change any part of the system. That
only works if every agent has a **fully isolated stack**: its own worktree, ports, build, cache and dist dirs, and
browser profile. Then no agent has to work around another, and fixes land where they belong. The orchestrator
(one Claude session) starts tasks, watches them and lands their branches one at a time. Build the MVP of that
tooling now. Others are blocked on it, so aim for *small, solid, tested*, not feature-rich.

You may take ideas from `ssh osgallery-dev`, read-only: `~/kernel-hive/scripts/dev/wt.sh` (full stack per worker),
`~/osgallery/scripts/dev/codex-task.sh` and `codex-watch.py`, and `~/agent-foundry` (worktree isolation,
verification, compare-and-swap landing). **Do not copy them 1:1.** Write our own, smaller implementations that fit
macOS and this repo.

## Environment contract (other Wave 0 briefs use exactly these names)

| Variable | Meaning | Default for the main checkout |
|---|---|---|
| `TURKU3D_DATA` | shared data root; `raw/` is read-only for workers | `~/turku3d/data` |
| `TURKU3D_WORK` | this sandbox's writable root: `build/`, `cache/`, `dist/`, `reports/`, `qa/` | `~/turku3d/data` |
| `TURKU3D_SANDBOX` | sandbox name (`main` for the main checkout) | `main` |
| `TURKU3D_PORT_BASE` | first of 10 ports reserved for this sandbox: game dev = +0, dist server = +1, QA stub = +2, rest free | `5170` |
| `TURKU3D_BROWSER_DIR` | Playwright/Chrome user-data and cache root for this sandbox | `~/turku3d/browser/main` |

Sandbox layout: `~/turku3d/wt/<name>/{repo/ (git worktree, branch wt/<name>), work/, browser/, sandbox.env}`.
Port blocks are claimed atomically (lock file plus registry under `~/turku3d/wt/.registry/`) and released on `rm`.

## Deliverables

1. `scripts/dev/wt` (bash or python, your choice):
   - `new <name> [--from REF]`: creates the sandbox, claims a port block and writes `sandbox.env`.
   - `ls`: lists sandboxes with branch, ahead/behind, port block, live processes and size.
   - `path <name>`
   - `env <name>`: prints the exports.
   - `rm <name> [--force]`: refuses if processes are live or commits are unmerged.
   - `gc`
2. Make every tool honour the contract. This includes the Makefile/`mk/*.mk`, `game/vite.config.*`,
   `game/playwright*.config.ts`, `tools/dev/serve-dist.mjs`, `qa/` render scripts, `textures/` runners and
   `pipeline/turku3d/config.py` path helpers. Coordinate the config.py part with W0-C: W0-C changes the
   *defaults* to Mac-local paths; you add `TURKU3D_WORK` and keep `TURKU3D_DATA`. No hard-coded ports or `~/turku3d-data`
   paths may remain. Running `make game-e2e` in two sandboxes at the same time must work; add a test that proves it,
   for example two dist servers on two port blocks.
3. `scripts/dev/codex-task`: runs one brief as a headless `codex exec` in a sandbox.
   - `run <name> --brief docs/briefs/X.md [--from REF] [--effort high]`: creates the sandbox if missing and starts
     `codex exec -m gpt-6-astra --sandbox danger-full-access -C <repo>` in its own process group, with the brief +
     COMMON as the prompt and `sandbox.env` exported. It writes `~/turku3d/tasks/<name>/{prompt.md, events.jsonl,
     last.md, pid, meta.json}`.
   - `ls`
   - `status <name>`: state, elapsed time, last event, last commit.
   - `log <name> [N]`
   - `result <name>`: the final message.
   - `resume <name> "text"`: a follow-up in the same session (`codex exec resume`).
   - `stop <name>`: TERM then KILL of the process group.
   - `land <name>`: serialised by a lock. It rebases the branch on `main`, runs the gate for the changed paths
     (`scripts/dev/gate` once W0-B lands; until then the touched areas' `make *-test`), fast-forwards `main` and
     records the result. It refuses if the result is not `## Result: DONE` or the gate is red.
   - Check Codex quota before launching. Read the usage the way `textures/turkutex/codex` does and refuse at 100 %
     unless `--force`.
4. `scripts/dev/here`: one screen in under 2 s. Shows where you are (sandbox, branch, ports), dirty files, running
   tasks, the last landed commits and the next suggested command.
5. `AGENTS.md`: the single policy file. Base it on `docs/briefs/COMMON.md` §1–2 plus the tool usage.
   `CLAUDE.md` contains only `@AGENTS.md` and a one-line comment. Shorten COMMON.md §2 to a pointer to AGENTS.md,
   so the policy does not exist twice.
6. Tests: unit tests for port claiming (concurrent claims never collide), env generation and `land` refusal
   paths. Use a fake `codex` binary on PATH for runner tests. Keep them fast.

## Acceptance

- `scripts/dev/wt new a && scripts/dev/wt new b`: both sandboxes run `make game-e2e` concurrently and both pass.
- `scripts/dev/codex-task run demo --brief <a tiny test brief>` runs to completion with a fake codex binary and with the
  real one (one real call is fine), and `land` fast-forwards main on a trivial change.
- `scripts/dev/here` works from the main checkout and from inside a sandbox.
- A README in `scripts/dev/` has exact commands.
