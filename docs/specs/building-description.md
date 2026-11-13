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
`wall_defaults`, which has no frame and places bays by a grid.

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
| `balconies` | By `type`: `cantilever`, `recessed`, `french`, `continuous`, `corner`, `glazed`, `stacked_frame`; with railing kind and the storeys that have one. |
| `bay_windows` | By `type`: `box`, `canted`, `bow`, `corner_turret`, `stair_tower`; from storey to storey. |
| `attached` | `porch`, `canopy`, `steps`, `downpipe`, `sign`, `lamp`, `flagpole`, `ladder`, `vent_grille`, `hatch`. |
| `like` | Inherit every field of another wall, then override. |
| `observed` | How much of the wall was visible and which rectangles were hidden. |

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
pixels (tree crowns and deep shadow left out), deepened to a paint or tile colour of the same hue. A red pitched
roof is clay tile on a small house from 1940 on and painted sheet metal elsewhere; any other roof keeps the
roofing of its style in the measured colour (`priors.measured_roof`).

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
replaces a description somebody else stored. Nothing else reads the old format.

## Changes

A change that readers must follow gets a line on the campaign board. Additive fields keep version 1.
