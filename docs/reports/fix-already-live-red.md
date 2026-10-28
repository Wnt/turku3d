## Result: DONE

## What changed

Fixed data-lane duplicate scheduling and made publication retries a no-op.

The 2026-10-28 successful row f3d8add47be3.3 began at f3d8add, carried the parallel code lane's ca3be1ac game, and published ca3be1ac-c7f5b39d4d73. Its `from` remained f3d8add while `to` became ca3be1ac. The residual facade-approval delta triggered another run because scheduling checked only `from`. The next row ca3be1ac48fa.2 rebuilt the identical key and failed when prepare correctly refused to overwrite live. Release retention only deletes obsolete directories; it does not rename releases.

Scheduling now recognizes successful destination commits as well as attempted base commits. Live records persist the input data key and tile builder; legacy releases are recognized by their existing deterministic name. Both scheduling and run entry skip already published inputs, including the interval before Pages/advisories write the ledger. Publication rechecks under the publication lock and records `skipped` if another publication already supplied those inputs. Skips exit successfully; stale refreshes still distinguish tile builders.

Added pure decision tests to the fast publish stage and extended the existing synthetic lane-race regression to verify that a residual approval delta neither reschedules nor rebuilds the published inputs.

## How to verify

```sh
source ../sandbox.env
.venv/bin/python -m unittest scripts/dev/tests/test_integration_data.py scripts/dev/tests/test_integration_ensure.py scripts/dev/tests/test_integrator.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

## Gate output

Focused suite: 41 tests passed in 45.208 s. Fast gate: GREEN in 2.409 s after the documentation line-budget correction (cached stages reused; initial full fast run took 25.395 s with only that policy failure).

## Follow-ups

None. The runner lands the committed branch when this DONE turn ends.
