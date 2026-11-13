# Wall description

Kind `wall` (`pipeline/turku3d/world/kinds/wall.py`): walls that hold the ground, one entity `wall/c_<i>_<j>` per
62.5 m cell that has any. First version, 2026-11-13: quay walls along open water only.

| Field | Meaning |
|---|---|
| `cell` | `[i, j]` of the grid cell. |
| `level` | Height of the water the walls stand in, metres N2000 (0.0, as in `water-description.md`). |
| `walls` | One row per run of quay inside the cell: `key` (`quay:<cell>:<n>`), `line` (survey E, N every 2 m along the mapped water outline), `inland` (unit vector per point, towards the land), `top` (height of the crest per point, metres N2000). |
| `masonry` | Material of the courses: `family`, `params`, `color`, `roughness`. Source default: rock-faced dark reddish-grey `granite` with ashlar relief, 0.5 m courses, water-stained. A stored description may replace it. |
| `coping` | Material of the coping stones (version 2): pale cut `granite`, 1.2 m stones. A stored description may replace it. |

Where a wall stands (source facts, `kinds/ground/banks.py quay_lines`): on the outline of OpenStreetMap
`natural=water` wherever a polygon of the street register (`GIS:Katualueet`) lies within 8 m or a mapped wall or quay
(OpenStreetMap `barrier=retaining_wall|wall|city_wall`, `man_made=quay`, the old generator's masonry) within 4 m,
even with a grass embankment behind it, and the lidar ground
3.5 m inland is 0.8 m or more above the water as a median over the run. The crest is that lidar height,
median-filtered over 7 points (14 m), because the scan smears the drop over about 2.5 m and single points fall on
stairs, tree pits and bridge feet. Green land beside water is a natural bank: no wall, the ground's own slope.

Output: per run two closed solids (water face, land face, top, underside, two ends): the courses, part `quay`, 0.5 m
thick on the land side of the outline, from 1.2 m under the water to 0.25 m under the crest; the coping, part
`quay-coping`, 0.25 m high and 0.7 m deep, 6 cm proud of the water face and 3 cm above the crest. UVs in metres
(along the wall, height). The ground kind
reads the same lines when it samples its relief: a node within 3 m of them takes the lidar height 3.5 m inland of
its foot on the line (`kinds/ground/banks.py held`, a function of the place alone, so cells agree on borders), so the ground meets the cap instead of sliding into the water; the water kind's sheet ends at
the water face.

Not yet: retaining walls away from water (OpenStreetMap `barrier=retaining_wall`, the old `coarse/retaining.py`),
steps, mooring rings and ladders; runs that continue across a cell border are separate
solids that meet end to end; the ground under bridge decks.
