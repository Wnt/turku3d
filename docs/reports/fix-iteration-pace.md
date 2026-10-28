# Iteration pace (2026-10-28)

Read-only baseline: `python3 scripts/dev/iteration_metrics.py --days 3`.
The captured [baseline](fix-iteration-pace-before.json) includes delivery latency,
every inventoried Chrome spec, deep stage, advisory and observed slot wait.
Missing results are unknown, never counted as passing checks.

## Slice 1: guaranteed publication capacity

The previous CPU allocation reserved six jobs for the code lane and four for data;
both running together left zero capacity for agents. Three existing agent builds
also delayed a new code integration until one finished. Assigning two jobs to each
integration leaves six for three two-job agent builds. All five fit on ten cores.
The code bundle itself has a measured three-day median near three seconds; six
reserved jobs did not justify making it wait for a tile build. Reservations now
cover actual build work, excluding checkout, release preparation, upload and diagnostics.

Verification: 13 process-level host admission tests in 4.731 s, and 32 synthetic
integrator tests in 28.84 s. In the admission regression, three running builds no
longer prevent either integration lane from starting. Historical maximum admitted
build wait: 3453.23 s; browser wait: 2325.12 s. These are measured historical waits,
not predictions of the revised scheduler.

## Baseline and scope

Twenty code landings with reconstructible Pages timestamps (including batched
landings) took a median **118.590 s**, maximum **1006.273 s**. Earlier rows predate
publication timing and are explicitly excluded; treating a later ancestor as their
first publication would invent hours of latency. Those twenty observations already
meet the requested 10/20-minute thresholds, unlike the slot wait tail. Historical
`stage_seconds.game-build` is a median 2.970 s, max 29.620 s; it does **not** support
the brief's 25–55-minute game-build observation for this current code lane. The
separate slot ledger confirms the historical wait problem.

## Slice 2: stop automatic exhaustive verification

All Chrome specs are demoted to explicit diagnostics; none is deleted merely for
an observed failure. Full-suite scheduling and data-list streaming Chrome are removed.
The deep tier runs only for explicit `integrator.py run --full` or `make gate-deep`,
not after every batch. The JSON inventory records every spec's last test-seconds,
observed runs and failures; unobserved specs have unknown runtime/failure yield.
In particular `parked.spec.ts` costs 1440.7 s (WebGPU) / 1920.1 s (WebGL),
`integration.spec.ts` 167.2 / 687.1 s and `tile.spec.ts` 293.4 / 589.1 s.
Repeated known-red results are not evidence of newly caught regressions.

All ten deep stages are demoted from automatic batch runs. Over 34 observed logs:
dependency-imports median 49.949 s, zero failures; validator-tests max 184.574 s,
zero failures; tooling-tests median 76.984 s, five failures; pipeline-tests median
80.333 s, seven failures; integrator-tests median 68.556 s, two failures. The
inventory includes the remaining stages and separates cache hits (zero seconds).
Keep cheap data audits: collision median 8.191 s / 5 failures in 28 observations,
facade-ground audit 1.349 s / 10 in 28, car report 10.162 s / zero in 9.
These remain scoped to changed colliders/parked cars and run after publication.
AGENTS.md now defaults to one focused check and one changed real tile/instance,
with extra tiles/views only for a relevant boundary or input case.

## Slice 3: short checks and bundle reuse

Reserve two one-job short-check slots alongside two two-job bulk builds and both
two-job publishing lanes (10 total). The largest bulk count that preserves all
these shares is two; this replaces the earlier three-build count. Normal game
checks use the short lane and never queue behind bulk tile work. A 300 s queue
limit for short checks/Chrome reports exhaustion instead of silently waiting.
Checks serialize as `kind: build, purpose: check` for old live readers; a first
four-second benchmark exposed an incompatible `kind: check`, which was corrected
and covered by a compatibility regression before landing.

Measured in this sandbox: public cold `make game-build` **4.044 s**, hash-verified
repeat **0.320 s** (12.6x faster); both exit zero. The cold run includes types and
both the public and solo companion bundles. The stamp includes source, dependency,
Node version, Vite environment and all output-file hashes. Missing, extra or corrupt
outputs rebuild. Cache hits take no host slot. The shared live bundle key now
excludes runtime tile/layer/facade data and prior tile-stage keys, which previously
invalidated the bundle for unrelated data changes. Environment changes still miss.
