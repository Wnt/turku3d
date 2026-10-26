<!-- Source: ~/turku3d/logs/transcript-study/ (report.md, stats.json, stats.md, digests/). -->

# Codex transcript study: where development time and tokens go

**Scope:** 74 Codex tasks, from 2026-10-25 20:59 to 2026-10-26 18:36 (21.6 h elapsed).

**Sources:**
- `stats.json`, `stats.md` and `digests/` in this folder
- raw Codex rollouts under `~/.codex/sessions/2026/10/`
- `~/turku3d/tasks/<name>/events.jsonl`
- `wt/.registry/landed.jsonl`
- `scripts/dev/README.md` and `scripts/dev/gate.toml`

**Method:** The `stats.md` category table puts most command time in the wrong category (§1), so it is not used for ranking. Every number below either comes from the re-credit scripts (`recredit.py`, `latency.py`, in the scratchpad `tstudy/` folder) run on the raw rollouts, or names its source file.

## Summary

- **The "wait 73 %" figure is not idle time.** It counts `write_stdin` polls of commands that are already running: gates, Chrome suites, tile builds and prechecks. Once each poll is credited to the command it polls, command time ranks as follows:
  - gate 7.7 h (33 %)
  - Chrome/e2e 4.0 h (17 %)
  - tile build 1.5 h (6 %)
  - refresh and game build 0.9 h (4 %)
  - precheck 0.8 h (3 %)
- **Commands are only about a quarter of the time.** The 74 tasks span 102 h in total:
  - 60.4 h inside turns: commands 23.4 h, model latency about 37 h.
  - 27.2 h between turns: a turn had ended and the task was not resumed yet.

  Speeding up the gate and builds can cut at most about 20–25 % of task span. Halving gate + e2e saves about 6 %.
- **Polling is the largest overhead we can control, and it costs tokens more than wall time.**
  - 4,944–5,622 model calls were polls. The count depends on how calls are paired.
  - They took 9.2 h of model latency and about 584 M input tokens (28 % of all input).
  - 3,188 polls asked for `yield_time_ms: 1000` and came back after about 5 s.
- **The time between turns (27.2 h) is the largest wall-time item.** It is not yet attributed to any cause (§1.3).
- **These transcripts cannot compare weekly quota use or model quality per task** (§3). The facade batch benchmark in `~/turku3d/logs/facade-bench/` is the valid source for that.
- **The live-build checks, the isolation rules and the facade baseline stay.** Each one exists because of a concrete incident on main (§5). The plan makes them cheaper; it does not remove them.

## 1. Where the time goes

### 1.1 Re-crediting polls

**How polls arise:** Codex runs a command with `exec_command`. If the command is still running when the yield time ends, the call returns with a `session_id`. The agent then polls it with `write_stdin({session_id})`.

**The correction:** `stats.md` gave each poll its own "wait" category. The re-credit script maps each `session_id` back to the command that started it, and adds the poll's wall time to that command's category.

| category | stats.md (h) | re-credited (h) | share of command time |
|---|---:|---:|---:|
| gate (`scripts/dev/gate`, `make *-test`, vitest, pytest) | 1.4 | 7.65 | 32.8 % |
| e2e / Chrome (Playwright, `warm_browser`) | — | 4.04 | 17.3 % |
| edits and exec-cell continuations (`apply_patch`, cell yields) | — | 2.15 | 9.2 % |
| explicit `sleep` (mostly 30 s) | — | 1.99 | 8.5 % |
| other shell | 1.2 | 1.71 | 7.3 % |
| tile build (`turku3d.package.assemble`, `make tile-build`) | 0.3 | 1.49 | 6.4 % |
| polls of commands that could not be identified | — | 0.99 | 4.3 % |
| refresh / game build / collision sweep | — | 0.93 | 4.0 % |
| precheck (`codex-task precheck`) | 0.0 | 0.79 | 3.4 % |
| data pipeline | 0.4 | 0.64 | 2.7 % |
| imagegen | — | 0.32 | 1.4 % |
| coordination (`inbox`, `board`, `ack`, `msg`, `host_slots`) | 0.9 | 0.31 | 1.3 % |
| read / explore | 0.9 | 0.26 | 1.1 % |
| git, rebase | 0.4 | 0.09 | 0.4 % |

**Precision limits:**
- The categories come from regular expressions, so a single row can be off by a few percent. The ranking holds at that level.
- Command wall time is a lower bound on how long a process ran, because a backgrounded process keeps running while the model thinks.

**Where the time sits, by task:**
- **Gate:** `w1-12-playtest` 82 min, `fix-tile-e2e` 38, `tool-host-slots` 37, `tool-merge-queue` 28. Tooling tasks account for only 1.85 h of the 7.65 h.
- **e2e:** `fix-tile-e2e` 60 min, `tool-host-slots` 29, `tool-fast-browser-checks` 22, `w1-19-bridges` 18.
- **Precheck:** only 5 of 74 tasks ran it: `fix-collision-validate` 19 min, `fix-landing-review` 10, `tool-merge-queue` 7. Its cost per iteration cannot be generalized from this sample.

### 1.2 Model latency

Model latency is the time from a tool output to the model's next action, measured with `latency.py`.

| emitted action | calls | hours | median s | p90 s |
|---|---:|---:|---:|---:|
| real action (command, edit) | 10,609 | 26.0 | 4.5 | 15.7 |
| poll of a running command | 4,944 | 9.2 | 3.1 | 13.4 |
| progress message | 2,078 | 5.7 | 5.5 | 20.2 |
| sleep | 189 | 0.4 | 5.1 | 20.1 |

### 1.3 Between turns

**Size:** 82 gaps between turns, 27.2 h in total.
- Median 7.1 min, p75 21 min, p90 54 min.
- Seven gaps over 60 min make up 13.7 h.
- The longest: `fix-structures-in-main` (216 and 89 min), `fix-landing-review` 131, `fix-collision-validate` 122, `tool-live-checks-wide` 109.

**Cause: not classified yet.** Possible causes:
- orchestrator response time
- a task deliberately parked while it waits for main
- a quota pause

Classifying them needs the orchestrator's resume reasons joined to `events.jsonl` (step M3 in §7).

### 1.4 Landing is no longer the bottleneck

Source: `landed.jsonl`.

- **Queue landings** (`fix-landing-review`, `fix-structures-in-main`):
  - DONE to live took 0.1 and 0.4 min.
  - Merge wall time was 1.9 s and 1.4 s.
  - All six live stages were `cache_hits`.
- **The 50 older landings:**
  - The gate took 2.26 h in total (median 158 s, maximum 651 s).
  - Live builds were recorded for 9 landings and took 0.54 h, of which Chrome was 0.35 h.

The cost has moved into the worker's own gate runs and its polling (§1.1). The recommendations therefore target `scripts/dev/gate` per iteration, not the queue.

## 2. Where the tokens go

**Totals:**
- 2,096 M input tokens, of which 2,073 M (98.9 %) were cached
- 5.2 M output tokens and 1.95 M reasoning tokens
- 17,826 calls, about 118 k of context per call

**Input tokens by the action each call produced** (`recredit.py`):

| action | calls | input M | share |
|---|---:|---:|---:|
| polls (all targets) | ~5,600 | ~584 | 28 % |
| progress messages | 2,004 | 236 | 11 % |
| shell "other" | 2,227 | 236 | 11 % |
| edits / cell continuations | 2,030 | 213 | 10 % |
| read / explore | 1,805 | 187 | 9 % |
| e2e launches | 1,287 | 145 | 7 % |
| gate launches | 1,292 | 128 | 6 % |
| coordination | 1,037 | 105 | 5 % |

**Required reading:** AGENTS.md, ARCHITECTURE.md, STATUS.md, COMMON.md and `scripts/dev/README.md` add up to 103 KB, about 26 k tokens. They stay in the cached prefix until the first compaction.

**Compaction lowers per-call context.**
- The 47 compactions cut context from about 150–240 k to 25–60 k.
- Tasks with no compaction averaged 107 k per call; tasks with three averaged 131 k.

Long sessions raise the average context only a little. The number of calls drives the volume.

**This limit applies to every token claim in this report:** 99 % of input is cached, and nothing in the evidence shows how cached tokens count against the weekly quota. Fewer calls certainly means less latency and less uncached work. The effect on the quota stays unproven until it is measured as described in §3.

## 3. Quota and model comparison: what these transcripts cannot show

**Per-task quota deltas are invalid.**
- On average 4.7 tasks ran at once over the 21.6 h window, and a typical task overlapped about 7 others.
- The deltas add up to 725 percentage points.
- Seven tasks show decreases: `w1-16-parked` 80→3, `fix-e2e-flakes` 62→0, `w2-02-region` 71→3, `w1-17-player` 80→12, `w1-02-structures`, `tool-watch` and `w2-01-costbench`.
- The rollouts show a `resets_at` of 2026-10-26 14:12, and later 11-01 18:33, 11-01 23:30 and 11-02 09:48. The readings therefore come from different windows.
- `scripts/dev/README.md` §Codex token ledger says: "No token-to-weekly-percentage conversion is claimed."

**Per-model efficiency from this sample is invalid.**
- gpt-6-astra ran the early W0/W1 greenfield tasks, before the gate cache and merge queue existed.
- gpt-6-sol ran the later fix and integration work under heavier policy.
- Many tasks mix 2–3 models across resumes.
- The `model` field counts per-turn mentions (astra 105, sol 82, luna 26), not calls.

**Valid method (already in progress):**
- Run one agent type at a time, with no other Codex task running.
- Read `usage.py` before and after each batch. `facade-bench/baseline.json` records 25 % at 18:28, "before stopping other Codex tasks".
- Report tokens with `scripts/dev/codex-cost --by model --json` and `--by task --json` from the per-turn ledger.
- Use the same wall set, brief and effort for each model.
- Report accepted walls per batch, together with the QA rejection reasons.
- Discard any batch that crosses a `resets_at` boundary.

## 4. Recommendations

§1 bounds the expected gains:
- Command-side changes reach at most about 20–25 % of span.
- Token-side changes cut calls; their quota effect is unproven (§2).

### R1. Stop 5-second polling (largest item, tokens first)

**Evidence:**
- 3,188 polls used `yield_time_ms: 1000` and returned after about 5 s.
- Polls took about 584 M input tokens (28 %) and 9.2 h of model latency.

**Changes:**
1. Add a blocking subcommand to `codex-task`, for example `scripts/dev/codex-task wait-cmd -- <command>`.
   - It runs the command, returns only at exit, and prints the last N lines and the exit code.
   - `scripts/dev/gate`, `codex-task precheck` and `make tile-build` then each become one tool call.
2. Add this sentence to AGENTS.md §Working rules: "Poll a command that runs longer than 30 s with `write_stdin` and `yield_time_ms: 30000` (or `codex-task wait-cmd`); never poll at 1000 ms." 30000 is the largest value seen in the transcripts.

**Expected gain:**
- About 2,600 fewer model calls, which is about 300 M input tokens (about 15 %) and about 2 h of model latency.
- Wall time changes little, because the command still runs for as long as it takes.

### R2. Make the gate cheaper per iteration without narrowing coverage

**Evidence:** the gate is 7.65 h, a third of command time, and 76 % of it is in feature tasks.

**Candidates in `gate.toml`** (each needs a measurement before it changes):
- `gate-tests` triggers on `scripts/dev/*`, so every tooling edit runs it.
- `tooling-tests`, `queue-tests` and `watch-tests` all trigger on `task_runner.py`, `sandbox.py` and `coordination.py`.
- `full_triggers` includes `host_slots.py`, `resources.py` and `env_sync.py`.

**Procedure:**
1. For each of those files, record the stage times from `scripts/dev/gate --paths <file> --json`.
2. Use an import graph to check whether the tests of each triggered stage import the file.
3. Narrow a trigger only where they do not.
4. Record the before and after times in the commit message.

**Kept unchanged:**
- The rule that unmapped files select everything.
- The physics `[[stages.related]]` fan-out to `@walk @drive @character @ground @structures @buildings`, because collision regressions show up in the browser. Removing a tag requires showing which past bug it would have missed.

**Cache:** the gate cache (`$TURKU3D_DATA/cache/tool-stages/gate`) already reuses green stages. Report its hit rate per task from `precheck.json` and `meta.json`, to see whether repeated iterations hit it.

### R3. Measure and shorten the time between turns

**Evidence:** 27.2 h, the largest wall-time item (§1.3).

**Changes:**
- Have the orchestrator write a `resume_reason` (blocked-on-main, quota, review, idle) to `events.jsonl` on every `codex-task resume`.
- Add a `codex-task board` column showing the minutes since the last turn ended.

**After classification only:** if the delay is orchestrator response time, have the sentinel poller resume a task within N minutes of a `turn.completed` with result `BLOCKED`. No saving is claimed before then.

### R4. Fewer progress messages

**Evidence:** 2,078 progress messages took 5.7 h of model latency and 236 M input tokens (11 %).

**Change:** add this sentence to `docs/briefs/COMMON.md`: "Send a progress message only when a stage finishes or the plan changes, not while polling." The orchestrator also watches with `scripts/dev/watch`, so the messages are not its only signal.

**Expected gain:** modest, and unmeasured until a batch runs with the rule.

### R5. Split long tasks at natural boundaries (weak lever)

**Evidence:** per-call context is 107 k with no compaction and 131 k with three. The difference is small (§2).

**Change:** none to policy. Brief scope stays "one coherent slice, then precheck", which AGENTS.md already says. No compaction-based split rule is recommended, because the evidence does not show a large gain.

### R6. Add parallel agents only once slot throughput is measured

**Current caps** (README §Host slots):
- 3 `build` and 2 `browser` jobs at once, with at most 2 `TURKU3D_JOBS` per build.
- A queued `land` reservation stops new admissions.

**Why the caps matter:** before the slots existed, the host reached a load of about 433 on 10 cores (`fix-gate-speed`), and motion and tooling tests timed out (`tool-land-friction`).

**Facade batches:**
- Imagegen calls (about 490 s per call, as in `w1-09f-generated-facades`) are remote and use no host slot. They can run at higher concurrency than builds.
- Their local QA and assembly steps go through `build` slots.

**Disk:**
- 126 GiB is free; there are 21 sandboxes and `wt/` takes 126 GiB, about 6 GiB each. This is an upper bound on APFS.
- The 30 GiB floor that `run` enforces leaves room for about 15 more sandboxes.

**Change:**
- Make `host_slots.py` log the wait seconds of each admission to `~/turku3d/host-slots/waits.jsonl`.
- Increase the agent count only while the median slot wait stays under a set limit.
- Every tile build, e2e run and live build goes through the slots; the README already lists all of them.

## 5. What not to cut, and why

**Make the precheck live-build checks optional, sampled or post-landing** (refresh, 4-tile build, game build, collision sweep, collision audit, Chrome)
- *Why it stays:* each check caught broken data that no unit test caught:
  - bridges vanished when a manifest was missing (`fix-bridges-in-main`);
  - missing ground PBR arrays shipped an unbound ground (`fix-ground-visible`);
  - structures were not wired into refresh (`fix-structures-in-main`);
  - the office parent tile was estimated at 2.86 GB, with an L0 tile over budget (`fix-integration-1b`).
- *Cheaper alternative:* the live stage cache already exists. Its keys are the declared source paths, the input manifests and the preceding stage key. Both queue landings hit it on all six stages. Report the hit rate per precheck.

**Lean on `queue_sweep` as the safety net so the queue can admit more**
- *Why it stays:* the sweep "does not delay publication". A red sweep runs after the live pointer has moved, so players would see the broken build.
- *Cheaper alternative:* on a red sweep, roll the live pointer back automatically to the last green release, then hold the domain. This is not implemented today.

**Widen cache reuse, or loosen the 210 s / 120 s queue recheck budgets**
- *Why it stays:* tile outputs depend on shared mutable inputs under `$TURKU3D_DATA`. In `fix-tile-e2e`, one extra structures input from another sandbox pushed L0 over budget by 19,756 bytes.
- *Cheaper alternative:* first add a test that changes a shared published layer without changing the source, and asserts that the live cache misses.

**Narrow e2e tag routing, or "unit tests only" for shared or physics code**
- *Why it stays:* flakes under load came from input, timing and focus races. In `fix-e2e-flakes`, the baseline was 45 passed and 1 failed, and a host controller leaked into the tests.
- *Cheaper alternative:* the R2 procedure. Remove a tag only with evidence of which bugs it would have missed.

**Shared dev servers, shared browser profiles or shared writable caches**
- *Why it stays:*
  - `fix-release-symlinks`: nested symlinks into shared data were cloned into releases, and refresh could write through them.
  - `fix-landing-review`: main advanced before publication.
  - `fix-tile-e2e`: input contamination across sandboxes.
- *Cheaper alternative:* share only read-only content-addressed caches. Add a guard test that refresh cannot write outside `TURKU3D_WORK`.

**Drop the facade baseline or the `Facade regression reason` rule**
- *Why it stays:* generated facade work (`w1-09e-lost-facades`, `w1-09f-generated-facades`) is where bindings get lost, as the `fix-facade-binding` and `fix-facade-vertical` fixes show. The check costs almost nothing: it compares `facades_bound` with `facades_baseline` in `live_build`.
- *Cheaper alternative:* keep it. Consider failing precheck when the count drops, instead of accepting a written reason.

**Skip Chrome in precheck**
- *Why it stays:* AGENTS.md rule 4 ("Never weaken a test") and the goal of verifying in the live build. Chrome was 0.35 h of the 0.54 h of live builds.
- *Cheaper alternative:* warm-browser reuse (`scripts/dev/warm_browser.mjs`) already exists in the queue. Measure its hit rate and extend it to worker prechecks.

**Drop the per-turn rebase, the inbox check or the required reading**
- *Why it stays:* the cost is small. Rebase took 0.04 h. Coordination took 0.31 h of commands and 105 M tokens (5 %). Its quota effect is unproven.
- *Cheaper alternative:* no change without a token-weighted measurement.

## 6. Rework is a recurring cost, not a one-off

**Tasks tagged as infra, tooling or post-landing rework:**
`tool-*`, `w0-*`, `fix-gate-speed`, `fix-landing-review`, `fix-e2e-flakes`, `fix-release-symlinks`, `fix-streetlevel-cache-shared`, `fix-integration-1/1b`, `fix-structures-in-main`, `fix-bridges-in-main`, `fix-tile-e2e`, `fix-ground-visible`, `fix-refresh-rebind`.

**Split:**
- Infra and rework: 28 of 74 tasks, 41.3 h of span (40 %), 805 M input tokens (38 %).
- Feature tasks: 46 tasks, 60.7 h, 1,291 M.

**Observations:**
- The two most expensive tasks by tokens are repairs: `fix-structures-in-main` (145 M, 7 turns, 3 compactions) and `fix-tile-e2e` (136 M).
- Several repairs fixed data that reached main because no check covered it. That is why §5 keeps the live checks.
- The tag list is manual, so treat the split as approximate.

## 7. Measurement plan before further policy changes

- **M1. Facade batches:** one model at a time, nothing else running, quota read before and after, `codex-cost --by model`. In progress in `~/turku3d/logs/facade-bench/`.
- **M2. After R1 lands:** compare polls per task and input tokens per task on the next facade batch, with the same brief.
- **M3. Between-turn time:** add `resume_reason` to `events.jsonl` (R3), then reclassify the 27.2 h.
- **M4. Slot waits:** add the `host_slots.py` wait log (R6) before raising agent concurrency.
- **M5. Gate triggers:** collect per-file `gate --paths ... --json` timings for the R2 candidates.
- **M6. Cache invalidation:** add a guard test for shared inputs (§5, cache-reuse item) before widening any cache.