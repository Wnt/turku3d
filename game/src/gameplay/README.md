# Gameplay

`contract.ts` is the shared interface between the mission system and the minimap/router: targets,
`MissionHudState`, `MissionHudSource` and `Router`. It changes only with a message to both owners.
All text the HUD shows is English.

## Missions

Story, locations, economy and the state machine are specified in
[docs/design/story.md](../../../docs/design/story.md).

| File | Role |
|---|---|
| `missions/missions.ts` | `MissionSystem`: deterministic state machine (`idle → permit_needed → at_pizzeria → carrying → delivered \| failed`), delivery timer, money, permit counter, plates, kidney, vehicle ownership, open shop and its `ShopView`, dialogue queue; implements `MissionHudSource`; `serialize()` / `restore(text \| null)` for the save slots (`core/save.ts` stores the text; format in `missions/save.ts`) |
| `saveGame.ts` | `SaveGameController`: captures the running game into a save slot, places a loaded or new game (missions, clock, own car, player on the ground), autosave triggers, the pause menu's frame handling (`game/src/ui/README.md`, "Main menu, pause menu and save slots") |
| `economy.ts` | Prices, permit rental, payouts, base pay, tip formula, par time, deadline |
| `content/locations.ts` | Pizzeria, black market and dealer targets, and the bought-vehicle kerb spot |
| `content/shops.ts` | The three shops: target, menu background, options in order with icons; zone label lines per kind and zone ground-ray heights |
| `zones.ts` | `ZoneMarkers`: glowing ring, light wall and floating label at every HUD target, coloured like the minimap (`KIND_COLOURS`) |
| `content/deliveries.ts` | The scripted opening: four deliveries in play order by address, each with a fallback sidewalk point for a pool that lacks it (they repeat only without an address pool) |
| `content/addresses.ts` | Loads `manifest.addresses` (the pool published by `pipeline/turku3d/content`: drop-off, street point, route metres), checks its origin is the pizzeria, skips addresses in tiles the manifest lacks |
| `missions/stops.ts` | `Stops`: an order stop by id (`delivery_<n>` or a pool slug); a scripted delivery takes its address's pool drop-off and keeps its own point as the fallback; `relocate` moves a rejected drop-off to its next candidate |
| `missions/dropoffs.ts` | Street-level check of a drop-off before its marker is placed (`DropoffGuard`, used by `ZoneMarkers`): a point on a building or more than 2 m off the street height around it is rejected with a console warning and moved by `MissionSystem.relocateDropoff` |
| `missions/save.ts` | The mission save text (format 3, opaque to `core/save.ts`) and the migrations of formats 1 and 2 |
| `missions/orders.ts` | Seeded order draws after the opening: no repeats until the pool is exhausted, distance ladder, short hops, every fifth order a double |
| `content/dialogue.json` | Every displayed line: intro, hints, prompts, notices, pay lines, character dialogue |
| `content/dialogue.ts` | Typed lookup by line id (`lineText(id, vars?)` fills `{name}` placeholders of unvoiced lines, `dialogueLine`) |

`main.ts` constructs the system with the street router (below) and the address pool, calls
`missions.update(dt, { position, interact, input, vehicleId, choose, leave })` every frame (dt 0
while paused or on the start screen), asks `missions.mayDrive(id)` before entering a vehicle, scales
run and sprint speed by `missions.runFactor` (the kidney), and parks a bought vehicle at the dealer kerb
when `missions.vehicle` changes: the scooter (physics id `SCOOTER_ID`, spawned then) or the player's car
(`OWN_VEHICLE_ID`). `ownedVehicleId(missions.vehicle)` is the physics id deliveries accept.
The interaction button is the `mission` input action (Xbox A / keyboard E / touch Use); targets act within 3 m.
At a shop it opens the menu: `missions.shop` is then a `ShopView` that `ui/shopMenu.ts` renders,
and the menu's `choose` / `leave` go back through the next `update`. While `shopMenu.open`,
`main.ts` feeds no movement, camera or vehicle input (the car holds its handbrake) and swallows the
other buttons (Escape is pause and D-pad down is mute outside the menu). `ZoneMarkers` follows the
same HUD source as the minimap and finds each zone's ground with a physics ray once its collision
tile is resident. The mission system does not touch storage: `serialize()` returns its save text and
`restore(text)` continues from one (null starts a new game; dialogue, the open shop and slice energy are
dropped, a running order keeps its timer). `update()` returns `checkpoint: 'delivery' | 'purchase' | null`
when a delivery ended or something was bought or sold, and `main.ts` autosaves then; `deliveries`,
`lastAddress` and `missionSummary(text)` feed the slot list (save slots and menus: `game/src/ui/README.md`).
The start screen shows the intro lines only when no save exists, and `missions.begin()` runs once a
loaded or new game is placed.
`window.__game.missions`, `__game.zones`, `__game.shopMenu` and `__game.orbit` expose the pieces;
`__game.state()` adds `missions` (with `shop`, `permit`, `runFactor`), `zones` and `shopMenu`
snapshots for tests.

### Delivery addresses

Orders after the opening come from the published address pool; a new tile build adds its
addresses without a code change (see `docs/design/story.md`, "Address pool"). The scripted opening
resolves through the pool too: a scripted delivery takes its address's pool drop-off, route metres
and par, and its own `point` only when the pool lacks the address. Before the marker of any
drop-off is placed, `DropoffGuard` (`missions/dropoffs.ts`) checks it against the collision: ground
of building material, or more than 2 m (`MAX_STREET_STEP_M`) from the median street height on two
rings around it, rejects it with `[missions] drop-off <address> at x, z rejected: <reason>` in the
console, and `MissionSystem.relocateDropoff` moves it (trigger and marker) to the next candidate:
the pool's point, the scripted fallback, the pool's street point, then the nearest street-network
point. A drop-off with every candidate rejected gets no marker. Moves are not saved; a reload checks
again. To change the scripted opening:

1. Pick an address inside the published area, preferably one in the pool
   (`dist/tiles/addresses/<sha>.json`). Its fallback point is the pool's `point`; for an address the
   pool lacks, apply the pipeline's rule to the buildings layer: 2.5 m (`SIDEWALK_M`) out from the
   street-side door wall nearest the register point, outside every footprint.
2. Add the lines `hint_deliver_<n>`, `pizzeria_handover_<n>`, `customer_<n>_fast`,
   `customer_<n>_ok` and `customer_<n>_slow` to `content/dialogue.json` (hints are
   `"voiced": false`).
3. Append `delivery(<n>, '<address>', { x, z })` to `DELIVERIES` in `content/deliveries.ts`, keeping
   the list ordered by route distance, and add the row to the table in `docs/design/story.md`.
4. Run `npx vitest run tests/unit/missions.test.ts` from `game/`: it checks that every referenced
   line exists and that prices quoted in lines match `economy.ts`.
5. Voice the new lines: `make voice-setup` (once) and `make voice-build` from the repo root, then
   commit the clips in `game/public/assets/voice/` (see `audio/voice/README.md`). The gate fails on a
   voiced line without a current clip. The game plays a clip when the HUD shows its exact text.

A saved game stores scripted stops by id (`delivery_<n>`) and pool stops by address slug, so a
rebuilt pool keeps existing saves valid; an order whose address left the pool is dropped on load.

### Adding a shop option

Add the id to `OptionId` and the shop's `options` in `content/shops.ts` (with an icon from
`public/assets/ui/`, see `ui/README.md`), the lines `option_<id>` and `detail_<id>` (unvoiced) and
any reply (voiced) to `dialogue.json`, its price and availability to `optionState()` and its effect
to `choose()` in `missions.ts`, and a row to the shop table in `docs/design/story.md`. New state
that must survive a reload goes into `MissionSave` with a format bump and a migration of the
previous format.

### Tests

```sh
cd game
npx vitest run tests/unit/missions.test.ts tests/unit/orders.test.ts tests/unit/dropoffs.test.ts tests/unit/shops-ui.test.ts tests/unit/input.test.ts
# Chrome checks on the real tiles (build the game first: make game-build)
python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.tile.config.ts \
  tests/e2e-tile/missions.spec.ts tests/e2e-tile/shops.spec.ts tests/e2e-tile/deliveries.spec.ts
```

`deliveries.spec.ts` (WebGPU, skipped without `manifest.addresses`) starts from a save that has
just finished the scripted four, takes the fifth and sixth orders at the pizzeria, checks both are
different pool addresses with the tally and the address hint, opens the big map, hands both over and
reloads with the delivered set intact; screenshots go to
`$TURKU3D_WORK/reports/gameplay-delivery-pool/`. `dropoffs.test.ts` resolves the four scripted addresses through a synthetic pool, checks their
fallbacks lie outside synthetic block outlines, and drives `DropoffGuard` over a synthetic street
with a roof. `missions.spec.ts` (WebGPU) plays permit, pickup and a timed delivery through the menus
(the first drop-off's marker within 2 m of the spawn ground, unmoved by the check, and handed over
on foot) and writes screenshots of each prompt to `$TURKU3D_WORK/reports/gameplay-missions/`. `shops.spec.ts` (both
backends) looks at the pizzeria zone from the street, walks into the black market zone (zone visible,
prompt shown), opens the menu with E, checks that holding W leaves the player where it was, rents a
permit (money and permit counter), closes with Escape and checks the player moves again, then opens
the pizzeria and dealer menus. Screenshots: `$TURKU3D_WORK/reports/gameplay-shops-ui/<backend>-*.png`.
To run them on the published tiles without building tiles, add `TILE_DIST=$TURKU3D_DATA/dist/tiles`.

## Map and route guidance

### Wiring

`main.ts` (play mode) creates both pieces after the manifest and assets load:

```ts
const { router, minimap, area, streets } = await createGameplay(manifest, document.body);
const missions = new MissionSystem({ router, storage });
minimap.setSource(missions);   // any MissionHudSource; emptyHudSource shows no targets
```

Every frame it sets `router.mode` to the player's mode (`foot` | `car`) and calls
`minimap.update({ position, headingDeg, facingDeg, mode, nearEdge })`: the camera's compass heading turns
the map, the player's or car's heading turns the centre arrow. The map action (M / Xbox View)
toggles the large north-up view; the debug HUD (H) is independent. `window.__game.router` and
`window.__game.minimap` expose both for tests (`minimap.snapshot()`).

### Road graph asset

The pipeline layer [`turku3d.roads`](../../../pipeline/turku3d/roads/README.md) builds the graph
from the OSM snapshot; `make refresh` keeps it current and `make tile-build` publishes it as
`dist/tiles/roads/<sha256[:16]>.json`, referenced by `manifest.roads`. The live release carries it
with the tiles. `roadGraph.ts` loads and decodes it; a manifest without `roads` (the synthetic dev
tiles) gives an empty graph, so the minimap draws no streets and every route is null.

**Draft tiles' streets** (localhost, `world/coarseTiles.ts`): each draft carries its own OSM graph (`roads.json`
from `pipeline/turku3d/coarse`, clipped to the 250 m tile, every piece kept). `StreetMap.update(drafts)`
(`index.ts`) merges the published graph with the drafts' (`mergeRoadGraphs`: nodes merge by coordinate at 0.1 m,
so streets join where they cross a tile edge; an edge two parts both carry is kept once) and hands the result to
`StreetRouter.setGraph` and `Minimap.setGraph`. `play.ts` calls it from `CoarseTiles.onMapChange`: when a draft with
streets arrives or leaves, on a release swap, and after every 60 s manifest poll; the published graph is fetched
again when `manifest.roads` changed. The router routes onto draft streets as soon as they are on the map.

### Router (`router.ts`)

`StreetRouter` implements the contract `Router`. `snap()` projects a point onto the nearest
segment of an edge usable in the current mode (uniform 32 m grid index, at most `MAX_SNAP_M` =
80 m away, otherwise the end counts as off the graph). `route()` runs A* (Euclidean heuristic)
from a virtual start on the snapped edge to a virtual goal, honours car oneways, and returns the
polyline from the snapped start to the snapped goal including every bend. `distance()` sums the
polyline. A 60 × 60 grid routes corner to corner in well under 50 ms; the 318 m route of the Chrome
check on the published graph (1,015 edges) takes 0.3 ms.

### Minimap (`minimap.ts`)

A 240 × 240 CSS px rounded canvas at the bottom left, sized by `devicePixelRatio`. All streets
are drawn once into an offscreen layer (3 px/m, fewer when the merged graph is wider than 6,000 px at that
scale; road width by class), again when the graph changes (`setGraph`); each redraw only rotates
and scales that layer, then draws the route, markers (`KIND_COLOURS` per `TargetKind`, clamped to
the rim when off the map, the active one ringed) and the player arrow. It redraws only when the
view, the HUD state or the size changed. `RouteTracker` re-plans when the active target or travel
mode changes or the player is more than 5 m (`REROUTE_M`) from the last route start. Above the
canvas: the `message` line, the `timerSeconds` countdown (m:ss, red at 10 s) and `money` as €.
The view spans 200 m on foot, 320 m in a car and 650 m in the large map. Everything outside the
playable area (`setArea`, below) is darkened and its edge outlined, red within 20 m of it. A redraw measured
0.1–0.6 ms in Chrome on both backends, well inside the 60 fps budget.

## Safe reset (`recovery.ts`, `safeReset.ts`)

`safeReset.ts` (`SafeReset`) is the frame-loop driver `main.ts` calls; `recovery.ts` holds the rules.
The playable area is `PlayableArea`: the union of the manifest's collision tiles (a tile list, holes
included), with its bounding box. It reads the live `manifest.collision` record, so collision tiles added at
runtime (draft coarse tiles) join the area, and the box grows once the player stands on one. `RecoveryMonitor` gets a `Probe` from `main.ts` every unpaused
frame (the feet on foot, the body of the driven car in a car) and applies one rule set to both
(constants in `RECOVERY`):

- **Safe sample**: grounded (a car: three wheels down and upright), on a loaded collision tile
  inside the area, not under the river surface and at most 2 m/s vertical speed. One sample per
  second `{ time, x, y, z, heading, inVehicle }` goes into a 10 s ring buffer.
- **Armed**: the lost rules apply only after the first safe frame since the start, a loaded game
  or a teleport. A player who has not yet stood on safe ground (a debug spawn on the river bed, a
  placement in mid-air or before its collision arrived) has no known place to return to and is
  never reset. Every placement through the physics client (`PhysicsClient.placement`: the character
  via `setCharacterEnabled` with a position, a vehicle via `resetVehicle`) of the probed actor
  farther than 10 m from where it stood is a teleport: samples and any lost state are dropped and
  the rules wait for the next safe frame. Nearer placements (leaving a car, R in a car) keep them.
- **Lost**: below the lowest ground of the loaded tiles (manifest `h[0]`) minus 5 m, more than 5 m
  outside the area's bounding box, more than 2 s with the feet or car body over 0.3 m under the
  river surface (no swimming), or more than 3 s without ground contact. The edge of the built area is
  closed by the physics edge walls (`physics/edgeWalls.ts`, `world/README.md`), so in play the lost rules
  catch the river, a fall through a hole and a placement off the loaded tiles, not a drive off the edge. Landing safely again
  cancels it. Over a cell of the area whose collision has not arrived yet the player is streaming,
  not lost; once it loads, a player who sank below it is lost by the first rule.
- **Reset**: the newest sample at least 3 s older than the last safe frame (before the slide
  towards the edge began; the oldest sample otherwise), moved 2 m inside the edge
  (a car 4.3 m), snapped to the ground by a downward ray after its collision tile is resident. The
  car comes back upright with zero velocity and the player stays in it; the camera snaps behind.
  Samples newer than the target are dropped and the rules pause for 0.5 s. Missions, money and
  the delivery timer are not touched: a fall is not a failed delivery.

The river surface comes from a physics ray with the `water` filter (`physics/world.ts`), which hits
only the water sensor; every other query and the character pass through it. `main.ts` samples it
at 4 Hz. `ui/recovery.ts` (`RecoveryHud`, `#recovery`) shows "You left the map. Press R to return
to solid ground" (or "You fell into the river", with Ⓨ on a gamepad and Reset on touch) and a 5 s
countdown that resets automatically; `respawn` (Y, R, touch Reset) or `vehicleReset` (R) resets at
once. While lost, Y does not enter or leave a car. After the reset "Back on solid ground" fades over
3 s. Within 20 m of the area's edge the HUD shows "Edge of the built area" and the minimap's edge
line turns red; there are no invisible walls. `__game.recovery` and `__game.state().recovery`
(`armed`, `lost`, `countdown`, `samples`, `resets`, `waterDepth`, `edgeDistance`, `hud`) expose it for tests.
Each reset logs `[recovery] reset (<reason>) from x,y,z to x,y,z` to the console.

```sh
cd game && npx vitest run tests/unit/recovery.test.ts tests/unit/physics.test.ts
# Chrome, synthetic dev tiles (east edge x 250, river pond in c_1_2), both backends, after a game build:
cd game && python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test safe-reset.spec.ts
```

The Chrome check drives the car off the east edge (automatic reset, then R at once), walks into the
river (Y at once, then the countdown), checks the mission state is unchanged and the edge notice,
and writes screenshots to `$TURKU3D_WORK/reports/gameplay-safe-reset/`.

### Verification

```sh
cd game && npx vitest run tests/unit/router.test.ts tests/unit/minimap.test.ts tests/unit/input.test.ts
# Real tiles with the published road graph (sandbox ports, browser profile):
make refresh && make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" && make game-build
cd game && python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test \
  -c playwright.tile.config.ts minimap.spec.ts
```

The Chrome check injects a fake `MissionHudSource` whose active target is a street node about
300 m away along the network, asserts the route has more than two points, ends on the target and
re-plans after the player moves, toggles the large map, and writes screenshots and draw/route
timings to `$TURKU3D_WORK/reports/gameplay-map-route/`. On synthetic tiles without a road graph
it skips.
