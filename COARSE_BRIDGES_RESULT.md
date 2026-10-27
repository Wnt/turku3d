## Result: DONE

## What changed

Draft OSM road, pedestrian and railway bridges now have separate colliding slabs, railings/girder sides, railway ballast, piers outside crossed-way corridors and ground-blended approaches. Abutment profiles clear roads by 4.5 m, rail by 6 m and paths by 2.5 m below the slab. Reused full-pipeline box geometry, mitered profile walls and collision-cell assignment. Optional edgeHeights survive graph merging and routing. Synthetic 150 m bridge geometry added 0.115 s and 3,076 triangles; actual per-tile geometry time is recorded in seconds.bridges, and the existing 8 MB visual cap remains enforced. Fence and facade integration preserved. Committed on the task branch; the runner lands it at turn end.

## How to verify

```sh
source ../sandbox.env
bash pipeline/env/run.sh pytest -q pipeline/tests/test_coarse.py -k osm_deck_height
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

## Gate output

Fast gate GREEN in 4.888 s after rebasing fence and facade changes; policy, Python/JS lint, game types and unit checks passed. Four synthetic profile tests passed in 5.81 s. An in-memory producer smoke check retained underpass ground and elevated only the bridge edge. No tile build, browser suite or deep gate run.

## Follow-ups

Check real-tile added seconds and long-span seam alignment where a complete bridge extends beyond the cached lidar margin during normal integrator/game testing.
