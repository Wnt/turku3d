# Vehicle (W1-07)

The physics core has no browser/renderer imports and uses erasable TypeScript, so Node 24 imports it directly.
The constructor accepts the Rapier namespace: the browser uses `@dimforge/rapier3d`, the validator uses
`@dimforge/rapier3d-compat` (both 0.21). Import `vehicle.ts` and `curbQueries.ts`; `chaseCamera.ts` alone needs three.js.

```ts
import { Vehicle } from './game/src/vehicle/vehicle.ts';
import { CurbQueries } from './game/src/vehicle/curbQueries.ts';
// R: initialized Rapier namespace; world: existing Rapier.World.
const surfaces = new CurbQueries(R, world);
// For each curb/quay primitive or material-selected curb trimesh:
const helper = surfaces.add(sourceCollider, curbShape, point => ({ material, top: faceTopAt(point) }));
const car = new Vehicle(R, world, { x: 0, y: 0.8, z: 0 }, 0, surfaces);
car.step(1 / 60, { throttle: 0.5, brake: 0, steer: 0, handbrake: false });
world.step();
const state = car.state();
// On tile unload: surfaces.remove(helper.handle); world.removeCollider(helper, false).
```

`PhysicsWorld.loadTile` and the Node tile validator share the `tileCurbs.ts` adapter
for material-selected meshes and rotated primitives. Tile unload unregisters these surfaces. It preserves the solid trimesh topology
needed by KCC, then creates a compact curb submesh as a non-interacting sensor (collision and solver groups 0).
The helper caches Rapier's BVH. Wheel queries first use the world broad phase, then pairwise sphere casts against
only registered curb helpers. Helpers never enter solid queries, foot-IK helpers or physical contacts. Their triangles
are included in the collision memory estimate. Never pass Rapier trimesh `featureId` to material lookup: 0.21 returns
0; the adapter uses the witness point and `TriLocator`. Both material `curb`/`quay` and `MAT_FLAG.CURB` are supported;
`curb_dropped` is deliberately excluded. Face height supplies the upper surface; a ray just before the face measures
the lower road, independently of chassis pitch or suspension travel.

Each approaching wheel loses 1.6–5% horizontal speed once per contact, with a vertical impulse at the wheel producing
pitch/roll. Impulse is proportional to speed × height up to a saturation limit that prevents launching the car.
Contact suppression releases after 0.9 m of chassis travel. Steps above 0.25 m stop inward motion below 3 m/s;
the barrier is applied after engine forces each tick. Chassis CCD handles building walls at high speed.
`state.curbStrike` carries a monotonically increasing event id, wheel, measured height/speed, loss, blocked status
and rumble strength. The render loop sends each new event to W1-06's `Input.rumble` once.

Handling uses rear drive, analog RT/LT pedals, brake-then-reverse, speed-sensitive steering with a steering rate limit,
progressive tyre grip and rear-only handbrake slip. Top speed is limited by engine torque taper plus quadratic drag.
The chase camera grows from 6.5 to 10.1 m and from 60° to 73.5° vertical FOV; right-stick orbit recentres after 1.2 s
without look input while moving. A 0.3 m sphere sweep in the physics worker protects its near plane, and only a swept
pose is committed to the camera. Reset is R3/C. Leaving the car cancels pending camera results.

## Verification

From the repo root:

```bash
make game-test game-vehicle-test game-build
# Source your sandbox.env before running these commands.
cd game
npx playwright test tests/e2e/vehicle.spec.ts --workers=2
```

`game-vehicle-test` runs the same small fixtures on game WASM and native Node/validator WASM. The reusable fixture
factory, `CURB_SCENARIOS` and `runCurbScenario` live in `game/tests/fixtures/vehicle.ts`. Native acceptance is
`game/tests/headless/vehicle.test.ts`. No real tiles or network access are needed after dependency installation.
The `.col` integration test additionally drives over the committed development tile and checks helper unload.

Measured on the 9 low-curb scenarios: peak pitch 2.43–8.77°, horizontal speed loss 9.13–18.28%. Loss compares the tick
before the first wheel strike to the tick after the last strike, coasting with no throttle; it includes drag across
the wheelbase. The 26/30/40 cm fixtures at 2 m/s remain blocked with throttle held, and a 30 m/s vehicle stops before
a 3 cm thick building shell. Browser acceptance injects a standard virtual Xbox pad on WebGPU and WebGL2 and checks
entry, analog driving, curb events, wheel motion, rumble, speed-dependent FOV, orbit, braking and exit.

Input comes directly from W1-06's landed `src/input/` module. `main.ts` passes the named
`{throttle, brake, steer, handbrake}` actions unchanged to the vehicle; its brake/reverse and steering conventions
belong to `handling.ts`. Character interpolation and foot IK remain supplied by W1-06.

The tile validator now enforces the same ARCHITECTURE 1.14 limits through its own
scripted routes. Its nine low-curb scenarios measure 2.48–8.77° pitch and
7.32–18.26% loss; 26/30/40 cm steps block at 2 m/s. It also checks decoded `.col`
curb faces through the shared adapter, and catches deliberately absent registrations.
Run `node --test tools/validator/test/acceptance.test.ts` from the repository root.

## Audio collision telemetry

Call `vehicle.afterStep(dt)` after `world.step()` to collect solver contact impulses (the physics worker
already does so). `state().impact` is null until a collision exceeds 600 N·s, then retains the latest
`{id, impulse}` for consumers to deduplicate. Reports are limited to one per 150 ms and reset clears
the retained event. The audio adapter uses this value for positional impact loudness.
