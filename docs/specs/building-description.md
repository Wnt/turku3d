# Building description

What one building looks like, stored as measured data: `turku-description/building/1`, the description of the
`building` kind in the [world pipeline](../design/world-pipeline.md).
Schema: [building-description.schema.json](building-description.schema.json).
Owner: `w2-buildings` (generator). Filled by `w2-observe` (vision readings, priors). Stored by `w2-core`.

One document describes one building entity, `building/<permanent building id>`. The generator compiles it into a
mesh and material records. Nothing about a building lives in code: a part that only one building has is a named
[landmark component](#landmark-components) with parameters in its description.

```sh
bash pipeline/env/run.sh python -m turku3d.world.kinds.building.description FILE...   # validate, list what is left to priors
```

## Stored and effective descriptions

- The **stored description** is what observation and review wrote: any subset of the fields below, never `shell`.
- The **effective description** is what `describe` hands to `compile`: the automatic fit from source data (`shell`,
  `register`, a `style`, `wall_defaults` and a `roof` from priors) with the stored description laid over it.
  - `shell` and `register` always come from the sources.
  - `materials` and `opening_types` merge by name; `wall_defaults` merges field by field; stored values win.
  - Every other top-level field of the stored description replaces the automatic one.
- `shell` holds the LoD2 surfaces as rings of `[E, N, H]`, the terrain line around the footprint and the footprints
  and heights of touching neighbours, so that `compile` needs nothing but the description.
- `shell.passages` (only where there are any) is the open part of the ground storey: where the open map runs a way
  at ground level under a roof through or along the building (`tunnel=building_passage|covered`, `covered=yes|arcade|
  colonnade`, layer 0, at least 1 m of it inside the footprint), its corridor (width and height from `width`,
  `lanes`, `maxheight`, else 3 m / 2 lanes) widened by the street register's part (Katualueet) that the way runs in
  where that part reaches under the footprint. `polygons` of `[E, N]`, `clear_m` (from `maxheight`, else null),
  `use` `foot` or `road`, `refs`. LoD2 closes the ground storey; `compile` opens it (`passages.py`): where a
  facade's base line runs in a passage the wall is open up to the soffit (3.0 m over the floor on foot, 4.2 m on a
  road, kept under the first floor). An open stretch whose far side lies inside the building is an **arcade**: a
  recess of the ground storey whose openings stand on its back wall, with 0.5 m square piers on the facade line at
  most 4 m apart (in the plinth's material); one whose far side is the outline again is a **through mouth**. A
  soffit covers the passage and lining walls close it where no facade's back wall does. Collision is the drawn mesh.

### The automatic tier

What a building looks like when nobody has read it (`describe.automatic`, rules in `archetypes.py` and `priors.py`):

- **Type** from the LoD2 function code, storeys, height and footprint: `detached_house` (function 1000, up to two
  storeys), `row_house` (other residential functions, up to two storeys, built since 1940), `garage` and
  `outbuilding` (ancillary functions and unclassified pieces under 70 m²: a garage from 15 m² and 2.8 m wide),
  `industrial`, `commercial`, `office`, old `wooden_house`, and blocks of flats by decade. A building the
  register has not dated takes the year of its dated neighbours within 60 m (`register.year_near`), else it is
  drawn as new. `register.old_share` is the share of the dated neighbours within 60 m built before 1940, by
  count (none under three dated neighbours): how old the street round the building is. From 0.5 an office,
  public building or business block built since 1960 of "other material" is clad like the stone town (stone slab,
  facing tile, render; upright windows since 2000), not in metal cassettes. Metal fronts since 2005 include
  terracotta and red-brown panels.
- **Wall** in the register's facade material, in a colour such a building usually has by type and decade (brick
  houses since 1975 are mostly buff or white, older ones red; houses of "other material" are rendered until 1955,
  boarded upright, red brick or rendered until 1965, mostly pale or yellow brick until 2000; a low house of
  "other material" from before 1940 is an old wooden house; blocks of flats of "other material" from 1965 to 1979
  outside the old town are concrete elements, some tiled or rendered).
- **Storeys of a post-war house:** a detached house of 1940 to 1979 whose wall is under 7.2 m high is one storey
  on a basement storey with small windows (doors stay at the ground) under a 0.5 m knee wall, whatever the register
  counts.
  The palettes are populations: one colour is picked per building by its id.
- **`measured`**, when stored, replaces the palette pick: `measured.wall.colour` is a robust sample of the wall in
  a street photograph, taken without reading the building (`spread`: CIELAB dE of the sample, `share`: how much
  of the strip was used, `conf`). The generator keeps its hue and chroma and holds its lightness inside the
  family's range, because a photograph's pixels carry its exposure. It counts by its confidence against the
  palette pick: not under 0.2, half at 0.2, all of it from 0.5 (blended in CIELAB).
  `measured.roof.colour` takes the place of the orthophoto's roof colour. `measured.trim` and `measured.plinth`
  are stored for later use. A stored description may consist of `measured` alone, with tier `auto`. A stored
  description whose default wall material has no colour, or one at colour confidence 0.2 or less (a palette pick,
  an uncalibrated photo sample), takes `measured.wall` beside it the same way; glass keeps its own. A material
  somebody described always wins over a measurement. A colour measured by the panorama batch
  (`python -m turku3d.observe colour --batch`) also says where it comes from: `from {provider, id, date,
  overcast}` (overcast 0 sunny .. 1 overcast), `visible` (share of the part free of trees, cars and fences),
  `agreement` (CIELAB distance between the panoramas used) and `n` (panoramas used).
- **Roofing** from the orthophoto's roof colour (`archetypes.roofing`), corrected against the readings: the photo is
  taken in full sun, and the same roof read from the street is about half as light, so a grey roof is drawn dark
  and pale only where a pitched roof is very bright and colourless in the photo. On a house, warm red is tile,
  warm grey and brown dark concrete tile, cold grey seamed sheet metal; on other pitched roofs red is tile on
  low post-war blocks and red-painted sheet metal elsewhere, a faint green is green sheet, and what is not tile
  is seamed sheet metal (felt on sheds and on blocks built since 1980). Flat roofs keep felt.
- **Eaves and chimneys:** houses have deep boxed eaves with a gutter. Chimneys stand below the ridge of the
  largest roof plane: one on a detached house built before 2020, one or two on an old wooden house, one about
  every nine metres of ridge on stone houses of flats and post-war blocks under pitched roofs.
- **Blocks by decade** (`archetypes.block`): small two-leaf windows 3.2 m apart after the war, wide windows with
  a vent leaf in the element blocks of 1960 to 1979, wider bays since. The long wall that faces nearest
  south-west carries balconies on most blocks of three storeys or more: small cantilevers with sheet fronts
  after the war, recessed bays or cantilevers with concrete fronts in the sixties and seventies, glazed
  balconies since 1980 (glass fronts since 2000).
- **Shop floors:** `register.premises` holds the points of the open map's shops, cafes, banks and the like inside
  the footprint. The wall such a point lies at (and on a corner the next wall within 8 m) has a ground storey of
  shopfronts (`@shop`, every third bay `@shop_door`) under a sign band; a building whose register use is trade
  has one on its longest wall.
- **Houses of a storey and a half:** a house whose eaves are under 5.4 m above its plinth is one storey whatever
  the register counts: the wall above the windows is the attic's knee wall (up to 1.4 m), what is left over
  raises the plinth (a basement on a sloping site), and the gable has the attic room's window.
- **Walls by role** (`archetypes.wall_roles`, through `compile.wall_roles(doc, shell)`): the longest eaves-side
  wall of a house takes the door under a porch (a row house one door a dwelling), a gable takes a gable window,
  a garage its door on the shortest wall that fits one (a row of garages a door every 3 m), a shed a boarded
  door and no windows, a hall loading doors and ribbon windows.

## Rules

1. **Units.** Lengths are metres (`*_m`). Colours are sRGB `#rrggbb` as the surface looks in overcast daylight: the
   paint or stone colour, not a sunlit or shadowed pixel. Coordinates are EPSG:3877 `[E, N]`; heights are N2000.
2. **Everything is optional** except `schema` and `id`. A field that is absent is *not observed*, never "none".
   Say `"kind": "none"` or an empty list to state that a part is absent. The generator fills absent fields from
   priors selected by `style` and `register`, and from `wall_defaults`.
3. **Confidence per field.** Any object may carry `conf`, a map from its own field names to 0..1; `"*"` is the
   default for the object's fields and for nested objects without their own `conf`. `src` gives the `key` of one or
   more entries of the top-level `provenance` list. Readers merge by taking, per field, the value with the highest confidence. The
   generator ignores `conf`, except to decide what to drop when a wall does not fit its shell.

   | conf | Meaning |
   | --- | --- |
   | 0.9–1.0 | measured on a rectified crop, or checked by a reviewer |
   | 0.6–0.8 | read from one clear view |
   | 0.3–0.5 | read from an oblique, distant or partly hidden view |
   | 0.0–0.2 | a prior written down (give it a provenance entry with `"source": "prior"`) |
4. **References.** `materials` and `opening_types` are named dictionaries. A material field takes a name or an
   inline object. A storey's `window`, a `row` cell and a placed opening's `type` take an opening-type name.
5. **Tiers** are the envelope's: `auto` (fitted from registers, LoD2 and priors), `observed` (read from imagery by
   the automatic chain), `reviewed` (checked against several panoramas, or written by hand from photos).

## Wall frame

A wall is a street-facing or yard-facing facade plane of the shell. Its `frame` places it:

- `p0` → `p1` runs **left to right as seen from outside**. `u_m` is the distance from `p0` along the wall.
- `v_m` is the height above the wall base `base_h`. The default base is the ground at the wall's lowest point (the
  shell's terrain line; without one, the lowest point of the shell wall). Storey floors are level, so on a sloping
  street the plinth is taller at the low end.
- An opening's `u_m` is its **centre line**; its `v_m` is its **bottom edge**. Bands and zones give bottom edges.

The generator groups coplanar LoD2 wall polygons into facade planes and matches each described wall to the plane
its frame overlaps most (same direction within 15°, within 1.5 m). Planes without a described wall use
`wall_defaults`, which has no frame and places bays by a grid. A plane of 8 m or less that meets the end of a
described wall (a rounded or cut corner, a step in the front), or chains on from one within 14 m, takes that wall's
material and plinth instead of the default's.

**Fit against the shell.** The shell decides the real width and height.
- Width: a difference under 3 % rescales `u_m`; a larger one keeps metric positions measured from the wall's centre.
- Height: the plinth and the ground storey keep their heights. A difference under 12 % rescales the upper storeys.
  A taller shell leaves a plain band under the eaves; a shorter one drops the top storeys with the lowest
  confidence. The build report states every mismatch.

## What a wall says

| Field | Content |
| --- | --- |
| `material` | The body material. See [materials](#materials). |
| `plinth` | `height_m`, `projection_m`, `material` (granite ashlar, rubble, concrete, plaster). |
| `storeys` | Bottom to top: `kind` (`basement`, `ground`, `upper`, `mezzanine`, `attic`), `height_m` floor to floor, `sill_m`, and the openings (below). `count` repeats identical storeys. A tall ground floor and a low attic band are storeys with their own heights. |
| `ground_floor_m` | Height of the lowest storey's floor above the wall base (default: the plinth height). |
| `bays` | Centre lines: a list `[{ "u_m": 1.9 }, ...]` for an irregular rhythm, or a grid `{ "count", "pitch_m", "first_m" }`. A grid with `pitch_m` alone takes as many bays as fit the wall, centred. |
| `openings` | Openings placed by measurement outside the grid: stair windows between storeys, a gateway through two storeys. |
| `zones` | Rectangles with their own material and `relief` (banded or ashlar rustication, panelling): a boarded dado under the sills, a brick field in a plastered wall, a rusticated ground floor. |
| `bands` | Horizontal parts: `cornice`, `eaves_cornice`, `string_course`, `sill_band`, `frieze`, `plinth_cap`, `trim_board`, `fascia_band`, with `height_m`, `projection_m`, `profile`. Placed by `v_m` or by `at` (a storey edge). |
| `verticals` | `pilaster`, `lesene`, `corner_board`, `quoins`, `trim_board`, `pier`, `downpipe`, placed by `u_m` or `where` (`left`, `right`, `ends`, `between_bays`). |
| `balconies` | By `type`: `cantilever`, `recessed`, `french`, `continuous`, `corner`, `glazed`, `stacked_frame`; with railing kind and the storeys that have one. A `recessed` balcony is a bay of the wall set back by `depth_m`, floor to the slab above; openings inside it sit on its back wall. `glazing: true` closes the front of any type with panes above the railing. |
| `bay_windows` | By `type`: `box`, `canted`, `bow`, `corner_turret`, `stair_tower`; from storey to storey. |
| `attached` | `porch`, `canopy`, `steps`, `downpipe`, `sign`, `lamp`, `flagpole`, `ladder`, `vent_grille`, `hatch`. |
| `like` | Inherit every field of another wall, then override. |
| `observed` | How much of the wall was visible and which rectangles were hidden. A hidden rectangle is not blank: the generator adds there the openings the automatic tier would give the wall (with its roles: shopfronts at mapped premises, the street door), where they clear what was described. |

### Openings on the grid

Each storey fills the wall's bays in one of three ways, and may combine them:

- `"window": "w1"`: the same opening type in every bay.
- `"row": ["w1", "w1", null, "door1", "w2"]`: one cell per bay, left to right. `null` is plain wall. A blind window
  is an opening type of kind `blind`.
- `"openings": [{ "type": "shop1", "u_m": 4.2, "w_m": 5.6 }]`: placed by measurement, ignoring the bays. This is how
  a ground floor of shopfronts sits under a regular grid of windows.

A grid opening stands on the storey's `sill_m` above the storey floor. Doors, gateways and shopfronts stand on the
floor whatever `sill_m` says.

### Opening types

`kind`: `window`, `door`, `gateway`, `shopfront`, `garage`, `blind`, `vent`.

| Field | Content |
| --- | --- |
| `w_m`, `h_m` | Clear size of the hole in the wall. |
| `head`, `head_rise_m` | `flat`, `segmental`, `round`, `pointed`, `basket`, `shouldered`. |
| `shape` | An outline that is not a rectangle with a head: `circle`, `oval`, `hexagon`, `octagon`, `diamond`, `lunette`. |
| `recess_m` | Depth of the glass behind the wall face (0.02 flush wooden house, 0.12–0.25 masonry). |
| `multiple` | Coupled openings in one cell. |
| `lights` | Subdivision. `pattern`: `single`, `two_leaf`, `three_leaf`, `t_frame` (a transom over two leaves), `cross` (transom and mullion), `grid` (`cols` × `rows` equal panes: a six-pane window is 2 × 3), `side_vent` (a wide fixed pane and a narrow vent leaf on `vent`), `top_vent`, `ribbon`. `bars` adds glazing bars inside each light. |
| `frame` | Colour and visible width of the frame and sashes. |
| `sill` | `kind`, `projection_m`, `height_m`, colour. |
| `surround` | `flat`, `moulded`, `eared`, `carved`, `rusticated`, `brick_arch`, `pilastered`; width, projection, colour, `keystone`, `apron` under the sill. |
| `pediment` | `cornice`, `triangular`, `segmental`, `carved_crest`, `hood`, `ornament` over the head. |
| `glass` | `tint`, `reflectivity`, shares of windows with `curtains` and `blinds`, and the `interior` behind (`dwelling`, `office`, `shop`, `stair`, `dark`, `frosted`). |
| `leaf` | Doors, garages, gates: `leaves`, `style`, colour, `fanlight`, `sidelights`, `steps`. |
| `passage` | Gateways: `through` or `recess`, depth, lining material. |
| `shopfront` | `panes`, `stallriser_m` and its material, `transom_h_m`, `door` position, `fascia` with text, `awning`. |

## Materials

`family` selects a procedural PBR material at true metric scale; the other fields are its parameters.

| Family | Parameters |
| --- | --- |
| `plaster` | `colour`; `finish` `smooth`, `fine`, `rough`, `roughcast`, `scored` |
| `brick` | `colour` (brick), `colour2` (mortar), `bond` `stretcher`, `flemish`, `english`, `header`, `stack`; `finish` `facing`, `painted`, `slurried` |
| `weatherboard` | `colour`; `orientation` `horizontal`, `vertical`; `profile` `bevel`, `shiplap`, `tongue_groove`, `board_batten`, `beaded`; `scale_m` visible board width |
| `log` | `colour`; `scale_m` log height |
| `granite`, `stone` | `colour`, `colour2` (joints); `finish` `ashlar`, `rock_faced`, `polished`, `slab`; `scale_m` course height |
| `rubble` | `colour`, `colour2` (mortar); `scale_m` typical stone size |
| `concrete` | `colour`; `finish` `cast`, `board_marked`, `washed`, `panel`; `scale_m` panel width |
| `ceramic_tile` | `colour`, `colour2` (joints); `scale_m` tile width |
| `sheet_metal` | `colour`; `profile` `standing_seam`, `flat_seam`, `corrugated`, `cassette`; `scale_m` seam spacing |
| `clay_tile`, `concrete_tile` | `colour`; `profile` `pantile`, `flat_tile` |
| `felt` | `colour`; `profile` `plain`, `batten`, `shingle` |
| `slate`, `copper`, `metal`, `painted_wood`, `glass` | `colour`; `finish` `matt`, `gloss`, `weathered` |

`weathering` 0..1 adds dirt, streaks and fading in every family.

## Roof

The roof **form** comes from the LoD2 shell. The description says what covers it and what stands on it:
`material`; `eaves` (`overhang_m`, `fascia_m`, `soffit`, `brackets`, `gutter`); `verge` (gable overhang and
`bargeboard`); `parapet`; `dormers`; `chimneys`; `details` (skylights, ladders, snow guards, ridge crests, plant
rooms). `form_hint` records what a reader saw, for audits of the shell.

A dormer is `{ "type": gable | hip | shed | wall_dormer, "anchor": { "wall", "u_m", "setback_m" }, "w_m", "h_m",
"material", "roof_material", "window" }`: `h_m` is its whole height over the roof at its front, `rise_m` the height
of the gable triangle (two fifths of the height when not given), `window` an opening type (the attic window
when not given), placed when the front wall under the gable has room for it. A wall dormer that the LoD2 shell
already has as a wall gable is not built twice.

A roof nobody gave a material takes it from the orthophoto: `register.roof_colour` is the median of the roof's lit
pixels (tree crowns and deep shadow left out); the roofing family and the colour as the street sees it follow
by the rule under "The automatic tier" (`archetypes.roofing`, through `priors.measured_roof`).

A dormer, chimney or detail has an `anchor`: either `at: [E, N]`, or `wall` + `u_m` + `setback_m` (above the
described wall, that far behind its face). Its base follows the roof surface.

## Landmark components

`components` lists named parts of the component kit, each with an anchor (`wall` + `u_m` + `v_m`, or `at`) and
free `params`. The kit registry (`turku3d.world.kinds.building.components`) documents each name and validates its parameters:
`portico`, `dome`, `tower`, `spire`, `glazed_box`, `colonnade`, `arcade`, `clock_face`. A component may be used by
any building.

## Site parts

`site` holds what belongs to the building's street face but stands on the plot: a board fence with its gateway
between two wooden houses, a plot wall. `path` is a polyline in `[E, N]`; `gates` are placed along it.

## Example

A five-storey plastered apartment block, written by hand from one photo (shortened; the complete file is
`pipeline/turku3d/world/kinds/building/tests/fixtures/stone-apartment.json`, and a wooden house is beside it):

```json
{
  "schema": "turku-description/building/1",
  "id": "building/example-stone",
  "tier": "reviewed",
  "style": { "type": "classicist_apartment", "era": "1920-1939" },
  "identity_cues": ["dull red plaster", "cream window surrounds", "green columns under an entrance canopy"],
  "provenance": [{ "source": "hand", "key": "street-photo-2026-07", "at": "2026-11-12T21:00:00Z", "by": "w2-buildings" }],
  "materials": {
    "wall": { "family": "plaster", "colour": "#a8564f", "finish": "fine" },
    "plinth": { "family": "granite", "colour": "#b9a58c", "finish": "ashlar", "scale_m": 0.45 }
  },
  "opening_types": {
    "w": {
      "kind": "window", "w_m": 1.15, "h_m": 1.55, "recess_m": 0.12,
      "lights": { "pattern": "t_frame", "transom_at": 0.7 },
      "frame": { "colour": "#ece6d4", "width_m": 0.07 },
      "surround": { "kind": "flat", "width_m": 0.12, "projection_m": 0.03, "colour": "#e6d9a8" },
      "sill": { "kind": "plaster", "projection_m": 0.06, "height_m": 0.08, "colour": "#e6d9a8" },
      "conf": { "*": 0.7, "recess_m": 0.3 }
    },
    "arch": { "kind": "window", "w_m": 1.6, "h_m": 2.3, "head": "round", "lights": { "pattern": "grid", "cols": 3, "rows": 4 } },
    "door": { "kind": "door", "w_m": 1.5, "h_m": 2.6, "head": "round", "leaf": { "leaves": 2, "style": "panelled", "colour": "#6b4a36" } }
  },
  "walls": [{
    "id": "street",
    "frame": { "p0": [23459120.0, 6704740.0], "p1": [23459135.0, 6704722.0] },
    "material": "wall",
    "plinth": { "height_m": 0.9, "projection_m": 0.05, "material": "plinth" },
    "bays": [{ "u_m": 2.1 }, { "u_m": 5.6 }, { "u_m": 9.4 }, { "u_m": 13.2 }, { "u_m": 16.7 }, { "u_m": 20.5 }],
    "storeys": [
      { "kind": "ground", "height_m": 3.8, "sill_m": 0.5, "row": ["arch", "door", "arch", null, "arch", "arch"] },
      { "kind": "upper", "count": 4, "height_m": 3.1, "sill_m": 0.95, "window": "w" }
    ],
    "bands": [{ "kind": "eaves_cornice", "at": { "storey": -1, "edge": "top", "offset_m": -0.5 }, "height_m": 0.5, "projection_m": 0.35, "profile": "dentil", "colour": "#e6d9a8" }],
    "attached": [
      { "kind": "canopy", "type": "entablature", "u_m": 5.6, "v_m": 3.3, "w_m": 3.0, "h_m": 0.6, "depth_m": 1.3,
        "supports": { "kind": "columns", "count": 2, "diameter_m": 0.35, "colour": "#3f5f52" }, "colour": "#e6d9a8" },
      { "kind": "downpipe", "u_m": 3.9, "colour": "#3a3a3a" }
    ],
    "conf": { "*": 0.7 }, "src": "street-photo-2026-07"
  }],
  "roof": { "material": { "family": "sheet_metal", "colour": "#4a4a48", "profile": "standing_seam", "scale_m": 0.6 },
            "eaves": { "overhang_m": 0.4, "soffit": "cornice" } }
}
```

## Earlier facade programs

The 650 programs in `textures/library/facade-programs.json` (`facade-program-prototype-1`) convert with
`python -m turku3d.world.kinds.building.convert (--out DIR | --store) [KEY ...]` into `observed` descriptions.
Measured window, ground-floor and door rectangles become placed openings of a few opening types (sizes to 5 cm); a
wall known only by its rows becomes storeys with one opening per bay; the material that was read, a measured wall
colour, string courses and the cornice carry over, and the programs' `support` becomes `conf`. A wall of which the
program read nothing is left out, so the priors describe it: 256 of the 650 programs hold something a description
can carry (313 walls, 4,044 openings); the other 394 only say why the wall could not be read. `--store` never
replaces a description somebody else stored (a measured wall colour stored beside a conversion is kept). The
reviewed overlay `textures/library/facade-appearance.json` is laid over the programs: its building material class
replaces the register's word, its wall colours the programs'; an uncalibrated colour counts as a guess (0.2) and a
natural stone front keeps its family's colour. A rectangle the program excluded and a storey whose bays it could not
tell become `observed.hidden_m`. Nothing else reads the old format.

## Changes

A change that readers must follow gets a line on the campaign board. Additive fields keep version 1.

- 2026-11-13: `shell.passages` (building passages and arcades from the open map and the street register).
- 2026-11-13: `observed.hidden_m` is drawn with the automatic tier's openings; a guessed wall colour (confidence
  <= 0.2) gives way to `measured.wall`.
- 2026-11-13: `measured.<part>` may carry `from`, `visible`, `agreement` and `n` (the panorama batch).
- 2026-11-13: `measured` (wall, trim, plinth, roof colours sampled without a reading); style type `row_house`;
  the attachment kind `porch` is built (deck, steps, posts, roof `shed` / `gable` / `flat`; types `open`,
  `boarded`, `glazed`).
