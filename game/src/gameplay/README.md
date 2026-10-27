# Gameplay

`contract.ts` is the shared interface between the mission system and the minimap/router: targets,
`MissionHudState`, `MissionHudSource` and `Router`. It changes only with a message to both owners.
All text the HUD shows is English.

## Missions

Story, locations, economy and the state machine are specified in
[docs/design/story.md](../../../docs/design/story.md).

| File | Role |
|---|---|
| `missions/missions.ts` | `MissionSystem`: deterministic state machine (`idle → permit_needed → at_pizzeria → carrying → delivered \| failed`), delivery timer, money, permit counter, plates, kidney, vehicle ownership, open shop and its `ShopView`, dialogue queue; implements `MissionHudSource`; persists to `localStorage` key `turku3d.missions` (format 2, migrates format 1) |
| `economy.ts` | Prices, permit rental, payouts, base pay, tip formula, par time, deadline |
| `content/locations.ts` | Pizzeria, black market and dealer targets, and the bought-vehicle kerb spot |
| `content/shops.ts` | The three shops: target, menu background, options in order with icons; zone label lines per kind and zone ground-ray heights |
| `zones.ts` | `ZoneMarkers`: glowing ring, light wall and floating label at every HUD target, coloured like the minimap (`KIND_COLOURS`) |
| `content/deliveries.ts` | Delivery missions in play order (repeat after the last) |
| `content/dialogue.json` | Every displayed line: intro, hints, prompts, notices, pay lines, character dialogue |
| `content/dialogue.ts` | Typed lookup by line id (`lineText`, `dialogueLine`) |

`main.ts` constructs the system with the street router (below) and `localStorage`, calls
`missions.update(dt, { position, interact, input, vehicleId, choose, leave })` every frame (dt 0
while paused or on the start screen), asks `missions.mayDrive(id)` before entering a vehicle, caps
the player's car at bicycle speed when the bicycle is owned, scales run and sprint speed by
`missions.runFactor` (the kidney), and moves the player's car to the dealer kerb after a purchase.
The interaction button is the `mission` input action (Xbox A / keyboard E / touch Use); targets act within 3 m.
At a shop it opens the menu: `missions.shop` is then a `ShopView` that `ui/shopMenu.ts` renders,
and the menu's `choose` / `leave` go back through the next `update`. While `shopMenu.open`,
`main.ts` feeds no movement, camera or vehicle input (the car holds its handbrake) and swallows the
other buttons (Escape is pause and D-pad down is mute outside the menu). `ZoneMarkers` follows the
same HUD source as the minimap and finds each zone's ground with a physics ray once its collision
tile is resident. The start screen shows the intro lines unless `hasSavedGame(localStorage)`, and
`missions.begin()` runs when it closes.
`window.__game.missions`, `__game.zones`, `__game.shopMenu` and `__game.orbit` expose the pieces;
`__game.state()` adds `missions` (with `shop`, `permit`, `runFactor`), `zones` and `shopMenu`
snapshots for tests.

### Adding a delivery

1. Pick an address inside the published area and a street-side point in front of it. Check that the
   point is walkable and has no building above it in the published collision tiles.
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

A saved game stores only the delivery index, so adding deliveries keeps existing saves valid.

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
npx vitest run tests/unit/missions.test.ts tests/unit/shops-ui.test.ts tests/unit/input.test.ts
# Chrome checks on the real tiles (build the game first: make game-build)
python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test -c playwright.tile.config.ts \
  tests/e2e-tile/missions.spec.ts tests/e2e-tile/shops.spec.ts
```

`missions.spec.ts` (WebGPU) plays permit, pickup and a timed delivery through the menus and writes
screenshots of each prompt to `$TURKU3D_WORK/reports/gameplay-missions/`. `shops.spec.ts` (both
backends) looks at the pizzeria zone from the street, walks into the black market zone (zone visible,
prompt shown), opens the menu with E, checks that holding W leaves the player where it was, rents a
permit (money and permit counter), closes with Escape and checks the player moves again, then opens
the pizzeria and dealer menus. Screenshots: `$TURKU3D_WORK/reports/gameplay-shops-ui/<backend>-*.png`.
To run them on the published tiles without building tiles, add `TILE_DIST=$TURKU3D_DATA/dist/tiles`.

## Map and route guidance

### Wiring

`main.ts` (play mode) creates both pieces after the manifest and assets load:

```ts
const { router, minimap, area } = await createGameplay(manifest, document.body);
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
are drawn once into an offscreen layer (3 px/m, road width by class); each redraw only rotates
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
- **Lost**: below the lowest ground of the loaded tiles (manifest `h[0]`) minus 5 m, more than 5 m
  outside the area's bounding box, more than 2 s with the feet or car body over 0.3 m under the
  river surface (no swimming), or more than 3 s without ground contact. Landing safely again
  cancels it.
- **Reset**: the newest sample at least 3 s older than the last safe frame (before the slide
  towards the edge began; the oldest sample or the spawn otherwise), moved 2 m inside the edge
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
(`lost`, `countdown`, `samples`, `resets`, `waterDepth`, `edgeDistance`, `hud`) expose it for tests.

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
