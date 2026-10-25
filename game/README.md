# game/

Game skeleton (ARCHITECTURE.md Phase 0 step 7). It uses Vite 8, TypeScript, three.js 0.186.1 (`three/webgpu` and TSL) with a WebGPU / WebGL2 switch, and Rapier 0.21 (`@dimforge/rapier3d`, non-compat build through `vite-plugin-wasm`) in a physics Web Worker. Visual tiles stream through 3DTilesRendererJS 0.5.3 with KTX2 and meshopt. The player is a third-person CC0 character, and there is a raycast car.

## Commands

```bash
cd /Users/wnt/Documents/game-test/game
npm install
npm run dev            # http://localhost:5173/  (COOP/COEP on; ?backend=webgl for WebGL2)
npm run build          # tsc --noEmit + vite build -> dist/
npm test               # vitest (Node): .col reader, Rapier character/car on the dev tiles, origin, URL params, sun
npm run test:e2e       # build + Playwright, Chrome channel (real Metal GPU), both backends; screenshots in test-results/screenshots/
npm run dev-tiles      # regenerate public/dev-tiles/ (dummy tileset)
TILE_DIST=~/turku3d-data/dist/tiles npx playwright test -c playwright.tile.config.ts   # after npm run build: pipeline stub tiles (make tile)
npm run probe -- "http://localhost:5173/?backend=webgl" out.png 6000   # debug: console, HUD and screenshot of any URL
```
The same commands are available from the repo root through `mk/game.mk`: `make game-dev`, `make game-build`, `make game-test`, `make game-e2e`, `make game-dev-tiles`, `make game-preview`, `make game-sync`, `make game-serve-dist` and `make game-test-all`.

## Controls

Click the canvas for mouse look (pointer lock); the mouse wheel zooms. **WASD** or the arrow keys move. **Shift** sprints (6 m/s), **Alt** walks (1.5 m/s), and the default is a run at 3.6 m/s. **Space** jumps, or applies the handbrake in the car. **F** enters or exits the car (within 4 m of it). **R** resets the car upright. **H** toggles the HUD.

## URL parameters

| Param | Meaning |
|---|---|
| `backend=webgl` (or `webgl`) | Force the WebGL2 backend of `WebGPURenderer`. The default is WebGPU. |
| `tiles=<url>` | tileset.json to load. The default is `dev-tiles/tileset.json`. For real tiles use e.g. `tiles=/tiles/tileset.json` with `make game-preview`. |
| `manifest=<url>` | manifest.json. The default is `manifest.json` next to `tiles`. |
| `spawn=E,N[,heading]` | Spawn point in EPSG:3877 (default: the office, E 23459668 N 6704254, heading 0 = north). |
| `time=<ISO-8601>` | Sun position (NOAA formula, Turku lat/lon). The default is the fixed `2026-06-21T14:00:00+03:00`, so screenshots are deterministic. |
| `colRadius=<m>` | Collision streaming radius (default 200). Tiles unload beyond radius + 50 m. |
| `hud=0` | Start with the HUD hidden. |
| `cam=...` | Visual QA camera, see below. |

## Visual QA render contract (D8)

The game implements the contract in [qa/README.md §2](../qa/README.md) (the qa/ tooling is the reference; `qa/render/stub/index.html` is its reference page and `qa/qalib/camera.py` the camera maths).

`?cam=E,N,H,yaw,pitch,fov[&roll=R][&time=ISO][&qa=1][&pass=color|id][&backend=webgl][&tiles=...]`

- `E,N`: EPSG:3877 metres. `H`: absolute camera (eye) height, N2000.
- `yaw`: compass bearing, clockwise from north. `pitch`: positive looks up. `roll` (separate param; a 7th `cam` value is also accepted): positive = camera turned clockwise seen from behind.
- `fov`: **vertical** field of view in degrees (three.js `camera.fov`). Mapping: `camera.rotation.set(rad(pitch), -rad(yaw), -rad(roll), 'YXZ')`.
- `time`: ISO-8601 with offset (`+` URL-encoded as `%2B`). Sun azimuth/elevation, frozen clouds. Absent: the fixed default `2026-06-21T14:00+03:00`.
- QA mode: no HUD, help, player, car or physics worker; pixel ratio 1 so the canvas is 1:1 with the viewport.
- Globals: `__qaReady` (tileset settled for 10 consecutive frames), `__qaError` (bad params, tile load error, 120 s timeout), `__qaPose` `{E,N,H,yaw,pitch,roll,fov}`, `__qaInfo` `{renderer, backend, pass, time, three, tiles[], tileset}`, `__qaProject(E,N,H)` → `[px,py]` CSS pixels or `null` behind the camera. `window.__game.qa` holds `{loadMs, visTris, models, sunElevation, sunAzimuth}`.
- `pass=id` (`src/render/idPass.ts`): every mesh unlit (`MeshBasicNodeMaterial`), no antialiasing, no tone mapping, linear output (no sRGB encode), no sky/fog/shadows; id = `R<<16|G<<8|B`, 0 = sky. Ids come from glTF node extras `{kind, id, tile, material}` (docs/specs/tiles.md §3.1); `__qaIdTable` maps id → entry.
- Driver: `node qa/render/render.mjs --views qa/views/views.jsonl --url "http://localhost:8080/?tiles=/tiles/tileset.json" --out DIR`, or `make tile-qa` (serves `dist/` + the synced tiles and runs `qa/loop.py --no-judge`).
- Tested by `tests/e2e-tile/tile.spec.ts` on the pipeline stub tile (pose echo, canvas size, `__qaProject` centre and behind-camera, ID pass pixels all in the table) and by `tests/e2e/smoke.spec.ts` on the dev tiles.

## Test hooks (play mode)

`window.__game` holds the following:
- `backend` and `three`
- `ready` (true after spawn)
- `state()`, which returns `{mode, feet[x,y,z], vel, grounded, groundMaterial, E, N, H, vehicle{pos,speed}, collision{tiles,tris,colBytes,estWasmBytes}, visTris, visModels, tilesSettled, frameMs, errors}`
- `input` (`input.forced.add('KeyW')` holds a key without real events)
- `physics` (the `PhysicsClient`)
- `tiles`

## Layout

```
src/main.ts                 entry: QA mode or play mode (spawn, loop, enter/exit car)
src/core/origin.ts          origin O, EN <-> engine, tile ids (mirror of pipeline/turku3d/config.py)
src/core/params.ts          URL parameters
src/core/input.ts           keyboard / mouse
src/core/sun.ts             solar position
src/render/renderer.ts      WebGPURenderer(forceWebGL), AgX, PCF shadows
src/render/environment.ts   SkyMesh, sun light following the player, hemisphere light
src/world/tiles.ts          TilesRenderer + GLTFExtensionsPlugin(KTX2Loader, MeshoptDecoder); group.rotation.x = -PI/2
src/world/manifest.ts       manifest.json loader + origin/grid assertions
src/world/collisionStreaming.ts   .col tiles within radius -> worker (transferable ArrayBuffer)
src/physics/protocol.ts     worker message protocol (typed)
src/physics/worker.ts       the worker; src/physics/client.ts is the main-thread side
src/physics/world.ts        Rapier world: tile colliders per collision class, KCC, raycast vehicle, raycasts
src/physics/colTile.ts      adapter over the shared reader src/shared/col (owned by the col-format workstream)
src/physics/triLocator.ts   hit point -> triangle -> material (Rapier featureId workaround, see below)
src/physics/constants.ts    character and car tuning
src/actors/                 player (animated mannequin), car visual, orbit camera
src/ui/hud.ts               debug HUD
scripts/make-dev-tiles.mjs  dummy tileset generator (also a minimal .col v1.0 RAW writer)
scripts/prepare-character.mjs   trims the Quaternius UAL GLB
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
- `step {seq, dt, character{vx,vz,jump}, vehicle{throttle,steer,brake,handbrake}|null}` → `state {character, vehicle, substeps, stepMs}`. The worker runs fixed 60 Hz substeps (at most 5), and the client keeps a single step in flight.
- `raycast {id, origin, dir, maxToi, filter: solid|query|all}` → `raycastResult {hit{toi, point, normal, material, tile}}`.
- `stats {id}` → `statsResult {tiles[], tris, colBytes, estWasmBytes}`.

Character (KCC):
- Capsule r 0.3, half-height 0.6, skin 0.02.
- Autostep 0.22 m with min width 0.15, snap 0.3, max climb 45°, min slide 50°.
- **One combined `computeColliderMovement` per 60 Hz step** with a small constant vertical speed (−0.5 m/s) while grounded and full gravity only in the air (the tools/engine-probe scheme; the validator's default `--kcc-mode probe`). Measured with Rapier 0.21: climbs steps ≤ 0.27 m, blocked at ≥ 0.30 m at 1.5–5 m/s. Applying gravity as a separate second move climbs only ≤ 0.22 m at 1.5 m/s, so a 25 cm curb would block (Task 1.11c). `tests/unit/validator-fixtures.test.ts` walks the game KCC over the validator fixtures (12/25 cm climbed, 40 cm blocked).

Collision groups follow materials.md: STATIC_SOLID, STATIC_QUERY, STATIC_SENSOR, CHARACTER, VEHICLE, DYNAMIC.

The car is a Rapier `DynamicRayCastVehicleController` with a 4.2 × 0.6 × 1.8 m chassis of 1200 kg. Chassis +X is forward; the driver side is −Z.

## Status and known gaps

- The HUD shows backend, fps and ms/frame, physics ms per step message, and resident visual tris (counted on `load-model`/`dispose-model`, because `renderer.info` reads 0 on WebGPU). It also shows collision tiles, tris and MB, where the WASM figure is an estimate at 135 B/tri from engine.md. Player E/N/H is in EPSG:3877 + N2000.
- Verified with the dev tileset in the e2e test: the 3D Tiles Z-up ↔ engine Y-up handling is correct. The NE marker at tile-local (240, ·, 10) ends up at engine x 240, z 10, so `tiles.group.rotation.x = -PI/2` together with the renderer's default glTF Y-up → Z-up rotation is right. This resolves the [U] in docs/specs/tiles.md §1.
- **Rapier 0.21 bug:** `castRayAndGetNormal(...).featureId` is always 0 for trimesh faces. A canary test covers this. Material lookup therefore uses `triLocator.ts`. `src/shared/col/rapierBuild.ts#hitMaterial` relies on `featureId`, so it returns the material of triangle 0.
- No interpolation between 60 Hz physics states, so there can be judder on 120 Hz displays. Camera collision uses an async worker raycast whose result applies one frame late.
- There is no curb-strike module yet (Task 1.14), no stair animation or foot IK, no CSM, GTAO or TRAA, and no floating origin.
- The character's walk, jog and sprint clip speeds (1.5, 3.6, 6.0 m/s) were tuned by eye.
