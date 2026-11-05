# Handover: roadfix (bridge and road faults from the drive sweep, `bridge-ends@1` and `roadbed@1`)

2026-11-05, native Claude agent, branch `local/roadfix` off `local/loop-integration`. Input: the autopilot drive
sweep (`~/turku3d/loops/autopilot/sweep-v2/report.md`, [autopilot](autopilot.md)). Every rule is city-wide and
driven only by the street graph, OSM and the tile's own collision; there are no structure ids or coordinates in code.

## Structures examined (nearest Kauppatori first)

| Structure | Distance | Class in the sweep | Root cause found |
|---|---:|---|---|
| Building passage w994856582 (Aurakatu block) | 230 m | hole / fall 29.7 m | `roads.json` heights jump to the roof (30.7-32.6 m) where the passage floor has a gap; a 3x4 m hole beside the lane |
| Auransilta w90088650 | 210 m | jolt 2.1 m/s | straight 8 % approach ramps (`bridge_join.approach_patches`): grade breaks at the deck end and the ramp foot; a deck end slab 4-7 cm proud of the ramp |
| Building passages w1095425960/62/66 | 390 m | hole / fall 25 m | a 1-1.5 m sliver without floor at the building's wall line; lane heights jump to the roof (35.2 m) over it |
| Myllysilta w531029806 | 570 m | step 1.19 m | a 1 m deep trench between the two decks where the lanes cross it at the deck end |
| Tuomiokirkkosilta w27734777, w4306348 | 650 m | step 2.0 m, jolts | 1 m holes in the road layer at the deck-to-land seam; straight approach ramps |
| Martinsilta w4306342 | 720 m | jolt 8.4 m/s | deck-end seam step and straight approach ramps |
| Aninkaistensilta w4265032/33 | 1.2 km | obstacle (stuck, 7.7-11.6 m/s impacts) | 7 m deck lamp posts (OSM `street_lamp` nodes in a median narrower than the mapped lanes) standing in the outer lanes |
| Kupittaa trunk link w631256465 (Jonni's report) | 1.6 km | obstacle (stuck, roll 2.4) | the link's lane runs off its deck into the wedge between it and the next deck; its `roads.json` heights are up to 2.2 m below the deck |
| Helsinginkatu under Lemminkäisensilta (Jonni's report) | 3.0 km | obstacle (all lanes stuck at the deck edge) | the lower road's own surface (`road`, 24 deg) climbs under the deck to its soffit, so the underpass is closed |
| Tykistökatu under Kalevan silta (Jonni's report) | 1.9 km | obstacle (stuck, 3.7 m kerb strike) | the same: the lower road is buried in an embankment 0.8-1.7 m above its lanes |

Building-passage obstacles (cars hitting walls at the passage mouth) were diagnosed but not fixed here: the OSM
passage line runs 2-3 m off the opening the generator builds, or a second covered way carries the opening. See
"Open".

## Rules

### `bridge-ends@1` (spot rebuild; generator `coarse/bridge_join.py`, `coarse/bridges.py`)

- **Vertical curves.** A bridge's landward ramp is a cubic Hermite curve that leaves the deck at the deck's grade and
  meets the ground at the ground's grade (`vertical_curve`). Its length is the first that keeps both the 8 % mean
  grade and a curvature of `CURVE_KAPPA` 0.012 /m (1.5 m/s² at 40 km/h; `vertical_curve_rise`), at most 30 m.
- **Ramps stop inside the tiles that build the bridge** (`bridges.building_region`, `bridge_join.region_exit`): a
  ramp used to run into a neighbour tile that does not build that bridge, leaving a step at the seam.
- **Deck lamps clear of the lanes** (`bridges.clear_of_lanes`): a mapped lamp inside a lane's envelope (way width less
  its 0.5 m shoulders, plus 0.25 m) moves to the nearest deck point outside every lane within 3 m; with no such room
  it is not built.
- Affected class: tiles whose manifest counts a deck (`bridges` > 0), 285 of 4,596 tiles.

### `roadbed@1` (migration; shared rules `roads/roadbed.py`)

Runs on every tile's finished collision and street graph, as the last step of the chain (so a generator build gets
the same result):

1. **Lane heights on the floor** (`repair_heights`): a car-edge vertex more than 1 m off the median of the edge's
   heights within 6 m takes the floor nearest that level, else the interpolation of its neighbours.
2. **No pits under a lane** (`slabs`, rule `pit`): wheel tracks every 0.5 m across each car edge (its mapped width
   plus 1 m, continued 6 m past both ends through the junction) sample the top floor within 1.2 m of the lane level.
   A gap or trench deeper than 5 cm and at most 3 m long, rimmed on both sides by carriageway material (road, crossing,
   bridge deck, cobblestone, paving slab, ramp) at a consistent grade, gets a 0.3 m collision slab (an oriented box
   in the cell's PRIM section) whose top is the chord between the rims.
3. **No steps on structures** (rule `step`): on bridge and tunnel edges and the edges that meet them, a 4-30 cm step
   between paved samples gets a 1:16 ramp slab on its low side.

The meshopt triangle sections of a cell stay byte for byte; only PRIM changes (like `furniture@2`). Counts are in
the non-public `manifest.roadbed` (`lane_height_edges`, `lane_height_vertices`, `pit_slabs`, `step_slabs`).

## Results

Drive sweep on the real vehicle physics, before = sweep-v2 (pin `bulkgen2-3d1445b2+maa2cf1ce`).

**Chosen structures** (75 lanes: the 13 above plus 8 that regressed in a first city run), bridge-ends rebuilds
(`roadfix-m1`, code 89b5a7cc) with the final roadbed:

| Kind | Before pass/warn/fail/not covered | After |
|---|---|---|
| bridge | 0 / 4 / 37 / 0 | 0 / 5 / 34 / 2 |
| tunnel | 0 / 0 / 16 / 11 | 0 / 0 / 16 / 11 |
| underpass | 0 / 1 / 6 / 0 | 0 / 1 / 6 / 0 |

| Lane | Before (worst) | After |
|---|---|---|
| Tuomiokirkkosilta 1beeeab9, 96084efa | fail, step 2.02 m / 1.39 m (holes at the deck seam) | warn, step 0.03 m |
| Myllysilta dbea3a8a, 715470f6 | step 1.19 m / 0.84 m (trench between decks) | contact 0.63 / 0.82 s, no step |
| Martinsilta 771965a2 | jolt 8.39 m/s | step 0.21 m |
| Aninkaistensilta 06e26035 (both carriageways) | stuck (lamp post) | arrived, step 0.04 m |
| Passages w1095425962/66 | fall 25 m (lane on the roof) | stuck at the passage mouth (obstacle, see Open) |
| Passage w994856582 | fall 29.7 m | fall 4.2 m (a 3x4 m hole beside the lane, longer than a pit) |
| Auransilta | step 0.08 / 0.13 m | jolt 2.4 / step 0.10 m: the south-end sag is the street's own profile |

One lane got worse than before: w4310235 (t0_-13_3) warn -> fail, jolt 1.24 m/s (0.66 before).

**City-wide, roadbed only** (an early version with no ledge check, offline on all 877 swept tiles; 743 lanes):

| Class (structures / lanes) | Before | After |
|---|---|---|
| hole or fall-through | 74 / 102 | 53 / 72 |
| obstacle on the lane | 72 / 102 | 81 / 113 |
| wheel off the ground / jolt | 52 / 86 | 55 / 94 |
| step 4-30 cm | 17 / 21 | 18 / 22 |

Falls 29 -> 18 lanes; 5 lanes that arrived got stuck (ledges of slabs across oblique seams, then fixed by the
corner check), 1 stuck lane arrived; holes that no longer swallow a wheel often turn into the obstacle behind
them. Lane heights repaired: 201 edges, 904 vertices. The final rules' city run was stopped to keep the farm free
(see Cost); it is the first thing to rerun on the train.

Visual check (Air, headless, QA camera): `~/turku3d/loops/roadfix/shots/*-before.png`, `*-after.png`; collision
maps `~/turku3d/loops/roadfix/maps/`.

## Cost and what `train4` needs

- `roadbed@1`: 4-10 s CPU per tile (median ~6 s on the loaded farm), no rebuild, all tiles.
- `bridge-ends@1`: a spot rebuild of the 285 of 4,596 tiles that build a deck (`bridges` > 0): a full build by
  the integration generator, 12-25 CPU-min each (two timed out at the farm's 1800 s on the loaded host and built on
  the retry). On the train, the steps before it are computed and then discarded for those tiles.
- The generator change (`coarse/bridges.py`, `coarse/bridge_join.py`) is in the import closure of floodlights,
  trees-lidar, furniture, racks, landmark-windows, signals and signs, so those steps rerun on every tile, and
  landmark-windows' spot rebuilds are rebuilt by this generator. `wires@2` is unchanged: `--checkpoint wires@2`
  still resumes after it.
- **train4**: register from integration after this merge with `--rebuilds-from train3`; the chain ends
  `..., shell-look@1, signals@1, signs@1, bridge-ends@1, roadbed@1` (signals/signs are the traffic-light and sign
  agent's). Run it on the UpCloud host: the rebuilds dominate (~285 x 20 CPU-min, about 95 CPU-hours, 1.5 h on 64
  cores); roadbed is ~1.5 CPU-hours for the city.

## Tools (local, osgallery-dev `~/turku3d-local/roadfix/tools`, copies in `~/turku3d/loops/roadfix/`)

- `apply_roadbed.py PRODUCTS OUT TILE...`: `roadbed@1` offline over finished products.
- `mksweep.sh NEWDIR PRODUCT...`: a drive-sweep folder from the sweep-v2 cache (hard links) with these products
  swapped in, so the sweep runs on migrated tiles without a farm alias.
- `compare.py BEFORE AFTER [ONLY] [-v]`: per lane and per kind before/after, and regressions.
- `plotmap.py`, `stepmap.py`, `profile.py`, `holemap.py`, `walls.py`, `objat.py`, `dbg.py`: collision maps,
  ground-step maps, longitudinal profiles, vertical faces, visual objects at a point, and the roadbed tracks at a point.

## Open

1. **Ground under bridge decks (Jonni's Lemminkäisensilta and Kalevan silta reports).** The lower road's own
   surface (`road` material, 24 deg) climbs under the deck to its soffit, so the underpass is closed and parked or
   driving cars sit on the slope. A cut of the terrain grid over every `way["under"]` corridor inside
   `bridges.produce` (94dac80a) was rebuilt on t0_10_4 and changed nothing: the raised surface is made later
   (street profiles or surface stages sampling the survey under the deck, where returns hit the deck). Next: find
   the stage that writes that `road` surface (`street_profiles.py`, `street_survey.py`, `surfaces.py`) and give it
   the lower way's level read beyond 10 m of the deck (`under_height`-style), with vertical abutment faces at the
   corridor edges. Reverted in f9aba535.
2. **Lane heights far off the deck (Kupittaa link).** `roads.json` heights 2.2 m below the deck: the roadbed rules
   then measure the wedge under the deck instead of the deck. Bake bridge lane heights from the deck surface
   (`route_surface.bake` picks the floor nearest a DEM reference) or widen `repair_heights` to whole-edge offsets.
3. **Building passages** (the most common central obstacle): the OSM passage line runs 2-3 m off the opening the
   generator builds (w1196792244: a `covered=yes` driveway carries the walled tube, the `building_passage` one runs
   into the building), or the approach lane cuts the building corner (w994856577). The passage corridor
   (`tunnels.produce`) should follow the lane envelope of every car way through the opening.
4. Periodic 6-8 cm steps along Aninkaistensilta's approaches and remaining 5-15 cm deck-edge steps on
   Martinsilta: lanes run over the deck's kerbs (lane data).
5. Re-run the city-wide sweep with the final rules on the train's products.
