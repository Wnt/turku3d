# Turku Open World: Architecture

Status: draft v2, 2026-10-25 (v2: office address, Street-View-referenced Codex texturing, Astra visual QA loop). Author: architect pass over the six research reports in `docs/research/`.
Guiding principle (user): **collision is faithful to the lidar; visuals need not match the city 1:1**. Textures are regenerated or improved with AI (Codex), using real photos as references, so the city is recognisable rather than a scan.
Target: a GTA-style open world of Turku that **runs in desktop Chrome** (WebGPU, WebGL2 fallback, WASM), served as static tiled files over HTTP.

Evidence tags used below:
- **[V]** verified hands-on and confirmed by the adversarial pass
- **[V\*]** verified, but the adversarial pass corrected a number (the corrected value is used)
- **[U]** unverified (docs or reasoning)
- **[R]** refuted claim that this document deliberately does *not* rely on

Source reports: [turku-data](research/turku-data.md), [streetview](research/streetview.md), [engine](research/engine.md), [reconstruction](research/reconstruction.md), [prior-art](research/prior-art.md), [ai-textures](research/ai-textures.md).

---

## 1. Decisions

### D1. Engine: three.js r186 `three/webgpu` + Rapier 0.21 WASM
- **Decision.** three@0.186.1 with `WebGPURenderer` and TSL. Use `forceWebGL` as the WebGL2 fallback. Physics is `@dimforge/rapier3d` 0.21 (non-compat build with `vite-plugin-wasm`, 852 kB brotli) running in a **physics Web Worker**. Build with Vite 8, stream with 3DTilesRendererJS 0.5.3, load glTF through `GLTFExtensionsPlugin` (KTX2Loader + MeshoptDecoder). Plain three.js, no React Three Fiber.
- **Rationale.**
  - Both backends run in headless Chrome 156 on the M1 Max Metal GPU [V].
  - The Rapier KinematicCharacterController handles short curbs, and a height-limited clearance move climbs measured steps through 35 cm at walking speed. Taller visible faces block the capsule. Curbs in the collision mesh therefore really change gameplay.
  - The Rapier raycast vehicle mounts curbs [V].
  - 3DTilesRendererJS loads a local meshopt+KTX2 tileset on both backends [V].
  - Frame cost of CSM + GTAO + TRAA + bloom is about 3.4–3.8 ms at 1080p. This is a synthetic scene and was not re-run [UNCERTAIN], so treat it as an indication of headroom only.
  - brunosimon/folio-2025 is a production reference on the same stack [V].
- **Rejected.**
  - Godot 4.7 web export: WebGL2/Compatibility renderer only [V, docs].
  - Unity and Unreal web builds: heavy, and no WebGPU path that fits a static tiled server.
  - Bevy: immature at this scale.
  - Babylon 9.32 + Havok: no vehicle controller [V]. Babylon stays the runner-up, mainly if splats under WebGPU become a must.
  - PlayCanvas: only if the project pivots to splats.
  - Jolt 1.1.1 is **plan B**, not rejected. The stock npm build has a fixed 128 MB heap [V], but a 500k-tri `IndexedTriangleList` MeshShape builds fine [V\*]. The original "OOM at 500k" claim is **[R]**. Jolt is the fallback if the Rapier vehicle feel is not good enough (WheeledVehicleController, cylinder casts).
- **Known gap [V].** Rapier ray wheels climb any step lower than the chassis underside (up to 38 cm) **with no speed loss**. "Car bumps over curbs" therefore needs custom code from day one: shape-cast wheels against curb faces, plus a speed-dependent impulse, jolt and damage system.

### D2. Data sources

| Layer | Source | Status | Role |
|---|---|---|---|
| Ground and heights | City lidar LAZ, `https://turku.asiointi.fi/3d/pistepilvi/<xmin>_<ymax>.laz` (the opaskartta URL gives a **301**; use `curl -L`), 500 m tiles, CORS `*`, range requests OK | [V] | DTM, curb and stair heights, building points, trees |
| Curb, stair, wall and ramp **positions** | Kantakartta WFS `turku.asiointi.fi/TeklaOGCWeb/WFS.ashx` (GML only): `kanta:Liikennevayla` 62520 curbs, 62522/3 sidewalk edges, `kanta:Rakenne` porras 66626, step edges 66618, wall top 66634, ramps 62540, fences | [V] | Breaklines and procedural stairs and walls |
| Street surfaces | `GIS:Katualueet` (street parts and surface material, incl. Portaat), `GIS:Keskilinjat`, `GIS:Varusteet`, `GIS:Puut`, `Liikennemerkit` | [V] | Material IDs, props, trees, signs |
| Buildings | City CityGML `bldg:Building_LOD2` (136 buildings per tile, ~31 roof surfaces each) **and** roofer v1.0.0 LoD2.2. Choose per building by a robust lidar residual (median/p75, not RMSE) | [V] | Building shells and collision |
| Ground texture | WMS 2022 true ortho 5.5 cm (no lean) is primary. 2025 ortho 5.2 cm for recency, the drone layer ~1.2 cm where it exists (patchy), 2026 IMS tiles 12.5 cm | [V] | Ground albedo and segmentation input |
| Facade imagery | Google Street View 16K panos via `streetlevel` 0.12.13; Apple Look Around (2026-06); Mapillary once a token exists | [V] | Facade labelling, projection and reference (offline only) |
| Materials | Codex `image_gen` library + trim sheets, local tiling, DeepBump normals, KTX2 | [V] | Generic photoreal surfaces |
| **Semantics** (user: OSM is a core source) | OSM (Geofabrik Finland extract + osmium clip; Overpass mirrors are flaky). Geometry: city vectors win on position; **OSM wins on meaning**: road graph (names, lanes, turn lanes, oneway, maxspeed, surfaces), traffic signals, crossings, bus stops, cycleways, building levels/roof shapes/entrances, shops and amenities (POIs for interaction and missions), street furniture tags, trees, landuse; osm2streets for lane polygons and intersections | [V] | Traffic AI lane graph, pedestrian navigation hints, POIs and interactions, building heights where lidar is outdated, props and gap fill |
| Optional backdrop | Google Photorealistic 3D Tiles (needs an API key, no caching) | [U] | Far LOD only, runtime only |

Not used: NLS (coarser, hosts unreachable today [U]); Apple Flyover (no Turku coverage [V]); CityGML textures (only about 5 % of URIs resolve [V]); the CityGML road TIN (no curb faces [V]); Google depth maps as geometry (planar proxy [V]).

**Facts we do NOT rely on:**
- The ±2 cm "absolute accuracy" from kantakartta spot heights is **[R]**: the check was circular, because the spot heights were digitised from the same lidar. Only the relative noise (~1 cm) is trusted.
- "~96 MB per LAZ tile" is **[R]**. Real tiles are 130–150 MB (max 305 MB), about 170 GB for the whole city.
- "~100 pts/m²" is **[R]**. Totals are 63–86 pts/m², and class-2 ground is about 8–33 pts/m². Merging near-ground **class-12** points is mandatory, because class 2 alone leaves 46 % of 1 m cells empty [V].
- Data epochs differ: lidar 2021-04, vectors 2022–2024, Google mesh ~2022, Street View up to 2026-07. Expect mismatches; the vectors win on position.

### D3. Coordinate system and origin
- **Source CRS:** EPSG:3877 (ETRS-GK23) + N2000 heights. The LAZ has **no CRS VLR**, so this is assumed [V]. It is consistent with the WFS and with the Street View pose fit.
- **World origin O = (E 23459500, N 6704500, H 0).** This is the NW corner of LAZ tile `23459500_6704500`, which contains the office block. **Office: Läntinen Rantakatu 15** (lat 60.44839, lon 22.26726 → E 23459668, N 6704254 → engine x ≈ 168, z ≈ 246).
- **Engine axes:** glTF Y-up, metres. `x = E − 23459500`, `y = H`, `z = −(N − 6704500)`.
- **Precision:** float32 at E≈2.35e7 resolves only 2 m [V]. Every vertex buffer is tile-local, and each tile carries its offset as a node translation (doubles in JSON). Physics runs in world-local coordinates. Add a floating-origin rebase once the play area exceeds ~8 km from O.
- **Lat/lon:** Street View uses WGS84 via pyproj. The rocktree Google OBJ sits on a **6,371,010 m sphere**, not EPSG:4978 [V\*].

### D4. Tiling
- **Grid:** square tiles aligned to the city's 500 m LAZ grid, ID `t{level}_{i}_{j}` with integer indices from O.
  - **Visual tiles:** L0 = 250 m (a LAZ tile is 2×2 game tiles). Coarser LODs are 500 m / 1 km / 2 km parents in a 3D Tiles 1.0 `tileset.json` (`asset.version: "1.0"`; this avoids the 1.1 "limited support" warning [V]).
  - **Collision tiles:** 62.5 m (16 per visual tile), loaded within about 150–250 m of the player. A 125k-tri tile with FIX_INTERNAL_EDGES builds in 72 ms [V], so the target is ≤ 150k tris per collision tile.
- **Meshing margin:** a 50 m buffer when building the CDT, then a clip. Edge vertices are shared exactly between neighbours, with no skirts in collision.
- **Formats:**
  - Visual: glTF 2.0 + `EXT_meshopt_compression` + `KHR_texture_basisu` (ETC1S albedo; UASTC+zstd normals). Run the **KTX2 step before meshopt** [V].
  - Collision: custom binary `.col` (Float32 positions + Uint32 indices + Uint8 material ID per triangle + compound primitive list), meshopt-encoded, sent to the worker as a transferable ArrayBuffer. Not glTF: this avoids the KHR_mesh_quantization dequantise bug [V] and the `gltf-transform optimize` default simplification (2M → 157k tris) [V].
- **Budgets (initial):** ≤ 300 MB GPU texture memory [U]; ≤ 150 MB resident WASM collision (≈ 135 B/tri [V] → about 1M resident tris); visual L0 tile ≤ 8 MB on the wire.
- **Playable area is a tile list.** `$TURKU3D_DATA/playable-tiles.json` (`{"level": 0, "tiles": [...], "core_bbox_en": [...]}`, migrated to the four office tiles `t0_0_0 … t0_1_1` when missing) names the L0 tiles that ship with full collision. `config.playable_tile_ids()` reads it (a sandbox's own `$TURKU3D_WORK/playable-tiles.json` shadows it), the `playable-tiles` area, `make refresh`, the live build, its per-tile budget checks and the integrator's data key follow it, and the game discovers tiles from `tileset.json`/`manifest.json` without code changes. The four core tiles require every layer; expansion tiles require ground, buildings and roads. The client refines at most nine L0 tiles at once (nearest 500 m parents first), so memory stays bounded as the list grows. `make tile-add T=t0_i_j` grows a sandbox's list by one adjacent tile and rebuilds the list-following layers (ground, buildings, massing, roads, props); `python -m turku3d.region list-tile` lists it in the data root for the next live build.

- **Two tiers (localhost).** Published tiles come from the full pipeline (lidar TIN with curbs, structures, generated
  facades, LOD parents). When the player comes within one tile of an L0 cell the published tileset lacks, the
  localhost companion service `tools/dev/coarse-tiles` builds a *coarse* tile of that cell in seconds
  (`pipeline/turku3d/coarse`: ~2 m key-point TIN with the true ortho, LOD2 footprints extruded to the mean roof
  height, street-polygon collision materials, water sensor) in the same `vis.glb` + `.col` format, and
  `game/src/world/coarseTiles.ts` streams it with a desaturated tint and a "draft tile" badge, hiding a published
  500 m leaf once all four quadrants are drafted. When the published manifest later gains the tile, the draft is
  swapped out. The coarse tier skips curbs, stairs, walls, bridges, props, vegetation, parked cars, facade maps and
  roof shapes; it is never published to Pages or written into the data root's published layers.
- **Localhost runs main, hot (user decision 2026-10-27).** `live` and Pages serve every release that builds
  (user decision 2026-10-27 21:00: advisory checks never block them); the localhost game
  (`tools/dev/localhost-game`) runs main's checkout through the Vite dev server with a badge
  `main <commit> · unverified | verified | tests red: <stage> | verification failed: <stage>` from the integration
  rows. `main.ts` owns the
  world (renderer, tiles, physics worker with the character and vehicles) and hot-swaps `play.ts`, the play session,
  carrying missions, money, permit, clock and the driven vehicle; changes outside the session autosave and reload
  into the autosave. `/tiles` there serves a content-addressed tile release (`$TURKU3D_DATA/tile-store/<key>`, key =
  tile-build inputs of the commit + the data root's parts) that the integrator's data lane fills right after its
  tile-build stage and the service fills by a quick `make tile-build` of the affected tiles, and live and Pages ship
  the same set (`scripts/dev/served.py`); the open tab swaps a new
  release in place (changed collision tiles replaced in the worker, the visual tileset swapped once the new one has
  settled; `game/src/world/README.md`). The hot hooks exist only
  under `import.meta.hot`; production builds are unchanged. See `tools/dev/localhost/README.md`.
- **Pages ships a chosen region; nothing good is lost (user decision 2026-10-27).** Every tile build records a
  per-tile quality ledger (`turku3d.region.quality`: buildings, facade coverage by map source, ground texture
  kind, trees, parked cars, wire bytes, build time, validator) that travels with the tile tree; live publication
  merges it into `$TURKU3D_DATA/tile-quality.json`. The playable list only grows, a live build fails rather than
  ship without a listed tile, and the tile store keeps an entry for every listed tile. The GitHub Pages release
  ships the listed tiles within a radius of a centre (default 1 km around Läntinen Rantakatu 15) that meet the
  `region` thresholds of `publish/pages.json` (facade coverage, no draft ground, validator green) and connect to
  the centre tile; excluded siblings stay visual-only scenery and the release notes name each exclusion's reason
  (`scripts/dev/pages_region.py`, `publish-pages --plan`).

### D5. Collision vs visual layer strategy (the core decision)
The two layers are **separate artifacts** built from the same ground truth. They are never derived from each other at runtime.

| Element | Collision layer | Visual layer |
|---|---|---|
| Ground | CDT (Triangle `pc`) from the lidar ground (class 2 + near-ground class 12), adaptive base points, **double breaklines** along 62520 curbs snapped to the lidar edge. Curb step = lidar height difference from **slope-fitted profiles on both sides** [V] | The same CDT with UVs, plus a procedural curb mesh (bevel, granite material) |
| Curbs | Vertical face from the breaklines. Position from vectors (0.15 m), height from lidar. The 0.17 m transition is a result, not an input [V\*] | Modelled curb stones |
| Stairs | **Single inclined plane** over the stair footprint (character-controller friendly), plus side walls | Procedural treads: riser count = round(rise / 0.16), from porras/66618 lines + lidar top/bottom [V for inputs; U for the generator] |
| Ramps | Kept by the CDT | Same |
| Retaining walls (66634) | Vertical quads to the lower DTM + 0.3 m box | Textured wall |
| Fences | Thin boxes, 1.2 m default, height checked against class-1 points | Mesh from library |
| Buildings | Closed, simplified LoD2 shell (≤ 2k tris per building), convex-decomposed roofs not needed | LoD2(.2) shell + procedural facade modules + trim sheets |
| Trees and poles | Capsules / cylinders | Instanced meshes |
| Quay edges | 62520 lines with a step > 1 m are reclassified as quays and given a wall + railing collider | Quay stone |

Rules:
- The visual layer **may** contain geometry with no collision (e.g. curb bevels), but **never** collision with no visual.
- Splats, the Google 3D Tiles mesh and AI textures are **visual only**, never collision.

### D6. Street View usage
- **Offline only.** Nothing from Google/Apple is fetched by the game at runtime, and raw panos are not shipped.
- **Uses:**
  1. Facade labelling (Codex, multimodal) into a strict-schema `spec.json` (`--output-schema`) [V].
  2. Reference crops for library texture generation.
  3. **Per-facade reference crops for Codex texturing (Phase 1, user decision).** For every street-facing facade (from the footprint + LoD2 shell), pick the 2–4 newest panos (Google SV, Apple Look Around) with the best angle/distance, render a perspective crop aimed at the facade centre, and mask occluders roughly with lidar/Google depth. Codex "improve" gets these crops + the spec and returns a cleaned, rectified facade texture that keeps the real bay rhythm, colours and ornaments [V for the improve step]. Pose error (~1–5°) only shifts the crop, which Codex tolerates; it is **not** projection baking, so it does not need the Phase 2 BA.
  4. Reference photos for the visual QA loop (D8).
  5. Later: facade projection baking and curb/furniture segmentation (Phase 2).
- **Gotchas:**
  - Depth must be `np.fliplr`-ed [V\*].
  - Pick the newest historical layer per spot [V\*].
  - Camera height is calibrated per capture campaign (2026: 2.31 m; 2022/24: 2.86 m) [V].
- **Pose:** the skyline-vs-DSM refinement **does not generalise** (3 of 3 extra panos failed or were poor), so it is **[R]**. Phase 2 plans feature-based registration/BA (COLMAP/hloc against lidar-rendered views). Until then, texture projection is not used for geometry-critical work.
- **Rejected:** Google depth as geometry (planar proxy); KartaView (almost no coverage); training splats from 10 m-spaced panos.

### D7. Textures
- A material library (~150–300 entries) + 20–40 facade trim-sheet families + per-building tint/wear.
- Unique textures only for hero buildings.
- Codex `image_gen` runs on the **Mac** (the remote CLI 0.144.4 is too old [V]), `--sandbox read-only`, collected by thread_id.
- A mandatory local make-tileable pass and low-frequency flatten (Codex tiles are not trustworthy [V]).
- DeepBump for normals [V], ktx2-encoder/toktx for KTX2 [V].
- Codex quota cost per image is **unknown** (the 0 % reading was weakened [R]), so run a 30–50 call pilot before scaling.
- **Street-facing facades are photo-referenced** (D6 use 3): every visible facade starts from its own Street View / Look Around crops, so the building looks like the real one. Courtyard facades and roofs use library/trim sheets and ortho; they need not be accurate.

### D8. Visual QA loop: render vs reference photo, judged by Codex GPT-6-Astra (user decision)
- **Reference set:** posed photos of the AOI: Street View/Look Around perspective crops (pose from metadata, height per campaign), turku.360tour.fi 3DVista panoramas, Google Maps photos where obtainable (see `research/reference-photos.md`). Each entry: image, camera pose (E, N, H, yaw, pitch, FOV), source, date, pose confidence.
- **Render:** a Playwright script opens the game with `?cam=<pose>&fov=..&time=<sun matching the photo>` and saves a screenshot of the same view (both backends).
- **Judge:** `codex exec -m gpt-6-astra --sandbox read-only --output-schema qa.schema.json` with both images (+ a depth/ID render so Astra can name objects). Output: list of issues `{what, where (image bbox), category: geometry|texture|material|lighting|missing_object|scale|vegetation|other, severity 1–5, fix_hint}` + an overall similarity score 0–10.
- **Back-mapping:** issue bboxes → object-ID render → building/tile/material IDs, so each issue becomes a ticket in `qa/issues.jsonl` with a pointer to the asset to fix.
- **Use:** (1) fix-list generation after each texture/geometry pass; (2) regression: the same view set is re-run per commit and the score must not drop; (3) acceptance gate for Phase 1–3 tiles.
- Not a pixel metric: the goal is "recognisably the same place", in line with the not-1:1 principle. Same quota guard as D7.

---

## 2. End-to-end pipeline

```
 RAW (local ~/turku3d/data/raw)            PROCESS                                          SHIP (static)            ENGINE (Chrome)
 ───────────────────────────────            ───────                                          ─────────────            ───────────────
 LAZ 500 m tiles ──[L]fetch_laz──► raw/laz ─[L]ground: cls2+cls12 merge, denoise ─┐
 WFS GML (kanta, GIS, bldg) ─[L]fetch_wfs─► raw/wfs ─[L]gml2geojson (local coords)─┤
 WMS ortho 2022/2025/drone ─[L]fetch_wms─► raw/ortho (GeoTIFF/JPEG 5 cm)           │
 OSM Geofabrik pbf ─[L]osmium clip────────► raw/osm                                 │
                                                                                    ▼
                         [L] breaklines: 62520 curbs (snap to lidar edge, step from slope-fit profiles,
                             reject >1 m = quay), 66634 walls, 62540 ramps, pools
                         [L] CDT (Triangle 'pc') per 250 m tile + 50 m margin → ground.ply (+ material ID per tri
                             from Katualueet / ortho classifier)
                         [L] stairs/walls/fences generator → procedural visual meshes + simple colliders
                         [L] buildings: city LoD2 (CityGML→mesh, earcut/cjio) + roofer LoD2.2 → per-building
                             pick by robust lidar residual → shell + collision shell
                         [L] trees: CHM peaks + GIS:Puut → instances.json ; furniture: Varusteet/OSM → instances
                                                                                    │
 Street View / Look Around ─[L]fetch_panos─► raw/panos ─┐                           │
 ortho + pano crops ────────────────────────────────────┼─[M] Codex label → spec.json (strict schema)
                                                        ├─[M] Codex generate → library candidates
                                                        └─[M] tileable+flatten, DeepBump, QA → library/*.ktx2
                                                                                    │
                         [M or L] Blender headless (bpy): facade modules on bay grid, UVs into trim sheets,
                             ground UVs into ortho / material splat                 │
                                                                                    ▼
                         [L] package: visual GLB → gltf-transform ktx2 (FIRST) → meshopt (no simplify/join)
                                      collision → .col (meshopt) ; tileset.json + manifest.json (hashes)
                         [L] validate (Node + Rapier WASM): scripted walkers + cars per collision tile:
                             no holes, curb steps vs lidar, building penetration test → report.json
                                                                                    │
                         [L] serve local sandbox dist/            ─────────► dist/tiles/... ──HTTP──► 3DTilesRenderer (main)
                                                                                 (COOP/COEP,              + .col → physics worker
                                                                                  brotli, CORS)            (Rapier) via transferables
                         [M] Playwright headless Chrome (real Metal GPU): visual + perf + physics regression
```

`[L]` = local Mac native pipeline: bulk download, lidar, CDT, roofer, packaging, Node validation.
History: Phase 0 used Linux osgallery-dev; Wave 0 retires that execution path.
`[M]` = Mac M1 Max: Codex CLI 0.160, Blender GUI/MPS work, mono depth and segmentation (MPS), Chrome/Playwright tests, game dev server.
GPU-heavy Phase 2 work (splats, large-scale segmentation) needs the M1 MPS or a rented CUDA GPU.

Pipeline principles:
- Each step is a pure function of (inputs, params, code version) and writes into a content-addressed cache keyed by `sha256(inputs + params + step version)`.
- Each step is resumable per tile.
- `make tile T=...` rebuilds one tile end to end.

Gates (Jonni, 2026-10-27: iteration speed beats coverage; he plays the game and reports what to fix): the fast gate
(`scripts/dev/gate --fast`, run by every worker and by `finish`) is lint and format, type checks, the policy stage
and only those unit-test stages whose full run stays under 20 s, so it finishes in under 30 s at host load 100 and
never starts a browser. Stages over that budget carry `tier = "deep"` in `scripts/dev/gate.toml`; the integrator runs
the deep tier (`gate --deep --all`, cached per stage input hash) after each code batch is published, and
`make gate-deep` runs it by hand. Deep and Chrome results are advisory. New tests are small unit tests of pure
functions; browser specs only when a brief asks for them; time budgets only in by-hand perf targets.

Landing and integration: each agent lands its own green slice with `codex-task finish` (rebase, fast gate,
fast-forward; conflicts are the agent's to resolve) and every landing sends a `landed` message to the other
running agents, who rebase at their next natural point; unlanded branches are never shared, so an interface
change lands first as its own compatible slice. The landing starts the integrator (nothing polls). **Live and
Pages publish what localhost serves** (user decision 2026-10-27 22:55): a code batch never rebuilds tiles. The
code lane classifies a batch by its paths (`scripts/dev/landing_class.py`): `inert` (docs, prose) re-points `live`
to the new commit with the published content; any other batch builds only main's game bundle (about 1–2 min) and
publishes it with the served tile set (the tile store entry for main's tile key, else the live release's tiles;
`scripts/dev/served.py`) to `live` and GitHub Pages. Only a game-build failure bisects and reverts the culprit
landing. The data lane owns every change of the data root (facade approvals, the playable tile list, layers): it
rebuilds the affected tiles with the tile code that built the live tiles, so a pipeline change in main never forces
a rebuild of tiles whose outputs exist (main's tile code is the fallback when the old code cannot build the new
data), and runs tile-validate (format and seams block), the car report, the facade ground audit and the @streaming
specs when the tile list changed; it ships the live game bundle. The release record names each tile's builder
(`tiles_from`, `tile_commits`) and the tiles built with older tile code than the release commit's (`stale_by_code`);
`make tiles-refresh-stale` rebuilds those with main's code when the host is idle (the sentinel's heartbeat, load
< 40). Advisory findings (car report, facade ground audit, tile-validate's walk checks, the facade baseline,
Chrome, the deep gate tier the code lane runs after publishing) are recorded in the ledger row and the release, shown on the localhost badge, listed in the Pages release
notes and sent to the batch's authors as an inbox warning when new; they never block, bisect or revert. Landings
join a running code batch only while that is free (before its first stage that is not a cache hit, at most 3
heads); publication is serialised: a code release is recomposed on tiles the data lane published meanwhile, and a
data release ships the newer game of a code release published meanwhile on the same tiles. A full Chrome suite runs on live
every 2 h with landings, as information. The integrator's Chrome stage
gets 4 workers below load 60 (else 2), and agents' Chrome suites queue behind it. Details and ledger fields:
`scripts/dev/README.md`, "Landing", "Landing and integration" and "Host slots".

---

## 3. Repo layout

```
/Users/wnt/Documents/game-test/          (git repo, Phase 0)
├── docs/
│   ├── ARCHITECTURE.md                  this file
│   └── research/                        topic reports (existing)
├── pipeline/                            Python 3.12 package `turku3d` (runs on [L], mostly also on [M])
│   ├── pyproject.toml                   laspy[lazrs], pdal, numpy, scipy, shapely, triangle, open3d, pyproj, lxml
│   ├── turku3d/
│   │   ├── config.py                    origin, CRS, tile grid, paths (single source of truth)
│   │   ├── tiles.py                     tile IDs ↔ bbox ↔ LAZ tiles
│   │   ├── tile_layer.py                per-tile producer runs: tile inputs, keys, ownership (pipeline/README.md)
│   │   ├── fetch/                       laz.py, wfs.py (GML), wms.py, osm.py, panos.py
│   │   ├── ground/                      merge_ground.py, breaklines.py, curb_profile.py, cdt.py, materials.py
│   │   ├── structures/                  stairs.py, walls.py, fences.py, quays.py
│   │   ├── buildings/                   citygml.py, roofer.py, select.py, shells.py
│   │   ├── vegetation/                  trees.py (CHM + register)
│   │   ├── col/                         .col v1.0 writer/reader, meshopt codec, materials table, fixtures
│   │   ├── layers.py                    shared collision/visual layer interchange
│   │   ├── package/                     assemble.py, visual.mjs, gltf.py, col_format.py, manifest.py, seams.py
│   │   └── qa/                          curb_check.py, coverage.py, reports
│   ├── blender/                         bpy scripts: facades.py, uv_ground.py, preview_render.py
│   └── tests/                           pytest on fixture patch (100 m around the office)
├── textures/                            [M] Codex + local material tooling
│   ├── prompts/  schemas/  style/STYLE.md (style bible)
│   ├── turkutex/ (codex runner + collector, streetview refs, post-processing)
│   └── library/manifest.jsonl          accepted assets, prompts, thread_ids, hashes (images themselves in data/)
├── game/                                Vite 8 + three/webgpu + Rapier (TypeScript)
│   ├── src/
│   │   ├── core/ (origin.ts, loop.ts, input.ts)
│   │   ├── world/ (streaming.ts, tiles.ts, materials.ts)
│   │   ├── physics/ (worker.ts, world.ts, colTile.ts, triLocator.ts, curbStrike.ts)
│   │   ├── shared/col/ (colFormat.ts, rapierBuild.ts, materials.ts: shared with tools/validator)
│   │   ├── actors/ (player.ts, car.ts, npc.ts)
│   │   └── render/ (pipeline.ts, atmosphere.ts, post.ts)
│   ├── public/basis/                    transcoder
│   └── tests/ (node physics sims, playwright e2e)
├── tools/
│   ├── reconstruction/  research/       existing research scripts
│   ├── validator/                       Node Rapier tile validator (walkers; cars TBD), reads .col via game/src/shared/col
│   └── dev/ (serve-dist.mjs with COOP/COEP + brotli, environment helpers, localhost-game: main hot on :5171)
├── data-manifests/                      small, committed: what bulk data exists and where
│   ├── laz-tiles.csv                    copied from leikkipuistodata (read-only source)
│   ├── sources.yaml                     endpoints, layers, years, licences notes
│   ├── areas/office-block.yaml          Phase 1 AOI (bbox, tiles)
│   └── checksums/                       sha256 lists of raw downloads
├── qa/                                  visual QA loop (D8): views registry, render driver, judge, reports
├── mk/                                  per-area Make fragments (included by the root Makefile)
├── Makefile                             make help | tile T=... | <area>-test ...
└── .gitignore                           data/, dist/, node_modules/, *.laz
```

**Bulk data lives locally** in `~/turku3d/data/`. Raw inputs are shared and immutable to workers;
outputs use `$TURKU3D_WORK/{build,cache,dist,reports}`. Bulk data is never committed.
```
$TURKU3D_DATA/raw/{laz,wfs,ortho,osm}/           immutable downloads (+ checksums)
$TURKU3D_WORK/cache/streetview/                   sandbox Street View downloads
$TURKU3D_WORK/cache/<step>/<hash>/                 intermediate products
$TURKU3D_WORK/dist/tiles/{visual,collision}/...    shippable output + tileset.json + manifest.json
~/turku3d/envs/                                     conda/venv envs (pipeline, recon)
~/turku3d/research/                                 research scratch (existing)
```
- The old Mac mirror is consolidated into `~/turku3d/data/`; builds need no sync step.
- Game previews serve `$TURKU3D_WORK/build/game` with COOP/COEP headers [V].
- `~/leikkipuistodata` is never written to.

---

## 4. Phased plan

### Phase 0: Tooling (≈ 1 week)
1. `git init` the repo, add the layout above, `.gitignore`, Makefile skeleton, `config.py` with origin O and the tile grid. Commit `laz-tiles.csv` as a manifest.
2. Local Mac: `~/turku3d/envs/pipeline` (conda-forge: pdal 2.10, python-triangle, open3d, laspy[lazrs], shapely, pyproj). Node 24, gltf-transform 4.5.1, KTX-Software 4.4.2, gltfpack 1.3, roofer v1.0.0 binary.
3. Mac: the native pipeline env, Blender 5.2 headless, Codex CLI 0.160, Playwright + Chrome channel.
4. Fetchers with caching and checksums: LAZ (`curl -L`, resumable), WFS (paged GML → GeoParquet/GeoJSON in local coords), WMS (tiled GeoTIFF at native resolution), Geofabrik Finland pbf + osmium clip.
5. `.col` collision format (spec + Python writer + TS reader + round-trip test).
6. Node Rapier validator skeleton (from `tools/engine-probe` and the reconstruction verify scripts) that consumes `.col` tiles.
7. Game skeleton: `game/` (copy the engine-probe bootstrap) with the WebGPU/WebGL2 switch, physics worker, 3DTilesRenderer loading a dummy tileset, a debug HUD (backend, frame ms, resident tris/MB).
8. `tools/dev/serve-dist.mjs` (COOP/COEP, brotli, range requests).

**Exit:** `make tile T=<office tile>` runs the whole chain on stub data, and Chrome shows the stub tile with a walking capsule.

**Historical Phase 0 status (before the local Wave 0 migration, 2026-10-25): exit criterion met.** `make tile T=t0_0_0` (75 s) runs: remote fetch (all cached) → `turku3d.package.stub_tile` (maanpinta key points → 1.25 m heightfield → 16 meshopt `.col` tiles with Katualueet materials + one ortho-draped ground GLB) → `tileset.json` + `manifest.json` (hash-checked) → `sync.sh` to `~/turku3d/data/dist` (sha256-verified) → validator (format, holes, belowGround PASS; buildings/curbs/stairs skipped: the stub has none) → game build + Playwright in Chrome (WebGPU and WebGL2): tile renders, the animated character walks 8 m on the stub collision, QA render contract incl. ID pass. How to run everything: [STATUS.md](STATUS.md).

| Step | Status | Notes |
|---|---|---|
| 1. repo, layout, Makefile, config.py, laz-tiles.csv | done | `mk/*.mk` per area; first commit "Phase 0 tooling skeleton" |
| 2. native Mac environment (PDAL, GDAL, Triangle, laspy, shapely, pyproj; gltf-transform 4.5.1, gltfpack 1.3, roofer 1.0.0) | done | Includes KTX-Software / toktx 4.4.2; `make pipeline-bootstrap pipeline-versions` |
| 3. Mac env: pipeline venv, Blender, Codex CLI 0.160, Playwright + Chrome | done | Blender headless not exercised by any Phase 0 step yet |
| 4. fetchers (LAZ, WFS paged → GeoParquet, WMS tiled GeoTIFF, OSM clip) with checksums | done | office-block fetched; drone layer has no coverage in the AOI; Street View panos fetched by textures/ and qa/ on the Mac, `fetch/panos.py` not written |
| 5. `.col` format: spec + Python writer + TS reader + round trip | done | Python `pipeline/turku3d/col/`, TS `game/src/shared/col/` (used by game and validator); 7-case bit-exact round trip |
| 6. Node Rapier validator | done (walkers) | holes, buildings, curbs, stairs, belowGround; no car checks and no seam-identity check yet |
| 7. game skeleton (WebGPU/WebGL2, physics worker, 3DTilesRenderer, HUD, character, car) | done | KCC switched to the validator's single-move scheme (25 cm curb climbs); QA render contract of qa/README §2 implemented (vertical fov, roll, pose echo, `__qaProject`, ID pass) |
| 8. `serve-dist.mjs` (COOP/COEP, brotli, ranges) | done | Local sandbox output; no sync step |
| exit: `make tile` on stub data, Chrome shows the tile with a walking character | done | `make tile-qa` also renders the 3 QA registry views against the game (no judge) |
| Codex texture pilot / QA judge self-test | done (pilot) | 2 facades textured; judge planted-difference test 9/9; quota cost per call still unresolved (integer % counter) |

### Phase 1: One office block, walkable + drivable (≈ 4–6 weeks)
AOI: one 250 m visual tile centred on the office block at Läntinen Rantakatu 15 (E 23459668, N 6704254), i.e. roughly E 23459543–23459793, N 6704129–6704379 (16 collision tiles) + a 50 m margin. LAZ tiles: `23459500_6704500` (+ neighbours if the margin crosses).

| # | Task | Machine | Acceptance criteria |
|---|---|---|---|
| 1.1 | Fetch the AOI: 1–4 LAZ tiles, all listed WFS layers, 2022 true ortho + 2025 ortho + drone at native resolution, **OSM clip (mandatory) conflated to city vectors by ID/overlap**, ~40 Street View panos (newest layer) | L | Checksums recorded; WFS layer counts match a fresh probe; ortho resolution measured at ≤ 5.5 cm |
| 1.2 | Ground points: class 2 + near-ground class 12 (within 0.15 m of a robust local surface), denoise | L | ≥ 95 % of 1 m cells in open ground have ≥ 1 point (class 2 alone: 54 %); Ground noise MAD ≤ 1.5 cm on flat pavement |
| 1.3 | Curb breaklines: 62520 lines → offset double breaklines (inner/outer), snapped to the lidar edge; step from slope-fitted profiles every 1 m; steps > 1 m reclassified as quay; steps < 3 cm marked flush (dropped kerb / crossing) | L | Deferred from the current block iteration by user decision (2026-10-25). Preserve measured breakline input for the next iteration; current ground acceptance covers coverage, materials, seams, budgets and no holes, including Rapier below-ground checks. |
| 1.4 | CDT ground mesh per collision tile (Triangle `pc`, adaptive base points at 2 cm tolerance), per-triangle material ID from Katualueet (+ ortho fallback) | L | Hold-out lidar RMSE ≤ 3 cm on open ground; ≤ 150k tris per 62.5 m tile; watertight across tile seams (0 open edges except the AOI border); every triangle has a material ID |
| 1.5 | Stairs: group porras 66626 + 66618 lines into flights; rise from lidar top/bottom; riser count = round(rise / 0.16); visual treads + inclined-plane collider + side walls | L | Each flight's top and bottom height within 5 cm of the lidar; collider slope ≤ 40° (otherwise flag); visual and collider top/bottom edges coincide within 2 cm; character climbs every flight in the validator |
| 1.6 | Retaining walls (66634), fences, ramps (62540), quays: colliders + visual meshes | L | Wall height within 10 cm of the lidar; the character cannot step over any wall > 0.3 m or fence; ramps climbable |
| 1.7 | Buildings: city LoD2 → triangulated shells (earcut/cjio, not fan); roofer LoD2.2; select per building by median abs residual vs class-6 points; collision shell ≤ 2k tris; footprints snapped to kantakartta | L | 100 % of buildings in the AOI have a shell; selected model median residual ≤ 10 cm and p75 ≤ 25 cm; shells closed (manifold); footprint vs kantakartta Hausdorff ≤ 0.3 m |
| 1.8 | Textured buildings v1: roofs from the 2022 true ortho; **street facades from Street View/Look Around reference crops → Codex improve** (D6 use 3); courtyard facades from library + trim sheets (Codex label → spec.json with strict schema; ~30 materials + 3 trim sheets; tint/wear) | M | Every facade has a non-placeholder material; each street facade has ≥ 1 reference crop logged; bay count matches lidar/footprint; no visible tiling seams in a 2×2 test (seam ratio < 1.5); quota per call logged |
| 1.9 | Ground textures: ortho-draped ground (5 cm) blended with library materials by material ID; curb stone material | M/L | Ground texture ≤ 4 cm/px at street level after KTX2; no colour seams at tile edges |
| 1.10 | Package: visual GLB (KTX2 → meshopt, no simplify/join), `.col`, `tileset.json`, `manifest.json` | L | L0 visual tile ≤ 8 MB brotli; `.col` ≤ 1 MB per collision tile; decoded geometry identical to source within 2 mm |
| 1.11 | Node validator over every collision tile | L | (a) 0 holes: 1 ray per 0.5 m² over the AOI hits the ground; (b) **the character cannot walk through any building**: 2,000 random walkers aimed at building shells for 10 s each, 0 end positions inside any building footprint; (c) the character climbs every step ≤ 35 cm and every stair flight; (d) walkers never fall below the ground |
| 1.12 | Physics worker in the game: stream `.col` tiles within 200 m, build colliders in the worker (FIX_INTERNAL_EDGES on curb tiles), floating-free local coords | M | No main-thread stall > 8 ms on tile load; resident collision ≤ 150 MB; walking across all 16 collision-tile seams without snagging or falling |
| 1.13 | Player character (GTA V-style stairs: sloped collider + stair animation + foot IK on treads): Quaternius CC0 rig with UAL animations, Rapier KCC with a height-limited step clearance move, snap 0.3 m and maxClimb 45° | M | Steps up any ledge through 0.35 m at walking speed; walks up and down every stair flight with foot IK or stair animation; blocked by visible walls taller than 0.35 m |
| 1.14 | Car: Rapier raycast vehicle + **curb-strike module**: wheel shape casts against faces with material = curb; impulse ∝ speed × step height; a speed-dependent block above ~25 cm | M | **Car bumps over curbs**: at 3–10 m/s over a 7–15 cm curb, chassis pitch spike ≥ 2° and speed loss 5–20 %; > 25 cm at < 3 m/s blocks; never tunnels through a building at 30 m/s |
| 1.15 | Rendering v1: CSM sun, sky (takram atmosphere, smoke test under WebGPU first), GTAO (light, since albedo has baked AO), TRAA, bloom | M | 60 fps at 1440p on the M1 Max in Chrome (WebGPU) for the AOI; WebGL2 fallback ≥ 45 fps |
| 1.17 | Visual QA loop (D8): ≥ 20 posed reference views around the block (SV, Look Around, 360tour), render-matching script, Astra judge with strict schema, issue back-mapping | M | Every view renders at the same pose; issues map to asset IDs; at least one fix-and-rerun cycle shows a higher score; scores stored per commit |
| 1.16 | Playwright e2e on the Mac (real Metal GPU): scripted walk + drive route, screenshots, frame stats, physics assertions; both backends | M | Green in CI-like `npm run test:e2e`; screenshots archived per commit |

**Phase 1 done when:** the user can open `http://localhost:${TURKU3D_PORT_BASE:-5170}` in Chrome, walk around the office block on foot (curbs, stairs, ramps felt), drive a car around it (curbs felt), cannot enter any building, and all the automated criteria above pass.

### Phase 2: Photoreal pass (≈ 6–10 weeks)
- Street View pose registration (COLMAP/hloc SuperPoint+LightGlue against lidar-rendered views). The skyline method is **[R]** and is not used. Target: reprojection error < 2 px on 20+ panos.
- Facade projection baking: best view by angle, distance and occlusion, median across time layers, de-lighting, KTX2. Hero buildings get Codex-cleaned unique facades.
- Procedural facade geometry: window recesses, sills, balconies from spec.json; bay counts from footprint/lidar (the Codex value is a prior only [V]).
- Street furniture and props: Varusteet/OSM instances with a CC0 library; trees from the CHM + register species.
- Segmentation (Mapillary Vistas/OneFormer on MPS) back-projected with **lidar-rescaled** mono depth (DAv2: raw scale ~3.4× off [V]) to add missed curbs and furniture.
- Render: atmosphere, weather, night lights, reflections. Optional: splats for hero spots **only if** a WebGL-path or custom TSL splat pass is accepted (Spark fails under WebGPU [V]), with own 360 video/drone capture as input.
- Optional: Google Photorealistic 3D Tiles as a far backdrop (key needed) [U].

### Phase 3: Scale-out automation (ongoing)
- A district-level local job runner: tile queue, per-step cache, resumable, a nightly validator report, and a coverage map (HTML).
- Order: the office district (2×2 km) → city centre (≈ 5×5 km) → whole city (1,282 LAZ tiles ≈ 170 GB [V\*]; process and discard the raw LAZ, keep only products).
- Coarse LODs: merged roofs + ortho-draped DTM for 500 m–2 km parents. Floating origin beyond ~8 km.
- Throughput target [U]: 1 km² ≈ 16 visual tiles in ≤ 2 h on the 10-core server (excluding Codex), plus ~1.5–2 h with 5 Codex runners per km² for textures.
- Budget guards: ≤ 5 GB per research topic; a disk monitor for `~/turku3d/data`.

### Phase 4: Gameplay (GTA-like)
- Characters: player + NPC pedestrians (Quaternius/Mixamo-compatible animation, retargeting), recast navmesh per tile built server-side, yuka steering, crowd LOD.
- Vehicles: several car classes, damage, enter/exit, a camera system. Evaluate Jolt WheeledVehicleController (custom build) if the Rapier feel is not enough.
- Traffic: a lane graph from Keskilinjat + osm2streets (built from the repo; npm is stale [V]); signals from Liikennemerkit; intersection logic; parking.
- Interaction: doors, simple missions, minimap from the ortho, save state.
- Audio: footstep and tyre sounds by material ID; ambient city sounds.

---

## 5. Risks and open questions

### Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Curb and stair extraction from airborne lidar is novel (no open tool does it) | Phase 1 core | Vectors for position, lidar for height only; hold-out QA; procedural stairs |
| Absolute vertical accuracy unknown (validation was circular [R]) | Mismatch with other datasets | Rely on relative accuracy; spot-check against the user's own measurement of 2–3 curbs/steps |
| Data epochs differ (lidar 2021, vectors 2022–24, imagery 2026) | Wrong geometry where the city changed (e.g. Kauppatori) | Vectors win; flag tiles where lidar and vectors disagree by > 20 cm |
| Rapier vehicles ignore curb faces [V] | No "bump" feel | Custom curb-strike module (Task 1.14); Jolt plan B |
| Collision memory and build cost (135 B/tri, 0.4 µs/tri) | Stalls, OOM | 62.5 m tiles, worker builds, ≤ 1M resident tris |
| `gltf-transform optimize` silently decimates; KTX2/meshopt order; quantised positions | Lost curbs | Explicit steps only; `.col` is not glTF; geometry round-trip test |
| Codex quota cost unknown; style drift; non-tileable output | Texture throughput | Pilot first; style bible; mandatory local tiling/QA |
| Street View endpoints undocumented; ToS | Facade source breaks | Cache everything once; Mapillary/Look Around/own photos as alternatives |
| Pose registration unproven (skyline [R]) | Facade projection blur | Phase 2 BA work; Phase 1 does not depend on it |
| Splats incompatible with WebGPURenderer [V] | Renderer lock-in if splats wanted | Keep splats optional; decide in Phase 2 |
| Render budget measured only on a synthetic scene [UNCERTAIN] | Perf | Measure on the real AOI in Task 1.15 |
| No GPU on the server | Slow ML | M1 MPS; rent a CUDA GPU for Phase 2 if needed |
| Kantakartta 62520 includes quay edges; codes 66620/66622/66624/62534 unlabelled | Wrong breaklines | Step-height filter; get the Turku code list |

### User decisions (2026-10-25)
- Performance target: **1440p / 60 fps** in desktop Chrome on an M1 Max class machine.
- Fidelity bar: **GTA V**. Stairs use the GTA V approach: a smooth sloped collider plus a stair locomotion animation and **foot IK** placing feet on the visual treads (raycasts against a per-stair "tread" helper). Curbs remain real steps in collision.
- Codex quota: may be used **up to 100 %** of the weekly window (the user resets it). Schedulers still stop at the limit and resume.
- **No paid services** beyond the existing Codex and Claude subscriptions: no cloud GPU (Phase 2 ML runs on the M1 Max via MPS or on the CPU server), and no Google Maps Platform key, so the Photorealistic 3D Tiles backdrop is dropped.
- Mapillary: the access token (`MLY|…`, used as `Authorization: OAuth <token>` against graph.mapillary.com, verified HTTP 200) is in `~/.config/turku3d/mapillary.token`; the client secret, not needed for reads, is kept separately in `~/.config/turku3d/mapillary.client_secret`. Both are mode 600 and never committed or copied to the server unless needed.

### Remaining open questions
1. Do you want Gaussian splats at all? (No cloud GPU, so training happens on the M1 Max only.)
2. Time period of the city: "today" (2026 imagery, current vectors) even where the 2021 lidar is outdated?
3. Is a fixed spawn at the office acceptable for Phase 1, and is a stylised fallback acceptable for buildings whose facades are not yet textured?

## Multiplayer server

The [Node 24 multiplayer server](../server/README.md) targets deployment on osgallery-dev
(the server-specific exception to the Mac pipeline policy), behind the outbound
forwarder at `wss://turku.lab.madekivi.fi`. It owns presence, session nicknames and
delivery score validation for one room of up to 32 connections, with archived
Europe/Helsinki clock-hour leaderboards. It relays client poses at 20 Hz and runs
no physics. The game client (`game/src/net/`) owns reconnects and session tokens,
interpolates remote characters and vehicles, places kinematic Rapier proxies in
loaded collision tiles, and shows remote positions on the minimap. Local collision
impulses feed the impact sound and crash flash. Paid mission drop-offs send score
claims; the live HUD and start/pause menus show the current leaderboard and archived hours.
The [versioned JSON protocol](../server/PROTOCOL.md) defines local E/N coordinates,
reconnects, rates and economy bounds. Only completed hours persist across restarts.
