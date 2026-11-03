# Object editor (developer, localhost)

Point the camera at a tree, sign, bench, bin, bike rack or street-light post, pick it and move, turn, lift or
delete it with the gamepad. Every committed edit goes into an **edit session** for an agent to turn into generator
rules (format and incorporation contract: `~/turku3d/loops/editor/EDIT-FORMAT.md`; reader:
`scripts/dev/edit_sessions.py list | effective | clusters`). Edits show at once as local
**previews** only: no tile, collision, physics body, breakable-pole state or multiplayer message changes, so other
players never see them. On by default on localhost (`?editor=0` turns it off; `?editor=1` elsewhere, never on Pages).

| File | Role |
|---|---|
| `index.ts` | `ObjectEditor`: per-frame `handle(input)` (after `Input.update`) and `update(now)` (before render), wiring |
| `state.ts` | `EditorMachine`: the input state machine (frame of editor actions in, commands out); the mapping |
| `catalog.ts` | Editables of the drawn tiles resolved to source placements: prop rows of merged batches, tree instances, posts |
| `pick.ts` | Centre-ray picking against vertical cylinders, candidate ordering and L1/R1 cycling |
| `preview.ts` | Visual previews: prop index range degenerated in place + a detached copy; trees and posts via their owners |
| `session.ts` | `EditSession`: rows, undo, localStorage restore and outbox, flush to the dev server |
| `context.ts` | Automatic context: surface under, nearest road/kerb/sidewalk surfaces, facades, road graph edge, props, LiDAR |
| `provenance.ts` | Tile metadata per placement and the generator stage/rules to look at |
| `geodesy.ts` | Engine → EPSG:3877 → EPSG:3067 (JHS 154 Krüger series, pyproj-checked) |
| `shots.ts` | 384 px before/after JPEGs from the canvas inside the frame task |
| `hud.ts` | Crosshair, label, delete bar, reason picker, hint strip |
| `pointCloudHook.ts` | The hook the LiDAR point-cloud overlay (lidarpc loop) registers |

Dev server: `tools/dev/editSessions.mjs` stores `~/turku3d/edits/<session>/` (`TURKU3D_EDITS` overrides).

## Mapping (DualSense names; Xbox in brackets)

Toggle: **touchpad click** [L3 + R3 together] or F8. A short press enters or leaves; **holding it 1.5 s in the mode
closes the session** (previews cleared, `session.json` gets `ended`). While a session is open its previews stay
visible outside the mode and come back after a reload; if the mode was on, it comes back on too.

The editor layer (`input/bindings.ts` `editorBindings`) takes ✕ ○ △ L1 R1 Create L3 and the D-pad from gameplay.
The sticks, □ (jump; double tap toggles fly), R2/L2 (fly up/down), Options (pause) and R3 (camera reset) keep working,
and L3 becomes the fast-flight modifier in place of ✕.

| Aiming (nothing held) | |
|---|---|
| ✕ [A] | pick the highlighted object |
| L1 / R1 [LB / RB] | previous / next candidate along or near the ray (`n/m` in the label) |
| hold △ [Y] 1 s | delete the highlighted object |
| ○ [B] | undo the last edit |
| D-pad within 2 s of a commit | reason: ↑ on road or path · → wrong facing · ↓ doesn't exist · ← misaligned with LiDAR (skip: ignore) |
| Create [View] | point cloud on/off; hold Create + ↑↓ opacity ±10 %, ←→ point size ∓1 px |

| Holding | |
|---|---|
| left stick | move on the ground, camera-relative, snapped to the collision surface (L3: 3× faster) |
| L2 / R2 [LT / RT] | turn smoothly (90°/s at full) |
| L1 / R1 [LB / RB] | turn 15° |
| D-pad ↑↓ | height ±5 cm (held: repeats) · D-pad ←→: turn 1° |
| □ [X] | back onto the ground (drops the generator's embedding and the height offset) |
| ✕ [A] | drop: commit the edit · ○ [B] cancel · hold △ [Y] 1 s delete |

Keyboard: Enter pick/drop, Backspace undo/cancel, Z/X cycle or 15°, Comma/Period turn, PageUp/PageDown height,
Home/End 1°, O ground, Delete (hold) delete, L point cloud, WASD move.

## Previews and what they do not change

Props: the row's index range in the merged batch becomes degenerate (other rows keep their offsets; DevLayers and
light scans read the same ranges) and a compact copy of its vertices draws at the new transform with the batch's
material. A model that leaves restores its batch, and the preview reapplies when it loads again. The tile's merged
shadow batch still casts the old shadow, and collision and breakable-pole bodies stay at the generator's position.
Trees: `VegetationLod.setEdit(id, delta)`; posts: `StreetLights.setEdit(key, {x, y, z, yaw})` (corona and pool follow).

## Tests

```sh
cd game && npx vitest run tests/unit/editor-*.test.ts tests/unit/vegetation-lod.test.ts tests/unit/street-lights.test.ts
```
`editor-input.test.ts` drives Input with stubbed `navigator.getGamepads` (DualSense with 18 buttons, Xbox with 17).
