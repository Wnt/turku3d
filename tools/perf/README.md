# Local performance captures

Open the local pause menu and select **Performance capture: On**. The red REC marker remains visible while driving and paused. Select Off to flush and detach the recorder. The HUD shows Saving until the final write succeeds, and the pause row then shows the saved capture ID. If delivery fails, SAVE FAILED stays visible and the pause menu offers Retry saving capture. Buffered data is retained; save it before starting another capture. The selection survives reloads in the same tab. Published builds do not expose capture.

Vite dev, Vite preview and `tools/dev/serve-dist.mjs` append capture files under `~/turku3d/captures/` (`TURKU3D_CAPTURES` overrides it). Their HTML responses allow JS Self-Profiling. Existing servers need a config reload/restart to pick up the middleware/header; do not restart someone else's server. If JS Self-Profiling is unavailable, the HUD shows an amber **REC · NO JS SAMPLES** marker and the pause row explains the missing sampling. Frame/pose capture continues; the reason is recorded in `meta.jsProfiler`. Normal JS sampling keeps the red REC marker. `window.__game.capture.recorder` exposes the active recorder; `lastRecorder.error` and `lastRecorder.flush()` allow inspection/retry after Off. Keep the page open until final delivery completes. Stopping also waits for any profiler rollover already in progress before reporting Saved. Page exit re-sends unacknowledged batches using best-effort beacons; the server deduplicates retries. Beacons remain subject to Chrome's aggregate 64 KiB limit.

Interrupted disk writes retain a temporary batch and the previous file lengths. The middleware repairs unfinished appends before the next read or write, including after Vite restarts. Committed retry receipts prevent duplicate rows. Temporary recovery data is removed after a successful commit.

Core files match the coordinator recorder: `meta.json`, `frames.jsonl` (one array of frame rows per batch), and individual `poses.jsonl`, `stats.jsonl`, `loaf.jsonl`, `marks.jsonl` rows. Additional `js.jsonl` rows contain `{t,end,trace}` with native profiler resources/frames/stacks/samples. Once-per-second stats also carry `gpuPassMs` and `gpuPassFrame`: a copied complete render-pass result and its originating renderer frame, or explicit nulls when unsupported/overflowed. The seven-field frame rows are unchanged. The summary deduplicates pass timings by their owning frame and labels their p50/p95 as 1 Hz sampled stats, rather than every-frame distributions. Render-pass timings exclude unrelated copies/compute/idle that remain in whole-frame GPU time; per-pass percentiles must not be summed. `network.jsonl` holds resource response timing/bytes (capture POSTs excluded; cross-origin sizes may be hidden by Resource Timing policy).

```sh
python3 tools/perf/capture_summary.py ~/turku3d/captures/drive-01 --segment 1
python3 tools/perf/capture_summary.py ~/turku3d/captures/REPLAY_ID
python3 tools/perf/capture_summary.py --compare ~/turku3d/captures/drive-01 ~/turku3d/captures/REPLAY_ID --segment 1
```

Replay: `http://localhost:5220/?bench=drive&capture=drive-01&segment=1`. Segment numbers start at 1; pose timestamps determine replay order even if final beacons arrived out of order. The starting resolution comes from the latest stats sample at or before the segment. New recordings use precise pause/resume marks, while legacy recordings use pose pause transitions. The bench retains source quality, AA, viewport, pixel ratio, time of day and population settings unless a URL override is provided. It settles the starting tiles and render resources for ten quiet seconds (errors after 180 seconds), then replays timestamp-interpolated camera and player car poses at real time. Wheels use estimated suspension/rotation because legacy poses do not contain wheel transforms.

`window.__bench` exposes `phase` (loading/settle/prepare/play/drain/done/error), `done`, `error`, `capture`, `replayOf` and `segment`. Scene failures stop and flush the active recorder. The reported measurement duration excludes the final disk flush. After completion, `await window.__bench.run('drive-01', 1)` starts another capture in the same scene. A run with another quality/AA setting needs a new page. Output meta includes `replayOf`, segment and source timestamps, so the summary automatically compares the original segment. The replay API resets seeded ambient populations, the source clock mode and sky lighting before each run, and advances `FrameClock` from the recorded timestamps; old drives without a replay snapshot use seed 20261102 and their wall-clock start. Source actor state cannot be recovered from old recordings.

The bench uses the QA render path, including deterministic traffic/walkers and a kinematic player car; it does not simulate live driving physics, input or HUD updates. Compare **replay against replay** to measure build changes. Original/live versus QA numbers describe different workloads. Viewport stats follow renderer resizes. Comparisons use the latest stats sample before each window, show source commit and render settings, and flag resolution changes or mismatches; host contention and cache state also affect results. Frame GPU timings are deduplicated by the associated renderer frame, since GPU readbacks can arrive late. JS values are sampled-time estimates, not complete call counts.

## Tile / LOD streaming trace

**Recording a tile/LOD debug session (Jonni):**
1. Open the pause menu on the local game (:5220) and switch **Performance capture: On**.
2. Close the menu and play or fly the route that shows the problem. Each time you see a late tile, a pop, a hole
   or a hitch, keep going for a few seconds so its context is in the trace.
3. Switch the capture **Off** in the pause menu and wait for "Saved as <id>".
4. Hand the id over. Anyone can then run
   `python3 tools/perf/tile_timeline.py ~/turku3d/captures/<id> --html /tmp/<id>.html` and open the page: every
   hitch is listed with the tile or band event that caused it. There is nothing else to switch on.

Every capture also records the streaming trace (`game/src/telemetry/streamTrace.ts`). Streaming code reports into
one shared trace; with no capture running each call is a single boolean check.
- **Recording:** a capture switches the trace on, gives it the camera every frame, and drains it with each batch.
- **Overhead:**
  - about 0.3 µs per event while recording;
  - at most `meta.trace.capacity` events (20,000) between 5 s drains; overflow is counted in `stats.recorder.dropped`;
  - the recorder's own cost per frame (frame hook plus trace) is in `stats.recorder.msPerFrame`; the budget is 0.3 ms.

`tiles.jsonl` holds one event per line: `{t, k, id, d, h, ...}`.
- `t` is the capture clock (ms), `k` the kind, `id` the tile or cell id.
- `d` is the horizontal distance from the camera to that square at event time, `h` the camera height.
- Timed stages are logged when they end, with `ms`; they started at `t - ms`.

Event kinds:
- **Tiles:**
  - `tile.new` and `tile.manifest`. The manifest event carries `role` (wanted, lookahead or ahead), `load`,
    `demand` distance, `status` (200, 202 with `eta`, 429 or an error), `pass`, `tris`, `bytes`, and the queue
    depth `pending`, `loading`, `preparing`, `details` and `leaving`.
  - Stages: `tile.collision` (cells, bundle), `tile.glb`, `tile.buildings`, `tile.style`, `tile.facades`,
    `tile.vegetation` and `tile.prepare` (static and shadow batches, vegetation draws, shader preparation).
  - Installs and lifecycle: `tile.preview`, `tile.full` (`replaced` preview or model), `tile.refresh`,
    `tile.cache`, `tile.restore`, `tile.hold`, `tile.release` (`ms` held, `covered`), `tile.drop`, `tile.fail`.
- **Bands:**
  - switches: `band.tile` (`to` full or cell: height-aware detail), `band.block` (`to` hd or lod), `band.props`;
  - dissolves: `band.fade` and `band.fadeEnd` (`dir` in or out, bias `tier`, `held`); `band.swap` when a model swapped at once because its dissolve twin was not compiled yet;
  - `cut.commit`, the quadtree cut: wanted, shown, hidden, outgoing, held, standby, pending;
  - `coverage` every second: `cells` is a 9x9 grid of 250 m squares around the camera, rows by z from `j0`,
    columns by x from `i0`, one character each:
    - `F` full HD, `h` HD held while its cell dissolves in, `P` collision preview;
    - `C` scenery cell, `c` cell dissolving, `U` a coarser scenery node;
    - `D` double (HD and a settled cell), `.` a hole.
- **Scenery:** `cell.catalogue`, `cell.manifest` (level, rank, standby, active, status), `cell.glb`, `cell.trees`,
  `cell.prepare`, `cell.install`, `cell.error`, `batch.merge`.
- **Collision:** `col.load` (cell, bytes, cached) and `col.unload`.
- **Graphics:** `gfx.prepare` (queue `wait`, `ms`, `compiles`, `compileMs`) and `gfx.upload` (textures drained, `ms`).

`slices.jsonl` holds one row per frame that did streaming work on the main thread: `[t, frame, parse, install,
compile, upload, scenery, lod]` in ms (`meta.trace.slices`).
- parse: GLB records applied from the decode worker;
- install: full-tile commit and draft tinting;
- compile: the synchronous part of shader preparation;
- upload: texture upload drains;
- scenery: cell styling, cut commits and batch merges;
- lod: merging block-LOD parents.

```sh
python3 tools/perf/tile_timeline.py ~/turku3d/captures/<id>                  # text summary
python3 tools/perf/tile_timeline.py ~/turku3d/captures/<id> --html out.html  # self-contained timeline page
```

The **summary** lists:
- the slowest tiles from first request to full model, with their per-stage ms;
- the slowest tiles per stage, and scenery cells;
- holes and double coverage from the snapshots, and pops (full installs that replaced a preview or nothing,
  with no dissolve);
- band event counts;
- hitches (frame interval above `--hitch`, default 50 ms) by cause, from what actually ran in the frame:
  - its measured streaming slices (`slice:*`);
  - its JS samples by class (`js:shader-build` for r186 node-program building, `js:streaming`, `js:population`,
    `js:render`, `js:other`);
  - `native` for the time with no JS sample: GPU process waits, synchronous pipeline or buffer calls.

  The largest share wins. Events near the frame are listed as context only, never as the cause. A table shows how
  often each event kind occurs in the second before a hitch against before any frame; a lift above about 1.5
  suggests a cause, and about 1 means coincidence.

The **page** shows, on one time axis:
- the frame-time strip with hitches (hover for the cause) and the stacked slices;
- the band-switch lanes;
- Gantt rows per tile and per scenery cell (stage bars and lifecycle marks; hover for fields);
- holes and doubles over time (hover for the grid);
- the camera path with hitch positions.

The recorder is tested by `game/tests/unit/stream-trace.test.ts`, the middleware by `tools/dev/capture.test.mjs`
and the report by `tools/perf/test_tile_timeline.py`.
