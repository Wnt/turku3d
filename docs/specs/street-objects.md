# Street objects: descriptions and assets

The fixed objects of the street in the world pipeline (`docs/design/world-pipeline.md`): sign posts, street lamps,
fences, gate posts and railings today; traffic lights, other poles and furniture follow in the same shape. Code:
`pipeline/turku3d/world/kinds/{signpost,lamp,fence}.py` with shared readers and geometry in
`pipeline/turku3d/world/objects/`.

## Rules common to every kind

- **One entity per real object** (`signpost/s<register id>`, `lamp/<register id>`, `fence/r<E dm>_<N dm>_<length dm>`,
  `fence/g<E dm>_<N dm>`). `at` is the base point `[E, N, H]` (EPSG:3877, N2000) and the entity origin.
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
| `family` | `street-arm`, `street-top`, `park`, `wire-hung` |
| `height` | metres to the luminaire |
| `arm` | `{bearing, reach}` for `street-arm` |
| `span` | for `wire-hung`: `{ends: [[E, N, H], [E, N, H]], sag}`; `at` is then the point under the luminaire |
| `finish` | `galvanised` or `black` |
| `basis`, `guessed`, `measured.lidar`, `register` | as above |

## `turku-description/fence/1`

| Field | Meaning |
| --- | --- |
| `at` | first joint of the run |
| `type` | register class: `wood-fence`, `stone-fence`, `railing`, `gate-post` |
| `family` | `board`, `picket`, `stone-wall`, `railing`, `gate-post` (`mesh`, `wrought-iron` and `gateway` are planned) |
| `height` | metres above the ground at the joints |
| `line` | joints `[[E, N, H], ...]`: one panel between each pair, one post per joint |
| `colour` | `#rrggbb` of the paint (a guess from the lidar colours unless observed) |
| `break` | `panel` |
| `measured.lidar` | `{coverage, steps, height_p60, rgb}`: the share of 1 m steps along the line with returns between 0.25 and 3.5 m, and their height |

A register line that the lidar does not confirm (coverage under the family's threshold) and that was surveyed
before the scan is left out: an object in the wrong place is worse than a missing one.

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
