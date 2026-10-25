# Turku3D agent policy

Use English for all project communication, including code, comments, documentation, commit messages,
progress updates and final reports. This overrides personal default language settings.

## 1. Goal and context

- The project is a GTA V-fidelity open world of Turku that runs in desktop Chrome: three.js r186 (`three/webgpu`), Rapier
  0.21 in a physics worker, Vite, and 3DTilesRendererJS.
- The first goal is a **playable office block as soon as possible**: Läntinen Rantakatu 15, E 23459668 N 6704254.
  You walk and drive around it, and curbs and stairs are felt both on foot and in a car. You cannot pass through
  buildings. Facades look like the real ones. It is played with an Xbox Elite Series 2 controller.
- Read `docs/ARCHITECTURE.md` (decisions D1–D8, §4 Phase 1 task table with acceptance criteria) and `docs/STATUS.md`.
  The research reports in `docs/research/` hold verified commands and pitfalls.
- Everything now runs on this Mac (M1 Max, 10 cores, 64 GB). Data root: `~/turku3d/data`. Raw data is read-only for
  you and shared. Write into your own sandbox dirs (Wave 0 tooling provides them). osgallery-dev is legacy: no new
  work there.

## Working rules

1. **Fix things where they belong.** You may edit any part of the system, so do not leave workarounds in your own area
   for a problem elsewhere. Remove the code you replace in the same change, and leave no commented-out code.
2. **No feature flags** unless you are A/B-measuring two real alternatives. Then register the flag (owner, reason,
   removal condition counted in landed merges, at most 10) and remove it with the losing path once decided.
3. **Dead code is deleted**, not kept "just in case".
4. **Green before done.** Run the quality gate (`scripts/dev/gate` once Wave 0 lands; until then the area's `make
   *-test` targets) for what you touched. If you cannot make it green, finish with `## Result: BLOCKED`, the failing
   command and its output. Never weaken a test or a check to pass.
5. **Fast tests.** Run only the tests relevant to your change, in parallel. Unit tests use small synthetic fixtures, not
   real tiles. The target is 5–20 s for the gate on a typical change. If yours is slower, fix that too.
6. **Commit** on your branch with clear messages, and end each message with
   `Co-Authored-By: Codex (gpt-6-astra) <noreply@openai.com>`. Do not merge to `main`: the orchestrator lands branches.
7. **Isolation.** Use only your sandbox's ports, data/build/cache dirs and browser profile. Never kill processes you
   did not start, and never use `pkill -f` / `pgrep -f` with a pattern that appears in your own command line.
8. **No paid services** and no new API keys. Secrets live in `~/.config/turku3d/`, are never copied into the repo and
   are never printed. Codex image calls may use the weekly quota up to 100 %; the runners already check it.
9. **Language: English for everything in this project**: code, comments, docs, commit messages, progress updates
   and your final report. This overrides any personal default language setting. Match the surrounding style and keep
   each area's README current with exact commands.

## Isolated work and task tools

- Read `docs/ARCHITECTURE.md`, `docs/STATUS.md` and your brief. Layer and coordinate contracts remain in
  `docs/briefs/COMMON.md` and `docs/specs/`.
- Start with `scripts/dev/here`. Create a sandbox with `scripts/dev/wt new <name> --from main`, then
  `source ~/turku3d/wt/<name>/sandbox.env` and `cd ~/turku3d/wt/<name>/repo`.
- `TURKU3D_DATA` is the shared input root (`~/turku3d/data`); its `raw/` is read-only for workers.
  `TURKU3D_WORK` is the writable sandbox root. Use its `build/`, `cache/`, `dist/`, `reports/` and `qa/`.
  Main defaults `TURKU3D_WORK` to `TURKU3D_DATA`.
- `TURKU3D_SANDBOX` is the sandbox name (main: `main`). `TURKU3D_PORT_BASE` reserves ten ports:
  dev +0, dist/Playwright +1, QA stub +2; +3 through +9 are available for tests and probes.
  Main defaults to 5170. Never reuse another sandbox's server.
- `TURKU3D_BROWSER_DIR` contains sandbox browser profiles and caches (main: `~/turku3d/browser/main`).
  Source the environment before running tools, including direct npm or Python commands.
- The orchestrator starts work with `scripts/dev/codex-task run <name> --brief docs/briefs/X.md`.
  Inspect with `ls`, `status <name>`, `log <name>`, and `result <name>`; follow up with `resume <name> "text"`.
  `stop <name>` stops only that task's process group. The runner refuses exhausted or unreadable quota.
- Only the orchestrator runs `scripts/dev/codex-task land <name>`. It requires `## Result: DONE`, clean
  worktrees, rebase on current main, and a green gate before a fast-forward. Workers commit on their own branch.
- Remove finished sandboxes using `scripts/dev/wt rm <name>`; live processes must be stopped first.
  `--force` explicitly discards dirty or unmerged work; it never kills processes. `wt gc` only prunes missing entries.
- Exact commands and troubleshooting: `scripts/dev/README.md`. No new API keys or paid services.

## Coordination

- Check `scripts/dev/codex-task inbox --unread` and `scripts/dev/codex-task board` after each major
  step (each commit) and before finishing. Acknowledge what you act on with `codex-task ack <id>`.
- Announce interface changes others depend on (schemas, file layouts, module APIs) with
  `scripts/dev/codex-task msg all --kind interface --subject "..." --ref <commit> "body"`.
- Before changing another area's interface, post a `request` to its owner when that task is running,
  or to the board. Never edit another sandbox's files; reading other worktrees is allowed.

## Run result

End with these headings: `## Result: DONE` or `## Result: BLOCKED`, `## What changed`, `## How to verify`
(exact commands), `## Gate output` (summary and timing), `## Follow-ups` (only concrete remaining work).
Use English prose under these machine-readable headings.
