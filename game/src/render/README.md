# Rendering

The renderer uses three.js **0.186.1**, `three/webgpu`, on both WebGPU and the `forceWebGL` WebGL2 backend.
Play repeats **2027-10-15 08:45–17:30 Europe/Helsinki** over 30 real minutes with a ten-second skip back to morning.
`core/dayCycle.ts` derives phase from UTC. During the skip, lighting returns smoothly through daylight; no night is rendered.
The clock has no slider. Sun/sky update each frame using existing shadow passes; sky IBL is reused for 30 seconds and throughout the skip.
QA defaults to the unchanged **2026-10-15 14:00 Europe/Helsinki**. `?time=ISO` pins time and disables the cycle
(encode a literal `+` as `%2B`). `__game.dayClock.snapshot()` exposes measured clock/IBL costs. F3 toggles the debug HUD.

## Lighting and post-processing

- `SkyMesh` supplies analytic Rayleigh/Mie scattering. It was smoke-tested under both backends before adding effects;
  no takram dependency is needed. Clouds are off for this clear-afternoon scene.
  The sky box (2250 m half-extent) re-centres on the rendering camera in `onBeforeRender`, so it surrounds the
  player anywhere in the world; its colour depends only on view direction.
- A 128-pixel PMREM cube derived from the same sky provides IBL. It is regenerated only on time edits, reusing the target to retain material bindings and shader programs. The sun uses `core/sun.ts`, including true-north to grid-north convergence.
- `CSMShadowNode` uses custom street-scale splits, PCF, a 2.5 cm normal bias, cascade fades and the addon's existing
  light-space texel snapping. FOV/aspect changes update the cascade frustums. Camera motion does not resize them.
- An opaque normal/velocity/depth prepass feeds GTAO. AO radius is 0.6 m and the effect only attenuates indirect light,
  with a low blend strength to avoid doubling the occlusion already in facade albedo.
- Velocity uses **signed half-float** storage, independently of the unsigned-byte packed normals. Three.js lazily
  clones the first attachment when creating another MRT texture; inheriting the normal format clamps negative motion
  to zero and quantises small motion, leaving old car and wheel edges in temporal history.
- TRAA runs at full output resolution with MSAA disabled. Bloom is subdued (HDR threshold 1.5), followed by subtle
  desaturation/warm grading and **AgX**, exposure **0.65**. Tone mapping and sRGB conversion happen exactly once.
- QA `pass=id` bypasses IBL generation, material styling and all post-processing; there is no AA, grading, exposure,
  fog or shadow modulation. Colour QA waits for 32 settled frames to let temporal history converge.

`?aa=off|taa|fxaa` is a diagnostic selector (default `taa`) on both backends. `off` bypasses colour AA; `fxaa`
uses spatial AA. Both disable TRAA jitter/history and the AO temporal sampling pattern, while preserving lighting,
bloom and grading. `pass=id` continues to bypass post-processing entirely.

These are permanent user quality settings (`?quality=low|medium|high`, default **medium**), not feature flags:

| Setting | Low | Medium | High |
|---|---|---|---|
| CSM distances (m) | 50 / 180 | 16 / 50 / 200 | 12 / 32 / 80 / 240 |
| Shadow maps | 1024² | 2048² | 2048² |
| GTAO resolution / samples / blend | ½ / 4 / 0.22 | ½ / 8 / 0.28 | ¾ / 16 / 0.30 |
| TRAA subpixel correction | off | on | on |
| Bloom resolution / strength | ¼ / 0.035 | ¼ / 0.045 | ½ / 0.050 |
| River reflection resolution | ¼ | ½ | ¾ |
| Surface detail strength | 0.035 | 0.055 | 0.070 |
| Grading saturation | 0.98 | 0.97 | 0.97 |

References: [CSMShadowNode](https://threejs.org/docs/pages/CSMShadowNode.html),
[GTAONode](https://threejs.org/docs/pages/GTAONode.html), [TRAANode](https://threejs.org/docs/pages/TRAANode.html).
The installed r186 source is authoritative when later online docs differ.

## Materials and river

COMMON §4 remains the texture lookup contract: the **assembler** binds
`textures/library/<material_key>/{albedo,normal,orm}.ktx2`, or the per-building facade image with its library fallback,
into the GLB. The runtime does not guess filesystem URLs or perform extra texture requests.

`MaterialStyler` reads `material_key` from glTF material extras first, then the nearest ancestor's node extras.
It preserves glTF albedo, normal, ORM, colour factors, normal scale and UV transforms. Albedo stays sRGB; normal/ORM
stay linear as configured by GLTFLoader. Unknown keys keep their original material. Word-separated keys containing
`asphalt`, `granite`/`curb`, `cobblestone`/`cobble`/`setts`, or `grass` select restrained detail treatments.
Mipmapped grain varies roughness and albedo continuously in world space across tiles; grass adds broad colour patches.
A missing normal map gets subtle bump grain. Library normals still own cobble joints and curb stone relief.
Only the replacement materials and their private resources are disposed on tile unload; the tile loader owns GLB maps.

`water`/`river` keys select a planar river material. A plane derived from the mesh's local vertex/normal follows its
world transform. A non-recursive planar reflection, Fresnel response, sun highlight and two scrolling normal-map bands
provide the surface. The small periodic normal map is generated locally; there is no asset fetch. QA freezes waves.
Water geometry must be planar and have upward normals; vertical quay faces use their stone material instead.
The fixture tests render all material paths, water and the ID pass on both backends at all three tiers.

## Unbuilt far LOD

`unbuilt.ts` (play mode only) clones the materials of far LOD tile models (500 m and 1 km tiles) into node materials
whose colour turns grey at 70 % luminance with faint 8 m world-space stripes wherever the 250 m mask over the L0 grid
says no full-detail tile is drawn (published L0 tiles and drawn drafts, refreshed every 15 frames by `main.ts`).
Released models get their source materials back. Rationale and the edge marker: `world/README.md`, "The visible edge".

## HiDPI and screen-space error

**SSE is measured in drawing-buffer pixels**, never CSS pixels. `VisualTiles` passes
`renderer.getDrawingBufferSize()` to the tile renderer after every resize/update. The threshold remains 8 output pixels
on both backends. Play DPR is `min(devicePixelRatio, 2, sqrt(2560*1440/(CSS width*CSS height)))`, bounding fill rate to
1440p. For example, a 1280×720 Retina viewport renders at 2560×1440; a 2560×1440 Retina viewport also renders at
2560×1440. QA always has DPR 1 and exactly the requested viewport dimensions, even above 1440p. Camera projection
and `__qaProject` remain in CSS pixels. This rule avoids requesting finer LOD without actually rendering more pixels.

## Instrumentation

`window.__perf` returns a snapshot of the last 600 rendered frames:

- `cpuMs`: main-thread frame submission time (streaming, camera/game updates and rendering).
- `gpuMs`: a whole-frame GPU timestamp interval. WebGPU uses two timestamped one-invocation compute markers with
  real buffer writes (empty compute passes can be optimised away on Metal), and independent readback slots.
  WebGL2 uses one `EXT_disjoint_timer_query_webgl2` query spanning the frame. Disjoint results are rejected.
- `frameMs`: elapsed time between rendered frame starts. It includes scheduling and, in the perf test, GPU completion.
- `workMs`: conservative CPU + GPU interval for matching frame IDs; GPU intervals can include CPU submission gaps,
  so this is an upper bound, not a decomposition of wall-clock time.
- Each distribution reports `samples`, `p50`, `p95` (nearest rank). No samples means `null`, never a fabricated zero.
- `gpuStatus` says `timestamp-query`, `unsupported` or the timing error; `gpuSkipped` counts unavailable/disjoint slots.
- `drawCalls` and `triangles` count **all** passes, including shadows, prepass, reflections and fullscreen quads.
  Tile meshes use their own bounds for frustum culling. Their non-ground geometry is
  merged into one shadow caster per loaded tile; ground does not cast shadows.
  Colour meshes and ID-pass object ranges remain separate. The shadow mesh is built
  at load time and disposed with its tile.
  They are captured inside the animation callback before three.js resets its counters. Resident triangles remain a
  separate streaming statistic. The HUD updates four times per second to keep percentile sorting out of the hot loop.

Built-in per-pass timestamp tracking is disabled after feature negotiation: r186's asynchronous query pool can reuse
query indices and drop frames while resolution is pending. Independent whole-frame queries avoid that ownership issue
and include GPU work between render passes. Unsupported timers are explicitly reported; they cannot pass WebGPU's gate.

## Verification (repo root)

```sh
make render-test                              # typecheck + focused synthetic unit tests
make game-test                                # all game unit tests
E2E_PORT=5232 make game-e2e                    # play, QA camera and material contract, both backends
E2E_PORT=5231 make render-e2e                  # all tiers/materials/ID pass + 1440p performance
E2E_PORT=5231 make render-perf                 # performance only, both backends
```

The performance test uses Chrome/Metal, 2560×1440, DPR 1, unlocked vsync, medium quality, a fixed autumn sun and the
versioned `street-v1` camera path. It drains startup work, warms 120 rendered frames and measures 240. A final GPU
completion fence and timestamp drain are included in `drainedMsPerFrame`, which must also meet the backend budget.
Fast CPU submission alone therefore cannot pass. Per-frame GPU queries independently verify the p95 execution budget.
Readback is asynchronous: Chrome batches completion callbacks, so waiting for each callback between frames would measure
browser message-delivery latency instead of rendering throughput. The route always ends after exactly 240 measured frames.
WebGL2 also fences each frame: ANGLE elapsed-time queries otherwise include overlapping stages from queued frames.
That fence/scheduling cost is included in the WebGL2 frame and drained-time budgets. The report records the pacing protocol.
CPU, GPU, conservative work and rendered-frame p95 must be ≤16.6 ms on WebGPU, ≤22 ms on WebGL2. WebGPU requires ≥90%
valid GPU samples. The report records commit, dirty status, browser, tileset SHA-256, dataset, path, settings and counters.
Results and screenshots are in `$TURKU3D_WORK/reports/perf/<commit>[-dirty]/`; each run also attaches its JSON to Playwright.
Run after committing for a clean per-commit record. Hardware performance tests are separate from the fast unit gate.

Default measurements use the **synthetic development tile (2,192 resident triangles)**. This is not evidence of the
final AOI's performance or photographic facade quality. To test assembled tiles served on an owned port:

```sh
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/dist" --port 5233
PERF_TILES=http://localhost:5233/tiles/tileset.json E2E_PORT=5231 make render-perf
```

Capture all three QA registry views and a synthetic street view on both backends at the same autumn timestamp:

```sh
cd game
npm run build
npm run preview
# In another terminal, using the same sandbox environment:
npm run render-qa
```

Before/after runs have per-view PNGs and a manifest with camera, time, backend and errors. Browser profiles are isolated
under `TURKU3D_BROWSER_DIR` (with the shared sandbox environment helper). The shader fixture is generated only into `$TURKU3D_WORK/build/game/` when
running render tests, and is not an AOI layer or shipped game asset.

The landing gate runs `tests/e2e/render-materials.spec.ts` (all tiers, both backends) and the HiDPI/time control
contract as part of its `game-e2e` stage. The fixed-path hardware budget remains an explicit `make render-perf`
acceptance run so unrelated concurrent browser stages do not invalidate timestamp measurements.


### Car motion regression

```sh
source ../sandbox.env
npm --prefix game run build
(cd game && ./node_modules/.bin/playwright test tests/e2e/car-ghost.spec.ts --workers=1)
```

The test drives the actual worker-controlled sedan at 10–20 m/s on the synthetic development tile, captures 20
consecutive frames from each of two roadside views, and reads GPU motion at a visible roof point. It compares the
history lookup against the previous projected physics position (maximum error 1.25 pixels), for both signs of motion
and both backends. Projected positions and GPU velocity pixels are sampled in the same render callback; frame IDs ensure that capture skips no rendered frames. The camera is held fixed during these
captures to isolate object motion from asynchronous chase-camera collision queries; vehicle and wheel updates use
the normal play path. PNG sequences and motion JSON are saved under `$TURKU3D_WORK/reports/car-ghost/after/`.
`GHOST_CAPTURE=before` selects a separate evidence directory for a baseline run. The test asserts actual motion and
10–20 m/s speed, so a stationary car or missing velocity cannot pass. The `@drive`/`@render` tags include it in the gate.

Coplanar river meshes share one reflection material and target across visual tiles. The
material styler counts tile references and transfers the target when its original tile
unloads, then disposes it after the last user. This prevents each tile from rendering the
same river reflection and shadow cascades again. Assembler material batches retain source
object index ranges; the ID pass restores those ranges for per-facade QA colors.
