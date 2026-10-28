# Initial world preload and 360-degree residency

Measured 2026-10-28 on the sandbox copy of the published `d78eb3c7` tile release.
Manifest SHA-256: `03ff3fb572209e0c842f3a3447e36e11b65c7f6eb4dff5cc8ed93e9dfb020de2`.
All captures are generated game renders retained under `$TURKU3D_WORK/reports/preload/`; no images or city data are committed.

## Findings and change

The old start flow enabled controls after actor assets loaded, before visual streaming started to settle. In the
first cold run it exposed controls at 10.5 seconds and still had zero resident visual models at the 20-second idle
sample. It then requested 112 GLB/KTX2/collision/ground resources during the first turn. A second run waited for
`tilesSettled` before turning and still requested 98 resources, including already visited L0 cells. This confirms
view-driven loading/cancellation or eviction, independently of the early start-screen release.

The renderer's default byte cap is a hard admission limit: used content is protected from eviction but also stops
new requests once the cap is reached. The unit fixture reproduces the admission condition above 600 MiB and checks
that the ring retains headroom. A permanent never-refining state was not observed in these cold runs; the change
removes that known admission failure and rejects missing required content instead of interpreting idle as ready.

Play now selects complete REPLACE sibling groups intersecting a 250 m player-centred ring (or a larger configured
collision radius), retaining them for another 125 m. Far L1 models within 2 km also load in every direction.
The standard 3DTilesRendererJS plugin contract drives its existing traversal, priorities, loaders and LRU; there is
no second visual loader. Actual mesh rendering remains frustum-culled. QA retains the camera/SSE count budget.
Startup waits for every required model, draft-window tile, ground binding and worker-decoded collision cell, then
uploads textures, compiles offscreen materials and renders six warmup directions through the actual post pipeline.
The final view is restored before input becomes available. Progress exposes completed encoded response bytes,
required tile/collision counts, GPU textures, compile directions and failures through `worldLoad.snapshot()`.

## Cold-cache measurements

Manual `game/scripts/preload-probe.mjs`, Pages-like production dist and fixed real tiles, Chrome/Metal, 1280×720,
HTTP cache disabled, 10 Mbps download, 40 ms latency, 4× CPU throttling. Each camera turn spans 240 rendered frames.
No translation is commanded. “After” counts include all visual/collision/ground requests, not just the protected ring.

| Run | Controls available | Turn asset requests | Frame p50 / p95 / max | Host load (1 min) |
| --- | ---: | ---: | ---: | ---: |
| Before WebGPU, 20 s idle | 10.5 s | 112 | 133 / 800 / 3033 ms | 36 |
| Before WebGPU, initial view settled | 11.3 s | 98 | 117 / 783 / 1983 ms | 191 |
| After WebGPU | 134.8 s | **0** | 100 / 150 / 150 ms | 251 |
| After WebGL2 | 462.0 s | **0** | 133 / 183 / 367 ms | 392 |

After preload both backends report 37/37 required models, 46/46 decoded collision cells and 6/6 compile directions.
The untouched initial spawn render contains full-detail facades and ground before any rotation. The WebGPU
renderer reports 457 programs and 328 textures both before and after the turn: no first-turn shader or texture
allocation increase (small dynamic marker geometry allocations remain). These are individual stress runs under
different, very high host loads, not a controlled renderer-throughput benchmark. In particular the WebGL startup
cost remains large under that load and throttle.

| Memory immediately after preload | WebGPU | WebGL2 |
| --- | ---: | ---: |
| Sum of Chrome process RSS | 2.14 GB | 1.78 GB |
| JavaScript used heap | 452 MB | 552 MB |
| TilesRenderer decoded cache estimate | 410 MB | 410 MB |
| Renderer tracked GPU allocations | 259 MB | 274 MB |
| Physics estimated WASM collision allocation | 65 MB | 65 MB |

RSS sums can double-count shared pages; renderer/cache/heap figures overlap and must not be added together.
The older settled-view WebGPU sample used 1.94 GB RSS and a 371 MB tile cache. Reports preserve the complete
request lists, initial/turned screenshots, resident ids, memory breakdowns, progress and page errors.

The first full-data runs also exposed r186's removed `waitForGPU()` stub. It was replaced with WebGPU queue
completion and WebGL sync fences; normal frustum culling is restored immediately after offscreen compilation.
Final verification of those backend waits is recorded with the final task result.

## Reproduce

Use the exact sandbox build/server/probe commands in `game/src/world/README.md`. For a baseline retain its game
build separately and pass its sandbox URL as the probe's third argument. `before-settled` waits for the old
stationary view, whereas `before` samples after 20 seconds. Do not serve changing live tiles during a comparison.
Small unit checks: `cd game && npx vitest run tests/unit/residency.test.ts tests/unit/load-region.test.ts`.
The normal fast gate remains `scripts/dev/gate --fast --base main`.
