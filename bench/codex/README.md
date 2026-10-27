# Codex model benchmark

Uses the logged-in Codex CLI, never API keys or paid API services. Run from the repository root
with the sandbox environment sourced. The 2026-10-25 pilot is **incomplete**: quota stopped
six low-effort QA cells; reviewed facade truth is unavailable. See
[the report](../../docs/reports/codex-model-bench.md) and [numeric evidence](results/pilot-v1.json).

## Fixed corpora

- `qa-v1.json`: eight image pairs from four existing Phase 0/1 QA street references, resized to
  768 pixels maximum. Four identity controls and four deterministic grey patches at
  `[.2,.2,.8,.8]`, using `qa/qalib/degrade.py`. Original and generated SHA-256 hashes are pinned.
  Eight pairs are judged in one batch to make the pilot feasible; batch context is a confound.
- `facade-candidates-v1.json`: 12 hashed existing rectified crops, explicitly **unreviewed**.
  Floors/bays cannot be verified from these partial/occluded images alone. No labels are guessed,
  model predictions are not truth, and the runner refuses to score unreviewed label cases.
- `improve-v1.json`: six production prompts and hashed reference inputs; the first four are the
  requested pilot subset. Existing labels are prompt inputs, not reference truth. No improve
  calls were made in this pilot because the shared quota stop was reached first.

Images stay outside Git. Original read-only evidence locations remain in provenance; the
pinned QA, label-candidate and improve inputs have been copied into this sandbox’s report
root, using `${TURKU3D_WORK}` paths. QA pairs can be recreated deterministically:

```sh
source ../sandbox.env
.venv/bin/python bench/codex/prepare.py \
  --refs ~/turku3d/wt/w1-11-visualqa/work/qa/refs \
  --out "$TURKU3D_WORK/reports/costbench/qa-inputs" --manifest bench/codex/qa-v1.json
.venv/bin/python bench/codex/run.py --manifest bench/codex/qa-v1.json \
  --out "$TURKU3D_WORK/reports/costbench/pilot"
.venv/bin/python bench/codex/summarize.py --manifest bench/codex/qa-v1.json \
  --run "$TURKU3D_WORK/reports/costbench/pilot" --out bench/codex/results/pilot-v1.json
```

The existing pilot exits 3 at its budget stop. Do not create another directory to reset its budget.
A fresh quota window needs a separately authorized run; the old evidence remains immutable.
`--models` and `--efforts` restrict the seven-model, low/medium matrix. Image generation is
limited to Astra, 6.1 Sol and 6 Sol and four pilot facades. `--limit` bounds calls for smoke runs.

## Metrics and accounting

Labelling compares reviewed truth fields exactly; unknown fields are excluded. The corpus must
include the prompt and response schema when reviewed cases become available. QA uses one-to-one
category matching and IoU >= .25; duplicates become false positives. The offline summary also
reports localization without category matching as a diagnostic, preserving the original strict score.
Improve checks aspect error, normalized RGB edge seam MAE and mean-colour distance, followed by
an Astra medium judge of reference fidelity and cleanliness. Judge calls are separately charged.
Colour distance is a diagnostic, not illumination-invariant quality truth. Seam scores on whole
facades are diagnostics, not a seamless-tile acceptance threshold.

All real attempts use the production texture runner and shared ledger; cache hits are not charged.
The benchmark retains raw event streams, results, scores and budget state. SHA-256 input checks,
model/effort cache keys, a single-writer lock and a persistent quota baseline support resuming.
A successful generation without its score is reused and assessed on resume. No automatic retries.

The guard fails closed for unknown usage and quota resets. It refuses before each call at the
lower of 80% weekly usage or baseline + 3 percentage points, **minus a 1-point reserve**.
Calls are serial. Concurrent users' consumption counts against this allowance. Integer snapshots
cannot provide an exact per-job percentage attribution or guarantee the consumption of an
unbounded in-flight call; the reserve is conservative admission control, not a server-side token
limit. Token counts, including separately reported cached input and reasoning subsets, are exact
when present in `turn.completed.usage`.

## Offline verification

```sh
source ../sandbox.env
.venv/bin/python -m ruff check bench/codex
.venv/bin/python -m ruff format --check bench/codex
.venv/bin/python -m pytest textures/tests/test_codex_runner.py qa/tests/test_qa.py -q
scripts/dev/gate
```

Tests use small fake events and cover idempotent/concurrent append, partial tails, backfill retry
cost, effort cache invalidation, quota boundary/reset refusal, and one-to-one difference matching.
The gate owner has been asked to map benchmark-only edits to these tests; until integrated, run
these explicit benchmark checks as well as the gate.

## Geometry-derived / harder-difference pilot v2

```sh
source ../sandbox.env
.venv/bin/python bench/codex/prepare_v2.py
.venv/bin/python bench/codex/run_v2.py
```

Preparation reads the frozen W1-03 layer metrics/geometry, City LoD2 source and W1-09b
reference images, and writes sandbox-owned image copies plus versioned truth manifests.
These source sandboxes must remain available for reconstruction. Labels are geometry
proxies, not reviewed visual ground truth: floor height is 3.2 m; top wall height can
include a gable. Roof shape comes from City LoD2 planes, roof colour from the layer's
orthophoto median; physical roof material is unknown. Wall material is scored only
when the city semantics explicitly contain it. No bays or invented material truth.

V2 runs four small models at low effort on labels and hard QA plus one Astra low QA
reference: nine calls total, no retries, no image generation. A persisted 3-point quota
allowance, one-point reserve and absolute95% cap apply; missing telemetry stops calls.
A resume reuses terminal records (including failures), verifies frozen image hashes and
preserves the budget baseline. It never upgrades a failed call to a free retry. Inputs
and truth may not change during a resumed run. The run holds an exclusive output lock.

The harder QA set contains two colour shifts, two roof recolours, two procedurally added
balconies removed from the render, and two pasted-storey defects. It is a controlled
2D stress set, not a simulation of physically rendered architecture. Exact category and
IoU>=0.25 are required; unmatched predictions count as false positives. The older easy
set supplies unchanged controls. Results cannot establish production false-positive rates.
