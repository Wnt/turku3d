# W2-01: Codex cost accounting and model benchmark for expansion tasks

Read `AGENTS.md` (coordination: `scripts/dev/codex-task inbox/board/msg`). Timebox: about 30 minutes; split
follow-ups rather than overrun.

## Why

The next phase expands from the office block to districts. Codex is used for facade labelling, facade cleaning
("improve", image generation), library material generation and the visual QA judge. One evening of Phase 1 work used
about 68 % of the weekly Codex quota. Before scaling we need to know (a) what each job type costs, and (b) the cheapest
model that meets the quality bar for each job type.

Available models (`~/.codex/models_cache.json`): gpt-6.1-sol, gpt-6-astra, gpt-6-sol, gpt-6-luna, gpt-5.6-sol,
gpt-5.6-terra, gpt-5.6-luna. Codex `--json` events carry exact token usage in `turn.completed.usage` (input, cached
input, output, reasoning output). Use tokens as the cost unit. The weekly percentage is an integer and too coarse.

## Deliverables

1. **Accounting**:
   - Every Codex call made by our tooling records model, effort, job type, wall time and token usage into one
     append-only ledger, `~/turku3d/ledger/codex-calls.jsonl`. This covers the `textures/` runner, the `qa/` judge and
     `scripts/dev/codex-task` (per turn).
   - Add a `scripts/dev/codex-cost` report: by job type, model, task and day, plus cost per facade and per view.
   - Backfill from existing events files under `~/turku3d/tasks/*/events*.jsonl` and the texture/QA run dirs where
     possible.
2. **Benchmark harness** `bench/codex/`:
   - Fixed, versioned test sets built from data we already have. For example: 12 street-facade reference crops with
     reviewed label truth (floors and bays from lidar/footprints, roof type, wall material); 6 facades for the improve
     job; 8 QA view pairs with known planted differences (reuse the Phase 0 QA planted-difference method).
   - Metrics per job type:
     - labelling: field accuracy against the truth
     - improve: an automatic score (Astra judge comparing against the reference crop, plus local checks such as
       aspect, seam and colour)
     - QA judge: planted-difference recall and false positives
   - Also record tokens, wall time and failure rate.
   - A model × effort matrix runner with a hard budget guard (default: stop when the weekly quota reaches 80 %, and
     keep this benchmark under 3 % of the weekly quota), resumable and cached.
3. **Pilot run** within that budget:
   - Labelling and QA judge across all 7 models at low/medium effort.
   - The improve job only for gpt-6-astra, gpt-6.1-sol and gpt-6-sol on 4 facades.
   - Write `docs/reports/codex-model-bench.md` with a table: quality vs tokens vs time per job type, and a
     recommended routing (job type → model + effort).
4. A **routing config** `textures/codex-routing.toml`, read by the runners, so the choice can change without code
   edits. Defaults follow the recommendation.

## Acceptance

- The ledger is populated, including the backfill. `codex-cost` prints a per-job-type summary.
- Benchmark report with numbers and the routing config are committed. Quota used by the benchmark is reported.
- `scripts/dev/gate` is green.
