# Ground description

What the ground of one 62.5 m cell is, stored as measured data: `turku-description/ground/1`, the description of
the `ground` kind in the [world pipeline](../design/world-pipeline.md). The entity id is `ground/c_<i>_<j>`.
Generator: `pipeline/turku3d/world/kinds/ground/`.

A description holds the survey facts of the cell and one chosen cover per surface. Everything is in the survey
frame (ETRS-GK23 east, north; N2000 heights) and in metres unless a field name says otherwise.

| Field | Meaning |
|---|---|
| `cell` | `[i, j]` of the cell on the grid (i east, j south). |
| `relief` | The lidar ground as a node lattice: `step` (0.5 m), `halo` (nodes beyond the cell edge on every side), `side` (nodes per row and column), `heights_mm` (row-major from the north-west node, integers). |
| `surfaces` | Polygons that say what the ground is, clipped to the cell and its halo. Each has `key` (`street:<id>`, `green:<id>`, `osm:<type>/<id>`, `#n` for further pieces), `source`, `use` and `surface` (the register's own words), `level` (`low` for carriageways, `high` for everything else), `rank` (the lower rank wins where polygons overlap), `rings` (exterior first) and `cover`. |
| `crossings` | Marked pedestrian crossings: `key`, `line` (the walking line) and `width`. |
| `lanes` | Dashed centre lines: `key`, `line`, `start` (station of the line's first point along the whole street, so dashes continue across cells), `dash` and `gap`. |
| `base` | The cover of land no surface describes. |
| `kerb` | The cover of the kerb stones. |

A **cover** is `{family, params, color, roughness}`: a material family of
`pipeline/turku3d/world/families.json` with its flat parameters, and the mean linear albedo and roughness of that
family at those parameters. The families of the ground are `asphalt`, `paving`, `setts`, `cobbles`, `gravel`,
`grass`, `soil`, `sand`, `bedrock`, `kerbstone` and `road_paint`.

## Stored overrides

A stored description (`descriptions/ground/c_<i>_<j>.json`) replaces what the registers say, by key:

| Field | Meaning |
|---|---|
| `covers` | `{surface key: cover}`: the cover seen at that surface (a footway the register calls slabs that is asphalt). |
| `base` | The cover of unmapped land in the cell. |
| `kerb` | The cover of the kerb stones. |

## Output

- **Skin.** One continuous, single-valued surface over the cell, split into parts by cover. Carriageways lie at the
  measured kerb reveal below what borders them; the drop is a ramp under the kerb stone. Crossings and lane lines
  are regions of the skin with the `road_paint` cover, not layers above it. UVs are engine x, z in metres.
- **Kerbside strips.** Cycle lanes (`use: cycle-lane`, key `osm:way/<id>:cycle-<side>`) and parking lanes (`use: parking-lane`, key `osm:way/<id>:parking-<side>`, 2 m, from `parking:left|right|both=lane`) are surfaces of their own on the carriageway, so a stored cover can pave them differently from the lanes.
- **Water.** A mapped water area (OpenStreetMap `natural=water`) is a surface with `use: water`, `level: bed`: every skin vertex it uses lies 1 m under the water level (N2000 0.0), also in the height raster and the far level. It wins over other mapped areas, not over the registers (a bridge deck stays a street). The surface itself is the `water` kind's.
- **Kerbs.** Closed solids 0.20 m wide along the carriageway edge, from 0.10 m below the carriageway to the level of
  the footway.
- **Height field.** `Output.heightfield`: the skin's heights at 0.5 m spacing, stored beside the mesh.
- **Far level** (lod 1). The relief at 2.5 m spacing with the mean colour of the cover at each node, family
  `ground_far`.
