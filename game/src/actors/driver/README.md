# Seated drivers

Every driven car and truck shows its driver: the player character (MakeHuman on the UAL rig) seated on the
H-point, hands on the steering-wheel rim, the wheel turning with the front road wheels, the body leaning with
the g-forces, bouncing on landings, thrown forward in crashes and looking into turns. The local player's driver
and every remote player's driver animate the same way from what each client already draws of the vehicle; nothing
new is synced. Targets and measurements: `~/turku3d/research/driver-animation/study.md` ("Turku3D driver rig
targets"): GTA V's look where the games differ (Jonni's preference: wheel lock, grips, recline, head look, horn,
reverse look), Wreckfest 2 where only it was measured (lean, seat bounce, crash).

| File | Role |
|---|---|
| `layout.ts` | Six posture classes, the per-model wheel anchors (`anchors.json`), the seat solve, the steering ratio |
| `motion.ts` | The motion channels from the drawn pose: lean, seat spring, whiplash, head look, reverse look |
| `rig.ts` | The character clone's pose: base `Driving_Loop`, spine/neck/head rotations, two-bone arm IK to the rim |
| `drivers.ts` | `Drivers`: who is seated this frame, distance LOD, the drawn steering wheel, the dev snapshot |

## Frame and flow

Each frame the code that draws a driven vehicle calls `drivers.seat(key, frame, layout, input)` with the
Object3D in the vehicle's fleet model frame (+Z forward, +X the driver's left, tyres on Y = 0):

- the starter car: `CarVisual.frame` (play.ts); a driven fleet car: `ParkedCarVisuals.driven(id).actor` (play.ts);
- the trophy truck: `TruckVisual.driverFrame` (seatedVehicles.ts), remote trucks the clone's `driver-frame`;
- remote players' cars and trucks, and the local player's own shared car: `RemotePlayers.update` (net/remotes.ts).

`input` is the drawn pose's world velocity, its `wheels` (front steer), the horn and, for a locally simulated vehicle,
`impact.id`. `drivers.finish(camera)` (just before the render) animates the seated drivers within 60 m of the camera
(5 m hysteresis) and hides every other one. A driver keeps one seat per frame (the first caller). The scooter keeps
the player character itself (`PlayerVisual.ride`).

## Layout

The steering wheel turns linearly in the front wheels' share of the vehicle's lock (`modelTuning(id).maxSteer`; the
mean of the pair, the inner wheel on the 4WS trophy truck) to the class lock angle. The wheel centre is the model's own steering wheel where its source names one (`anchors.json`, from the
importer's `roles.json` bounds: corolla, generic sedan, S90, W212, Prado, Yaris, euro-van), otherwise the class
position: the class lateral offset, the class H-point height plus the wheel rise, and a class fraction of the
wheelbase behind the front axle (ahead of it for cab-over trucks and buses). The H-point height is lowered under
a low roof (7 cm headroom over the hair). The seat's fore-aft position is solved the way a driver sets it: the rest
grips at a 0.43 m shoulder-to-wrist distance (elbows about 105°), moved forward until both hands reach their
grips at every wheel angle to full lock; the rig's upper body leans and turns toward a grip still beyond reach. The trophy truck uses its own `seat-driver` and articulated `steer-wheel`
(its rig turns it with the same `steeringAngle`); fleet cars get a drawn wheel (rim, three spokes, hub).

| Class | Models | H-point | Recline | Wheel Ø | Tilt | Grip | Lock | Rest reach (elbow) |
|---|---|---|---|---|---|---|---|---|
| sports | cars with a roof at most 1.40 m | 0.45 m | 27° | 0.36 | 22° | ±58° | ±95° | 0.52 m (~145°) |
| saloon | sedans, estates, classics | 0.52 | 25° | 0.37 | 25° | ±58° | ±95° | 0.48 |
| hatch | hatchbacks, MPV, microcar | 0.55 | 23° | 0.37 | 25° | ±58° | ±95° | 0.47 |
| suv | SUVs | 0.70 | 16° | 0.38 | 28° | ±70° | ±95° | 0.45 |
| pickup | pickups, trophy truck | 0.72 | 16° | 0.38 | 28° | ±70° | ±130° | 0.45 |
| van | vans, ambulances | 0.98 | 12° | 0.40 | 35° | ±65° | ±130° | 0.43 (~105°) |
| truck | trucks, fire truck | 1.55 | 12° | 0.44 | 40° | ±65° | ±180° | 0.43 |
| bus | buses | 1.05 | 8° | 0.48 | 55° | ±60° | ±180° | 0.43 |
| race | (WF2 look, no model yet) | 0.42 | 12° | 0.35 | 20° | ±70° | ±190° | 0.43 |

## Motion channels (motion.ts)

- **Steering wheel**: `θ = lock × δ / δ_lock` (GTA: about ±95° for cars, about 2:1); hands gripped at the class angle
  from 12 o'clock ("10 and 2"), turning with the rim; at lock the outer hand is over the top near 11 or 1 o'clock,
  no re-grip.
- **Horn**: while it sounds (vehicle signals; remote poses carry `horn`) the right hand goes to the hub over 0.15 s.
- **Lean**: body-frame acceleration over 50 ms, 50 ms ago, from the drawn velocity; pitch 5°/g (τ 0.18 s, ±8°,
  braking forward), roll 2°/g outward (τ 0.2 s, ±4°), split 0.4 / 0.35 / 0.25 over the spine; the head counter-rolls
  half the roll.
- **Seat spring**: 3 Hz, damping 0.6, driven by the vertical acceleration: a landing dips the hips up to 4 cm (and
  nods the head up to 10°), free fall lifts them up to 1.5 cm.
- **Whiplash**: a body-frame velocity change of 2.5 m/s within 0.12 s (1.2 m/s on a local impact event): 0.35 cm of
  head travel and 0.6° of head pitch per km/h, attack 0.05 s, hold 0.1 s, release τ 0.15 s; side crashes roll.
- **Head look** (GTA): `42° × δ / δ_lock + 0.05 s × yaw rate`, ±45°, τ 0.2 s, 25 % neck and 75 % head; it follows
  the steering even at rest. Reversing (below -0.8 m/s for 0.3 s): over 0.4 s the head turns 70° and the upper spine
  20° to the right, the right hand to the passenger seat's backrest; back over 0.4 s.

## Cost

Drivers are SkeletonUtils clones of the loaded character: geometry and materials (so programs) are shared, bones
are per driver. They cast no shadow and cull with a fixed sphere; teeth, tongue and shoes are never drawn. Each
skinned mesh is one draw, and on the M1 Air a draw costs about 50 µs of frame time whatever its size (measured:
hiding the hair, the skin, the clothes or the wheel of 20 drivers saved 0.9-2.6 ms in proportion to the draws, while
the scene passes' own GPU time grew only 1.2 ms). So the detail falls with distance from the camera: skin, hoodie
and hair always; the jeans within 15 m; the drawn steering wheel within 30 m; eyes, brows and lashes within 8 m;
beyond 30 m a driver is posed on alternate frames; beyond 60 m (5 m hysteresis) none is drawn. `drivers.frameMs` is
the main-thread time of `finish()` (about 0.14 ms per driver on the Air); `__game.state().drivers` lists the seated
drivers with their hands' distance from the grips.

City.lab, M1 Air, perf lease, 20 bench cars in view with seated drivers against the same cars without (before the
distance tiers): frame p50 20.3 -> 27.9 ms, GPU 19.7 -> 27.5 ms, +88 draws, `finish()` 2.9 ms.

## Tests

`npx vitest run tests/unit/driver-rig.test.ts` (from `game/`): the body constants against the shipped GLB, every
fleet model's layout under its roof with both hands within 1.5 cm of the rim at rest and full lock, the lock angles,
the horn hand and each motion channel. On the deployed share, `game/scripts/driver-probe.mjs` (`shots`: lock, braking,
crash, a second client's view; `perf`: 20 animated drivers against none, alternating). The vehicle gallery (`vehicle-gallery.html`) seats a driver in every model;
`window.vehicleGallery.hold` / `.view` hold an input and a camera for scripted shots.
