# Surface material IDs (shared table)

Version: **materials v1** (the `.col` header field `material_table_version` = 1).
Used by: `.col` per-triangle material bytes and primitive records ([col-format.md](col-format.md)), the visual ground splat (material ID per ground triangle), footstep/tyre audio, the curb-strike module, foot IK, and QA back-mapping.

Rules:
- IDs are `uint8`. **0 = unknown** (must not appear in a shipped tile; the validator fails on it, per Task 1.4 "every triangle has a material ID").
- IDs are **append-only**. Never renumber or reuse an ID. Adding an ID bumps the table to v(n+1); readers that see an unknown ID treat it as `default` behaviour (solid, generic friction) and log once.
- 200–254 are reserved for experiments and must not appear in a shipped tile; 255 is reserved.
- The machine-readable copy of this table must be generated from this file (or kept identical to it) by `make col-materials` (`pipeline/turku3d/col/materials.py` writes `pipeline/turku3d/col/materials_v1.json` and `game/src/shared/col/materials.ts`; a test fails when either is stale). There is one table; do not keep private copies.

## Collision classes

Each material has exactly one **class**. The class decides which Rapier collider group the triangle or primitive is built into (the physics worker splits a tile into one trimesh per class that is present).

| Class | Meaning | Character (KCC) | Vehicles | Scene ray queries |
|---|---|---|---|---|
| `solid` | Normal static collision | collides | collides | hit |
| `query` | Query-only helper; never blocks movement | ignores | ignores | hit only with the `QUERY_HELPERS` filter (foot IK) |
| `sensor` | Trigger volume (water etc.), no contact response | sensor event | sensor event | ignored by default |

Suggested Rapier interaction groups (16-bit membership / filter, set in the worker):
`STATIC_SOLID = 1<<0`, `STATIC_QUERY = 1<<1`, `STATIC_SENSOR = 1<<2`, `CHARACTER = 1<<4`, `VEHICLE = 1<<5`, `DYNAMIC = 1<<6`.

## Table (v1)

`fric` = suggested friction coefficient (Rapier, combined with `average`); `audio` = footstep/tyre sound family; `flags`: `CURB` = curb-strike module reacts to this face, `STAIR` = stair locomotion/foot-IK logic, `NOWALK` = KCC treats any slope as non-walkable (vertical blocker), `FIX_EDGES` = the tile should be built with `FIX_INTERNAL_EDGES` if any triangle has this material.

| ID | Name | Class | fric | audio | flags | Source / notes |
|---:|---|---|---:|---|---|---|
| 0 | `unknown` | solid | 0.8 | concrete | | Must not ship |
| 1 | `road` | solid | 0.9 | asphalt | | Katualueet carriageway, asphalt |
| 2 | `sidewalk` | solid | 0.85 | concrete | | Katualueet sidewalk (asphalt or concrete slabs) |
| 3 | `curb` | solid | 0.85 | stone | CURB, FIX_EDGES | Vertical curb face **and** the 0.15 m curb top between the double breaklines (62520) |
| 4 | `curb_dropped` | solid | 0.85 | stone | FIX_EDGES | Curb segment with step < 3 cm (crossing, driveway); no curb strike |
| 5 | `crossing` | solid | 0.9 | asphalt | | Painted pedestrian crossing |
| 6 | `cycleway` | solid | 0.9 | asphalt | | |
| 7 | `cobblestone` | solid | 0.8 | stone | | Setts / natural stone paving (Kauppatori etc.) |
| 8 | `paving_slab` | solid | 0.85 | stone | | Concrete/stone slabs, squares, courtyards |
| 9 | `gravel` | solid | 0.7 | gravel | | |
| 10 | `grass` | solid | 0.6 | grass | | |
| 11 | `soil` | solid | 0.6 | soil | | Planting beds, bare ground |
| 12 | `sand` | solid | 0.5 | sand | | Playgrounds, beach |
| 13 | `rock` | solid | 0.8 | stone | | Bedrock outcrop |
| 14 | `water` | sensor | 0.0 | water | | River/sea surface; swimming/drowning trigger, the river bed is the solid below |
| 15 | `quay` | solid | 0.85 | stone | FIX_EDGES | Quay top and vertical quay wall (62520 lines with step > 1 m) |
| 16 | `stair_slope` | solid | 0.9 | stone | STAIR | The single inclined plane over a stair flight (character and vehicle collider) |
| 17 | `stair_tread_helper` | query | 0.0 | stone | STAIR | Per-tread horizontal quads + risers matching the **visual** treads; foot IK raycasts only |
| 18 | `ramp` | solid | 0.9 | concrete | | 62540 ramps |
| 19 | `wall` | solid | 0.8 | concrete | NOWALK | Retaining walls (66634), stair side walls, free-standing walls |
| 20 | `fence` | solid | 0.6 | metal | NOWALK | Thin fence boxes |
| 21 | `railing` | solid | 0.6 | metal | NOWALK | Quay/stair railings |
| 22 | `building` | solid | 0.8 | concrete | NOWALK | Building shell walls |
| 23 | `roof` | solid | 0.8 | roof | | Building shell roofs (walkable where flat enough) |
| 24 | `bridge_deck` | solid | 0.9 | asphalt | | |
| 25 | `pole` | solid | 0.6 | metal | NOWALK | Lamp/sign/traffic-light poles (usually a cylinder primitive) |
| 26 | `tree_trunk` | solid | 0.7 | wood | NOWALK | Tree trunks (capsule/cylinder primitive) |
| 27 | `bollard` | solid | 0.6 | metal | NOWALK | Bollards, posts |
| 28 | `street_furniture` | solid | 0.6 | metal | | Benches, bins, bike racks (boxes) |
| 29 | `vegetation_low` | solid | 0.6 | grass | | Hedges, low shrubs (boxes) |
| 30 | `metal_grate` | solid | 0.8 | metal | | Drain covers, grates |
| 31 | `wood_deck` | solid | 0.8 | wood | | Boardwalks, piers |
| 32 | `invisible_boundary` | solid | 0.0 | none | NOWALK | World/AOI edge blocker; the only solid allowed without visual geometry (exception to D5 rule, debug-visible) |

## Notes

- **Curbs** (`curb`, ID 3): the curb-strike module (Task 1.14) shape-casts wheels and reacts only to faces with this ID. The face normal decides "vertical face" vs "top"; both carry ID 3.
- **Stairs**: `stair_slope` is what the KCC walks on (GTA V approach); `stair_tread_helper` exists so foot IK can place feet on the real treads. They are built from the same flight description, so their top and bottom edges coincide within 2 cm (Task 1.5).
- **Water** is a sensor; the solid river/sea bed under it uses `rock` or `soil`.
- Ground material assignment comes from `GIS:Katualueet` surface material (+ ortho classifier fallback); the mapping from Katualueet codes to these IDs lives in `pipeline/turku3d/ground/materials.py`.
