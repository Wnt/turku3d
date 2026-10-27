# Vehicle (W1-07)

The physics core has no browser/renderer imports and uses erasable TypeScript, so Node 24 imports it directly.
The constructor accepts the Rapier namespace: the browser uses `@dimforge/rapier3d`, the validator uses
`@dimforge/rapier3d-compat` (both 0.21). Import `vehicle.ts` and `curbQueries.ts`; `chaseCamera.ts` alone needs three.js.

The physics worker now keeps vehicles by ID. The spawn car and every parked car use the same
`Vehicle` controller and enter/drive/exit path. Parked cars start asleep, wake on contact or
entry, and skip wheel updates while asleep. Parked glTF model/paint variants are rendered as
instances until takeover. Entering one removes its static instance and adds a shadow-casting
mesh directly to the actor scene. That mesh follows the rigid body after exit; remaining
parked instances refresh their culling bounds as physics moves them. `F` or Xbox `Y` enters
the closest car within 4 m.

Run `npm test --prefix game -- --run tests/unit/vehicle-fleet.test.ts` for the 20 m
physics route and `(cd game && npx playwright test tests/e2e/parked-vehicle.spec.ts
--workers=2)` for the Xbox takeover, 60 m culling and exit route on WebGPU and WebGL2.

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

## Scooter (two-wheeler)

`new Vehicle(R, world, pos, yaw, surfaces, { kind: 'scooter' })` uses the `SCOOTER` tuning instead of `CAR`
(`physics/constants.ts`, both `VehicleTuning`; `VEHICLES[kind]`). Every wheel loop, the steered/driven wheel
sets, the handling limits and the curb wheel casts (`castWheel(..., radius)`) read the tuning, so the car is
unchanged. `VehicleSpec.kind` and `VehicleState.kind` carry the class through the worker protocol.

| | Scooter | Car |
|---|---|---|
| Mass (rider included) | 190 kg, centre of mass 0.35 m below the chassis box centre | 1200 kg |
| Chassis box | 2.04 × 0.6 × 0.6 m | 4.2 × 0.6 × 1.8 m |
| Wheels | front (steered) and rear (driven) on the centre line, 1.25 m wheelbase, r 0.24 m; two outrigger wheels 0.36 m either side, 0.62 m ahead of the rear wheel | four, r 0.33 m |
| Top speed | 12.5 m/s = 45 km/h (engine limit 13.8 m/s against drag; measured 12.49 m/s) | about 25 m/s |
| Acceleration | 600 N on the rear wheel: 0–30 km/h in 2.8 s | 2 × 3200 N |
| Braking | 12.5 m/s to rest in 11.5 m (about 6.8 m/s²) | |
| Steering | 0.70 rad lock, half at 6 m/s, 3.2 rad/s rate; full-lock radius about 1.7 m at walking pace | 0.55 rad, half at 10 m/s |
| Reverse | 2 m/s (pushing back) | 8 m/s |

The outrigger wheels are invisible and keep the ray-cast controller upright; together with the lowered
centre of mass the chassis stays within 3° of upright in a full-lock turn at top speed. There is no lean
simulation: `lean.ts` `scooterLean(speed, steer)` gives the cosmetic lean that balances a kinematic bicycle's
lateral acceleration, capped at 15°, and `actors/scooter.ts` rolls the model and the rider about the tyre
contact line with it. Curbs: a 12 cm kerb at 4 m/s is felt (strike event, rumble, pitch) and passed; a 30 cm
step stops the scooter (the barrier rule below 3 m/s, otherwise the chassis box).

Run `npx vitest run tests/unit/scooter.test.ts` (parameters, lean, top speed/acceleration/braking/roll, turning
radius against the car, kerb and step) and `(cd game && npx playwright test tests/e2e/scooter.spec.ts --workers=2)`
for the purchase, kerb, 45 km/h, lean and dismount route on WebGPU and WebGL2.

## Trophy truck (prerunner)

`{ kind: 'truck' }` uses the `TRUCK` tuning. The suspension parameters that used to be fixed for every class
(compression and relaxation damping, travel) are `VehicleTuning` fields now, beside `frontTorque` (engine share on the
driven steered wheels; the rest on the driven rear wheels) and `curbFeel` (scale of the curb strike's speed loss and
lift impulse). Car and scooter keep their former values (4.8 / 5.5 damping, 0.22 m travel, rear drive, feel 1).
The handbrake acts on the driven rear wheels only.

| | Truck | Car |
|---|---|---|
| Mass | 2400 kg, centre of mass 0.35 m below the chassis box centre | 1200 kg |
| Chassis box | 4.7 × 0.8 × 2.0 m; centre 0.95 m above the ground at rest (car 0.72 m) | 4.2 × 0.6 × 1.8 m |
| Wheels | four, r 0.46 m, wheelbase 2.9 m, track 1.9 m; 4WD, 40 % of the engine force on the front axle | four, r 0.33 m, rear drive |
| Suspension | rest 0.45 m, ±0.25 m travel, stiffness 20 (sag 12 cm, 1.4 Hz), damping 4.2 / 5.6 (ratio 0.94 / 1.25), max 48 kN per wheel | rest 0.3 m, ±0.22 m, stiffness 38 (sag 6 cm, 2 Hz), damping ratio 0.78 / 0.89 |
| Top speed | 120 km/h (engine limit 37.5 m/s against drag; measured 120.4 km/h) | about 88 km/h measured |
| Acceleration | 4 × 3700 N: 0–100 km/h in 6.0 s, 120 km/h after about 10 s | 2 × 3200 N |
| Braking | 120 km/h to rest in 33 m | 88 km/h in 19 m |
| Steering | 0.5 rad lock, half at 12 m/s, 2.2 rad/s rate | 0.55 rad, half at 10 m/s, 2.5 rad/s |
| Kerb (12 cm at 40 km/h, cruising throttle) | `curbFeel` 0.2: 2.6 % slower than the same run on the flat, 1.9° pitch, speed held | 16.9 %, 9.5° |
| Kicker (1 m high, 4 m long, 12 m/s) | 0.9 s airborne, lands on its wheels with the chassis 0.38 m clear, no chassis impact | |

The chase camera frames the truck from 1.5 m farther back and looks at a point 1.5 m above the chassis centre
(car and scooter 0.9 m); field of view and its speed widening are the same (`chaseSettings(speed, kind)`).
Run `npx vitest run tests/unit/truck.test.ts` (tuning, torque split and handbrake, ride height, acceleration, top speed,
braking, full-lock roll, kerb against car, kicker jump, camera framing).

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
