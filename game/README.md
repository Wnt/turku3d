# game/

Game skeleton (ARCHITECTURE.md Phase 0 step 7). It uses Vite 8, TypeScript, three.js 0.186.1 (`three/webgpu` and TSL) with a WebGPU / WebGL2 switch, and Rapier 0.21 (`@dimforge/rapier3d`, non-compat build through `vite-plugin-wasm`) in a physics Web Worker. Visual tiles stream through 3DTilesRendererJS 0.5.3 with KTX2 and meshopt. The player is a third-person CC0 character, and a Kenney CC0 sedan with a raycast suspension, material-based curb strikes and gamepad driving.

Rendering v1 adds sky IBL, street-scale CSM, light GTAO, TRAA, bloom, AgX, material detail and planar river reflections.
See [the rendering README](src/render/README.md) for quality tiers, HiDPI/SSE, `window.__perf`, GPU timing and exact
1440p acceptance/screenshot commands. Hardware results currently use the synthetic dev tile until assembled AOI layers land.

## Play it and inspect the evidence

From the repository root:

```sh
# In a worktree, source its sandbox.env first (this sandbox: source ../sandbox.env).
make play                    # first run builds the four AOI tiles and game; cached startup <5 s
make playtest                # both backends, virtual Xbox, videos, screenshots, HTML report
TILE_DIST="$TURKU3D_DATA/dist/tiles" make playtest PLAYTEST_ARGS='--workers=2 --grep @aoi' # published AOI from a worker sandbox
open "${TURKU3D_WORK:-$HOME/turku3d/data}/reports/playtest/index.html"
make playtest-test game-test  # fast offline checks
```

`make play` prints the playable URL on `TURKU3D_PORT_BASE` (main default 5170).
It assembles published layers through `make tile-build`, verifies artifact hashes, and caches the production game under `$TURKU3D_WORK/build/game`. Game cache
keys include source, public assets, lockfiles and build configuration, including uncommitted
changes. The default main work directory is `~/turku3d/data`. `TILE_DIST` can select
an already assembled complete AOI; explicit tile directories are never rebuilt automatically.
When `build/layers/*/MANIFEST.json` exists, the assembler checks its input cache on every launch
so changed layers replace stale tiles. Publish layers before building a new AOI; existing verified
AOI builds can still be reused when no layer inventory is present.
Use `make play-prepare` to prepare everything without starting a server. Ctrl-C stops only
the server launched by this command. A busy port fails without selecting another port.

The start screen shows live controller status and Xbox bindings. Press Ⓐ or select **Start
playing**. Published starts use the mapped parking position; explicit debug spawns place
a car six metres to the right. `make playtest` uses
ports base+8 and base+9, separate isolated Chrome contexts, and one route at a time per
backend. The two backend workers still run in parallel.
Select subsystems with `make playtest PLAYTEST_ARGS='--grep @vehicle'`.

**Current limitation:** the assembled AOI supports ground and five-building routes, plus
measured foot/car curb crossings once the rebuilt curb tiles are published. Full street-loop,
real stair and quay-wall acceptance awaits structures and route surveys. The HTML report distinguishes real-AOI routes from synthetic controller
checks and lists these gaps even when the baseline suite is green. See
[playtest coverage and gate tags](tests/e2e-playtest/README.md).

## Commands

```bash
cd game
npm install
npm run dev # http://localhost:${TURKU3D_PORT_BASE:-5170}/  (COOP/COEP on; ?backend=webgl for WebGL2)
npm run build          # tsc --noEmit + vite build -> $TURKU3D_WORK/build/game
npm test               # vitest (Node): .col reader, Rapier character/car on the dev tiles, origin, URL params, sun
npm run test:e2e # build + Playwright, Chrome channel (real Metal GPU), both backends; screenshots in $TURKU3D_WORK/reports/screenshots/
npm run test:render    # render tiers/material/ID contract + fixed-path 1440p performance
npm run dev-tiles      # regenerate public/dev-tiles/ (dummy tileset)
TILE_DIST="$TURKU3D_WORK/dist/tiles" npx playwright test -c playwright.tile.config.ts   # after npm run build: pipeline stub tiles (make tile)
npm run probe -- "http://localhost:${TURKU3D_PORT_BASE:-5170}/?backend=webgl" out.png 6000   # debug: console, HUD and screenshot of any URL
```
The build uses a relative base and needs no COOP/COEP headers, so the integrator's live release is also
published to [GitHub Pages](https://wnt.github.io/turku-game/) (`scripts/dev/README.md`, "GitHub Pages").
Turku loads by default on local servers and Pages; use `?scene=dev` for the synthetic test world.
The home-screen manifest starts at `./` within scope `./`, so it keeps the deployment path and loads Turku.
A page-relative `?tiles=` (e.g. `../tiles/tileset.json`) reads `manifest.json` from the same directory.
The same commands are available from the repo root through `mk/game.mk`: `make game-dev`, `make game-build`, `make game-test`, `make game-e2e`, `make game-dev-tiles`, `make game-preview`, `make game-serve-dist` and `make game-test-all`.

## Audio

Game audio v1 adds material footsteps, positional car/impact/door effects, city/river/gull/traffic/wind ambience,
and a connection blip. The Audio panel controls master and channel volumes. D-pad down/N mutes; Menu/Escape pauses.
Audio unlocks on first input; if Chrome keeps it suspended after gamepad input, click once or press a key.
See [audio setup and tests](src/audio/README.md) and [CC0 sources](public/assets/audio/LICENSES.md).

## Controls

Chrome on macOS exposes Xbox Elite Series 2 as **Xbox Wireless Controller**, standard mapping. Press a controller button once after connecting it. Hot-plug and disconnects are polled every render frame; nonstandard mappings are ignored. HUD prompts switch between Xbox glyphs and keyboard labels with the active input source.

| Action | Xbox (on foot) | Xbox (car) | Keyboard / mouse |
|---|---|---|---|
| Move / steer | Left stick, analog walk → jog | Left stick | WASD / arrows |
| Sprint | A: hold, or tap for a 0.45 s burst while moving | — | Shift |
| Slow walk | Partial left stick | — | Alt |
| Jump | X | — | Space |
| Enter / exit car | Y (within 4 m) | Y | F |
| Throttle | — | RT | W / Up |
| Brake / reverse | — | LT, brakes forward motion before reversing | S / Down |
| Handbrake | — | RB | Space |
| Look | Right stick | Right stick | Click canvas for mouse lock, move mouse |
| Reset camera | R3 | R3 | C |
| Zoom | — | — | Mouse wheel |
| Map: minimap ↔ large map | View | View | M |
| Pause / resume | Menu | Menu | Escape |
| Mute / unmute | D-pad down | D-pad down | N |
| Reset car upright | — | — | R |
| Return to solid ground (only when off the map or in the river; also automatic after 5 s) | Y | Y | R (touch: Reset) |
| Toggle debug HUD | — | — | H |

Full stick = 3.6 m/s jog; sprint = 6 m/s; keyboard slow walk = 1.5 m/s. Input magnitude is preserved after a radial dead zone (left 0.16, right 0.12) and power response curves (1.35 / 1.6). Acceleration and deceleration are smoothed. Jump is a press edge, retained across worker latency and frames without a physics tick. Holding jump does not auto-jump on landing. Blur clears held input. Dual-rumble is feature-detected and used on landing and curb strikes; unsupported actuators fail silently. Trigger rumble is not required.

`src/input/` is the sole owner of browser input. Gameplay reads named actions (`moveX/Y`, `lookX/Y`, `sprint`, `jump`, `interact`, `throttle`, `brake`, `steer`, `handbrake`, `cameraReset`, `pause`, `map`, plus keyboard utilities). `Input.rebind(action, bindings)` replaces one action's serializable descriptors; `Input.bindings` can be saved as JSON by a settings client. No settings UI/storage is added. For example, `input.rebind('jump', [{kind:'button', index:1}, {kind:'key', code:'KeyJ'}])` binds B/J and updates the HUD glyph. Mouse look is accumulated as radians; right-stick look is frame-rate independent. The unit gate scans gameplay sources for direct browser input reads outside this directory.

## Character locomotion and verification

The KCC moves on `stair_slope` (material 16), while `foot` worker queries prefer `stair_tread_helper` (17, query-only) over solid ground. Helpers can lie below the slope. The visual rig uses analytic thigh/shin IK, separate ankle targets, surface-normal foot orientation and a smoothed pelvis offset. Jumping disables IK. Asynchronous results are bounded to one outstanding request per foot and rejected after large movement. The capsule remains physical; only the rendered vertical position is smoothed across autosteps. Camera focus follows that rendered position.

Each worker state includes the previous fixed-step character and vehicle poses, tick and accumulator remainder. Rendering interpolates both actors with one 60 Hz tick of latency; duplicate packets at 120/240 Hz do not reset interpolation. Teleports and enter/exit transitions snap. The chase camera follows the same interpolated car pose and applies collision replies only on a later render frame.

The [CC0 autumn player](public/assets/character/README.md) uses a textured MakeHuman human with a green hoodie, jeans and shoes, retargeted offline to the existing UAL skeleton. KTX2 textures and meshopt are decoded by the player loader. The foot camera aims 1.25 m above the feet with a slight downward pitch, framing the 1.79 m body on both backends.

CC0 Quaternius UAL Standard is extended with `Walk_Formal_Loop` for the slower stair gait. The inspected Standard archive contains no stair up/down clips; stair direction states use procedural swing clearance plus IK. Walk/jog/sprint gait phases stay synchronized while blending. Source, archive hash and retained clips are recorded in `public/assets/LICENSES.md`.

```bash
# From the repository root; no real tiles, controller, network or API keys needed.
make game-test                         # synthetic unit tests, normally <2 s
make game-build                        # typecheck + production bundle
make game-e2e             # full Chrome suite, WebGPU + WebGL2, sandbox worker count
# Focused virtual-controller routes after building:
cd game
npx playwright test tests/e2e/gamepad.spec.ts
npx playwright test tests/e2e/motion.spec.ts --workers=2  # rotating camera, both backends
npm run dev
```

Source `../sandbox.env` from the repository root first. Dev uses `TURKU3D_PORT_BASE`; Playwright uses base +1. Playwright launches isolated temporary Chrome profiles unless a warm Chrome endpoint is supplied. The default viewport is 960×540; pixel-detail and budget checks set larger viewports explicitly. Every game e2e context emulates focus and replaces `navigator.getGamepads` before navigation, so tests never read a host controller; controller routes install a virtual Xbox. Real-tile specs reuse one HTTP-caching context per worker and create a fresh page per test. Run them against a sandbox build with `cd game && TILE_DIST="$TURKU3D_WORK/dist/tiles" ./node_modules/.bin/playwright test -c playwright.tile.config.ts` after `make game-build` and `make tile-build`. Unit tests cover radial curves, standard mappings, bindings/glyphs, rapid keyboard taps, focus/hot-plug/rumble, 120/240 Hz interpolation, two-bone IK, pelvis adjustment, 7/12/15 cm curbs, query treads, jump buffering and the 35 cm blocking boundary. Virtual-controller routes cover curb traversal, stair ascent/descent with measured ankle targets, camera, jumping, pause, disconnect/reconnect and enter/drive/reverse/exit. Stair screenshots are in `$TURKU3D_WORK/reports/screenshots/*-stair-ik.png`. Physical Elite vibration and subjective feel still need a hardware play session.

Browser checks tolerate a loaded host without weaker assertions. Waits that used a wall-clock timeout budget simulated time instead: `untilSim(page, predicate, arg, seconds)` in `tests/e2e/waits.ts` fails only after `seconds` of `__game.clock.simSeconds` pass without the predicate. `open(page, url)` (or `pace(page)` after a plain `goto`) samples up to 30 frames on the first page of a test and multiplies the test timeout by max(1, p95 frame ms / 16.7), capped at 20; it logs a `[pace]` line and attaches the factor. Loading waits that precede the game clock keep a wall-clock bound scaled by the same factor. Both configs set `retries: 1`, and the `loadRetry` fixture runs that retry only when the 1-minute host load was above 40 when the attempt failed; otherwise the retry fails at once with the measured load. Timed-out tests report `Test timeout of ... exceeded`; the later `Target page, context or browser has been closed` error is Playwright closing the page at the timeout, not a browser crash. With `TURKU3D_BROWSER_LOAD_LOG=<file>` each attempt appends a JSON row (test, retry, status, host load, frame p95) that the live build summarizes.

## URL parameters

| Param | Meaning |
|---|---|
| `backend=webgl` (or `webgl`) | Force the WebGL2 backend of `WebGPURenderer`. The default is WebGPU. |
| `scene=dev` | Explicitly load the synthetic development world. Without it, load Turku. |
| `tiles=<url>` | Override the scene tileset (also overrides `scene=dev`). Default: `../tiles/tileset.json`, resolving to sibling `tiles/` on Pages and `/tiles/` on local servers. |
| `manifest=<url>` | manifest.json. The default is `manifest.json` next to `tiles` (an absolute `tiles` URL on another origin, e.g. `make game-serve-dist` on :8081, keeps that origin). |
| `spawn=E,N[,heading]` | Spawn point in EPSG:3877 (default: the office, E 23459668 N 6704254, heading 0 = north). |
| `time=<ISO-8601>` | Sun and sky IBL (NOAA formula, Turku lat/lon). Default: `2026-10-15T14:00:00+03:00`; play mode also has a time slider. |
| `aa=off\|taa\|fxaa` | AA diagnostic; default `taa`. `off` and `fxaa` disable temporal jitter/history and AO temporal sampling; ID passes always bypass AA. |
| `quality=low\|medium\|high` | Render quality tier; medium is the default. All tiers include GTAO, TRAA, bloom and CSM. |
| `colRadius=<m>` | Collision streaming radius (default 200, must be > 0). Tiles unload beyond radius + 50 m. Candidate tiles come from the i/j grid window around the player, not a scan of the whole manifest. |
| `hud=0` | Start with the HUD hidden. |
| `cam=...` | Visual QA camera, see below. |

## Visual QA render contract (D8)

The game implements the contract in [qa/README.md §2](../qa/README.md) (the qa/ tooling is the reference; `qa/render/stub/index.html` is its reference page and `qa/qalib/camera.py` the camera maths).

`?cam=E,N,H,yaw,pitch,fov[&roll=R][&time=ISO][&qa=1][&pass=color|id][&backend=webgl][&tiles=...]`

- `E,N`: EPSG:3877 metres. `H`: absolute camera (eye) height, N2000.
- `yaw`: compass bearing, clockwise from north. `pitch`: positive looks up. `roll` (separate param; a 7th `cam` value is also accepted): positive = camera turned clockwise seen from behind.
- `fov`: **vertical** field of view in degrees (three.js `camera.fov`). Mapping: `camera.rotation.set(rad(pitch), -rad(yaw), -rad(roll), 'YXZ')`.
- `time`: ISO-8601 with offset (`+` URL-encoded as `%2B`). Sun azimuth/elevation, clear sky, frozen waves. Absent: `2026-10-15T14:00+03:00`.
- QA mode: no HUD, help, player, car or physics worker; pixel ratio 1 so the canvas is 1:1 with the viewport.
- Globals: `__qaReady` (tileset settled for 32 consecutive frames), `__qaError` (bad params, tile load error, camera outside the tileset footprint with nothing loaded after ~2 s, timeout after 120 s of frame time, each frame counting at most 0.1 s like the play loop's dt), `__qaPose` `{E,N,H,yaw,pitch,roll,fov}`, `__qaInfo` `{renderer, backend, pass, time, effectiveTime, quality, sseUnits, three, tiles[], tileset}`, `__qaProject(E,N,H)` → `[px,py]` CSS pixels or `null` behind the camera. `window.__game.qa` holds `{loadMs, visTris, models, sunElevation, sunAzimuth}`.
- `pass=id` (`src/render/idPass.ts`): every mesh unlit (`MeshBasicNodeMaterial`), no antialiasing, no tone mapping, linear output (no sRGB encode), no sky/fog/shadows; id = `R<<16|G<<8|B`, 0 = sky. Ids come from glTF node extras `{kind, id, tile, material}` (docs/specs/tiles.md §3.1); `__qaIdTable` maps id → entry.
- Driver: `node qa/render/render.mjs --views qa/views/views.jsonl --url "http://localhost:8080/" --out DIR`, or `make tile-qa` (serves `dist/` + the synced tiles and runs `qa/loop.py --no-judge`).
- Tested by `tests/e2e-tile/tile.spec.ts` on the pipeline stub tile (pose echo, canvas size, `__qaProject` centre and behind-camera, colour pass not blank, ID pass pixels all in the table) and by `tests/e2e/smoke.spec.ts` on the dev tiles (`__qaProject` against `qa/tests/golden_projection.json` incl. roll and vertical fov; out-of-tileset pose errors fast).

## Test hooks (play mode)

`window.__game` holds the following:
- `backend` and `three`
- `ready` (true after spawn)
- `state()`, which returns `{mode, feet[x,y,z], vel, grounded, groundMaterial, locomotion, contacts, visualFeet, cameraYaw, inputSource, paused, E, N, H, vehicle{pos,rot,linvel,speed,forwardSpeed,wheels,curbStrike,modelLoaded}, vehicles[], drivenId, camera{pos,fov,yaw}, collision{tiles,tris,colBytes,estWasmBytes}, visTris, visModels, tilesSettled, frameMs, errors}`. `vehicle` is the driven car, or the spawn car while walking; `vehicles` contains every car by ID.
- `clock` `{frames, simSeconds}` (both modes): rendered frames and simulated seconds, which are 60 Hz physics steps in play mode and frame time clamped to 0.1 s per frame in QA mode. Browser checks budget waits in simulated seconds (`untilSim` in `tests/e2e/waits.ts`), so a starved host runs them slower instead of failing them.
- `input` (named actions, `rebind`, `bindings`, `rumble`; e2e tests inject a virtual Gamepad or send real keyboard events)
- `physics` (the `PhysicsClient`)
- `tiles`

## Layout

```
src/main.ts                 entry: QA mode, or play mode's world (renderer, tiles, physics, spawn) + build badge
src/play.ts                 play session (input, UI, actors, missions, loop); hot-swapped on the localhost game
src/core/origin.ts          origin O, EN <-> engine, tile ids (mirror of pipeline/turku3d/config.py)
src/core/params.ts          URL parameters
src/audio/                  centralized Web Audio events, positional mix and CC0 sound bank (see its README)
src/input/                  named actions, Gamepad API, keyboard/mouse, bindings and Xbox glyphs
src/core/sun.ts             solar position
src/render/renderer.ts      WebGPURenderer(forceWebGL), AgX, PCF shadows
src/render/environment.ts   SkyMesh, PMREM sky IBL and street-scale cascaded sun shadows
src/render/post.ts          GTAO, TRAA, bloom and colour grading
src/render/materials.ts     material_key styling preserving GLB PBR texture bindings
src/render/water.ts         normal-map waves, Fresnel and planar reflection
src/render/performance.ts   per-frame CPU/GPU distributions and renderer counters
src/render/gpuTimer.ts      whole-frame WebGPU timestamp / WebGL2 disjoint queries
src/world/tiles.ts          TilesRenderer + GLTFExtensionsPlugin(KTX2Loader, MeshoptDecoder); group.rotation.x = -PI/2
src/world/manifest.ts       manifest.json loader + origin/grid assertions
src/world/collisionStreaming.ts   .col tiles within radius -> worker (transferable ArrayBuffer)
src/world/releaseSwap.ts    a new tile release behind /tiles applied in place (localhost game; see src/world/README.md)
src/physics/protocol.ts     worker message protocol (typed)
src/physics/worker.ts       the worker; src/physics/client.ts is the main-thread side
src/physics/world.ts        Rapier world: tile colliders, KCC, vehicle integration, camera sphere casts and raycasts
src/vehicle/               headless handling/curb queries + browser chase camera (see its README)
src/physics/colTile.ts      adapter over the shared reader src/shared/col (owned by the col-format workstream)
src/physics/triLocator.ts   hit point -> triangle -> material (Rapier featureId workaround, see below)
src/physics/constants.ts    character and car tuning
src/actors/                 player, foot IK, fixed-state interpolation, car visual, orbit camera
src/ui/hud.ts               debug HUD
scripts/make-dev-tiles.mjs  dummy tileset generator (also a minimal .col v1.0 RAW writer)
scripts/prepare-character.mjs   retargets the CC0 human/outfit to UAL and compresses the GLB
public/dev-tiles/           generated dummy tileset (small; committed so build/tests work offline)
public/assets/              CC0 character, see public/assets/LICENSES.md
public/basis/               basis transcoder (copied from three/examples/jsm/libs/basis)
tests/unit/                 vitest (Node), incl. validator-fixtures.test.ts (game KCC on tools/validator/fixtures)
tests/e2e/                  Playwright on the dev tiles (playwright.config.ts)
tests/e2e-tile/             Playwright on the pipeline stub tiles via serve-dist (playwright.tile.config.ts; `make tile-e2e`)
src/render/idPass.ts        QA object-ID pass
```

## Physics worker protocol (src/physics/protocol.ts)

All coordinates are engine world-local metres: `x = E − 23459500`, `y = H`, `z = −(N − 6704500)`. Each `.col` tile's float64 header origin becomes its collider translation, and vertices stay tile-local float32.

- The worker initialises itself on load and posts `ready {rapier, colReader}`. The client queues messages until then; a module worker can drop messages that arrive during its top-level `await`.
- `loadTile {id, buffer}` (the buffer is transferred) → `tileLoaded {stats}` or `tileError`. Triangles are split by collision class (solid, query, sensor) into one trimesh each. FIX_INTERNAL_EDGES is applied when the header flag says so. Primitives become cuboid, capsule or cylinder colliders.
- `unloadTile {id}` → `tileUnloaded`.
- `spawnCharacter`, `setCharacterEnabled`, `spawnVehicle`, `resetVehicle`.
- `step {seq, dt, character{vx,vz,jump}, vehicle{throttle,steer,brake,handbrake}|null}` → `state {character, previousCharacter, tick, remainder, vehicle, substeps, stepMs}`. The worker runs fixed 60 Hz substeps (at most 5), and the client keeps a single step in flight.
- `raycast {id, origin, dir, maxToi, filter: solid|query|all|foot}` → `raycastResult {hit{toi, point, normal, material, tile}}`.
- `stats {id}` → `statsResult {tiles[], tris, colBytes, estWasmBytes}`.

Character (KCC):
- Capsule r 0.3, half-height 0.6, skin 0.02.
- Autostep 0.22 m with min width 0.15, snap 0.3, max climb 45°, min slide 50°.
- **One combined `computeColliderMovement` per 60 Hz step** with a small constant vertical speed (−0.5 m/s) while grounded and full gravity only in the air (the tools/engine-probe scheme; the validator's default `--kcc-mode probe`). Measured with Rapier 0.21: climbs steps ≤ 0.27 m, blocked at ≥ 0.30 m at 1.5–5 m/s. Applying gravity as a separate second move climbs only ≤ 0.22 m at 1.5 m/s, so a 25 cm curb would block (Task 1.11c). `tests/unit/validator-fixtures.test.ts` walks the game KCC over the validator fixtures (12/25 cm climbed, 40 cm blocked).

Collision groups follow materials.md: STATIC_SOLID, STATIC_QUERY, STATIC_SENSOR, CHARACTER, VEHICLE, DYNAMIC.

The car is a Rapier `DynamicRayCastVehicleController` with a 4.2 × 0.6 × 1.8 m chassis of 1200 kg. Chassis +X is forward; the driver side is −Z.

## Status and known gaps

- The HUD shows backend, fps, frame/physics time, CPU/GPU/frame p95, drawn triangles/draw calls and resident visual triangles.
  Renderer counters are captured before their per-frame reset. Collision tiles, triangles and MB remain available; WASM
  memory is estimated at 135 B/tri. Player E/N/H is in EPSG:3877 + N2000. `window.__perf` exposes the measurements.
- Verified with the dev tileset in the e2e test: the 3D Tiles Z-up ↔ engine Y-up handling is correct. The NE marker at tile-local (240, ·, 10) ends up at engine x 240, z 10, so `tiles.group.rotation.x = -PI/2` together with the renderer's default glTF Y-up → Z-up rotation is right. This resolves the [U] in docs/specs/tiles.md §1.
- **Rapier 0.21 bug:** `castRayAndGetNormal(...).featureId` is always 0 for trimesh faces. A canary test covers this. Material lookup therefore uses `triLocator.ts`. `src/shared/col/rapierBuild.ts#hitMaterial` relies on `featureId`, so it returns the material of triangle 0.
- Character rendering interpolates 60 Hz physics states; car rendering still uses the latest state. On foot, camera collision uses an async worker raycast; driving uses a swept sphere and commits only the checked camera pose.
- There is no floating origin.
- The character's walk, jog and sprint clip speeds (1.5, 3.6, 6.0 m/s) were tuned by eye.

## Vehicle acceptance (W1-07)

```bash
# From the repository root.
make game-test game-vehicle-test game-build
cd game
npx playwright test tests/e2e/vehicle.spec.ts --workers=2
# Existing keyboard enter/drive/exit regression, also on both backends:
npx playwright test tests/e2e/smoke.spec.ts --grep 'enter the car' --workers=2
```

The small fixtures cover 7/12/15 cm curbs at 3/6/10 m/s, 26/30/40 cm blocks at 2 m/s with throttle held after impact,
a single-wheel roll, non-curb negative controls, analog controls, terminal speed, camera clearance and 30 m/s CCD
against a 3 cm building shell. The same core runs directly under Node 24 using the validator's Rapier compat 0.21;
Vite unit tests use the game's non-compat WASM. See [vehicle/README](src/vehicle/README.md) for the validator API.
Screenshots: `$TURKU3D_WORK/reports/screenshots/{webgpu,webgl}-vehicle-curb.png`.

## Sandbox outputs

From the repository root, `source ../sandbox.env` in a worker, then `make game-e2e`.
Direct `npm` commands use the same environment. Dev uses `TURKU3D_PORT_BASE`, preview/e2e use base +1
and fail if the port is occupied. The build lives in `$TURKU3D_WORK/build/game`, Vite cache in
`$TURKU3D_WORK/cache/vite`, screenshots and test results in `$TURKU3D_WORK/reports`.
Chrome creates unique profiles under `$TURKU3D_BROWSER_DIR/tmp`. See `scripts/dev/README.md`.

Keyboard e2e routes mask physical gamepads in their browser contexts; virtual Xbox routes inject their own controller. This prevents connected host devices from steering concurrent keyboard tests.

## Selective quality checks

From the repository root, run `scripts/dev/gate --base HEAD` for local changes or
`scripts/dev/gate --all` for the complete offline suite. See the [quality gate guide](../scripts/dev/README.md)
for dependency setup, path selection, caches, sandbox ports and Git hooks.

Regenerate the CC0 sedan asset with `cd game && npm run prepare-car`.

### Collision streaming recovery

Transient network failures, HTTP 408/429 and server errors retry after 0.5, 1, 2, … seconds,
capped at 30 seconds while the tile remains nearby. Manifest-size and `.col` format/CRC
failures are permanent and surface through the game's error callback (`__qaError`).
CRC checking runs in the physics worker. Failed steps report their sequence, release the
client's in-flight slot and surface the error; the worker message queue continues.
Verify with `make game-test` (synthetic streaming, client and worker failure tests).

Gate browser tests use four workers with file-level parallelism and the shared 120-second
functional deadline. Gameplay contexts emulate focus so unrelated Chrome windows do not clear
input. The analog-speed regression captures a frame-local sample with its original speed bounds;
GPU/frame throughput budgets remain in `make render-perf`. `npm run test:all` shares one production
build between unit and browser suites. Use `scripts/dev/gate --paths game/src/physics/worker.ts`
from the repository root to run the walk/drive subset with its synthetic pipeline tile.

Ground texture material API and isolated Chrome QA: [src/render/ground/README.md](src/render/ground/README.md).
From the repo root, source the sandbox environment and run `make ground-render-test` (port 5310
by default; override `GROUND_QA_PORT` outside the W1-10 sandbox).

Public live/Pages builds set `VITE_PUBLIC_RELEASE=1` at build time. Their start menu offers Multiplayer only; local dev and ordinary `npm run build` keep all start options. This is a build target, not a runtime URL option.
