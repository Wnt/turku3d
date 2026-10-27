# Landmarks layer

Churches, towers and ships are not houses: roofer and the city LoD2 fit planar roof faces per
footprint, which turns a spire into a box. Allowlisted landmarks are meshed straight from the
full-density city lidar instead. Background and alternatives: `docs/research/landmarks.md`.

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_landmarks.py
bash pipeline/env/run.sh python -m turku3d.landmarks              # the playable tile list
bash pipeline/env/run.sh python -m turku3d.landmarks --tiles t0_3_-1
make refresh                                                        # runs it with the others
```

## The allowlist

`landmarks.toml` names each landmark, the buildings-layer footprint it replaces (`building`, a
kantakartta or `gis-` id) or its own `polygon_en`, the method, the heightfield cell and two
triangle budgets: the visual shell (at most 60,000) and the collision shell (at most 2,000,
the building collision limit of ARCHITECTURE.md D5). `wall` and `wall_rgb` choose the
procedural wall material and its colour. The orchestrator decides what joins the list.

## Method `lidar-heightfield`

Per landmark, inside its owner tile's build (the L0 tile holding most of the footprint, as for
buildings):

1. Configured lidar classes within 1 m of the footprint (class 6 by default), ground
   (class 2) returns in a 3 m ring. The boat uses classes 1 and 12 and a water-based hull draft.
2. A heightfield of the highest return per cell; empty cells take their nearest neighbour,
   a cell 3 m above all eight neighbours is clipped to them.
3. Greedy insertion into a constrained Delaunay triangulation of the footprint rings (holes
   included): the worst-approximated cell or boundary sample goes in next, until the closed
   shell reaches its budget or 5 cm. The shell is the roof surface, one vertical wall quad
   per boundary edge down to 0.5 m below the lowest ground return, and a flat base. It is
   closed and outward-wound by construction; the budget counts every triangle.
4. Visual: roof triangles (normal y >= 0.2) carry the 2022 true ortho at 10 cm/px, projected
   from above; walls and steep faces carry a seamless procedural 4 m material (metre UVs,
   REPEAT sampler). The base is not shipped. Collision: the same method at the collision
   budget, `roof` on the surface and `building` on walls and base.

The layer writes `<tile>.vis.glb`, the owning `c_i_j.col.npz` (with `building_ids` and
`building_index` like the buildings layer), `<tile>.records.json` and `metrics.json`. The
MANIFEST's `params.replaces` lists the replaced building ids. `turku3d.package.assemble`
drops those buildings' collision triangles and visual nodes, at every level; landmark meshes
stay whole in the 500 m and 1 km parents because they are seen from afar.

Inputs: `kanta_Rakennus` and `GIS_Rakennukset` of the tile's source area, the pistepilvi LAZ
under it and `raw/ortho/ortho2022to/<source area>.vrt`. No street imagery is read.
The allowlist contains Turku Cathedral (`103371570S`, owner tile `t0_3_-1`) and the
restaurant boat at Itäinen Rantakatu 11 (owner tile `t0_0_1`). Existing sandbox packaging
measured the cathedral tile at 7,756,116 wire bytes (3,890,833 visual + 3,865,283 ground
textures), below the 8,000,000-byte cap, with the 40,000-triangle cathedral visual budget.
Boat surface spikes and collision accuracy near the cathedral spire remain playtest follow-ups.
