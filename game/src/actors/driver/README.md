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
| `model.ts` | The one-draw driver: the character's meshes and the steering wheel merged over a GPU-baked colour atlas |
| `rig.ts` | The character clone's pose: base `Driving_Loop`, spine/neck/head rotations, two-bone arm IK to the rim, the wheel bone |
| `drivers.ts` | `Drivers`: who is seated this frame, distance detail tiers, the dev snapshot |
| `ownWheel.ts` | One steering wheel: the measured models' own static wheel left out of the driven/remote car's body |
| `ambient.ts` | Static drivers in the ambient traffic near the camera: one instanced draw per posture class |

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
importer's `roles.json` bounds: corolla, generic sedan, S90, W212, Prado, Yaris, euro-van, transit-bus), otherwise the class
position: the class lateral offset, the class H-point height plus the wheel rise, and a class fraction of the
wheelbase behind the front axle (ahead of it for cab-over trucks and buses). The H-point height is lowered under
a low roof (7 cm headroom over the hair); an anchor's `hip` sets it from the model's seat (the bus: cushion at 0.83 m,
H-point 0.85 m under its low, flat wheel). One wheel only: an anchor's `own` box (the source steering-wheel parts' bounds)
drops every connected part lying wholly inside it from the body that driven and remote cars draw (`ownWheel.ts`,
`fleet/visuals.ts actorBody`: the rim, spokes and hub, not the dashboard or column), and the rig draws its turning wheel
in its place; parked and traffic batches keep the source wheel. The seat's fore-aft position is solved the way a driver sets it: the rest
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

On the M1 Air a draw costs about 50 µs of frame time whatever its size (hiding the hair, the skin, the clothes or the
wheel of 20 drivers saved 0.9-2.6 ms in proportion to the draws), so a driver is **one skinned draw** (`model.ts`):
skin, hoodie, hair, jeans, eyes, brows, lashes and the drawn steering wheel merged into one geometry (about 14.5k
vertices) with one material. Its colour atlas (2048 x 1024 sRGB with mipmaps, about 11 MB) is baked on the GPU once
when the character loads: each source map times its material colour into its own tile (skin 1024, hoodie, hair and
jeans 512, eyes, brows and lashes 256), with 4 px gutters; per-vertex roughness; one alpha cutout for the hair, brows
and lashes; no normal maps. The steering wheel rides on an extra bone (placed, turned and scaled to the rim by the
rig; collapsed where the vehicle turns its own). Teeth, tongue, shoes and the cornea are left out. Drivers are
SkeletonUtils clones of that model: buffers and the material (so programs) shared; bones and a geometry object (for
its drawRange) per driver. They cast no shadow and cull with a fixed sphere.

The detail still falls with distance, as index ranges of the same draw (the index runs body, wheel, legs, face): skin,
hoodie and hair always; the drawn steering wheel within 30 m; the jeans within 15 m; eyes, brows and lashes within
8 m; beyond 30 m a driver is posed on alternate frames; beyond 60 m (5 m hysteresis) none is drawn. The arm IK reads
bone world matrices directly and refreshes only the arm chain it moves. `drivers.frameMs` is the main-thread time of
`finish()`; `__game.state().drivers` lists the seated drivers with their hands' distance from the grips.

City.lab, M1 Air, perf lease, 20 bench cars in view with seated drivers against the same cars without (before the
distance tiers): frame p50 20.3 -> 27.9 ms, GPU 19.7 -> 27.5 ms, +88 draws, `finish()` 2.9 ms.

## Ambient traffic

Moving traffic cars drawn in view within 40 m of the camera (`fleet/visuals.ts trafficSeats`) show a static driver
(`ambient.ts`): no IK, no motion, no drawn wheel (the traffic batches draw each model's own). The first model seen of a
posture class poses the rig at rest (steering straight) and skins it on the CPU into a plain geometry (body, legs and
face tiers, the same material); every car of that class is one instance of it, moved from that model's H-point to
its own model's. A street costs one instanced draw per class in view (traffic families: saloon, sports, hatch, SUV,
van), whatever the car count. Parked cars stay empty.

AMBIENT_RESULT

## Tests

`npx vitest run tests/unit/driver-rig.test.ts` (from `game/`): the body constants against the shipped GLB, every
fleet model's layout under its roof with both hands within 1.5 cm of the rim at rest and full lock, the lock angles,
the horn hand and each motion channel. On the deployed share, `game/scripts/driver-probe.mjs` (`shots`: lock, braking,
crash, a second client's view; `perf`: 20 animated drivers against none, alternating). The vehicle gallery (`vehicle-gallery.html`) seats a driver in every model;
`window.vehicleGallery.hold` / `.view` hold an input and a camera for scripted shots.

After the distance tiers (city.lab 9e306c10, same bench, perf lease; the world was still streaming, baseline draws
670 -> 790 over the run): frame p50 22.3 -> 29.5 ms (median of three alternating rounds), `finish()` 2.1 ms.

## Next steps

- **More anchors**: most CARLA and Sketchfab bodies merge the interior; derive the wheel and seat from the geometry
  (the glazing's windscreen base, the seat cushion) for the rest of the fleet.
- **Trophy truck**: its `seat-driver` is 0.55 m behind and only 0.24 m below its wheel; at ±130° the crossing hand is
  3-5 cm short of the rim even with the seat moved 20 cm and the torso leaning. Move the seat anchor or the wheel in
  the model, or lower the trophy's lock.
- Entry and exit animations (GTA: 2.7 s door closed, 1.7 s open, 1.6 s exit; 1.2x for high cabs); per-player
  character looks.
