# Handover: stream2 (tile streaming, budgets, memory)

State on `local/loop-integration` as of 2026-11-04 (last merge 27b7bf15). Loop notes: `~/turku3d/loops/stream2/STATUS.md`.
Measurements are by the coordinator, airperf (M1 Air), winperf (Radeon Pro 560X) and perf3; stream2 ran no browser.

## Streaming budgets (main-thread work per frame)

- `game/src/render/preparationBudget.ts`. All tile work (JSON and GLB record replies, building reads and clones,
  facade packs, static batches, collision previews, GPU measurement) runs in `PreparationBudget` windows from
  `streamingBudget()`, which resumes stale windows on a new task.
- `StreamingAllowance` is an interval controller. Its targets are a 24 ms frame interval while the 3x3 window lacks
  full tiles (`urgent`) and 20.5 ms otherwise.
  - The total streaming time per frame adapts by `0.1 x (target - interval)` and is clamped to 0.5..6 ms (12 ms
    urgent). Each window gets `total / windows` (at most 3 ms, 6 ms urgent), with a floor of 0.5 ms (2 ms urgent).
  - A CPU-caused long frame (interval > 1.6 x target) halves the total.
  - GPU-bound frames (idle >= 2.5 ms and GPU time >= 0.8 x interval) steer on main-thread idle instead, keeping a
    4 ms margin. Idle comes from `MainThreadIdle` (requestIdleCallback, each idle period counted once, capped at
    interval - callback CPU).
  - With no frame for more than 100 ms, the window falls back to 2 ms.
- Install order (`game/src/world/coarseTiles.ts`), detail loop:
  - The current 3x3 window always comes first.
  - Lead tiles are full installs started by arrival time (28 s or 450 m, up to 700 m; below 40 m/s), and only
    after a 1.5 s dwell with at least 4 s predicted residence.
  - The view wedge reaches out to 400 m; the camera is predicted 8 s ahead with a 75 m hysteresis.
  - Above 40 m camera height, full installs stay within 250 m (3D distance); collision previews still show there.
  - Tiles off the path or the view for 2 s are abandoned. Collision previews are unconditional.

## GPU and memory residency per tier

- Meter: `game/src/world/gpuBytes.ts`. With the renderer record (`rendererResidency`) only uploaded content counts:
  - a pooled vertex/index buffer counts its slot (perf3's `render/geometryPool.ts`, no padding);
  - any other buffer counts its GPU size, rounded up to 64 KiB (D3D12 placement);
  - a texture counts its mip bytes once it has a GPU texture (a released texture counts its recorded size);
  - anything never uploaded counts 0.
- The whole scene plus the return cache is measured every 3 s, time-sliced (`measureGpu`).
- Budgets (`GPU_BUDGET_BYTES`, `gpuLimit()` in `coarseTiles.ts`):

  | tier | rule |
  |---|---|
  | medium, high | no budget (M1 Max, NVIDIA, RDNA) |
  | reduced, discrete (560X etc.) | 1.2 GiB GPU bytes (raised from 0.8, which blocked look-ahead all drive) |
  | reduced, Apple (base M1 Air, by the startup probe) | JS heap (Chrome `performance.memory`, ArrayBuffers included) + GPU bytes <= 3 GiB; the GPU share is what the heap leaves, clamped to 0.9..1.5 GiB |
  | low / mobile | 0.7 / 0.5 GiB GPU bytes |

- Above 90 % of the limit, no lead or view tile starts a full install, and the return cache keeps nothing.
- Above the limit, tiles are evicted in this order: return cache (oldest first), tiles drawn as distant cells, then
  full tiles off the lead (farthest first), then lead and view tiles. The 3x3 window is never evicted.
- Startup installs (the tiles the menu waits for) are never stopped or evicted. Over budget, a settling camera waits
  for its window only.
- `coarse.gpuResidency()` returns `{ bytes, limit, over, rule: 'tier' | 'apple-memory' | 'url' }`.
- URL switches: `?gpuBudget=GiB` replaces the tier's budget (0 means none); `?memBudget=GiB` sets the Apple total.
- **Open: the Apple budget limits coverage on reduced.** airperf on 9f915859 (the earlier fixed 1 GiB rule) saw
  `over: true` in every run (945-991 of 1024 MiB) and HD 69-75 % / 86-92 % (window), against 79-85 / 94-97 on medium.
  Swap was small (0.1-1.0 GB in per drive). The heap rule (af20fa29) has not been measured yet.
  - If swap stays small, raise `APPLE_MEMORY_BYTES` or `APPLE_GPU_MIN` (`?memBudget=3.5` for the A/B), or give
    Apple no budget.
  - winperf's Apple probe can also class a loaded M1 Max as reduced (the headless canary under host load did).

## Memory census

- `__render.memory()` (`game/src/world/memoryCensus.ts`) returns retained bytes by category. Each array or texture
  counts once, in the first category that reaches it. Rows:
  - geometry by category: full tiles, scenery cells, previews, shadow batches, facade modules, static batch sources;
  - texture source data;
  - caches: visual GLB bytes, shared GLB buffers, decoded KTX2 textures, final fragments;
  - JSON estimates: preview fragments and road graphs;
  - collision cells and the replay ground index;
  - object, material and texture counts, a userData estimate, and the WGSL shader strings.
- It also returns:
  - `releasedMiB`/`releases` (geometry), `texturesReleasedMiB`/`textureReleases`, `textureRestores`;
  - `heapMiB` and `browser` (measureUserAgentSpecificMemory);
  - `gpuMiB` by category, `gpuPools` (geometry and uniform pool pages and slots) and `gpuResidency`.
- Heap gap tool: `tools/memory/heap-snapshot.mjs`.
  - `take --cdp URL --url bench=drive --out DIR` waits for `__bench.done` and saves `census.json`, `usage.json`
    (V8 heap and ArrayBuffer backing for the page and each worker) and a gzip heap snapshot.
  - `summarize FILE` reports: retained size by class over the dominator tree, a drill-down of the largest owners,
    ArrayBuffer bytes by holding property path (readable in minified builds) and duplicate strings.
  - Tests: `node --test tools/memory/heap-snapshot.test.mjs`.
  - **Bug:** `--open` builds `/json/new?${encodeURI(url)}`, so Chrome drops the replay URL after its first `&`.
    It must be `encodeURIComponent(url)`. Until fixed, open the page yourself and use `--url`.

## Releases (CPU copies after upload) and `?keepArrays`

- Geometry: `game/src/world/releaseArrays.ts` (`TileArrays`).
  - At a full tile's commit, uploaded static render-only arrays are replaced by empty typed arrays of the same type.
    Counts stay; there is no version bump. Uploads are checked from backend records, event-driven.
  - Position and index stay for drawn geometry (raycasts, light scans).
  - Merged-away, detached and shadow-batch sources release everything.
  - Skinned, morph, instanced and prop meshes keep their arrays.
  - Building templates release once every tile holding them is committed; a new tile then reads a fresh template.
- Collision previews release every array after upload: nothing reads them on the CPU.
- The replay/QA ground index (`world/groundIndex.ts`) keeps raw `.col` bytes and indexes a cell on its first query.
- Textures: `world/sharedAssets.ts` `releaseTextureData`.
  - At commit, resident shared KTX2 textures whose upload is current drop their mips (new empty mip objects) and
    their decoded template. Ground maps keep theirs, because the surface arrays copy them later.
  - Guard (`world/loadingKtx.ts` `guardReleasedTextures`): an upload of released or empty mips is skipped. The
    texture decodes its URL again, keeps that data and then uploads. This fixed the WriteTexture zero-size errors
    of 14f5b175.
  - The re-upload path is unknown. The first three restores log a stack; check Jonni's console for
    `[textures] … decoding again`.
- `?keepArrays` keeps all CPU copies (the id pass sets it too).
- Measured on the Air: renderer 4.2-4.7 against 5.5-5.7 GB, heap 1.3-2.0 against 2.7-3.0 GiB, no missing geometry.

## Next targets (airperf heap snapshot, medium replay end pose, 53efadee; ranked)

1. **MaterialStyler surface copies, 102 MiB.** `MaterialStyler.owned` keeps `restoreSurfaces` closures that hold
   the merged surface arrays. Release them after commit, or rebuild them on restore.
2. **RenderObject vertex buffers, 106 MiB.** three's `RenderObject.vertexBuffers` arrays (`scene.vertexBuffers.array`)
   keep CPU copies the geometry release does not reach. Find which attributes they are (probably node attributes or
   interleaved copies) and release them after upload.
3. **AssetCache bytes, 59 MiB.** Three caches of raw bytes (visual GLB, shared GLB buffers, decoded KTX2) hold entry
   promises. Cap them lower, or drop the entries once their content is uploaded and committed.
4. **386 PMREMGenerator / 385 PMREMNode instances, 41.5 MiB.** One per environment texture key, kept in WeakMaps:
   likely a leak (a generator per material or frame key instead of one shared). Share one generator.
5. **The exposure worker's 448 MiB** (`render/weather/exposure.worker.ts`, JS heap in the worker scope). Check what
   it retains per model and bound it.

Also outside the census: index arrays 125 MiB, texture mipmaps 65 MiB, walker VAT 31 MiB, lights buffer 19 MiB and
JS objects ~125 MiB.

## Tests

- Streaming: `coarse-tiles`, `coarse-region`, `coarse-serving`, `streaming`, `collision-preview`, `stream-trace`.
- Budgets: `preparation-budget`.
- Memory: `release-arrays`, `gpu-residency`, `shared-assets`, `surface-arrays`, `ground-index`.
- Buildings: `building-placements` (deterministic since 095f456a).
- All under `game/tests/unit/`. Pose replay sim: `~/turku3d/loops/stream2/sim/stream2-sim.test.ts`.
