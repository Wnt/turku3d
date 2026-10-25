# Codex accounting, benchmark and routing — 2026-10-26

Status: **DONE for the quota-emergency scope**. The orchestrator cancelled improve experiments,
restricted the pilot to cheap low-effort models plus one Astra low reference, and requested
immediate development/light routing. Nine calls completed; zero image-generation calls.
Full expansion-quality qualification remains follow-up work. No main merge was performed.

## Geometry-derived labels

Twelve street-facing reference facades, eight distinct buildings. Exact object IDs and baseline
coordinates are matched to the accepted W1-03 building layer. Truth provenance and source hashes
are committed in `bench/codex/label-v2.json`. Forty known fields are scored; unknown fields are
excluded. Each model receives the same twelve 1024-pixel-long-side images in one call, at low effort.

| Model | Field agreement | Tokens/batch | Tokens/facade | Wall s/batch | Call failures |
|---|---:|---:|---:|---:|---:|
| gpt-6-luna | 37.5% | 29,504 | 2,458.7 | 10.1 | 0/1 |
| gpt-5.6-luna | 32.5% | 28,149 | 2,345.8 | 10.9 | 0/1 |
| gpt-5.6-terra | 35.0% | 29,675 | 2,472.9 | 33.2 | 0/1 |
| gpt-6-sol | 37.5% | 30,018 | 2,501.5 | 16.2 | 0/1 |

| Model | Floors | Known wall material | Roof shape | Roof colour |
|---|---:|---:|---:|---:|
| gpt-6-luna | 2/12 | 3/4 | 5/12 | 5/12 |
| gpt-5.6-luna | 1/12 | 2/4 | 8/12 | 2/12 |
| gpt-5.6-terra | 1/12 | 3/4 | 8/12 | 2/12 |
| gpt-6-sol | 1/12 | 3/4 | 10/12 | 1/12 |

These are **geometry-derived proxies, not manually reviewed visual truth**. Floors use rounded
wall-top minus base height  / 3.2 m; roof gables and split levels can bias that proxy. Roof shape is
flat/pitched from median City LoD2 roof-plane slope (< 10 degrees means flat). Roof colour is the
existing orthophoto median class (all twelve happen to be gray). Physical roof material is unknown
and unscored; it cannot be inferred reliably from colour. Four wall-material labels come from
explicit city semantics. No bay-count truth is invented. Some roof/floor evidence is not visible
in the reference crops, so the aggregate score cannot distinguish model error from unobservable
truth or proxy error. The operational prompt asks for visible floors; this proxy mismatch is
particularly severe in the floor column and must be resolved before qualification.

No small model earns an absolute production-quality label pass. 5.6-luna uses the fewest tokens;
6-luna ties the best agreement and is fastest. Neither observation establishes a safe passing route.

## Harder QA differences

Eight frozen pairs: two subtle facade colour shifts, two roof recolours, two missing procedural
balconies, and two extra-storey texture clones. Balcony fixtures add a controlled balcony to the
reference and remove it in the render; storeys are 2D pasted facade strips, not 3D reconstructions.
Pixel recipes and source hashes are versioned in `prepare_v2.py` and `qa-v2.json`.

| Model (low effort) | Strict recall | Unmatched predictions | Tokens/batch | Tokens/view | Wall s/batch | Call failures |
|---|---:|---:|---:|---:|---:|---:|
| gpt-6-luna | 12.5% | 3 | 27,470 | 3,433.8 | 16.3 | 0/1 |
| gpt-5.6-luna | 0.0% | 4 | 25,105 | 3,138.1 | 12.8 | 0/1 |
| gpt-5.6-terra | 25.0% | 4 | 27,169 | 3,396.1 | 39.7 | 0/1 |
| gpt-6-sol | 50.0% | 2 | 26,967 | 3,370.9 | 15.6 | 0/1 |
| gpt-6-astra | 37.5% | 3 | 27,532 | 3,441.5 | 41.2 | 0/1 |

Matching is one-to-one, exact category and IoU >= 0.25. Unmatched predictions are the metric's
false positives; category/bbox mismatches can contribute and are not necessarily hallucinations.
This set has no unchanged controls, so it does not estimate production false-positive rates.
The earlier easy v1 set had four unchanged controls and four gray patches; all six tested models
localized every patch and emitted no issues for unchanged controls. Strict-category winners were
6-sol, 5.6-sol and 5.6-terra. Full v1 numbers remain in `bench/codex/results/pilot-v1.json`.

**QA recommendation: gpt-6-sol low.** It is the cheapest tested cell whose hard-set strict recall
is at least the Astra-low reference and whose unmatched count is no worse. This is a relative
reference comparison, not an absolute production-quality pass. It uses 26,967 versus 27,532 tokens
and takes 15.6 versus 41.2 seconds; a single call per cell cannot establish stable latency or cost
rankings. Its 50% strict recall is inadequate evidence for unattended visual acceptance. In
particular, neither Sol nor Astra strictly matches the planted extra-storey defects.

## Quota and accounting

V2: **251,589 input+output tokens**, 196.0 seconds summed model-call wall time, nine calls,
48 labelled facade evaluations and 40 QA pair evaluations. All nine completed successfully.
Cached input is zero. Reasoning output is a subset of output, not added again. Exact component
counts, per-field/per-view metrics and model answers are committed in
`bench/codex/results/pilot-v2.json`; raw events are in
`$TURKU3D_WORK/reports/costbench/v2/pilot/`.

Weekly snapshots were **35% before and 36% after** (+1 percentage point account-wide). Concurrent
workers prevent benchmark-only quota attribution; integer snapshots are not a token exchange
rate. The guard retained its 3-point allowance and 1-point reserve, with the user-authorized 95%
absolute ceiling. It paused twice on inconsistent/unavailable quota telemetry; resumes reused
terminal records without new calls. The reset timestamp and baseline remained unchanged.
V1 previously used 165,987 tokens with account-wide 75→77%; these are separate quota windows.

The append-only `~/turku3d/ledger/codex-calls.jsonl` is populated with 334 historical calls,
six v1 calls and nine v2 calls (349 effective records at this checkpoint). A synthetic test
entry was voided by an append-only annotation; all tests now isolate their ledgers. Backfill
is idempotent and reads event usage where available. Missing historical effort, wall times
and usage remain unknown; archived task model was inferred from the old hard-coded runner.

Historical workload totals: 100 building-label attempts used 4,310,152 tokens; 162 improve attempts
used 13,867,462; five library attempts 211,615 ; 43 QA attempts 883,922 ; 24 task turns 259,662,433.
Retries contribute to unit cost; repeated units in a logical job count once. These workload
averages do not predict universal cost per physical facade. `scripts/dev/codex-cost --by job_type`
prints calls, components, wall time and per-facade/per-view costs; `--by model`, `task` and `day`
provide the other requested groupings.

## Routing

`textures/codex-routing.toml` is read by textures, QA and codex-task. Explicit model/effort wins
over `TURKU3D_CODEX_MODEL` / `TURKU3D_CODEX_EFFORT`, then configuration. An ordinary task resume
retains its resolved selection; explicit `--route` selects fresh defaults. The route is recorded
as ledger job type for each task turn. `TURKU3D_CODEX_TASK_ROUTE` provides task-route selection.

| Job type | Configured model / effort | Basis |
|---|---|---|
| Development task | gpt-6-sol / medium | Operator-selected cost default, not measured development quality |
| Light / task_rebase | gpt-6-luna / low | Operator-selected for rebase, verify and finish follow-ups |
| QA | gpt-6-sol / low | Cheapest measured route matching Astra-low hard-set comparison |
| Label / building_label | gpt-6-astra / medium | Retained pending qualifying evidence; no small-model pass established |
| Improve / library | gpt-6-astra / medium | Existing route; image experiments cancelled and not run |

The original full 7-model low/medium matrix and improve variants were superseded by the quota
emergency instruction. No effort, crop-size, feedback or retry quality comparison for improve
is claimed. Image experiments require renewed user authorization before running.

## Verification

The scoped gate passed all seven selected stages in 38.614 s, with serialized stages, two pytest
workers and at most three tooling workers. New coverage exercises route switching, explicit
CLI precedence over environment, fallback routing and the 95% budget boundary. A final gate
run is recorded below. Reproduction uses the normal gate implementation with reduced concurrency:

```sh
source ../sandbox.env
scripts/dev/codex-cost --by job_type
PYTHONPATH=scripts/dev TURKU3D_JOBS=3 .venv/bin/python - <<'PYCODE'
from pathlib import Path
import tomllib
import gate_lib
root = Path.cwd()
config = tomllib.loads((root / 'scripts/dev/gate.toml').read_text())
config['gate']['workers'] = 1
config['gate']['python_workers'] = 2
raise SystemExit(gate_lib.run_gate(root, config, gate_lib.changes(root, 'main'), as_json=True))
PYCODE
```

## Follow-ups

1. Resolve visible-floor versus geometry-floor proxy mismatch and roof visibility before choosing
   a production label winner. Retain provenance; do not silently relabel truth to model answers.
2. Add harder QA unchanged controls, realistic rendered edits and repeated cells; quantify recall
   beyond the eight 2D stress cases before unattended acceptance.
3. Only after renewed authorization, run the four-facade improve lever comparisons and Astra
   reference assessments; keep these separate from the completed zero-image emergency pilot.
4. At integration retain the load-robust gate owner's richer shared `TURKU3D_JOBS` implementation
   if it overlaps the local tooling-test worker cap.

Final scoped gate: **GREEN**, seven selected stages, **25.802 s**. Evidence: `$TURKU3D_WORK/reports/costbench/gate-v2-final.json`.
