# Renderer path: recommendation and feasibility

Owner: `w2-render`. Written 2026-11-13 for the decision on how Turku3D gets from today's renderer to virtualised
geometry (as in UE Nanite) and software global illumination (as in software Lumen). The only target is Chrome on an
Apple-silicon Mac; the reference machine is the M1 Max (10 cores, 64 GB). Companion to
[render-readiness.md](render-readiness.md), which holds the rules for assets. Nothing here is built yet: this is a
recommendation, the measurements behind it and what would be expensive to reverse.

## Recommendation

**Draw the static world with our own GPU-driven passes in WGSL, on the same `GPUDevice` and in the same frame as an
unforked, pinned three.js, and keep three.js for what it already does well.** The world that the W2 pipeline emits
(ground, buildings, street objects, trees) leaves three's scene graph: it becomes a cluster table, an instance table
and a material table on the GPU, culled in a compute pass, drawn by a handful of indirect draws into a visibility
buffer, and shaded per pixel by one function per material family. three.js keeps the few hundred moving things
(vehicles, walkers, effects), sky, water, weather and the post chain (TRAA, bloom, grade), and draws into targets we
own. This is the third of the three paths (our own renderer, three.js where it earns its place), reached one pass
at a time beside the running game, never by a rewrite: the first slice already looks like the first path (stock
three.js plus our passes). A fork is not recommended.

**The measurements on this M1 Max that carry it** (headless Chrome 156 on the Metal adapter, 2026-11-13; synthetic
building blocks, not the game; best of several runs while other agents' browsers shared the GPU; sections 3 to 5):

1. **three.js costs CPU per object; a compute pass does not.** r186 spends 2.3 to 2.7 microseconds of CPU per mesh
   in `render()` at its cheapest (one shared geometry, one shared unlit material, frozen matrices): about 5 ms for
   2,000 meshes and 25 to 26 ms for 10,000, which is the whole 25 ms frame of 40 FPS. Its `BatchedMesh` costs 0.1 to
   0.3 microseconds per instance (2.2 to 6.0 ms for 20,000). A compute pass culled 4,000,000 cluster records in
   0.5 ms, and its CPU cost does not grow with the world.
2. **The hardware rasteriser draws pixel-sized triangles fast enough, so no compute rasteriser is needed.** 8 M
   triangles of a quarter pixel each, culled and drawn by one indirect draw into a 1920 x 1080 visibility buffer,
   take 2.9 ms; 32 M take 13.6 ms (2.2 to 2.8 M triangles per ms). That matters because the compute rasteriser
   Nanite uses for small triangles needs a 64-bit atomic that WebGPU does not offer on the M1 family. For scale,
   today's renderer on its ultra tier runs GPU-bound 115 to 160 ms frames at 19.7 M triangles on the same machine
   (with full forward shading and 4096-pixel shadow cascades, so not like for like).
3. **Distance-field tracing fits the frame, and three.js shares it.** One primary ray per pixel through a
   four-level distance-field clipmap at 1080p takes 1.5 ms, and diffuse rays run at about 0.3 M per ms, so a
   Lumen-style gather of a quarter ray per pixel is about 1.7 ms of tracing. Stock three.js r186 accepted a device
   we created and shared targets both ways (it sampled a texture a raw pass wrote; a raw draw depth-tested against
   its depth buffer): the two renderers coexist in one frame without a fork.

Beside these: 1,823 lines in 16 files and about thirty patched r186 internals exist today only to make thousands of
per-object world draws bearable (section 2).

## 1. The three paths

| | 1. Stock three.js, the world stays in its scene graph, our passes beside it | 2. Patched or forked three.js | 3. Our world passes, three.js as a guest (recommended) |
| --- | --- | --- | --- |
| World draws | one three.js draw per mesh, as today | the same, with the causes of today's workarounds fixed inside three | a few indirect draws for the whole world |
| Virtualised geometry | not in three's draw path; added as our passes, so the world leaves the scene graph anyway (this is then path 3) | the same | native: cluster culling, visibility buffer, per-pixel materials |
| Software GI | our passes; needs depth, normals and material data that three's forward pass does not keep | the same | our passes read our own visibility buffer |
| Existing render code kept (section 2) | all 14,214 lines, including 1,823 lines that exist only to work around r186, tied to r186 internals | the 1,823 lines move into the fork; about the same code, now merged against each monthly release | all renderer-independent code, sky, water, weather, post chain, dynamic objects, governor and timers; the world's batches, pools and per-draw workarounds retire as the world moves |
| Cost | lowest now; the per-draw cost stays and every three.js upgrade risks the 30 or so patched internals | highest standing cost: a renderer of some 65,000 lines to maintain; gives neither a visibility buffer nor GPU culling | a new world path of five or six passes and its data format; done in slices beside the old path |
| 40 FPS on the M1 Max, more on stronger hardware | bounded by CPU per draw (section 4) | bounded by CPU per draw, lower constant | GPU-bound; scales by parameters (section 8) |

Why not a fork. The workarounds in section 2 are not a few bugs to patch: they follow from three's model of one
render object per mesh, each with its own uniform buffer, its own refresh and its own program key. Upstream has had
the draw-call cost open since r173 (issue 30560, section 10). A fork that changes that model is a new renderer inside
someone else's code base; a fork that does not change it leaves the cost.

Why three.js stays. Its post chain, sky, water and node materials for dynamic objects work, are tested, and cost
little when they draw hundreds of objects rather than thousands. The game already runs raw WebGPU on three's device
in production (`gpuTimer.ts`, `gpuPasses.ts`, `uniformPool.ts`, `geometryPool.ts`, `shaderModules.ts`), so sharing the
device is proven, and section 3 shows the two directions of target sharing work without touching three's source.

## 2. What the game carries today because of three.js, WebGL2 and other targets

From a survey of `game/src/render` (122 files, 14,214 non-test lines) and the renderer-facing parts of
`game/src/world`, 2026-11-13. Key claims were re-read in the source.

**How the world is drawn today.** Plain `THREE.Mesh` with geometry merged per 62.5 m cell, distant scenery merged
per bucket, `InstancedMesh` for facade modules, lamps, vegetation and walkers. No `BatchedMesh`, no indirect draws,
no compute, no storage buffers anywhere in `render` or `world`. Culling is on the CPU: three's per-mesh frustum test
and a software occlusion test of buildings in a worker. Counts on record: 720 draws at spawn, 2,044 in another
census; ultra on the M1 Max: 115 to 160 ms frames at 19.7 M triangles.

**Because of three.js r186** (16 files exist for nothing else: 1,823 lines; more sit inside other files):

| What three.js does | What the game carries | Recorded cost |
| --- | --- | --- |
| One `GPUBuffer` per uniform binding, so one per render object | `uniformPool.ts`: pages, staged writes, quarantine; replaces five backend methods and `queue.submit` | 5,526 buffers, 1,732 `writeBuffer` calls a frame; a reused slot once hung the M1 Max GPU |
| One `GPUBuffer` per vertex attribute and index | `geometryPool.ts`: 4 MB pages; wraps `backend._draw` | 8,081 of 9,255 buffers under 64 KB |
| Refreshes every node-material draw in full every frame | `staticRefresh.ts`: lets unchanged draws skip | draws took 57 % of the main thread in M1 Max headless play |
| Walks and updates the whole scene graph per render | `frozenTransforms.ts`, `reflectionScope.ts` | 2.1 to 3.7 ms a frame before |
| Sorts opaque draws front to back, switching pipeline nearly every draw | `drawSort.ts` | about 3 ms a frame on the old Windows target |
| Builds a draw's program (TSL to WGSL, 20 to 80 ms) and its pipeline synchronously at first use | `asyncNodeBuilds.ts`, `asyncPipelines.ts`, `shaderModules.ts`, `yieldPacer.ts`, `programCache.ts`, `compileModel.ts`, keeper meshes in `lodFade.ts` | stalls of 50 to 110 ms; freezes of 1.35 s (M1 Max), 9.7 s and 22 to 25 s before the shims |
| Keys a program by node identity and by instanced mesh | `instancePrograms.ts`, shared node graphs in `nightSurface.ts`, `nightLights.ts`, `riverSurface.ts`, `weather/surfaces.ts`, `sceneryMaterial.ts`, `shadowMaterials.ts`, `materialIdentity.ts` | 339 program builds in one drive, 60 to 120 ms each on the old target |
| Changing the light set recompiles every lit material; `DynamicLightsNode` drops material lighting | `lighting.ts`: fixed light slots, re-added lightings, packed `vehicleLightData.ts` | 2 ms GPU for the batched hemisphere loop on the M1 Max |
| Private state not restored after an exception; cleanup, MRT and velocity quirks | `frameGuard.ts`, `nodeAttributes.ts`, `nodeBuilderHooks.ts`, `rigidVelocity.ts`, parts of `post.ts` | a black screen; a 1.3 GB leak before the hook dispatcher |
| Render bundles still refresh each replayed draw | `bundleReplay.ts`, `staticBundles.ts` (own record and replay, off by default) | no gain measured |

`GameRenderer` (`shadowMaterials.ts`) subclasses `WebGPURenderer` and overrides `_renderObjectDirect`,
`_renderBundle`, `_renderScene` and `_getShadowNodes`; about thirty private fields and methods of r186 are read or
replaced across these files. Each is a reason the game is pinned to 0.186.1. Nearly all of it exists because the
world is thousands of per-object draws with hundreds of programs. Many of the costs were measured on the retired
Windows laptop, but the mechanisms are the same on the Mac and three of the worst were measured there.

**Because of WebGL2.** Little code (the `forceWebGL` switch and labelling in `renderer.ts`, a timer-query path in
`gpuTimer.ts`, a `fenceSync` drain in `world/preload.ts`, tests and budgets for both backends) but a large silent
cost: nothing in the renderer uses compute, storage buffers or indirect draws, the features WebGL2 lacks and
everything in this note depends on. Dropping WebGL2 removes that ceiling.

**Because of mobile, weak GPUs and other browsers.** The `mobile`, `low` and `reduced` tiers and their tables
(`quality.ts`, lights, point cloud, walkers, tile budgets), user-agent tests and the 720p cap, the 30 FPS mobile
pacing (`framePacer.ts`), the Windows-laptop branches in `adaptiveResolution.ts` and `environment.ts`, flat varyings
chosen for iPhone GPUs. The mechanisms (a governor that holds a frame rate, adaptive resolution, a startup GPU
probe that tells an M1 Air from an M1 Max) are exactly what "scale up on stronger hardware" needs and are kept; the
tiers below `medium` and the device special cases can go.

**What each class of existing code becomes under the recommendation.**

| Class | Lines | Under path 3 |
| --- | --- | --- |
| Renderer-independent logic and data (day clock, time cycle, quality and budgets, governor, pacing, point-cloud LOD) | 1,993 | kept unchanged |
| TSL shading (materials, post chain, sky, water, weather, lights) | 6,028 | post chain, sky, water, weather, light sprites and dynamic-object materials stay in TSL; world surface materials are rewritten as WGSL family functions, which the W2 material families replace in any case |
| Scene graph, draw submission, resources (batches, bundles, pools, shadow set-up, environment) | 4,370 | environment and light set-up kept; world batches, bundles and LOD cross-fades are replaced by the cluster and instance tables |
| Workaround-only | 1,823 | deleted when the world has left three's draw path; the few that also serve dynamic objects shrink to their size |

## 3. WebGPU on this machine

Measured with `tools/render-spike/measure.mjs` in headless Chrome for Testing under a host lease on the M1 Max.

Adapter: Apple, `metal-3`, not a fallback adapter; Chrome for Testing 156.0.8078.73. A device was created with
every feature below enabled and every limit raised to the adapter's value, and three.js r186 ran on that device.

Features (25): `bgra8unorm-storage`, `chromium-experimental-subgroup-matrix`, `clip-distances`,
`core-features-and-limits`, `depth-clip-control`, `depth32float-stencil8`, `dual-source-blending`,
`float32-blendable`, `float32-filterable`, `indirect-first-instance`, `primitive-index`, `rg11b10ufloat-renderable`,
`shader-f16`, `subgroup-size-control`, `subgroups`, `texture-component-swizzle`, `texture-compression-astc`,
`texture-compression-astc-sliced-3d`, `texture-compression-bc`, `texture-compression-bc-sliced-3d`,
`texture-compression-etc2`, `texture-compression-unaligned`, `texture-formats-tier1`, `texture-formats-tier2`,
`timestamp-query`. WGSL language features include `readonly_and_readwrite_storage_textures`,
`pointer_composite_access`, `unrestricted_pointer_parameters`, `packed_4x8_integer_dot_product`, `subgroup_id`,
`immediate_address_space`, `buffer_view` and `fragment_depth`.

| Limit | This machine | WebGPU default |
| --- | --- | --- |
| `maxBufferSize` | 4 GiB | 256 MiB |
| `maxStorageBufferBindingSize` | 4 GiB | 128 MiB |
| `maxUniformBufferBindingSize` | 65,536 | 65,536 |
| `maxStorageBuffersPerShaderStage` (vertex and fragment stages included) | 10 | 8 |
| `maxStorageTexturesPerShaderStage` | 8 | 4 |
| `maxSampledTexturesPerShaderStage` | 48 | 16 |
| `maxUniformBuffersPerShaderStage` | 12 | 12 |
| `maxTextureDimension2D` | 16,384 | 8,192 |
| `maxTextureDimension3D` | 2,048 | 2,048 |
| `maxTextureArrayLayers` | 2,048 | 256 |
| `maxColorAttachments` | 8 | 8 |
| `maxColorAttachmentBytesPerSample` | 128 | 32 |
| `maxBindGroups` | 4 | 4 |
| `maxInterStageShaderVariables` | 28 | 16 |
| `maxComputeInvocationsPerWorkgroup` | 1,024 | 256 |
| `maxComputeWorkgroupsPerDimension` | 65,535 | 65,535 |
| `minStorageBufferOffsetAlignment` | 256 | 256 |

**The design may rely on** (all must be asked for when the device is created; the defaults are far lower):

- compute passes, storage buffers in every stage (10), indirect draws with `indirect-first-instance`: the whole
  GPU-driven path;
- buffers and bindings up to 4 GiB, so the cluster, instance and material tables are single buffers;
- `timestamp-query`: per-pass GPU time for the governor (quantised to 0.1 ms by Chrome's default);
- `texture-compression-bc` and `texture-compression-astc`: both present, pick one for shared array textures;
  2,048 array layers and 16,384-pixel atlases;
- `texture-formats-tier1` and `tier2`: 8- and 16-bit storage textures, read-write for `r8unorm` and `r16float`,
  which composing a distance-field clipmap in place needs; `float32-filterable`; `rg11b10ufloat-renderable`;
- `primitive-index`: the triangle id in the fragment stage without a per-vertex id;
- `shader-f16`, `subgroups`, `dual-source-blending`, `clip-distances`: optimisations, never preconditions.

**Absent from the list above, and the design must not need them:** multi-draw indirect (flag-gated in Chrome and
emulated on Metal; one indirect draw of instanced clusters makes it unnecessary); a 64-bit atomic (the new
`atomic-vec2u-min-max` feature gives a 64-bit atomic maximum in storage buffers, but Dawn enables it on Apple GPU
families 8 and 9 only, "so not the M1": section 10); mesh shaders; hardware ray tracing (proposals opened 2026-10,
not shipped); bindless textures (a resource-table proposal is in progress).

**Sharing the device and targets with three.js r186**, without touching its source:

- `new WebGPURenderer({ device })` took a `GPUDevice` we created (`renderer.backend.device` was that device).
- A raw render pass wrote a texture that belongs to a three.js `RenderTarget` (found with
  `renderer.initRenderTarget(target)` and `renderer.backend.get(target.texture).texture`, format `rgba8unorm`);
  three.js then sampled it as a material map and read back exactly the colour written (64, 128, 191).
- three.js rendered a plane into a target with a `DepthTexture` (`depth24plus`); a raw draw into the same colour
  and depth textures was hidden where three.js had drawn nearer and visible elsewhere.

`backend.get()` is an internal accessor, but it is one line, and the game already depends on it. With our frame as
owner even that goes away: three.js is handed targets we created.

## 4. Virtualised geometry on WebGPU

| Nanite piece | On WebGPU in Chrome on Apple silicon | Status |
| --- | --- | --- |
| Cluster hierarchy (128-triangle clusters, groups simplified with locked borders, a DAG) | Built offline by the pipeline as a derived object (`output.derived`), with meshoptimizer; the readiness check already builds it per entity to measure how well each asset simplifies | proven by open code: meshoptimizer `clusterlod.h` (MIT), Bevy, `nanite-webgpu`, `three-virtual-geometry` |
| LOD selection and culling | One compute pass over the cluster records of resident instances: frustum, projected error against a pixel threshold (cluster error at most the threshold, parent error above it), then occlusion against last frame's depth pyramid; survivors are appended with `atomicAdd` into the arguments of an indirect draw | measured below |
| GPU-driven draw | One `drawIndirect` of 384 vertices per surviving cluster; the vertex stage reads the cluster's indices and positions from storage buffers (no vertex buffers, no per-object state) | measured below; needs storage buffers in the vertex stage (core WebGPU) |
| Visibility buffer | `r32uint` (visible-cluster index and triangle) plus a depth buffer, drawn by the hardware rasteriser | measured below |
| Material evaluation | A full-screen pass per frame: read the id, fetch the triangle, compute barycentrics, attributes and their derivatives, then call the material family's function with the instance's flat parameter row; no bindless, so families are procedural or sample a few shared array textures | fetch pattern measured below; rule M1 exists for this |
| Small triangles | Nanite draws clusters with pixel-sized triangles in a compute rasteriser that needs a 64-bit atomic; that is not available on the M1 family in WebGPU. The answer here is to measure what the hardware rasteriser costs with small triangles and to choose the LOD threshold accordingly | measured below |
| Masked foliage | A second visibility pipeline with an alpha test, drawn after the opaque one; cards thinned by the renderer in the stored order (rule T2) | not measured |
| Shadows | The same cluster draw, depth only, per cascade, with its own cull | not measured |
| Streaming | Cluster pages by content address, fetched by distance and error; the instance table is resident for the neighbourhood, the far field is a few merged proxies per parent cell (section 7) | design only |

**Measured: cull, one indirect draw, visibility buffer** (1920 x 1080, `r32uint` and `depth32float`; clusters of
128 triangles from a pool of 16,384 different ones, 46 MB of indices and positions in storage buffers; twice as many
cluster records tested as drawn; clusters scattered at random depth):

| Triangles drawn | Pixels per triangle | Depth complexity | Cull and raster, ms | Triangles per ms | Resolve pass, ms |
| --- | --- | --- | --- | --- | --- |
| 1 M | 2 | 1 | 0.66 | 1.5 M | 0.07 |
| 2 M | 1 | 1 | 1.08 | 1.9 M | 0.09 |
| 4 M | 0.5 | 1 | 1.64 | 2.4 M | 0.16 |
| 8 M | 0.25 | 1 | 2.91 | 2.8 M | 0.33 |
| 16 M | 0.25 | 2 | 7.20 | 2.2 M | 0.57 |
| 32 M | 0.06 | 1 | 13.6 | 2.4 M | 1.01 |
| 4 M | 2 | 4 | 2.59 | 1.5 M | 0.17 |
| 1 M | 8 | 4 | 1.51 | 0.7 M | 0.16 |
| 1 M | 16 | 8 | 1.72 | 0.6 M | 0.12 |

Times are wall time per frame with frames submitted back to back (the pass's own GPU timestamps agree within
15 %). The resolve pass here only reads the id, the cluster and the triangle's three positions and writes a normal:
it shows that the fetches are cheap (0.1 to 0.3 ms at 1080p), not what materials will cost. The vertex stage is
simpler than a real one (no instance transform), so expect somewhat fewer triangles per ms in the game.

Culling alone at the size of a city: 4,000,000 cluster records of 32 bytes tested in one compute pass, 250,000
kept and appended to the draw's arguments: 0.48 ms (0.36 ms on the GPU's own clock).

What it settles:

- **Small triangles are not a problem on this GPU.** Throughput per triangle is highest when triangles are smaller
  than a pixel; nothing collapses down to a sixteenth of a pixel. A compute rasteriser is not needed on the M1 Max.
- **The triangle budget is generous.** At one triangle per pixel, a 1440p view with a depth complexity of 1.5 after
  occlusion culling is about 5.5 M triangles: 2 to 3 ms. Three shadow cascades of the same order fit as well.
- **Fill costs more than vertices.** A million 16-pixel triangles at depth complexity 8 cost more than four
  million sub-pixel ones. Occlusion culling of clusters against last frame's depth is worth its pass.

**Measured: three.js r186 on the same device**, `render()` CPU time, static scene, matrices frozen:

| Drawn through three.js | Objects | Draw calls three.js reports | `render()` CPU, ms (two runs) | Per object |
| --- | --- | --- | --- | --- |
| `Mesh`, one shared box geometry and one shared `MeshBasicNodeMaterial` | 2,000 | 2,000 | 4.6, 5.3 | 2.3 to 2.7 us |
| the same | 10,000 | 10,000 | 24.9, 26.2 | 2.5 to 2.6 us |
| one `BatchedMesh`, per-instance frustum culling | 20,000 | 20,000 | 6.0, 6.1 | 0.3 us |
| one `BatchedMesh`, no per-instance culling | 20,000 | 20,000 | 2.2, 2.2 | 0.1 us |

This is the cheapest case three.js has: the game's draws carry lit node materials, shadows, a
velocity target and per-object updates, and its own records show draws taking 57 % of the main thread. Upstream
figures agree (2.4 us per mesh at 4,000 meshes on r183; 20,000 cubes at about 15 FPS on an M1 Pro; section 10).
`BatchedMesh` is ten to twenty times cheaper per instance but still linear in instances on the CPU, has no
hierarchy of detail, and on WebGPU issues one draw per instance.

**What open implementations show.**

- `three-virtual-geometry` 0.1.3 (2026, MIT) runs inside stock three.js r180+ `WebGPURenderer`: clusters of up to
  128 triangles, a DAG, LOD selection and frustum, size and occlusion culling in compute, hardware raster only, no
  streaming. Its README states 3.0 M instances at about 150 FPS at 1080p on an M4 Pro in Chrome 154 (the author's
  figures, not checked here). So the technique fits WebGPU on Apple silicon and can share a frame with three.js.
- Scthe `nanite-webgpu`: the full hierarchy, per-meshlet culling against last frame's depth pyramid, impostors. Its
  software rasteriser is a demonstration only, because WGSL has no 64-bit atomic: depth and a normal packed into
  32 bits, no ids, visible artefacts. No streaming.
- Bevy's meshlets: 0.14 drew everything with the hardware rasteriser from one indirect draw (2.78 ms for 3,092
  instances of a 144,042-triangle mesh on an RTX 3080); 0.15 added a compute rasteriser with a 64-bit atomic and
  measured it "only a 10-20 % performance improvement over mesh shaders". It supports Vulkan and Metal natively, not
  WebGPU. So the software rasteriser is an optimisation for desktop GPUs, not a precondition.
- three.js itself offers indirect draws (`geometry.setIndirect`), compute and storage buffers in r186, but the
  per-object path stays as measured below, and TSL has no draw index on WebGPU.

## 5. Software global illumination on WebGPU

| Lumen piece | On WebGPU in Chrome on Apple silicon | Status |
| --- | --- | --- |
| Mesh distance fields | Derived per asset by the pipeline from closed solids (rules F1, F2, G2), sparse 8-bit bricks; in WebGPU a 3D texture atlas of bricks. Used only for the first metres of a trace | design only; UE uses them for the first 2 m by default |
| Global distance field | A clipmap of 3D textures around the camera (here 4 levels of 128 voxels, 0.2 to 1.6 m), composed from the asset fields and the ground's height raster (rule F3) as the camera moves, a slab per frame | tracing measured below; composition not measured |
| Surface cache | Cards per asset (rule F6) holding albedo, normal, emissive and lit radiance in an atlas, relit over many frames; hits of field traces read radiance from it | design only |
| Screen traces | A depth-pyramid march in the previous frame's lit colour; three's `SSGINode` and `SSRNode` do this today | exists in three.js |
| Final gather | Probes placed on screen every 8 or 16 pixels, a few rays each, traced first on screen and then in the field; interpolated and filtered over time | ray budget measured below |
| Radiance cache | World probes on a sparse grid for far and rough lighting, updated a few per frame | design only |
| Sky and sun | Sky visibility from the same traces; sun from shadow maps as today | - |

**Measured: tracing a distance-field clipmap in a compute pass** (4 levels of 128 x 128 x 128 `r16float`, 16 MiB,
voxels of 0.2, 0.4, 0.8 and 1.6 m, 205 m across; a street grid of box buildings; one primary ray per pixel of up to
64 steps, then diffuse rays of up to 32 steps and 60 m from each hit; 89 % of pixels hit a surface):

| Size | Diffuse rays per hit | ms per frame | Field samples per pixel | Field samples per ms |
| --- | --- | --- | --- | --- |
| 960 x 540 | 0 | 0.55 | 17 | 16 M |
| 960 x 540 | 1 | 1.67 | 29 | 9 M |
| 960 x 540 | 4 | 6.35 | 65 | 5 M |
| 960 x 540 | 16 | 25.2 | 210 | 4 M |
| 1920 x 1080 | 0 | 1.54 | 17 | 23 M |
| 1920 x 1080 | 1 | 7.34 | 29 | 8 M |
| 1920 x 1080 | 4 | 30.9 | 65 | 4 M |

Diffuse rays cost about 3.2 ms per million (0.3 M rays per ms), about twelve field samples each. What it settles:

- **One diffuse ray per pixel at full resolution does not fit; a probe gather does.** Lumen's final gather places
  a probe every 16 pixels with 64 rays, a quarter ray per pixel: at 1080p that is 0.5 M rays, about 1.7 ms. A
  probe every 8 pixels is 2 M rays, about 7 ms: a tier for stronger hardware.
- **The field itself is small**: 16 MiB for four levels at 16 bits, a quarter of that at 8 bits.
- Not measured, and the larger part of the work: composing the clipmap from asset fields and the height raster as
  the camera moves, lighting the surface cache, filtering the gather over time. The real field has more detail than
  boxes, so rays take more steps.

**What open implementations show.** No open project runs a complete Lumen-like pipeline in WebGPU, and none of the
partial ones publishes a frame cost: probe GI traced through a BVH for three.js WebGPU (`speedball-gi`), radiance
cascades with screen probes (`three-rc`), 2D radiance cascades (TypeGPU). Godot's SDFGI documents the same structure
(cascades of a distance field, half-resolution option) as one of its most demanding techniques. UE's own budget for
Lumen is 8 ms of a 30 FPS frame and 4 ms of a 60 FPS frame at 1080p internal resolution on consoles, and software
Lumen needs walls at least 10 cm thick, closed meshes and at most 12 cards a mesh. So GI is the larger risk of the
two techniques; the plan is to make it a ladder of tiers (section 8), each of which is a complete look.

## 6. Sizes for a city of Turku's extent

Measured on the Humalistonkatu scene (32 cells of 62.5 m, 0.125 km2, integration build of 2026-11-12) and scaled.
Per square kilometre of dense centre: about 530 buildings, 2,900 trees, 880 sign posts, 220 lamps, 1,700 fences.

| Item | Basis | Per km2 of centre | For 30 km2 of centre-equivalent city |
| --- | --- | --- | --- |
| Ground mesh | 0.71 triangles per m2 measured | 0.7 M triangles, about 11 MB as cluster pages | 21 M triangles, 0.35 GB |
| Ground height raster (truth for the field) | 0.5 m, 16 bits after quantising | 8 MB raw, some 2 to 3 MB compressed | 0.1 GB |
| Buildings | 19,968 triangles in the one real building measured, 94 % of them repeated openings and trim: about 1,200 own triangles plus shared assets | 0.6 M own triangles (10 MB) and 10 M drawn through instances | 0.3 GB own, plus one shared library of openings (tens of MB) |
| Trees | 9,788 triangles median; as a library of some 300 assets rather than one per tree | 28 M drawn triangles from instances; the library is 3 M triangles, 50 MB, once for the city | 50 MB once |
| Street objects | 151 assets for 1,478 instances measured | instance rows only: 28 bytes each | a library of a few MB, 0.2 M rows of 28 bytes |
| Instance table | one row per instance: transform, asset, material row, id | about 16,000 rows, 0.5 MB | 15 MB |
| Mesh distance fields | sparse 8-bit bricks, 0.1 to 0.2 MB a building at 16 cm voxels (estimate) | 50 to 100 MB if every building is unique | fetched within 200 m only: tens of MB resident |
| Surface cache | 12 cards an asset, 64 px per card side at 0.25 to 0.5 m texels | resident atlas 4096 x 4096 of four layers: 270 MB at 16 bytes a texel | resident only |

Cluster pages are counted at 16 bytes per source triangle including every LOD level; Nanite reports about 11 MB per
million source triangles on disk (section 10), so this is conservative. The whole city's unique geometry is then
under 1 GB on the server and a few tens of MB per kilometre travelled, because buildings and trees are mostly
instances of shared assets. That holds only while rule I1 holds: flattening the openings into each building
multiplies the building share by about sixteen.

Resident on the GPU on the M1 Max, as a planning budget: geometry pages 0.5 GB, textures 1 GB, surface cache
0.3 GB, distance fields 0.1 GB, targets at 1440p 0.3 GB. The adapter's limits (4 GiB per buffer, 16,384-pixel
textures, 2,048 array layers) are far above every item; how much memory Chrome's GPU process may take in total was not measured.

## 7. Decisions in today's formats and partition that could put 40 FPS out of reach

None is locked in yet; each is cheap to keep right now and expensive after the city has been generated.

1. **Repeats flattened into entities.** The building generator bakes every opening into the building (94 % of its
   triangles are repeats). Flattened, a street scene's buildings alone are sixteen times the unique geometry, and
   neither cluster pages nor distance fields nor cards can be shared. Rule I1 covers it; it is the single largest
   factor in the sizes above.
2. **One unique mesh per tree, mostly masked cards.** 368 trees in one scene are 3.0 M unique triangles today, and
   their hierarchies keep 72 to 91 % of the triangles at 300 m. Masked surfaces also defeat the hidden-surface
   removal a tile-based GPU gives opaque geometry for free. Without a library, a renderer-side card prefix (rule T2)
   and a merged far form, foliage alone exceeds the triangle budget of a street view.
3. **No hierarchy above the entity.** With a visibility buffer the unit of cost is the cluster: every visible
   instance costs at least one. A view from Vartiovuori over 30 km2 holds some 16,000 buildings and 90,000 trees.
   The 62.5 m cell must therefore have parents (a quadtree of cells, each with a merged, simplified proxy of its
   content as a derived object), so that the far field is thousands of clusters, not hundreds of thousands of
   instances. This is a partition decision for `w2-core` and a derived product for the renderer; assets do not change.
4. **Pictures in geometry or in per-entity textures.** WebGPU has no bindless textures: a per-pixel material pass
   can sample a few array textures, not one texture per entity. Sign faces drawn as triangles are 55 % of a scene's
   drawn triangles today. Families must stay procedural from parameters or draw from shared array textures and
   atlases (rules M1, M4).
5. **Delivery as thousands of small objects.** 1,948 entities for one location are 1,948 records and as many GLBs.
   The store stays content-addressed, but the client should fetch derived pages per cell and per parent cell, not
   one request per entity.
6. **Cracks at borders.** A hierarchy can only simplify across a border whose vertices are shared: 33 ground cell
   pairs do not match today, and the measured building's walls keep 21 % of their triangles at 300 m against 0.5 %
   when welded (rules G3, F3).

## 8. One source, every fidelity

Rule S3 keeps one source at the fidelity wanted on strong hardware. The renderer scales it with parameters that a
governor sets from measured GPU time, holding 40 FPS (25 ms) on the M1 Max and spending the surplus on stronger
hardware. The mechanism exists today (`settingGovernor.ts`, `adaptiveResolution.ts`, `gpuTimer.ts`, `gpuProbe.ts`).

| Parameter | What it scales | M1 Max (expected) | Stronger hardware |
| --- | --- | --- | --- |
| Error threshold in pixels | cluster LOD cut: triangles on screen | 1 to 2 px: 4 to 8 M triangles in view, 2 to 3 ms | towards 1 px |
| Render scale with temporal upsampling | every per-pixel cost | 0.7 to 1.0 of 1440p | native, then 4K |
| Texture pixels per metre | procedural detail octaves, array-texture mip bias, resident texture memory | 256 px/m near | 512 px/m reference and above |
| Foliage card prefix | leaf cards kept per tree (rule T2) | a share by distance | all |
| Shadow reach and resolution | cascades, cluster shadow draws | 2 to 3 cascades | more, further |
| Lighting tier | 0: sky and sun with ambient occlusion (today). 1: screen-space GI. 2: half-resolution probes traced in the global field with a surface cache. 3: more rays, mesh fields near, reflections | 2, a probe every 16 px | 3 |
| Instance draw distance | by projected size, not by kind | from the governor | further |

No description, kind or GLB names a tier; the tiers are rows of numbers in the client.

A 25 ms frame on the M1 Max, as a plan built on the measurements (the starred items are not measured): cluster
culling 0.5 ms; visibility buffer at 1440p 3 ms; shadow cascades by the same draw 3 to 5 ms*; material evaluation
and direct light 3 to 5 ms* (the fetches alone are 0.3 ms); GI tier 2 about 5 ms* (1.7 ms of it is tracing); the
post chain and three.js's own objects as today. That is about 20 ms, so the M1 Max holds 40 FPS with little to
spare at these settings, and the governor has real parameters to trade. Nothing in the path grows with the size of
the city, only with what is on screen.

## 9. Decisions that are expensive to reverse

1. **The world's runtime form is tables, not a scene graph**: cluster pages, an instance table and a material
   table, drawn by the GPU. Everything downstream (delivery, streaming, culling, shadows, GI, picking) is shaped by
   it, and every asset rule in render-readiness.md assumes it. Choosing it also decides that the world's draws
   leave three.js.
2. **Materials are per-pixel functions over flat rows in WGSL, with no per-entity textures.** A visibility buffer
   without bindless textures leaves no other way that scales. It fixes the material model of every asset track
   (families and parameters, palette, shared atlases) and means the world's surface shaders are written once in
   WGSL, not in TSL.
3. **Hardware rasteriser only on the M1 family, so the triangle budget is set by LOD, and the partition needs
   parents above the cell.** The M1 family is left out of the 64-bit atomic a compute rasteriser needs, and the
   measurements show it can do without. The far field must come from merged proxies per parent cell and the near
   field from an error threshold the governor moves.

The frame's owner (which code creates the device, the main targets and the pass order) follows from these: our
frame, with three.js rendering into it. It is cheap to reverse while both paths exist side by side.

## 10. Sources

Open implementations and platform state, read 2026-11-13:

- Scthe, nanite-webgpu: https://github.com/Scthe/nanite-webgpu
- three-virtual-geometry 0.1.3: https://cdn.jsdelivr.net/npm/three-virtual-geometry@0.1.3/README.md
- Bevy virtual geometry, 0.14, 0.15, 0.16: https://jms55.github.io/posts/2024-06-09-virtual-geometry-bevy-0-14/ ,
  https://jms55.github.io/posts/2024-11-14-virtual-geometry-bevy-0-15/ ,
  https://jms55.github.io/posts/2025-03-27-virtual-geometry-bevy-0-16/
- meshoptimizer clusterlod.h: https://github.com/zeux/meshoptimizer/blob/master/demo/clusterlod.h
- three.js, WebGPURenderer draw-call cost (issue 30560, open since r173; 20,000 cubes at about 15 FPS against 60 with
  WebGLRenderer on an M1 Pro): https://github.com/mrdoob/three.js/issues/30560
- three.js forum, r183, 4,000 meshes, render() 9.7 ms on WebGPU against 4.5 ms on WebGL:
  https://discourse.threejs.org/t/webgpurenderer-2x-slower-cpu-and-5-10x-slower-first-frame-than-webglrenderer-on-many-mesh-scenes-r183-same-on-both-backends/91904
- WebGPU in Chrome by version: https://developer.chrome.com/docs/web-platform/webgpu/news
- Multi-draw indirect emulated on Metal: https://dawn.googlesource.com/dawn/+/94a3f7f616c4a2bb773568e7491c743b150f81d4
- 64-bit atomic min/max proposal and its Metal support (not the M1):
  https://github.com/gpuweb/gpuweb/blob/main/proposals/atomic-64-min-max.md ,
  https://dawn.googlesource.com/dawn/+/b620b2fe514a5ee2e6a2d924fd998c514019da87
- Lumen technical details and performance guide (software tracing, 12 cards, 10 cm walls, 4 and 8 ms budgets):
  https://dev.epicgames.com/documentation/en-us/unreal-engine/lumen-technical-details-in-unreal-engine ,
  https://dev.epicgames.com/documentation/en-us/unreal-engine/lumen-performance-guide-for-unreal-engine
- Karis, "A Deep Dive into Nanite Virtualized Geometry", SIGGRAPH 2021 (the PDF was too large to open here; the
  figures of 128-triangle clusters, 128 KB pages and about 11 MB per million triangles are from secondary summaries):
  https://advances.realtimerendering.com/s2021/Karis_Nanite_SIGGRAPH_Advances_2021_final.pdf
- Wright et al., "Lumen: Real-time Global Illumination in Unreal Engine 5", SIGGRAPH 2022 (not opened, same reason):
  https://advances.realtimerendering.com/s2022/SIGGRAPH2022-Advances-Lumen-Wright%20et%20al.pdf
- speedball-gi, three-rc, Godot SDFGI: https://cdn.jsdelivr.net/npm/speedball-gi@0.6.6/README.md ,
  https://github.com/CodyJasonBennett/three-rc ,
  https://docs.godotengine.org/en/stable/tutorials/3d/global_illumination/using_sdfgi.html

Not verified: any vendor statement on the cost of pixel-sized triangles on Apple's tile-based GPUs (none found; the
measurement in section 4 stands in for it), and the Chrome version that ships `atomic-vec2u-min-max`.

## 11. Reproduce the measurements

```sh
cd ~/turku3d/wt/w2-render/repo && source ../sandbox.env
python3 ~/turku3d/tools/host-lease/session.py run mac "$TURKU3D_SANDBOX" --kind func --window-size 1920,1080 \
  --load-timeout 300 -- node tools/render-spike/measure.mjs --out "$TURKU3D_WORK/render-spike/mac.json"
```

`--only adapter,raster,three,shared,field` selects parts. The page (`tools/render-spike/spike.js`) is synthetic: it
measures the building blocks, not the game. Timings are the best of several runs; other agents' browsers shared the
GPU during the campaign, so read them as lower bounds on what the machine can do.
