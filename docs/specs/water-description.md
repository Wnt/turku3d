# Water description

Kind `water` (`pipeline/turku3d/world/kinds/water.py`): the surface of open water, one entity `water/c_<i>_<j>` per
62.5 m cell that has any. First version, 2026-11-13.

| Field | Meaning |
|---|---|
| `cell` | `[i, j]` of the grid cell. |
| `level` | Height of the surface, metres N2000. Source fact: 0.0, the shared datum of the tidal river and the sea (the old generator's water level). A stored description may set it (ponds lie at their own height). |
| `areas` | The mapped water polygons clipped to the cell: `key` (`osm:<type>/<id>`) and `rings` (survey E, N; exterior first). Source: OpenStreetMap `natural=water`. |
| `surface` | Material: family `water`, parameter `body` (`river`, `sea`, `pond`). A stored description may replace it. |

Output: one flat sheet at `level`, UVs in metres. No height raster: the ground kind owns the height field and lowers
its skin to a bed 1 m under the water level inside the same outline (surface `use: water`, `level: bed`, see
`ground-description.md`), so nothing of the ground shows above the sheet. Banks and quays are the ground's slope for
now; quay and retaining walls become entities of a `wall` kind.

Not yet: inland levels measured from lidar, flow direction, waves and reflections beyond a plain glossy surface,
piers and floating decks.
