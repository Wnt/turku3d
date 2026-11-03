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

## Open, ranked

1. **Walker tiers.** Height rays for near walkers only; far walkers at a lower update rate. Walkers are now evaluated within the 110 m ring only, but every candidate still requests chain height rays and is re-evaluated every frame.
2. **Remaining allocation:**
   - about 35 KB per frame of walker code: routes for newly born lives, the 400 ms scan's new life objects, and part of the draw loop (TurboFan-optimised; not yet located);
   - the flow step's own 10 Hz allocations (leader filter/sort, `pedestrianStop` arrays, `routePoint` objects);
   - per-car arrays in the worker's publish.
3. **Junction gridlock at the centre seam,** near engine (145, −15), controller `3110a1e593cd98cf`. It predates this work.
   - **Symptom:** 47 of 90 cars lock within 10 min on the real centre tiles.
   - **Cause:** the lane polyline kinks there, so a leader 8 m ahead along the arc is 5 m away in 2D. Pulling away, it swings its rear into the follower's nose, and the swept-footprint guard stops both for good.
   - **Tried:** ignoring the follower in the guard caused overlaps and was not merged.
   - **Needs:** smoothed lane geometry or a follower-aware guard, plus a deterministic unlock. Repro: `~/turku3d/loops/npc/lock-trace.test.ts`.
4. **Smaller items:**
   - walk/idle crossfade (shader; needs a visual check);
   - run/jog/fall clips (walkers.bin rebake);
   - parked cars pulling out;
   - walking groups;
   - tiers in the replay's `scenePopulation.draw`;
   - a per-area car model cap.
