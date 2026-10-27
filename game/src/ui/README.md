# UI

DOM overlays drawn over the WebGPU/WebGL canvas. Styles live in `game/index.html`.

| File | Role |
|---|---|
| `hud.ts` | Debug HUD (H): backend, frame times, resident tiles, collision, player position |
| `startScreen.ts` | Controls, controller status, the main menu (Continue / New game / Load game) and the story intro of a first game; quitting a game reopens it |
| `menu.ts` | `MenuList` (the row list every menu uses), `MenuPanel` (a titled menu page), `PauseMenu`, and `SaveMenuFlow` (start, pause, save, load and confirm pages over `core/save.ts`) |
| `shopMenu.ts` | `ShopMenu`: the full-screen shop menu that renders a `ShopView` from the mission system |
| `fullscreen.ts` | `FullscreenPolicy`: touch play runs the scene in fullscreen; the "Tap to continue in fullscreen" overlay |
| `devMenu.ts` | `DevMenuFlow`: the pause menu's Developer pages (localhost only): money, fly mode, teleport, time of day |

## Touch play and fullscreen

The touch controls themselves are an input source (`game/src/input/README.md`). On a touch player
(`input.source === 'touch'`) the start screen works in the normal browser view and shows a touch line.
Closing it calls `FullscreenPolicy.enterScene` synchronously inside the tap: `requestFullscreen()` on the
document element, then `screen.orientation.lock('landscape')`, and a screen wake lock (re-taken when the page
becomes visible again); every rejection is ignored. While the scene runs, `update()` each frame applies
`mustBlock`: a touch player outside fullscreen gets the overlay, the touch buttons hide and the game pauses
(`guardPause` resumes only a pause the policy made). A tap on the overlay requests fullscreen again.

- A Home Screen launch (`display-mode: standalone` or `fullscreen`, or iOS `navigator.standalone`) counts as
  fullscreen. `game/public/manifest.webmanifest` asks for `display: fullscreen`, landscape.
- Where the browser cannot fullscreen an element (iPhone Safari), the start screen shows "For fullscreen on
  iPhone add the page to the Home Screen." and play continues without the overlay.
- Keyboard and gamepad players are never blocked or sent to fullscreen.
- In touch play the help line, the audio panel and the time slider are hidden (CSS in `index.html`) so the
  thumb buttons own the bottom-right corner; the corner minimap shrinks to 45 % of the viewport height, at
  most 240 px, so the HUD stays readable at 844×390.
- Touch devices without `?backend=` use WebGL2 unless `navigator.gpu.requestAdapter()` returns an adapter
  (`defaultBackend` in `render/renderer.ts`).

Unit tests: `cd game && npx vitest run tests/unit/touch.test.ts`. Browser: `tests/e2e/touch.spec.ts`.

## Main menu, pause menu and save slots

`core/save.ts` keeps three named slots and an autosave in `localStorage` key `turku3d.saves`
(`{version: 1, slots: {auto, slot1, slot2, slot3}}`). A slot is
`{version, savedAt, label, mission, player: {E, N, H, yaw} | null, vehicle: {owned, E, N, H, yaw} | null,
clock: {timeOfDay} | null, stats: {deliveries, money}}`: `mission` is the opaque text of
`MissionSystem.serialize()` (the mission module keeps its own format and migrations), E/N are EPSG:3877,
H is N2000, yaw is the engine yaw in radians (the camera yaw on foot). The label is the last delivery's
address and the Helsinki clock time (`Linnankatu 21 · 14:05`, `Before the first delivery · 14:00`).
A mission save from before the slots (key `turku3d.missions`) becomes slot 1 on the first run and the old
key is removed; it has no position, so it continues from the spawn point at the default time.

The game autosaves (`Autosave`) after every finished delivery and every shop purchase or sale
(`missions.update(...).checkpoint`), on Quit to start screen, and on `pagehide` or a hidden tab while a
game runs (never from the start screen).

- **Start screen**: Continue (the most recent slot, with its label, delivery count, money and save
  time), New game (asks first when any save exists), Load game (greyed without saves).
- **Pause menu** (`pause`: Menu, Escape, touch Pause): Resume, Respawn at the start, Save game (Slot 1–3; a filled slot asks
  before it is overwritten, then "Saved to Slot N."), Load game (autosave and slots, empty ones greyed),
  New game (always asks), Quit to start screen. While it is open the world is paused and its input is
  locked like in a shop menu. Menu resumes from any page; Escape and B go back one page (resume on the
  first); the touch context is `menu`, so the touch Back button does the same. The fullscreen overlay
  rule is unchanged: a pause the player chose survives a trip out of fullscreen.
- **Respawn at the start** (every build, the escape from a stuck spot such as the inside of a building): asks
  "Respawn? You keep your money and order.", then `SaveGameController.respawn` puts the player at the new-game
  spawn (Läntinen Rantakatu 15) with the same ground snap and safe-reset restart as a loaded game. Missions, money
  and a running order (its timer too) are untouched. Vehicles stay where they are, except the driven one: the own
  car returns to its start parking, any other vehicle to the kerb 5.5 m behind it (`kerbBehind`).
- **Load / new game** (`gameplay/saveGame.ts` `SaveGameController`): leaves the car, restores the missions (a running order keeps
  its timer), the clock and the time slider, puts the own car at its saved pose when one is owned (else at
  its spawn parking), streams the collision tile under the saved position and snaps the player onto the
  ground with a downward ray from 2 m above the saved height (from the sky when that misses), then waits
  paused until the physics worker reports the character there. Safe reset then restarts its sampling
  there (`SafeReset.restart`), so a later fall never returns the player to the previous game's spot.

All menus share `MenuList`: `menuUp` / `menuDown` (D-pad, left stick, arrows, W/S) move the selection,
`menuSelect` (A, Enter, E) or a `pointerup` on a row picks it, `menuBack` (B, Escape, Backspace, touch
Back) goes back. A pick runs synchronously inside the tap, so the start screen's fullscreen request still
has its user gesture. Disabled rows carry `aria-disabled="true"` and the `disabled` class.

Unit tests (no DOM): `cd game && npx vitest run tests/unit/save.test.ts tests/unit/missions.test.ts`
(slot CRUD, corrupt storage, migration, labels, autosave triggers, every menu page). Browser, both
backends, screenshots in `$TURKU3D_WORK/reports/gameplay-save-menu/`:
`cd game && python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test tests/e2e/save-menu.spec.ts`.

## Developer tools (localhost only)

`core/localOnly.ts` `devToolsEnabled(location)` is the same environment check as play telemetry: on for
`localhost`, `127.0.0.1` and `[::1]` and with `?dev=1` on another host, off with `?dev=0`, and always off on
`github.io` (the published Pages build), where neither the Developer row nor fly mode exists. Where it is on, the
pause menu lists **Developer** (before Quit), whose pages (`devMenu.ts`, wired in `play.ts`) are:

- **Money +1 000 €**, **Money +10 000 €**: `MissionSystem.grantDevMoney` adds to the local wallet only. Nothing is
  sent to the multiplayer server (it counts only paid drop-offs), and the mission save and the slot's
  `stats.devMoney` keep the total, so a scoreboard can leave it out; the slot details show `(N € dev)`.
- **Fly mode on/off** (also a double tap of jump in the game, `gameplay/flyMode.ts`): Minecraft-style flight
  without gravity at three times walking speed (`FLY`), ten times with sprint. The move stick or keys move in
  the camera's ground plane; `flyUp` (Ⓧ, RT, Space) rises and `flyDown` (Ⓑ, LT, Q) sinks. The physics worker
  keeps the character's collisions (`CharacterInput.fly`), so walls and roofs stop it; the camera is the on-foot
  orbit camera. Safe reset holds while flying and re-arms at the first grounded contact after it
  (`RecoveryMonitor.hold`, `gameplay/README.md`). Entering a vehicle ends the flight.
- **Teleport to...**: the named places of `gameplay/content/places.ts` (Office, Pizzeria, Tuomiokirkko, Turun
  linna, Kauppatori, Aurakatu 6a) and **Enter E,N** (a browser prompt for `E,N[,heading]`, EPSG:3877, like
  `?spawn=`). `SaveGameController.teleport` places the player like a loaded game: it streams the collision tile,
  takes the ground under E/N from the sky (the `?spawn=` rule), waits paused until the worker reports the
  character there and restarts safe reset. A place whose collision tile is not built says "Not built: arrives
  flying": the player arrives at 60 m (N2000) with fly mode on.
- **Time of day**: dawn, noon, dusk or night (`core/sun.ts` `phaseTime`: the sun at 4° rising, highest, at 4°
  setting, lowest in the night after) on the game clock's day; the time slider follows.

Unit tests: `cd game && npx vitest run tests/unit/dev-tools.test.ts tests/unit/recovery.test.ts tests/unit/physics.test.ts`
(the gate, the fly controller, dev money in the save, the day phases, the pages, the recovery hold, flight
collisions). Browser (WebGPU, synthetic tiles, screenshots in `$TURKU3D_WORK/reports/gameplay-dev-tools/`):
`cd game && npm run build && python3 ../scripts/dev/host_slots.py browser -- ./node_modules/.bin/playwright test tests/e2e/dev-tools.spec.ts --project webgpu`.

## Shop menu

`ShopMenu` draws its rows with the shared `MenuList` (`menu.ts`, row prefix `shop`).
`main.ts` calls `shopMenu.render(missions.shop, input)` every frame (cheap when the view object is
unchanged) and, while `shopMenu.open`, `shopMenu.handle(input)` before the mission update. `handle`
moves the selection with `menuUp` / `menuDown` (D-pad, left stick, arrows, W/S) and returns
`{ choose, leave }`: `menuSelect` (A, Enter, E, or a click or tap on a row) chooses the selected
option, `menuBack` (B, Escape, Backspace, the touch Back button) leaves. Rows react to `pointerup`, so a
tap selects and chooses at once. The mission system decides what a choice does;
the menu only shows its result. The layout is the shop's background illustration with a dark
gradient on the left, the shop name, cash and permit counter, the shopkeeper's latest line (the
same text the HUD shows, so the voice clip plays), and one row per option: icon, label, detail
(or the pizzeria's order quote: address · route metres · par m:ss), price (`80 €`, income as
`+400 €`). Greyed rows show the reason instead of the detail. All texts except the button hints and
the status row are dialogue lines (`game/src/gameplay/content/dialogue.json`).

Unit tests (no DOM): `cd game && npx vitest run tests/unit/shops-ui.test.ts`. The DOM, the input
lock and the art are checked in Chrome by `tests/e2e-tile/shops.spec.ts`
(see `game/src/gameplay/README.md`, "Tests").

## Menu art

`game/public/assets/ui/`: three 1280×720 backgrounds and nine 256×256 icons, WebP, each under
200 KB (largest `bg_dealer.webp`, 129 KB; 338 KB in total). All but `icon_scooter` were generated with the project's
Codex image runner and are project-owned (`game/public/assets/LICENSES.md`).

| Job id = file | Used by | Subject (full prompt: `textures/turkutex/ui_art.py`) |
|---|---|---|
| `bg_market` | Black market | Stall under the stone arch of Auransilta at night, bare bulb, wet cobbles, hooded seller from behind |
| `bg_pizzeria` | Pizzeria Linnanportti | Old family pizzeria counter, wood-fired oven, pizza boxes, delivery bag, blank menu board |
| `bg_dealer` | Rantakadun Kulkine | Used-vehicle lot on the Aura riverbank at dusk, string lights, sales cabin |
| `icon_permit` | Rent a courier working permit | Laminated permit card on a lanyard |
| `icon_plates` | Forged license plates | Two blank plates with a rubber band |
| `icon_kidney` | Sell your kidney | Cartoon kidney on ice in a picnic cooler |
| `icon_warning` | Buy back the permit expiry warning | Phone with an alert bubble |
| `icon_pizza_box` | Take the next delivery order | Steaming pizza box |
| `icon_slice` | A slice for yourself | Pizza slice |
| `icon_coins` | Tip rules, sell your vehicle back | Coins and banknotes |
| `icon_scooter` | Scooter | Moped scooter with a pizza box on the rear rack (interim hand-drawn file, see below) |
| `icon_car` | Car | Dented second-hand hatchback |

Every prompt shares one style block (flat-shaded vector illustration, palette navy #141a2e, slate
#2c3550, amber #f2a541, brick #c8553d, teal #3fa7a0, off-white #efe6d8; no text, letters, logos or
watermarks) and a framing line: backgrounds are wide with the subject on the right half because
the menu panel covers the left; icons are one centred object on flat #1b2133, the menu's row colour.
Prompt version `ui-art-v1`, routing kind `library` (`textures/codex-routing.toml`: `gpt-6-astra`,
medium effort).

Generation record (2026-10-27): 12 jobs, 12 image calls, no retries, one PNG each (backgrounds
1672×941, icons 1254×1254), weekly Codex usage 27 % before and 27 % after. Thread ids are in the
run's `out.jsonl`; the content-hash cache makes a rerun of unchanged prompts free.

Regenerate (sandbox environment loaded, `make textures-venv` once):

```sh
cd textures
.venv/bin/python -m turkutex.ui_art jobs "$TURKU3D_WORK/ui-art/jobs.jsonl"
.venv/bin/python -m turkutex codex run "$TURKU3D_WORK/ui-art/jobs.jsonl" --out "$TURKU3D_WORK/ui-art/run1" -j 4
.venv/bin/python -m turkutex.ui_art encode "$TURKU3D_WORK/ui-art/run1" ../game/public/assets/ui
```

`icon_scooter.webp` (2026-10-27) is an interim, project-owned flat-vector drawing in the same palette:
`textures/ui-art/icon_scooter.svg`, rendered at 256×256 in headless Chromium and saved as WebP (quality 90)
with Pillow. The `ART` entry `icon_scooter` holds its prompt; the next Codex `ui_art` run replaces it.

`encode` centre-crops each primary PNG to its aspect ratio, scales it with Lanczos and writes WebP at
the highest quality (86 down to 38) that fits 200 KB. To change one picture, edit its subject in
`ART` (or bump `PROMPT_VERSION` for all), rerun the three commands, and look at the result before
committing. Tests: `cd textures && .venv/bin/python -m pytest -q tests/test_ui_art.py`.
