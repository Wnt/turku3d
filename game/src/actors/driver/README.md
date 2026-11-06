# Seated drivers

Every driven car and truck shows its driver: the player character (MakeHuman on the UAL rig) seated on the
H-point, hands on the steering-wheel rim, the wheel turning with the front road wheels, the body leaning with
the g-forces, bouncing on landings, thrown forward in crashes and looking into turns. The local player's driver
and every remote player's driver animate the same way from what each client already draws of the vehicle; nothing
new is synced. Targets and measurements: `~/turku3d/research/driver-animation/study.md` ("Turku3D driver rig
targets"; Wreckfest 2 measured, head look a design value until GTA V is measured).

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

`input` is the drawn pose's world velocity, the mean front `wheels[].steer` and, for a locally simulated vehicle,
`impact.id`. `drivers.finish(camera)` (just before the render) animates the seated drivers within 60 m of the camera
(5 m hysteresis) and hides every other one. A driver keeps one seat per frame (the first caller). The scooter keeps
the player character itself (`PlayerVisual.ride`).

## Layout

The wheel centre is the model's own steering wheel where its source names one (`anchors.json`, from the
importer's `roles.json` bounds: corolla, generic sedan, S90, W212, Prado, Yaris, euro-van), otherwise the class
position: the class lateral offset, the class H-point height plus the wheel rise, and a class fraction of the
wheelbase behind the front axle (ahead of it for cab-over trucks and buses). The H-point height is lowered under
a low roof (7 cm headroom over the hair). The seat's fore-aft position is solved the way a driver sets it: the rest
grips at a 0.43 m shoulder-to-wrist distance (elbows about 105°), moved forward until the crossing arm reaches its
grip at every wheel angle to full lock. The trophy truck uses its own `seat-driver` and articulated `steer-wheel`
(its rig turns it with the same `steeringAngle`); fleet cars get a drawn wheel (rim, three spokes, hub).

| Class | Models | H-point | Recline | Wheel Ø | Tilt | Grip | Ratio, cap |
|---|---|---|---|---|---|---|---|
| saloon | sedans, estates, classics | 0.52 m | 22° | 0.37 | 25° | ±70° | 6.3, ±200° |
| hatch | hatchbacks, MPV, microcar | 0.55 | 18° | 0.37 | 25° | ±70° | 6.3, ±200° |
| suv | SUVs, pickups, trophy truck | 0.70 | 16° | 0.38 | 28° | ±70° | 6.3, ±200° (trophy 8, ±250°) |
| van | vans, ambulances | 0.98 | 12° | 0.40 | 35° | ±65° | 8, ±250° |
| truck | trucks, fire truck | 1.55 | 12° | 0.44 | 40° | ±65° | 8, ±250° |
| bus | buses | 1.05 | 8° | 0.48 | 55° | ±60° | 10, ±300° |

## Motion channels (motion.ts)

- **Steering wheel**: `θ = ratio × δ`, `δ` the mean front road-wheel angle, capped; hands gripped at the class angle
  from 12 o'clock, turning with the rim; the arms cross at lock, no re-grip.
- **Lean**: body-frame acceleration over 50 ms, 50 ms ago, from the drawn velocity; pitch 5°/g (τ 0.18 s, ±8°,
  braking forward), roll 2°/g outward (τ 0.2 s, ±4°), split 0.4 / 0.35 / 0.25 over the spine; the head counter-rolls
  half the roll.
- **Seat spring**: 3 Hz, damping 0.6, driven by the vertical acceleration: a landing dips the hips up to 4 cm (and
  nods the head up to 10°), free fall lifts them up to 1.5 cm.
- **Whiplash**: a body-frame velocity change of 2.5 m/s within 0.12 s (1.2 m/s on a local impact event): 0.35 cm of
  head travel and 0.6° of head pitch per km/h, attack 0.05 s, hold 0.1 s, release τ 0.15 s; side crashes roll.
- **Head look**: `0.12 s × yaw rate + 0.8 × δ`, ±35°, τ 0.25 s. Reversing (below -0.8 m/s for 0.3 s): over 0.4 s the
  head turns 70° and the upper spine 20° to the right, the right hand to the passenger seat's backrest; back over 0.3 s.

## Cost

Drivers are SkeletonUtils clones of the loaded character: geometry and materials (so programs) are shared, bones
are per driver. A driver draws four meshes (skin, hoodie, jeans, hair; eyes, brows and lashes within 8 m; teeth,
tongue and shoes never), casts no shadow and culls with a fixed sphere. `drivers.frameMs` is the main-thread time
of `finish()`; `__game.state().drivers` lists the seated drivers with their hands' distance from the grips.

## Tests

`npx vitest run tests/unit/driver-rig.test.ts` (from `game/`): the body constants against the shipped GLB, every
fleet model's layout under its roof with both hands within 1.5 cm of the rim at rest and full lock, the ratio and
caps, and each motion channel. The vehicle gallery (`vehicle-gallery.html`) seats a driver in every model;
`window.vehicleGallery.hold` / `.view` hold an input and a camera for scripted shots.
