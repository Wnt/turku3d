# Handover: npc (pedestrians and NPC drivers)

2026-11-03/04, native Claude agent, branch `local/loop-npc`. Gap table vs GTA V, evidence and benches:
`~/turku3d/loops/npc/GAPS.md`, `STATUS.md`, `bench/` (local, not in git).

## Behaviour added (all client-side, no tile data)

- **Drivers:**
  - brake for every player's car in their path (local, remote, the car the player left, claimed/crashed cars) and honk when held up;
  - go round a stopped one when the oncoming side is clear; oncoming drivers squeeze right.
  - Files: `traffic/pedestrians.ts vehicleStop`, `traffic/flow.ts passPlan/makeRoom`, `traffic/lights.ts honking`.
- **Walkers:**
  - sidestep players, hurry or dive from players' cars, fall when hit, scatter near an incident (`world/walkers/reactions.ts`, local and cosmetic);
  - wait at signalised crossings for the drivers' signal phase (`network.ts` gates, `population.ts signalWait`, deterministic);
  - stand and chat on the pavement in face-to-face pairs;
  - are never born or removed in plain sight;
  - look at a nearby player.

## Car tiers (`physics/traffic.ts TIERS`), matched to live GTA V

GTA keeps real physics for about 5 cars, the nearest moving ones within 11–35 m. Parked and waiting cars are dummies, and peds exist within about 100 m (techstudy REPORT §7 L4). Ours:

- **real**: moving, in view, within 35 m, the 5 nearest (kept to 40 m / 6 nearest). Wheel rays on collision every substep.
- **dummy**: everything else to 200 m, including any car stopped for 0.5 s.
  - Stands on its lane's collision samples: every 4 m, sampled once per lane and cached. On t0_0_0 lanes these are within 3.5 cm of the ground.
  - The surveyed lane Y is off by up to 21 cm, so it is only a fallback until the samples arrive.
  - No wheel rays.
- **far**: beyond 200 m (back inside 190 m). Exact pose at 5 Hz, closed-form drift between.
- **Transitions and scope:**
  - a real/dummy change blends over 0.5 s;
  - kinematic bodies (within 70 m of the player) and the shared flow do not depend on tiers;
  - lane-change planning is capped at `PLAN_BUDGET` = 60 per 10 Hz tick (about 10 per frame), in the flow's canonical order, so MP stays deterministic.
- Tests: `traffic.test.ts` 'car simulation tiers', `traffic-flow.test.ts` 'simulation budgets'; opt-in real-tile height check in `ground-index.test.ts` (`GROUND_PARITY_DIR`).

## Incremental traffic graph (`traffic/flow.ts reset/refreshGraph`)

A tile install or release used to rebuild every movement and every junction conflict (25 tiles: 0.8–1 s per change). Now:
- outgoing lanes and siblings come from an index;
- connector curves are cached per lane pair, and sorted lanes per tile;
- lanes whose tile and neighbours are unchanged (or whose movements never reach a seam) keep their movement objects;
- conflict sets are built lazily on first use at a junction (`conflictsOf`): from last build's answer for kept pairs, else a pair cache keyed by tile road hashes, using typed-array path samples.

25 real centre tiles:
- first build 702 → 35–40 ms;
- a never-seen tile 9–10 ms;
- release/re-add 2.4–5 ms.

Results are identical to a fresh build (`traffic-flow.test.ts` 'incremental graph rebuilds' compares `allConflicts()`). The same code runs on the replay main thread and in the live physics worker.

Replay and QA (no physics worker) stand cars and walkers on `world/groundIndex.ts`: collision cells in a 2 m triangle grid, 0.9 µs per query. The position HUD names its crosshair target from a collision ray plus building meshes at the hit.

## Walker allocation

- **Frame path:**
  - lives are re-evaluated in place (`Population.at(…, into)`);
  - view, candidate, instance, pose and threat storage is pooled;
  - one reaction result is reused, reaction states are kept and looks are cached;
  - frame stamps replace per-frame Sets/Maps.
- **Hashing and helpers:**
  - `hash32` takes fixed arguments (identical output);
  - no `Math.hypot` or spread calls in the frame path.
- **Scans and focus:** crossing scans (`Population.around`) cover only cells near crossings, `populationFocus` is cached per graph, and the replay feeds pedestrians at 4 Hz.
- **Results:**
  - Node allocation test (`walker-allocation.test.ts`, sampling heap profiler, dense crowd): 134 → about 35 KB per frame, guarded at 60 KB.
  - Bench per 60 Hz step (3 real tiles): p50 0.42 → 0.03 ms, p95 0.83 → 0.31 ms, max 24.6 → 1.2 ms.

## Traffic deadlocks (2026-11-05, `local/traffic-gridlock`)

- **Lane geometry, city-wide** (`traffic/tileNetwork.ts cleanLine`): road data is quantised to 0.1 m, so diagonal
  streets arrived as 0.1 m staircases. Offsetting each step's mitre threw lanes sideways and back. Each stretch is
  now Douglas-Peucker simplified (0.25 m across, 0.1 m in height), corners are rounded within 0.8 m of the vertex,
  and offset segments that run backwards on tight insides are merged. Census over 437 bulkgen2 tiles (9,337 lanes):
  vertices turning > 45 deg 53,910 -> 63, reversals 11,295 -> 9, lanes with one 6,001 -> 40, arc-length excess
  16.9 -> 0.7 m/km, lane build 4.7 -> 0.2 s. Junction movements with a reversal 161 -> 39.
- **Junction movements:** connector handles at most half the chord (no cusps); folded crossing/signal-link
  movements >= 45 m are smoothed over +-3 m with a per-metre speed profile (`Connector.limits`, optional).
- **Guard** (`traffic/flow.ts`): 0.05 m contact margin (not 0.25 m) against a car following in the same direction;
  cars on different levels (data heights > 2.5 m apart) never block each other; poses are checked as they will be
  drawn (lane-change rate and heading, swerve yaw scaled by speed, past a seam lane's end onto the next tile's
  lane).
- **Rules:** a stalled lane change completes at a creep at its stop point; no yield to a car that is itself held
  (exit, storage, receiving, signal, no exit); after 15 s at the line a driver takes its turn against traffic that
  waited less and can still stop; a receiving lane gives its next space to the head that has waited longest.
- **Deterministic unlocks:** two cars stopped by each other's footprint for 6 s: the one behind reverses up to
  1.5 m (with up to 4 stopped cars behind it, tail first; guard-checked). Cars waiting on each other in a closed
  cycle (each car's last named blocker since its last 2 m of headway) for 60 s: the lowest key leaves the flow
  (`stats.cleared`). All pure flow state (checkpointed; `TRAFFIC.version` 12), so every peer agrees.
- **Evidence:** the 10-min centre repro (t0_0_0, t0_1_0, t0_0_-1): 42 locked > 300 s -> 0, 0 overlaps, exits
  93 -> 165, flow step mean 4.1 -> 2.3 ms (osgallery-dev, same load, interleaved). Soaks (real tiles, 16:00,
  locked > 300 s, before -> after): centre 5x5 60 min 1158/1341 -> 214/1393; east 30 min 367 -> 145; south
  672 -> 136; northwest 231 -> 0; far-east 430 -> 0; overlaps 0 everywhere after (south had 10 before).
  Opt-in test: `TRAFFIC_TILES_DIR=<roads-*.json dir> npx vitest run tests/unit/traffic-gridlock.test.ts`.

## GTA-style recycling, junctions, passes, budgeted rebuilds (2026-11-05, `local/traffic-gta`)

Spec: `docs/handover/gta-traffic-study.md` (clean-room study of GTA V's own files, scripts and observations). Its main
finding is that GTA never dissolves jams: any car no player sees is disposable (off-screen cull at 150 m against about
435 m on screen, zone/hour budgets, spawning on a hidden ring). In view, GTA tolerates jams. We built:

- **Out-of-view recycling** (`traffic/flow.ts recycle`, once a second). The head of every chain of stuck cars (no 2 m
  of headway for 90 s; the chain follows `waitsFor` through cars stuck for 30 s or more), and any car stuck for 180 s,
  leaves the flow when no watcher sees it. `unseen()`: beyond 320 m, or beyond 90 m outside the view cone widened by
  0.35 rad. Both radii sit beyond the ring's draw radii (85 m out of view, 300 m in view), so a recycled car was not
  drawn. With no watcher list (`null`) nothing is recycled. Sources keep their timetable. `stats.recycled`,
  `recycledLog`.
- **Watchers:**
  - single player: the camera view (`physics/traffic.ts`);
  - multiplayer: the server's `watch` message (`server/watch.js`, once a second), which carries every online player's
    position rounded to 1 m and is stamped with the server second. Every peer schedules it at the same tick (stamp +
    2 s, valid 1.5 s; `flow.scheduleWatch`) as all-round watchers, so peers recycle the same cars. A peer that misses
    a list recycles nothing in that window. `TRAFFIC.version` is 13.
- **Stop lines out of crossing paths** (`safeFront`): a waiting head's front stops where its body keeps 0.3 m from
  every other approach's movement path through the junction (moved back at most 6 m). This is the east pattern,
  where a reservation holder was footprint-blocked by a head at another stop line.
- **Going round obstacles** (`passPlan` takes a list of bodies): a player's stopped vehicle (as before), a player
  standing in the lane, or a car stopped mid-block, together with what holds it there (a player's car or person, or
  a footprint lock that has lasted 20 s). Queues at a junction (within 15 m of the line) are never passed. While a car
  passes, a lane leader it already clears sideways is not followed; the guard still checks contact.
- **Aggressive drivers when the lane is blocked** (Jonni 2026-11-05: "change lanes or reverse a little when their
  lane is blocked, also change their destination"). Each driver's patience comes from its key
  (`honk-patience`, 0.8-1.6 × PASS_AFTER).
  - **Escape lane change** (`LaneChangePlanner.escape`): stopped behind an obstacle, a driver signals for 1 s, then
    changes into the adjacent same-way lane with the most room ahead (same gap checks as any change; never within
    ESCAPE_ROOM 20 m of the line). It creeps sideways from a standstill. Otherwise it goes round in its own lane.
  - **Short reverse** (`backUp`): nose up against the obstacle it can get round, it first backs up 1.5-3 m at
    0.8 m/s, guarded like an unlock (never into a body; only when the space behind is clear).
  - **New route at a blocked exit** (`reroute`): a junction head kept at its line by a full exit for 3 × its
    patience takes another movement its lane allows whose receiving lane has room (at most once per 30 s).
  - **Stalled lane changes are given up:** a car stopped half-way by another body for 6 s eases back into its
    old lane (two drivers aiming for the same gap held each other before).
  - `stats.escapes / reverses / rerouted`; checkpoint fields `reverse`, `rerouted`. East 30 min with these:
    104.7 exits/min (100.1 without, 95.0 at baseline), 0 locked, 0 overlaps, 78 reroutes, 139 recycled (182 without).
    Video: `~/turku3d/loops/traffic-gta/capture/pass-topdown-aggressive.mp4` (purple reversing, green changing lanes,
    orange going round, cyan just rerouted).
- **From the GTA blocked-lane experiments** (`gta-traffic-study.md` 4.5; real-tile fast sim `probe/blockade.ts`,
  16 m truck across a 7 m two-way street in t0_0_0 for 20 min, 9 centre tiles, 3-5x real time on osgallery-dev):
  - a driver meeting a stopped player's vehicle goes round it without stopping when the corridor is clear
    (GTA drifts out 8-60 m ahead at unchanged speed);
  - a fully blocked front car backs off 3-6 m after 2-4 s, then rocks (1 m forward for 2.1 s, back for 2.1 s),
    and after 45-90 s turns round onto the opposite lane when it is clear (`beginUTurn`: a U-shaped cubic whose
    forward reach is the room ahead, guarded against cars and players' vehicles; `stats.uturns` counts finished
    turns);
  - in view, 20 min blocked: cars stuck > 300 s 56 -> 2, stuck > 60 s within 200 m 42 -> 13, 14 U-turns; out of
    view the queue never builds (recycling). Both clear within a minute of removing the truck;
  - hidden jams far from every watcher (> 150 m) go sooner: chain heads after 60 s, others after 120 s. 20/40 s
    also took cars waiting at red lights (east 98.4 vs 104.7 exits/min);
  - a final contact check: the pose a car ends its tick in must not touch another car or a player's vehicle (the
    heading of a pass scales with speed, so a sudden stop turned corners into the oncoming car); otherwise it keeps
    last tick's pose.
- **Busy-avenue and sprint scenes** (study 4.5, T4/T5): drivers turn off at the last junction before a visible jam
  without stopping, so a car within 45 m of its line whose exit is jammed (last car stuck 10 s, no room) takes another
  movement (`reroute(…, ahead)`, `stats.reroutedAhead`); U-turns after 30-60 s (GTA 24-114 s); hidden chain heads go
  after 60 s (45 s beyond 150 m; GTA within seconds, but our ring draws out-of-view cars to 85 m, so 90 m stays the
  floor). East 30 min: 104.5 exits/min (104.7 before), 0 locked, 0 recycled in view, 72 reroutes ahead.
- **Car budget per tile and hour (A/B, removed):** a tile over 2.5x (or 4x) the cars its lanes carry at free flow
  that hour fed no new cars and shed hidden excess. East 30 min: 93.9 (4x: 102.5) vs 98.4 (later 104.7) exits/min
  without it, 0 locked either way. Demand already follows the hour profile; GTA needs budgets because it spawns
  around the player, we spawn at the area boundary.
- **Budgeted graph rebuilds** (`buildGraph` generator, `rebuildBudgetMs`): a tile install or release builds the new
  graph in slices beside the live one (3 ms per worker step, `REBUILD_MS_PER_STEP`) and swaps it in at a step
  boundary, remapping cars through id maps built in the job. Before, one step paid 16-140 ms on 25 loaded centre
  tiles (osgallery-dev). Initial builds, clock jumps and checkpoint restores stay synchronous.
- **Source throttling (A/B, removed).** Sources fed no cars while a district's stuck cars held more than 8 % of its
  storage. On south 30 min it cost exits (71.5 against 90.9 per minute without it, both with 0 locked); a stricter
  rule never triggered.

- **Evidence** (real tiles at 16:00 on osgallery-dev; probe `~/turku3d/loops/traffic-gta/probe/gridlock.ts`, launcher
  `soak.sh`). A watcher stands at the district centre and turns its camera once every 120 s. Recycles are checked
  against that camera by the probe's own rule (85 m all round, 300 m in the cone + 0.2 rad).

  | district (sim) | locked > 300 s | exits/min, last half | recycled (in view) | overlaps |
  |---|---|---|---|---|
  | centre 5x5 (60 min) | 214 -> 0 | 122.0 -> 123.9 | 1048 (0) | 0 -> 0 |
  | east 3x3 (30 min) | 145 -> 0 | 95.0 -> 100.1 | 182 (0) | 0 -> 0 |
  | south 3x3 (30 min) | 136 -> 0 | 84.1 -> 90.9 | 563 (0) | 0 -> 0 |
  | northwest 3x3 (30 min) | 0 -> 0 | 46.3 -> 43.4 | 306 (0) | 0 -> 0 |
  | far-east 3x3 (30 min) | 0 -> 0 | 43.3 -> 45.0 | 82 (0) | 0 -> 0 |

  The centre baseline is the `f-centre` run of `local/traffic-gridlock` (its district baselines reproduce exactly).
  Cars locked > 120 s at the end: 0 in every district (centre 341, east 188, south 311, northwest 90 before).
  Recycled cars count as removals, not exits. In northwest, recycling also takes 90-300 s waits that cleared on their
  own before, so it exits 6 % fewer cars. The flow step mean on the loaded host (load ~55 on 16 cores) went from 42 to
  63 ms on centre and from 15.4 to 18.6 ms on east, but concurrent load differed between runs. A same-load A/B of the
  10-minute busy-intersection unit test gives 31.0 s vs 32.1 s.
- **Visual check:** `~/turku3d/loops/traffic-gta/capture/pass-topdown.mp4` (local) is a top-down render of the flow on
  t0_0_-1: a player's car stands mid-block for 120 s and 7 drivers go round it (white cars; blue while held, orange
  while passing; renderer `probe/passvideo.ts`). Air captures of the deployed share with a player standing in a lane
  are in `capture/obstacle-solo-*` (the camera faced away from the oncoming car, so they do not show a pass).

## Open, ranked

1. **Walker tiers.** Height rays for near walkers only; far walkers at a lower update rate. Walkers are now evaluated within the 110 m ring only, but every candidate still requests chain height rays and is re-evaluated every frame.
2. **Remaining allocation:**
   - about 35 KB per frame of walker code: routes for newly born lives, the 400 ms scan's new life objects, and part of the draw loop (TurboFan-optimised; not yet located);
   - the flow step's own 10 Hz allocations (leader filter/sort, `pedestrianStop` arrays, `routePoint` objects);
   - per-car arrays in the worker's publish.
3. **Traffic, after GTA-style recycling** (2026-11-05): no district locks out of view any more. What remains, from
   `docs/handover/gta-traffic-study.md` section 7:
   - no ambient traffic on dead ends, and thin unobserved arterials;
   - GTA-like signal plans (about 10 s phases, protected lefts where turn lanes exist);
   - per-driver aggressiveness.
   In view, jams are still possible (GTA also tolerates them); no soak had one within 320 m of its watcher.
4. **Smaller items:**
   - walk/idle crossfade (shader; needs a visual check);
   - run/jog/fall clips (walkers.bin rebake);
   - parked cars pulling out;
   - walking groups;
   - tiers in the replay's `scenePopulation.draw`;
   - a per-area car model cap.
