# Rebase verification after W0-C and W1-06

Rebased `wt/w1-11-visualqa` onto main at `7218b14`, including W1-06 `9523b80`.
The QA commits are now `7b4813b` and `3ec65de`. No merge into main was performed.

Conflict resolutions retain both intents:

- `mk/tile.mk`: native Mac fetch/build/validation, shared read-only raw inputs and local
  sandbox products. No remote targets were restored. QA retains explicit port override,
  safe server cleanup, `JUDGE=1` guard, parallel workers and manifest provenance. Its
  default port follows the native pipeline convention, sandbox base + 1.
- `qa/qalib/paths.py` and `mk/qa.mk`: `QA_DATA` overrides `TURKU3D_WORK/qa`, then
  `TURKU3D_DATA/qa`, then `~/turku3d/data/qa`. Portable `data/qa/` registry paths still
  round-trip through the selected root. The dependency stamp and W0-C's forced reference
  regeneration are both retained.
- `qa/README.md`: native data/output paths and local commands, with all QA capabilities
  and the remaining separate tour/photosphere cache location documented.

A regression test now checks Python path precedence and portable-path round trips.
Read-only Make probes also confirmed native defaults, sandbox paths, base + 1, the
explicit port override, judge guard and absence of remote tile targets.

## Commands and results

```sh
source ../sandbox.env
TURKU3D_PORT_BASE=5251 /usr/bin/time -p make -j2 qa-test game-test
/usr/bin/time -p make qa-regress QA_BASELINE=w1-20-aligned QA_RUN=w1-20-2fc2a3f
```

- QA Python: **31 passed**, 6.09 s.
- QA browser contracts: **6 passed**, 13.42 s including browser lifecycle.
- Game: **45 passed** across 7 files, 3.69 s.
- Combined test command: **exit 0**, 14.97 s wall time.
- Regression: **exit 0**, +0.05 mean delta against a 0.5 margin, 0.88 s including setup.

Logs: `$TURKU3D_WORK/qa/rebase-gate.log` and `rebase-regress.log`.
The regression uses existing measurements. No new photo downloads, game renders or
Astra calls were made during this rebase. Original measured commit IDs in the historical
reports are preserved; these scores do not claim to measure the rebased game.

The expected building-layer acceptance dependency remains: rerender and judge the
assembled W1-03/W1-04 AOI when it is available.
