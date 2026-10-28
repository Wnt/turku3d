# Data lane structure seams

## Cause

The two solid boundary failures are 25 mm foundations of stair cheeks in the
structures layer, not curbs, bridges, landmarks or stale ground:

| Cells | Boundary | Structure component | Lower / upper height (m N2000) |
| --- | --- | --- | --- |
| `c_3_5` / `c_4_5` | x = 250, z = 357.108111 | `stairs:cf75d809f66b1066:cheek:1` | 9.296331 / 9.321331 |
| `c_11_2` / `c_12_2` | x = 750, z = 130.092070 | `stairs:89f11f50a81d1d2b:cheek:-1` | 15.349246 / 15.374246 |

`structures.geometry.stairs` lowers each cheek foundation by 0.025 m.
`structures.layer.write_tile` snaps near-grid coordinates exactly and writes a
whole component to its owner's L0 collision cells only. The stair cheek endpoints
therefore landed exactly on an L0 boundary without matching solid faces in the
neighbour. Rebuilding the same layer inputs could not repair this ownership gap.
The original integration outputs reproduced both failures across 324 shared edges
in the repair sandbox.

## Repair

Assembly deduplicates structure triangle copies, preserving winding and material,
and distributes each intact triangle to every available ground collision cell its
bounds touch. This includes both sides of an exact grid boundary and all four cells
at a corner. No layer files are changed and no triangle is clipped. Structure
primitives retain their original ownership. The effective per-cell structure hash
enters the collision cache key, so a changed or removed neighbour contribution
invalidates a retained cell too.

## Verification

With the sandbox environment sourced, seed the failed integration's layers,
textures and distribution into the sandbox's own work directories, then run:

```sh
make tile-build AREA=playable-tiles T="t0_0_0 t0_0_1 t0_1_-1 t0_1_0 t0_1_1 t0_2_0 t0_2_2"
make tile-validate T="t0_0_0 t0_0_1 t0_1_-1 t0_1_0 t0_1_1 t0_2_0 t0_2_2" VALIDATOR_REQUIRE=format,seams VALIDATOR_ARGS="--checks format,seams"
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_assemble.py pipeline/tests/test_assemble_structures.py pipeline/tests/test_assemble_cache.py
scripts/dev/gate --fast --base main
```

The explicit `--checks` selects format and seams; `--require` alone still runs all
physics checks. Long commands must use `codex-task wait-cmd --` or be polled every
30 seconds. `tile-build` uses the host build scheduler itself.

Measured on 2026-10-28: the seven-tile assembly passed in 757.4 s (52 visual
products including parents/scenery, 180 collision cells). Format and seams passed
in 2.138 s of validator time / 2.5 s wall time: **324 shared edges, zero failures**.
The selected packaging suite passed **18 tests in 44.36 s**. The full selected fast
gate passed in **12.171 s** (the subsequent cached run passed in 0.732 s).

The runner lands this branch after the DONE turn. Post-landing data-lane status
must be checked in `~/turku3d/wt/.registry/integrations.jsonl`; the orchestrator
has been asked to resume this task for that check. No post-landing result is
claimed by this pre-landing report.
