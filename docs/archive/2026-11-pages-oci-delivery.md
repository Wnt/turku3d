# Retired delivery flows (archived 2026-11-12)

These sections were moved out of `scripts/dev/README.md` when the visual-improvement flow review replaced the
contradictory instructions with one current policy (`AGENTS.md`). They describe the Pages/OCI-era `codex-task finish`
delivery into `main`, the Wave 0 integration notes and the finish receipts. **None of this is current:** the published
Pages game, its OCI tiles and `city.madekivi.fi` are frozen, and work merges into `local/loop-integration` and
deploys to `city.lab.madekivi.fi` only. The tooling code still exists; do not follow these instructions.

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
Read the health response: recent full-pass failures/cancellations or excessive latency
make smoke red even when HTTP is 200. `/coarse/health` and `checks` expose rolling
15-minute per-pass outcomes, p50/p90 and error kinds; the sentinel watches this signal.
One agent-owned selective async run starts from frozen deployed source; it has ten
minutes including bulk-slot waits. `checks` shows recent commands and outcomes so the
agent can avoid repeating other agents' evidence. Choose evidence for the actual deployed
change: generator work builds representative real tiles on OCI, first and full, checks
outcomes/timing and compares counts with source inputs; client work exercises its feature.
The agent selects commands, tiles, timing budgets and count expectations. Generic suites
are no substitute. `scripts/dev/probe_drafts.py --commit SHA --tile TILE --first-budget N
--full-budget N` is an optional real-build probe, never a deployment gate. A failing run permits one forward
fix through `finish --fix-for JOB`; later failures go to the orchestrator. Results
live under the task's `checks/` directory and survive sandbox removal.

The register is `wt/.registry/agents.json`, refreshed by `codex-task agents` and lifecycle
changes. Merge events retain the historical `landed.jsonl` filename for current
clients; deployments and test outcomes are in `deployments.jsonl` and `checks.jsonl`.
There is no integrator process or verified-only release selection. The game world is
generated on demand; facade assets and generator source are published independently
of the game bundle. Details: [AGENTS.md](../../AGENTS.md), [verification.md](verification.md).

### Wave 0 integration

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

### Direct delivery receipts

`codex-task finish` records selected command results, merge time, package/upload
metrics, five-second smoke and the async job path. It accepts a running agent's own
slice and keeps that checkout active. A DONE turn-end compatibility call uses the
recorded selection; a new change requires a new explicit selection. Rebase conflicts
and quick-test failures leave main unchanged; failures after merge are forward only.
