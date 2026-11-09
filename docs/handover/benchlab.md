# Handover: bench lab (isolated perf scenarios)

Owner until 2026-11-04: the `benchlab` loop (branch `local/loop-benchlab`). Full contract: `tools/perf/README.md`,
section "Bench lab: isolated scenarios". Code: `game/src/qa/lab/` (params, stats, ablate, overrides, lab, record).
Tests: `game/tests/unit/bench-lab.test.ts`, `tools/perf/run-bench.test.mjs`, `tools/perf/test_bench_matrix.py`.

## Modes

All modes run the real engine: the same streamer (`CoarseTiles` with its scheduler paused, tiles installed through
its own preview and detail paths via `benchInstall`), materials, preparation queue, renderer and post pipeline.
Time, weather and population seed are fixed; quality comes from the URL.

- `?bench=install[&tiles=…][&quiet=1500]`: camera fixed, scene settled, a 120-frame baseline, then each tile
  streamed preview → quiet → full → quiet. Per tile: preview/full ms, frames, longest frame, stall over the baseline,
  in-frame program builds, streaming slices, a per-stage split, long-animation-frame scripts. Plus the ten worst
  frames with their subsystem split. This is the long-frame hunting scenario.
- `?bench=static[&frames=600][&warm=120]`: load the tile set, settle, warm, measure N frames: CPU/GPU/interval
  p50/p95/p99, draws, tris, per-pass GPU, per-subsystem callback split.
- `?bench=fly[&seconds=24][&height=40][&stream=1]`: a closed scripted loop (low pass, climb, high pass, descent)
  over pre-loaded tiles; band/LOD switch counts and swap-frame times.

Common: `tiles=` (default the 3×3 around t0_0_0), `cam=` or `id=` (default the riverside street camera),
`quality=`, `aa=`, `time=`, `seed=`, `size=WxH`, `dpr=`, `record=0`, `settle=`, `settleLimit=`.

**Ablations** `off=shadows,reflection,traffic,walkers,vegetation,scenery,post,bloom,traa,lights,facades,props`
(install and fly default to `off=traffic,walkers`; `on=` re-enables).

**LOD and distance overrides** (absent = engine value; each sets an existing constant before anything reads it;
the values in effect are echoed in `result.lod`): `hd=` (block-shell switch, 280 m from tile data), `props=` (125 m),
`trees=N/M/F` (medium 50/120/650), `shadow=` and `cascades=` (medium 16/50/200), `fullr=` (window radius, 1 = 3×3),
`cells=` (scenery L0 refinement; floor 580 m), `reflect=` (reflection max distance, unlimited), `cars=`, `walkers=`.
The constant locations are in the README table.

## Running

```sh
node tools/perf/run-bench.mjs 'bench=install&tiles=t0_0_0,t0_1_0' [--repeat N] [--out DIR] [--no-trace]
python3 tools/perf/bench_matrix.py 'bench=static' --off shadows,post,bloom --repeat 3
python3 tools/perf/bench_matrix.py 'bench=static' --variants 'hd=180' 'trees=40/150/500' --repeat 3
python3 tools/perf/bench_matrix.py --results ~/turku3d/captures/bench-lab/*.json
```

`run-bench.mjs` connects to Chrome on 127.0.0.1:5223, opens a tab at `<base>?<query>`, waits for `__bench.done`,
writes `~/turku3d/captures/bench-lab/<stamp>-<label>-r<n>.json` and closes the tab. It traces the measured part, so
`result.trace` has exact GC counts, GC ms, heap freed and main-thread busy time per window and tile (in-page heap
sizes are bucketed in that Chrome). With `record` on, the capture folder also gets the result in
`meta.json` `bench.result`, so `capture_summary.py` and `tile_timeline.py` read it. `bench_matrix.py` runs a base
query plus variants and prints one table (medians over `--repeat`, deltas against the first row).

## Lessons from 2026-11-03 (apply to every comparison)

1. **Discard a warm-up run.** The first run after a page or server start is 10–25 % slow (cold HTTP and shader
   caches, JIT). Run one throwaway variant first, or drop run 1 of each series.
2. **Interleave the variants.** Run A B C A B C …, not A A A B B B: host load, thermals and cache state drift over
   minutes and otherwise show up as a variant effect. `bench_matrix.py` runs variants in sequence with `--repeat`
   inside each; for close calls, call it several times with `--repeat 1` (or loop run-bench over the variants) and
   tabulate with `--results`.
3. **Freeze the build on its own port.** The live :5220 Vite follows integration merges, so a matrix spanning a
   merge compares two builds. Serve a fixed checkout (a detached worktree with its own Vite or `serve-dist` on another
   port) and pass `--base http://localhost:<port>/`; record its commit (`result.commit`).
4. **Use a fresh tab per run.** Reusing a tab (navigating or reloading) can restore the previous page from the
   back-forward cache with its GPU memory still held, so the next run starts with less GPU memory and different
   costs. `run-bench.mjs` already opens and closes a new tab for every run; do not use `--keep-tab` for measurements.

## Hooks outside the lab files (all no-ops by default)

`CoarseTiles.scheduling` and `benchInstall` (`game/src/world/coarseTiles.ts`), `PerformanceMonitor.sample`
(`game/src/render/performance.ts`), `facadeModuleSwitch` (`game/src/render/facadeModules.ts`), `BLOCK_LOD`
(`game/src/world/buildingPlacements.ts`), `TILE_WINDOW` (`game/src/world/coarseRegion.ts`), `SCENERY_CELLS` and the
detail floor following `TILE_DETAIL` (`game/src/world/sceneryView.ts`), `REFLECTION_RANGE`
(`game/src/render/reflections.ts`), and the `lab` branches in `game/src/core/params.ts` and `game/src/main.ts`
(`runLab`).

## Open

- Fly's trace window can be missed when tracing starts late: its warm-up is capped at 60 frames
  (`Math.min(lab.warm, 60)` in `runFly`, `game/src/qa/lab/lab.ts`); raise the cap if fly GC counts matter.
- `game/tests/unit/streaming-resources.test.ts` has two failures on integration that predate the lab.
