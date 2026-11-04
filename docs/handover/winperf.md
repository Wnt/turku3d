# winperf handover (2026-11-04)

Goal: Jonni's Windows machine (MacBookPro15,1 under Boot Camp: i9-8950HK, Radeon Pro 560X 4 GB, 2880x1800, Chrome 156,
full screen) reaches the Mac goal: about 40 FPS mean, no slow frames, only HD LOD in view. Working notes and every run
id: `~/turku3d/loops/winperf/STATUS.md` (local).

## Tiering design (game/src/render/quality.ts)

- **Tiers:** mobile, low, `reduced`, medium, high. `reduced` is medium's look for lower-end GPUs. Its changes:
  - trees switch to impostors at 100 m instead of 180 m;
  - the river uses the screen-space reflection instead of the planar pass (water W1);
  - the drawing buffer is capped at 1920x1080 instead of 2560x1440 (`maxPixels`);
  - the crowns are `spread` (lidartrees);
  - it has the governor (`governor: true`) and stream2's GPU budget.

  Simulation never changes: parked bodies and the traffic and walker caps are medium's.
- **Detection:** `deviceQuality()` in main.ts runs before the renderer exists.
  - AMD `gcn-*` and Intel `gen-*` adapters go to `reduced`.
  - Apple GPUs go to `reduced` only if both hold: the startup probe takes over 3.5 ms
    (`APPLE_REDUCED_PROBE_MS`), and `navigator.hardwareConcurrency` is 8 or fewer (`APPLE_BASE_MAX_CORES`).
  - Measured probe times: M1 Max 1.76-1.86 ms on 10 cores; M1 Air 5.96 ms cool and 7.01 ms throttled on 8 cores.
    A loaded M1 Max can read above 3.5 ms, but it never demotes because of its core count.
  - NVIDIA, RDNA and unknown core counts keep medium. `?quality=` always wins. QA, the bench lab and recorded replays
    keep their deterministic tier.
- **Probe (render/gpuProbe.ts):** fixed FMA compute work timed with timestamp queries.
  - On Apple it runs on a short-lived device before the renderer: the fastest of 5 dispatches, and a slow first read
    is re-probed twice after 150 ms pauses.
  - Elsewhere it runs after start and is only recorded.
  - On the 560X it read 2.62-3.54 ms between sessions (±35 %).
- **`renderTier()`:** returns `{tier, source, gpu, cores, probe}`, the interface for stream2's residency.
  Captures record it as `meta.tier`.
- **AdaptiveResolution:** still targets 60 FPS on desktop, so every desktop sits at the 0.5 floor; on Windows reduced
  that is pixel ratio 0.474, a 910x569 buffer. Six consecutive frames over 80 ms now step the resolution down. Before
  that fix, a 4 GB card thrashing at 1440p (200 ms frames) never adapted. `?resolutionTarget=40` is an A/B.
- **Setting governor (render/settingGovernor.ts):** for reduced and low only.
  - It acts when 5 s windows stay slower than 35 FPS at the resolution floor: two windows in a row, at most one
    change per 10 s.
  - The steps, in order: distant city to 2500 m, trees to impostors sooner (near 35 m, mid 70 m), small casters drop
    their sun shadows.
  - Six windows faster than 46 FPS undo the last step. Medium and high never change at runtime. `?governor=0` is the A/B.
- **Back-forward cache:** a game page entering the bfcache destroys its WebGPU device and reloads on return. Before
  this, a navigated-away game page held 2-3 GB of VRAM, and the next page paged about 20 ms per frame.

## Windows results (live full-screen drive replay, `20261103-144904-0rb85a` segment 1, fresh tab per run)

| Build | Tier | FPS | >50 ms frames / 96 s | CPU p50 | HD coverage (mean / window) |
|---|---|---|---|---|---|
| c30d96af | medium | 29.8 | 285 | 18.2 ms | 64 / 80 % |
| c30d96af | reduced | 33.1-34.5 | 220-238 | 17.7 ms | 66 / 81 % |
| e462ffee+ (integration HEAD) | reduced | **41.2** | **about 150** | 14.4 ms | **60-62 / 76-78 %** |

- The reduced row at HEAD is the mean of 4 runs (2 with pools on, 2 off).
- The rise from 33 to 41 FPS comes from the other agents' CPU work between c30d96af and HEAD.
- The Mac reference is 40-42 FPS with 18-27 slow frames and 91-92 / 98 % coverage.
- **Pools on (perf3):** Chrome's GPU-process dedicated memory peaks at 2541 MB instead of 3207 MB, and the whole card
  at 2822 MB instead of 3519 MB, with FPS unchanged. A static soak and three drive replays with both pools on had no
  Device Lost and no TDR (Display event 4101).
- **Bottleneck now:** the main thread (trace: renderer main thread busy 7.3 of 8 s; Chrome's GPU process about 45 %).
  The GPU frame span exceeds the pass sum because the GPU waits while the CPU encodes.

## A/B outcomes on Windows

- `tileShadows=chunks`, `bundles=1`, `drawSort=material` and `resolutionTarget=40` stay within noise (±1 FPS,
  ±30 slow frames, plus about +1.4 FPS drift over a session). Re-run a baseline at the end of every series.
  - chunks saves 165 MB of GPU memory, bundles 90 MB.
  - `resolutionTarget=40` keeps the 0.474 floor on Windows, so it changed nothing there.
- **Governor:** at HEAD it never triggers (about 24 ms frames), so on and off both give 40.0 FPS. Under 2x CPU throttling
  it applies all three steps: FPS stays 18.0 against 18.0, CPU -1.6 ms, slow frames -11 %. Not yet measured on the Air.
- **Water W1, reduced:**
  - No WGSL or validation errors.
  - The planar reflection pass (1.8 ms across the river, 5.1 ms along it) is gone.
  - Along the river: 55.2 FPS against 46.6 for planar, and beauty p95 11.8 ms against 18.2.
  - Look: fixed (1e5cc203, a0ebdee9) and judged on 2026-11-04 (water.md): SSR is as close or closer to the Aura references than planar.
- **Leaves, earlier probes:** single-sided -1.3 ms, unshadowed -1.0 ms, alpha test 0.65 -0.4 ms, texture bias about 0.
  Decided 2026-11-04 (treelook, blind judges against Street View): none ships and `?leaves=` is removed. Single-sided
  cards and alpha 0.65 thin the crowns visibly; `noshadow` drew pixel-identical frames in the bench lab.
- **Crowns (lidartrees), riverside static, round 1 only (stopped at the wrap-up):**

  | Tier | Mode | Beauty p50 | Leaf cost |
  |---|---|---|---|
  | medium | asset | 16.0 ms | — |
  | medium | spread | 16.1 ms | 5.5 ms |
  | medium | stretch | 17.2 ms | 6.7 ms |
  | reduced | asset | 9.9 ms | 1.6 ms |
  | reduced | stretch | 10.35 ms | 1.8 ms |
  | reduced | spread | 10.1 ms | 1.5 ms |

  Stretch costs about +1.2 ms of beauty at medium; on reduced the modes are within 0.45 ms (910x569 against 1214x758
  explains the smaller reduced numbers). The medium `asset` run's leaf cost was not measured (a matcher bug in its run).
- **Night venue** (Paavo Nurmi, 22:00, reduced): 59.9 FPS (vsync cap), GPU 15.8 ms, beauty 10.8 ms. The fog
  variant changed nothing measurable.

## Running a Windows series

- **Host:** `ssh jonni@192.168.1.250` (key auth). Jonni's session is 1.
  - Hold `C:\Research\gta\game.lock` = `winperf <ISO> <minutes>` while measuring.
    Use `win/lock.ps1` to take it and `win/unlock.ps1` to release it (which also lists Chrome processes).
  - GTA must not run during measurements.
- **Chrome:** a one-shot scheduled task (`schtasks /create /tn T3DChrome /sc once /st 00:00 /it /ru jonni`, deleted at the
  wrap-up; recreate it) runs `C:\Research\turku3d\chrome.cmd`:
  - a separate profile `C:\Research\turku3d\chrome-profile`;
  - `--remote-debugging-port=9222 --start-fullscreen`, opening `about:blank`.

  Tasks started over plain ssh land outside the desktop session. A second task, T3DAwake, running `awake.ps1` hidden
  through `C:\Research\gta\hidden.vbs`, keeps the PC awake for 90 min (it slept once mid-series).
  The PowerShell helpers are in `~/turku3d/loops/winperf/win/`.
- **Tunnels:** the coordinator owns Mac :9322 to Windows :9222, plus :5240 and :5220. Serve your branch on your own Vite
  (127.0.0.1:5250-5259) and add your own `ssh -R 5250:127.0.0.1:5250`.
  - Start Vite with `env -u VITE_PUBLIC_RELEASE` (otherwise the drive bench refuses), `TURKU3D_COARSE_PROXY=http://127.0.0.1:5225`
    and your own `TURKU3D_WORK`.
- **Scripts (`~/turku3d/loops/winperf/`):**
  - `bench.mjs`: replays a drive in a NEW tab, closed afterwards (navigating one tab kept old game pages in the
    bfcache). It samples stream2's residency meter, writes `gpu-end.json` and supports `BENCH_CPU_THROTTLE`.
  - `memrun.sh <tag> <query> [trace]`: a replay plus 1 Hz Windows GPU memory counters (`win/gpumem.ps1`) and an
    optional light trace (`trace.mjs`, categories gpu, viz, toplevel).
  - `table.py <tags>`: FPS, p95/p99, slow frames, CPU/GPU, beauty, HD coverage, pixel ratio, peak VRAM, 5xx errors.
  - `memalign.py`, `resalign.py`: align the memory counters and the residency meter with the frame times.
  - `probe.mjs <tab> <steps.json>`: in-page A/B on a loaded tab (hide families, mutate `QUALITY`, set pixel ratio).
  - `soak.mjs`, `waterrun.mjs`, `crownrun.mjs`: static scenario runners with console/error capture.
  - Run `win/tdr.ps1` for TDR events.
  - Always discard one warm-up run, interleave variants and finish with a baseline control.
- **Pitfalls:**
  - A run with tile 5xx errors (proxy restarts) does not count.
  - Static loads are 2-12 min on Windows.
  - `?view=live` is required for real full-screen play; the default replay pins the Mac's 1728x941 at 0.7528.

## Open items (ranked)

1. **Coverage on Windows:** HD coverage is 60-62 % / 76-78 % against the Mac's 91 / 98 %. stream2's 0.8 GiB budget
   reads "over" through the whole drive while about 1.25 GB of VRAM is free. Its meter rounds every buffer up to 64 KiB,
   which pooled buffers no longer pay. Meter pooled bytes and raise the reduced budget to about 1.2 GiB.
   The Air needs its own number, from total memory rather than GPU bytes.
2. **Slow frames:** about 150 over 50 ms per drive against the Mac's 18-27. The main thread is the limit: per-object
   culling, uniform packing, draw submission and binding updates in three.js (perf3), plus shader-build hitches.
3. **A 40 FPS adaptive-resolution target on reduced (airperf):** with the 60 FPS target every desktop sits at the
   floor. A goal-based target would only lower resolution when needed. Measure on the Air and the 560X with
   `?resolutionTarget=40`. The Mac must stay on 60 unless Jonni decides otherwise.
4. **Governor on the Air:** measure thermal-throttled sessions with and without `?governor=0`. On Windows it is neutral.
5. Remove the decided A/B paths within ten merges: `resolutionTarget`, `governor`.
