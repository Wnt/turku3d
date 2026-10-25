# Codex accounting and model benchmark — 2026-10-25

Status: **partial / BLOCKED on quota and independent label truth**. Accounting and routing are implemented; the requested full pilot has not been completed. No main merge was performed.

## Measured QA pilot

Eight fixed pairs (four identity controls, four grey-patch defects) in one batch per model. This is a small planted-difference smoke benchmark, not evidence of production facade quality. Input pixels, prompts, schemas and hashes are versioned in `bench/codex/`; images remain outside Git.

| Model | Effort | Strict recall | Strict FP | Localization recall / FP | Input | Cached | Output | Reasoning subset | Tokens/view | Wall s (8 views) | Failed |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| gpt-6.1-sol | low | 0% | 4 | 100% / 0 | 27,484 | 0 | 681 | 59 | 3,520.6 | 51.8 | 0 |
| gpt-6-astra | low | 0% | 4 | 100% / 0 | 27,384 | 0 | 652 | 0 | 3,504.5 | 35.0 | 0 |
| gpt-6-sol | low | 100% | 0 | 100% / 0 | 26,935 | 0 | 575 | 0 | 3,438.8 | 17.9 | 0 |
| gpt-6-luna | low | 0% | 4 | 100% / 0 | 26,738 | 0 | 608 | 0 | 3,418.2 | 20.9 | 0 |
| gpt-5.6-sol | low | 100% | 0 | 100% / 0 | 26,602 | 0 | 904 | 159 | 3,438.2 | 26.0 | 0 |
| gpt-5.6-terra | low | 100% | 0 | 100% / 0 | 26,602 | 0 | 822 | 197 | 3,428.0 | 25.0 | 0 |
| gpt-5.6-luna | low | Not run | — | — | — | — | — | — | — | — | — |
| All seven models | medium | Not run | — | — | — | — | — | — | — | — | — |

Strict matching requires the planted category (`texture`) and IoU >= .25, one-to-one. The three zero-strict-recall models detected all patches but assigned other categories; their four strict false positives are category mismatches, not hallucinated additional defects. Localization ignores category only as a separate diagnostic. All six models correctly left four unchanged controls free of issues. No thresholds were changed after seeing outputs.

The cheapest **strictly passing measured cell** is gpt-5.6-terra low (27,424 tokens / eight views); gpt-6-sol low is faster (17.9 s versus 25.0 s) at 27,510 tokens. That 86-token difference is only 0.31% and does not establish a meaningful cost winner. All models had 100% localization recall; this corpus is too easy and too narrow for production routing. Each cell has one call, no confidence interval or repeatability estimate. Schema/prompt overhead and batching affect tokens and latency.

## Quota evidence

The first benchmark model call started at weekly **75%**; the last completed at **77%**. The guard then refused the next call: `budget stop: 77%, baseline 75%, reserve 1 point`. The absolute cap was 80%, allowance 3 percentage points, reserve 1 point. Six model calls, 48 judged view-pairs, **165,987 input+output tokens**, 176.6 seconds of model-call wall time. Cached input was zero; reasoning output was 415 tokens, already included in output. No improve or labelling model calls were made.

The observed account-wide change was **+2 percentage points**, including concurrent workers; the benchmark-only percentage cannot be isolated from integer snapshots. No token-to-quota conversion or exact weekly-token denominator is asserted. The persistent baseline prevents a resume from silently granting another 3%. Unknown quota or a changed reset timestamp fails closed. Admission control is serial and reserves one point, but integer telemetry cannot strictly bound an unbounded in-flight call. A server-enforced per-call quota limit is not available to this runner.

## Historical accounting

Backfilled **334 calls**: 24 task turns, 100 building-label attempts, 162 improve attempts, five library attempts and 43 QA attempts. Sources were `~/turku3d/tasks`, the W1-09 facade work outputs and the W1-11 visual-QA work outputs. Backfill is idempotent, deduplicates attempts/out copies and uses event usage when it survives. Task creation dates and QA file modification dates are historical timestamp approximations; task wall times and legacy effort are unknown. Archived task model is inferred from the runner’s hard-coded Astra setting. These historical runs are not controlled model-quality benchmarks.

| Job type | Calls | Input+output tokens | Known wall s | Tokens/facade | Tokens/view |
|---|---:|---:|---:|---:|---:|
| building_label | 100 | 4,310,152 | 6045.0 | 14085.5 | — |
| improve | 162 | 13,867,462 | 12017.6 | 109192.6 | 40667.0 |
| library | 5 | 211,615 | 241.6 | — | — |
| qa | 43 | 883,922 | 1322.1 | — | 22098.0 |
| task | 24 | 259,662,433 | unknown | — | — |

Costs per unit include retry costs and count each logical job’s units once; only attributable calls enter unit numerators. They are workload summaries, not universally transferable costs per physical facade. Cached input and reasoning output are reported separately by `codex-cost` and are never added again. Missing usage remains null. A synthetic QA failure initially escaped test isolation; an append-only `record_type=void` annotation excludes it. Every QA test now uses an isolated ledger.

## Routing recommendation

Keep conservative production routing until representative quality evidence exists. The config is `textures/codex-routing.toml`; explicit model/effort overrides win. The task worker also reads its model from this file, preserving explicit task effort.

| Job type | Recommended default now | Evidence / next candidate |
|---|---|---|
| Labelling / building labels | gpt-6-astra, medium | No independent reviewed truth; no measured quality ranking |
| Improve / library | gpt-6-astra, medium | Historical costs only; no controlled cross-model image quality evidence |
| QA judge | gpt-6-astra, medium | Keep established model; compare 5.6-terra low and 6-sol low on harder defects next |
| Development task | gpt-6-astra, high | Existing task convention, not benchmarked |

These defaults follow the recommendation to retain the established model; medium effort is now explicit for texture/QA calls instead of inheriting local CLI settings. They are **not** claimed to be the cheapest passing choices. The local model cache advertised low/medium for all seven requested model IDs; the pilot verified access only to the six actually called. CLI effort selection follows the [official Codex example](https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex).

## Missing acceptance work

1. Independently review and version 12 facade labels using readable crops and available geometry. W1-09 explicitly confirmed current specs are model predictions, not truth. The committed candidate manifest has empty truth and `reviewed: false`; none is scored.
2. Complete the remaining low-effort QA model and all seven medium cells in a newly authorized quota window, with repeated and harder planted differences.
3. Run the first four of six pinned improve cases on Astra, 6.1 Sol and 6 Sol; the harness includes aspect/seam/colour diagnostics and an Astra reference judge, but no image quality results are claimed.
4. Finish label low/medium cells after truth review, then choose routing from measured quality thresholds and costs.
5. Integrate the benchmark-only gate mapping requested from fix-gate-speed; meanwhile explicit benchmark lint and texture tests are mandatory.

## Verification

```sh
source ../sandbox.env
scripts/dev/codex-cost --by job_type
scripts/dev/codex-cost --by model --json
.venv/bin/python -m ruff check bench/codex
.venv/bin/python -m ruff format --check bench/codex
.venv/bin/python -m pytest textures/tests/test_codex_runner.py qa/tests/test_qa.py -q
scripts/dev/gate
```

Initial gate: all seven selected stages passed in **69.841 s** under concurrent host load. A prior standalone tooling batch hit the existing worktree-removal 20-second timeout; the complete gate subsequently passed without changing any limits. Final verification is recorded below. Raw gate logs, events and budget evidence are in `$TURKU3D_WORK/reports/costbench/`. The committed numeric pilot evidence is `bench/codex/results/pilot-v1.json`.

Verification follow-up: the 95.764 s and 99.618 s gates failed only existing tooling timing
checks under host contention. Imported only `scripts/dev/sandbox.py` from the gate-speed
owner’s commit `36aea01` to avoid descriptor-wide `lsof` scans; no timeout changes were
imported. The subsequent 94.721 s gate passed worktree removal and failed only `here` at
2.104842 s against its unchanged 2 s limit. `here` now obtains root and branch in one Git
process. All functional assertions, the 20 s subprocess timeout and 2 s orientation limit
remain unchanged. Final gate evidence follows below.

Focused offline verification: 48 texture/QA tests passed; added void/failure-identity and
multi-turn backfill checks also passed. Historical texture/QA backfill repeated with
**0 additional calls**. The ledger contains **340 effective calls** (334 historical + six
pilot calls); the synthetic test record and its void annotation are excluded.

**Final gate: GREEN**, all seven selected stages, **101.095 s**.
Command: `scripts/dev/gate --json`; retained output: `$TURKU3D_WORK/reports/costbench/gate-delivery.json`.
Benchmark Ruff check/format also passed. The slower wall time reflects concurrent host load;
no test or timing limit was weakened. Full task status remains BLOCKED on incomplete pilot
coverage and independent label truth, not on this final gate.
