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

Every fleet car (`modelTuning(id)`, `tuning.tyre`: the starter, parked cars a player takes, shared multiplayer cars)
drives on slip-curve tyres calibrated on Wreckfest 2's Bravion (`~/turku3d/research/wreckfest2/physics-study.md`,
"Turku3D tuning targets") through the starter; the trophy truck, the scooter, the generic `CAR` and ambient traffic
(kinematic, traffic/flow.ts) keep their own driving. Rapier still runs the suspension (spring, dampers, contact point, wheel load);
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
- **Surfaces** (`TARMAC.surfaces`, keyed by the contact's `MATERIALS[].audio` class, read in `trackSuspension`):
  gravel from Wreckfest 2 (side peak × 0.87, initial stiffness × 0.55 to fit its axle points μ 0.5 / 0.8 / 1.0 at
  4.8 / 8.5 / 11.6°, braking and traction × 1); soil (× 0.8, stiffness 0.4 / 0.48, longitudinal × 0.85), grass
  (× 0.62, 0.36 / 0.42, × 0.6) and sand (× 0.66, 0.3 / 0.36, × 0.62) are estimates; asphalt, concrete, stone, metal
  and wood grip as tarmac.
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
  exceeds the front's by 2°, or the body slides tail-out against its yaw by 8° (all four tyres past their peak, as on
  soil or grass with the throttle floored), it cuts the drive and brakes the outside front wheel, up to 60 % at 8°
  of excess or 16° of sideslip. Without the sideslip trigger a half-stick turn at 45 km/h on soil spun the car.
- **Fleet** (`slipCurveHandling`, from each model's spec): the same tyres, steering, keyboard slew, assists and
  brake build-up; centre of mass at 0.375 × the body height (the starter's 0.52 m of 1.385 m) capped at track / 2.6
  (static stability factor 1.3: no rollover on the tyres' grip); ride 1.75 × (1351 kg / mass)^0.15 Hz (0.7-1.05 ×;
  the rear 1.5 / 1.75 of it); roll 3.6°/g × height / 1.385 m (1-1.6 ×); limited-slip 0.7 FWD, 0.35 RWD (Wreckfest's
  Bravion), 0.5 AWD; brake force × mass / 1351 kg and spring force limit ≥ 5 × the corner load, so vans and buses
  brake at their tyres' limit and do not sit on their bump stops. A shared multiplayer car spawns with its model's
  tuning (net/sharedVehicles.ts), so a transferred or rejoined car drives as its owner's did.
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

In the browser (`scripts/handling-probe.mjs` on the Air against city.lab: a virtual pad drives a closed-loop skidpad
on the largest clear flat disc near the start, the keyboard brakes and steers on the longest straight; run it with
`~/turku3d/tools/host-lease/session.py run air NAME --kind func --headless --load-timeout 240 -- node
scripts/handling-probe.mjs OUT`). The disc it finds near the start is a soil field (material 11). The page's
controller and samples run at its frame rate, 4-15 fps headless beside other agents' Chromes:

| Build | Skidpad (virtual pad) | Brake from 80 km/h (S held) | Held A key |
|---|---|---|---|
| slice 1 (e6cf9909, 4.4 fps) | R 20 held to 50 km/h, 1.01 g | 1.26 g, 21.8 m | 24.6° at 59 km/h |
| slice 2 (901770c4, 14.9 fps) | R 20 held to 50 km/h, 0.90 g | 1.33 g, 19.9 m | 26.5° at 52 km/h |
| slices 3-4 (dad25927, 5.7 fps) | soil R 15 held to 35 km/h, 0.64 g; R 20 lost at 25 km/h (the 5.7 Hz controller) | 1.23 g, 22.2 m; build-up 0.30 / 0.59 / 0.84 g over the first 0.1 / 0.2 / 0.3 s | 26.6° at 51 km/h |

Slice 4, gravel against tarmac (same harness manoeuvres on a gravel slab; Wreckfest 2 section 7):

| Measure | Tarmac | Gravel | Ratio | Wreckfest 2 ratio |
|---|---|---|---|---|
| Braking from 50 km/h | 1.37 g, 8.2 m | 1.37 g, 8.2 m | 1.00 | 0.99 |
| 9° step at 45 km/h: lateral g, front / rear slip | 0.77 g, 3.1 / 2.4° | 0.69 g, 5.3 / 4.3° | 0.89 | 0.86 (transient peaks) |
| Slalom 18 m | clean to 60 km/h | clean at 40, the tail goes at 50 | | clean at 35 and 45 |

Slice 5, fleet (harness, each model's own tuning; all drivability checks pass except the 70 km/h slalom of the
light FWD hatches, which Wreckfest's Bravion also loses at 68 km/h):

| Model | Mass | CoM | Ride F / R | Roll | K | Max steady g (R 20 / R 30) | Braking 80 km/h |
|---|---|---|---|---|---|---|---|
| golf7 (starter, FWD) | 1351 kg | 0.53 m | 1.74 / 1.50 Hz | 3.66°/g | 0.92 / 0.75 | 1.00 / 0.92 | 1.41 g, 18.9 m |
| yaris-2001 (FWD) | 850 kg | 0.56 m | 1.83 / 1.57 Hz | 4.61°/g | 1.09 / 0.91 | 0.96 / 0.90 | 1.39 g, 18.4 m |
| e30 (RWD) | 1200 kg | 0.51 m | 1.78 / 1.52 Hz | 3.59°/g | 1.14 / 0.92 | 0.79 / 0.95 | 1.43 g, 18.4 m |
| carla-ford-mustang (RWD, 330 kW) | 1700 kg | 0.49 m | 1.69 / 1.45 Hz | 3.61°/g | 1.06 / 0.88 | 0.97 / 0.92 | 1.43 g, 18.6 m |
| crownvic-police (RWD) | 1860 kg | 0.57 m | 1.67 / 1.44 Hz | 3.96°/g | 1.22 / 0.99 | 0.80 / 0.95 | 1.42 g, 18.7 m |
| prado-2010 (AWD SUV) | 2165 kg | 0.65 m | 1.63 / 1.40 Hz | 4.76°/g | 1.20 / 0.96 | 0.97 / 0.94 | 1.41 g, 18.8 m |
| euro-van (RWD) | 2600 kg | 0.67 m | 1.59 / 1.37 Hz | 5.49°/g | 1.28 / 1.03 | 0.80 / 0.66 | 1.42 g, 18.6 m |
| transit-bus (RWD) | 13500 kg | 0.72 m | 1.27 / 1.08 Hz | 5.98°/g | 1.90 / 1.32 | 0.37 / 0.53 | 1.43 g, 18.6 m |

Further work: a gravel constant-radius reference and the contrasting light and heavy Wreckfest cars (the study's
next steps) would calibrate the loose surfaces and the fleet scaling directly; the loose-surface estimates (soil,
grass, sand) are not measured.

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

Next iterations (handling, mechanical states, loose parts, glass and surface wear are done, see below): crease detail
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
  `1 / .88 / .76 / .62 / .5`, never below a third (the car stays drivable). The power study has no Wreckfest numbers
  for damaged engines, so these steps are a judgement. On the slip-curve tyres the launch is traction-limited up to
  about 66 km/h, so the loss shows above that. Golf on a flat road, headless (0-50 km/h, 0-80, 70-80, speed after
  30 s): fresh 2.83 s, 5.22 s, 0.98 s, 102 km/h; radiator 1 (one 80 km/h hit) 2.72, 5.17, 1.07, 100; radiator 2 +
  engine 1 (six at 61.5) 2.93, 6.27, 1.62, 93. A rough engine (`rough`, misfires and hunting: `audio/mix.ts roughEngine`,
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
  i.e. 0.36-0.86° more); six hits at 61.5 km/h 1.55°. Hands off at 80 km/h after one 75 km/h hit the car turns
  0.67°/s; after six hits at 61.5 0.5-4°/s; a heavily wrecked car (three 85 km/h side hits at the front-left wheel,
  six frontal, one rear) under 6°/s with all four wheels down. Same on Rapier's tyres and on the slip-curve tyres
  (`tyres.ts`): toe and pull go through the wheel steering, sag and damping through the suspension.
- **Indicator** (`ui/damageHud.ts`, while driving a damaged car): a 26 × 46 px top view at the bottom centre (the
  rear-steer tag's place, which only the truck uses) with the four corner zones, wheels and the engine bay tinted
  by level (yellow → red), and up to three short lines for what is damaged, worst first ("Engine", "Radiator",
  "Steering pull ←", "Suspension front left"). Hidden for an intact car; full strength for 6 s after a new hit or getting in, then dimmed
  to 45 %. It shows no key.
- **Wire** version 2 (224 bytes, 300 base64 characters): the body state plus wear (uint16, 8 (km/h)² units) and the
  steering side. Version 1 (body only, older saves) still decodes, unworn. The server checks only a bounded base64
  form. `mechanicsOf(wire)` caches the decode for the renderer, audio and HUD.

Measured on city.lab (2026-11-06, headless Chrome on the M1 Air, `car-damage-probe.mjs handling`: real wall crashes,
then runs on a flat straight driven through `input.driver`; means of two runs). Rapier tyres (build a175317e):

| State | 0-50 km/h | 0-80 km/h | at 100 m | best | hands-off at 60 km/h | reverse probe |
|---|---|---|---|---|---|---|
| fresh | 2.19 s | 4.66 s | 89.5 km/h | 92.8 km/h | +0.01°/s | 0.33° |
| after 49 km/h (16 cm crumple, nothing mechanical) | 2.19 s | 4.67 s | 89.6 | 92.9 | -0.05°/s | 0.48° |
| after 75 km/h (42 cm; radiator 1, front zones 1, steering -0.13°, toe ±0.27°) | 2.31 s | 5.04 s | 87.5 | 90.6 | -0.88°/s | 1.18° |

The 75 km/h crash costs 5-8 % acceleration and 2 % speed, the pull is what the 0.13° steering offset predicts
(0.83°/s at 60 km/h) and the reverse probe gains 0.85° (Wreckfest 2: 0.36-0.86°; the in-game probe also counts the
stop after the 2.5 s, which the headless one does not). On the slip-curve tyres (build e6cf9909) a 73 km/h crash gives
the same levels and +0.57 to +0.74°/s hands off at 61-65 km/h, the reverse probe -0.6° against +0.13° fresh; the
launch is traction-limited, so a radiator-1 car is as quick as a fresh one to 80 km/h (headless the same). The probe's
hands-off runs on these tyres are sensitive to the line holder's last correction (no body angular damping): a
fresh car spun once. A second client (`mpsmoke`, build 2ef113ff) draws the owner's wrecked car (four 78 km/h
dents: radiator 3, engine 1, 49.1 cm crumple) with the same shape and levels within 0.9 s, and its steam and smoke (12 and
10 live sprites; a busy room used to leave a remote car without a tyre-effect slot and so without steam). Frame cost on
the Air, chase view, the wrecked car venting against a fresh body, alternating rounds: GPU p50 28.18 / 28.26 ms and
25.11 / 26.17 ms (wrecked / fresh), CPU p50 25.07 / 25.14 and 22.81 / 22.45 ms: within run-to-run noise, the draw
count unchanged (the sprites share the tyre smoke draw).
The room drops a departed player's starter car ten minutes after its owner left unless someone sits in it
(`server/vehicles.js` `starterOfflineMs`, like the trophy trucks' five-minute rule); the probe's searches still avoid
other players' cars and park its own car back home; PROBE_WALL / PROBE_STRAIGHT pin a spot across runs.

Next steps: a leaking radiator that heats the engine over time (engine wear while driving), remote cars' rough
engines, the Realistic and Wrecker settings as alternative ladders, and the fleet (Phase 3).

```sh
npm --prefix game test -- --run tests/unit/car-mechanics.test.ts tests/unit/car-damage.test.ts
```

## Loose and detached parts (the starter car)

Phase 2, iteration 3. Bumpers, bonnet, boot lid and both mirrors hang loose after repeated hard hits and tear off into
debris; a part that is off stays off (there is no repair).

- **Split by region** (`actors/fleet/damageParts.ts splitParts`, once per body geometry, with the cage weights): each
  triangle-connected piece of the body goes to a part when 70 % of its area lies in that part's region of the damage
  box and its bounds stay inside it (10 cm margin), so authored panels stay whole and wheel-arch liners, the side
  shell and the scuttle stay on the body; only a fused shell spanning 80 % of the length would be cut triangle by
  triangle. Regions: bumpers the lower 45 % of the end 17 % of the length; bonnet the up-facing panel of the front
  30 % inside the wings; boot lid the rear 16 % between 42 and 72 % of the height (a hatchback's glazed tailgate half
  stays on the body); mirrors small pieces sticking out past 93 % of the half-width near the A-pillars. Lamps stay on
  the body. Vertices shared across a part boundary are duplicated (the Golf: 6 of 26935). The Golf's split: front
  bumper 4054 triangles, rear 4632, bonnet 1005, boot lid 1591, mirrors 233 / 231. The same rule serves every model.
- **Loads** (`vehicle/damage.ts PART_LOAD`, in `applyHit`): per hit (km/h of the part's face push - `v0`)² times its
  footprint share, summed; loose from `loose`, off from `off`. Bumpers (v0 36, 1500 / 2600): loose after 3 hits at 60
  km/h or one at 75, off after 5 at 60 or one at 90 (Wreckfest 2 detached nothing in single hits up to 84 km/h, and its
  bumper beam hung after 4 x 61.5 on Realistic). Bonnet and boot lid (36, 2000 / 4000): loose after 4 at 60 or one at
  81, off after 7 at 60 or one at 99. Mirrors (15, 150 / 500; side hits over the front two thirds, half a roof hit):
  loose from one 27 km/h side impact, off from one at 38. Parking knocks (20 x 30 km/h from each side) loosen nothing.
  A corner hit frees the bumper end it struck (`partSide`).
- **Loose** (`PartPoses`, render side): a cheap damped swing about the part's remaining mount, driven by the drawn
  body's own acceleration (from its world pose, so local and remote cars swing alike): a bumper hangs from the end that
  held, its free end dropped to the ground, pulled out 10-20 cm and tipped outward; the bonnet pops 0.12 rad and lifts
  in the wind (+0.0016 rad per (m/s)², up to 1.05 rad); the boot lid hangs 0.35 rad open and swings with braking; a
  mirror dangles 0.9 rad on its cable. The transforms live in the damage pool row (two texels per part after the
  cage) and the vertex stage applies them before the cage blend; the row re-uploads every frame only while a part
  hangs loose.
- **Detached**: the part's vertices collapse after the cage blend (scale 0, zero-area triangles); every client that
  sees the state change copies the part as drawn (CPU deform) into a mesh and a Rapier box (`actors/fleet/debris.ts`,
  `physics/world.ts spawnDebris`: 7 kg bumpers, 14 kg bonnet, 11 kg boot lid, 1.5 kg mirrors; 60 s each, at most 24,
  oldest first). Debris collides with the world, cars (after 0.6 s, once clear of the body it came off) and other
  debris; wheel rays, query rays and the character pass through it (`DEBRIS.group`). A piece starts 10 cm in towards
  the car's middle, clear of the wheel plane and lifted in 10 cm steps until its box (85 % of the part's bounds) is
  clear of static colliders (a crushed nose over a kerb pushed the front bumper through the road), and one that falls
  10 m below its start goes at once. Verified on city.lab (build 3144fa2c): the front bumper, both mirrors and the
  rear bumper all came to rest on the road. A car first drawn with parts
  already off (a remote car coming into view, a loaded save) just lacks them.
- **Wire** version 3: per part its state and free end (one byte) and its load (uint16).

Measured on city.lab (2026-11-06, headless Chrome on the M1 Air, `car-damage-probe.mjs parts`, build 37f0b4ff):
three 62 km/h hits on the front-left corner, a 32 km/h knock at the left mirror and an 82 km/h rear hit leave the
front and rear bumpers, the boot lid and the left mirror loose; a square 70 km/h front hit then tears the front
bumper off and pops the bonnet, a 40 km/h side hit the mirror (two debris pieces on the ground, gone 60 s later). A
second client draws the same part states within 0.7 s, spawns no debris for parts already off when it first sees the
car, and spawns its own rear bumper debris when a later 70 km/h rear hit tears it off. Frame cost (`partsperf`, perf
lease, build 394c3247, fixed camera, 20 bench cars, four alternating rounds): GPU p50 14.0 / 13.0 / 13.1 ms and CPU
p50 20.2 / 20.6 / 20.6 ms for plain fleet cars / crumpled damage-capable cars / wrecks with four loose swinging
parts, three broken windows and worn, dirty paint each; the per-round differences stay within the ±2 ms run-to-run
noise and the draw counts match (836-957 per frame).

## Glass (the starter car)

- **Windows** (`damageParts.ts splitWindows`): glazing pieces above 62 % of the height (lower ones are lamp covers,
  never broken) facing sideways are side windows (front and rear by the gap at the B-pillar), the others the windscreen
  and the rear window. Each vertex gets its window and `crackUV`, its place in the window's plane around a crack origin
  a little off the window's centre.
- **Loads** (`damage.ts GLASS_LOAD`, stages intact / cracked / crazed / shattered): per hit (km/h - 10)² times a share.
  Side windows take side hits by their distance from the contact (as the crumple footprint), half of a roof hit; the
  windscreen and the rear window a quarter of a side hit at their end, all of a roof hit and 8 % of (km/h - 50)² of a
  crash on their end face. Steps: side glass 100 / 300 / 600 (a 21 km/h knock cracks, three at 25 km/h or one at 35
  shatter), windscreen 200 / 450 / 800, rear window 150 / 400 / 700. Wreckfest 2: side and roll loads break side glass;
  a head-on crash up to 84 km/h (and six at 61.5) breaks none.
- **Drawing**: the glazing's fragment stage reads its window's stage (one pool texel per window, a vertex-stage
  varying) and draws a crack star (11 wobbling spokes and broken rings, 0.26 m across) at stage 1, crazed all over
  with a milky haze at 2; a shattered window collapses in the vertex stage (an open frame) and throws 36 flakes
  (`actors/fleet/glassShards.ts`: one instanced draw for every burst, ballistic on the main thread to the car's wheel
  plane, 25 s on the ground then fading).
- **Wire** version 4: per window its stage and load.

Measured on city.lab (`glass`, build 394c3247): the front-left door window cracks at a 21 km/h side knock, crazes at
a further 25 and shatters at 30 (36 shards); a 28 km/h roof landing crazes the windscreen and cracks the rear and
the other side windows; an 80 km/h head-on crash changes no glass. A second client draws the same six stages within
0.8 s and throws its own 36 shards when the owner's front-right window shatters later.

## Surface wear (the starter car)

- **Cells** (`damage.ts surfaceCell`): a 16 x 16 unwrap of the damage box: the sides 16 along x 4 up, the roof 16 x 6,
  the ends 8 x 2; per cell a wear level (light scratches, bare metal beyond half) and a dirt level.
- **Painted** by hits (`surfaceHit`: from 12 km/h, bare metal from about 50, over the footprint; a wall the whole face
  at bumper height; a roof hit all of the roof with dirt) and by scrapes (`vehicle.ts trackScrape`, every third step:
  each chassis contact sliding faster than 1.5 m/s wears 0.06 per metre within 0.3 m of the point on the face it lies
  on; ground under a car on its side or roof adds 0.2 dirt per metre, walls 0.01 grime). Scrapes commit a new wire at
  most every half second (a 2 s slide along a wall: a handful of updates). A scrape alone marks the car damaged.
- **Drawing** (`damageRig.ts surfaceMask`, `material.ts wornPaint`): one 16 x 512 RGBA8 atlas (32 slots, 32 KB,
  bilinear), uploaded on change; the body's fragment stage picks the face from the rest normal and samples its cell,
  then adds thin scratch streaks along the car, patchy bare metal (metallic, no clear coat) and dirt blotches, from
  hashes of the rest position (the detail stays on the panel as it dents). Scratches and bare metal only on paint.
- **Wire** version 5: per cell one byte (wear and dirt in 15ths); 516 bytes, 688 base64 characters (the server's
  bound is 1024).

Measured on city.lab (`surface`, build 9e306c10): a 25 km/h door knock and a 50 km/h corner hit scuff 11 cells (up to
0.4); a 12 m scrape along the left side and a 6 m roof slide on the ground bring 44 cells to wear up to 0.73 and 28
to dirt up to 0.2 (scratch streaks and bare patches along the side). A second client draws the same 44 worn cells
within 0.4 s.

## Damage sounds

Original procedural sounds (`scripts/prepare-audio.mjs`, CC0): `crunch` (low thump, crumpling bursts, a ringing
panel), `scrape` (grinding loop), `glass-break` (a crack and a shower of high pings), `rattle` (irregular knocks) and
`hiss` (fluttering steam). Every drawn car's damage changes (`damageRig.ts onDamage`: `hit`, `detach`, `shatter`)
play a positional crunch scaled by the per-hit speed change (from the state's energy; 0 at 12 km/h, full at 80) and a
glass break; the driven car's continuous mix (`audio/mix.ts damageLoops`, at MIX_HZ) grinds while its body slides along
something (`VehicleState.scrape`, m/s), rattles with its loose parts and speed, and hisses while the radiator steams.
The loops start on first use only. Remote cars get the one-shots, not the loops. On city.lab (`sounds`, build
9e306c10): five damaging hits play five crunches, a shattered side window one glass break, and driving off with a
loose bonnet and mirror and a steaming radiator runs the rattle and hiss loops.

```sh
npm --prefix game test -- --run tests/unit/car-parts.test.ts tests/unit/audio.test.ts
```

## Body damage across the fleet

Phase 3 of the car-damage plan: the starter car's damage (all sections above) for every fleet model.

- **Driven cars** (slice 1): every fleet car a player drives takes damage like the starter car. Parked bodies spawn
  with their model's cage (`world/parkedVehicles.ts`, `modelDamageBox`), which takes crashes and scrapes once a
  player drives the car (`vehicle.ts armDamage`, from `world.ts driveVehicle`); a shared vehicle record of any car
  model spawns with its cage and continues from the damage in its pose (`net/sharedVehicles.ts`), so an owner change
  keeps a wreck. The driven and remote actors already draw any model with the damage material.
- **Validation at load** (`damageParts.ts`, in `skinToCage`, once per body and glazing geometry): the same rule for
  every model, checked per model (`splitReport`, `windowReport`). A part stays on the body (deforms and wears, never
  hangs loose or comes off) when it is implausible for its role (`PLAUSIBLE`): bumpers under 60 % of the width or 6
  % of the height, a bonnet under half the width or 10 % of the length, a boot lid under 45 % of the width, fewer than
  24 triangles (mirrors 12), or covering only a sliver of its region (bumpers 30 %, bonnet 35 %, boot lid 25 %: the
  panel is fused to the shell and the rest of it would stay on the car). Glazing modelled as one shell (a piece over
  60 % of the length, or one of 0.25 m² facing both the side and an end) is split triangle by triangle, with vertices
  shared across windows duplicated; a window found far larger than its role (a windscreen over half the length, a side
  window over 30 % of the width) stays intact. `cageFit`: the share of vertices more than 10 cm outside the model's box
  (weights clamp to the box) and the body's centre offset along the length.
- **Outcome** (all 55 models, `tests/unit/fleet-damage.test.ts` keeps the tables): 29 models split all six parts.
  Left on the body: boot lids of 18 models (most vans and trucks, a bus, and hatchbacks with a glazed tailgate such as
  fabia2, mazda3-bk, punto-1995), bonnets of 16 (fused to the wings), rear bumpers of 6 (carla-audi-etron,
  carla-audi-tt, carla-carlamotors-firetruck, crownvic-police, crownvic-taxi, generic-sedan), the front bumper of
  carla-carlamotors-carlacola, mirrors of 10 trucks, buses and vans without separate mirrors. No glazing to break: the
  European HGV, the fire truck and the Sprinter ambulance (opaque or body glass). Windows kept intact: 8 models (one
  or two each). Cage misfit: transit-bus (its body sits 27 cm off its origin along the length, so the front crumple
  starts that far ahead of the bumper: a model fix). The rav4's glazing is one shell and now splits into five windows.
- **Crash test** (`vehicle-gallery.html`, every model): K steps through a 50 km/h wall hit, a 60 km/h front-left
  corner, a 40 km/h door, a 60 km/h rear hit, a 75 km/h head-on and a 30 km/h roof landing; Backspace gives a fresh
  body; the status line names the parts that stay on the body. Real knocks in the gallery dent the car too. The hook
  (`window.vehicleGallery`: `select(id)`, `crash({ kmh, push, at, halfWidth })`, `clearDamage()`, `damage()`, `view`)
  drives `game/scripts/fleet-damage-sheet.mjs` (every model after a scripted 50 km/h dent, and as a wreck with its
  parts hanging loose; `fleet-damage-sheet.py` tiles the shots).

```sh
npm --prefix game test -- --run tests/unit/fleet-damage.test.ts tests/unit/car-parts.test.ts
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
