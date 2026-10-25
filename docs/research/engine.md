# Engine and web stack for "Turku open world" (Chrome, desktop)

Date: 2026-10-25. Author: research subagent. Scope: choose the browser game stack (renderer, physics, streaming, splats, post-processing, reusable parts) for a GTA-5-style open world built from real Turku lidar and orthophoto data. It must run in desktop Chrome, be developed on an M1 Max Mac, and have its assets built on a GPU-less Linux server.

Legend: **[VERIFIED]** means I ran it in this session and the numbers come from that run. **[UNVERIFIED]** means the claim comes from docs, web search or memory.

---

## 1. Recommendation (TL;DR)

| Layer | Pick | Why |
|---|---|---|
| Renderer | **three.js r186, `three/webgpu` `WebGPURenderer` + TSL**. It falls back to its WebGL2 backend automatically, or on request with `forceWebGL`. | Verified on this Mac in headless Chrome 156 on both backends. It has the largest ecosystem for real-world data: 3DTilesRendererJS, three-mesh-bvh, takram atmosphere, recast-navigation/three and Spark. Built-in TSL nodes cover CSM, GTAO, SSR, SSGI, TRAA, TAAU, bloom, SkyMesh and WaterMesh. A shipped game uses this exact stack: brunosimon/folio-2025, which combines three/webgpu, TSL, Rapier and a raycast vehicle. |
| Physics | **Rapier 0.21 (`@dimforge/rapier3d-compat`, or non-compat with `vite-plugin-wasm` for a smaller download)**. | Verified: one trimesh with 12 cm curbs and stairs, a capsule `KinematicCharacterController` (autostep, snap-to-ground, slope limits) and a `DynamicRayCastVehicleController`. The tests pass in Node and in Chrome on WebGPU and WebGL2. The same code runs in Node, so the GPU-less server can bake and validate collision. |
| Physics, plan B | JoltPhysics.js 1.1.1 | Better vehicles (wheel cylinder casts, motorcycle, tracked) and `CharacterVirtual`. **Verified catch:** the npm builds have a fixed 128 MB heap that does not grow. They hit OOM on a 500k-triangle mesh, so a custom emscripten build would be needed. |
| Asset format | glTF 2.0 + **EXT_meshopt_compression + KHR_mesh_quantization + KHR_texture_basisu (KTX2: ETC1S for albedo/ortho, UASTC for normals and detail)** | Verified end to end: built on osgallery-dev with no GPU, then loaded in Chrome by `GLTFLoader`, `KTX2Loader` and `MeshoptDecoder`. It transcodes to ETC2 (WebGPU) or ASTC (UASTC) on M1. |
| Streaming | **3d-tiles-renderer (NASA-AMMOS) 0.5.3** over static HTTP. The tiles are produced on the Linux box. | It renders with three.js as plain meshes, and its plugin system covers KTX2, meshopt, fades and LRU memory. Its use with WebGPURenderer is **[UNVERIFIED]** and should be smoke-tested in the next step. |
| Gaussian splats | **Optional and outside the critical path.** If needed, use **Spark 2.4** (`@sparkjsdev/spark`). | Verified: Spark plus a mesh plus Rapier works on `WebGLRenderer`. Spark **fails under `WebGPURenderer`** (`getContext(...).getExtension is not a function`). Our source data is lidar plus nadir orthos, which is not multi-view imagery, so splats are not needed for a first slice anyway. |
| Post-processing | three TSL `RenderPipeline`: pre-pass MRT, then GTAO at half resolution, then scene pass, then TRAA, then bloom, with AgX tone mapping and CSMShadowNode. | Verified throughput at 1080p on M1 Max is about 3.8 ms/frame for 3.5–5.8M triangles (section 6). The pmndrs `postprocessing`, n8ao and realism-effects libraries are WebGL-only. |
| Navigation and AI | `recast-navigation` 0.43 + `@recast-navigation/three`; yuka 0.7 for steering and FSMs. | [UNVERIFIED in browser] Both are active and MIT-licensed. |
| Characters and animation | Quaternius Universal Animation Library 1+2 (CC0, humanoid rig, Mixamo-compatible), Quaternius/Kenney CC0 characters; three `AnimationMixer` + `SkeletonUtils.retargetClip`. | [UNVERIFIED] |
| Build tooling | Vite 8.3, Playwright 1.64 (headless Chrome uses the real Metal GPU on the Mac), gltf-transform 4.5.1, gltfpack 1.3, KTX-Software 4.4.2 on Linux. | Verified. |

**Runner-up: Babylon.js 9.32 + Havok 1.3.14.** It is all-in-one, WebGPU-first, has a built-in JS `PhysicsCharacterController` on Havok, and Gaussian splats that work in WebGPU. It loses on three counts. Havok has no vehicle controller, so a raycast vehicle would have to be written. Havok is a closed WASM binary. And the real-world data and streaming ecosystem is thinner, although 3DTilesRendererJS does ship an `index.babylonjs.js` build (verified in the npm package). Pick Babylon if splats under WebGPU become a must-have.

**Ruled out:**
- **Godot 4.7 web.** Web export is WebGL2 / Compatibility renderer only, with no WebGPU, so there is no Forward+ quality, no SDFGI and no volumetrics. 4.7 adds wasm64. [UNVERIFIED, docs and news]
- **Unity and Unreal.** Unreal has no web target. Unity 6 web builds are large, closed and awkward for a custom streaming pipeline built from city data. [UNVERIFIED]
- **Bevy 0.20.** wgpu/WebGPU on the web works, but the engine is too immature for this content scale and has no web-ready streaming or 3D Tiles ecosystem. [UNVERIFIED]
- **PlayCanvas 2.23.** It has the best splat streaming (`GSplat*` LoD and budget balancer classes were verified in its d.ts) and an editor. Its physics is still ammo.js-based, and its geo and 3D Tiles ecosystem is weaker than three's. It is a fair alternative if the project pivots to splats.

---

## 2. Hands-on probe: what was built and run

Location: `/Users/wnt/Documents/game-test/tools/engine-probe/` (see its README.md).

```
src/level.js   one merged trimesh: road | 12 cm curb | sidewalk | 8 steps (17 cm rise / 30 cm run) | platform,
               a 15° ramp lane, a 55° slope lane, and an 8° driveway ramp + deck for the car
src/sim.js     Rapier world shared by Node and the browser: 3 capsule walkers + 1 raycast car + verdict()
src/main.js    three/webgpu renderer (shadows, ACES), ?auto=1 deterministic run, ?webgl=1 forces WebGL2
src/splat.js   Spark 200k procedural splats + mesh + Rapier on WebGLRenderer (?webgpu=1 tries WebGPURenderer)
src/fx.js      render-budget probe: SkyMesh + PMREM IBL, CSMShadowNode (4 cascades), GTAO, TRAA, bloom
src/glb.js     loads server-built meshopt+KTX2 GLB, builds a Rapier trimesh from the decoded geometry
scripts/*.mjs  node physics test, headless Chrome tests (Playwright), trimesh/snapshot benchmarks, GLB generator
```

### 2.1 Character controller and vehicle on curb-level collision [VERIFIED]

Rapier setup (from `src/sim.js`):
```js
world.createCollider(RAPIER.ColliderDesc.trimesh(positions, indices, RAPIER.TriMeshFlags.FIX_INTERNAL_EDGES));
const cc = world.createCharacterController(0.02);   // 2 cm skin
cc.enableAutostep(0.25, 0.15, false);                // steps <= 25 cm, >= 15 cm free tread
cc.enableSnapToGround(0.3);
cc.setMaxSlopeClimbAngle(45°); cc.setMinSlopeSlideAngle(50°);
const car = world.createVehicleController(chassis);  // 4 wheels, r = 0.33 m, rest 0.3 m, axle (0,0,+1)
```

Commands: `npm run test:node`, plus `npm run test:headless` and `npm run test:headless:webgl` for Google Chrome 156.0.8078.48 headless. Results were identical in Node, Chrome/WebGPU and Chrome/WebGL2:

| t (s) | stairs walker x / feetY | 15° ramp walker x / feetY | 55° walker x / feetY | car x / chassis y |
|---|---|---|---|---|
| 1.52 | -0.65 / 0.020 (road) | -0.65 / 0.020 | -0.63 / 0.020 | -14.66 / 0.707 |
| 2.02 | 0.03 / **0.140** (up the 12 cm curb) | 0.01 / 0.141 | 0.10 / 0.141 | -13.64 / 0.707 |
| 6.02 | 6.11 / 0.416 (step 2) | 6.05 / 0.702 | **3.83 / 0.143 (blocked)** | 8.42 / **0.827** (on sidewalk, +12 cm) |
| 7.02 | 7.20 / 0.991 | 7.42 / 1.068 | 3.84 / 0.144 | 14.44 / 1.171 (on 8° ramp) |
| 8.02 | 8.33 / **1.500** (top: platform 1.48 + 2 cm skin) | 8.79 / 1.435 | 3.84 / 0.146 | 20.44 / 2.021 |
| 14.0 | 13.02 / 1.500 grounded | 13.01 / 1.748 (top 1.728) | 3.84 / 0.155 | 47.98 / 2.795 (braked on deck) |

Verdict: `stairsClimbed`, `ramp15Climbed`, `ramp55Blocked` and `carMountedCurbAndRamp` are all true, with **PASS** on WebGPU and WebGL2 and no page errors. The only console message is a favicon 404. Physics cost is **0.16 ms per 60 Hz step** (3 controllers, 1 vehicle, 1 trimesh) in both Chrome and Node. Screenshots: `probe-chrome.png` and `probe-chrome-webgl.png`.

Observations that matter for the game:
- The 55° slope walker creeps up about 1.4 cm over 10 s against the wall. That is harmless, but gameplay code should zero the uphill component when `computedCollision` reports a steep normal.
- The ray-cast vehicle "teleports" onto the 12 cm curb. The suspension absorbs it and the chassis rises 0.707 → 0.827 m smoothly at 6 m/s. There is **no tyre-sidewall impact**, because rays do not see the vertical curb face. For a GTA feel, add a curb-impact impulse or use shape-cast wheels; Jolt has `VehicleCollisionTesterCastCylinder`. Raise rest length and travel for 15–20 cm curbs.
- An axle of `(0,0,-1)` with positive engine force drove the car backwards. Use `(0,0,+1)` when forward is +X (verified).
- Rapier needs `world.step()` once after adding colliders before scene queries (raycasts) see them (verified in `glb.js`).

### 2.2 Trimesh scale limits (Rapier 0.21 WASM) [VERIFIED, Node 24 on M1 Max, `npm run bench:trimesh`]

| Grid (0.25 m cells) | Triangles | Flags | Build | Char-controller step | Raycast |
|---|---|---|---|---|---|
| 50 m | 80k | none | 30 ms | 0.16 ms | ~1.6 µs |
| 125 m | 500k | none | 175 ms | 0.11 ms | ~1.8 µs |
| 125 m | 500k | FIX_INTERNAL_EDGES | 439 ms | 0.10 ms | ~1.7 µs |
| 250 m | 2.0M | none | 740 ms | 0.10 ms | ~2.0 µs |
| 250 m | 2.0M | FIX_INTERNAL_EDGES | 1550–2185 ms | 0.10 ms | ~2.0 µs |
| 350 m | 3.9M | none | 1452 ms | 0.10 ms | ~2.2 µs |
| heightfield 2000×2000 | (8M equivalent) | – | 13 ms | – | – |

(The raycast column label in the script output says "us" but is µs per ray: total ms / 100k × 1000.)

- **Memory:** the snapshot of a world holding one 2M-triangle trimesh with FIX_INTERNAL_EDGES is **269 MB raw (38 MB gzip)**, about **135 B/triangle** in WASM memory. Restoring it takes 156 ms, taking it 6.5 s (`npm run bench:snapshot`). Snapshots are too big to ship, but they show the budget.
- **In Chrome**, building a 2M-triangle collider from a loaded GLB takes **~0.8 s on the main thread** (`glb.js`). That is a visible hitch, so collision must be built in a **Web Worker** and in small tiles.
- **Design rule for collision:**
  - Use a separate **collision LOD** per tile: about 64 m tiles at 50–150k triangles. Decimate flat areas aggressively and keep curb edges, stairs, kerb ramps and walls exact.
  - Use a Rapier **heightfield** for open terrain and trimeshes only for "hard edges" (curbs, stairs, building shells).
  - Keep a 3×3 to 5×5 tile window resident, which is under about 150 MB of WASM.
  - Character and query cost do not depend on mesh size (BVH), so the only real costs are build time and memory.

### 2.3 GPU-less asset pipeline on osgallery-dev → Chrome [VERIFIED]

On the remote (Ubuntu 24.04, Node 24.18, no GPU), in `~/turku3d/research/engine/`:
```bash
curl -LO https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-x86_64.tar.bz2   # toktx/ktx CLI
curl -LO https://github.com/zeux/meshoptimizer/releases/download/v1.3/gltfpack-ubuntu.zip                               # gltfpack 1.3 (has basisu built in)
export PATH=$K/bin:$PATH LD_LIBRARY_PATH=$K/lib        # K = extracted KTX-Software dir
node scripts/make-test-glb.mjs tex.png level.glb       # probe level + 2M-tri grid + 2048² PNG -> 54.5 MB
npx gltf-transform etc1s level.glb t.glb && npx gltf-transform meshopt t.glb level.etc1s.meshopt.glb   # 5.37 MB (gzip 0.65 MB*)
npx gltf-transform uastc level.glb t.glb --level 2 --zstd 18 && npx gltf-transform meshopt t.glb level.uastc.meshopt.glb  # 10.1 MB
gltfpack -i level.glb -o level.pack.glb -cc -tc -tu     # 9.5 MB, 18.5 s, 239 MB RSS
npx gltf-transform draco level.glb level.draco.glb      # 11.5 MB, 21.9 s
```
\*The synthetic grid is very regular, so expect much less extra gzip gain on real lidar meshes.

Gotchas:
- **Order matters.** Running `optimize --compress meshopt` and then `uastc` re-wrote the file *without* meshopt (40.9 MB). Do KTX2 first, then meshopt.
- `optimize` defaults to WebP textures. Pass the KTX2 steps explicitly.

In Chrome (`npm run test:glb`), on WebGPU and WebGL2:
- Load and decode times: **111 ms** for ETC1S and **193 ms** for UASTC, from localhost.
- Textures transcode to GPU format 37492 (ETC2 RGB) for ETC1S and 37808 (ASTC 4×4) for UASTC.
- A Rapier trimesh built from the decoded meshes gives a raycast of y = 0.801 at x=7 on the stairs (exact value 0.80). Quantization error is about 1 mm.

**Bug to remember:** with KHR_mesh_quantization, `geometry.applyMatrix4()` writes back into int16-normalized attributes and clamps. Dequantize to Float32 first, as `glb.js` now does. Better still, ship collision as its own raw Float32/Uint32 (or meshopt-encoded) buffer per tile instead of deriving it from render meshes.

### 2.4 Splats mixed with meshes and physics [VERIFIED]
- `splat.html` on `WebGLRenderer`: 200k procedural splats (`SplatMesh({constructSplats})`) plus the probe trimesh plus Rapier walkers and car. Splat build takes 37–39 ms, no errors, and the physics verdict still passes. Screenshot: `splat-webgl.png`.
- The same page with `WebGPURenderer`: **throws** `this.renderer.getContext(...).getExtension is not a function`. Spark is WebGL2-only (r180+). A grep of `spark.module.js` finds no WebGPU path.
- Consequence: splats would force either the WebGL renderer for the whole game or a different splat renderer (Babylon or PlayCanvas, or a custom TSL splat pass). Physics never uses splats in any case. Collision always comes from lidar-derived meshes or heightfields, and splats are just visual "skin".

---

## 3. Renderer and engine comparison (2026 state)

| | three.js r186 | Babylon.js 9.32 | PlayCanvas 2.23 | Godot 4.7 web | Bevy 0.20 | Unity 6 / Unreal |
|---|---|---|---|---|---|---|
| WebGPU | yes, `WebGPURenderer` + TSL; WebGL2 backend fallback **[VERIFIED both]** | yes (mature), WebGL2 fallback | yes, WebGL2 fallback | **no**, WebGL2 Compatibility only [UNVERIFIED docs] | yes via wgpu | Unity WebGPU experimental; UE no web |
| License | MIT | Apache-2.0 | MIT (editor is SaaS) | MIT | MIT/Apache | proprietary |
| Physics | bring your own (Rapier/Jolt) | Havok plugin (closed WASM, MIT npm), JS character controller **[VERIFIED class exists]**, no vehicle | ammo.js built in | Godot physics / Jolt (native) | avian/rapier | PhysX/Chaos |
| 3D Tiles / geo | 3DTilesRendererJS, takram atmosphere + clouds (has a `./webgpu` export **[VERIFIED in npm exports]**) | 3DTilesRendererJS babylon build **[VERIFIED file]** | weak | none | none | Cesium for Unity/Unreal (native only) |
| Splats | Spark (WebGL only) | built-in GaussianSplatting (WebGPU) | best-in-class SOGS + streaming LoD **[GSplat* classes VERIFIED]** | addons | community | plugins |
| Bundle | three.core 379 kB + three.webgpu 690 kB raw, **235 kB brotli** total **[VERIFIED]** | larger (tree-shakable ES modules) | ~1.5 MB min | 30–40 MB wasm+pck [UNVERIFIED] | 10–30 MB wasm | 20–100 MB |

Three.js TSL effects shipped in r186 (verified file list in `three/examples/jsm/tsl/display/`):
- GTAO, SSAO, SSGI, SSR, SSS, TRAA, TAAU, SMAA, FXAA, FSR1
- Bloom, DepthOfField, MotionBlur, Godrays, Lensflare, LUT3D, Denoise, OIT
- `csm/CSMShadowNode`, `objects/SkyMesh`, `objects/WaterMesh`, `tsl/lighting/ClusteredLightsNode`

Note: `PCFSoftShadowMap` is removed in WebGPURenderer (warning seen), so use `PCFShadowMap`, VSM or CSM.

---

## 4. Physics in WASM

| | Rapier 0.21.0 | JoltPhysics.js 1.1.1 | Havok 1.3.14 (Babylon) | ammo.js / cannon-es |
|---|---|---|---|---|
| Character | `KinematicCharacterController`: autostep, snap, slope limits **[VERIFIED]** | `CharacterVirtual` + `ExtendedUpdateSettings` (stick-to-floor, walk-stairs) **[VERIFIED class exists]** | Babylon `PhysicsCharacterController` (JS) **[VERIFIED d.ts]** | DIY |
| Vehicle | `DynamicRayCastVehicleController` (rays only) **[VERIFIED]** | `WheeledVehicleController`, `MotorcycleController`, `TrackedVehicleController`, cylinder-cast wheels **[VERIFIED classes]** | none (only shape-cast APIs: 130 `HP_*` fns, no vehicle) **[VERIFIED]** | btRaycastVehicle (ammo) |
| Trimesh limits | 2M tris OK; ~135 B/tri; build 0.7–2.2 s/2M **[VERIFIED]** | **npm build fixed 128 MB heap, OOM at ≥500k-tri MeshShape** even with `INITIAL_MEMORY` **[VERIFIED]**; 180k tris builds in 262 ms | [UNVERIFIED] fast native | slow, stale (2022) |
| Threads | single thread (SIMD variants: `@dimforge/rapier3d-simd(-compat)`) | multithreaded build needs COOP/COEP (memory fixed at 2048 pages = 128 MB) **[VERIFIED in js]** | single | single |
| Repo | moved into `dimforge/rapier` monorepo `typescript/` (rapier.js repo archived) **[VERIFIED]** | active (pushed 2026-10-23) | Babylon | – |

Decision: **Rapier now**, because it has proven character and vehicle behaviour, growable memory, Node parity for server-side validation and simple code. Revisit Jolt (custom build with `ALLOW_MEMORY_GROWTH`) if vehicle feel becomes the bottleneck. Do not use ammo.js or cannon-es.

---

## 5. Streaming real-world data in the browser

- **3DTilesRendererJS 0.5.3** (Apache-2.0, pushed 2026-10-22). Its main target is three.js, and it also has `index.babylonjs.js` and `index.r3f.js` builds **[VERIFIED in package]**. Plugins: Cesium Ion / Google auth, GLTF extensions (meshopt, KTX2), fade, TilesFadePlugin, LRU caching. Google Photorealistic 3D Tiles work through `GoogleCloudAuthPlugin` [UNVERIFIED for this repo version]. Whether Google has photogrammetry for Turku is [UNVERIFIED]; for a private prototype it could serve as a distant-LOD backdrop, but our own lidar data is better up close.
- **CesiumJS 1.146** is a full globe engine with its own renderer. It is not a good fit for a game loop, physics or character work. Use it only as a data viewer.
- **loaders.gl 4.5.4** offers a 3D Tiles, LAS/LAZ and I3S parser suite. Use it in tooling or workers if needed.
- **Recommended tiling:**
  - Generate a static tileset on the server: `tileset.json` + GLB content (meshopt + KTX2), quadtree in a local ENU frame around EPSG:3877 x≈23459500, y≈6704500. Use 64 m leaves for full detail and 256 m / 1 km parents for simplified LODs.
  - Keep **collision tiles as a parallel grid** (`col/{x}_{y}.bin`: Float32 positions + Uint32 indices, optionally meshopt-encoded) that are loaded by a physics worker.
  - Cache in **OPFS** or the Cache API after first download [UNVERIFIED in probe].
- Use a local float origin (metres around the block centre) for everything in the scene and physics. EPSG:3877 coordinates of order 2.3e7 / 6.7e6 overflow float32 precision.

## 6. Photoreal rendering budget on M1 Max Chrome [VERIFIED, `npm run test:fx`]

Test conditions: headless Google Chrome 156 with flags `--disable-gpu-vsync --disable-frame-rate-limit` at 1920×1080, DPR 1. Scene: a 2M-triangle terrain plus 600 instanced PBR boxes plus 300 glossy instances, SkyMesh + PMREM environment, and a sun with CSMShadowNode (4 cascades, 2048²). Throughput is measured over 240 frames with a final GPU sync (`queue.onSubmittedWorkDone` / `readPixels`).

| Backend | Post-processing | ms/frame (throughput) | Draw calls | Triangles/frame |
|---|---|---|---|---|
| WebGPU | none | 3.36 | – | – |
| WebGPU | GTAO ½-res + TRAA + bloom | **3.75–3.83** | – | – |
| WebGL2 | none | 2.58 | 13 | 3.48M |
| WebGL2 | GTAO + TRAA + bloom | 3.43 | 31 | 5.77M |

The `renderer.info` counters stayed at 0 under the WebGPU backend in this probe, so the WebGPU rows have no draw-call or triangle counts. GPU timestamp queries (`trackTimestamp`) gave inconsistent numbers (9–17 ms, accumulated across passes), so trust the throughput column. Real assets with textures, overdraw, transparency and skinned characters will cost more, but **the M1 Max has a lot of headroom at 1080p** for a GTA-like target (16.6 ms).

The WebGL2 backend was slightly faster here. WebGPU's advantages are compute (GPU culling, particles, splat sorting), lower CPU cost with many draws, and the future direction of three.js. Keep the code TSL-only, so that the `forceWebGL` switch stays a one-liner.

Photoreal checklist:
- AgX or Neutral tone mapping; `SkyMesh` or takram `@takram/three-atmosphere/webgpu` for a physically based sky, aerial perspective and clouds.
- PMREM IBL refreshed with time of day; CSM; GTAO + TRAA; SSR on wet asphalt.
- Lidar-derived per-material PBR textures in KTX2.
- Decals for road markings, and instanced street furniture.

## 7. Reusable open-source parts (versions and activity checked 2026-10-25 via `npm view` / `gh api`)

| Purpose | Package / repo | Version | License | Last activity |
|---|---|---|---|---|
| Renderer | `three` / https://github.com/mrdoob/three.js | 0.186.1 | MIT | 2026-10-25 |
| Physics | `@dimforge/rapier3d-compat` / https://github.com/dimforge/rapier (typescript/) | 0.21.0 | Apache-2.0 | npm 2026-09-25 |
| Physics alt | `jolt-physics` / https://github.com/jrouwe/JoltPhysics.js | 1.1.1 | MIT | 2026-10-23 |
| Tiles | `3d-tiles-renderer` / https://github.com/NASA-AMMOS/3DTilesRendererJS | 0.5.3 | Apache-2.0 | 2026-10-22 |
| Asset tooling | `@gltf-transform/cli` / https://github.com/donmccurdy/glTF-Transform | 4.5.1 | MIT | 2026-10-20 |
| Mesh compression | `meshoptimizer` + gltfpack / https://github.com/zeux/meshoptimizer | 1.3.0 | MIT | 2026-10-14 |
| KTX2 | KTX-Software 4.4.2 (toktx, ktx) / https://github.com/KhronosGroup/KTX-Software | 4.4.2 | Apache-2.0 | 2026-10-25 |
| KTX2 alt | basis_universal / https://github.com/BinomialLLC/basis_universal | – | Apache-2.0 | 2026-10-23 |
| BVH / raycast | `three-mesh-bvh` / https://github.com/gkjohnson/three-mesh-bvh | 0.9.16 | MIT | 2026-10-22 |
| Navmesh | `recast-navigation`, `@recast-navigation/three` / https://github.com/isaac-mason/recast-navigation-js | 0.43.1 | MIT | 2026-07-06 |
| Steering / AI | `yuka` / https://github.com/Mugen87/yuka | 0.7.8 | MIT | repo 2026-10-20 (npm 2022) |
| Sky / atmosphere | `@takram/three-atmosphere`, `@takram/three-clouds` / https://github.com/takram-design-engineering/three-geospatial | 0.19.1 / 0.7.6 | MIT | 2026-05-27 |
| Splats | `@sparkjsdev/spark` / https://github.com/sparkjsdev/spark | 2.4.0 | MIT | 2026-10-20 |
| Splats alt | PlayCanvas engine + SuperSplat / https://github.com/playcanvas/supersplat | 2.23.3 | MIT | 2026-10-23 |
| Splats (stale) | `@mkkellogg/gaussian-splats-3d` | 0.4.7 | MIT | 2025-10 (inactive) |
| R3F path (optional) | `@react-three/fiber` 9.8.2, `@react-three/drei` 10.7.9, `@react-three/rapier` 2.2.0, `ecctrl` 2.0.2 (https://github.com/pmndrs/ecctrl) | – | MIT | 2026-08…10 |
| WebGL-only post-processing (if using WebGLRenderer) | `postprocessing` 6.39.5 (peer three <0.187), `n8ao` 2.0.1 | – | Zlib / ISC | 2026 |
| Path-tracer for reference shots | `three-gpu-pathtracer` 0.0.27 | – | MIT | 2026-10-23 |
| Reference game (exact stack) | https://github.com/brunosimon/folio-2025: three 0.183 `three/webgpu` + TSL + `@dimforge/rapier3d` 0.17 + `PhysicsVehicle.js` (addWheel) **[VERIFIED via gh code search]** | – | MIT | 2026-04-07 |
| Reference sketches | https://github.com/isaac-mason/sketches (Rapier/recast/kinematic character sketches) | – | MIT | 2026-05-06 |
| Racing reference | https://github.com/pmndrs/racing-game (R3F + cannon) | – | MIT | 2023 (stale) |
| Animations | Quaternius Universal Animation Library 1 & 2 (https://quaternius.itch.io/universal-animation-library, https://opengameart.org/content/universal-animation-library-2) | v2.1 / 2026-01 | CC0 | 2026 [UNVERIFIED] |
| Models | Kenney (kenney.nl), Quaternius, KhronosGroup/glTF-Sample-Assets | – | CC0 / various | – |
| Testing | `playwright` 1.64.1, `vite` 8.3.5 | – | Apache-2.0 / MIT | 2026-10 |

GTA-like open-source web games: the web search found only toy and vertical-slice projects (for example "gta7"-style three.js demos and PigCity on Rapier), none worth adopting [UNVERIFIED]. folio-2025 is the best production-quality code to study for vehicle feel, TSL materials and the game loop.

## 8. Delivery constraints for Chrome (measured)

- Bundle sizes: three core + webgpu are 235 kB brotli. The Rapier compat JS with inlined base64 WASM is 4.3 MB raw / **1.2 MB brotli**. The non-compat `@dimforge/rapier3d` ships a separate `.wasm` (3.08 MB raw, **852 kB brotli**) and needs `vite-plugin-wasm`, as folio-2025 does. The Spark chunk is 3.0 MB raw / 760 kB brotli. The basis transcoder is copied to `public/basis/`.
- COOP/COEP headers in the Vite dev and preview config make `crossOriginIsolated === true` and `SharedArrayBuffer` available **[VERIFIED]**. That is needed later for Jolt MT or WASM threads; the static host must send the same headers.
- Playwright `channel: 'chrome'` in headless mode uses the real Apple GPU (adapter `apple metal-3`, ANGLE Metal M1 Max) **[VERIFIED]**. CI-style visual and physics regression tests can therefore run on the Mac. The Linux server has no GPU, so browser tests there would fall back to SwiftShader [UNVERIFIED]; run them on the Mac.

## 9. Proposed architecture (next steps)

1. **Main thread:** three/webgpu renderer, input, camera, animation (AnimationMixer), tile streaming with 3DTilesRendererJS.
2. **Physics worker:** Rapier world. It receives collision tiles as transferable ArrayBuffers and builds or frees colliders (FIX_INTERNAL_EDGES only on hard-edge meshes). The main thread sends it inputs and gets back transforms (SharedArrayBuffer ring).
3. **Server pipeline (osgallery-dev, Node + Python + Blender headless):**
   - lidar → ground/curb/stairs extraction
   - render mesh + collision mesh per 64 m tile
   - gltf-transform: KTX2 first, then meshopt
   - tileset.json
   - Node-side Rapier validation that runs scripted walkers and cars over every tile and flags holes or unclimbable curbs (the `sim.js` verdict approach, applied per tile)
4. Characters: Quaternius CC0 rig + UAL animations, three `SkeletonUtils.retargetClip` for others; ecctrl-style floating-capsule feel can be ported if desired.
5. Traffic and pedestrians: recast navmesh per tile (built server-side with recast-navigation in Node), lane graph from OSM/municipal street data, yuka steering.

## 10. Risks / open items
- WebGPU + 3DTilesRendererJS has not been smoke-tested yet. Same for WebGPU + takram atmosphere and for the real Turku lidar mesh triangle counts.
- Spark splats are incompatible with WebGPURenderer, so committing to splats later means a renderer decision.
- Raycast vehicles ignore vertical curb faces, so curb strikes need custom impulses or shape-cast wheels.
- A 2M-triangle collider costs about 270 MB WASM and about 1–2 s to build. Collision LOD and worker builds are mandatory.
- The Jolt npm build has a 128 MB heap; a custom build is needed if switching.
- The rapier.js repo is archived and moved into the monorepo, so watch API churn (0.17 → 0.21 changes were minor in this probe).

---

## Verification (adversarial pass, 2026-10-25)

An independent verifier tried to refute the claims that drive architecture decisions. Every check below was run in this pass with new code. None of it reuses the probe's scripts or geometry. Scratch code is in `/private/tmp/claude-501/-Users-wnt-Documents-game-test/360b6725-0759-4d22-88a7-b80c4b97be51/scratchpad/verify/` (`phys.mjs`, `tm.mjs`, `jolt/j.mjs`, `jolt/ji.mjs`, `tiles/`).

| # | Claim | Verdict | How it was checked |
|---|---|---|---|
| 1 | Rapier 0.21 KCC (autostep 0.25/0.15, snap 0.3, 45°) climbs curbs and stairs on a trimesh and is blocked by steep slopes | **CONFIRMED** | New box-step trimesh (`phys.mjs`). Step heights 12, 17, 24 and 27 cm were all climbed, and 35 cm was blocked. With autostep **disabled**, a 12 cm curb blocks the capsule (x stops at 4.76), while 5 cm is climbed by the capsule's rounding. So curb climbing comes from autostep and not from capsule rounding, which means curb geometry in collision really matters. Note that 27 cm passed even though maxHeight is 0.25, so leave about 3 cm of margin when tuning. |
| 2 | The Rapier raycast vehicle "mounts a 12 cm curb" | **CONFIRMED, but the report understates the problem** | Same `phys.mjs`, with curbs of 12, 15, 20, 30, 38 and 45 cm. The car rises by exactly the curb height **at any speed (1.5–6 m/s) up to 38 cm**, with no loss of speed (min vx 2.47 m/s at 3 m/s target). Only at 45 cm does the chassis box hit the face and stop. A 38 cm step would stop a real car. **Correction:** the report's advice to "raise rest length/travel for 15–20 cm curbs" is unnecessary, because they already mount with no resistance. The real requirement is the opposite: curb resistance, impact and damage must be **custom code** (wheel shape casts against the curb face, impulses, or speed-dependent blocking). Collision-level curbs give cars the height change for free but no "reaction". |
| 3 | 2M-triangle Rapier trimesh builds in about 0.74 s (about 2 s with FIX_INTERNAL_EDGES), raycasts cost about 2 µs, and query cost does not depend on mesh size | **CONFIRMED** | `tm.mjs` with a noisy (non-flat) terrain: 2M triangles took 778 ms without flags and 2022 ms with FIX_INTERNAL_EDGES, raycasts 2.4 µs. A **125k-triangle tile with FIX_INTERNAL_EDGES builds in 72 ms**, which supports the 64 m collision-tile design. |
| 4 | The Jolt npm build has a fixed 128 MB heap and aborts with OOM on a MeshShape of 500k triangles or more | **REFUTED as stated (the heap limit itself is CONFIRMED)** | `jolt/j.mjs` and `ji.mjs` with jolt-physics 1.1.1 in Node 24. The heap is fixed at 128 MB in both the release and debug wasm-compat builds, and the debug abort text says it was built without ALLOW_MEMORY_GROWTH. The OOM threshold, however, depends on the input path. A `TriangleList` (non-indexed) build succeeds at 320k and OOMs at 500k. An **`IndexedTriangleList` + `VertexList` build of 500k triangles succeeds (602 ms)** and only about 1M OOMs. Per-tile collision meshes of 50–150k triangles therefore fit within the stock npm Jolt. A custom build is needed only for big monolithic meshes or many resident tiles. This makes Jolt a more realistic plan B than the report suggests. |
| 5 | Spark 2.4 splats do not work with WebGPURenderer | **CONFIRMED** | The `spark.module.js`/`cjs` dist contains 0 occurrences of "webgpu". 2.4.0 (2026-10-20) is the latest npm version. GitHub issue sparkjsdev/spark#394, "three.js WebGPURenderer support with `forceWebGL` enabled", is still **open**. |
| 6 | Babylon + Havok has no vehicle controller | **CONFIRMED** | `@babylonjs/core` 9.32.0 has no vehicle files under `Physics/`. `@babylonjs/havok` 1.3.14 exports 130 `HP_*` symbols, of which only ray and shape casts are relevant (`HP_World_ShapeCastWithCollector` etc.) and none relates to a vehicle or wheel. Shape casts would allow a better curb-aware wheel than Rapier's rays, but it would have to be written by hand. |
| 7 | 3DTilesRendererJS works with WebGPURenderer (UNVERIFIED in the report) | **CONFIRMED (upgraded to VERIFIED for local tilesets)** | `tiles/` uses three 0.186.1, 3d-tiles-renderer 0.5.3, `GLTFExtensionsPlugin` (KTX2Loader + MeshoptDecoder), and a 4-tile `tileset.json` whose content is the probe's meshopt+ETC1S GLB. In headless Chrome with the Metal GPU, **both the WebGPU and the forceWebGL backends** loaded 4/4 tiles with 0 failures (469 ms on WebGPU, 716 ms on WebGL2). Textures arrived as format 37492 (ETC2), and the screenshot showed the meshes rendered. The only warnings were a favicon 404, the `renderAsync` deprecation and "tiles 1.1 limited support" (use `asset.version: "1.0"`). Google Photorealistic tiles and takram atmosphere under WebGPU remain **untested**. |
| 8 | A GPU-less Linux server can produce meshopt+KTX2 GLBs, and the KTX2 step must run before meshopt | **CONFIRMED, plus a new gotcha** | On osgallery-dev with the existing tools (`ktx v4.4.2`, gltf-transform 4.5.1): running `meshopt` then `etc1s` gave a 36.5 MB file whose `extensionsUsed` **no longer lists EXT_meshopt_compression**, which confirms the order gotcha. New finding: the one-step command `gltf-transform optimize level.glb out.glb --compress meshopt --texture-compress ktx2` works on the server (18 s, 566 MB RSS) and gives 1.76 MB. But `optimize` **merges meshes and simplifies them by default** (`--simplify true`, error 1e-4 of extent): the 2M-triangle grid became 157k triangles. **Never run `optimize` with default settings on collision or curb-detail meshes.** Use `--simplify false --join false`, or run the explicit steps. Temporary files were deleted. |
| 9 | `applyMatrix4` on KHR_mesh_quantization geometry "clamps" positions | **CONFIRMED in conclusion, mechanism corrected** | three 0.186.1 test with Int16 normalized positions and a ×50 scale plus translation: the result is **wrapped garbage** (`[-150, 0, 50, 32668, -32768, 0]` in the raw array), not clamped values. Dequantizing to Float32 before transforming is still required. |
| 10 | Godot 4.7 web export is WebGL2/Compatibility only | **CONFIRMED (docs, not hands-on)** | The official docs page https://docs.godotengine.org/en/4.7/tutorials/export/exporting_for_web.html says Godot 4 web targets WebGL 2.0 only and WebGPU is not supported. A third-party release write-up (app.cinevva.com, 2026-06-19) retracted an earlier claim that WebGPU had shipped behind a flag. |
| 11 | The rapier.js repo is archived and moved to the monorepo | **CONFIRMED, path corrected** | `gh api repos/dimforge/rapier.js` returns archived=true. The JS bindings live at **`dimforge/rapier/bindings/typescript`**; there is no top-level `typescript/` folder. |
| 12 | folio-2025 uses three/webgpu, Rapier and a raycast vehicle | **CONFIRMED** | `gh api`: package.json lists three ^0.183.2 and @dimforge/rapier3d ^0.17.3. `PhysicsVehicle.js` line 1 is `import * as THREE from 'three/webgpu'` and calls `createVehicleController` and `addWheel`. Code search finds 30 files that reference `three/webgpu`; the report says 81, which does not matter. |
| – | Render budget of 3.8 ms/frame on WebGPU and 3.4 ms on WebGL2 at 1080p | **UNCERTAIN (not re-run)** | Not independently re-measured. The numbers come from a synthetic scene, so treat them as an upper bound on headroom. |

### Net effect on the recommendation
- The main stack still holds: three/webgpu, Rapier, meshopt+KTX2 and 3DTilesRendererJS. The biggest open risk, 3D Tiles under WebGPU, now passes a local smoke test.
- **Vehicle–curb interaction is a known gap and needs dedicated work.** Rapier ray wheels climb any step lower than the chassis underside, up to 38 cm in this test. Budget a custom curb-strike system or shape-cast wheels from day one, because a curb that exists only in collision geometry will not make cars react realistically.
- Jolt plan B is cheaper than the report says. Tile-sized collision meshes (≤500k triangles via IndexedTriangleList) fit within the stock 128 MB npm build, but many resident tiles would still hit the cap.
- In the pipeline, guard against `gltf-transform optimize` silently decimating curb geometry.
