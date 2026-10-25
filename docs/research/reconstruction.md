# Reconstruction pipeline: lidar + ortho + panos + city vectors → browser game assets

Research date: 2026-10-25. Target: a GTA-style Turku running in **desktop Chrome** (WebGPU/WebGL2 + WASM physics).
Every claim is tagged **VERIFIED** (I ran it, numbers below) or **UNVERIFIED** (docs, web or my own judgement).

Remote work dir: `osgallery-dev:~/turku3d/research/reconstruction/` (`data/` LAZ, `work/` WFS vectors, `out/` results,
`scripts/` copies of the scripts, `tools/` roofer, micromamba, npm). Local scripts: `/Users/wnt/Documents/game-test/tools/reconstruction/`.
Local copies of the result meshes and renders: `/Users/wnt/Documents/game-test/docs/research/reconstruction-assets/`.

---

## 0. TL;DR

1. **The city already publishes most of the hard vector data, and it is precise enough for the collision mesh** (VERIFIED). Turku's WFS
   (`https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx`) has the base-map edge lines: road edges/curbs (`kanta:Liikennevayla`
   kplkoodi 62520), footway edges (`kanta:Kevyenliikenteenvayla` 62522/62523), and `kanta:Rakenne` lines for stairs (`porras`), ramps (`luiska`),
   retaining-wall tops (`tukimuurin yläreuna`), fences (`aita`) and pools. It also has street trees with species and planting year
   (`GIS:Puut`), city furniture, footprints, and **textured CityGML LoD2 buildings** (`bldg:Building_LOD2`). The texture URLs return 404.
2. **Curb-preserving ground meshes work** (VERIFIED, 100 × 100 m patch at 23459525, 6704575). I built a constrained Delaunay
   triangulation (CDT) with double breaklines from the curb vectors, snapped to the lidar edge. It uses the **same triangle budget as a
   0.5 m heightmap (82 k tris/ha)**. At lidar-confirmed curbs (≥ 8 cm step):
   - **10–90 % transition width: 0.10 m, against 0.60 m for the 0.5 m grid.**
   - Median max slope: 61°, against 10° for the grid.
   - Step height error: 9 mm.

   A 0.5 m grid turns every curb into a 10° ramp that a car rolls over. Lidar alone, even a 0.1 m grid with 2 M tris, only reaches a
   0.36 m width. Collision meshes need the vector breaklines.
3. **Lidar is about 63 pts/m² in this tile (21 ground pts/m² in the patch), not 100** (VERIFIED from the header and point counts). The ground
   points cannot show a curb face: the lidar-only profile width is 0.49 m. Vertical curb faces must be *synthesised* from the vectors.
4. **3DBAG `roofer` v1.0.0 runs on the CPU-only server** (VERIFIED). It reconstructed 135/135 buildings in the 500 m tile in 83 s, giving
   LoD2.2, 34.6 k triangles and a 0.7 MB GLB. Fit quality is mediocre on the large perimeter blocks: median roof RMSE 0.86 m, and only 16
   buildings have RMSE ≤ 0.3 m. The city's own LoD2 CityGML should be compared with it before choosing (§4).
5. **Browser delivery is cheap** (VERIFIED). The 82 k-tri collision/ground tile is 1.48 MB as a raw GLB, **72 KB with Draco** and 265 KB
   with gltfpack/meshopt quantised (177 KB gzipped). That is roughly **0.3–1 MB per 250 m tile** for ground collision.
6. **Trees** (VERIFIED). CHM peak detection found 484 trees in the tile in 12 s. It matched 80 % of the register trees planted before
   2016. Most misses are trees planted after the 2021 flight. Use the register for species and lidar for height and crown radius.
   Generate the instances from both.

---

## 1. Data sources (all EPSG:3877, heights N2000)

| Source | Endpoint | Status |
|---|---|---|
| Classified lidar 2021, 500 m tiles | `https://turku.asiointi.fi/3d/pistepilvi/<xmin>_<ymax>.laz` (the `opaskartta.turku.fi` URL in `laz-tiles.csv` now 302-redirects here; `curl -L`) | VERIFIED: tile `23459500_6705000` is 96 MB, 15.68 M pts, LAS 1.2 PDRF 3 (RGB), TerraScan, scale 1 mm, **no SRS in header** (pass `EPSG:3877` yourself), 1.9 s download |
| Thinned ground-only tile | `https://turku.asiointi.fi/3d/maanpinta/<tile>.laz` | VERIFIED: only 39 k pts/tile (0.16 /m²), useless for curbs |
| Base map vectors (kantakartta), WFS 1.0.0 GML | `https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx?service=WFS&version=1.0.0&request=GetFeature&typeName=<layer>&bbox=x0,y0,x1,y1` | VERIFIED, < 0.5 s per layer per 500 m tile |
| CityGML LoD2 buildings | same WFS, `typeName=bldg:Building_LOD2` (also LOD0/1/3) | VERIFIED: 136 buildings / 15 MB GML / 2 s; 4 268 RoofSurface, 4 923 WallSurface, `measuredHeight`, address, year; created 2016 |
| CityGML facade textures | `https://opaskartta.turku.fi/3d/citygml/textures/<id>.jpg` (from `app:imageURI`) | VERIFIED **404** (the redirect to turku.asiointi.fi also 404s; directory listing is 403) |
| CityGML roads, furniture, vegetation | `tran:Road_LOD2` (342 in the tile, 13 MB), `frn:CityFurniture_LOD2` (96), `veg:*` | VERIFIED: fetched; not yet parsed in detail |
| Street trees | `GIS:Puut` (species, `Istutusvuosi`) | VERIFIED: 120 in the tile |
| Footprints | `GIS:Rakennukset` (also `kanta:Rakennus`) | VERIFIED: 135 in the tile |
| Spot heights, contours, rocks, slopes | `kanta:Korkeuspiste`, `kanta:Korkeuskayra`, `kanta:Jyrkanne`, `kanta:Kivi` | VERIFIED: listed in GetCapabilities; Korkeuspiste fetched |
| Orthos 5 cm / drone 2.5 cm / 2026 12.5 cm | WMS/tiles described in `~/leikkipuistodata/HANDOVER.md` (`tiles2026.py`, `drone-crops.py`) | VERIFIED by the earlier project; not re-fetched here |

The full layer list is in `work/wfs-caps.xml` on the remote. The GML → GeoJSON converter is `tools/reconstruction/gml2geojson.py`.

### Which vector line classes are real height steps? (VERIFIED, whole 500 m tile, 1 m stations)

I took the median ground height 0.15–0.6 m on each side of each line and computed |Δz|:

| kplkoodi / type | stations | median step | p75 | share ≥ 8 cm | Interpretation |
|---|---|---|---|---|---|
| 62520 road edge (`reunaviiva`) | 5 606 | 7.3 cm | 10.6 cm | 44 % | **curb**; the flush parts are crossings and lowered kerbs |
| 62522 footway edge | 2 278 | 2.5 cm | 4.2 cm | 10 % | mostly flush (footway/grass edges) |
| 66634 retaining-wall top | 250 | 41 cm | 84 cm | 71 % | retaining wall: becomes a vertical wall collider |
| 66626 `porras` (stairs) | 379 | 6.8 cm | 24 cm | 49 % | stair outlines and nosings (see §2.4) |
| 62540 `luiska` (ramp) | 206 | 2.2 cm | 3.6 cm | 2 % | ramp edge, no step: the slope comes from the DTM |
| 66630/66636 `aita` (fence) | 706 | 8–13 cm | — | ~50 % | fences: make them **thin box colliders**, not ground steps |
| 66618 / 66620 (untyped) | 1 428 | 2.6–3.3 cm | 18–21 cm | 31–39 % | mixed; needs a code-list lookup |
| 64500 `allas` (pool) | 24 | 22 cm | 44 cm | 79 % | pool rim |

So **62520 + 66634 + 64500 + steps detected per station** are the breakline set. In my run I used every class with median ≥ 5 cm, fences
included. In the production pipeline, fences should become separate wall colliders.

---

## 2. Ground: DTM meshing that keeps curbs

### 2.1 Experiment setup (VERIFIED)

- Patch: x 23459525–23459625, y 6704575–6704675 (city centre by the riverside, chosen by the most curb and stair line length in the tile).
  It has 505 k points (50.5 /m²) and 213 k ground points (21.3 /m²). Class histogram: 2 ground, 3/4/5 vegetation, 6 building, 12 overlap
  (5.5 M of the tile's 15.7 M points are class 12).
- Script: `tools/reconstruction/ground_mesh.py <laz> <vectors_dir> 23459525 6704575 100 out/ground`. Python 3.11 env from conda-forge:
  PDAL 2.10.0, laspy, lazrs, scipy, shapely, trimesh, `triangle` (Shewchuk CDT), Open3D 0.20 (quadric decimation and ray casting). Wall
  time **129 s** for everything, 2.7 GB RSS. The CDT itself takes **5–9 s**.
- 10 % of the ground points are held out for accuracy. Curb "truth" is the lidar step, measured from raw points binned perpendicular to the
  line (0.1 m bins, ±0.35 m along the line).
- Meshes:
  - **A**: median grid DTM at 0.5 m.
  - **A2**: the same at 0.25 m.
  - **B**: Triangle CDT on a 0.3 m median grid plus *double breaklines* (two polylines 6 cm apart that carry the measured top and bottom
    heights, so the step is near-vertical).
  - **B2**: B with the breaklines **snapped to the lidar edge** (max-gradient offset within ±0.45 m, median-filtered along the line).
    The snap moved 18 % of stations by more than 10 cm, p90 18.5 cm. This matches the vector's stated 0.15 m positional uncertainty.
  - **B3**: B2 on a 0.5 m base grid.
  - **C0**: a 0.1 m grid.
  - **C**: C0 decimated with quadric decimation to B's triangle count (the "lidar only, smart decimation" baseline).

### 2.2 Results: 88 curb stations (62520) with a lidar step ≥ 8 cm (median 11.3 cm, p90 38 cm)

| Mesh | Triangles | Hold-out RMSE | Curb step error (median) | **10–90 % transition width** | Width ≤ 0.15 m | Max slope across curb |
|---|---|---|---|---|---|---|
| Lidar points themselves | — | — | — | 0.49 m | — | — |
| A grid 0.5 m | 80 000 | 2.1 cm | 1.4 cm | **0.60 m** | 0 % | **10°** |
| A2 grid 0.25 m | 320 000 | 1.8 cm | 0.8 cm | 0.45 m | 1 % | 19° |
| C0 grid 0.1 m | 2 000 000 | 1.7 cm | 0.8 cm | 0.36 m | 10 % | 36° |
| C 0.1 m + quadric → 223 k | 223 158 | 1.7 cm | 0.8 cm | 0.38 m | 8 % | 34° |
| B CDT + vector breaklines | 223 159 | 1.7 cm | 0.5 cm | 0.22 m | 42 % | 47° |
| B2 CDT + snapped breaklines | 223 235 | 1.7 cm | 0.6 cm | 0.14 m | 52 % | 61° |
| **B3 snapped, 0.5 m base** | **81 836** | 2.3 cm | 0.9 cm | **0.10 m** | 55 % | **61°** |

Retaining walls (42 stations, median 26 cm step): B3 gives a 0.07 m width and an 81° face. Grid A gives 0.43 m and 31°.

**Conclusion (VERIFIED for this patch).** Height accuracy is about 2 cm for every method. Only the breakline CDT produces collision geometry
in which a curb *is* a step: the grid turns it into a 10° ramp. B3 costs no more than the naive 0.5 m heightmap. The renders
`reconstruction-assets/curb_A_grid_0.5m.png` and `curb_B3_cdt_snapped_base0.5.png` show the same 12 cm curb in each: the grid gives a smooth
undulation, B3 a clean vertical step.

Why 45 % of curb stations are still wider than 0.15 m: the snap fails where the lidar gradient is weak (parked cars, low curbs), and line
junctions and crossings create Steiner points. Two fixes:
- Clip breaklines at pedestrian crossings (OSM `kerb=lowered` / `crossing`).
- Rebuild curbs as explicit **curb solids**: extrude a 15 cm × h(x) granite profile along the snapped line, and use the same profile for
  the visual curb stone mesh.

### 2.3 Recommended ground pipeline (VERIFIED parts marked)

1. Fetch the LAZ tile and the WFS vectors (VERIFIED).
2. Ground points come from class 2 (VERIFIED adequate). Optionally re-run PDAL `filters.csf`/`filters.smrf` where class 2 has holes
   (UNVERIFIED need).
3. Breaklines:
   - From vectors: 62520 curbs, 66634 retaining walls, 64500 pools, 62538 inner slope tops (VERIFIED).
   - Snap to the lidar gradient (VERIFIED: +40 % sharper).
   - Add lidar-only detections (0.1 m DTM gradient > 30°, step 6–25 cm) for curbs that are missing from the vectors (UNVERIFIED, not run).
4. Run the CDT with Triangle (`pc` switches). **Without `c`, Triangle eats every unprotected boundary triangle**: my first run produced
   1 201 triangles (VERIFIED pitfall).
   - Base points: an adaptive set is better than the 0.5 m grid. Greedy insertion until 2 cm tolerance should cut flat streets 3–10×
     (UNVERIFIED estimate).
5. Classify the surface per triangle from vector polygons and the ortho:
   - road = inside the 62520 edges / `GIS:Katualueet`
   - sidewalk = between curb and building / footway polygon
   - grass = `GIS:Viheralueet`
   - then sample ortho colour per triangle

   The output is a material ID per triangle, used both for physics (friction, footstep sounds) and for rendering. (UNVERIFIED, not run.)
6. Export tiles in local coordinates (VERIFIED).
   - The **visual** ground can be the same mesh with UVs into the ortho (5 cm = 2 000² px per 100 m) or tiling PBR materials keyed by
     the material ID.
   - The **collision** mesh is the same triangles without UVs.

### 2.4 Stairs, ramps, retaining walls

- `porras` lines are drawn as individual short parallel lines about **0.49 m apart** (median nearest-neighbour centroid spacing in the
  patch; groups of 3 lines of 1.5 m, 3.3 m and 7.4 m), plus long 63–86 m lines along the river terrace (VERIFIED).
- At 21 ground pts/m², the lidar resolves only the total rise (one group: 9.49 → 9.32 m, 17–18 cm), not every tread (VERIFIED).
- Recommendation (UNVERIFIED):
  - Generate stairs **procedurally**: footprint from the porras lines' bounding polygon, total rise from the lidar top/bottom,
    riser count = round(rise / 0.16), tread from the line spacing.
  - The collision is a single **inclined plane** (what GTA/Unreal-style character controllers prefer) plus visual step geometry.
  - Ramps (`luiska`) need nothing extra: the CDT keeps their slope (2 cm cross-step).
  - Retaining walls (66634 tops) become vertical wall quads from the top line down to the lower DTM, plus a 0.3 m thick wall collider.
- Fences (`aita`, 206 lines in the tile): thin box colliders of standard height (1.2 m), with height validated from class 1 lidar
  points above the line.

---

## 3. Buildings

### 3.1 3DBAG roofer v1.0.0 (VERIFIED)

```
curl -sSL -o roofer.tgz https://github.com/3DBAG/roofer/releases/download/v1.0.0/roofer-linux-x86_64-v1.0.0.tar.gz
tar xzf roofer.tgz -C roofer && chmod +x roofer/bin/roofer      # the tarball ships it non-executable
export GDAL_DATA=$PWD/roofer/share/gdal PROJ_DATA=$PWD/roofer/share/proj
ogr2ogr -f GPKG -a_srs EPSG:3877 -nlt POLYGON -makevalid -clipsrc 23459500 6704500 23460000 6705000 \
  -nln footprints -sql "SELECT PysyvaRakennusTunnus AS bid, CAST(Kerrosluku AS integer) AS floors, Geometry FROM Rakennukset" \
  fp2.gpkg wfs_GIS_Rakennukset.xml
roofer/bin/roofer -j 10 --srs EPSG:3877 --lod12 --lod22 pistepilvi_23459500_6705000.laz fp2.gpkg out/roofer
```

- **First attempt failed** with `[cropper] Failed to crop tile 0. bad optional access`. That attempt used GML → GPKG with geometry type
  `Unknown`, unclipped footprints and `--box`. Forcing `-nlt POLYGON -makevalid -clipsrc` fixed it. I did not isolate which of the three
  mattered.
- The GPKG GML import also needs an explicit `-sql` field list, because `gml_id` NOT NULL breaks ogr2ogr.
- Docker tags in the docs (`3dgi/roofer:latest`) don't exist; Hub only has `develop`/`v1.1.0-beta.1`. Use the release tarball. An
  install script exists at `https://raw.githubusercontent.com/3DBAG/roofer/refs/heads/main/distribution/install.sh`.
- Output is CityJSONSeq. `tools/reconstruction/cityjsonseq2glb.py` converts it to GLB (earcut triangulation, roof and wall colours,
  local origin).

| Run | Wall time | Buildings | LoD2.2 tris | GLB | Roof RMSE median / p90 | Roof planes median / max |
|---|---|---|---|---|---|---|
| defaults (complexity 0.888, eps 0.3, density ceiling 20) | **83 s** | 135/135 ok | 34 644 | 0.70 MB | 0.86 m / 2.0 m | 16 / 159 |
| complexity 0.95, eps 0.15, density ceiling 60 | 171 s | 135/135 | 53 648 | — | 1.00 m / 2.2 m | 31 / 282 |

Only 16 of 135 buildings reach RMSE ≤ 0.3 m. The bad ones are 1 300–5 000 m² perimeter-block footprints, 15–28 m tall, with many roof
parts. My hypotheses (UNVERIFIED):
- `GIS:Rakennukset` footprints don't match the roof outlines: eaves overhang, merged courtyards.
- Steep mansard and pitched roofs with dormers exceed the plane-arrangement complexity.

Next steps:
- Try `kanta:Rakennus` footprints.
- Use the `--crop-output` LAS to inspect a bad building.
- Compare with the city's LoD2 per building (the 3D IoU / Hausdorff distance against lidar).

### 3.2 Turku's own LoD2 CityGML (VERIFIED it exists; quality not measured)

There are 136 buildings in the tile with 4 268 roof surfaces (about 31 per building, so more detailed than roofer's 16), a
`measuredHeight` and `storeysAboveGround`. It is hand- or semi-automatically produced (2016) and has `BuildingInstallation` parts. Its
textures are referenced but 404.
**Likely best path:** use the city LoD2 for the old city blocks and roofer for anything newer than 2016 or missing. Choose per building by
roof-point RMSE against the 2021 lidar. A GML → glTF converter is needed: citygml-tools / FME, or `citygml2gltf`-style Python
(UNVERIFIED).

### 3.3 Facade detail and texturing (UNVERIFIED, research only)

- LoD2 walls are flat. GTA-like facades need:
  - **Facade textures**: project street-level panoramas onto the wall quads. The camera poses come from the pano metadata (see the
    `streetview` topic), refined by aligning the pano depth to the LoD2 walls. Mask occluders (cars, people, trees) with a segmenter, blend
    several views by angle and distance, and inpaint holes with AI texture models (see `ai-textures`).
  - **Window/door parsing**: run a facade segmentation model on the rectified texture, then extrude window recesses (10–20 cm) into the
    *visual* mesh only. Collision stays LoD2 plus a ground-floor facade offset.
  - **Roofs**: project the 5 cm ortho (or 2.5 cm drone) onto roof planes directly. A nadir ortho has building lean (true-ortho not
    guaranteed), so shift by the roof height × view-angle, or use the 2026 tiles and accept the lean.
- No recent oblique imagery exists for Turku (from the earlier project's `README-oblique.md`), so pano projection is the main facade source.

---

## 4. Vegetation and street furniture

- **Trees** (VERIFIED): `tools/reconstruction/trees.py`. It builds a CHM from classes 4 and 5 on a 0.5 m grid, applies a Gaussian
  (σ = 1 cell), and takes local maxima in a 4.5 m window with height ≥ 4 m.
  - It finds 484 trees in the 500 m tile in 12 s, with median height 12.3 m. Crown radius is the median of 8 rays to the 50 % height.
  - Against the 120 register trees, 49 match within 3 m (median distance 1.1 m). Of the 71 misses, **59 were planted after 2015**
    (so small, or absent in 2021).
  - Recall for trees planted ≤ 2015 is 47/59 = 80 %.

  Output JSON `{x, y, z, h, crown_r}` is ready for instancing. Combine it with register species (`Acer platanoides`, `Tilia` …) to pick
  the model variant and scale.
- **Tree models** (UNVERIFIED): instanced low-poly trunk plus a billboard/card crown per species, with 2–3 LODs and an impostor at
  distance. Sources: Blender Sapling/Geometry-Nodes generated, or CC0 packs (see the `prior-art-assets` topic). Collision is a trunk
  capsule only.
- **Street furniture** (partly VERIFIED): `frn:CityFurniture_LOD2` (96 in the tile), `GIS:Liikennemerkit` (traffic signs with type),
  `GIS:Varusteet`, `GIS:Pysakointiautomaatit`, `GIS:Polkupyoraparkki` and `kanta:Pysakki` exist as points. Use them as placement for a
  prefab library (pole, sign by code, bench, bin, bike rack, lamp). Detect what the registers miss with lidar: thin vertical clusters
  in class 1 with height 2.5–10 m become poles (adapt `~/leikkipuistodata/detect_zip.py` PCA linearity) (UNVERIFIED).

---

## 5. Visual layer options (all UNVERIFIED unless stated)

| Option | Pros | Cons for this project | Verdict |
|---|---|---|---|
| **Textured meshes** (CDT ground + LoD2/roofer buildings + projected ortho/pano textures + PBR materials) | Same geometry as collision, relightable (day/night), cheap on GPU, KTX2/Basis streams well | Facade realism depends on pano projection quality; much tooling | **Primary** |
| **3D Gaussian Splatting** from panos + aerials | Photoreal for static views | No GPU on the server. Training needs CUDA (gsplat, nerfstudio, 2DGS/PGSR). On the M1 Max, OpenSplat (Metal) or Brush (WebGPU/Rust) can train but slowly. Panos sparse in time, poor coverage of upper floors. Baked lighting; dynamic objects and relighting are hard. Web splat renderers (e.g. Spark, gsplat.js, mkkellogg GaussianSplats3D) handle about 1–5 M splats in Chrome, so a city needs heavy LOD and streaming. | **Experiment** on one block for "hero" facades and skyline only |
| **2DGS/PGSR → mesh** | Turns splats into meshes with better geometry | CUDA only, same coverage problem | Skip until a GPU is available (cloud rental) |
| **Google Photorealistic 3D Tiles** as visual skin + own collision | Instant city-wide visuals, Cesium/3DTilesRendererJS load them in Chrome | Needs an API key and has ToS limits (no caching or offline). Coverage of Turku at good quality is unknown (not checked). Melted look at street level, no separation into objects, attribution overlay. | Useful as a **far-LOD backdrop / reference**, not the main street-level look |

The visual layer and the collision layer are separate in every option. The collision is always our own (§2–4).

---

## 6. Output formats, tiling, coordinates, browser budgets

- **Coordinates** (VERIFIED reasoning): EPSG:3877 eastings are ~2.35e7. **float32 resolution at 2.35e7 is 2 m**, so raw coordinates in
  vertex buffers break. My GLBs store float32 positions relative to the patch SW corner, with Z in N2000 metres; the glTF is Y-up
  `(x, z, -y)`.
  - Recommendation: fix a **world origin** (e.g. E 23459000, N 6704000, H 0). Each tile stores its own offset as a node translation
    (doubles in JSON), and the engine rebases the camera (floating origin) if the world grows beyond about 10 km.
- **Tiling**: a 250 m quadtree aligned to the city's 500 m LAZ grid (a LAZ tile = 2 × 2 game tiles), plus a 50 m skirt/margin when
  meshing (the CDT used 5 m). Tile ID = `{x0}_{y0}` in metres from the origin.
  - **Collision**: per tile, a binary trimesh for Rapier (WASM) / Jolt (WASM) heightfield-free colliders. Load within about 150–250 m of
    the player.
  - **Visual**: glTF 2.0 with `EXT_meshopt_compression` or `KHR_draco_mesh_compression` and `KHR_texture_basisu` (KTX2), wrapped in
    **3D Tiles 1.1** (`tileset.json` with implicit quadtree) or our own JSON manifest. Three.js `3DTilesRendererJS` and Babylon both
    stream 3D Tiles in Chrome.
- **Measured sizes** (VERIFIED, gltfpack 0.22, @gltf-transform/cli 4.1.1 via `npm i`, on 100 m × 100 m):

| Mesh | Raw GLB | Draco (gltf-transform default) | meshopt `gltfpack -c -vp 16` | meshopt + gzip-9 |
|---|---|---|---|---|
| B3 ground/collision, 82 k tris | 1.48 MB | **72 KB** | 265 KB | 177 KB |
| A 0.5 m grid, 80 k tris | 1.45 MB | 61 KB | 245 KB | — |
| B2, 223 k tris | 4.03 MB | 160 KB | 689 KB | — |
| roofer LoD2.2, whole 500 m tile | 0.70 MB | — | — | — |

A 1 km² ground layer at B3 density is about 8 M tris: 7 MB Draco, or about 18 MB meshopt+gzip. Collision only near the player is a
few hundred k tris in WASM memory, which is fine.

Choosing a codec:
- Draco decode costs CPU in a worker. Meshopt decodes about 10× faster, and is preferable for streaming collision tiles (UNVERIFIED
  general knowledge).
- Quantised positions: 14-bit Draco over 100 m is about 6 mm and 16-bit meshopt about 1.5 mm. Both are well below curb-height
  tolerances.

---

## 7. Reproduce

```
# remote, once
cd ~/turku3d/research/reconstruction/tools
curl -sSL -o mm.tar.bz2 https://micro.mamba.pm/api/micromamba/linux-64/latest && tar xjf mm.tar.bz2 bin/micromamba
MAMBA_ROOT_PREFIX=~/turku3d/mamba ./bin/micromamba create -y -p ~/turku3d/envs/recon -c conda-forge \
  python=3.11 pdal python-pdal laspy lazrs-python numpy scipy shapely pyproj trimesh scikit-image pillow rasterio gdal   # ~100 s
~/turku3d/envs/recon/bin/pip install triangle mapbox_earcut open3d matplotlib
export PROJ_DATA=~/turku3d/envs/recon/share/proj          # otherwise pdal prints pj_obj_create errors

# data
curl -sSL -o data/pistepilvi_23459500_6705000.laz https://turku.asiointi.fi/3d/pistepilvi/23459500_6705000.laz
for L in kanta:Liikennevayla kanta:Kevyenliikenteenvayla kanta:Rakenne GIS:Puut GIS:Rakennukset bldg:Building_LOD2; do
  curl -sS -A Mozilla/5.0 "https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx?service=WFS&version=1.0.0&request=GetFeature&typeName=$L&bbox=23459500,6704500,23460000,6705000" -o work/wfs_${L/:/_}.xml
done
python -I scripts/gml2geojson.py work/wfs_kanta_Rakenne.xml work/wfs_kanta_Rakenne.geojson   # etc.

# experiments
python -I scripts/ground_mesh.py data/pistepilvi_23459500_6705000.laz work 23459525 6704575 100 out/ground   # 129 s
python -I scripts/trees.py data/pistepilvi_23459500_6705000.laz work/wfs_GIS_Puut.geojson out/trees/trees.json   # 12 s
tools/roofer/bin/roofer -j 10 --srs EPSG:3877 --lod12 --lod22 data/pistepilvi_23459500_6705000.laz work/fp2.gpkg out/roofer   # 83 s
python -I scripts/cityjsonseq2glb.py out/roofer/23459500_6704500.city.jsonl out/roofer/bldg 23459500 6704500 2.2
tools/npm/node_modules/.bin/gltf-transform draco in.glb out.glb ; tools/npm/node_modules/.bin/gltfpack -i in.glb -o out.glb -c -vp 16

# local render (Blender 5.2.1)
blender -b -P tools/reconstruction/render_curb.py -- <glb> <png> 36.4 37.9
```

---

## 8. Not done or failed

- **Texture download** for the city CityGML: 404.
- **roofer Docker images**: the documented tag doesn't exist (`3dgi/geoflow-bundle` and `ghcr.io/3dbag/roofer` are denied). The release
  tarball works.
- **Not tried:** City3D, PolyFit and 3dfier, because roofer covered LoD2. CloudCompare CLI, because PDAL/Open3D covered the needs.
  Gaussian splatting, because there is no GPU on the server and no panos were fetched for this topic.
- **Lidar-only curb detection** (without vectors) and **adaptive base point selection** were not run.
- **City LoD2 vs roofer accuracy comparison** was not run.

---

## Verification (adversarial check, 2026-10-25)

An independent verifier tried to refute the claims that matter most for the architecture. It did not reuse the original scripts: it
wrote its own estimators, sampled stations at a different spacing and phase, and ran a physics test the original did not have.

Verifier scripts:
- `/Users/wnt/Documents/game-test/tools/reconstruction/verify/curb_verify.py`
- `/Users/wnt/Documents/game-test/tools/reconstruction/verify/roof_verify.py`
- `/Users/wnt/Documents/game-test/tools/reconstruction/verify/curbphys.mjs` and `v2.mjs` (Rapier)

Remote copies and decoded meshes are in `osgallery-dev:~/turku3d/research/reconstruction-verify/`. No new large downloads were made:
LAZ headers were read with HTTP Range requests of 375 bytes.

| # | Claim | Verdict | Independent check |
|---|---|---|---|
| 1 | LAZ at `turku.asiointi.fi/3d/pistepilvi/<tile>.laz`, ~63 pts/m², LAS 1.2 PDRF 3, no SRS | **CONFIRMED** (with nuance) | See note 1. |
| 2 | WFS has curb lines 62520, Rakenne porras/tukimuuri/aita, GIS:Puut, LoD2 | **CONFIRMED** | See note 2. |
| 3 | CityGML texture URLs 404 | **CONFIRMED** for every URL variant tried | See note 3. |
| 4 | 62520 lines are real curbs (median step 7.3 cm, 44 % ≥ 8 cm) | **CONFIRMED** | See note 4. |
| 5 | B3 breakline CDT gives sharp curbs (0.10 m, 61°) against a 0.5 m grid (0.60 m, 10°) | **CONFIRMED in direction, numbers softer** | See note 5. |
| 5b | The collision mesh actually changes physics behaviour (implied by the whole design) | **CONFIRMED** (new test) | See note 6. |
| 6 | roofer v1.0.0 works; "fit is poor, median RMSE 0.86 m" | **Tool CONFIRMED; the quality conclusion is REFUTED as stated** | See note 7. |
| 7 | Draco 72 KB / meshopt 265 KB (177 KB gzip) without hurting curbs | **CONFIRMED** | See note 8. |
| 8 | float32 at E≈2.35e7 has 2 m spacing | **CONFIRMED** | `np.spacing(np.float32(23459500))` gives 2.0, and 0.5 m for N. |
| 9 | Google Photorealistic 3D Tiles need a key; Turku coverage unknown | **UNCERTAIN** | Without a key, `tile.googleapis.com/v1/3dtiles/root.json` returns 403. Coverage remains unchecked. |

**1. LAZ tiles.** The opaskartta URL returns **301** (not 302) to turku.asiointi.fi. The verifier parsed the headers itself from
Range requests of 5 tiles:
- Density is 62.7 (the original tile), 73.3, 86.0, 79.7 and 69.5 pts/m², so **63–86 pts/m², below the advertised 100**.
- All tiles are LAS 1.2 PDRF 3, dated 2021 (day 295).
- The only VLR is the laszip one, so the SRS really is absent.

The server sends `accept-ranges: bytes` and `access-control-allow-origin: *`. A browser could therefore range-read the tiles directly,
but the game should still serve its own baked tiles. Note that 35 % of the points are class 12 (overlap).

**2. WFS layers.** The verifier fetched GetCapabilities again (281 layers) and ran a fresh GetFeature for the 100 m patch:
- `kanta:Liikennevayla` features have `kplkoodi 62520`, `geom_role reunaviiva` and `sijaintiepavarmuus 0.15`.
- `kanta:Rakenne` had 13 × `porras`, 3 × `tukimuurin yläreuna` and an `aita`.
- `GIS:Puut` had 44 features and `bldg:Building_LOD2` had 12.

**New caveat:** many vector features are dated **2022–2024** (`alkuPvm`), newer than the 2021 lidar. Some vector/lidar disagreement is
therefore expected, beyond the 0.15 m positional uncertainty.

**3. Textures.** For 3 `imageURI`s from a fresh fetch, all of these returned 404:
- the original URL
- the same path on turku.asiointi.fi
- `.png` instead of `.jpg`
- the path without `textures/`

`/3d/` and `/3d/citygml/` return 403. A web search found no alternative download. No other way to get the textures was found.

**4. Curb steps.** The verifier used its own estimator: the 30th percentile of class-2 points 0.25–1.0 m on each side, ±0.6 m along
the line, stations every 1.7 m. Over the whole tile (3 207 stations) the **median is 7.9 cm and 50 % are ≥ 8 cm**, against the
original's 7.3 cm / 44 %. Inside the 100 m patch the values are lower (5.5 cm, 24 % ≥ 8 cm), so the patch is not the curbiest part of
the tile.

**5. Curb sharpness.** The verifier ray-cast the original GLBs with Open3D at 1 cm steps across 27 independently chosen curb stations
(lidar step ≥ 8 cm):

| Mesh | Width (10–90 %) | Share ≤ 15 cm | Max slope |
|---|---|---|---|
| A, 0.5 m grid | 0.59 m | 0 % | 10.1° |
| A2, 0.25 m grid | 0.47 m | — | 18° |
| C0, 0.1 m grid | 0.48 m (claimed 0.36) | — | 37° |
| B, unsnapped CDT | 0.39 m | — | 62° |
| B3, snapped CDT | **0.17 m** (claimed 0.10) | 44 % (claimed 55 %) | 61.3° |

Against the verifier's own lidar step, the step error is 7 mm for B3 and 15 mm for A.

**Methodological caveat:** the original "step error 9 mm" and "width 0.10 m" are partly circular. The breakline heights were set from
the same lidar step that is then used as truth, and the 0.06 m spacing of the double breaklines nearly fixes the width by construction.
The independent sample still shows the qualitative result is real: the breakline CDT is the only mesh at grid budget with a steep curb
face.

61° is not a vertical curb. If true vertical faces are wanted, use the curb-solid extrusion from §2.2.

**6. Physics test in Rapier** (new; `@dimforge/rapier3d-compat` 0.21.0 in Node 24, the same WASM as in Chrome). The test loaded the
GLBs as trimesh colliders, which built in 30–75 ms for 82 k triangles. At 19 curb stations with an 8–25 cm step it ran two tests.

Test 1 is a character controller: a capsule of radius 0.3 m walking at 1 m/s, max climb 45°.

| Character test | 0.5 m grid (A) | B3 CDT |
|---|---|---|
| No autostep | 19/19 walk straight over: the curb is invisible | 8–9/19 cross: the curb is an obstacle |
| Autostep 0.3 m | 19/19 cross | 19/19 cross |

Test 2 is a ball of radius 0.33 m (wheel-sized) rolling at 5 m/s. Its median peak upward velocity is **0.66 m/s on A and 1.65 m/s on
B3**, so B3 kicks a wheel about 2.5× harder.

So the curb geometry does reach gameplay.

**Pitfall (VERIFIED):** Rapier 0.21 autostep fails (0/19) when the desired movement includes a downward gravity component of
−0.05 m/frame. Apply gravity separately, or keep the per-frame y small, with snap-to-ground.

**7. Roofer.** `gh api repos/3DBAG/roofer/releases` shows that v1.0.0 (2026-04-20) is the latest stable release. v1.1.0-beta.1
(2026-07-30) also exists, and **a macOS arm64 build ships too**, so roofer can also run on the M1. Recomputing from the CityJSONSeq
attributes gives 135/135 successes, a median `rf_rmse_lod22` of 0.861 m (n = 129) and 16 buildings ≤ 0.3 m, which matches the report.

The verifier then measured vertical residuals of 400 k class-6 lidar points against `bldg_lod2.2.glb` by ray casting:

| Model | Median abs residual | p75 | p90 | Points within 0.3 m |
|---|---|---|---|---|
| LoD2.2 | **3.4 cm** | 10 cm | 1.06 m | **86 %** |
| LoD2.2, roofer2 settings | 2.4 cm | — | — | 86 % |
| LoD1.2 | 1.67 m | — | — | 22 % |

18 % of class-6 points fall outside every footprint (overhangs or footprint mismatch). `rf_rmse` is therefore dominated by a minority
of outliers (dormers, chimneys, wall and eave points, misfit parts). Roofer LoD2.2 is **good for most of the roof area**, not "poor".
The per-building choice against the city LoD2 is still sensible, but judge it by robust metrics (median or share within 0.3 m), not
RMSE.

**8. Compression.** `gltf-transform inspect` shows Draco and meshopt both keep 81 836 triangles (41 587/41 588 vertices). gzip-9 of
the meshopt file is 177 298 bytes. The verifier decoded both files back to plain glTF with the gltf-transform 4.1.1 API and re-ran
the curb profiler:
- **meshopt:** identical to raw (0.17 m width, 61.3°).
- **Draco default:** 0.21 m width, 61.0°, step error 8 mm.

Neither codec destroys the curbs. meshopt is slightly more faithful.

**Corrections to the TL;DR:**
- Density is 63–86 pts/m² depending on the tile (not a flat 63).
- The B3 curb width is about 0.17 m on an independent sample, not 0.10 m.
- Roofer LoD2.2 fits 86 % of roof points within 0.3 m (median 3.4 cm). Its "median RMSE 0.86 m" reflects outliers, not general poor
  fit.
- The opaskartta redirect is a 301.
- The vectors are newer (2022–24) than the lidar.
