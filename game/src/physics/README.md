## Worker startup

The client waits for the worker's ready handshake before transferring collision data.
If module evaluation silently stalls for 15 seconds, it terminates that worker and
starts one replacement, retaining queued messages. A second timeout rejects startup
and surfaces an error instead of leaving the loading screen waiting indefinitely.
Loading errors reject immediately. The watchdog is cancelled on ready and never
restarts an active simulation. This recovers the silent pre-handler stall observed
in an installed iOS PWA without clearing browser or generator asset caches.

## Breakable traffic-sign poles

The full props layer and the draft producer emit sign and bus-stop nodes with stable
`pole_id` (`<visual tile>:<source object id>`). The visual packager batches most poles
but retains per-object index ranges. `world/poleExtraction.ts` reads both forms; on a
break it moves only that range into a falling mesh and removes its triangles from the
standing batch. `world/tiles.ts` tracks full and draft model lifetimes. Only poles within
the collision stream radius plus 20 m have sleeping physics bodies.

`world.ts` creates a 2.7 m by 0.1 m solid cylinder for each resident pole, fixed at its
base until it receives a Rapier contact force from a vehicle moving at least 10 km/h.
The joint and body locks release on contact, and a torque starts the fall. The walking
character collides with the pole but never breaks it. Debris settles into a fixed body
after 20 simulated seconds. Break IDs survive tile eviction and multiplayer sync;
`PoleSession` applies one €50 fine, one officer notice and one voice line for each locally
broken pole.

From a sourced sandbox, verify the pure extraction and physics fixtures with
`npm --prefix game test -- --run tests/unit/poleExtraction.test.ts tests/unit/polePhysics.test.ts`.
For the real published office tile and a built draft `t0_2_0`, generate the source list with `bash pipeline/env/run.sh python game/scripts/pole-source.py`,
then run
`cd game && ../scripts/dev/codex-task wait-cmd -- ./node_modules/.bin/vitest run -c vitest.pole.config.ts`.
The headless run writes `pole-qa-full.json`, `pole-qa-draft.json` and pole placement JSON
in the sandbox reports. Render top-down and oblique local QA views for both tiles with
`bash pipeline/env/run.sh python game/scripts/pole-placement-qa.py`; PNGs are kept under
`$TURKU3D_WORK/qa/poles/`.

## Live clock and frame transport

Gameplay calls `PhysicsClient.updateInput(character, vehicle, running)` every frame,
including paused frames. The worker owns a 60 Hz wall clock and feeds the existing
fixed-step accumulator (at most five substeps). Delayed timers discard excess debt;
resuming after pause starts a fresh clock. Inputs expire after 500 ms without a
render-frame heartbeat, so suspended tabs cannot keep driving. Explicit `step(dt, …)`
remains an ordered command for initial query barriers, save/load placement and tools;
it never waits for a state reply. The deterministic fixed-step simulation is unchanged; actor publication caches
affect state extraction only. Timer ticks join the same promise chain as tile decode, actor commands,
remote proxies and pole updates, with at most one timer queued.

When `crossOriginIsolated`, two single-writer SharedArrayBuffer mailboxes carry input
and state. The frame loop writes input and reads `latest` directly; no main-thread
message task is involved in state delivery. A jump counter preserves pulses across
worker reads. Each mailbox has three Int32 header words (sequence, byte length, try-lock), then
UTF-8 JSON bytes: odd sequence means writing, even means complete. Readers and writers attempt the lock once and never spin or wait. Copying bytes
under that lock is data-race-free; readers decode after releasing it. On contention,
the writer skips publication or the reader retains its previous snapshot until the
next frame. Unacknowledged actor updates and jump counters survive skipped writes.
Input capacity is 2 KiB and state capacity 4 MiB; overflow is an explicit physics error, never a truncated actor list.
JSON retains string IDs and the existing pose fields; actor updates use the
acknowledged delta protocol below. The client reconstructs complete vehicle state.
`publishedAt` uses `performance.timeOrigin + performance.now()` across contexts;
interpolation uses publication time rather than delayed message receipt time.
Locally caused pole breaks remain latched by stable ID in snapshots, preserving the
existing one-fine/one-sync dedup even when the renderer skips a snapshot.

Without isolation, input messages still feed the independent worker clock. At most
one state message awaits acknowledgement, preventing stale reply queues. Delivery
can still be delayed by browser task priorities on this fallback, but simulation
continues; neither stepping nor elapsed time depends on acknowledgement.

Local Vite and dist servers send COOP `same-origin` and COEP `require-corp` directly.
Static hosts (Pages) bootstrap through `core/isolation.ts` and `public/isolation.js`:
the worker adds these headers to same-origin responses, claims the game subpath and
reloads once. It stores no assets and preserves normal HTTP freshness. Registration
failure or a four-second control timeout starts the fallback. A controlled browser
that still cannot isolate also starts the fallback without a reload loop. Registration
uses document-relative URLs/scope, preserving `/turku-game/game/`, query parameters
and manifest `start_url: ./`, including standalone home-screen launches.

Runtime resource audit: the bundled modules/WASM, models, audio, Basis decoder and
UI images are same-origin; fonts are system fonts. Pages tiles are same-origin
siblings under `../tiles/` (outside the worker's scope but allowed by COEP). Optional
external tiles already use CORS fetches; their server must allow the game origin.
Multiplayer uses WebSocket (not an embedded resource); its HTTP score fetches use
CORS. No credentialless rewriting, cross-origin asset proxy or new endpoint is used.
[COEP requirements](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Embedder-Policy).

Verify pure transport, clock, input and existing physics fixtures from a sourced sandbox:

```sh
npm --prefix game test -- --run tests/unit/physicsExchange.test.ts tests/unit/physicsClient.test.ts tests/unit/input.test.ts tests/unit/vehicle.test.ts tests/unit/polePhysics.test.ts
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

Manual held-key/load diagnostic (not a gate or browser spec): start sandbox Vite,
then `scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py browser -- node game/scripts/latency-probe.mjs after`.
It invokes `~/turku3d/profiling/record.mjs` against sandbox CDP port +8, dispatches
30 autorepeat keydowns/s and burns 22 ms per animation frame. Per-frame state age,
worker step duration and measured keyboard callback costs stay under
`$TURKU3D_WORK/reports/latency/`. The `before` argument temporarily restores the
reported baseline's transport/input/frame callers during measurement, then restores
the working files. Run it only while this sandbox has no concurrent edits/checks.

### Actor allocation

The wire `state` contains cumulative actor deltas (`actorsDelta`, `revision`,
`removedVehicles`, `removedPoles`); `PhysicsClient.latest` reconstructs the complete
vehicle list and resident broken-pole poses in caches. Standing poles are already
placed by source geometry and are omitted entirely. Sleeping vehicles and settled
broken poles reuse their last pose objects in the worker, so they incur neither
per-step Rapier pose extraction nor serialization/decoding. Moving actors publish
fresh poses. Final resting poses and removals remain in every snapshot until the
client acknowledges its consumed revision through shared input (or `stateAck` on
the fallback). This keeps the exchange correct when the renderer skips snapshots,
without resending hundreds of stationary actors. `stateBytes` reports shared packet
size for the manual allocation diagnostic. Initial actor discovery can be a larger
one-time snapshot; subsequent packets contain only actors that changed.


## Ambient traffic

Moving cars (street-richness plan §3.6, Phase 3) are a pure function of shared time and each tile's own
`roads.json`, so every client computes the same cars without sending any of them.

- `traffic/roads.ts` is the one adapter to the road data: it reads a tile through `gameplay/roadGraph.ts`
  (roads.json revision 2 from G3: stable ids, lanes, widths, limits, car density, heights, setbacks, SEAM
  flags, signal controllers, OSM `highway`) and keeps the car edges that carry ambient traffic, each with a
  peak flow (below). A v1 tile gets the reader's fallback ids and 1+1 lanes, widths and limits by class; its
  heights are sampled from collision in the worker, at most 24 rays per physics step, following a lane under
  bridges from its previous sample.
- `traffic/density.ts` sets how many slots carry cars. A street's peak flow (vehicles per hour per lane in the
  busiest hour) comes from its OSM `highway` in revision 2 (motorway 600, trunk 540, primary 480, secondary
  420, tertiary 240, unclassified 120, residential 45, living street 6; service roads, aisles, driveways, bus
  lanes and pedestrian streets none) or its v1 class (street 60, major 450; v1 cannot tell a living street from
  a residential one), times an area factor for streets below secondary (×1.3 within 900 m of Kauppatori,
  falling to ×0.7 beyond 3 km). A weekday profile from Fintraffic LAM counts around Turku scales it by clock
  hour (0.02 of the peak at 1–4, 0.75 at 7–8, 0.61 at 13, 1 at 15–16, 0.16 at 22). The share of slots with a
  car is flow × profile × `?trafficDensity=` over the conveyor's 600 slots per lane-hour.
- `traffic/tileNetwork.ts` builds a tile's lanes from that tile alone: stretches between junctions, seams and
  dead ends, lanes offset by the v2 divider rule and stopped at the junction setback, in-lanes matched one to
  one to out-lanes (straight on, then right, then left, then by flow mismatch: a living street's cars turn
  back at a main road rather than main-road chains turning in; by hash). Slot n of lane L enters it at
  `phase(L) + n·H` (H = 6 s on every lane). A car waits at its stop line until the next lane's own slot grid,
  so phases lock across junctions and chains never merge: consecutive cars keep the headway. Junction
  out-lanes split the headway into two windows by approach axis (crossing streams take turns; a signal
  controller seeds its junctions alike), and each junction's fastest straight-on movement waits zero (a green
  wave). Seam crossings take a phase that is a continuous function of the crossing point, which both tiles
  compute from their own copy. A wait never exceeds H − 1.5 s; the rest is driven slower.
- `traffic/network.ts` links seams between resident tiles and evaluates a car's pose in closed form: the
  timetable (drive, wait, cross) smoothed by a 1.4 s box filter, which keeps cruising exact, turns stops into
  braking and pulling away, and preserves the spacing of a chain. A car's identity in a tile is its chain's
  origin lane and slot there (`<tile>:<lane>:<slot>`); its model and paint come from that key. `present`
  decides whether a slot carries a car: a hash of the car's slot at its chain's anchor against its lane's share
  at the clock hour it reaches the anchor. The anchor is the seam the chain enters the tile by, else the seam it
  leaves by (the next tile's chain from that crossing anchors there too, so the car keeps its presence across
  the seam), else its origin lane (closed chains re-decide each lap). Presence therefore never changes during a
  car's life on its chain; a quieter lane on the same chain shows fewer of its cars.
- `physics/traffic.ts` runs in the worker: a 5 Hz population ring around the camera (created in view only
  90–200 m out, out of view from 40 m; removed beyond 300 m in view and 85 m out of view) that creates cars
  only on present slots, the cap from `?traffic=` or the quality tier, poses every substep, and permanent
  kinematic Rapier bodies on cars within
  70 m of the player. A contact hook lets them push the driven car and pile up behind a claimed car but pass
  parked cars. The driven car's contact, a pedestrian in a car's braking path or the player entering a car
  (`trafficClaim`) claims it: the worker turns it into an ordinary dynamic vehicle `tr:<key>` with its speed.
  Automatic claims (pedestrians, pile-ups) are limited to six a minute; beyond that a car pushes the
  character aside. `ClaimSet` follows each claimed slot across seams, so its continuation stays gone. A drawn
  car whose slot stops carrying a car (a seam whose two chains anchor differently, a quieter street, a clock or
  density change) is `deferred`: it drives on until it is out of view, so density changes never pop a car in
  sight. The density clock is the shared cycle in multiplayer; in single player the clock the player sees
  (`?time=`, `?cycle=`, the developer time menu).
- The main thread (`traffic/client.ts`) feeds the worker the drawn draft tiles' road graphs and copies the
  published poses, moved on by their velocity to the frame, into the parked cars' per-body InstancedMeshes
  after their parked instances (`ParkedCarVisuals.setTraffic`; capacity reserved at load): no draw, material
  or pipeline is added. A claim becomes a vehicle record that parked cars, saves and the vehicle registry
  already handle. The nearest eight cars drive pooled engine voices; the street bed follows the count within
  150 m.

Multiplayer: the clock is the server's; `net/client.ts` reports each resident tile's road hash
(`traffic/gate.ts`, including the algorithm version) and the server answers with the hashes every reporting
player shares (`server/roads.js`). A multiplayer tile carries cars only when its local hash is the agreed one;
before the answer, or against an older server, it spawns nothing. A claimed car registers with source
`tr:<key>`, and every client drops that slot. Which slots carry cars is a function of the tile data and the
shared cycle, so every client thins alike; `?trafficDensity=` is per client, and a lower value shows a subset
of a higher one's cars. The ring is per client: positions agree, but which cars a client draws near the edge
of its ring, and how long a deferred car stays, may differ.

Defaults per quality tier: density ×1 everywhere; drawn-car caps mobile 10, low 15, medium 30, high 40. Around
the centre at 13:00 about 8–14 cars are within 150 m on v1 tiles (every slot used to carry one: 26–48), at
22:00 about 2–4, at 03:00 under one.

`window.__game.traffic.snapshot()` gives the cap, the density multiplier, the density clock hour, the drawn
count, `moving_cars` (engine x, z, the `--spawner-state` format of `scripts/dev/richness_scorecard.py`),
worker stats (cost per substep, `deferred` cars) and main-thread cost per frame.

```sh
npm --prefix game test -- --run tests/unit/traffic.test.ts tests/unit/audio.test.ts tests/unit/parked-paint.test.ts
node --test server/tests/roads.test.js
```

## Bounded collision coverage holds

Before each runtime substep, `world.ts` checks the character capsule and every car's
swept footprint against worker-resident cells. `CollisionStreamer` sends
`loadingTile` for the lifetime of actual known-cell requests, including worker
installation, and clears it on success/failure. Queued and retrying cells are not
active loads. The character and driven car wait at most 2.5 simulated seconds while
all missing cells actively load. A missing source, stopped request or timeout sets
`coverageBlock.recover` immediately; `SafeReset` uses the existing safe-sample reset
without the fall countdown. It keeps the player in their current car and resets its
body upright, dynamic and still. Held poses never become safe samples. The request
is repeated in state snapshots until coverage returns or placement clears it, so
skipped shared-mailbox frames cannot lose recovery. Flight bypasses character holds.

Parked cars retain indefinite holds. Held cars preserve their original linear and
angular motion for coverage checks and resume it if collision arrives in time;
zeroing the fixed body's velocity cannot falsely release a swept-edge hold.
Explicit reset clears both the fixed-body hold and its recovery request.

Focused Node tests (no browser or scripted in-game run):

```sh
npm --prefix game test -- --run tests/unit/coverage-recovery.test.ts tests/unit/collision-coverage.test.ts tests/unit/streaming.test.ts tests/unit/recovery.test.ts
```
