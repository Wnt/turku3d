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
| `lanes` | Dashed lines along the carriageway (between the directions, between lanes of one direction, along cycle lanes and parking bays): `key`, `line`, `start` (station of the line's first point along the whole street, so dashes continue across cells), `dash` and `gap`. |
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
- **Lane lines.** A street carries lines when its class is tertiary or above, or residential/unclassified with
  tagged lanes and a speed limit of 40 km/h or more (30 zones have none in the photos). The carriageway is measured
  across the way every 8 m (`_section`: width and where its middle lies beside the way); where the tagged lane
  count gives lanes 2.6 to 4.6 m wide, every lane boundary is drawn there: 3 m dashes with 5 m gaps between the
  directions, 9 m gaps between lanes of one direction (one-way streets with two or more lanes included). Where the
  count does not fit, a two-way street keeps one line on the way itself. No edge lines yet.
- **Railways.** A track bed (`use: railway`, key `osm:way/<id>:bed`, rank 1.5: over green and unmapped land,
  under the street register) 2.3 m either side of every track of the `rail` kind, and a 3 m unmown verge (`use:
  rough`, key `...:verge`) beside it wherever nothing else is mapped.
- **Verges and woodland.** The green register's `Kasvillisuus` and `Metsä` and OpenStreetMap woods and scrub are
  unmown grass (`mown` 0.2 and 0), not bare soil; soil stays for planting beds.
- **Bridges over water** (`kinds/ground/decks.py`, field `decks`: `key` and `rings` of each deck outline in the
  cell). A way with a `bridge` tag that crosses mapped water is a deck: its outline is the register's `Silta`
  polygons around the way (the one a street's centre line runs along is carriageway level, the others footway
  level), or a strip as wide as the way's class with the surface `osm:way/<id>:deck` where the register has none.
  Relief nodes on it take the straight line between the ground just beyond its two ends, so the street crosses at
  the banks' level instead of dipping to the water the scan saw. The deck is still part of the single-valued skin:
  its sides fall to the river bed as a bank and nothing passes under it. Bridges over land stay as scanned.
