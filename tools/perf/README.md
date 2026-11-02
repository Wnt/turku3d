# Local performance captures

Open the local pause menu and select **Performance capture: On**. The red REC marker remains visible while driving and paused. Select Off to flush and detach the recorder. The selection survives reloads in the same tab. Published builds do not expose capture.

Vite dev, Vite preview and `tools/dev/serve-dist.mjs` append capture files under `~/turku3d/captures/` (`TURKU3D_CAPTURES` overrides it). Their HTML responses allow JS Self-Profiling. Existing servers need a config reload/restart to pick up the middleware/header; do not restart someone else's server. Unsupported profiler APIs are recorded in `meta.jsProfiler`; frame/pose capture continues. `window.__game.capture.recorder` exposes the active recorder; `lastRecorder.error` and `lastRecorder.flush()` allow inspection/retry after Off. Keep the page open until final delivery completes. Page exit uses best-effort beacons, subject to Chrome's aggregate 64 KiB limit.

Core files match the coordinator recorder: `meta.json`, `frames.jsonl` (one array of frame rows per batch), and individual `poses.jsonl`, `stats.jsonl`, `loaf.jsonl`, `marks.jsonl` rows. Additional `js.jsonl` rows contain `{t,end,trace}` with native profiler resources/frames/stacks/samples. `network.jsonl` holds resource response timing/bytes (capture POSTs excluded; cross-origin sizes may be hidden by Resource Timing policy).

```sh
python3 tools/perf/capture_summary.py ~/turku3d/captures/drive-01 --segment 1
python3 tools/perf/capture_summary.py ~/turku3d/captures/REPLAY_ID
python3 tools/perf/capture_summary.py --compare ~/turku3d/captures/drive-01 ~/turku3d/captures/REPLAY_ID --segment 1
```

Replay: `http://localhost:5220/?bench=drive&capture=drive-01&segment=1`. Segment numbers start at 1; new recordings use precise pause/resume marks, while legacy recordings use pose pause transitions. The bench retains source quality, AA, viewport, pixel ratio, time of day and population settings unless a URL override is provided. It settles the starting tiles and render resources for ten quiet seconds (errors after 180 seconds), then replays timestamp-interpolated camera and player car poses at real time. Wheels use estimated suspension/rotation because legacy poses do not contain wheel transforms.

`window.__bench` exposes `phase`, `done`, `error`, `capture`, `replayOf` and `segment`. After completion, `await window.__bench.run('drive-01', 1)` starts another capture in the same scene. A run with another quality/AA setting needs a new page. Output meta includes `replayOf`, segment and source timestamps, so the summary automatically compares the original segment. The replay API resets seeded ambient populations and advances `FrameClock` from the recorded timestamps; old drives without a replay snapshot use seed 20261102 and their wall-clock start. Source actor state cannot be recovered from old recordings.

The bench uses the QA render path, including deterministic traffic/walkers and a kinematic player car; it does not simulate live driving physics, input or HUD updates. Compare **replay against replay** to measure build changes. Original/live versus QA numbers describe different workloads. Recorded viewport/DPR and source commit appear in comparisons; host contention and cache state also affect results. Frame GPU timings are deduplicated by the associated renderer frame, since GPU readbacks can arrive late. JS values are sampled-time estimates, not complete call counts.
