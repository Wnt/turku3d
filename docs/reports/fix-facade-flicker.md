# Facade flicker: overlapping LOD2 part walls

The served `t0_-7_6` GLB at producer `6ba68a7902bf` contains two outward-facing
Linnankatu 72 (`way/93976301`, register `103371470J`) walls sharing 71.133 m
of frontage at roughly 0.1 mm separation. Both endpoint sets were confirmed in
the meshopt-decoded served GLB. `facade-programs.json` identifies rule-sampled
painting, with no stored map or reviewed overlay for this building.

Each LOD2 roof part is extruded as a closed solid. Narrow fallback roof strips
and independently simplified roof-part rings produce near-coincident outward
walls with independently assigned facade layouts. At Linnankatu 72, the fallback
strip is only 0.0274 m² but has a 71 m edge. The resulting depth competition
explains the texture swaps; changing atlas derivatives cannot fix it.

The same cached-shell audit identifies Blanko (Aurakatu 1, OSM restaurant node
68173711; building `way/90775556`, register `103277347A`, tile `t0_1_0`). Its lower
shell has 0.47 m and 0.17 m duplicate strips over a longer wall, totaling 5.4564 m²
of duplicated vertical surface. These extend down to the ground-floor base.

Before assigning paint, the generator now subtracts overlapping same-facing wall
areas within 5 mm, retaining exposed remainders and sloping tops. Plane distance
is measured over shared frontage, so a long edge's distant divergence cannot hide
a short overlapping strip. Exact ties have deterministic ownership. Collision
solids, roof meshes and stored facade assets remain reusable. Existing descriptions,
reviewed programs and map matching still supply the resulting walls' appearance.

## Scope

Read-only audit of the exact `tunnels` stage keys recorded in served tile stage
reports, before any rebuild. Count criterion: outward-facing surfaces within 5 mm,
parallel dot product > 0.99999, projected overlap > 0.01 m². Building IDs count once
per tile even when a passage splits their shell.

| Tile | Affected building IDs | Affected shell parts |
| --- | ---: | ---: |
| t0_0_0 | 28 | 29 |
| t0_0_1 | 9 | 11 |
| t0_1_0 | 15 | 18 |
| t0_1_1 | 13 | 16 |
| Core total | 65 | 74 |
| t0_-7_6 | 4 | 4 |

Applying the correction to those exact cached shells leaves zero overlaps under
the same criterion in all five tiles. This measures the geometry defect, not how
many facades a player can see flickering. Jonni owns the in-game visual check;
no screenshot, browser or drive test was run.

## Verification

```sh
PYTHONPATH=pipeline:textures PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_facade_wall_overlap.py pipeline/tests/test_facade_programs.py pipeline/tests/test_facade_assets.py
```

30 tests passed in 1.29 s. New regressions cover the long roof sliver, partial
sloping overlap, stable ties, short shared frontage and untouched opposite-facing
or separated walls. Private before/after audit JSON, cached shell snapshots and
the served GLB remain under the agent's `work/reports/flicker/` directory.
