# framepace handover: slow frames toward the release goal (2026-11-04)

Agent: framepace (Claude), branch `local/framepace`, merged into `local/loop-integration`. The goal is
[frame-goal.md](frame-goal.md); `tools/perf/frame_pacing.py CAPTURE` prints it with PASS/FAIL per tier. Working
files, scripts and run index: `~/turku3d/loops/framepace/` (`runs/index.txt` maps every tag to its capture).

## What was causing the slow frames

Attribution came from `tile_timeline.py` hitch causes, Long Animation Frame scripts in `loaf.jsonl`, and a per-build
debug census (deleted cache keys, key-part diffs between preparation and live draws).

1. **Node-program builds inside the frame (20–23 per drive):** r186 builds a draw's TSL program synchronously on its
   first draw (50–110 ms each).
   - 10 were builds r186 had already made and then freed: `NodeManager.delete` drops a state as soon as its last
     render object goes, for example a vegetation pool replaced as it grows, or a tile's bollards.
   - 13 were never prepared: a building's own facade-component programs when its block swaps to HD, and the pool
     of a fence type met for the first time.
2. **Build stages between frames (the largest remaining cause on every machine):** r186's async builds
   (`compileAsync`, `NodeBuilder.buildAsync`) split work with `scheduler.yield()`. Its continuations are
   high-priority tasks, so 10–30 ms stages ran back to back between two frames.
   - On the 560X, `Scheduler.yield.then` (three.core) ran in 132 of 135 hitches over 50 ms, plus about 28 ms
     `setTimeout` preparation tasks.
   - Most Windows hitches have both the frame callback and the GPU frame under 40 ms: the time went to these tasks.
3. **Too many distinct programs (94 built during one drive, after fixes 1 and 2):**
   - TSL programs are keyed by node-object identity, so every material with its own node instances is its own
     program.
   - Per-texture-identity wet recipes made one program per tile for bollards and one per building for facade
     components (26).
   - Fresh nodes per scenery cell did the same for canopy and regional cells (15).
   - River reaches build `river-planar` plus its dissolve twin, 38 per drive (open, see below).

## What is merged

| Commit | Change | Files |
|---|---|---|
| `Never build a node program inside a live frame` | Idle node-builder states are retained (LRU, 512). In the live frame, a draw whose program is neither built nor cached is skipped and built after the frame (`getForRenderAsync`), like asyncPipelines does for pipelines. In-frame builds 20–23 → 0 per drive; a deferred draw appears within about 80 ms. | `render/programCache.ts` `retainNodeStates`, `render/asyncNodeBuilds.ts`, `GameRenderer._renderObjectDirect` |
| `Share the far canopy and regional mask nodes` | One node per tint/mask for all scenery cells | `render/sceneryMaterial.ts` |
| `Share wet surface programs between equal-code texture copies` | Wet recipes keyed by what a texture changes in WGSL (UV channel, colour space, type, format, sampler, kind), not its identity. The test asserts r186 emits identical WGSL for texture copies with other offset/repeat, and different WGSL for another UV channel. | `render/weather/surfaces.ts`, `tests/unit/wet-graph-reuse.test.ts` |
| `Pace r186 shader-build yields after frames during play` (+ `8 ms per frame while the 3x3 window waits`) | During play, `self.scheduler.yield` queues continuations and releases them after each frame within a share (3 ms; 8 ms while `streamingAllowance.urgent`); one long stage ends the gap's share. Loading, bench settles and hidden pages keep the native yield. | `render/yieldPacer.ts`, `main.ts` |
| `Continue paced yields inside the current frame gap` | A yield arriving with budget left in the current gap continues at once. Before, each one waited a frame, so every `compileAsync` of a tile's preparation (cached programs included) cost a frame and one tile's prepare took 10–17 s. | `render/yieldPacer.ts` |
| `Prepare car batches in an actor lane ahead of queued tile jobs` | `GraphicsPreparation` lanes actor > tile > scenery. A job reaching the head while higher-lane jobs wait goes behind them, and the running job is never interrupted. Parked and traffic car batches and keepers take the actor lane, so they no longer wait for a queue of tile preparations (parked bodies were 39–79 s late). | `render/graphicsPreparation.ts`, `main.ts` `prepareActorGraphics` |
| goal + tool | [frame-goal.md](frame-goal.md), `tools/perf/frame_pacing.py` | |

No URL switches were added. Each change is a scheduling or sharing change with no look difference, except that a
first-use draw now appears a few frames late instead of stalling the frame.

## Measuring on the Air and the 560X

- Leases: `~/turku3d/tools/host-lease/lease.py acquire HOST NAME --kind perf`, touch at every run, release after the
  series. framepace uses `framepace-win` and `framepace-air`; the Windows profile is
  `C:\Research\turku3d\agents\framepace-win`.
- **Never serve the builds through the Mac** while it is loaded (load 140–350 tonight). The remote page's tiles and
  assets then crawl, and coverage collapses or settles time out.
  - Serve the frozen dists and a tile proxy from osgallery-dev: `~/turku3d-local/framepace/` (serve-dist 7792
    base3, 7793 fix, proxy 7791, captures written there; `~/turku3d/loops/framepace/pull.sh` copies them back).
  - The page stays on `localhost` for WebGPU's secure context:
    - Windows: `netsh interface portproxy` on 127.0.0.1:5690/5692;
    - Air: `~/turku3d/loops/framepace/fwd.py`, a Python forwarder.
  - Check `netstat` first: another agent's ssh -R was using 5390.
- Count a run only if HD coverage and the tile stage times (`tile.glb`, `tile.manifest` p50) match its pair.
- Scripts: `win9.sh`, `air9.sh` (fix6 = merged head with the in-gap fix, against base3 = 2cd3d67c).
- `tests/unit/parked-preparation.test.ts` fails identically at 2cd3d67c, before framepace: pre-existing.

## Results

Drive replay `20261103-144904-0rb85a` segment 1. Frozen builds on their own ports, fresh tab per run, a discarded
warm-up, interleaved variants. Builds: `base2` = integration b4fce70d; `fix2` = async builds, retention and program
sharing; `fix3` = fix2 + the 3 ms yield pacer; `fix4` = fix3 + 8 ms while urgent (not cleanly measured yet).
Medians of the runs; HD = tile_timeline mean / 3x3 window.

| Machine | Build | Runs | FPS | >50 ms | >33.4 ms | p99 (x median) | HD |
|---|---|---|---|---|---|---|---|
| M1 Max, medium (host load 25–140 from other agents) | base2 | 2 | 44.2 | 44 | 4.5 % | 50.2 ms (2.3x) | 90 / 98.5 % |
| | fix2 | 2 | 47.0 | 33 | 3.6 % | 46.9 ms (2.3x) | 95.6 / 99.7 % |
| | fix3 | 1 | 52.1 | **2** | 1.5 % | 35.2 ms (1.9x) | 93.1 / 99.2 % |
| M1 Air, reduced (thermal level 0 throughout) | base2 | 3 | 51.9 | 37 | 3.4 % | 47.5 ms (2.7x) | 78.3 / 92.6 % |
| | fix3 | 3 | 55.0 | **7** | 1.6 % | 35.9 ms (2.1x) | 73.7 / 88.9 % |
| Radeon 560X, reduced, full screen | base (a350f0eb) | 2 | 35.8 | 173 | 17 % | 77.7 ms (3.1x) | – |
| | fix1 (async builds + retention) | 3 | 38.4 | 135 | 14.5 % | 75.2 ms (3.2x) | – |
| | base2 | 3 | 35.1 | 185 | 19.8 % | 80.2 ms (3.3x) | 62 / 78 % |
| | fix2 | 2 | 35.8 | 159 | 19.0 % | 73.6 ms (2.9x) | 63 / 79 % |

- **Mac:** the replay's 20–23 in-frame builds are gone in every fix run (0 `gfx.build` events); 12 deferred builds
  appear within 80 ms. Mac numbers swing with three other agents' browsers on the host; read the interleaved order
  in `runs/index.txt`.
- **Air:** fix3 cuts slow frames by about 80 % and p99 from 2.7x to 2.1x the median, but costs about 5 points of
  mean HD coverage (prepared tiles arrive later). fix4 raises the pacer's share while the 3x3 window waits, to win
  that back; the Air was screen-locked before fix4 could run.
- **Windows:** fix1/fix2 cut slow frames 15–20 %. The pacer on Windows is not measured cleanly: from about 20:00 the
  farm served manifests 4x and GLBs 5x slower (osgallery-dev city pass), so fix4's run reached only 6 full tiles
  (33 % HD). Runs with tile 5xx, settle timeouts or HD collapse do not count.
- The goal is not met on any machine yet. The Mac and Air fix3 runs are close on >50 ms (2 and 7) and p99 (1.9x
  and 2.1x).

## Open, ranked

1. **Measure fix4 cleanly** (pacer + urgent share) on the Air and the 560X against base2, with HD coverage:
   `~/turku3d/loops/framepace/airseries3.sh` and `winseries3.sh` (wait for a healthy farm: manifest p50 under 200 ms).
   If coverage is still lower, size the share from `StreamingAllowance` headroom instead of a constant.
2. **Fewer programs.** 94 node programs are built per drive, each 20–60 ms of JS on Windows.
   - `river-planar` plus its dissolve twin, one per reach: 38 per drive. Share the node graph across reaches, with
     the reflection texture as a per-material reference. This is the water agent's area.
   - Per-building `landmark_*` materials and twins: about 230 per session at load.
   - `bollard_mat` and facade components should now share; verify with a build census
     (`~/turku3d/loops/framepace/builddbg.mjs` with a census.ts debug hook).
3. **Windows "native" hitches:** most 560X hitches have both callback and GPU frame under 40 ms. LoAF shows 28 ms
   `setTimeout` preparation tasks: the first synchronous build stage inside `compileModel` -> `compileAsync`.
   - Take a Windows trace with `tracebench.mjs`, which now streams NDJSON (a full JSON dump exceeded Node's string
     limit), and `hitches.py`.
4. **Allocation and GC:** the walker garbage (about 35 KB per frame, npc.md) was not measured here. The trace run
   was cut short by the demo recording.
5. Shared infrastructure: the coordinator's tile proxy on :5225 answered EHOSTUNREACH to osgallery-dev, while curl
   from the same Mac worked (that node process lost local-network access). framepace used its own proxy on :5295.
