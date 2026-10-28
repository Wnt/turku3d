## Result: DONE

Implemented and measured on the M1 MacBook Air. The matched two-minute drive sustains approximately 60 Hz,
with p95 17.7 ms and no frames over 100 ms. Streaming flight improves substantially but still has shader-build
hitches: p95 34.2 ms, worst frame 451 ms. This does not establish uninterrupted 60 fps during streaming.

## What changed

- Play resolution now follows measured frame delivery: a 30-frame window, 60 Hz target, one-second downward
  hysteresis, eight-second upward probes and a 0.5 scale floor relative to the existing 1440p pixel ceiling.
  QA remains deterministic at DPR 1.
- All glTF loaders and final ground bindings share a largest-first 2 MiB / 2 ms texture-upload queue.
  Publication waits for uploads; an oversized texture runs alone because three.js uploads are atomic.
  Reusing a ground library no longer invalidates and reuploads unchanged array textures.
- Streamed models prepare their final prepass, beauty and river contexts before publication. Preparation shares
  a 2 ms frame budget between atomic draws. The native processTileModel hook preserves glTF ownership and
  REPLACE parents; aborted preparation releases both styled additions and original resources.
- Separate opaque/cutout shadow override materials prevent r186 alpha-test toggles from invalidating every
  shadow caster's program key each frame. Depth contexts stay stable across beauty and reflection passes.
- Shadow proxies are partitioned into compact 62.5 m chunks with complete triangle bounds. Whole triangles,
  transforms and silhouettes are retained. Compact buffers avoid duplicate full-tile LRU estimates.
- River reflections reuse the primary camera's CSM maps, refresh at 20/30/60 Hz by quality and render large
  static surfaces/sky. Vegetation, actors and small props are excluded from reflections. The primary view retains
  its material maps, mip chains, shadow resolution and vegetation settings.
- Scene transforms update once per frame across render passes. Vegetation retains stable membership/order,
  reusable selection buffers and instance matrices; shadow/ground batching fills final typed buffers directly.

Matched Air comparison (nearest-rank percentiles, continuous independent RAF log):

| Scenario | Before p50 / p95 / max (ms) | After p50 / p95 / max (ms) | >100 ms frames/min, before → after |
|---|---:|---:|---:|
| 75 s Aura flight | 33.32 / 51.25 / 1134.37 | 16.73 / 34.19 / 451.17 | 20.79 → 12.00 |
| 120 s actual car drive | 33.33 / 35.32 / 116.35 | 16.67 / 17.65 / 49.99 | 1.00 → 0.00 |

The flight moves streaming focus along both banks between Teatterisilta and Myllysilta. Its maximum interval
includes streaming, unlike post-capture CDP serialization stalls, which are excluded by phase. The car uses
actual worker physics and identical waypoint/steering code; it travels 236.5 m before and 238.2 m after, ending
against a building near x=-33,z=365. The reachable street graph does not complete the whole river corridor.
Both successful runs have no page/console errors. Steady drive end-window CPU p50 falls 29.0 → 14.5 ms and
submitted geometry 8.79M → 5.12M triangles; these are end-window samples, not whole-route decompositions.

| Physics publication age | Before p50 / p95 / max (ms) | After p50 / p95 / max (ms) |
|---|---:|---:|
| Flight | 9.44 / 16.84 / 589.36 | 8.48 / 15.89 / 174.28 |
| Drive | 12.55 / 23.98 / 72.17 | 10.77 / 17.48 / 38.10 |

Both sides already contain fix-physics-latency's shared transport. Publication timestamps, rather than state
message receipt or recorder RTT, measure age. Shared transport sends no per-frame state messages.

During flight, shadow key checks fall 682,933 → 649; during the drive 585,620 → 12. Flight allocation sampling
falls 145.4 → 82.9 MB/s (43%). Sampling covers recorder intervals 89.7/91.4 s, including capture overhead;
it is diagnostic and is not a pure steady-engine allocation benchmark. Page-reported heap first→last is
697.1→856.4 MiB before and 1093.2→1228.5 MiB after the flight; drive growth is 73.0→37.4 MiB.
Capture pose retention, assets and GC phase affect these snapshots; they do not establish a memory-leak rate.
The remaining 358–367 ms animation tasks in the final flight trace are dominated by synchronous TSL construction.

Baseline is main 104343661aed4b6a5d163da4ab4fa716697eff45, served from the sandbox source archive. After is
renderer commit ef239d10 (identical game tree to the captured local 3683511); final documentation/cascade-initialization guards leave the measured
steady path unchanged. Subsequent main rebases contain no game-source changes relative to that baseline.
Both are Vite dev builds, WebGPU, medium, fixed 2026-10-15T11:00:00Z, 1600×913 CSS at DPR 2. Before's output is
2541×1450; after adapts through 1779×1015 to 1270×725, with 1397×797 probes. The lower output is softer: no
claim of identical pixel sharpness is made. Material/silhouette inspection uses retained local screenshots.

Published data is an APFS clone of release 10434366, not a link following live. The tileset SHA256 is
`d5016df5694e667b9d7e5d2fba34cdadce4642a78eecffda4e5858d6475cce3a`; manifest SHA256 is
`1b17eab5c02182939bd2bcbc4aeef2da3f45777e6bad85733543bfb32582ba9d`.
Both use the same frozen coarse snapshot (t0_0_2, t0_0_3, t0_1_2, t0_1_3) and read-only draft service.
Earlier captures are superseded diagnostics and are not used in this comparison. A temporary shadow-buffer
accounting regression caused blank QA refinement; compact chunk buffers fixed it before the accepted pair.

Real-data headless inspection covered t0_0_0 and t0_0_1 in top-down and oblique views. Each residency audit
checked 2,728 chunks from 36 shadow-bearing models among 47 resident models: all 711,722 triangles preserved,
zero vertices outside their chunk spheres, nonempty scene output. All four images were inspected. Screenshots,
traces, heap profiles, raw frame logs and input hashes remain under `$TURKU3D_WORK/reports/air-render/`.
Accepted captures are `air-main-before/` and `air-verified/`; `shadow-sanity-verified/audit.json` holds geometry counts.
No city/street imagery is committed.

## How to verify

```sh
source ../sandbox.env
scripts/dev/gate --fast --base main
make render-perf
make render-perf-dev
TILE_DIST="$TURKU3D_WORK/air-inputs/published" npm --prefix game run dev -- --host 127.0.0.1 --port 5244
```

Use the exact Air acquisition, read-only draft service and capture commands in
`tools/perf/air-scenarios/renderer.md`. Run `run.mjs CDP URL OUTPUT both 120`, then:

```sh
node tools/perf/air-scenarios/summarize.mjs "$TURKU3D_WORK/reports/air-render/air-verified" flight
node tools/perf/air-scenarios/summarize.mjs "$TURKU3D_WORK/reports/air-render/air-verified" drive
python3 scripts/dev/host_slots.py browser -- node tools/perf/air-scenarios/shadows.mjs \
  http://localhost:5244/ "$TURKU3D_WORK/reports/air-render/shadow-sanity"
```

The supplied recorder's injected probe had invalid template escaping. Captures use a sandbox copy with the probe
changed to String.raw and compile-checked; set `TURKU3D_RENDER_RECORDER` to that copy. External recorder files were
not modified. The independent RAF log also avoids the recorder's missing interval across per-second polls.

## Gate output

`scripts/dev/gate --fast --base main`: GREEN; final rebased run 4.150 s (cached repeat 1.616 s). The preceding uncached run was GREEN
143.938 s at host load over 200; the earlier renderer gate was GREEN 19.373 s. No checks were weakened.
The gate covers lint/format policy, Chrome launch policy, generated contracts, type checks and unit tests.
New pure synthetic tests cover resolution hysteresis, upload admission/errors, native tile ownership/abort,
render-state restoration, shadow cache invalidation, chunk bounds and stable vegetation updates.

`make render-perf`: 4 passed, 1.8 min, production build before final compact-buffer cleanup. Final production
retry waited for a build slot behind integration reservations and was canceled without bypassing admission.
The final dev source uses the same acceptance tests and backend budgets through the browser scheduler:

| M1 Max, synthetic 1440p path | CPU p50/p95 (ms) | GPU p50/p95 (ms) | Frame p50/p95 (ms) | Drained ms/frame |
|---|---:|---:|---:|---:|
| Final WebGPU | 1.25 / 2.99 | 2.17 / 2.29 | 1.77 / 3.93 | 2.67 |
| Final WebGL2 | 1.14 / 1.51 | 8.55 / 12.37 | 19.10 / 19.77 | 19.07 |

Both backend performance/functional tests passed with unchanged 16.6/22 ms budgets. An initial dev run at
host load 224 passed WebGL2 but failed WebGPU's cold outside diagnostic (19.5 s versus 15 s) and 90 s QA-ready
wait. Retesting only those failures with the retained Vite cache passed both in 11.9 s, host load 124.
The final WebGPU rerun uses `playwright.render-dev.config.ts`; final WebGL2 used the equivalent inherited
config in the sandbox. `make render-perf-dev` retains the configuration for repeatable dev-source checks.
These unlocked synthetic throughput measurements are separate from the headed, vsynced Air results.
All benchmark logs and per-commit JSON remain in the sandbox. Air and owned dev/draft servers were stopped.

## Follow-ups

- Remove remaining synchronous TSL construction bursts during streamed publication; the matched flight still
  reaches 451 ms. Oversized individual texture uploads and shader draws remain atomic.
- Validate sharper reconstruction and reflection vegetation on the Air before expanding the reflected scene.
- Extend the automated driving graph beyond its reachable riverside component to measure the complete corridor.
