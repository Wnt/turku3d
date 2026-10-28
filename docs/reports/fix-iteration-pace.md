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
