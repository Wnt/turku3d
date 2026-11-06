# Vehicle (W1-07)

The physics core has no browser/renderer imports and uses erasable TypeScript, so Node 24 imports it directly.
The constructor accepts the Rapier namespace: the browser uses `@dimforge/rapier3d`, the validator uses
`@dimforge/rapier3d-compat` (both 0.21). Import `vehicle.ts` and `curbQueries.ts`; `chaseCamera.ts` alone needs three.js.

The physics worker now keeps vehicles by ID. The spawn car and every parked car use the same
`Vehicle` controller and enter/drive/exit path. Parked cars start asleep, wake on contact or
entry, and skip wheel updates while asleep. Only the nearest `QUALITY[tier].parkedBodies`
draft cars within 120 m (mobile 24, low 40, medium 60, high 80) have bodies
(`world/parkedVehicles.ts`); a closer car takes a body only when it is 15% and 4 m nearer,
rolling cars keep theirs, and bodies leave beyond 170 m. Cars without a body are drawn at
their last physical pose or draft slot, tilted with the ground as the generator measured it
(`pitch`, `roll`); claimed multiplayer slots are left to the shared renderer. A slot holds its car
only while its generator draw (`occ`) is below its class's occupancy at the shared clock's night
level (`world/parkedOccupancy.ts`: the centre empties at night, residential streets fill up), so
every client shows the same cars; an emptied slot loses its visual and, once at rest, its body. In single
player the night level follows this browser's view clock instead (`?time=`, the developer time panel), so a
scrubbed or fast-run time shows its own parked cars.
Each recolourable Kenney body model is one InstancedMesh: a per-instance paint redraws only the
generator's baked paint swatch (`actors/parkedPaint.ts`), so glass, tyres and lamps keep the GLB
texture. Other models keep one batch per GLB. Entering a car removes its instance and adds a
shadow-casting mesh with the same material and its paint as an object uniform. That mesh follows
the rigid body after exit; remaining parked instances refresh their culling bounds as physics
moves them. `F` or Xbox `Y` enters the closest car within 4 m.

Run `npm test --prefix game -- --run tests/unit/vehicle-fleet.test.ts` for the 20 m
physics route, `tests/unit/parked-cap.test.ts tests/unit/parked-paint.test.ts` for the
body cap and the paint batches (real r186 CPU shader builder: one program per pass for any
count or paint), and `(cd game && npx playwright test tests/e2e/parked-vehicle.spec.ts
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
and rumble strength. `input/haptics.ts` turns each new event into one rumble (input/README.md "Haptics").

Handling uses rear drive, analog RT/LT pedals, brake-then-reverse, speed-sensitive steering with a steering rate limit,
progressive tyre grip and rear-only handbrake slip. Top speed is limited by engine torque taper plus quadratic drag.
The chase camera grows from 6.5 to 10.1 m and from 60° to 73.5° vertical FOV; right-stick orbit recentres after 1.2 s
without look input while moving. A 0.3 m sphere sweep in the physics worker protects its near plane, and only a swept
pose is committed to the camera. Reset is R3/C. Leaving the car cancels pending camera results.

## Tyres and handling (slip-curve cars)

The starter car (`modelTuning(PLAYER_MODEL)`, `tuning.tyre`) drives on slip-curve tyres calibrated on Wreckfest 2's
Bravion (`~/turku3d/research/wreckfest2/physics-study.md`, "Turku3D tuning targets"); every other vehicle keeps Rapier's
ray-cast tyre until the fleet slice. Rapier still runs the suspension (spring, dampers, contact point, wheel load);
for a slip-curve car its side friction stiffness, engine force and brake are zero and `tyres.ts solveTyres` applies
the tyre forces after the raycast, as impulses on the chassis:

- **Lateral**: μ(α) = peak · (1 − e^(−α/α0)) per axle, front 1.16 / 3.45°, rear 1.09 / 2.7°, no fall-off to 25°;
  load sensitivity μ × (Fz / 1.75 F_static)^−0.12 (≈1.35 at 0.6 × static load, ≈1.17 at 2 ×); the slip angle relaxes
  over 1 m of rolling (at most 0.1 s), which gives the yaw response its Wreckfest-like lag and overshoot.
- **Longitudinal**: no wheel-spin DOF. Drive is the engine force up to the tyre's peak (μ 1.35; traction control
  `traction: 1` keeps the wheels from spinning, without it a wheel spins at μ 1.31); brakes bound a constraint that
  stops the patch rolling, capped at 95 % of the peak (`brakeShare`: an ideal anti-lock, so no wheel locks). The rear
  brakes give way to the rear tyres' side force (cornering brake control), so braking in a turn never takes the tail
  away; the handbrake locks the rear wheels (sliding friction at 0.75 × μ 1.31 against the patch velocity).
- **Combined**: when longitudinal use and side use exceed the friction ellipse, both shrink in proportion.
- **Solver**: projected Gauss-Seidel over the four patches, each a velocity constraint bounded by its curve force. At
  road speed the bound binds (force = μ(α, Fz)·Fz); at walking pace the constraint binds and the tyre holds like
  static friction. Warm started; the coming world step's gravity is solved against (no creep on slopes beyond
  Rapier's own sink artefact). All forces act at the patches (`rollCentre` 0): springs and anti-roll bars carry the
  whole lateral load transfer.
- **Traction and anti-lock follow the slip angle**: a tyre's force follows its slip direction, so at α = 2 α0 a wheel
  held at its peak slip ratio gives 71 % of the longitudinal peak and at 25° little (`COMBINED`); at full lock under
  power the front no longer pushes the nose round with drive force.
- **Ride** (`tuning.ride`, `ride.ts`): springs and dampers from 1.75 / 1.5 Hz and damping ratios 0.45 / 0.41 bump,
  0.59 / 0.5 rebound at each axle's static corner load; the rest length grows by the softer spring's extra sag, so
  the static ride height (and the drawn stance) stays the base tuning's. Anti-roll bars add what the springs lack for
  3.6°/g of roll (counting the weight's own moment as the body rolls) with 64 % of the roll stiffness on the front
  (Wreckfest's own figure; its measured 52 % of the transfer includes roll-centre geometry this model lacks, and 52 %
  here let the tail go at the limit); a bar's force pair acts at the wheel mounts and moves that load between the
  axle's tyres. Centre of mass 0.52 m (`comDrop` 0.2).
- **Brakes** (`tuning.brakes`): the pressure builds over 0.26 s to the pedal's share (and releases as fast) with
  60 % of the force on the front axle; anti-lock caps each wheel at 95 % of its peak, which it reaches at about two
  thirds pressure, so the deceleration reaches 90 % in 0.15-0.18 s (Wreckfest 2: 0.16-0.19 s).
- **Stability control** (`tuning.stability`, Wreckfest's "half" assist): once the rear axle's relaxed slip angle
  exceeds the front's by 2°, it cuts the drive and brakes the outside front wheel, up to 60 % at 8°.
- **Car data** (`modelTuning`): steering lock barely falls with speed (`steerHalfSpeed` 40: 25° at 60 km/h), no body
  angular damping (the tyres damp the yaw; `angularDamping` absent stays 1.2), a 70 % load-split limited-slip
  differential (`differential`: an open diff spins the unloaded inner wheel and loses its side grip), keyboard
  steering slews at 1.55 of full input per second, 3.1 back to centre (`keySteer`, `VehicleInput.steerKey` from
  play.ts: about 46°/s at the road wheel as Wreckfest's keyboard).
- **Damage** plugs in unchanged: the toe and steering pull are in Rapier's wheel steering, sag and damping in the
  suspension, power loss in the engine force; the tyre layer reads them from there.
- **Diagnostics**: `Vehicle.tyreForces` (per-wheel N along heading and axle); `state().wheels[].tyre.lateral` is the
  patch's side velocity as before, `longitudinal` the tyre's sliding estimate.

Headless harness (`tests/unit/wf2-compare.harness.test.ts`, the same `PhysicsWorld.fixedStep` path as the worker;
`WF2_COMPARE=1 WF2_REPORT=out.md npx vitest run tests/unit/wf2-compare.harness.test.ts` in `game/`): skidpads R 20/30,
full stick, braking 50/80/100, step steer 2/4/8°, full stick and a held key at 60 km/h, ride, and drivability checks
(slalom 40-70 km/h, lift-off, braking and full throttle in a corner, handbrake turn, parked on a slope).
Starter Golf before → slice 1 (tyres, steering) → slice 2 (ride, CoM, bars, stability control), Wreckfest 2 Bravion:

| Metric | Before | Slice 1 | Slice 2 | Wreckfest 2 |
|---|---|---|---|---|
| Max steady lateral g, R 20 / R 30 | 1.41 / 1.48 (lock-limited) | 0.98 / 0.94 (grip) | 1.00 / 0.95 (grip; 1.06-1.07 in the lock steps) | 1.07 / 1.06 (grip) |
| Understeer gradient < 0.5 g | 0.08 / 0.05 deg/g | 0.96 / 0.85 | 0.91 / 0.74 | 0.9 |
| Slip angles at 0.63 g, front / rear | 0.14 / −0.06° | 1.95 / 1.52° | 2.0 / 1.6° | 2.8 / 2.1° |
| Road-wheel lock at 60 km/h | 7.8° | 25.0° | 25.0° | about 25° (keyboard) |
| Yaw rise 10-90 %, overshoot (2-8° steps) | 0.03-0.12 s, 0-6 % | 0.13-0.20 s, 5-9 % | 0.12-0.18 s, 6-9 % | 0.16-0.26 s, 7-13 % |
| Held key at 60 km/h: steer to 90 %, slips | 0.05 s | 0.57 s, 30 / 22° | 0.57 s, 19 / 5° | 0.50 s, 25 / 9° |
| Ride frequency front / rear, damping | 1.92 / 2.00 Hz, 0.76-0.91 | unchanged | 1.74 / 1.45 Hz, 0.48 / 0.40 | 1.76 / 1.48 Hz, 0.41-0.59 |
| CoM height | 0.723 m | 0.723 m | 0.521 m | 0.52 m |
| Roll gradient | 3.18 deg/g | 3.33 | 3.66 | 3.65 |
| Braking 80 km/h: mean, front share, pitch | 1.40 g, 90 %, 2.1° | 1.22 g, 85 %, 1.9° | 1.31 g, 78 %, 2.3° | 1.34 g, 75 %, 2.6° |
| Slice 3 braking 50 / 80 / 100 km/h: mean decel | | | 1.37 / 1.41 / 1.45 g (tyres about 1.29 g; the rest is the game's 10 × aero drag) | 1.31 / 1.34 / 1.34 g |
| Slice 3: to 90 % of the deceleration, slip ratio, stop 100-0 | 0.017 s, no wheel slip | | 0.15-0.18 s, 0.11, 27.8 m | 0.16-0.19 s, 0.1-0.2, 31.9 m |
| Slalom 18 m | (not run) | clean to 70 km/h | clean to 60, the tail goes at 70 | clean to 60, rear out at 68 |
| Brake 0.5 in a 0.79 g turn, steering held | (not run) | 17° sideslip | 3.8° | |

In the browser (`scripts/handling-probe.mjs` on the Air against city.lab, slice 1, virtual pad and keyboard): the
skidpad on a 20 m circle held to 50 km/h at 1.01 g; braking from 80 km/h with S held 1.26 g mean, 21.8 m; a held A
key at 59 km/h turned the wheels to 24.6°. The Air rendered 4.4 fps headless beside another agent's Chrome, so the
page's controller and samples ran at that rate.

Next slices: surfaces (gravel: 0.87 × peak, 0.3-0.55 × stiffness), then every fleet car from its mass and size.

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

The following table records the original handling baseline. The trophy truck is now the #19-style 4400 car
(four-wheel steer, IFS + IRS, 1.067 m tyres, 3.16 m wheelbase, 2.18 m track, 0.36/0.38 m bump and 0.30/0.33 m
droop; model in `actors/trophy4400/`, SPEC in `~/turku3d/loops/trophy4400/SPEC.md`). Current tuning lives in
`physics/constants.ts`; power and handling changes are measured separately from the original baseline below.

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

### Rear steer

The truck steers its rear wheels (`rearSteer.ts`, stepped in `vehicle.ts`; inner-wheel angles, + left). In the truck a
press of `sprint` (Xbox A, PlayStation ✕, Shift) cycles the mode (`rearSteerControl.ts`, `play.ts`):

| Mode (tag) | Rear inner angle at full front lock and walking pace | Behaviour |
|---|---|---|
| AUTO (`auto`, default) | −32° | passive counter-phase: `−passiveRatio(v) · front/42° · 32°`, full below 2.5 m/s, 0 at 13.4 m/s |
| AUTO 50% (`auto50`) | −16° | exactly half of AUTO's ratio at every speed |
| LOCK (`lock`) | 0° | rear centred |
| SIDEWAYS (`crab`) | +32° (front stops at 32°, all four wheels parallel) | in phase, `min(|front|, 32°)` |

Every mode except LOCK locks out at 13.4 m/s (the rear centres at the 1.4 rad/s slew) and releases below 11.6 m/s.
A press of A at a shop or drop-off is the mission's (`mission` shares the button): it opens or hands over there and
leaves the mode alone. The autopilot drives in AUTO. The HUD tag (`ui/rearSteerTag.ts`, bottom centre) shows
`REAR <mode>`, a rear-angle tick and a LOCKED tint; above it a key hint (`Ⓐ · Rear steer: AUTO`, the binding of the
device in use) shows for 4 s after entering the truck and 2.5 s after a mode change, then fades. Multiplayer sends the
mode as `drv[3]` (0 AUTO, 1 SIDEWAYS, 2 AUTO 50%, 3 LOCK; `net/drv.ts`); remote rear wheels follow the synced wheel
`steer`. Tests: `npx vitest run tests/unit/trophy4400-steer.test.ts` and `-t D2` of `tests/unit/trophy4400-dynamics.test.ts`.

## Verification

From the repo root:

```bash
make game-test game-vehicle-test game-build
# Source your sandbox.env before running these commands.
cd game
npx playwright test tests/e2e/vehicle.spec.ts --workers=2
```

`game-vehicle-test` runs the small curb, CCD and impact fixtures on the game's Rapier WASM; the validator's car
report checks the same vehicle code on its Node WASM against the published tiles in the integrator. The reusable
fixture factory, `CURB_SCENARIOS` and `runCurbScenario` live in `game/tests/fixtures/vehicle.ts`. No real tiles or
network access are needed after dependency installation.
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

## Bumps

`bumps.ts` reports what a tyre feels on any surface, curb material or not: the real tiles' kerbs are lidar slopes
(10–20 cm over about 0.6 m) that the ray wheels roll up smoothly, so they never strike a curb face. Each fixed step
`BumpDetector` reads every wheel's suspension length and ground contact height (`wheelContactPoint`):

- **step**: the change of slope of the wheel's ground path over travelled distance s, h(s) − 2h(s − 0.6) + h(s − 1.2).
  A kerb of height h reads about h whatever its ramp width up to 0.6 m; a steady hill reads 0; 2 cm road texture
  stays far below the 5 cm floor. The measure swings to the opposite sign one window later, so a wheel reports one
  step per burst and ignores that lobe for 1.8 m. A wheel in the air starts a new path when it lands.
- **jolt**: suspension compression speed from 0.5 m/s (humps and potholes at speed, landings).

Each channel holds its peak while it grows and publishes `state().bump = {id, wheel, height, jolt, speed}` on the first
tick it stops growing, so both front wheels on one kerb give one event and the rear axle a second.

## Collision telemetry

Call `vehicle.afterStep(dt)` after `world.step()` (the physics worker already does so). The collision impulse is the
chassis momentum the world step changed beyond gravity: every wheel, drag and curb impulse of the tick is applied
before the step, so only contacts and CCD remain. Rapier 0.21 reports no contact manifold impulses between the
chassis and a trimesh wall (every building in the tiles), so the manifolds cannot measure crashes. `state().impact`
is null until a speed change of 0.5 m/s (`IMPACT_MIN_DELTA_V`, 600 N·s for the car, 95 N·s for the scooter), then
retains the latest `{id, impulse}` for consumers to deduplicate. Reports are limited to one per 150 ms and reset
clears the retained event. The audio adapter uses it for positional impact loudness, haptics for the crash rumble.

## Body damage (the starter car)

Phase 2 of the car-damage plan, calibrated on the Wreckfest 2 study (`~/turku3d/research/wreckfest2/damage-study.md`):
a deformation cage evaluated on the GPU, no extra geometry, one damage state owned by the physics worker.

- **Cage** (`damage.ts damageLayout`): 54 nodes from the model's box (`actors/fleet/catalog.ts fleetDamageBox`):
  9 stations along the length (fractions 0, .07, .14, .235, .5 and mirrored: dense at both crumple zones, one cabin
  station), 2 levels (bumper/sill, belt/bonnet line), 3 laterals. The same rule serves every fleet model.
- **Weights** (`actors/fleet/damageRig.ts skinToCage`, at load, once per geometry): Kuhn tetrahedra of the cage cell
  give each vertex 4 nodes whose weights reproduce any affine offset field exactly; parts under 0.55 m (connected by
  triangles and coincident positions: lamps, grille, mirrors, handles, seats) move rigidly with the weights at their
  centroid. `skinIndex`/`skinWeight` are 8-bit.
- **Crumple model** (`damage.ts applyHit`): per face (front, rear, both sides, roof) the push component of the hit's
  velocity change counts `(km/h)² - 35²` of energy into bins under the footprint (front/rear by lateral, sides by
  thirds, one roof bin), less as a bin nears its stop (deformed structure is harder). Face depth
  `D = 0.149 L (1 - exp(-E / 6000))`, hard stop `0.105 L` (sides `0.15 W`, roof `0.12 H`, stiffer); a soft bumper
  layer adds up to 3 cm from 15 km/h. The end face moves by `D` up to 0.117 L deep, falling linearly to 3 % at
  0.235 L (the cabin stays within 1.5 cm) and 0 at mid-car; the bonnet/boot buckles up, the band bulges, the end
  droops, and the rigid end is uneven (deterministic per node). Node offsets accumulate the change of this shape.
  Golf, single head-on: 30 km/h 2 cm (bumper), 50 15 cm, 70 32, 80 40; six at 61.5 km/h 25 → 43 cm (Wreckfest 2,
  scaled to 4.255 m: 0, 19-21, 32, 40; 27 → 44). An off-square hit crushes its leading corner (Wreckfest's bimodal
  results).
- **Hits from physics** (`vehicle.ts trackCrash`): the chassis velocity change of each solver step beyond gravity
  (the same measure as collision telemetry, so trimesh walls count) is summed in the chassis frame while steps exceed
  1 m/s, and applied as one hit after two quiet steps or 0.3 s. The angular impulse places the contact on the chassis
  box; a contact beyond half of the face's half-width gets a footprint to its nearer corner, else it is a wall. A
  sleeping damageable car measures the hit that wakes it. Hits from below (landings) do nothing. Multiplayer contact
  impulses from the server add no hit: the remote car's kinematic proxy is already a local contact.
- **State**: `DamageState` (offsets, 19 energy bins, unbounded `energy` and `hits` as hooks for handling, mechanics and
  loose parts). Wire form (`encodeDamage`): 208 bytes, base64 280 characters, offsets at 5 mm in int8. It rides in
  `VehicleState.damage` (absent while undamaged), in the shared vehicle pose (`server/PROTOCOL.md`), in saves
  (`SaveGame.carDamage`) and in a travel ticket's ride. `vehicleDamage` sets it (a load, a record, null for a fresh
  car: a new game or a progress reset). There is no repair.
- **Drawing**: the starter car (`actors/car.ts`) and every car drawn on its own (`fleet/visuals.ts actor`: driven,
  remote) use `fleetDamageMaterial`, which blends node rotation and translation in the vertex stage from one pool
  texture (32 rows of 54 nodes, half floats), selected by the car's slot (a per-object uniform). One program per pass
  for all of them; r186 SkinnedMeshes would name every skeleton's bone buffer apart and build programs per car. The
  pool re-uploads only when a car's damage changes; nothing runs per frame. The wheel hubs follow the cage at their
  pivots, as the physics wheel mounts do (see Mechanical damage). Instanced batches (parked, traffic) stay undamaged.
- **Developer console** (`dev/carDamage.ts`, `window.__game.damage`): `bounds()` (crumple from the drawn body's
  deformed bounds, cm), `state()`; with developer tools (`?dev=1`) `dent({ kmh, at, halfWidth, offset })`, `clear()`
  and `look({ yaw, pitch, distance })`, a camera held around the car. Deployed check:
  `~/turku3d/tools/host-lease/session.py run air NAME --kind func --headless --load-timeout 240 -- node
  game/scripts/car-damage-probe.mjs dent|wall|mp|perf OUT_DIR` (four views per state, wall runs at 30/50/80 and
  3 × 60 km/h, a second client, the frame-cost bench `damage.bench({ count, kind })` under a perf lease).

Measured on city.lab (2026-11-06, headless Chrome on the M1 Air, the Golf driven into a building wall, crumple from
the drawn body's deformed bounds): 30 km/h 0.8 cm, 49.5 km/h 15.1 cm, 74.5 km/h 37.7 cm (a 59 m run-up), three at
59-60 km/h 20.5 / 27.6 / 32.4 cm; a second client draws the owner's car with the same bounds within 0.5 s. A facade
flat to only a few centimetres makes a hit corner-first, which crushes that corner more (as Wreckfest's off-square
runs). Frame cost (same draws with and without the cage, alternating rounds, prepass + shadow + beauty GPU time):
+0.5-0.7 ms for 20 damaged cars and +1.5 ms for 60 on the Air; no CPU change. Cage weights take about 10 ms per
27k-vertex body at load, in frame-sized phases.

Next iterations (hooks in DamageState: `energy`, `hits`, the bins): handling (toe/camber pull from front-zone
depth, wheel mounts following the cage), mechanical states per system (radiator, engine, per-corner suspension), loose
and detached parts (node pairs with hinges, high thresholds), glass stages, scratch and dirt masks, crease detail
where the cage compresses, and Phase 3: the same rule for the whole fleet (parked and traffic batches would need
per-instance pool slots or a damaged car's own draw).

```sh
npm --prefix game test -- --run tests/unit/car-damage.test.ts tests/unit/parked-paint.test.ts
```

## Mechanical damage (the starter car)

Phase 2, iteration 2: damage affects the car. Same calibration source, same single state and wire as the body.

- **Wear** (`damage.ts wearHit`, in `applyHit`): each face's `(km/h)² - 35²` of a hit (before the face stiffness) also
  wears the systems behind it, scaled by the footprint share of its bins, and never saturates (Wreckfest 2: the shape
  stops at the hard stop, the part states keep stepping per hit). Front: radiator and engine by `.25/.5/.25` of the
  three laterals, each front corner by its lateral, the steering by half of both. Rear: the rear corners. Sides: the
  corner under the front or rear third, the steering (half) and the engine (a quarter) from the front third. The
  steering's side (`steerSide`, +1 the car's left) is the first steering hit's side, or a deterministic coin for a
  square one. Seven systems: cooling, engine, steering, front-left, front-right, rear-left, rear-right (physics wheel
  order).
- **Levels** (`mechanics.ts LADDERS`, 0 intact, 1-3 damaged, 4 terminal, like Wreckfest's five states): cooling at
  4000 / 10000 / 18000 / 28000, engine at 15000 / 24000 / 34000 / 46000, the body zone of each corner at 4000 / 7500 /
  10000 / 12500, steering and suspension hardware at 16000 / 26000 / 38000 / 52000. This reproduces the Wreckfest 2
  tables: nothing up to 72 km/h in one hit; the radiator from one ~75-80 km/h hit, the second at 61.5 or the seventh
  at 42.5 km/h, its level 2 at the fourth at 61.5; the hit-side front zone 1 → 4 over hits 2-5 at 61.5; the engine at
  the sixth; steering and suspension hardware intact through six hits at 61.5.
- **Effects**: drive force and power (`Vehicle.step`, all speeds) × cooling `1 / .95 / .88 / .8 / .7` × engine
  `1 / .88 / .76 / .62 / .5`, never below a third (the car stays drivable). Golf on a flat road, headless (0-50 km/h,
  0-80, speed after 30 s): fresh 2.23 s, 4.40 s, 102 km/h; radiator 1 (one 80 km/h hit) 2.35, 4.70, 100; radiator 2 +
  engine 1 (six at 61.5) 2.93, 6.23, 93. A rough engine (`rough`, misfires and hunting: `audio/mix.ts roughEngine`,
  evaluated at the 30 Hz continuous-mix tick on the car engine loop's gain and rate, steadier under throttle). Steam
  (cooling ≥ 1) and dark smoke (engine ≥ 1) from the bonnet front while the car is driven, local and remote
  (`TyreEffects.engineBay`: sprites of the tyre smoke's one instanced draw, white or dark by a per-instance shade).
- **Handling** (`mechanics.ts ALIGNMENT`, applied in `Vehicle.step` and `mountWheels`): the wheel alignment changes
  before any hardware level. A bent steering turns the steered wheels towards `steerSide` by `0.4° (1 - exp(-(w -
  2000) / 8000))` of steering wear; each corner's toe turns its wheel out (front) or in (rear) by `1° (1 - exp(-(w -
  1500) / 12000))`, so a square hit's toe cancels across the axle and a corner hit pulls towards that corner. Each
  wheel mount (`setWheelChassisConnectionPointCs`) follows the cage at its hub along and across the car (one 80 km/h
  head-on moves the front mounts back 11 cm, shortening the wheelbase; the drawn hubs move with the same
  `cageOffset`) and a corner with suspension level n moves up into the body by 0 / 1 / 2 / 3.5 / 5 cm (the corner
  sits lower; the drawn hub height comes from the physics wheel) with its dampers × 1 / .85 / .7 / .55 / .4. Camber
  is not modelled: Rapier's ray-cast wheel projects its axle onto the contact plane (Wreckfest 2: under 0.35°). No
  wheel comes off. Wreckfest 2's pull probe (2.5 s of straight full reverse from rest, hands off), headless: fresh
  0.00°, after 50 km/h 0.00°, 75 km/h 0.54°, 80 km/h 0.69° (Wreckfest: -0.14° fresh, -0.5 to -1.0° after 80 km/h,
  i.e. 0.36-0.86° more); six hits at 61.5 km/h pull over 0.8°. Hands off at 80 km/h after six hits at 61.5 the car
  turns 0.5-4°/s; a heavily wrecked car (three 85 km/h side hits at the front-left wheel, six frontal, one rear) 5.6°/s
  with all four wheels down.
- **Indicator** (`ui/damageHud.ts`, while driving a damaged car): a 26 × 46 px top view at the bottom centre (the
  rear-steer tag's place, which only the truck uses) with the four corner zones, wheels and the engine bay tinted
  by level (yellow → red), and short lines for what is damaged, worst first ("Engine", "Radiator", "Steering pull ←",
  "Suspension front left"). Hidden for an intact car; full strength for 6 s after a new hit or getting in, then dimmed
  to 45 %. It shows no key.
- **Wire** version 2 (224 bytes, 300 base64 characters): the body state plus wear (uint16, 8 (km/h)² units) and the
  steering side. Version 1 (body only, older saves) still decodes, unworn. The server checks only a bounded base64
  form. `mechanicsOf(wire)` caches the decode for the renderer, audio and HUD.

```sh
npm --prefix game test -- --run tests/unit/car-mechanics.test.ts tests/unit/car-damage.test.ts
```

## Tyre marks and smoke

`Vehicle.step` measures wheel contact velocity (including chassis angular velocity) in each steered wheel's
frame. Lateral slip is sideways m/s. Rapier's ray vehicle has no wheel angular inertia, so longitudinal sliding
is estimated from engine/brake impulse demanded beyond suspension-load × friction × timestep. `WheelState.tyre`
contains both speeds, the actual wheel collider's triangle/primitive material and chassis-local contact point/normal.
Sleeping vehicles omit it. This adds no ground rays. `tyreStrength` supplies the same hard-surface/contact-gated
demand to visual effects and the existing tyre screech loop. Both apply `TyreSlipFilter`: 80 ms of settled
contact followed by 180 ms of continuous sliding, with a 100 ms release. Contact loss, invalid/soft surfaces
and suspension travel faster than 1.5 m/s reset the trigger immediately. Lateral/longitudinal thresholds
start at 2.2/2.8 m/s and rise with vehicle speed, suppressing small straight-road disturbances. Remote
visuals use the same filter with replicated suspension/contact metadata and planar velocity. Handling is unchanged.

`TyreEffects` owns 4,096 ground quad segments and 384 instanced analytic soft smoke sprites, two shared materials
and two draws. Both draws exist before startup warmup, with zero initial opacity, so first use creates no pipeline.
Emission/update paths reuse typed arrays and scratch transforms. Strips follow contact planes with an 18 mm offset,
fade over 40–65 seconds and fade before their ring slots recycle. Lost slip/contact, soft ground, teleports and slot
reuse break strips. Smoke grows, rises and fades over three seconds; low/medium/high quality emits 7/13/20 particles
per second per fully sliding wheel. Local emission requires a driven car/truck. Multiplayer emission consumes the
replicated per-wheel data against the interpolated physical chassis; stale poses stop emitting after 300 ms.
Parked cars and scooter stabiliser wheels never emit. Session disposal releases the GPU buffers/materials.

Affected quick checks: `npm --prefix game test -- tests/unit/tyre-effects.test.ts tests/unit/audio.test.ts` and
`npm --prefix game run typecheck`. The focused tests cover actual Rapier contact/material and handbrake signals,
surface filtering, sloped/connected/broken strips, bounded recycling, smoke lifetime/density and shared audio slip.
In-game appearance and driving are checked by Jonni.

## Horn and road lights

While driving, hold **G / L3** for the positional horn (truck: lower/louder pair; scooter: single beep).
Press **L / D-pad up** to cycle **off → driving lights → long beams → off**. Entry always selects driving
lights. Menus consume the light press without changing the vehicle; pause/menu/blur releases the horn.
Copy camera position moved to **J**. Signals travel as absolute `lights: 0|1|2` and `horn: boolean`
through worker snapshots and the multiplayer pose sanitizer. Remote cars render lamps and positional horns;
only the locally driven vehicle allocates projected beams (two shadowless spotlights).

GLB pipeline convention: name separate lens nodes **lamp-front-left**, **lamp-front-right**,
**lamp-tail-left**, **lamp-tail-right**, or give their materials those names. Single `lamp-front` / `lamp-tail`
works for a scooter. Keep the existing GLB axis convention of the model pipeline: names survive its loading
transform. Mounted actors and fallback geometry use the physics frame **+X forward, +Y up, +Z right**,
measured from the chassis centre.
Materials must expose emissive colour/intensity; each actor clones only its lens materials. Current atlas-only
sedan and scooter receive attached lenses with dark housings and optical ribs. The trophy truck uses its `led-optics` (headlights, pods and light bar) and `tail-lens`
materials; `fleet_headlamp` / `fleet_taillamp` remain supported in authored fleet GLBs. Named nodes or materials
are baked into front/running-tail vertex tags when the fleet is flattened; the generated fleet keeps its existing `fleetSurface` vertex lamp tags and per-object lamp uniform.

Driving lamps glow in daylight; dusk/night adds a warm, soft asymmetric 40 m dipped pool (raised on the
right-hand verge). Long beams use a brighter, higher, wider 145 m cone with HDR lens emission for the
existing bloom pass. Both follow the shared night factor/exposure and use normal PBR direct lighting,
including the road's existing weather roughness. A 10 Hz nine-ray fan per lamp conservatively masks each angular segment
at collision geometry; the lights share one packed uniform buffer and skip the fragment loop when inactive; it is a bounded occlusion approximation, not per-pixel shadows. There are no volumetric
beam meshes. Cars keep their last selected lights after exit; horns stop immediately.

Focused contract evidence: `npm --prefix game test -- tests/unit/vehicle-signals.test.ts
 tests/unit/shared-vehicles.test.ts tests/unit/input.test.ts tests/unit/haptics.test.ts`.
