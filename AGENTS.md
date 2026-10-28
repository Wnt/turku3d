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

You own the outcome of your brief. Decide the means yourself: plain `git`, `npm`, `python`, `make` and the repo's
scripts are all yours to use, and the scripts under `scripts/dev/` are conveniences, not a cage. When a tool refuses or
breaks, fix its cause or go around it with the underlying tools and say so in your report; do not stop for a process
reason. The rules below are the ones that stay hard.

1. **Fix things where they belong.** You may edit any part of the system, so do not leave workarounds in your own area
   for a problem elsewhere. Remove the code you replace in the same change, and leave no commented-out code.
2. **No feature flags** unless you are A/B-measuring two real alternatives. Then register the flag (owner, reason,
   removal condition counted in landed merges, at most 10) and remove it with the losing path once decided.
3. **Dead code is deleted**, not kept "just in case".
4. **Green before done** means `scripts/dev/gate --fast --base main` is green: lint and format, type checks, the
   policy stage and the unit-test stages that stay under 20 s. The Chrome suite and the deep tier (`make gate-deep`:
   pipeline, validator, tooling and other slow test stages) are never required to finish; run them explicitly only when they prove the changed behavior. Normal integration does not run
   Chrome or the deep tier. Do not add browser specs or slow tests unless
   your brief asks for them. Real-data generator sanity screenshots (rule 11) are expected outside the gate.
   If you cannot make the fast gate green, finish with `## Result: BLOCKED`, the failing
   command and its output. Never weaken a test or a check to pass.
5. **Fast tests.** `gate --fast` finishes in under 30 s at host load 100; a stage whose full run takes over 20 s gets
   `tier = "deep"` in `scripts/dev/gate.toml`. New tests are small unit tests of pure functions on synthetic
   fixtures, never tests that build tiles, drive the car or start a server. Time budgets are checked by hand only
   (`make streaming-perf`, `make render-perf`), never in a gate.
   Long commands (over 30 s) go through `scripts/dev/codex-task wait-cmd -- <command>` or are polled every 30 s.
6. **Commit** on your branch with clear messages, and end each message with
   `Co-Authored-By: Codex (gpt-6-astra) <noreply@openai.com>`. You land your own slices on `main` with
   `codex-task finish` (see below); nobody lands for you.
7. **Isolation.** Use only your sandbox's ports, data/build/cache dirs and browser profile. Never kill processes you
   did not start, and never use `pkill -f` / `pgrep -f` with a pattern that appears in your own command line.
   Run heavy builds and Chrome suites through the host slot scheduler (`scripts/dev/host_slots.py`).
8. **No paid services** and no new API keys. Secrets live in `~/.config/turku3d/`, are never copied into the repo and
   are never printed. Codex image calls may use the weekly quota up to 100 %; the runners already check it.
9. **Language: English for everything in this project**: code, comments, docs, commit messages, progress updates
   and your final report. This overrides any personal default language setting. Match the surrounding style and keep
   each area's README current with exact commands.
10. **No street imagery in the repo.** Main's tree is published to the public GitHub repository on every landing,
    so never commit Street View, Look Around, Mapillary or Bing imagery (or crops, previews, contact sheets of it)
    or raw city data; the gate's policy stage enforces this and `docs/published-images.toml` lists the source and
    licence of any marked or large image.
11. **Use the lightest evidence that proves the change.** A normal change-to-evidence loop should finish
    within five minutes. Run the routed fast gate and one focused check for the changed behavior. Reuse existing
    built tiles, bundles and screenshots. For a geometry or placement algorithm, rebuild only the affected stage
    and changed real tile, then inspect top-down and oblique views of a representative changed instance. Add a
    second tile or more instances only for a seam, boundary or materially different input case. Do not rebuild an
    area, render every instance or run both browser backends by default. Compare a reference photo only when the
    change concerns real-world appearance or placement; keep imagery local and check calibration before treating
    views as pose-aligned. Report the exact evidence and any untested cases. Full Chrome suites, deep gates,
    performance probes and exhaustive geometry audits are explicit investigations, never completion requirements.

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
- Rebase on current main at the start of every turn. DONE means finish, which lands (if finish itself is
  broken, land with git the way `scripts/dev/landing.py` does and tell the orchestrator): committed on your
  branch, `scripts/dev/gate --fast --base main` green, a result file with the headings below, and
  `scripts/dev/codex-task finish <name> --result <file>` (Codex runs record the result themselves and
  their runner lands it at turn end). Finish rebases onto main, reruns the fast gate and fast-forwards main
  in seconds; on a conflict or a red gate it stops with main unchanged, and you resolve it yourself,
  rerun the gate and finish again. Land every green slice immediately (`--keep-sandbox` when the task
  continues); `--note "..."` adds a line for the other agents.
- Every landing sends a `landed` message to the other running agents. When you receive one, rebase onto
  main before your next gate and resolve conflicts yourself; `codex-task status <name>` shows how many
  landings you are behind. Unlanded branches are never shared: land an interface or data-structure change
  first as a small compatible slice, then build on it; never ask others to merge your branch.
- The integrator publishes the game bundle with already built served tiles before data refresh or diagnostics.
  Code and data builds have dedicated CPU shares; neither queues behind agent builds. Normal integrations run no
  Chrome or deep gate. Data publication retains cheap collision, car and facade-ground diagnostics; their failures
  are advisory warnings and release notes. Explicit `integrator.py run --full` runs Chrome and the deep tier after
  publication. Only a batch that cannot be built is bisected and reverted with an inbox request to its author.
- Remove finished sandboxes using `scripts/dev/wt rm <name>`; live processes must be stopped first.
  `--force` explicitly discards dirty or unmerged work; it never kills processes. `wt gc` only prunes missing entries.
- Exact commands and troubleshooting: `scripts/dev/README.md`. No new API keys or paid services.
- Watch agents live with `scripts/dev/watch`; `--plain --once` replays task and coordination events read-only.

## Coordination

- **When you are stuck, say so instead of waiting or guessing.** Post a `request` to the orchestrator
  (`scripts/dev/codex-task msg orchestrator --kind request --subject "..." "what blocks you, what you tried, what you
  need"`), to the task that owns the thing (`msg <task> ...`), or to everyone (`msg all --kind warning ...`) when it
  affects others; then continue with the parts that are not blocked. The orchestrator watches the inbox and unblocks
  (data, decisions, other agents, budget). Never wait silently on another task.
- Runner hooks announce new inbox messages after tool calls. Handle announced requests and warnings,
  then acknowledge them with `codex-task ack <id>`. Check `scripts/dev/codex-task board` after each
  major step (each commit) and before finishing. Use `codex-task inbox --unread` for a manual check.
- Announce interface changes others depend on (schemas, file layouts, module APIs) with
  `scripts/dev/codex-task msg all --kind interface --subject "..." --ref <commit> "body"`.
- Before changing another area's interface, post a `request` to its owner when that task is running,
  or to the board. Never edit another sandbox's files; reading other worktrees is allowed.

## Run result

End with these headings: `## Result: DONE` or `## Result: BLOCKED`, `## What changed`, `## How to verify`
(exact commands), `## Gate output` (summary and timing), `## Follow-ups` (only concrete remaining work).
Use English prose under these machine-readable headings.
