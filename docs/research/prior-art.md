# Prior art: open-source systems for drivable/walkable worlds built from real-world data

Topic: `prior-art` · Date: 2026-10-25 · Target: **a GTA-style Turku game running in desktop Chrome (WebGPU/WebGL2 + WASM)**

Labels used below:
- **VERIFIED**: I ran it, or fetched it from the GitHub API, in this session.
- **UNVERIFIED**: taken from docs, papers or memory.

Every repo's stars, last push and license come from the `gh api repos/<name>` metadata pulled on 2026-10-25, so those are VERIFIED. Statements about features are UNVERIFIED unless the hands-on section below covers them.

Remote workspace: `osgallery-dev:~/turku3d/research/prior-art/` (about 720 MB). Probe scripts were copied locally to `docs/research/prior-art-assets/scripts/`. Screenshots are in `docs/research/prior-art-assets/`.

---

## 1. TL;DR

No open-source project does what we want end to end: a photoreal, lidar-accurate, curb-level, drivable city that runs in the browser. There are good, maintained parts for each layer, though, and three of them ran on real Turku data in this session:

| Layer | Best reuse candidate | Status on Turku data |
|---|---|---|
| Buildings (LoD2.2 roofs from lidar + footprints) | **3DBAG/roofer** (CLI binary) | **VERIFIED.** 160 buildings in 92 s on the 500 m tile, then three.js in Chrome |
| Generic OSM → 3D (streets, furniture, trees, windows, 3D Tiles) | **OSM2World** (Java CLI) | **VERIFIED.** glb at LOD4, 224 MB, reduced to 56.6 MB with meshopt; loads in Chrome in about 0.3 s |
| Lane-level street geometry (driving lanes, sidewalks, footways, bike lanes) | **osm2streets** (Rust, npm WASM package) | **VERIFIED.** 748 lane polygons in 0.9 s in Node |
| Browser runtime | three.js (+ WebGPURenderer) **or** Babylon.js / PlayCanvas | three.js loader + meshopt decoder VERIFIED in Chrome |
| Streaming | 3D Tiles (3DTilesRendererJS) or a custom grid of glb tiles | UNVERIFIED (OSM2World has a `tileset` command, VERIFIED to exist) |
| Physics / vehicles / character | **Rapier** (WASM: raycast vehicle + kinematic character controller) or **Jolt Physics.js** | UNVERIFIED (well known) |
| Game-feel reference (GTA-like character ↔ car in three.js) | **swift502/Sketchbook** (archived, MIT) | UNVERIFIED |
| Mesh/texture packaging | glTF-Transform + meshoptimizer + KTX-Software (Basis/KTX2) | glTF-Transform `optimize` VERIFIED |
| Photoreal "look" layer (optional) | 3D Gaussian splats via **Spark** / PlayCanvas, trained with gsplat (needs GPU) | UNVERIFIED |
| Street furniture assets | TRELLIS(.2) / Hunyuan3D-2.1 / TripoSR (image → glb) | UNVERIFIED (needs a GPU, not available on the remote) |

**Recommended architecture, based on what worked:** build an offline Python/Node pipeline on the Linux box:
1. lidar → DTM plus curb, stair and ramp breaklines (our own code; see section 5).
2. roofer → building shells.
3. osm2streets → lane and sidewalk polygons, extruded against the lidar DTM so kerbs are real 10–15 cm steps.
4. OSM2World, or our own generator, for furniture and vegetation placement.
5. Textures from the orthophotos.
6. glTF-Transform for meshopt and KTX2.
7. Tiled glb files with a separate collision glb per tile, plus a runtime of three.js + Rapier in Chrome.

None of the existing "real city in Godot/Unity/Unreal" repos is worth forking.

---

## 2. Hands-on results (all VERIFIED)

Test area: the LAZ tile `23459500_6705000`, which covers EPSG:3877 x 23459500–23460000, y 6704500–6705000. That is the Turku centre, west bank of the Aura, around WGS84 60.4505–60.4551 N, 22.2641–22.2731 E.

### 2.1 Data inputs
```bash
# LAZ: the opaskartta URL now 302-redirects to turku.asiointi.fi, so curl needs -L
curl -sSL -o 23459500_6705000.laz https://opaskartta.turku.fi/3d/pistepilvi/23459500_6705000.laz   # 96 MB
# OSM: overpass-api.de answered 406 to curl; the main OSM API worked (5.5 MB)
curl -sS -A "turku3d-research/0.1" -o block_api.osm \
  "https://api.openstreetmap.org/api/0.6/map?bbox=22.2635,60.4500,22.2738,60.4556"
```
- The LAZ file is LAS 1.2 from TerraScan with **no CRS in the header**, so tools need `--srs EPSG:3877`. It holds 15.68 M points (about 63 pts/m² over the full tile) with RGB, intensity and GPS time.
  - Class counts: 1: 163k, **2 (ground): 4.0 M**, 3/4/5 (vegetation height bands): 76k / 178k / 839k, **6 (building): 4.83 M**, 7: 10k, 8: 20k, 9 (water): 4.5k, 12 (overlap): 5.56 M.
- OSM in this bbox:
  - 252 `building` tags and 348 `building:levels`.
  - 116 kerb nodes (`kerb=lowered` 83, `flush` 23, `raised` 6, `no` 4) and 44 `sidewalk*` tags.
  - 56 `highway=steps`, 30 `step_count`, 54 `incline` and 28 `area:highway`.
  - **Conclusion:** OSM tells us *where* kerbs, steps and ramps are, and roughly what type each is. The lidar has to supply the actual heights.

### 2.2 roofer (3DBAG): LoD2.2 buildings from Turku lidar
```bash
curl -sSL -o roofer.tgz https://github.com/3DBAG/roofer/releases/download/v1.0.0/roofer-linux-x86_64-v1.0.0.tar.gz
tar xzf roofer.tgz && chmod +x bin/roofer        # static-ish bundle with its own GDAL/PROJ data
python -I scripts/osm_footprints.py data/block_api.osm data/footprints_3877.geojson 23459500 6704500 23460000 6705000   # 160 footprints
bin/roofer --srs EPSG:3877 --lod12 --lod22 -j 10 data/23459500_6705000.laz data/footprints_3877.geojson out_roofer
```
- **Runtime:** 1 min 32 s wall time, 229 s CPU, 690 MB peak RSS, CPU only (no GPU needed).
- **Output:** a CityJSONSeq file (`.city.jsonl`, 1.3 MB) with semantic surfaces (Roof, Wall, Ground) per BuildingPart.
  - 147 of the 160 buildings got geometry. Most of the misses are probably footprints with too few points, or ships moored in the Aura that OSM tags as buildings.
- **Converted with `scripts/cjseq2glb.py`:**
  - LoD2.2: 10,038 polygons and 31,670 triangles, 579 KB glb.
  - LoD1.2: 6.6k triangles, 123 KB.
- **In Chrome:** three.js `GLTFLoader` loads the LoD2.2 glb in 11 ms. See `prior-art-assets/roofer_lod22.png`: ridges, dormers, inner courtyards and the church dome are all recognisable.
- **Caveat:** my converter uses fan triangulation, which produces wrong wedges on concave faces (visible in the screenshot). Use earcut or CGAL, or `cjio export glb`, in the real pipeline.
- **Verdict:** this is the strongest reusable component. It is GPL-3.0, which is fine for private use. It builds buildings only: no facades, textures, terrain or curbs.

### 2.3 OSM2World: whole-scene OSM → glTF
```bash
curl -sSL -o osm2world.zip https://osm2world.org/download/files/latest/OSM2World-latest-bin.zip   # 473 MB, version 0.5.0-SNAPSHOT
java -Xmx8g -jar OSM2World.jar convert --config=standard.properties --lod=4 \
     -i block_api.osm --input_bbox="60.4505,22.2641 60.4551,22.2731" -o block.glb
npx gltf-transform optimize block.glb block_opt.glb --compress meshopt --texture-compress webp --simplify false
```
- **Runtime:** 167 s and 4.3 GB RSS on Java 21. The `-o block.obj` output threw an exception, but glb output worked.
- **Size:** the raw glb has 7.5 M render vertices, 3,888 meshes and 224 MB. Meshopt + WebP brought it to **56.6 MB** in 31 s, which is 2.5 M triangles in three.js.
  - The size comes mostly from long ways and relations that reach far outside the bbox (scene bbox about 2.7 km × 1.7 km), from LOD4 windows, and from trees.
- **Content:**
  - Buildings with roof shapes, levels and 3D window geometry at LOD4.
  - Roads with markings, sidewalks and crossings.
  - Trees, street lamps, fences, parked cars and bollards.
- **In Chrome:** it loads in about 0.3 s from localhost and uses 154 MB of JS heap. Screenshots: `prior-art-assets/osm2world_block.png` (aerial) and `osm2world_street.png` (street level).
  - The world is **flat**: terrain is optional through SRTM, which is far too coarse for us.
  - Facades are generic and kerbs are not visibly stepped.
- **Also VERIFIED:**
  - `OSM2World.jar tileset --bbox … --baseDir …` creates **3D Tiles** directly, with precompressed `.gz` tiles.
  - `--lod=0..4` gives levels of detail.
  - `--input_mode=OVERPASS` can fetch OSM itself.
- **Verdict:** good as a placement oracle and quick baseline (where lamps, benches, trees, crossings and parking go), and its Java code is a reference for the OSM tag semantics. It is not a final-quality mesh generator. MIT licensed and actively maintained (last push 2026-10-15).

### 2.4 osm2streets (A/B Street): lane-level street polygons, WASM
```bash
npm i osm2streets-js@0.1.4      # ships osm2streets_js_bg.wasm, which runs in Node and in the browser
node scripts/o2s.mjs data/block_api.osm out_o2s_lanes.geojson
```
- **Options object needed (required by 0.1.4):**
  ```js
  {osm2lanes:false, debug_each_step:false, dual_carriageway_experiment:false,
   sidepath_zipping_experiment:false, inferred_sidewalks:true, inferred_kerbs:true,
   override_driving_side:"Right"}
  ```
- **Runtime:** 0.9 s.
- **Output:** 748 lane polygons: Driving 242, Footway 203, Sidewalk 136, Shoulder 92, Biking 37, SharedUse 30, Bus 8. There are also 431 intersection polygons and 359 road polygons. Each lane carries its width, direction, speed limit and OSM way IDs.
- **Verdict:** this is the right base for **road, sidewalk and kerb geometry**:
  1. Take the 2D polygons.
  2. Drape the carriageway on the lidar DTM.
  3. Raise sidewalks to the lidar-measured kerb height.
  4. Emit the kerb face as a vertical strip. That strip goes into the collision mesh.
- **Caveats:** a few degenerate intersections were skipped with warnings. Last push was 2025-10, so activity is slowing. Apache-2.0.

### 2.5 Browser check
- **Probe viewer:** `prior-art-assets/scripts/glb-probe-viewer.html`, using three@0.170.0 from jsdelivr with `GLTFLoader` and `MeshoptDecoder`.
- **Result:** I opened it in Chrome through the DevTools MCP. Both glbs rendered, and the meshopt-compressed glb decoded fine. This confirms the delivery chain: glb + EXT_meshopt_compression + WebP textures, served as static files over HTTP to three.js.

---

## 3. Survey by category

Columns: **Stars / last push / license** (VERIFIED from the GitHub API) · what it gives us · maturity · whether it runs on macOS or Linux CPU (UNVERIFIED unless noted).

### 3.1 OSM / GIS → 3D world generators
| Project | URL | ★ / push / lic | What it gives us | Maturity | CPU Mac/Linux |
|---|---|---|---|---|---|
| **OSM2World** | github.com/tordanik/OSM2World | 795 / 2026-10-15 / MIT | Full OSM scene → glb/OBJ/3D Tiles, LOD0-4, street furniture, roofs, windows | Mature, active | **Yes (VERIFIED, Linux)**; Java 21 |
| **osm2streets** | github.com/a-b-street/osm2streets | 159 / 2025-10-02 / Apache-2.0 | Lane-level road and sidewalk polygons, intersections, kerb inference; npm WASM | Solid, slowing | **Yes (VERIFIED, Node WASM)** |
| A/B Street | github.com/a-b-street/abstreet | 8187 / 2025-09-10 / Apache-2.0 | Traffic sim on OSM, pedestrian/vehicle agents, lane changing (Rust). Source of NPC traffic ideas | Mature, slowing | Yes |
| Blosm (blender-osm) | github.com/vvoovv/blosm | 2143 / 2026-09-08 / none on GitHub | Blender addon: OSM buildings with facade textures, terrain, Google 3D Tiles import. **The GitHub repo now holds only a README**; the code is distributed through Gumroad (free + premium) | Mature | Yes (Blender, local). Not run: the code is not on GitHub |
| Streets GL | github.com/StrandedKitty/streets-gl | 1108 / 2025-08-21 / MIT | Very pretty WebGL2 OSM renderer: on-the-fly S3DB building geometry, roads, trees, PBR deferred shading, terrain LOD. **Best TypeScript reference for in-browser OSM mesh generation and render graph** | Early dev, slowing | Browser |
| Arnis | github.com/louis-e/arnis | 18388 / 2026-10-24 / Apache-2.0 | OSM + elevation → Minecraft (voxel). Shows popular demand, but voxels are of no use to us | Mature | Yes |
| OSMBuildings | github.com/OSMBuildings/OSMBuildings | 1010 / 2021-04-29 / BSD-ish | Web 2.5D buildings | Dormant | Browser |
| 3D-OSM-GODOT | github.com/Frataj/3D-OSM-GODOT | 39 / 2024-01-07 / MIT | Toy OSM → Godot extrusion | Toy | Yes |
| Glavin001/open-world | github.com/Glavin001/open-world | 32 / 2018 / – | "GTA clone using open data". Dead | Dead | – |
| CARLA | github.com/carla-simulator/carla | 14537 / 2026-10-25 / MIT | UE driving sim. OSM → OpenDRIVE (osm2odr) importer, vehicle physics, pedestrians. Too heavy for the browser, but its OpenDRIVE plus kerb/sidewalk mesh approach is instructive | Mature | Linux, needs a GPU |
| MetaDrive | github.com/metadriverse/metadrive | 1265 / 2026-10-16 / Apache-2.0 | Lightweight Panda3D driving sim with real-map import (Waymo/nuScenes/OSM) | Mature | Yes |
| libOpenDRIVE | github.com/pageldev/libOpenDRIVE | 509 / 2026-10-15 / Apache-2.0 | C++ OpenDRIVE → road meshes incl. lanes and sidewalks with height; has a WASM web viewer (odrviewer.io) | Mature | Yes |
| SUMO | github.com/eclipse-sumo/sumo | 4246 / 2026-10-25 / EPL-2.0 | `netconvert` OSM → road network and OpenDRIVE; traffic demand. Possible source for NPC traffic routes | Mature | Yes |

### 3.2 Lidar / CityGML / 3D city model pipelines
| Project | URL | ★ / push / lic | What it gives us | Maturity | CPU |
|---|---|---|---|---|---|
| **roofer** | github.com/3DBAG/roofer | 208 / 2026-10-23 / GPL-3.0 | LoD1.2/1.3/2.2 buildings from lidar + footprints, released binaries (linux-x86_64, macOS-arm64), Python bindings | Production (3DBAG NL) | **Yes (VERIFIED, Linux)** |
| 3dbag-pipeline | github.com/3DBAG/3dbag-pipeline | 13 / 2026-10-22 / Apache-2.0 | Dagster orchestration around roofer, tiling, CityJSON → glTF/OBJ/GPKG exports. Template for our own pipeline orchestration | Production | Linux, Docker |
| geoflow | github.com/geoflow3d/geoflow | 26 / 2025-01-10 / GPL-3.0 | Older node-graph engine behind 3DBAG v1. Superseded by roofer | Legacy | Yes |
| 3dfier | github.com/tudelft3d/3dfier | 639 / 2026-09-08 / GPL-3.0 | 2D polygons + lidar → 3D LoD1 terrain, roads, water and buildings **as one watertight surface**. Useful idea for terrain + road coupling | Mature | Yes |
| City3D | github.com/tudelft3d/City3D | 359 / 2025-09-03 / GPL-3.0 | LoD2 from airborne lidar without footprints (PolyFit-based) | Research | Yes |
| PolyFit | github.com/LiangliangNan/PolyFit | 835 / 2025-04-16 / GPL-3.0 | Polygonal surface reconstruction (CGAL + solver) | Research | Yes |
| cjio | github.com/cityjson/cjio | 151 / 2025-05-09 / MIT | CityJSON CLI: merge, subset, reproject, **export glb/OBJ** | Mature | Yes |
| citygml-tools | github.com/citygml4j/citygml-tools | 165 / 2026-05-31 / Apache-2.0 | CityGML ↔ CityJSON conversion (for any Turku/NLS CityGML) | Mature | Yes (Java) |
| 3DCityDB citydb-tool | github.com/3dcitydb/citydb-tool | 22 / 2026-10-24 / Apache-2.0 | CityGML database v5 | Mature | Yes |
| val3dity | github.com/tudelft3d/val3dity | 110 / 2026-08-26 / GPL-3.0 | Validates solids (watertight collision meshes) | Mature | Yes |
| PDAL | github.com/PDAL/PDAL | 1426 / 2026-10-20 / BSD | Lidar pipelines (filters.smrf, hag, cluster, writers.gdal). The workhorse for DTM and breaklines | Mature | Yes |
| CloudCompare | github.com/CloudCompare/CloudCompare | 4828 / 2026-10-25 / GPL | Interactive inspection, CSF ground filter, Poisson meshing | Mature | Yes |
| Potree / Entwine | github.com/potree/potree, connormanning/entwine | 5651 / 532 | Browser point cloud streaming (debug view of the raw lidar in-game) | Mature / slowing | Yes |
| CGAL | github.com/CGAL/cgal | 6088 / 2026-10-23 / GPL/LGPL | Shape detection, constrained Delaunay, polygon repair | Mature | Yes |

### 3.3 Browser runtime, streaming, compression
| Project | URL | ★ / push / lic | Role |
|---|---|---|---|
| three.js | github.com/mrdoob/three.js | 116911 / 2026-10-25 / MIT | Renderer (WebGL2 + WebGPURenderer/TSL). **VERIFIED loading our glbs** |
| Babylon.js | github.com/BabylonJS/Babylon.js | 26224 / 2026-10-23 / Apache-2.0 | Fuller "engine" (WebGPU, Havok physics plugin, 3D Tiles support). Strong alternative |
| PlayCanvas engine | github.com/playcanvas/engine | 17136 / 2026-10-23 / MIT | Engine with WebGPU and first-class Gaussian splats; editor is SaaS |
| 3DTilesRendererJS | github.com/NASA-AMMOS/3DTilesRendererJS | 2507 / 2026-10-22 / Apache-2.0 | 3D Tiles streaming for three.js / Babylon / r3f (also loads Google Photorealistic Tiles) |
| CesiumJS | github.com/CesiumGS/cesium | 15883 / 2026-10-25 / Apache-2.0 | Globe engine. Weak at game feel and physics; use only its tooling |
| 3d-tiles-tools, gltf-pipeline | github.com/CesiumGS/3d-tiles-tools, CesiumGS/gltf-pipeline | 541 / 2136 | Tileset building and upgrading |
| iTowns | github.com/iTowns/itowns | 1275 / 2026-10-22 | three.js geospatial framework (IGN) |
| glTF-Transform | github.com/donmccurdy/glTF-Transform | 1993 / 2026-10-20 / MIT | **VERIFIED.** optimize, meshopt, KTX2 (via toktx), dedup, simplify |
| meshoptimizer | github.com/zeux/meshoptimizer | 8628 / 2026-10-14 / MIT | Compression, simplification, clusterization (meshlets for LOD) |
| KTX-Software | github.com/KhronosGroup/KTX-Software | 1391 / 2026-10-25 | Basis UASTC/ETC1S KTX2 textures |
| Draco | github.com/google/draco | 7526 / 2026-10-16 / Apache-2.0 | Alternative mesh compression (meshopt decodes faster) |
| Spark | github.com/sparkjsdev/spark | 3807 / 2026-10-20 / MIT | Best-maintained three.js Gaussian-splat renderer (mixes with meshes) |
| GaussianSplats3D | github.com/mkkellogg/GaussianSplats3D | 2927 / 2025-10-19 / MIT | Older three.js splat renderer |
| SuperSplat, splat-transform, spz | playcanvas/supersplat, playcanvas/splat-transform, nianticlabs/spz | 10462 / 1396 / 938 | Splat editing, cleanup and compression for the web |

### 3.4 Physics, vehicles, characters, navigation (browser)
| Project | URL | ★ / push / lic | Role |
|---|---|---|---|
| **Rapier** (rapier.js moved into the main repo `typescript/`) | github.com/dimforge/rapier | 5916 / 2026-10-10 / Apache-2.0 | WASM physics: trimesh/heightfield colliders, **DynamicRayCastVehicleController**, **KinematicCharacterController** (autostep for stairs/kerbs, max slope, snap-to-ground). Best fit |
| Jolt Physics.js | github.com/jrouwe/JoltPhysics.js | 583 / 2026-10-23 / MIT | WASM port of Jolt (Horizon Forbidden West): CharacterVirtual with stair walking, **wheeled vehicle constraint** with tire model. Strongest vehicle model |
| ammo.js / enable3d | kripken/ammo.js, enable3d/enable3d | 4583 / 1180 | Bullet in WASM. Older; raycast vehicle |
| cannon-es | pmndrs/cannon-es | 2065 / 2024-01 | Pure JS, slow for city trimeshes |
| react-three-rapier, ecctrl | pmndrs/react-three-rapier, pmndrs/ecctrl | 1517 / 810 | r3f bindings + ready-made physics character controller |
| **Sketchbook** | github.com/swift502/Sketchbook | 1754 / 2024-10 / MIT (archived) | three.js + cannon "GTA playground": walking/running/jumping character state machine, **enter/exit car, drive car/heli/plane**. Best gameplay reference to port onto Rapier/Jolt |
| racing-game | pmndrs/racing-game | 2224 / 2023-02 / MIT | r3f + cannon raycast vehicle, a polished feel reference |
| three-mesh-bvh | gkjohnson/three-mesh-bvh | 3527 / 2026-10-22 / MIT | Fast raycasts and capsule collision against huge static meshes (cheap character collision without a physics engine) |
| recast-navigation-js | isaac-mason/recast-navigation-js | 435 / 2026-07-06 / MIT | WASM navmesh + crowd (pedestrian NPCs on sidewalks) |
| three-pathfinding | donmccurdy/three-pathfinding | 1376 / 2026-10-06 / MIT | Simpler navmesh |
| isaac-mason/sketches | github.com/isaac-mason/sketches | 344 / 2026-05-06 / MIT | Many Rapier/Jolt vehicle and character controller samples |

Characters and animation (UNVERIFIED, not on GitHub): Mixamo (free, Adobe account), Quaternius / Kenney CC0 packs, the Ready Player Me successors, and CMU mocap via `bvh` → glTF.

### 3.5 Image / Street-View-based reconstruction (research, mostly CUDA GPU)
| Project | URL | ★ / push / lic | Notes |
|---|---|---|---|
| Street Gaussians | github.com/zju3dv/street_gaussians | 1409 / 2026-09-15 / custom | Dynamic urban 3DGS from drive logs (Waymo/KITTI style input: calibrated cameras + lidar) |
| DriveStudio (OmniRe) | github.com/ziyc/drivestudio | 1277 / 2025-08-27 / MIT | Unified 3DGS for urban scenes incl. humans and vehicles. Needs a CUDA GPU |
| CityGaussian (V1/V2) | github.com/Linketic/CityGaussian | 1276 / 2026-08-16 / custom | Large-scale aerial 3DGS with LoD, plus mesh extraction (V2). Relevant if we fly drone or oblique imagery |
| Hierarchical 3DGS | github.com/graphdeco-inria/hierarchical-3d-gaussians | 1475 / 2025-06-10 / custom | Very large street-captured scenes with LOD |
| HUGS | github.com/hyzhou404/HUGS | 191 / 2024-06 / custom | Holistic urban 3DGS with semantics |
| neuralsim (StreetSurf) | github.com/PJLab-ADG/neuralsim | 671 / 2024-03 / MIT | Neural surface reconstruction of streets. Gives meshes but is dormant |
| DrivingGaussian | github.com/VDIGPKU/DrivingGaussian | 420 / 2024-07 / – | Surround-view driving 3DGS |
| Block-NeRF, Urban Radiance Fields | (Waymo/Google papers) | no official code | Ideas only (appearance embeddings, exposure handling, lidar depth supervision) |
| nerfstudio / gsplat | nerfstudio-project/nerfstudio, gsplat | 12114 / 5835 | Training frameworks (gsplat needs CUDA; nerfstudio has slow CPU/MPS paths) |
| COLMAP / GLOMAP (merged) / OpenSfM / hloc | colmap/colmap, mapillary/OpenSfM, cvg/Hierarchical-Localization | 13061 / 3812 / 4250 | SfM to pose street/drone images, CPU-capable (slow). OpenSfM handles **equirectangular panoramas** (Mapillary) |
| MASt3R, VGGT, MapAnything | naver/mast3r, facebookresearch/vggt, facebookresearch/map-anything | 3141 / 14556 / 3852 | Feed-forward multi-view geometry: fast poses and depth from a few photos. GPU strongly preferred |
| Depth Anything V2, Depth Pro, MoGe | DepthAnything/..., apple-aiml-research/ml-depth-pro, microsoft/MoGe | 9026 / 5765 / 3090 | Monocular (metric) depth for panoramas or facade photos. Depth Anything V2-Small runs on CPU or M1 |
| streetview (python) | github.com/robolyst/streetview | 548 / 2026-04-15 | Downloads Google Street View panoramas, including historical ones (ToS grey, but this is private use) |
| mapillary-js | github.com/mapillary/mapillary-js | 522 / 2026-09-29 / MIT | Mapillary viewer. The Mapillary API gives panoramas plus SfM point clouds per sequence |
| CityDreamer, HunyuanWorld-1.0 | hzxie/CityDreamer, Tencent-Hunyuan/HunyuanWorld-1.0 | 702 / 2955 | Generative unbounded cities and worlds. Not geo-accurate, so they don't fit our goal |
| MatrixCity | city-super/MatrixCity | 379 | UE-rendered city dataset (benchmarks only) |

**Assessment for us:** with 5 cm orthophotos and 100 pts/m² lidar we already beat these pipelines on geometry. Their value is a **photoreal appearance layer** for facades and street level. Two practical paths:
- **(a)** Project Street View or Mapillary panoramas and drone images onto our lidar/roofer meshes. This is classic texture mapping, with depth models used only for occlusion checks. It works on CPU.
- **(b)** Train per-block 3DGS (needs a rented CUDA GPU) and render the splats in Chrome with Spark, *on top of* simple collision meshes.

Both need a GPU or a lot of manual work. Treat them as phase 2+.

### 3.6 Curb / stair / ramp detection from lidar
| Project | URL | ★ / push / lic | Notes |
|---|---|---|---|
| awesome-lidar-curb-detection | github.com/crankler/awesome-lidar-curb-detection | 48 / 2022-05 | Index of ~20 curb-detection codes (mostly ROS, vehicle-mounted 16–64 beam scanners) |
| CurbNet | github.com/guoyangzhao/CurbNet | 25 / 2026-04-02 / – | Deep curb segmentation (mobile lidar) |
| lidar_curb_detection, LiDAR-CURB-DETECTION, curb_detection | speedzjy/…, bigbigpark/…, linyliny/… | 27–73 / 2019–2022 | Height-jump / normal / smoothness heuristics on scan rings. Not directly usable on airborne tiles |
| Myria3D | github.com/IGNF/myria3d | 297 / 2026-10-22 / BSD-3 | IGN's airborne lidar HD semantic segmentation (deep learning). Re-classification if Turku classes are insufficient |
| Pointcept (PTv3) / Open3D-ML | Pointcept/Pointcept, isl-org/Open3D-ML | 3275 / 2358 | Point cloud segmentation frameworks |

**Assessment:** at about 63–100 pts/m² airborne density (roughly 10 cm spacing), a 12–15 cm kerb shows up as a height step in a **10 cm DTM made from class 2 points**. The kerb *location* is already given by osm2streets sidewalk edges and OSM `kerb=*` nodes. So the robust approach is **OSM-guided step fitting**:
1. Sample DTM profiles perpendicular to each predicted kerb line.
2. Fit a step to each profile.
3. Snap and smooth.

This is our own code, a few hundred lines with numpy/scipy (the existing venv has both). No existing repo does airborne kerb extraction for game meshes. Do the same for stairs: OSM `highway=steps` + `step_count` gives the expected stair, and lidar gives the start and end heights. Generate the treads procedurally.

### 3.7 Facades: parsing and procedural generation
| Project | URL | ★ / push / lic | Notes |
|---|---|---|---|
| DeepFacade, FacadeParsing, RTFP, VFM-FP | liuhantang/DeepFacade, gregunz/FacadeParsing, wbw520/RTFP, WHU-USI3DV/VFM-FP | 4–40 | Facade window/door segmentation research codes (CMP / ECP datasets). Small and unmaintained, except VFM-FP (2026), which uses vision foundation models |
| SAM 2 / Grounded-SAM-2 | facebookresearch/sam2, IDEA-Research/Grounded-SAM-2 | 20056 / 3795 | **Practical replacement:** text-prompted "window", "door", "shop sign" masks on rectified facade photos |
| Blosm facade textures | (Gumroad) | – | Procedural facade materials keyed by building:levels |
| OSM2World windows | (above) | – | **VERIFIED.** LOD4 generates 3D window insets per level. A procedural fallback that is already decent |

Recommended: rectify each facade from Street View/Mapillary using the roofer wall plane. Run SAM2 to get window and door grids. Then either:
- emit a procedural facade (insets + trim + material from the photo colour), or
- use the rectified photo directly as the albedo.

### 3.8 AI asset generation (street furniture, props)
| Project | URL | ★ / push / lic | Notes |
|---|---|---|---|
| TRELLIS / TRELLIS.2 | microsoft/TRELLIS, microsoft/TRELLIS.2 | 14104 / 12452, MIT | Best open image → textured glb quality. Needs a 16–24 GB CUDA GPU |
| Hunyuan3D-2 / 2.1 | Tencent-Hunyuan/Hunyuan3D-2(.1) | 15197 / 4277, custom (non-EU-restricted license, irrelevant for private use) | Shape + PBR texture. 2.1 gives production-ready PBR. CUDA |
| TripoSR | VAST-AI-Research/TripoSR | 7219 / 2026-06-04 / MIT | Fast and lower quality. Runs on CPU in about a minute (UNVERIFIED) |
| Stable Fast 3D (SF3D) | Stability-AI/stable-fast-3d | 1904 / 2025-01 | UV-unwrapped mesh in about 1 s on GPU; experimental MPS support on Apple Silicon |

Plan: photograph or crop Turku-specific props: Föli bus stops, the yellow-green waste bins, benches, lamp posts, bollards and kiosks. Generate them on a rented GPU (or MPS for SF3D and TripoSR). Decimate to under 5k triangles, then KTX2. Instance them at OSM2World-style placements.

### 3.9 "Real city in a game engine" and commercial references
- **Cesium for Unreal** (CesiumGS/cesium-unreal, 1248★, Apache-2.0), **cesium-godot** (unofficial, about 5★), and **Google Photorealistic 3D Tiles** (proprietary API).
  - Flight-sim-style photogrammetry streaming. The meshes are blobby at street level and have no curbs or separable collision.
  - Cesium/Google tiles could serve as a distant-LOD backdrop beyond our hand-built core. 3DTilesRendererJS can load them in three.js. Turku coverage was not checked (UNVERIFIED).
- **MS Flight Simulator 2020/2024 pipeline** (Blackshark.ai: 2D ortho + AI building extrusion; Bing photogrammetry). Closed source, but the *method* is the same as roofer + ortho texturing.
- **vts-geospatial / vts-browser-js** (Melown): `melowntech/vts-browser-js` has 223★, last push 2021-04, BSD-2. The `vts-geospatial` umbrella repo no longer exists on GitHub (404). Dead. Skip it.
- **Godot / Unreal / Unity OSM city repos**: all toys or dead (3D-OSM-GODOT, the Unity OSM job-test repos, gta-clone Unity repos). None worth forking.

---

## 4. Top 10 ranked for this project

| # | Project | Why | Use it for | Verified here? |
|---|---|---|---|---|
| 1 | **3DBAG/roofer** | LoD2.2 roofs from *our* lidar in about 1.5 min per 500 m tile on CPU. Semantic surfaces. Production-proven | All building shells + collision | **Yes** |
| 2 | **Rapier (WASM)** *(or Jolt Physics.js as a close #2 for vehicles)* | Browser physics with a kinematic character controller (autostep for kerbs and stairs, slope limits) and a raycast vehicle. Trimesh/heightfield colliders loaded per tile | Walking + driving + collisions | No (well-known) |
| 3 | **osm2streets (osm2streets-js WASM)** | Lane, sidewalk and intersection polygons with widths. Kerb-aware | Road/sidewalk/kerb mesh generation (drape on lidar DTM, step sidewalks) | **Yes** |
| 4 | **three.js** (+ **3DTilesRendererJS** or a custom tile grid) | Largest ecosystem; WebGPURenderer; meshopt/KTX2 loaders | Runtime renderer + streaming | **Yes (loader)** |
| 5 | **glTF-Transform + meshoptimizer + KTX-Software** | Standard browser asset pipeline. Took 224 MB down to 57 MB with no tuning | Pack every tile (meshopt, KTX2/UASTC for ortho and facades) | **Yes** |
| 6 | **OSM2World** | Fastest complete baseline; furniture, vegetation and window placement logic; direct 3D Tiles output | Prop/vegetation placement oracle; early "whole-city" low-LOD fill | **Yes** |
| 7 | **PDAL** (+ our numpy kerb fitter) | DTM at 10 cm, height-above-ground, classes 2/6 split, breaklines | Terrain + kerb/stair/ramp extraction | No (standard) |
| 8 | **swift502/Sketchbook** | The only open GTA-style three.js playground with character ↔ vehicle transitions | Port its character state machine and car enter/exit onto Rapier | No |
| 9 | **recast-navigation-js** | WASM navmesh/crowd | Pedestrian NPCs on sidewalks, (later) traffic on osm2streets lanes | No |
| 10 | **Spark (3DGS in three.js)** + **gsplat** / **TRELLIS.2** | Photoreal layer and props when a GPU is rented | Phase 2: photoreal façades/street level, AI props | No (needs GPU) |

Honourable mentions:
- **Streets GL**: TypeScript reference for in-browser OSM geometry and a PBR render graph.
- **3dfier**: idea for a watertight terrain + road + building surface.
- **cjio**: CityJSON → glb.
- **SAM2**: facade parsing.
- **OpenSfM**: panorama SfM.
- **Depth Anything V2**: CPU depth.
- **A/B Street**: traffic logic.
- **libOpenDRIVE**: OpenDRIVE road meshes with sidewalk heights.

---

## 5. Gaps no project fills (we must build)
1. **Lidar-measured kerbs, stairs and ramps in the collision mesh.** Nothing open does airborne-lidar kerb extraction for game meshes. Approach: OSM/osm2streets-guided step fitting on a 10 cm class-2 DTM (see section 3.6).
2. **Terrain + road + building coupling.** roofer floors (`rf_h_ground`) and osm2streets polygons must agree with one DTM. 3dfier's idea is the reference.
3. **Ortho/photo texturing of roofs and facades.** Project the 5 cm ortho onto roofer roof faces (trivial). Facades need Street View/Mapillary or drone imagery (see section 3.7).
4. **Tiling and LOD for the browser.** Proposal:
   - About 250 m tiles.
   - Per tile, a render glb (meshopt + KTX2, under about 15 MB at LOD0) plus a **separate simplified collision glb** (trimesh for kerbs and stairs, heightfield for open ground).
   - A coarser LOD1/LOD2 per tile from roofer LoD1.2 + ortho drape.
   - Served as static files.
   - OSM2World's `tileset` and 3DTilesRendererJS are options if we adopt 3D Tiles. A plain JSON tile index is simpler for game streaming.
5. **Gameplay glue** (character ↔ car, camera, traffic, NPCs). Port it from Sketchbook and the Rapier/Jolt samples.

## 6. Risks / gotchas seen
- **Source URLs:** `opaskartta.turku.fi/3d/...` now 302-redirects to `turku.asiointi.fi/3d/...`, so use `curl -L`. The LAZ header has no CRS, so pass `--srs EPSG:3877` explicitly.
- **Overpass:** overpass-api.de and kumi.systems returned HTTP 406 / no data to curl from the remote. The main OSM API `/api/0.6/map` worked, but it is limited to 50k nodes per call. For a city-wide run, use a Geofabrik Finland extract plus osmium.
- **OSM2World:** pulls whole ways and relations, so scenes extend well beyond the bbox. Clip before or after conversion. OBJ export crashed in 0.5.0-SNAPSHOT.
- **roofer:** GPL-3.0 (private use, fine). Ships and docked boats tagged `building` become "buildings", so filter by tag and water class.
- **AI and 3DGS tooling:** essentially all of it needs a CUDA GPU; the remote has none. M1 Max MPS works only for some of them (SF3D, Depth Anything).
- **Blosm:** the code is no longer on GitHub, only through Gumroad.

## 7. Reproduce
Remote layout under `~/turku3d/research/prior-art/`:
- `data/`: the LAZ tile, OSM data, footprints
- `tools/bin/roofer`
- `tools/osm2world/`
- `node/`: glTF-Transform 4.x and osm2streets-js 0.1.4
- `scripts/`
- `out_roofer/`
- `out_osm2world/`
- `out_o2s_lanes*.geojson`

The Python venv is `~/turku3d/.venv` (laspy[lazrs], numpy, pyproj, shapely, trimesh). Local copies of the scripts are in `docs/research/prior-art-assets/scripts/`.

---

## Verification (adversarial check, 2026-10-25)

An independent verifier re-ran the claims that matter most for architecture decisions, using different settings and its own scripts. Scripts are in `osgallery-dev:~/turku3d/research/prior-art/verify/` (`laz_check.py`, `kerb_probe.py`, `osm_count.py`, `cj_count.py`). The Rapier test is local, in the session scratchpad (`rapier/test.mjs`, using `@dimforge/rapier3d-compat@0.21.0`). No new large downloads were made; the existing LAZ and OSM files were reused.

| # | Claim | Verdict | How it was checked |
|---|---|---|---|
| 1 | LAZ tile has 15.68 M points, RGB, no CRS, class 2 = 4.0 M, class 6 = 4.83 M | **CONFIRMED** | laspy header and VLRs: LAS 1.2, point format 3, TerraScan; the only VLR is the laszip one, so `parse_crs()` returns None. Class counts match exactly. |
| 2 | Kerbs are detectable "from ~63–100 pts/m² class-2 points" in a 10 cm DTM | **Density REFUTED, detectability CONFIRMED (statistically)** | Class-2 density is a **median of 33 pts/m²** (p10 12, p90 38) over the 1 m cells that have ground points. 63 pts/m² is the density of *all* classes. Only **30 % of 10 cm cells** contain a ground point, so a plain 10 cm gridded DTM would be mostly holes. Profiles still work: 1,059 cross-street profiles (0.8 m corridor, 10 cm bins, 2–12 m from the OSM centreline) found a ≥ 8 cm up-step in **49 %** of cases (median max step 7.9 cm). A control run of along-street profiles found one in only **4.5 %** (median 1.4 cm). Adding class-12 near-ground points raises the point count by 52 % but barely changes the result. Conclusion: fit kerbs along lines (profiles, or edge fitting guided by vectors), not by reading a raw 10 cm raster. |
| 3 | roofer v1.0.0: 160 buildings, 92 s, CPU only, 690 MB; 147 got LoD2.2; glb 31,670 tris | **CONFIRMED** (reproducible) | The original log shows 16:35:22 → 16:36:54, which is 92 s. A re-run with `--lod22 -j 4` took 109 s wall, 210 s CPU and 387 MB RSS. Both runs: 160 Buildings with `rf_success=True`, of which 14 have `rf_extrusion_mode=skip`; 147 BuildingParts with LoD2.2. Counting the glb with trimesh gives 31,670 faces. |
| 4 | osm2streets-js 0.1.4: 748 lane polygons; the `osm2lanes` field is required | **CONFIRMED, with a caveat** | Re-run gave 748 polygons (1.4 s on this run). Dropping the field throws `Error: missing field 'osm2lanes'`. With `inferred_sidewalks/kerbs=false` it gives 950 polygons, so the inference options change the output a lot. **Caveat:** the npm package was last published on **2023-06-04**, but the GitHub repo was pushed on 2025-10-02. The WASM we use is more than 2 years behind the source, so plan to build the WASM ourselves from the repo. |
| 5 | OSM block: 116 kerb nodes, 56 steps, 30 step_count, 252 buildings | **Partly REFUTED (minor)** | An XML parse (not grep) gives **100 kerb-tagged nodes + 16 kerb-tagged ways** (that is where 116 comes from); 97 `barrier=kerb` nodes and 26 ways; **54** `highway=steps` ways, not 56; 30 step_count; buildings are 245 ways + 6 relations + 1 node = 252. |
| 6 | Overpass unreliable from the remote (406); use Geofabrik | **Partly REFUTED** | overpass-api.de still returns 406 with POST and with a User-Agent, and kumi.systems timed out. But **`https://maps.mail.ru/osm/tools/overpass/api/interpreter` returned 200** with fresh data (base 2026-10-25T16:53Z); a second call got a 504. Mirrors are flaky; Geofabrik + osmium is still the right choice for city-wide runs. |
| 7 | Blosm GitHub has only a README (code via Gumroad); vts-geospatial 404; Sketchbook and rapier.js archived | **Partly REFUTED** | `vvoovv/blosm` does contain only README.md. But **`prochitecture/blosm`** (GPL-3.0, pushed 2026-10-19) holds the full addon source (`building`, `terrain`, `threed_tiles`, `realistic`, ...). vts-geospatial 404: confirmed. Sketchbook archived (last push 2024-10): confirmed. rapier.js archived (2026-07); its JS/TS bindings now live in `dimforge/rapier/bindings/typescript`, and npm `@dimforge/rapier3d-compat@0.21.0` was published 2026-09-25: confirmed. |
| 8 | Rapier KCC autostep handles kerbs and stairs; its raycast vehicle suits driving over kerbs (was UNVERIFIED) | **CONFIRMED for Rapier**; Jolt UNCERTAIN (not run) | Rapier 0.21 in Node, with a **trimesh** collider (as a collision glb would give) and capsule r = 0.3, half-height 0.6, `enableAutostep(0.2, 0.15, true)`, `snapToGround(0.3)`, walking at 1.4 m/s. A 15 cm kerb is climbed (feet end at y = 0.16). A 25 cm step blocks the character (x stops at 1.70). 8 stairs with 17 cm risers and 30 cm treads are climbed to y = 1.37. A 1:12 ramp is followed. A 30° slope with a 25° max climb stops the character. `DynamicRayCastVehicleController` (4 wheels, 1,200 kg): the chassis rest height goes from 0.750 on the flat to **0.900** on a 15 cm kerb, at both 3 m/s and 10 m/s. Wheel rays react to kerb geometry in the collision mesh. Vehicle *feel* was not judged. |
| 9 | OSM2World has a `tileset` subcommand | **CONFIRMED** | `java -jar OSM2World.jar help tileset` prints the usage (`--baseDir`, `--bbox`/`--bboxTiles`, `--lod`, `--precompressedTiles`). |
| 10 | Open question: does Turku publish official LoD2 / footprints? | **ANSWERED: yes** | The Turku WFS `https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx?service=WFS&version=1.0.0&request=GetFeature&typeName=bldg:Building_LOD2&bbox=23459500,6704500,23460000,6705000` returned HTTP 200 (15.2 MB, 1.3 s) with **136 buildings and 4,268 RoofSurfaces**. This matches `reconstruction.md`. That file also documents city footprints (`GIS:Rakennukset`, 135) and kantakartta curb/carriageway-edge vectors. Those are better kerb-line guides than OSM's 116 point/way tags, and better footprints for roofer than the 160 OSM ones (which include boats). |

### Corrections to apply to this report
- The "~63–100 pts/m² class-2" figure is wrong. Ground density is about 33 pts/m² median and 12 pts/m² at p10. Design kerb extraction as line or profile fitting (≥ 0.5 m along-line corridors), not as a 10 cm raster DTM. The statistical step signal is clear: 49 % of cross-street profiles versus 4.5 % in the control.
- Kerb, stair and ramp *location* guidance should come primarily from the Turku kantakartta WFS vectors, with OSM as a fallback. Building inputs should be compared against the city's own CityGML LoD2. Don't treat roofer + OSM footprints as the only option.
- Blosm source is openly available at `prochitecture/blosm` (GPL-3.0).
- The osm2streets-js npm package (0.1.4, 2023-06) is stale. Build the WASM from the repo.
- Overpass: maps.mail.ru mirror works intermittently. Use Geofabrik + osmium for bulk.
- OSM counts: 100 kerb nodes + 16 kerb ways; 54 steps ways.
- Rapier's character autostep and raycast vehicle over kerbs are now VERIFIED in headless Node (not yet in Chrome). Jolt remains unverified.
