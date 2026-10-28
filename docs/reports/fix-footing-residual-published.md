# Published footing residual: data-lane builder selection

## Cause

`integrations.jsonl` row `dc63566f9056.2` named release commit `dc63566f`,
but `tiles_commit` was `eac45d3b76eeaa692ca3e5031a7e858c80ea7555`, before the
footing fix. `refresh`, `tile-build`, and `tile-validate` were all cache hits.
The audit then reported wall `1034470398_32` at +0.464 m.

The data lane tried the live tile builder first, and tried main only on a
build failure. A successful old build therefore bypassed landed pipeline
fixes. The facade audit runs after publication and never triggers that retry.
This was code selection, not a missing cache dependency: `live_cache.INPUTS`
includes `pipeline/*` for refresh and tile-build, and Python `fnmatch` matches
nested paths, including `buildings/grounding.py`, `buildings/build.py`, and
`refresh.py`.

## Repair

Data rebuilds now select main once and record that builder in `tiles_commit`.
The old-builder retry path is removed. Code-only batches continue shipping
served tiles without rebuilding. Explicit stale refreshes still use main.
No audit, threshold, shared input, or stage cache was changed.

The synthetic integrator cases verify selection when the old builder would
succeed, selection when the old builder is incompatible, explicit stale refresh
without a data change, tile provenance, and concurrent code/data publication.
All 32 cases pass (25.524 s). The fast gate passes (17.304 s, including 16.039 s
environment sync).

## Real integration evidence

The existing data rebuild that started at Unix time `1793154699.7765481`
records builder `dc63566f90569e3bd3c9e15cf9413c7eb4a7cfb3`. Its refresh rebuilt
the buildings layer in 32.329 s. Read-only inspection of those rebuilt layers
with the unchanged audit gives:

- Wall `1034470398_32` base: 24.83508717412581 m.
- Adjacent ground: 24.959297961769458 m.
- Signed residual: **−0.12421078764364779 m**.
- Full audit: **771 sampled, 771 approved, no failures**.

The sandbox report is `$TURKU3D_WORK/reports/footing-integration-check.json`.
The next completed data integration is **`dc63566f9056.3`** in
`~/turku3d/wt/.registry/integrations.jsonl`: `result=green`,
`tiles_commit=dc63566f90569e3bd3c9e15cf9413c7eb4a7cfb3`, no build cache hits.
It was the already-running explicit stale refresh, which selects main in the
same way as ordinary data rebuilds now do. Refresh took 80.182 s, tile-build
513.324 s, and format/seam validation 3.333 s. Its post-publication facade
check took 1.045 s and passed all 771 approved walls; the audit JSON is on
line 26 of `integration-dc63566f9056.3-advisory.log`. Live and Pages publication
completed. Its sole advisory is the separate collision check's missing curb
test input; there is no facade-ground-audit advisory.

The lane-selection repair itself remains on this task branch for runner-owned
turn-end landing. The real batch confirms that selecting main picks up the
already-landed footing geometry; the synthetic tests verify ordinary data
rebuilds now make that selection too.

## Commands

```sh
source ../sandbox.env
scripts/dev/codex-task wait-cmd -- python3 -m unittest discover -s scripts/dev/tests -p test_integrator.py
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
python3 scripts/dev/integrator.py status
```
