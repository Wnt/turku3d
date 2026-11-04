# Handover: in-game object editor

Localhost developer tool. Jonni points the camera at a street object, picks it and moves, turns, lifts or deletes it
with the gamepad (DualSense or Xbox). Each committed edit becomes **evidence for a generator rule change**. It is
not an instance override. Previews are local and visual only: tiles, collision, physics, traffic and multiplayer
never change.

- Code: `game/src/dev/editor/` (its `README.md` has the module table).
- Input layer: `game/src/input/bindings.ts` (`editorBindings`).
- Engine hooks:
  - `world/vegetationLod.ts`: `forEachTree`, `forEachBarrier`, `setEdit`;
  - `render/lights/index.ts`: `forEachPost`, `setEdit`;
  - `traffic/signals.ts`: `forEachHead`, `setEdit`.
- Dev-server storage: `tools/dev/editSessions.mjs`.
- Reader: `scripts/dev/edit_sessions.py`.
- Format and incorporation contract: `~/turku3d/loops/editor/EDIT-FORMAT.md` (outside git).
- Loop status: `~/turku3d/loops/editor/STATUS.md`.

## Controller map

DualSense names; Xbox in brackets. The editor's on-screen hint strip shows the same mapping.

**Mode and sessions**

- **Toggle:** touchpad click [L3 + R3 together]. Keyboard: Y or F8.
  - A short press enters or leaves the mode.
  - **Holding 1.5 s in the mode closes the session**, but only once every edit is saved. Until then the previews
    stay and a badge reads "Closing: N edit rows not saved, retrying". After that: "Session saved: N edits ·
    previews off until the generator incorporates them", and the **close dialog** opens (see below).
  - Entering the mode again cancels a pending close.
- **Previews:** while a session is open, its previews stay visible outside the mode and survive F5. If the mode
  was on, it comes back on after F5.
- **Flight keeps working in the mode:**
  - sticks: move and look;
  - □ [X]: jump, and a double tap toggles fly;
  - R2/L2: fly up and down;
  - L3: fast;
  - Options, R3: as usual.

| Aiming (nothing held) | |
|---|---|
| ✕ [A] | pick the highlighted object (cyan box; label: type · id · distance · n/m) |
| L1 / R1 [LB / RB] | previous / next candidate along or near the crosshair ray |
| hold △ [Y] 1 s | delete it |
| ○ [B] | undo the last edit |
| D-pad within 2 s of a commit | reason: ↑ on road or path · → wrong facing · ↓ doesn't exist · ← misaligned with LiDAR (ignore to skip) |
| Create [View] / L | point cloud on/off. Hold it, then: ↑↓ opacity, ←→ point size, L1/R1 colour mode. The cloud only shows in the mode unless the page has `?pointcloud=1` |

| Holding (orange box) | |
|---|---|
| left stick | move on the ground, relative to the camera, snapped to collision (L3: 3× faster) |
| L2 / R2 | turn smoothly |
| L1 / R1 | turn 15° |
| D-pad ←→ | turn 1° |
| D-pad ↑↓ | height ±5 cm (holding repeats) |
| □ [X] | back onto the ground |
| ✕ [A] | drop (commits the edit) |
| ○ [B] | cancel |
| hold △ [Y] 1 s | delete |

Keyboard equivalents:

| Key | Action |
|---|---|
| Enter | pick / drop |
| Backspace | undo / cancel |
| Z / X | cycle, or 15° |
| Comma / Period | turn |
| PageUp / PageDown | height |
| Home / End | 1° |
| O | back onto the ground |
| hold Delete | delete |
| L | point cloud |
| WASD | move |

The HUD's top right always shows "Edits saved N · pending M" in the mode.

## The close dialog and the session summary

When a session closes, a modal dialog shows the session id and a short plain-text summary to paste into a Claude
session (`dev/editor/closeDialog.ts`, built by `dev/editor/summary.ts`):

| Button | Gamepad | Keyboard |
|---|---|---|
| Copy summary (the summary starts with the id) | ✕ [A] | Enter |
| Copy ID | △ [Y] | F |
| Close | ○ [B] | Esc / Backspace |

The mouse works too; the dialog releases pointer lock. While it is open, gameplay buttons and looking are locked and
the editor toggle waits. Copying uses the clipboard API (a secure context: localhost or city.lab over HTTPS). If the
browser refuses it (Chrome may want a key or click, not a gamepad press, as user activation), the text stays
selected in its field and the dialog says "press ⌘+C / Ctrl+C".

The summary is about ten lines: a header, one line per prop type and net action, and a pointer to the full rows.
This is the summary of the real session `20261103-222447-fnf9i8`:

```
Edit session 20261103-222447-fnf9i8 · localhost:5220 · build 1b0c2ea8 · 2026-11-04 00:24–00:41 UTC+2 · 42 edited objects
- 15 bench turned 30–175° (med 122°; 11 ccw, 4 cw) (no reason) · t0_-1_1 around E 23459376 N 6704079 (within 95 m)
- 6 bench moved 0.5–1.8 m (med 1.5), carriageway edge 3.0 → 2.3 m, turned 123–154° (med 131°; 2 ccw, 4 cw) (no reason) · t0_-1_1 around E 23459448 N 6704123 (within 15 m)
- 6 sign moved 2.2–9.2 m (med 3.6), carriageway edge 1.4 → 2.7 m (no reason) · t0_1_0, t0_1_-1, t0_2_-1 around E 23459907 N 6704513 (within 265 m)
- 5 bike_rack moved 1.2–5.2 m (med 2.0), carriageway edge 4.0 → 3.2 m, turned 76–93° (med 91°; 2 ccw, 3 cw) (no reason) · t0_1_-1, t0_-1_1, t0_1_1 around E 23459777 N 6704348 (within 505 m)
- 4 bike_rack turned ~91° (3 ccw, 1 cw) (no reason) · t0_0_1, t0_1_0 around E 23459722 N 6704230 (within 305 m)
- 1 bench deleted (no reason) · t0_-1_1 around E 23459448 N 6704127
- 1 sign deleted (no reason) · t0_2_-1 around E 23460140 N 6704637
- 1 tree_willow deleted (no reason) · t0_2_-1 around E 23460090 N 6704708
- … 3 more edits in 3 smaller groups (tree_willow_v2, bin, lifebuoy)
Full details: session 20261103-222447-fnf9i8 (scripts/dev/edit_sessions.py show 20261103-222447-fnf9i8)
```

- **Objects, not commits.** Each object counts once, by its net change: its last effective edit, `after` against
  `original`, with the newest reason tag. The header adds "(N edits, M undone)" when there were re-edits or undos.
- **Net action.** Jitter is ignored: a move under 0.3 m, a turn under 3° and a height change under 0.1 m count as
  unchanged. The actions are deleted, moved + turned, moved, turned and height changed.
- **Magnitudes.** A move is the horizontal range and median. When most of the group has road context, it also gives
  the median distance to the carriageway edge before and after. A turn gives its magnitude and direction (ccw is
  positive yaw, counter-clockwise from above).
- **Reasons** come with counts when they are mixed. "No reason" means none was picked on the D-pad.
- **Position.** Up to three tiles, most edits first. The mean original EPSG:3877 position, plus the group's radius
  when it is 15 m or more.
- **Header.** The host (`location.host`), the multiplayer nick, the build sha and the times in local time with the
  UTC offset.
- **Overflow.** At most eight group lines; the rest fold into one line.
- The editor has no free-text notes yet, so the summary has none.

**Multiplayer.** Editing is per player. Each browser keeps its own session in localStorage, and previews stay local.
In a multiplayer session, `session.json` gets `player: {id, nick}`, and the summary header names the nick. Two
players editing at once make two sessions with two ids.

### Finding a session by its id

| Host | Store | How a Claude session reads it |
|---|---|---|
| Mac, Vite dev server (:5220, agents' :53xx) | `~/turku3d/edits/<id>/` (`TURKU3D_EDITS` overrides) | `python3 scripts/dev/edit_sessions.py show <id>`, then `effective <id>` / `clusters <id>` |
| city.lab (osgallery-dev, `city-web`) | `~/turku3d-local/city/edits/<id>/` on osgallery-dev | from the Mac: `edit_sessions.py pull <id>` (rsync into `~/turku3d/edits/<id>/`), then `show <id>`; on osgallery-dev itself `show <id>` finds it directly |

`show` prints `summary.txt`, the folder, the counts, the player and the build URL. Sessions closed before summaries
existed have no `summary.txt`; for those, use `clusters`.

## Session format and storage

Sessions live in `~/turku3d/edits/<session-id>/`. The Vite dev-server middleware writes them; `TURKU3D_EDITS`
overrides the root. On city.lab (`?editor=1`; the editor stays off there without it), `serve-dist.mjs --edits`
mounts the same middleware write-only into `~/turku3d-local/city/edits/` on osgallery-dev. It accepts only PUT and
POST from the same origin, and it has a 512 MB quota (`--edits-quota-mb`).

| File | Content |
|---|---|
| `session.json` | build, the tile generations seen, start/end, device, counts |
| `edits.jsonl` | one line per row id, sorted by seq |
| `summary.txt` | the close dialog's summary (written after the final `session.json`) |
| `shots/<edit>-before.jpg`, `<edit>-after.jpg` | 384 px screenshots; local evidence, never commit them |

`edits.jsonl` holds three kinds of rows: **edit rows** (move / rotate / delete), **undo rows** and **tag rows**
(the reason, which arrives after the commit).

An edit row carries:

- the object's category, type, tile and generator placement id;
- provenance: source dataset and feature, the generator files and named rules, the tile's own record, and what
  the tile is missing;
- the original, before and after transforms, each in engine, EPSG:3877 and EPSG:3067 coordinates with yaw and
  heading;
- the delta and a preview matrix;
- the camera pose;
- the tile's generation pin;
- automatic context at the original and the new position:
  - collision surface under the object;
  - nearest road / kerb / sidewalk surfaces;
  - facades and walls;
  - road-graph edge with carriageway distance and side;
  - nearby props;
  - LiDAR evidence.

**Effective edits** are the edit rows that no undo row names, each with the reason of its newest tag folded in.
An object's net change is its last effective edit (`after` against `original`).

The browser also keeps the session, its rows and an outbox of unsent rows in localStorage
(`turku3d.editor.v1`). A reload restores the previews and resends anything the server hasn't confirmed.

## How sessions reach rulesmith

1. `python3 scripts/dev/edit_sessions.py list | effective | clusters [SESSION…]`. `clusters` groups effective
   edits by category, type, dataset, action and reason, with the median move and turn and the original
   surroundings.
2. Follow EDIT-FORMAT.md §7:
   1. Cluster the edits.
   2. Find the rule from `object.provenance.generator`.
   3. Change the rule, never the instance.
   4. Keep the generator and the tile migration on one shared function.
   5. Ship a tilepatch migration (no regeneration, no repin).
   6. Verify on real tiles: within 0.5 m and 10° of Jonni's edits, and similar objects change consistently.
   7. Write `incorporated.json` into the session folder.
3. Traffic-light heads are placed partly by the client (`traffic/signals.ts`: offset `lanes.width/2 + 0.35 m`) and
   partly by `pipeline/turku3d/roads/tile_graph.py` (stop events and controllers).

## The save-queue fix (2026-11-04)

Sessions `20261103-221501-zd82ho` (13 edits) and `20261103-222447-fnf9i8` (42 edits) saved only their first 7 and
10 rows, while every screenshot uploaded. The coordinator recovered both from localStorage; each now has one line
per row on disk.

**Cause.** Both ran page builds from before the fix.

- The old writer resent the whole outbox as one `keepalive` fetch.
- Play telemetry (`telemetry/index.ts`, keepalive fetch) and capture (`sendBeacon`) share the browser's 64 KiB
  in-flight keepalive budget.
- Once the queue grew past that budget, every retry was refused. Screenshots used plain fetch, so they kept going.
- Requests had no timeout, so a hung request could also hold the send lock.
- Row 6's lost reason was a race: an amendment made during an in-flight send was acknowledged by that send.

**Fix.** Integration commits 0666065b and 0968d889:

- no keepalive;
- 10 s / 20 s timeouts, and a send older than a minute no longer blocks;
- rows go out in batches of at most 8 rows / 48 KB, before `session.json`;
- every edit row has a `rev`, and the outbox clears an entry only for the revision it sent;
- reasons are append-only tag rows;
- the server is idempotent by row id and keeps the highest `rev`;
- a close waits until everything is saved, and the close is remembered across reloads;
- the badge and the HUD counter show pending rows.

`tests/unit/editor-session.test.ts` drives 42 edits through a server that fails, refuses bodies over 64 KiB, and
answers slowly and out of order, and asserts that the outbox drains with each row stored once.

**The live :5220 Vite server must be restarted** (`vite.config.ts` loads the middleware at startup) before the
`rev` guard is active. Until then the server still de-duplicates by row id.

The same session also fixed the "THREE.Texture: Unable to serialize Texture" console spam, which came from
`render/materialIdentity.ts`, not from the editor. It now answers texture lookups without serialising images.

## Pickable and not

**Pickable**, each resolved to its generator placement:

- every street-furniture prop row: bench, bin, waste container, ashtray, planter, table, bike rack and station,
  parking meter, info board and pylon, path sign, traffic sign, bus stop, register bollard;
- trees and shrubs (instance ids: `puut:` register, `osm:`, `lidar:x:z`, `area:`);
- street-light posts (matched by tile and base position: posts carry no id);
- traffic-signal heads: junction controller, approach edge, OSM way, event position, direction, lanes;
  - visual-only: phases, stop lines and drivers are unchanged;
- barriers from `coarse/fences.py`: fences, walls, hedges, chains, stone/brick, bollard posts;
  - picked anywhere along their length;
  - id `barrier:<tile>:<kind>:<segment>`.

**Not pickable:**

| Object | Why |
|---|---|
| Parked cars | physics bodies you can enter and drive, shared in multiplayer; a visual-only move would separate the car from its collider |
| Kiosks | not generated as street objects |
| Hung and facade lights | no post to aim at |
| Buildings, stairs, tunnels, ground | generated geometry, not placements |

Known preview limits: the tile's merged shadow batch still casts a moved or deleted prop's old shadow, and its
collision stays where the generator put it.

## Open items, ranked

1. **Parked cars as record-only edits.** Pick a parked car, record move/delete edits with a ghost marker at the
   new pose, and leave the physics car alone. Rulesmith then fixes `pipeline/turku3d/coarse/parked.py`.
2. **Generator ids for barriers and traffic lights.**
   - Barriers: `coarse/fences.py` prototypes need `instance_object_ids` (`osm:way:<id>:<segment>`,
     `osm:node:<id>`).
   - Traffic lights: the roads stop events need the OSM `traffic_signals` node id. Today both are matched by tile,
     kind and position.
3. **Other missing generator metadata:**
   - the props rows' yaw and the orientation rule's chosen road edge (today estimated from geometry, mod 180°);
   - per-post ids in the `lights` field;
   - per-object adjustment codes from the furniture stage (EDIT-FORMAT.md §9).
4. **Restart :5220** to activate the `rev` guard (see above).
5. **Small items:**
   - previews for hung and facade lights;
   - `tests/unit/input.test.ts` fails on integration because `dev/vehicleGallery.ts` reads the Gamepad API
     directly (another loop's page).
