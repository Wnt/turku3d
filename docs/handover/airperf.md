# airperf handover: performance on Jonni's M1 MacBook Air

Status on 2026-11-04 01:30. The full log with every table is `~/turku3d/loops/airperf/STATUS.md`, and the scripts are
in `~/turku3d/loops/airperf/`. Screenshots and heap snapshots stay local there and never go into git.

## The machine

- **Model:** MacBookAir10,1 (M1, 8-core GPU, 8 GB), Chrome 156, on AC power.
- **Display:** 1680x1050 scaled screen. The air-session window is 1600x913 CSS at DPR 2.
- **WebGPU adapter:** `apple` / `metal-3`, the same as the M1 Max. Only the startup GPU probe tells them apart:
  - Air: 5.96 ms cool, 6.0-6.2 ms warm, 7.0 ms under heavy thermal pressure;
  - M1 Max: 1.8 ms.
- **Background load:** Jonni's own apps stay open (Claude.app about 0.9 GB, ChatGPT/Codex about 0.6 GB, his Chrome).
  Before the game starts the Air is already at 25-35 % free memory with 4-8 GB of swap in use. Measure it as he uses it.
- **Thermals:** the Air is fanless and throttles under sustained load. Thermal pressure goes to 1 (moderate) about
  18 minutes in and to 2 (heavy) after about 30.
  - On medium this costs 15-20 % FPS and doubles the slow frames: the same replay ran at 35.6 FPS early and 30.4 FPS
    after 50 minutes.
  - On reduced it costs about 9 %: 55 FPS falls to 50.

## Running Air sessions

1. **Address and lock.**
   - The Air's address changes with DHCP: it was .55, now `192.168.1.22`. `~/turku3d/profiling/air-session` defaults to
     the current one; override it with `TURKU3D_AIR=jonni@<ip>`. It is also reachable as `Jonnis-MacBook-Air.local`.
   - Start a session:
     `air-session start <task> --game-port <P> --cdp-port 9350 --url /manifest.webmanifest`.
     This takes the lock, reverse-tunnels the Mac's `127.0.0.1:P` to the Air's `localhost:P` (a secure context for
     WebGPU) and opens a headed Chrome whose CDP lands on the Mac's 9350.
   - Never touch Jonni's own Chrome.
   - Stop the session after every series with `air-session stop <task>`.
2. **Start on a cheap page, never the game.** `--url /manifest.webmanifest` does this.
   - Chrome's back-forward cache keeps a navigated-away game page alive, WebGPU device and all (2-3 GB of GPU memory).
   - 1d35ad17 now releases the device on pagehide, but the bench scripts should still not reuse game tabs.
   - My first baseline was contaminated this way.
3. **Use a bfcache-safe bench.**
   - Drive replays: `~/turku3d/loops/airperf/bench2.mjs` (winperf's fixed bench). It opens a new tab per run, closes
     the other game tabs, and keeps the tab with `KEEP=1` for the probes.
   - Lab scenes: `tools/perf/run-bench.mjs ... --cdp http://127.0.0.1:9350 --base http://localhost:P/ --keep-tab`.
   - `series3.sh` / `seriesM.sh` wrap both. Per series they run one session, caffeinate, the memory sampler and a
     discarded warm-up run. After each run they collect `memprobe.mjs` (`__render.memory()`, `renderer.info.memory`,
     the governor log), `vegprobe.mjs` (a per-pass draw census, retried until it catches a reflection frame) and
     `shot.mjs` (taken after a 2 s settle).
4. **Cool down before every series and log thermals.**
   - `cool.sh` waits for `notifyutil -g com.apple.system.thermalpressurelevel` to read 0 in three consecutive 30 s
     samples. That command needs no sudo.
   - Log the same value every 30 s during a series.
   - Interleave the A/B variants (A B A B or A B ... B A) and repeat the first configuration at the end.
5. **Memory.** `memsample.py` runs on the Air through `ssh ... /usr/bin/python3 -` and records every 5 s:
   - `kern.memorystatus_level` and pressure;
   - swap use and swap-ins and swap-outs;
   - per-process `phys_footprint` from `footprint -p`.

   `ps -o rss` under-reports Chrome's GPU and renderer processes by 5-10x, so use the footprint. Analyse with
   `memcorr.py` (hitches against swap rate), `drivetable.py`, `censuscmp.py`, `livetable.py` and `labtable.py`.
6. **Serving your own build.**
   - Run Vite on 5260-5269 with `env -u VITE_PUBLIC_RELEASE`. The shell exports `VITE_PUBLIC_RELEASE=1`, which turns
     off captures and the drive bench ("Drive bench is local only").
   - Point `TURKU3D_COARSE_PROXY` at `http://127.0.0.1:5225`.
   - Check `meta.build.loaded` in each capture.
7. **Heap snapshot.**
   - Replay in your own tab (KEEP=1), then run
     `node ~/turku3d/wt/loops/stream2/repo/tools/memory/heap-snapshot.mjs take --cdp http://127.0.0.1:9350 --url bench=drive --out DIR --no-summary`,
     and `summarize` it on the Mac.
   - Its `--open` drops everything after the first `&`: it uses `encodeURI` in `/json/new`, so the URL needs
     `encodeURIComponent`.

## Latest numbers: drive replay `20261103-144904-0rb85a` segment 1

| Build, mode | FPS | >50 ms | HD mean / window | Renderer / GPU process footprint | Swap in / out per 96 s |
|---|---|---|---|---|---|
| 6098c5bd medium recorded (baseline, bfcache-contaminated) | 36.0 | 119 | 77 / 93 % | 6.2 / 5.3 GB | 3.4 / 5.2 GB |
| c30d96af medium recorded, cool | 35.6 | 160 | 70 / 87 % | 4.7 / 3.4 GB | 2.3 / 3.2 GB |
| 53efadee medium recorded (memory rounds 1-3) | 38.5-38.9 | 111-115 | 79-81 / 94-95 % | 4.5 / 3.4 GB | 0.6-0.8 / 1.3-1.6 GB |
| 53efadee medium, first run while cool | 44.4 | 37 | 85 / 97 % | 4.4 / 3.5 GB | 0.7 / 1.4 GB |
| 9f915859 live (auto-detects reduced, source device) | 51-55 | 52-58 | 71-75 / 89-92 % | 3.3-3.4 / 2.6-2.9 GB | 0.2-1.0 / 0.1-1.1 GB |
| 9f915859 live, after 20+ min under heavy thermal pressure | 49.5-50.7 | 64-72 | 69-71 / 87-89 % | 3.2-3.4 / 2.8 GB | 0.6-0.8 GB |

- **Live resolution:** live play on the Air sits at AdaptiveResolution's floor the whole run.
  - Medium: 0.794 ratio, 1271x725 (0.92 MP, the same as the recorded replay).
  - Reduced: 0.596 ratio, 954x544 (0.52 MP).
  - The 60 FPS target keeps it at the floor even at 55 FPS. `resolutionTarget=40` did not lift medium either, because
    the Air never holds 40 FPS for 8 s.
- **Governor:** it never stepped on reduced. Its 35 FPS slow line is never reached, so `governor=0` is identical.
- **GPU budget:** reduced's 1 GiB Apple budget reads over the limit in every run (945-991 of 1024 MiB).
  `gpuBudget=0.8` costs about 2 HD points and saves no swap.
- **Static ablations** (6098c5bd riverside, cool, 1301x708; the scene is vsync-capped, so read ms, not FPS):
  - shadows off: CPU -4.5 ms and -181 draws;
  - reflection off: GPU p95 -8 ms;
  - distant city off: CPU -3 ms and -2.4 M tris;
  - vegetation off: beauty -1.4 ms;
  - `quality=low`: CPU -2.4 ms and about -0.5 GB of footprint.

  None of them moved the JS heap.
- **A/Bs closed on the Air:**
  - `tileShadows=chunks`: +122 draws, -1 ms shadow GPU. The default is kept.
  - Water `quarter`: -0.45 ms per reflection frame, -0.1 ms averaged, and visibly blocky.
  - Water `lod`: no change.
  - `nodepth` (bank fade): costs about 0.1 ms.
  - `oldsun`: no measurable cost.
  - `crowns`: asset, stretch and spread are all within 0.1 ms on the riverside beauty pass; stretch and spread add 4 %
    vegetation triangles.
  - Tree fade: no measurable cost.
- **Visual checks:**
  - No stipple in settled frames since 59105dd1.
  - No TRAA wander since 62296075.
  - No missing geometry with the array release, compared against `keepArrays=1`.
  - No black textures on medium or reduced.

## Memory: heap snapshot at the end of a medium replay (53efadee)

- **Page totals:** 981 MiB reachable. 807 MiB of it is ArrayBuffer backing stores in 31,546 buffers; JS objects are
  only about 125 MiB.
- **Census:** `__render.memory()` covers about 540 MiB.
- **Workers:** `exposure.worker.ts` holds **448 MiB** of JS heap, the largest worker.
- **ArrayBuffers by the path that holds them:**
  - `geometry.index` 125 MiB;
  - three's RenderObject `vertexBuffers` 106;
  - scene attributes 108;
  - position 72, normal 52, uv 32, color_1 17;
  - AssetCache promises (raw bytes) 59;
  - walker VAT data 31;
  - instance matrices 20;
  - lights interleaved buffer 19;
  - drawnSets index 18;
  - texture mipmaps about 65.
- **Retainers:**
  - `MaterialStyler.owned` keeps **102 MiB** through `restoreSurfaces` closures holding `merged` arrays;
  - **386 PMREMGenerator / 385 PMREMNode** instances (41 MiB), which looks like a leak;
  - three AssetCaches hold 59 MiB.
- **Medium versus `keepArrays=1` (c30d96af):** the array release saves 1.0-1.2 GB of renderer footprint and
  1.0-1.4 GB of JS heap.
- **Round 3 against c30d96af:**
  - collision ground index 84 -> 20 MiB;
  - collision previews 36 -> 0 MiB;
  - texture source data 66 -> 55 MiB, with 499 MiB released;
  - page JS 1.22 -> 1.05 GB.
- **Memory still to cut,** largest first:
  - the exposure worker (448 MiB);
  - index arrays kept for raycasts (125);
  - MaterialStyler restore copies (102);
  - RenderObject vertex buffers (106);
  - mip copies (65);
  - AssetCache bytes (59);
  - the PMREM generator leak (41);
  - VAT (31).

## Open items, ranked

1. **Reduced on the Air has spare frame time but low resolution and coverage.** It runs 50-55 FPS against the 40 goal,
   but at 0.52 MP and HD 70-75 %.
   - Aim AdaptiveResolution at 40 FPS on reduced (winperf), so the headroom goes to sharpness.
   - Revisit the 1 GiB Apple GPU budget, which now limits coverage, not swap (stream2).
2. **Thermal drift is the largest sustained effect on medium.** The governor exists for it but only runs below medium,
   and its 35 FPS line never fires on reduced. Re-check medium against reduced after 30 minutes of play.
3. **Memory targets** above, starting with the exposure worker and the MaterialStyler restore copies (stream2).
   Investigate the 386 PMREMGenerators (render).
4. **Fix `heap-snapshot.mjs --open`** (`encodeURIComponent`).
5. **Repeat the medium `keepArrays` frame A/B cool.** The c30d96af pair was confounded by throttling; the memory result
   is clear.
