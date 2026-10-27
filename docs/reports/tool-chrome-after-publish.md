## Result: DONE

## What changed
Code/full: game build with served tiles → live → Pages → Chrome → deep gate. Data: refresh/tile build/format and seams → live → Pages → collision/walk checks, car report, facade ground audit and selected Chrome. New landings cancel advisory command groups and record `advisory_interrupted`, freeing the lane for its next batch. Completed results update the exact release, inbox warnings and Pages notes without resending assets. Full-suite completion is recorded only after Chrome returns.

## How to verify
`scripts/dev/gate --fast --base main`

`.venv/bin/python -m unittest scripts/dev/tests/test_live_build.py`

`.venv/bin/python -m unittest scripts.dev.tests.test_advisory.AdvisoryTests.test_only_build_stages_precede_publication_for_every_kind scripts.dev.tests.test_advisory.AdvisoryTests.test_every_published_batch_deploys_pages_full_suite_or_not scripts.dev.tests.test_advisory.AdvisoryTests.test_newer_landing_cuts_post_publish_checks_short_without_marking_full_suite scripts.dev.tests.test_advisory.AdvisoryTests.test_red_full_run_is_advisory_and_reverts_nothing`

## Gate output
Final fast gate GREEN, 13.436 s (earlier uncached run: 63.831 s including environment synchronization under host load). Existing synthetic integration/advisory tests: 37 passed; rebased stage tests: 23 passed; focused ordering/cancellation/full-suite tests: 4 passed (24.354 s). No Chrome suite, deep gate, tile build or audit was launched for verification.

## Follow-ups
The first real post-change landing-to-Pages measurement is pending the runner's end-of-turn landing. Orchestrator request `38c6f9a5b5fe42738607dfd8a7432049` asks for that code row's `wall_seconds`, `pages.seconds` and `publish_seconds` from `~/turku3d/wt/.registry/integrations.jsonl`. `wall_seconds` includes diagnostics; `publish_seconds` ends at initial Pages completion. If a newer landing interrupts diagnostics, Pages retains its initial pending notes; the interruption and partial results remain in the exact release and row.
