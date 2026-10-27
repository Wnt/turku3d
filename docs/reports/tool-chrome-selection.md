# Chrome spec selection for the integrator (27 Oct 2026)

`scripts/dev/spec_selection.py` picks the real-tile Playwright specs an integration batch can
affect from `scripts/dev/gate.toml` (stage `game-e2e`), caches each passing spec per project and
schedules a full suite every 2 hours with a landing since the last one. Pages publishes only
after a green full suite.

## Before

The integrator's Chrome stage ran all 9 tile specs (17 tests) on both projects, 34 tests, for every
batch that touched `game/*` or `qa/*`, and any batch whose data changed:

| Integration | Batch | Chrome s | Host load (1 min, end) |
| --- | --- | ---: | ---: |
| b98401ef53d8.1 | game code | 304.8 | not recorded |
| 6216d61c1eae.1 | game code | 364.9 | not recorded |
| e323e39a5d32.1 | game + pipeline code | 1085.9 | not recorded |
| 6759d964c76f.1 | pipeline code (tool-tile-expansion-s2) | 712.9 | 253 |

## Selection for the last ten real landings

Computed with the selector from `landed.jsonl` (`git diff --name-only before..commit` on main) and
`landing_class.path_class` (these rows predate the recorded `class` field; `*` = derived). Tests are
real-tile suite tests × projects; the integrator ran 34 for each of them.

| Landing | Commit | Paths | Class | Tile specs | Projects | Tests run / full |
| --- | --- | ---: | --- | ---: | --- | ---: |
| tool-tile-expansion-s2 | 6759d964 | 13 | code* | 5 | webgpu | 13 / 34 |
| gameplay-safe-reset | ab0f025a | 38 | code* | 9 | webgpu+webgl | 34 / 34 |
| tool-coarse-tiles-live | 460add37 | 20 | code* | 9 | webgpu | 17 / 34 |
| fix-finish-acks-return | 0750f0f0 | 2 | code* | 0 | - | 0 / 34 |
| gameplay-save-menu | b9f18c1e | 40 | code* | 9 | webgpu+webgl | 34 / 34 |
| content-facades-1027-1402 | 12c4c1a1 | 10 | code* | 0 | - | 0 / 34 |
| gameplay-delivery-pool | 645e60cf | 69 | code* | 9 | webgpu | 17 / 34 |
| gameplay-two-wheeler | e9fe4f80 | 47 | code* | 9 | webgpu+webgl | 34 / 34 |
| fix-finish-acks-queue-return | 3bafdb32 | 3 | code* | 0 | - | 0 / 34 |
| tool-landing-classes | 9a782c5a | 15 | code* | 0 | - | 0 / 34 |

Four of ten landings (tooling and a facade content batch) run no browser test, three run half or
less, and the three gameplay landings that changed physics or render code still run every test on
both projects. The per-spec cache removes repeats on top of this: a bisection build, a Chrome rerun
or a later batch with the same bundle and tile manifest runs only specs that have not passed for that
exact input.

## After

For tool-tile-expansion-s2 (6759d964) the selection is 5 specs on webgpu, 13 tests. Their share of
that integration's measured per-test Chrome time was 553 of 2195 test-seconds (25 %), so the stage
would take about 180 s instead of 713 s at the same load. The measured integrator number for the
first selected code landing is a follow-up of this task (`tool-chrome-selection-s2`) once this
change is live; the first integration after landing is the scheduled full suite.

A sandbox run of the same code path (`spec_selection.run` through `host_slots.py browser`, one
worker, this sandbox's game build and a clone of the live tiles, host load 54):
`tests/e2e-tile/minimap.spec.ts` on webgpu ran in 7.6 s and the repeat was a cache hit in 0.0 s;
`tests/e2e-tile/tile.spec.ts` on webgpu ran in 118 s with the seam test red (the same failure as
integration ab0f025ade08.1 on main), and the red result was not cached.
