# Handover: physstall (physics worker stalls, render extrapolation)

Agent: physstall (Claude), 2026-11-03/04. Branch `local/loop-physstall`, merged into `local/loop-integration`.
Symptom: the player's car stayed still for a few frames while driving (capture 20261103-201621-v8hjwo).

## Cause
- Rapier 0.21 builds a trimesh collider in one synchronous WASM call, about 1 µs per triangle with
  FIX_INTERNAL_EDGES. Dense cells on the route (30-43k triangles) took 20-55 ms each in Node.
- The worker built each cell whole inside its `loadTile` message. Up to 4 loads queue back to back, so no state
  was posted for 100-240 ms and the render pose froze.
- For comparison: decode is 0.2-2 ms warm, removing a cell 0.1-0.3 ms, and a decode-only `checkTile` 0.2-2 ms
  (16 per bundle).
- Cells beyond the radius were also unloaded and rebuilt seconds later as the far end of the 8 s travel
  corridor swung with the heading. That was about a quarter of all builds.

## Collision build budget (`game/src/physics/tileInstall.ts`, `world.ts`, `worker.ts`)
- **Splitting.** `planCell`/`CellPlanner` splits each collision class of a cell into spatially compact pieces of at
  most `MAX_PIECE_TRIS` = 1000 triangles: in-place halving of the centroids' longer XZ extent, in bounded steps.
  Each piece gets its own vertices (`compactPiece`), TriLocator and curb helper. Primitives go in batches of 48.
- **Queueing.** `PhysicsWorld.requestTile` queues a job: decode, plan, then one piece per run. `InstallQueue.pump`
  runs jobs nearest the player's actor (or a held vehicle) first. It always runs at least one piece, then stops
  when the budget is spent.
- **Budget per worker tick** (`INSTALL_BUDGET`):
  - 2 ms while running;
  - 8 ms while the driven actor is held at missing collision (`coverageBlock`);
  - 12 ms idle (menu, pause, start-up).
- **Atomic swap.** A cell's colliders are created disabled and enabled together at commit. Only then does the
  cell count as resident (coverage), get its edge walls and notify traffic. A replacement keeps the old cell
  until the new one is complete.
- **Cancellation.** `unloadTile` cancels a queued build, and the client's waiter gets a non-permanent tileError.
  A newer request for the same id replaces the old one silently.
- **Checks.** `checkTile` decodes share the queue (`requestCheck`, priority as if 150 m away).
- **Immediate path.** `PhysicsWorld.loadTile` still builds at once (Node tests, tools).
- **Travel corridor** (`world/collisionStreaming.ts`). Corridor cells stay loaded 10 s after they leave the
  corridor (`AHEAD_KEEP_MS`); only the corridor itself requests loads.
- **Evidence.** `tests/perf/physstall.test.ts` replays the capture's 90.9-98.6 s load/unload sequence on the real
  cells (`PHYSSTALL_DIR=~/turku3d/loops/physstall`, cells fetched with `scripts/fetch_cells.py` there).
  - Whole-cell builds hold a tick for 94-143 ms of thread CPU.
  - Budgeted pieces hold it for 7-16 ms on a machine at load 20+ (about 1-2 ms when idle).
  - The same cells are resident at the end.
  - Unit tests: `tests/unit/collision-install.test.ts`.

## `phys.stall` events
- **Delivery.** The worker records stalls and repeats them in each state snapshot (`stalls`) until acknowledged,
  as it does traffic claims. `PhysicsClient` writes each one once to the capture trace (`tiles.jsonl`,
  `k: "phys.stall"`), with `ago` = ms since it happened.
- **`gap`.** No worker tick for more than 50 ms while running. `busy` gives the ms per message type, `install`
  and `step` in between; `queued` is the install queue length. A large gap with little busy time means the
  worker thread was descheduled.
- **`step`.** One snapshot's substeps took more than 12 ms. Includes `substeps` and `traffic` (the ambient
  traffic sim's ms of it).
- **`install`.** One install piece took more than 6 ms. Includes `cell`, `phase` (`decode`, `plan`, `mesh:N`,
  `prims:N`, `commit`) and `queued`.
- **`col.load` rows** now also carry `buildMs`, `pieces` and `waitMs`.

## Render extrapolation and the rotation fix (`game/src/actors/interpolation.ts`)
- **Normal frames.** The player's car and character interpolate between the latest two physics snapshots and hold
  at the newest for one step (`LATE_STEPS`), exactly as before.
- **Late snapshots.** Only a snapshot late by more than a step extrapolates, for up to `MAX_EXTRAPOLATION` =
  150 ms, then holds:
  - the car along its velocity, turn rate (last step's rotation) and wheel spin;
  - the character along its velocity.
- **Blend-back.** When the next state arrives after an extrapolated frame, the drawn pose carried on (position
  and orientation) minus the new pose fades out. At most half the actor's speed is spent closing it, so it
  never snaps or moves backwards. Teleports over 3 m are not smoothed. No extrapolation while paused.
- **Render only.** Physics and multiplayer state are untouched.
- **Regression (b1a78863, fixed in e123ac5a).** The first version blended back after any frame even slightly past
  the newest state, which ordinary 60 Hz jitter does on about 25% of frames.
  - Its correction predicted the position forward but pulled the orientation back.
  - The car's yaw stalled on those frames, so the turn looked updated a few times per second.
  - The test "the render yaw turns every frame of a steady turn" (35/60/120 fps, jittered delivery) now requires
    the pose to equal plain interpolation for on-time snapshots.
- **`?extrapolate=0`** (read in `game/src/play.ts`, `RENDER_EXTRAPOLATION`) turns extrapolation off for an A/B.
  - It is a plain URL parameter like `?geomPool=0`, not a `docs/flags.toml` entry: the registry only accepts
    `?ab.<name>=1`.
  - Register it or remove it, with the losing side, before this reaches main.

## Open items (ranked)
1. **Traffic refresh in the worker.** `trafficTiles` builds each new tile's lane network inside the message
   (`TrafficSim.setTiles`, `buildTileTraffic`). It also clears every lane's sampled heights. The next step's
   `TrafficFlow.update` then runs `refreshGraph`, which is incremental but costs ~9-10 ms for a never-seen
   tile. None of this is budgeted.
   - Next capture: look for `phys.stall` `step` rows with a large `traffic` share, or `gap` rows whose `busy`
     shows `trafficTiles`.
   - The fix would be the same pattern: queue tile graph builds and rebuilds as `InstallQueue` jobs, or split
     `refreshGraph` across substeps.
2. **Unexplained stalls under OS load.** Capture 20261103-201621 at 97.35-98.06 s had steps of 39-206 ms with no
   collision or tile events nearby. This was not reproduced in Node.
   - During this work the Mac ran at load average 20-27, and thread CPU time for identical work varied about 3x.
     Descheduling or E-core placement of the worker thread is the likely cause.
   - `gap` rows with low `busy` will confirm it.
   - Nothing in the worker can remove it. Extrapolation hides up to ~150 ms of it.
