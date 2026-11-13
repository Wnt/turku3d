# Street objects: descriptions and assets

The fixed objects of the street in the world pipeline (`docs/design/world-pipeline.md`): sign posts, street lamps,
overhead wires with their hung lamps, fences, gate posts, railings and street furniture today; traffic lights and
other poles follow in the same shape. Code: `pipeline/turku3d/world/kinds/{signpost,lamp,wire,fence,furniture}.py`
with shared readers and geometry in `pipeline/turku3d/world/objects/`.

## Rules common to every kind

- **One entity per real object** (`signpost/s<register id>`, `lamp/<register id>`, `wire/w<E dm>_<N dm>_<length dm>`,
  `fence/r<E dm>_<N dm>_<length dm>`, `fence/g<E dm>_<N dm>`, `furniture/<layer><register id>`). A register that
  repeats a row gives one entity. `at` is the base point `[E, N, H]` (EPSG:3877, N2000) and the entity origin.
- **Stored over automatic:** every top-level field of a stored description replaces the automatic one whole
  (`objects/common.py: describe`). A stored `{"plates": [...]}` replaces all plates; a stored `{"height": 1.1}` only
  the height. `tier` and `provenance` come from the envelope.
- **Measured, guessed, register:** `measured.<source>` holds what a measurement returned, `register` what the
  register row said, and `guessed` lists the fields that are neither (`["family", "height", "arm"]`). `basis` says in
  words what the automatic description rests on.
- **Interactive-ready:** every hittable piece is one instance row of a shared asset whose pivot is its base
  (y = 0 at the ground). `interaction` carries `behaviour` (`fixed`, `knock-over`, `breakable`), `shape`, `size`
  (metres, asset-local) and `mass`; the kind copies them to `Instances.behaviour / collision / mass` with one stable id
  per row (`<entity>#post`, `<entity>#plate0`, `<entity>#p3`). The rows of one sign post move as one body; the
  panels and posts of a fence break one by one (`break: "panel"`).
- **Shared assets:** geometry is quantised (post height to 0.1 m, lamp height and arm reach to 0.25 m, fence height
  to 0.1 m) so identical objects hash to one store asset.
- **Closed solids** at true thickness everywhere (`objects/tests/test_objects.py` checks every asset is closed and
  faces out). No alpha-masked cards so far.

## `turku-description/signpost/1`

| Field | Meaning |
| --- | --- |
| `at` | base of the support |
| `support` | `{type: "post", height, radius}` or `{type: "host", host: "lamp/<id>", height}` when the plates hang on a lamp pole |
| `facing` | `{bearing, method, conf}`: the grid bearing the faces look towards. `method` is `traffic-rule` (against the traffic of the nearest carriageway side) or `observed` |
| `plates` | top to bottom: `{code, register, label, note, turn, blue}`. `code` is the 2020 sign code (`C32`, `E1`, `H24`); `label` the register's text (speed digits are read from it); `note` free text of an additional plate; `turn` degrees about the post relative to `facing` (180 = the back-to-back twin of a crossing sign); `blue` a blue additional plate |
| `placement` | `register`, or `register+lidar` when the lidar moved the axis onto the measured post |
| `measured.lidar` | `{offset, top, returns}` of the post found in the lidar |

Sign faces are drawn by `objects/signfaces.py` from the code: a planar partition of coloured polygons (vertex
colours, family `sign`, params `{code, uv: "atlas", width, height, text?, blue?}`) that is the front of a 3 mm
aluminium slab. A code with no drawing gets the generic face of its class (triangle, disc, blue square, plate) and
`known: false`.

## `turku-description/lamp/1`

| Field | Meaning |
| --- | --- |
| `at` | base of the pole |
| `family` | `street-arm`, `street-top`, `park`, `span-pole` (a plain pole a span wire ends at) |
| `height` | metres to the luminaire (to the wire for a span pole) |
| `arm` | `{bearing, reach}` for `street-arm` |
| `finish` | `galvanised` or `black` |
| `basis`, `guessed`, `measured.lidar`, `register` | as above |

A measured top above 13 m is a tree or a facade at the register point, not the lamp: such a pole falls back to
the street rule (9 m arm lamp within 9 m of a carriageway, else a 4.5 m park lantern) and says so in `guessed`.

## `turku-description/wire/1`

One entity per overhead wire the survey lidar shows (`objects/wires.py`, the old generator's stage: wire returns in
free air, straight in plan and a parabola in profile, ends reached to a facade, a pole or another wire).

| Field | Meaning |
| --- | --- |
| `at`, `a` | first end `[E, N, H]`, the entity origin (a wire is not a ground object) |
| `b` | second end |
| `sag` | metres the wire hangs below its chord at mid span |
| `type` | `span` (lighting span between facades or carrying lamps), `utility`, `power` |
| `ends` | what each end hangs from: `facade`, `pole`, `knot`, `free` |
| `lamps` | hung luminaires: `{at: [E, N, H] on the wire, basis, guessed?}`; rows `<entity>#lamp<k>` of one shared asset |
| `measured.lidar` | `{length, extended}`; `extended` when the wire was carried on to a guessed lamp |

A lighting span on which the scan shows no lamp gets one over each carriageway centreline it crosses, at least
7 m over the ground and 4 m from any other lamp (`guessed: true`): the old generator's rule.

## `turku-description/fence/1`

| Field | Meaning |
| --- | --- |
| `at` | first joint of the run |
| `type` | register class: `wood-fence`, `stone-fence`, `railing`, `gate-post` |
| `family` | `board`, `picket`, `iron` (open bar fence), `stone-wall`, `railing`, `gate-post` (`mesh` and `gateway` are planned) |
| `height` | metres above the ground at the joints |
| `line` | joints `[[E, N, H], ...]`: one panel between each pair, one post per joint |
| `colour` | `#rrggbb` of the paint, from a small palette: cream unless the scan's colours are clearly warm (a guess until observed) |
| `break` | `panel` |
| `measured.lidar` | `{coverage, steps, height_p60, rgb, returns_per_m}`: the share of 1 m steps along the line with returns between 0.25 and 3.5 m, their height, and the returns per metre of fence |

Boards return the scan all along (20 to 35 returns per metre); a wood-class register fence with under 4 is open
bars (`iron`), and its height is read from the returns under 1.6 m only, because what stands higher over such a
line is the planting behind it.

A register line that the lidar does not confirm (coverage under the family's threshold) and that was surveyed
before the scan is left out: an object in the wrong place is worse than a missing one.

### Gates and mapped fence types

`fence/o<OSM node>` is a gate (`family: "gate"`): an OpenStreetMap `barrier=gate` node moved onto the register fence
line within 2 m of it, turned along that line (`bearing`), as tall and of the same `colour` as that fence, `leaf`
`board` or `iron` after the fence's family, `width` 3 m (two leaves) where a driveway passes through it and 1 m
where a path does (`guessed: ["width", "leaf"]`). A gate on no register fence is not drawn. A register line that
runs within 1.5 m of a mapped fence carrying `fence_type`, `height` or `material` keeps those tags in `osm`; a
mapped `fence_type` decides between open bars and boards before the lidar density does.

## `turku-description/furniture/1`

| Field | Meaning |
| --- | --- |
| `at` | base point |
| `model` | `bench`, `bin`, `waste-container`, `ashtray`, `table`, `info-board`, `info-pylon`, `guide-post`, `path-sign`, `lifebuoy`, `dispenser`, `charger`, `planter`, `bollard`, `bike-rack`, `bike-station`, `ticket-machine` |
| `variant` | bin paint (`green`, `black`, empty: steel) or the number of stands of a rack (two bikes per stand) |
| `bearing` | where the front looks (a rack's row runs a quarter turn from it); a rule, listed in `guessed` |
| `register` | the row's layer, type, species, model, maker, places |

The register labels are read as the old game read them (`furniture.LABELS`); wall ash trays, rescue ladders,
toilets, shelters and play equipment are not drawn. A city-bike station is drawn as its rack only.

## `turku-description/signal/1`

One entity per traffic-light post (`signal/n<OSM node><f|b|r|l>`: the post of a stop position's forward or backward
direction, or the right or left kerb of a signalled crossing).

| Field | Meaning |
| --- | --- |
| `at` | `[E, N, H]` of the post's base |
| `height` | post height in metres (3.45) |
| `heads` | `[{type: "vehicle" or "pedestrian", bearing}]`: the grid bearing each head's lenses look towards |
| `basis`, `guessed` | always `["position"]` until a panorama reading stores the post: no register lists signal posts |

Position: OpenStreetMap maps a signal on the way's centreline. The post stands to the right of the approach, 0.35 m
past the surveyed carriageway edge (`GIS:Katualueet`, medians excluded), the old game's rule
(`roads/signal_posts.py`); 3.85 m from the centreline where no carriageway is surveyed under the node. Posts nearer
than 2 m are one post with the heads of both. A crossing with a stop position within 30 m carries pedestrian heads
only. Compile: the post, vehicle heads and pedestrian heads are three shared assets; rows `#post` and `#head<k>`.

## Sources and what each is good for

| Object | Position | Type | Orientation | Dimensions |
| --- | --- | --- | --- | --- |
| Traffic signs | `GIS:Liikennemerkit`, one row per plate, within about 0.3 m | sign code, about one in ten `999` (unknown) | not in the register: traffic rule, conf 0.5 | post height from lidar |
| Lamp poles | `frn:CityFurniture_LOD2`, base point | none: family from the lidar | arm bearing from lidar | height from lidar for about two poles in three |
| Wire-hung luminaires | no register: lidar | lidar | span line | lidar |
| Fences, railings | `kanta:Rakenne` lines (wood 66636, stone 66630/66632, railing 62534) | class only | line | height from lidar; colour a guess |
| Gate posts | `kanta:Rakenne` 66114 points | | | default |
| Traffic lights | no register: OSM `highway=traffic_signals` nodes plus lidar poles | | | |
| Furniture | `GIS:Varusteet` (benches, bins), `GIS:Polkupyoraparkki`, `GIS:Pysakointiautomaatit`, OSM | model name | street direction | defaults |
