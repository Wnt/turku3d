# Rendering

The renderer uses three.js **0.186.1**, `three/webgpu`, on both WebGPU and the `forceWebGL` WebGL2 backend.
Play runs a **30-minute cycle: 20 min day (07:00–19:00), 4 min dusk, 4 min night, 2 min dawn**, Europe/Helsinki.
`core/dayCycle.ts` derives the clock from UTC (the server's welcome time aligns it), so every client agrees. The sun
follows a fixed synthetic path decoupled from the calendar and stays below −12° for about four real minutes per cycle.
The clock has no slider. `?time=HH:MM` freezes a local view of the cycle at that hour; `?cycle=MIN` shortens the cycle locally.
QA defaults to the unchanged **2026-10-15 14:00 Europe/Helsinki**. `?time=ISO` pins an astronomical instant and disables the cycle
(encode a literal `+` as `%2B`). `__game.dayClock.snapshot()` exposes measured clock/IBL costs. F3 toggles the debug HUD.

## Lighting and post-processing

`timecycle.json` is our own table, tuned by eye. Its light rows are keyed by **sun elevation** (so QA's calendar dates and
the synthetic cycle share one table): sun colour/intensity (0 below −1°, the sun stays visible and shadowed),
IBL intensity, hemisphere diffuse fill (neutral daylight; cool artificial ambient at night), fog colour/range,
exposure, bloom strength/threshold, the night factor and the twilight/night sky colours added below the horizon (a blue hour around −4°, then a city-lit night
horizon; black by day). The lit-window share is keyed by clock hour (15–60 %, peaking at 20:00, thinnest at 04:00).
The night grade keeps lamps, windows and their pools in colour but lets dim light fall to the table's `mesopic`
saturation (a luminance-keyed uniform in the final grade): autumn crowns and painted walls no longer glow in the dark.
All of these are uniforms: the sun is never hidden and no light is added or removed, so day and night share every
pipeline (`tests/unit/night-pipelines.test.ts` builds the real r186 programs at 13:00 and 23:30 on both backends).
At elevations of 12° and above, sun intensity 4.0, IBL 0.08 and neutral hemisphere fill 1.0 retain strong
street-level sun–shade separation; daylight exposure 0.9 lifts shaded street detail. Dawn, dusk and night keep their own keyed values.
Time edits also reposition the directional sun around its existing target, so a stationary QA camera gets the
same lighting and shadow direction as the visible sky without requiring another camera move.
Daylight PMREM omits the solar disc, avoiding a second unshadowed source of sunlight in rough reflections.
The visible disc is unchanged; the bake blends to the established twilight behaviour between 12° and 6°.
`Environment.setWeather()` derives sun, diffuse fill, exposure and fog from the clear timecycle sample on every
change. Clear/broken cloud preserve that sample exactly; overcast shifts direct light into diffuse fill. Night
light levels stay calibrated while fog can shorten visibility. Sky weather is forwarded to the atmosphere hook;
the weather controller schedules IBL refreshes rather than baking on every render frame.
`renderer.lighting` is r186 `DynamicLighting` (`lighting.ts`) with fixed point-light slots per tier (0 mobile, 0 low,
4 medium, 8 high) in `Environment.lamps.point`; only slots with intensity > 0 enter the shader loop. Its lights node
re-adds the material lightings (scene IBL, light maps, GTAO and material AO) that r186's `DynamicLightsNode` drops,
and keeps the hemisphere light on r186's per-light node (the batched hemisphere loop cost about 2 ms GPU on an M1 Max).
Each batched light type costs uniform buffers per fragment stage (point 3, spot 4; WebGPU guarantees 12), so spot
slots need a packed data node first; the pipeline test caps every tested program at 10.
Facade-kit walls light glazed cells at night (atlas glass is roughness 0.08); non-kit building walls use a
2.6 × 3.2 m world grid gated by their ORM glass (or dark albedo). Batched meshes whose object ranges are all roofs
(ortho roofs) are roofs, not walls, and get no grid windows. Each window's on/off state is an exact u32 hash of
per-triangle constants (or its wall plane), its cell and two-hour epochs of the shared clock: no draw, asset or random state.
A lit window is brighter towards its ceiling; one in five is a dimmer cool room. Glass modules (curtain walls and
shopfronts) show a lit ceiling band falling off into the room instead of one evenly glowing floor-to-ceiling cell.
Night raises bloom (not allocated on mobile), shadow cascades are not re-rendered while the sun has no intensity
(only after startup warmup), and deep-night IBL refreshes every two minutes. The river's unlit deep colour darkens with the night.

- `SkyMesh` supplies analytic Rayleigh/Mie scattering. It was smoke-tested under both backends before adding effects;
  no takram dependency is needed. Clouds are off for this clear-afternoon scene.
  The sky box (2250 m half-extent) re-centres on the rendering camera in `onBeforeRender`, so it surrounds the
  player anywhere in the world; its colour depends only on view direction.
- A 128-pixel PMREM cube derived from the same sky (plus the twilight/night term) provides IBL. It is regenerated on the clock's cadence, reusing the target to retain material bindings and shader programs. Sun vectors use `core/sun.ts`, including true-north to grid-north convergence.
- `CSMShadowNode` uses custom street-scale splits, PCF, a 2.5 cm normal bias, cascade fades and the addon's existing
  light-space texel snapping. FOV/aspect changes update the cascade frustums. Camera motion does not resize them.
- An opaque normal/velocity/depth prepass feeds GTAO. AO radius is 0.6 m and the effect only attenuates indirect light,
  with a low blend strength to avoid doubling the occlusion already in facade albedo.
- Velocity uses **signed half-float** storage, independently of the unsigned-byte packed normals. Three.js lazily
  clones the first attachment when creating another MRT texture; inheriting the normal format clamps negative motion
  to zero and quantises small motion, leaving old car and wheel edges in temporal history.
- TRAA runs at full output resolution with MSAA disabled. Daytime bloom is subdued (HDR threshold 1.5), followed by subtle
  desaturation/warm grading and **AgX**, daytime exposure **0.9** (the timecycle raises both at night). Tone mapping and
  sRGB conversion happen exactly once.
- QA `pass=id` bypasses IBL generation, material styling and all post-processing; there is no AA, grading, exposure,
  fog or shadow modulation. Colour QA waits for 32 settled frames to let temporal history converge.
- **Street lights** ([lights/](lights/index.ts)): every light corona in the resident window is one instanced additive
  draw in its own depth-less pass, added to the HDR colour after TRAA and bloom (no jitter, no history smear). Its
  vertex stage uses the unjittered projection and clamps coronas to 2..48 output pixels; each fragment compares its
  own depth with the opaque prepass depth (beauty depth on mobile), so walls hide lamps behind them. Lamp posts are
  one `InstancedMesh` holding only lamps within 125 m, scaled to zero between 105 and 125 m on the CPU, so every
  pass keeps one count-independent program. `?lights=0..1` fixes the night level. Where lights are is tile data:
  each draft manifest carries a `lights` list of 16 B records from the generator (register street-light posts,
  lamps hung over a carriageway, facade lights; `pipeline/turku3d/props/lights.py`), and the client adds only
  the tunnel strips and bridge-deck lamps the generator builds as geometry (`lights/tileScan.ts`). Their posts
  have static collision in the tile's collision cells.
- **Street furniture** batches (`props_palette`) switch off beyond 125 m from the camera and back on within
  115 m (`world/propLod.ts`): a hard switch per tile batch, no discard or vertex fade, so no program changes.
- **Light pools** ([lights/pools.ts](lights/pools.ts)): every 250 ms the street and bridge lamps nearest a point 12 m
  ahead of the camera take the tier's point slots (4 on medium, 8 on high), scaled by the night factor (0 by day, so
  no slot enters the shader loop). A chosen lamp's weight falls to 0 as its distance approaches the first lamp left
  out, and a slot fades (0.6 s) before it moves to another lamp, so pools never pop. Point lights cast no shadow, so a
  lamp whose ground line of sight from the camera crosses a building footprint (a 2 m raster per tile from the lamp
  scan) takes no slot, and the light hangs a third of the head height below the head, clear of the lamp's own arm.
  The choice is purely local to this camera; the lamps and the night level are already identical on every client.
  Facade and tunnel lights stay coronas only.

`?aa=off|taa|fxaa` is a diagnostic selector (default `taa`, or `fxaa` for mobile quality) on both backends. `off` bypasses colour AA; `fxaa`
uses spatial AA. Both disable TRAA jitter/history and the AO temporal sampling pattern, while preserving lighting,
bloom and grading. `pass=id` continues to bypass post-processing entirely.

These are permanent user quality settings (`?quality=mobile|low|medium|high`). Phones and tablets default to
**mobile**, including when using a controller; desktops and fixed-camera QA default to **medium**.
Explicit quality/AA choices override the defaults. Mobile quality uses one 1024² shadow cascade covering 90 m,
FXAA, smaller vegetation budgets, and sky-PMREM river reflections. It omits GTAO, the normal/velocity scene
prepass (unless TAA is explicitly selected), bloom, and the planar city reflection pass. Existing facade
materials, textures, physics and world residency are preserved.

| Setting | Low | Medium | High |
|---|---|---|---|
| CSM distances (m) | 50 / 180 | 16 / 50 / 200 | 12 / 32 / 80 / 240 |
| Shadow maps | 1024² | 2048² | 2048² |
| GTAO resolution / samples / blend | ½ / 4 / 0.22 | ½ / 8 / 0.28 | ¾ / 16 / 0.30 |
| TRAA subpixel correction | off | on | on |
| Bloom resolution / strength | ¼ / 0.035 | ¼ / 0.045 | ½ / 0.050 |
| River reflection resolution / still-camera updates per second | ¼ / 8 | ½ / 12 | ¾ / 20 |
| River updates per second during slow motion | 8 | 12 | 20 |
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
Reflections render static terrain, buildings, structures and the sky on a dedicated layer, excluding actors,
vegetation and small props. They use the default lighting context rather than sampling the main camera's GTAO.
A moving camera re-renders the reflection every frame (an older image visibly trails the view); only a still
camera reuses it at the tier's slow rate.
`MaterialStyler.prepare` serializes `compileAsync` work in each river's actual target and virtual-camera context;
new published/draft models remain uncommitted to the scene until their final ground bindings, uploads and programs
are ready. Published parse promises retain the existing REPLACE parent during preparation. Prepass, beauty and
reflection use one-draw `compileAsync` calls with stable matching contexts. Final shadow flags and
proxy geometry are prepared before compilation; publication does not change warmed shader keys. Vegetation
then selects its final per-LOD flags. r186's asynchronous node builder receives a view of the captured target,
MRT and flow context across shader-stage yields while live renderer state is restored immediately. A scoped
r186 context-manager adapter selects each pass's observed nesting depth (also part of shader cache identity).
GTAO, normal/beauty passes and river reflections skip image updates during native precompilation, avoiding
unnecessary whole-scene draws at different nesting depths. The shared GTAO effect retains its first
setup graph for this renderer/camera instead of replacing its fullscreen material whenever a streamed
surface references AO; native sample-count updates still rebuild that graph when needed. `game/scripts/tile-warmup-probe.ts` is a development-only
synthetic reproduction; it is never imported by the game entry.
Startup uploads every map, then warms the complete 360° ring with a real draw without frustum culling and six views per selectable spawn. It skips per-model async compilation until Ready; real prepass, beauty, reflection and shadow draws create their actual pipelines without repeating one async yield per material. QA updates reflections every rendered frame.
`GameRenderer` separates opaque/cutout shadow overrides and keeps their depth context stable across colour and
reflection passes: alternating the shared override's alpha-test state otherwise invalidates all caster keys per draw.
Normal source-material and dynamic shader invalidation checks remain active.

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
on both backends. Play starts at `min(devicePixelRatio, 2, sqrt(2560*1440/(CSS width*CSS height)))`.
Mobile play instead caps the initial ratio at 1.5 and the drawing buffer at 1280×720 pixels. Its whole play/render
update runs at 30 FPS through `FramePacer`, independently of display refresh rate. Skipped refreshes do no game
or render work; the physics worker keeps its fixed 60 Hz tick. Hidden pages suspend the play update and clear
physics input. This does not depend on whether the active input is touch, keyboard or controller.
`AdaptiveResolution` targets 60 Hz from measured frame intervals: a 30-frame window lowers scale by 0.1 when
median delivery exceeds 19 ms or over 20% of frames miss 20 ms. Changes are at least one second apart.
Scale bottoms out at 0.5 of the initial pixel ratio (one quarter of its pixels). Eight seconds of healthy
16.7 ms delivery permits a cautious 0.05 upward probe, up to the initial ceiling. Isolated intervals over
80 ms and hidden-tab gaps reset the sample window. The selected scale is available at `__game.resolution.scale`.
Material detail, texture resolution, shadows and the 360° residency ring retain their quality settings.
On mobile, delivery thresholds follow the 30 FPS target, and measured CPU/GPU frame cost also lowers resolution
above 70% of the 33.3 ms budget. Recovery requires cost below 45% for 30 seconds; intentional pacing idle time is
not mistaken for slow rendering. The cost is the larger CPU/GPU stage, since the stages overlap. This leaves
headroom for sustained play and streaming instead of continuously spending the device's peak performance.
QA always has DPR 1 and exactly the requested viewport dimensions, even above 1440p; adaptive resolution is
play-only. Camera projection and `__qaProject` remain in CSS pixels.

## Streaming GPU uploads

`textureUploads.ts` shares one queue per renderer across published tiles, drafts, actors, ground bindings and
startup warmup. A glTF `afterRoot` hook queues maps after the loader finalizes colour space and samplers and
holds model publication until uploads complete. Ground keeps its source material until its replacement maps
are resident. Shared texture/version pairs upload once. Pending jobs run largest first with a 2 MiB / 2 ms
budget per animation frame; three.js uploads a texture atomically, so a texture larger than the budget runs
alone and may exceed it. Shared ground-library sampler settings only invalidate arrays when they actually change; loading another tile
does not mark the same resident arrays for upload again. No map resolution or mip chain is discarded. Startup still warms all six directions.
The normal/beauty/shadow/reflection passes share one scene-matrix update per rendered frame.

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
  merged into 62.5 m shadow chunks per loaded tile; ground does not cast shadows.
  Each chunk retains whole source triangles, compact buffers and complete triangle bounds for cascade culling.
  Colour meshes and ID-pass object ranges remain separate. Shadow geometry is built
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
unloads, then disposes it after the last user. Reflections exclude blended/transmissive surfaces,
small structural parts, props and vegetation. Slow motion is measured against the last captured
pose (0.5 m / 1°); larger motion uses the tier's capped rate. Deterministic QA still refreshes
every frame. Water stays in the transparent queue to avoid the normal/velocity prepass; its
inclusive transparent draw time includes the nested reflection, not just alpha blending.
This prevents each tile from rendering the
same river reflection and shadow cascades again. Assembler material batches retain source
object index ranges; the ID pass restores those ranges for per-facade QA colors.

Air reproduction and validation use the capture-only [renderer scenario](../../../tools/perf/air-scenarios/renderer.md).


`make render-perf-dev` runs the same four acceptance specs and unchanged backend budgets against Vite dev,
using the normal browser slot. It matches dev-served Air captures without requesting a production build slot.
`make render-perf` remains the production-build command. Both write per-commit measurements under
`$TURKU3D_WORK/reports/perf/`.

Android main-thread follow-up (2026-10-29): boundary-band position/UV variants now
participate in startup warmup through a degenerate indexed mesh. It becomes hidden
when gameplay starts and retains the shared material's node-builder cache across
periods with no real edges. Dynamic bands keep their original appearance and bounds.
This closes the two synchronous first-use builds observed after the earlier
`first-play-stalls` fixes; it does not change mobile quality defaults.

Capture public gameplay without `?dev=1` on pinned Mac Chrome with touch emulation
(844×390 CSS pixels, DPR 3) and 6× CPU throttling:

```sh
TURKU3D_PROFILE_MOBILE=1 TURKU3D_PROFILE_CACHES=warm TURKU3D_PROFILE_SECONDS=60 \
  python3 scripts/dev/host_slots.py browser -- node game/scripts/initial-load-profile.mjs android-public
```

Add `TURKU3D_PROFILE_DRIVE=1` for the two-minute street-driving scenario. Add
`TURKU3D_PROFILE_EDGE_WARMUP=1` to wait for the deployed implementation and require
zero boundary-band node builds during play. Reports include fps, >50 ms frames,
node-build identities and CPU/trace captures under the sandbox's `reports/initial-load`.
The throttled Mac results compare this client implementation; they are not phone fps.

Generated node attributes from every render pass have explicit object/geometry retirement ownership.
This complements r186 geometry cleanup, which otherwise sees only the first draw context
and can retain later instance-matrix attribute views. Interleaved views share buffer ownership.

## Facade program interpolation

Facade-kit attributes are authored constants on each triangle, including integer atlas
module IDs and packed flags. `facadeAttribute` passes these fields through explicit flat
varyings on WebGPU and WebGL2. Perspective interpolation can perturb an integer across
`floor`, modulo or equality boundaries, selecting different atlas cells at adjacent pixels
and causing severe black speckling/flicker on iPhone GPUs. Surface UVs remain interpolated;
the original facade instructions, textures and atlas derivatives are preserved.

The visible atmosphere uses three samples of a generated, mipmapped density map for drifting cumulus and cirrus.
Wind UV phases come from shared UTC time, wrapped independently before float upload; QA fixes weather at zero.
Sky brightness transitions to 0.45 above a 15-degree sun elevation, reducing the pale daytime veil without
dimming clouds or changing dusk. PMREM temporarily restores brightness 1 so this visible-sky calibration does
not retune ambient lighting. Night uses keyed blue gradients with sparse stars and an occluded moon. Daylight fog
spans 500–12,000 m so the four-kilometre city retains distinct distance layers; deep-night fog converges to the
city-lit sky horizon. Both interpolate through the timecycle independently of streaming radius. `atmosphere.ts`
supports HMR without reloading the city.

`Atmosphere.setWeather({ cloudCover, overcast })` accepts the weather clock's current sample without integrating
local state. Cloud cover drives the existing cumulus threshold; overcast fills the sky with a lower grey deck
from the same density sample and hides celestial bodies. Partly cloudy (`.38`, `0`) preserves the fair-sky defaults.
Lighting can pass its final linear fog colour as `setWeather(sample, fogColor)` to join the overcast horizon
to distance haze. Without that argument, the keyed timecycle fog colour is used. Rain darkens the ceiling
above the horizon while the horizon retains the effective fog colour.
