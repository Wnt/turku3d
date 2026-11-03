# perf3 handover: render CPU, GPU memory, per-draw cost (2026-11-03/04)

perf3 took over render-CPU work from perf2. Every change below is merged into `local/loop-integration` and is a
client-only change: F5, no tile rebuild. The working log is `~/turku3d/loops/perf3/STATUS.md`.

## Results
- Drive replay on the M1 Max: 37.4 fps at the round 6 baseline, 47.2 fps on ca01bb4f, 51.0 fps on cfb0900c.
- CPU p50: 19.4 -> 16.5 ms. GPU p50: 23 -> 19.7 ms.

## What is merged, its switch, and what decided it
Switches are URL parameters, read once at load.

| Change | Files | Default | Switch | Decided by |
| --- | --- | --- | --- | --- |
| Frozen matrices: the scene root does not force every matrix; published models freeze their static transforms | `world/frozenTransforms.ts` `freezeModel`, `main.ts` | on | `?frozen=0` | bench=fly A/B: `frozen=0` −12 % FPS |
| Reflection scope: reflection renders skip subtrees they cannot draw (no reflection layer, fails the water test, outside the mirror frustum) | `render/reflectionScope.ts`, `GameRenderer._renderScene` | on | `?reflectionScope=0` | bench=fly A/B: off −2 %; test shows identical reflected draws, 80 % fewer projection visits |
| Whole-tile shadows: the outer cascades draw a tile's shadow batch once; the nearest cascade keeps per-chunk culling; shared buffers, no extra memory | `world/shadowChunks.ts`, `world/shadowBatch.ts`, `render/environment.ts` `cascadeLayerMask` | on | `?tileShadows=chunks` | Air ablation: shadows were 4.5 ms CPU and 181 draws. A/B pending |
| Uniform pool: uniform bindings take 256-byte slots in 1 MB pages instead of one GPU buffer each; reused slots are zeroed; reuse waits 3 frames and an `onSubmittedWorkDone` fence | `render/uniformPool.ts`, `render/gpuFence.ts` | on | `?uniformPool=0` | D3D12 64 KB placement: 5,526 buffers, at least 345 MB. The M1 Max hang was fixed by zero-on-reuse (c4a42e96) and validated: venue static night and day for 170 s, plus the drive, with no device loss. Windows soak pending |
| Geometry pool: small static vertex and index buffers (up to 64 KB) take slots in 4 MB pages, bound by offset | `render/geometryPool.ts`, `world/gpuBytes.ts` (counts slots) | on | `?geomPool=0` | D3D12: 8,081 small buffers, 591 MB -> 1,064 MB. Validated together with the uniform pool. A/B pending |
| Static render bundles: per-cell BundleGroups, without shadow chunks | `render/staticBundles.ts`, `render/bundleReplay.ts` | **off** | `?bundles=1` | bench=static: the per-tile version was a net loss (CPU +0.9 ms, +120 draws). Remove it unless the Windows A/B wins |
| Material-band draw sort: opaque draws grouped by material within 25 m depth bands | `render/drawSort.ts` | **off** | `?drawSort=material` | For the Windows per-draw state cost. A/B pending |
| Altitude shadows: above 100 m, small casters leave the cascades (GTA rule) | `render/shadowAltitude.ts` | on | `?altitudeShadows=0` | A/B pending (`bench=fly&height=150`) |
| Timecycle tolerance: lighting re-applied only when it moves more than 0.2 % | `render/timecycle.ts` `sampleMoved`, `render/environment.ts` | on | none | Uniform census of the drive: about 400 per-draw env-intensity writes per frame |
| Scene-wide uniforms (weather, wind, leaf sun) in the shared render group | `render/weather/surfaceState.ts`, `world/nature/materials.ts` | on | none (it changes shaders) | Uniform census: per-draw rewrites of global clocks |
| Vegetation selection skipped for a still camera; tree world positions cached | `world/vegetationLod.ts` | on | none | Trace: vegetationLod.update 6 % of main-thread time |
| Shadow casters prepared after the first shadow pass; fleet batches `prepareHidden` | `render/post.ts` `takeShadowless`, `actors/fleet/visuals.ts` | on | none | Replay census: 45–51 in-frame ShadowMap builds |
| Dissolve twins compiled for river reflections | `render/materials.ts` `prepareReflections` | on | none | 9 in-frame reflection-pass builds |
| Driven-car keepers: one detached actor per design is prepared, so entering a car builds nothing | `actors/fleet/visuals.ts` `warmActors`, `render/graphicsPreparation.ts` `preparesDetached` | on | none | Live-only path |
| River reflection plane sampled at styling | `render/water.ts` `waterSurface` | on | none | Bug: released normal arrays made the plane NaN (no reflections). Guarded by `reflection-bank.test.ts` |
| Removed: `?reflection=alternate` | | | | bench=fly: 69.2 vs 73.1 fps |

Diagnostics:
- `__render.census()`: draws by pass and family; bundled draws show as `[b]`.
- `__render.uniforms()`: one frame's uniform-buffer writes by pass, family, group and uniform; whole-buffer writes
  are their own rows.
- `gfx.build`: an event for every in-frame shader build in live captures. `tools/perf/tile_timeline.py` names the
  builds in each hitch.
- Tools: `~/turku3d/loops/perf3/work/subtree.py`, `callers.py`, `buildstacks.py`.

## r186 patch points to re-check on a three.js upgrade
All of them are in `game/src/render`.
- `shadowMaterials.ts` GameRenderer overrides `_renderBundle`, `_renderBundles` and `_renderScene`.
  - `_currentRenderBundle` nesting, the reflection scope hook, and bundles executed before the opaque list.
  - Relies on `RenderBundles`, `_bundleNeedsUpdate`, `_projectObject`, `renderList.sort`, `backend.beginBundle`,
    `finishBundle`, `addBundle`, and `renderContextData.renderBundles`/`currentSets`.
- `bundleReplay.ts`: a copy of r186 `_renderBundle`'s refresh loop, using `RenderObjectRefreshType` values 1 and 2.
- `uniformPool.ts` patches `backend.createUniformBuffer`, `destroyUniformBuffer`, `bindingUtils.updateBinding`
  (r186's range-merge code copied) and `bindingUtils.createBindGroup`. The last relies on `entry.binding` being the
  index in `bindGroup.bindings`.
  - **Zero-on-reuse relies on `UniformsGroup` starting with a zeroed CPU cache.** If three stops assuming zeroed
    buffers, revisit it.
- `geometryPool.ts` patches `attributeUtils.createAttribute`, `updateAttribute`, `destroyAttribute` and
  `backend._draw`.
  - `_draw` must take the pass encoder as its 8th parameter; `geometry-pool.test.ts` pins that.
  - It relies on createAttribute using `mappedAtCreation`, and on `_draw` calling only `setVertexBuffer`,
    `setIndexBuffer`, `setPipeline`, `setBindGroup`, `setStencilReference`, `setBlendConstant` and `draw*` on the
    encoder.
- `reflectionScope.ts`: relies on `scene.onBeforeRender` being called after matrix and camera updates, the same frustum
  r186 culls with, and lights being pushed only when their layers match.
- `census.ts`: wraps `_renderObjectDirect`, `_bindings.updateForRender`, `updateSharedForRender` and `_update`, and the
  uniform-group `updateByType` on the prototype it reaches.
- `staticBundles.ts`: chains `scene.onBeforeRender`; r186 clears render lists unused for 10 frames.
- `environment.ts` `cascadeLayerMask`: relies on CSMShadowNode cloning the sun's shadow camera into
  `csm.lights[i].shadow.camera`.
- `post.ts` / `compileModel.ts`: the program key includes the render context id (RenderContexts are keyed by
  attachment state, MRT and call depth).

## Open, ranked
1. **Windows slow frames (main-thread bound, 82 % busy, about 30–33 fps):** the remaining cost is per-object work.
   - Get winperf's A/Bs of the pools (`uniformPool=0`, `geomPool=0`), `drawSort=material`, `bundles=1` and
     `tileShadows=chunks`, with GPU memory at peak.
   - Next: fewer draws per tile, by merging props, block shells and facade modules where materials match. That is
     content work.
2. **In-frame shader builds in live play:** the next live capture's `gfx.build` events name them, through
   `tile_timeline.py`'s per-hitch "built:" and family counts. Prepare ahead whatever they name.
   - Suspects: building facade components (`component_*_mat`, about 8 per replay), fence pool materials
     (`nature-chain`/`nature-metal`) and the tree fade variants (26ba0c3c).
3. Decide or remove the A/B leftovers: `bundles=1` (remove if no Windows win), `drawSort=material`, and
   `altitudeShadows` (`bench=fly&height=150`).
4. **T12 remainder:** per-draw uniform compares on static draws (no writes left). This needs r186-internal work for
   little gain.
5. Pre-existing failures that are not perf3's: `tests/unit/streaming-resources.test.ts` (2 cases) and
   `parked-preparation.test.ts` (wheel batches), both failing on integration HEAD.
