# Rail description

Kind `rail` (`pipeline/turku3d/world/kinds/rail.py`): the rails and sleepers of railway tracks, one entity
`rail/c_<i>_<j>` per 62.5 m cell that has any. First version, 2026-11-13.

| Field | Meaning |
|---|---|
| `cell` | `[i, j]` of the grid cell. |
| `tracks` | One row per run of track inside the cell: `key` (`osm:way/<id>`, `#n` for further runs of the same way), `gauge` (m between the running edges; 1.524 unless the way says otherwise), `electrified` (the way has a contact line), `start` and `stations` (m along the whole way, so sleepers and sections continue across cells), `points` (survey E, N and the track's level in metres N2000 per station) and `flush` (per station: the track lies in a street there). |
| `steel`, `running`, `sleeper` | Materials of the rail's sides, its running surface and the sleepers. Source defaults: weathered and polished `metal`, cast `concrete`. A stored description may replace them. |
| `sleeper_pitch` | Metres between sleepers (0.6). |

Which tracks (source facts): OpenStreetMap ways `railway=rail|light_rail|narrow_gauge` that are not in a tunnel,
not on a bridge and not on another layer. The level is the lidar ground along the way, at every metre the median
over 6 m either side (a function of the place alone, so cells agree). A station is `flush` where it lies in a
polygon of the street register (`GIS:Katualueet`): a level crossing.

Output: per track two rails as closed bars (72 mm head, 172 mm high) on whole-metre sections of the way, standing
on the sleepers with their top 0.20 m above the level (`RAIL_TOP`), in a crossing 15 mm above the street; the
running surface is a part of its own. Sleepers (2.6 x 0.26 x 0.2 m, 3 cm proud of the ballast) are instances of
one box on whole pitches of the way, none inside a crossing. No collision shapes: a car rolls over the rails on the
ground's own surface.

The ballast is not here: the ground kind draws a surface `use: railway` (crushed-granite `gravel`, grade
`ballast`) 2.3 m either side of every such way, under whatever the street register maps, and a 3 m unmown verge
beside it where nothing else is mapped (`docs/specs/ground-description.md`).

For other kinds: `rail.tracks(box)` lists the ways (`key`, `line`, `gauge`, `electrified`), `rail.level(line,
stations)` gives the smoothed ground under a track, and the rail head is `rail.RAIL_TOP` above it. Catenary masts
and wires along `electrified` tracks are the objects track's.

Not yet: tracks on bridges and embankment shoulders of their own (the lidar relief carries the embankment),
switches (two ways simply cross), buffer stops, platforms, rubber crossing panels, wooden sleepers on old sidings.
