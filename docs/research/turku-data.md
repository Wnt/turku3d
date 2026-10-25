# Turku geodata inventory for the browser game ("turku-data")

Date: 2026-10-25. Test area: Turku city centre, lidar tile `23459500_6705000`
(EPSG:3877 x 23459500–23460000, y 6704500–6705000: Kauppatori, Aurakatu, Eerikinkatu,
Brahenkatu, Yliopistonkatu).

Each claim is tagged **[VERIFIED]** (I ran it today; the command or script is named) or
**[UNVERIFIED]** (taken from docs, the web or the earlier project's notes, and not re-run).

Data on the remote host: `osgallery-dev:~/turku3d/research/turku-data/` (183 MB).
Scripts: `/Users/wnt/Documents/game-test/tools/research/*.py` (copies in `~/turku3d/research/`).
Python venv: `~/turku3d/.venv` (laspy 2.7 + lazrs, numpy, scipy, pyproj, pillow, shapely).
Images: `docs/research/img/`.

---

## 0. Bottom line for the game

| Need | Best source | Why |
|---|---|---|
| Collision ground: curbs, slopes, ramps | **City lidar 2021** (class 2 + near-ground class 12) for heights, **kantakartta vector lines** for exact curb, stair and wall positions | Lidar heights match surveyed spot heights to ~2 cm. Curb steps of 6–20 cm show up in the lidar, but the curb face is smeared over 20–40 cm, so the map line gives the edge position. |
| Buildings (collision and base mesh) | **City CityGML LOD2/LOD3 via WFS** | Roofs match lidar (median +0.8 cm). Watertight solids with attributes. Only roof textures (from orthos); no facades. |
| Visual "photoreal" look | **Google photorealistic mesh** (the same data as Photorealistic 3D Tiles); exists for Turku centre | I extracted and rendered a level-20 mesh of Kauppatori. Captured around 2022 during the market-square renovation (construction site). |
| Ground textures | **2022 True ortho (5 cm)** and **2025 ortho (5 cm)**; **drone mosaic (~1–1.5 cm)** where it covers | The true ortho has no building lean, which makes it the best road and roof texture. The drone layer shows individual paving stones but covers only patches. |
| Street furniture and trees | WFS `GIS:Varusteet`, `GIS:Puut`, `kanta:Lehtipuu`, `GIS:Liikennemerkit`; OSM for lamps | Point registers with species, models and sign codes |
| Road semantics and surfaces | WFS `GIS:Katualueet` (polygons with `Katuosalaji`, `Pintamateriaali`), `GIS:Keskilinjat` | Carriageway, sidewalk, median and stair areas, plus surface material (noppakivi, graniitti, asfaltti…) |

The main caveat is that the lidar (April 2021) and the Google mesh (~2022) both predate the finished
Kauppatori (renovation 2020–22, roughly). The kantakartta, the 2025 ortho and the 2026 tiles are current.
For the office block, check that block's own construction history.

---

## 1. What the earlier project (`~/leikkipuistodata`) used

| Item | Endpoint / method | CRS | Resolution | Date | Status |
|---|---|---|---|---|---|
| Lidar tile index | `~/leikkipuistodata/laz-tiles.csv`: 2,564 rows = 1,282 `pistepilvi` (full cloud) + 1,282 `maanpinta` (ground key points) tiles, 500 × 500 m. The tile name is `<xmin>_<ymax>`. | EPSG:3877 + N2000 heights (assumed) | see §1.1 | flown 2021-04-17 (GPS time), processed 2021-10-22 | VERIFIED |
| LAZ download | `https://opaskartta.turku.fi/3d/pistepilvi/<tile>.laz` → **301 redirect** → `https://turku.asiointi.fi/3d/pistepilvi/<tile>.laz` (use `curl -L`). CORS `*`. The same scheme serves `/3d/maanpinta/<tile>.laz`. | | | | VERIFIED (`curl -sL`, 96 MB in 1.9 s) |
| Potree octree, all of Turku | `https://turku.asiointi.fi/3d/3d-pistepilvi/cloud.js` (Potree 1.7, **24.9 G points**, attributes POSITION/COLOR/CLASSIFICATION; nodes under `data/`). Used by `potree-direct.mjs`. Streamable straight into a browser. | 3877 | full density | 2021 | VERIFIED (`cloud.js` fetched) |
| WMS orthos | `https://turku.asiointi.fi/TeklaOGCWeb/WMS.ashx?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=<name>&SRS=EPSG:3877&BBOX=..&WIDTH=..&HEIGHT=..&FORMAT=image/png`. Max 4096 px per side, PNG only. Errors come back as HTTP 200 XML. Direct curl works; no browser needed. | 3877 | see §2.3 | | VERIFIED |
| Drone mosaic | WMS layer `Turun lennokkikuvaukset`: flat, undated mosaic of flights from different dates (some in winter) | 3877 | ~1–1.5 cm where present | mixed | VERIFIED (resolution); coverage is patchy (1 of 5 probe points around the centre) |
| 2026 ortho | Portal tile endpoint `https://turku.asiointi.fi/IMS/Tile/Layer?layer=Turku%20Kaarina%20ilmakuva%202026&x=<col>&y=<row>&z=<z>` (Locus grid: identity projection on 3877, 256 px tiles, res = 2^(17-z) m, z20 = 12.5 cm is the maximum). Script: `~/leikkipuistodata/tiles2026.py --bbox xmin,ymin,xmax,ymax --out f.png` | 3877 | 12.5 cm | 2026 | VERIFIED (480 × 480 px Kauppatori crop) |
| Kaarina | WMS `Kaarina Paimio Lieto ilmakuva 2022` (sharp); `Ilmakuva 2023` (regional, softer) | 3877 | — | 2022/2023 | UNVERIFIED today (from HANDOVER) |
| WFS client | `~/leikkipuistodata/wfs.py`: `https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx`, WFS 1.0.0 with an OGC `<Filter><BBOX>` | 3877 | | | VERIFIED |
| Obliques | none recent (city 1998/2003, Bing 2008) | | | | UNVERIFIED (HANDOVER) |

### 1.1 The centre LAZ tile, measured [VERIFIED: `tools/research/laz_inspect.py`]

`~/turku3d/research/turku-data/pistepilvi_23459500_6705000.laz` is 96.3 MB with 15.68 M points.

- LAS 1.2, point format 3 (XYZ, intensity, returns, class, scan angle, point source, **GPS time, RGB**). No NIR. **There is no CRS VLR**, so assume EPSG:3877 and N2000 heights. Scale is 1 mm. Software: TerraScan, 2021-10-22.
- **Flight date: 2021-04-17, 09:25–09:57 UTC** (adjusted GPS time). That is leaf-off, before Kauppatori was finished. Four flight lines (point_source 80–83).
- Density over the 0.25 km² tile:
  - All points: **62.7 pts/m²**.
  - First returns: 55.4 pts/m² (median per 1 m² cell 59, p10 33, p90 75; no empty cells).
  - The ~100 pts/m² figure quoted for this dataset holds only where flight lines overlap.
- Class histogram:

| class | meaning (TerraScan / city) | % of points | pts/m² |
|---|---|---|---|
| 1 | unclassified (play equipment, poles, cars…) | 1.0 | 0.7 |
| 2 | ground | 25.5 | 16.0 |
| 3 / 4 / 5 | vegetation by height band (low/med/high; play structures land in 5) | 0.5 / 1.1 / 5.3 | 0.3 / 0.7 / 3.4 |
| 6 | building | 30.8 | 19.3 |
| 7 | low noise | 0.1 | |
| 8 | model key points | 0.1 | |
| 9 | water (the Aura river) | 0.03 | |
| **12** | **overlap**: all surface types, including ground and roofs | **35.5** | 22.2 |

  Class 12 holds about 1/3 of the points, and about 2.0 M of its 5.6 M points are ground. To get
  dense ground, add class 12 points that lie within 8 cm of the local class-2 median. This raises
  ground from 16 to ~24 pts/m².
- Returns go up to 7 per pulse (1: 13.85 M, 2: 1.17 M, 3: 0.43 M…). Intensity is 16-bit (5089–65535). RGB is 8-bit scaled to 16-bit; colours come from the 2021 imagery.
- `maanpinta_<tile>.laz` (0.48 MB) holds 39,631 class-8 "key points" (0.16 pts/m²), the thinned DTM set. It matches class 2 with median 0.0 cm and MAD 0.6 cm.

### 1.2 Vertical accuracy [VERIFIED: `tools/research/z_check.py`]

- The 105 kantakartta surveyed spot heights (`kanta:Korkeuspiste`) in the tile, compared with the lidar class-2 median within 0.3 m: **median −1.6 cm, MAD 1.6 cm**. 80 % are within 5 cm and 92 % within 10 cm.
- Ground noise against a local plane fit is **1.1 cm** (median residual over 2,055 curb-side profiles).

### 1.3 Are curb steps visible? Yes [VERIFIED: `curb_profile.py`, `street_section.py`, `ground_hillshade.py`]

Method:
- Take the kantakartta line class **62520** (carriageway edge = curb line; my decoding, see §2.1).
- Every 2 m along it, cut a perpendicular strip ±3 m long and ±0.25 m wide of ground points (class 2 plus near-ground class 12).
- Fit a line to each side over 0.2–1.5 m and take the height difference at the line.

Results:
- **Whole tile:** 2,055 profiles. |step| median **7.5 cm** (p25 4.0, p75 10.6); 54 % of profiles are ≥ 7 cm.
  - Histogram of |step|, 5 cm bins: 0–5 cm: 621, 5–10: 825, 10–15: 528, 15–20: 65, ≥ 20: 16.
  - The 4–5 m drops are the Aura river quay edges, which also carry code 62520. I excluded them with |step| < 0.5 m.
- **Brahenkatu × Yliopistonkatu crossing** (non-construction area): 188 profiles, median **8.5 cm** (p25 5.7, p75 10.5), noise 1.3 cm. Example profiles (z in m every 0.1 m across the curb):
  ```
  23459741.67,6704752.76  step +6.6 cm:  -0.5:8.533 -0.3:8.518 -0.1:8.512 +0.0:8.540 +0.3:8.586 +0.4:8.594 +0.6:8.598
  23459768.46,6704788.49  step +17.9 cm: -0.5:9.608 -0.3:9.608 +0.2:9.837 +0.3:9.794 +0.5:9.796
  ```
- **What the data cannot give:** the curb face is not sharp. Point spacing is about 13 cm and there are only ~1.2–2.4 ground points per 10–15 cm cell, so the step is smeared over 0.2–0.4 m. Parked cars also leave gaps along kerbs; many street sections have no ground near the kerb.
- **Recommendation:** build curbs procedurally. Take the X/Y position from kantakartta 62520 / 62522 lines (stated position accuracy 0.15 m) and the curb height from lidar, measured per segment as a robust median across ±1.5 m. Snap ground TIN vertices onto the curb line on both sides.
- Image: `img/slope-brahe2.png`. This is a 15 cm ground-slope raster with 62520 (red) and 62522/3 (blue) lines overlaid; dark curb lines run along the red lines.
- **Stairs:** the tile contains 168 `porras` (stair outline, 66626) and 206 step-edge lines (66618). Lidar under trees does not reliably resolve individual 10–15 cm risers; it gives only the overall drop, e.g. 20.2 → 17.2 m over 9 m at 23459511, 6704850. Build stairs procedurally from the outline, the step lines and the lidar top and bottom heights.

---

## 2. Other open data

### 2.1 Turku WFS (`https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx`; `opaskartta.turku.fi` serves identical capabilities)

[VERIFIED: GetCapabilities lists 280 feature types; `tools/research/wfs_probe.py` fetched the layers below]

Access notes:
- WFS 1.0.0 with a `filter=<Filter><BBOX>…` parameter, or WFS 1.1.0 with `bbox=` for the CityGML types.
- **GML only**: `outputFormat=application/json` returns an exception.
- Responses are fast (0.1–2 s for a 0.25 km² tile).
- Two kanta layers return **401** (`kanta:LuokittelematonViiva`, `kanta:Sahkoverkonosa`; the second is the electricity network, probably including lamp posts).

Kantakartta (base map) layers, with the class codes (`kplkoodi`) I decoded from the tile:

| Layer | Content in the centre tile | Use for the game |
|---|---|---|
| `kanta:Liikennevayla` | **62520** lines = carriageway edge / curb line (follows the kerb in lidar; also quay edges); 62020 points (surface "kestopäällystetty") | **curb lines** |
| `kanta:Kevyenliikenteenvayla` | 62522 / 62523 lines = sidewalk / cycleway edges (190 in the tile) | sidewalk edges |
| `kanta:Rakenne` | 66626 `porras` stair outlines (168); **66618** step edges (206); 66634 `tukimuurin yläreuna` = retaining-wall top (46); 66630/66632/66636 `aita` fences (223, with material); 62540 `luiska` ramp/slope (47); 62538 inner-slope top; 66620/66622/66624 (unlabelled structure lines); 66114 gate posts; 64500 pools; 66110 monuments; 66152 floodlight masts; 66122 fountain | **stairs, retaining walls, fences, ramps** |
| `kanta:Rakennus` | Footprints 66000 (with `rakennustunnus`, `kerrosluku`, `julkisivumateriaali`, `kottovuosi`), 66001 (parts), 60020 points, plus ~230 unlabelled detail lines (eaves, canopies) | footprints + storey counts |
| `kanta:Korkeuspiste` | Surveyed spot heights (110 in the tile) | height checks |
| `kanta:Lehtipuu` / `kanta:Havupuu` / `GIS:Puut` | Trees. `GIS:Puut` gives the Latin species name and planting year (e.g. *Prunus sargentii* 'Rancho', planted 2022). | tree placement and species |
| `kanta:Korkeuskayra`, `kanta:Jyrkanne`, `kanta:Kivi`, `kanta:Vesikohde`, `kanta:Raide` | contours, cliffs, boulders, water, rails | terrain dressing |
| `GIS:Katualueet` | Street-area **polygons** with `Katuosalaji` (Ajorata, Jalkakäytävä, Välikaista, **Portaat**, Muu…), `Pintamateriaali` (Noppakivi, Luonnonkivi, Betonilaatta, Asfalttibetoni, Nupukivi, Graniitti), renovation year | **road/sidewalk areas and surface materials**; drives material choice |
| `GIS:Keskilinjat` | Centrelines per street part (Ajorata / Jalkakäytävä), with class, surface and maintenance attributes | road graph for AI traffic |
| `GIS:Varusteet` | Furniture points: benches, bins, ashtrays, leaning rails, tables, with manufacturer and model (e.g. "Lehtovuori CitySolar 240 l") | props |
| `GIS:Liikennemerkit` | Traffic signs with Finnish sign codes (E1, C37…) and text | signs |
| `GIS:Raitiotielinjaus`, `GIS:Follariasemat`, `GIS:Pysakointiautomaatit`, `GIS:Nopeusrajoitusalueet` … | misc | gameplay |

CityGML 2.0 layers (same endpoint; WFS 1.1.0 `bbox=`):

| Layer | Result | Notes |
|---|---|---|
| `bldg:Building_LOD1/2/3` | 136 buildings in the tile; LOD2 file is 19 MB. Solids with Roof/Wall surfaces, `measuredHeight`, `storeysAboveGround`, address and register attributes. LOD3 has finer roofs (813 vs 524 roof surfaces in the test bbox) but no facade detail. | **Roofs vs lidar class 6: median +0.8 cm, MAD 9.6 cm**, 69 % within 0.3 m [VERIFIED `lod2_check.py`]. Texture URIs point to `https://opaskartta.turku.fi/3d/citygml/textures/*.jpg`: **only ~10 % resolve (6 of 60 sampled; 1 of 40 in another test)**, and the ones that load are top-down ortho roof crops. So in practice the models have no facade textures. |
| `tran:Road_LOD2/3` | Draped TIN per street part (344 features, 28.7 k triangles in the tile), attributes such as `katuosalaji`. Median edge 2.5 m. | Vertices vs lidar: median 0.0 cm, MAD 5 cm, p95 28 cm. Too coarse to represent curbs as faces. Use it for semantics, not collision. |
| `veg:PlantCover_LOD2` | 51 TIN park areas | |
| `frn:CityFurniture_LOD1-3` | 96 **points** only (the same register as Varusteet), no geometry | |
| `dem:ReliefFeature_*`, `luse:*`, `veg:SolitaryVegetationObject_*` | **0 features** in the centre bbox | No DEM over WFS, so build the DEM from the lidar |

There is also a file-based city model and CityGML download pointer at
`https://www.turku.fi/kolmiulotteinen%20kaupunkimalli` (from avoindata.fi). [UNVERIFIED]

`https://turku.asiointi.fi/3d/` and `/3d/citygml/` return 403 (no directory listing). Guesses
at `/3d/mesh/`, `/3d/3dtiles/` and `/3d/tileset.json` return 404, so I found no city reality
mesh or 3D Tiles. [VERIFIED]

### 2.2 OSM [VERIFIED: Overpass `nwr(60.45057,22.26415,60.45511,22.27314)` on `overpass.private.coffee`; overpass-api.de returned 406]

The tile has 7,371 elements, including:
- 59 `barrier=kerb` nodes (37 `kerb=lowered`, 14 `kerb=flush`), mostly at crossings
- 33 `highway=steps` (17 with `step_count`), 18 `barrier=retaining_wall`, 42 fences
- 167 `natural=tree`, 22 `highway=street_lamp`, 75 crossings
- 234 buildings with `building:levels`, 41 + 37 + 24 with `roof:shape`, 28 with `height`, 71 `building:part`
- surfaces: 247 paving_stones, 23 sett

OSM is useful for lamp posts (the city layer is 401), crossings and opening hours.
For geometry it is coarser than the kantakartta.

### 2.3 Ortho layers on the city WMS [VERIFIED: `tools/research/wms_res.py` at 1 cm/px over 25 × 25 m at Kauppatori; run-length of identical pixels gives the native pixel size]

| Layer | Native pixel | Notes |
|---|---|---|
| `Turun lennokkikuvaukset` (drone) | **~1.2 cm** | Paving stones are visible. Coverage is patchy and dates are mixed. |
| `Ilmakuva 2025` | 5.2 cm | |
| `Ilmakuva 2022 True ortho` | 5.5 cm | **True ortho: no building lean**, which makes it the best roof and ground texture |
| `Ilmakuva 2021` | 5.4 cm | matches the lidar epoch (RGB in the LAZ) |
| `Turun kartoituskuvaukset` | 5.6 cm | survey flights, date unknown |
| `Ilmakuva 2018 True ortho` | 7.6 cm | |
| `Ilmakuva 2023` (Turunseutu) | ~16 cm | regional |
| 2026 tiles (portal) | 12.5 cm | `tiles2026.py` |

Older historical orthos go back to 1939, plus old address maps (useful only as references).
Contact sheet: `img/sheet.jpg`.

### 2.4 NLS (Maanmittauslaitos)

- `avoin-paikkatieto.maanmittauslaitos.fi` (OGC API Features / file service), `avoin-karttakuva…/wcs/v2` and `kartta.paikkatietoikkuna.fi` all **timed out** today (TCP connect timeout on port 443) from both the remote host and the Mac. `www.maanmittauslaitos.fi` answered 200. [VERIFIED: unreachable on 2026-10-25]
- From the docs [UNVERIFIED]:
  - Open laser data is 0.5 pts/m² (2008–2019, plus a thinned 2020– product).
  - The 5 pts/m² data is a paid order.
  - Open 2 m DEM, Maastotietokanta buildings and roads; CC BY 4.0.
  - Access needs a free API key (HTTP Basic, key as username).
- For Turku all of this is **inferior to the city's data** (62 vs 5 pts/m²; 5 cm vs 2 m). It matters only for the regional backdrop: horizon terrain outside the city lidar and the archipelago.

---

## 3. Commercial photogrammetry meshes

### 3.1 Google: Photorealistic 3D Tiles / Google Earth mesh, Turku centre **is covered** [VERIFIED]

- **Google Earth Web**, opened at Kauppatori in Chrome: the status bar showed imagery date **03/07/2022**. The WebGL canvas did not appear in automation screenshots, so I verified the mesh through the data instead.
- **Rocktree data:**
  - I cloned `https://github.com/retroplasma/earth-reverse-engineering` (exporter, Node) to `~/turku3d/research/turku-data/google/ere/`.
  - `node lat_long_to_octant.js 60.4512 22.2690` finds octants down to level 18 (`306061717372725073`).
  - `node dump_obj.js 30606171737272507 20` exported **143 octants at level 20**: 527 k vertices, 310 k faces, 197 texture atlases (256–512 px), 66 MB.
- **Blender render** (`~/turku3d/research/w0-gate/docs/research/img/gmesh_render.png`, script `scratchpad/render_obj.py`) shows a real textured photogrammetry mesh with facades, trees and parked cars. **Kauppatori is a construction site in it**, which fits ~2022 capture. The atlases (`img/gtex.jpg`) contain oblique facade fragments, as expected for photogrammetry.
- **Official API:** Photorealistic 3D Tiles (`https://tile.googleapis.com/v1/3dtiles/root.json?key=…`) serves the same Google Earth mesh in glTF/3D Tiles. It needs a Map Tiles API key with billing. [UNVERIFIED: no key available; the coverage list did not name Finland in the search results]
- **Browser fit:** this is the only ready-made photoreal mesh for Turku. Streaming it at runtime works with `3d-tiles-renderer` (NASA-AMMOS) in three.js, or with CesiumJS. [UNVERIFIED in this run]
- **Limits:**
  - Geometry is blobby. Kerbs and steps are not modelled, so it is unusable as collision.
  - It shows the ~2022 state.
  - Google's terms forbid offline extraction/caching (rocktree dumping is a ToS grey zone). The user accepts this for private use, but the API route needs attribution and a key.

### 3.2 Cesium ion [UNVERIFIED]

Ion offers Cesium OSM Buildings (extruded from OSM, not photoreal), Cesium World Terrain and
Google Photorealistic 3D Tiles as an ion asset (the same Google data, needs an ion token).
There is no independent Turku photogrammetry on ion that I know of.

### 3.3 Apple Maps 3D [VERIFIED: apple.com/ios/feature-availability, sections `maps-flyover` / `maps-3d-city` / `maps-look-around`]

- **Flyover (photoreal 3D): Helsinki is listed, Turku is not.**
- "3D city / globe" is listed only per country (Finland included).
- Look Around covers Finland, but it is street-level imagery with no mesh API.

### 3.4 Third party [UNVERIFIED]

3dcadbrowser lists a paid "Turku City, Finland 3D Model (2023)": about 27 km², 276 OBJ tiles with
4096² textures, in UTM 34. Its provenance is unknown; it may be derived from Google or Bing.

---

## 4. Summary table

| Source | What | Resolution / density | Date | Access method | Verified? |
|---|---|---|---|---|---|
| Turku lidar `pistepilvi` LAZ | Classified point cloud with RGB, intensity, returns, GPS time | 62.7 pts/m² all; ground ~16 (class 2) to ~24 (with near-ground class 12) pts/m²; z ±2 cm | 2021-04-17 | `https://turku.asiointi.fi/3d/pistepilvi/<xmin>_<ymax>.laz` (opaskartta URL redirects); index in `laz-tiles.csv`; 500 m tiles, ~96 MB | VERIFIED |
| Turku lidar `maanpinta` LAZ | DTM key points (class 8) | 0.16 pts/m² | 2021 | `/3d/maanpinta/<tile>.laz` | VERIFIED |
| Turku Potree octree | Whole-city cloud, 24.9 G points | full | 2021 | `https://turku.asiointi.fi/3d/3d-pistepilvi/cloud.js` | VERIFIED (metadata) |
| Kantakartta WFS (`kanta:*`) | Curb lines 62520, sidewalk edges 62522/3, stairs 66626/66618, retaining walls 66634, fences, ramps 62540, footprints, spot heights, trees | survey-grade, `sijaintiepavarmuus` 0.15 m | continuously updated (edits 1992–2025) | WFS 1.0.0 GML, BBOX filter | VERIFIED |
| Street registers (`GIS:Katualueet`, `Keskilinjat`, `Varusteet`, `Puut`, `Liikennemerkit`) | Street-part polygons with surface material; furniture, trees, signs | survey | current | WFS GML | VERIFIED |
| CityGML LOD1–3 buildings | Solid buildings with attributes; roof textures mostly 404 | roofs ±10 cm vs lidar | current (includes 2022 buildings) | WFS 1.1.0 `bldg:Building_LOD2/3` | VERIFIED |
| CityGML roads / parks | Draped TINs with attributes | ~2.5 m edges, ±5 cm | current | WFS `tran:Road_LOD3`, `veg:PlantCover_LOD2` | VERIFIED |
| CityGML relief / landuse / city furniture geometry | — | empty / points only | — | WFS | VERIFIED (empty) |
| Ortho 2025 / 2021 / 2022 true ortho / 2018 true ortho | RGB orthophotos | 5.2 / 5.4 / 5.5 / 7.6 cm | 2025, 2021, 2022, 2018 | WMS `TeklaOGCWeb/WMS.ashx`, EPSG:3877, ≤ 4096 px | VERIFIED |
| Drone mosaic | RGB | ~1.2 cm | mixed (some winter) | WMS `Turun lennokkikuvaukset` | VERIFIED (patchy coverage) |
| 2026 ortho | RGB | 12.5 cm | 2026 | IMS tile endpoint, `tiles2026.py` | VERIFIED |
| OSM | Kerbs (59 nodes), steps (33), lamps (22), trees, building levels and roof shapes | crowd-sourced | current | Overpass (`overpass.private.coffee` worked) | VERIFIED |
| Google photorealistic mesh (Earth / 3D Tiles) | Textured photogrammetry mesh | level 20 (~decimetre geometry); 256–512 px atlases | ~2022 (03/07/2022 shown) | Official: Map Tiles API key. Tested: rocktree via earth-reverse-engineering | VERIFIED (mesh exists and renders); API UNVERIFIED |
| Apple Flyover | Photoreal 3D | — | — | Apple Maps only | VERIFIED: Turku not covered (Helsinki is) |
| Cesium ion | OSM Buildings, World Terrain, Google 3D Tiles proxy | — | — | ion token | UNVERIFIED |
| NLS lidar / DEM / Maastotietokanta | 0.5 p open (5 p paid) lidar, 2 m DEM, buildings | coarse | 2008– | API key; endpoints timed out today | UNVERIFIED (unreachable) |
| Third-party "Turku 3D City 2023" OBJ | Paid textured mesh | 4096² per tile | 2023 | 3dcadbrowser | UNVERIFIED |

---

## 5. Implications for browser delivery

- Raw LAZ is too heavy to ship: 96 MB per 0.25 km² tile, 1,282 tiles. Preprocess offline into:
  - a ground heightfield / TIN at 0.25 m with curbs snapped to the vector lines;
  - collision meshes per 64–128 m cell, shipped as meshopt/Draco glTF or Rapier heightfield + trimesh binaries;
  - CityGML buildings converted to glTF.
- The Potree octree is a ready web LOD for "point cloud mode" or a distant backdrop, and it already has CORS `*`.
- WMS orthos at 5 cm come to ~400 MB of raw RGB per km². Cut them into KTX2 (UASTC for near tiles, ETC1S for far) per 64 m cell. Use the drone layer only for the hero block.
- Google 3D Tiles can stream at runtime as a visual layer only, if the user accepts a key and billing. Never use it as collision.

## 6. Files produced

- Remote host:
  - `~/turku3d/research/turku-data/`: `pistepilvi_*.laz`, `maanpinta_*.laz`
  - `wfs/`, `wfs-tile/` (kanta + GIS GML for the tile)
  - `citygml/`, `citygml-tile/` (LOD1–3, roads, parks, textures sample)
  - `wms/` (ortho samples), `ortho2026-kauppatori.png`, `osm-tile.json`
  - `google/` (exporter + level-20 OBJ)
  - `wfs-caps-*.xml`, `wms-caps.xml`, `wms-layers.txt`
- Local:
  - `tools/research/{laz_inspect,wfs_probe,wms_res,curb_profile,street_section,ground_hillshade,z_check,road_tin,lod2_check,stair_check}.py`
  - `docs/research/img/{gmesh_render.png,gtex.jpg,slope-brahe2.png,sheet.jpg}`

---

## Verification

An adversarial verifier re-checked this report on 2026-10-25 with independent probes. Scripts and outputs
are in `osgallery-dev:~/turku3d/research/turku-data-verify/` (`v_density.py`, `v_curb.py`, `v_z.py`, `wms/`).
Nothing large was re-downloaded; the existing centre LAZ was reused. Range GETs and HEAD requests were used
for other tiles.

| # | Claim | Verdict | How it was checked |
|---|---|---|---|
| 1 | LAZ download: `opaskartta.turku.fi/3d/pistepilvi/<xmin>_<ymax>.laz` 301-redirects to `turku.asiointi.fi`, CORS `*`, **"~96 MB each"** | **URL/CORS CONFIRMED; tile size REFUTED** | `curl -sIL -H Origin:` on a different tile (23460000_6705000): 301, then 200 with `access-control-allow-origin: *`, and HTTP Range works (206). That neighbour tile is **151.8 MB / 21.5 M points** (read from a 375-byte range GET of the header). HEAD on 40 random tiles from `laz-tiles.csv` gave a **median of 138 MB, a mean of 132 MB and a max of 305 MB** (min 4 KB, an edge tile). The full city is therefore about **170 GB**, not about 123 GB. The centre tile (96 MB) is below typical size. |
| 2 | Centre tile is LAS 1.2 point format 3, has no CRS VLR, 62.7 pts/m², 55.4 first-return pts/m², class 12 = 35.5 %, flown 2021-04-17 09:25–09:57 UTC | **CONFIRMED** | Fresh `laspy` script (`v_density.py`): identical numbers. `vlrs=[]`, `parse_crs()=None`, max 7 returns, intensity 5089–65535. Extra finding: in 1 m cells, class 2 alone has a median of only 8 pts/m² and **46 % of the 1 m cells have no class-2 point** (buildings take about 31 %; the rest is gaps under cars and class-12 ground). Merging class 12 ground is therefore essential, not optional. |
| 3 | Curb steps are detectable in lidar ground: median ~7.5 cm across 62520 lines | **CONFIRMED (with control)** | Own method on class 2 only: a line fit on each side over 0.2–1.5 m in a 0.6 m-wide strip every 2 m. On the 62520 lines: n=2,772, **median 7.3 cm**, p75 10.5, 52 % ≥ 7 cm. **Control** with the same lines shifted ±3 m and +6 m: median **0.7–1.0 cm**, only 2–9 % ≥ 7 cm. The step is a real signal, not slope or noise. Warning: a naive "median of each side" method without a slope fit gives 4–5 cm on the control lines (pavement crossfall), so the slope-fit step is required in the production tool. |
| 4 | Lidar vs "surveyed" kantakartta spot heights: median −1.6 cm, MAD 1.6 cm (n=105) | **Numbers CONFIRMED; meaning REFUTED as an independent accuracy check** | `v_z.py` reproduces n=105, lidar−spot +1.6 cm, MAD 1.6 cm. However, **all 110 `Korkeuspiste` features have `luontitapa=digitointi`** (digitized, not field-surveyed). **67 were created in 2021-09**, right after the April 2021 flight, and 95 % were created in 2021 or later. 71 of 105 carry 9+ decimals (computed values). These spot heights were very likely digitized from this same lidar/DTM or 2021 stereo, so the 2 cm agreement is largely circular. The true absolute accuracy is UNCERTAIN; a typical spec for such ALS is 5–15 cm (UNVERIFIED). The same applies to curb lines: all 93 `Liikennevayla` features are `digitointi` with a 0.15 m stated accuracy, not field survey. |
| 5 | WFS serves GML only (no GeoJSON); `kanta:Sahkoverkonosa` and `kanta:LuokittelematonViiva` return 401; `GIS:Katualueet` has `Katuosalaji` and `Pintamateriaali` | **CONFIRMED** | `outputFormat` = `application/json`, `application/geo+json`, `json` and `geojson` all return an `ExceptionReport`. WFS 2.0.0 is "Unsupported WFS version". Both kanta layers return 401 (1293-byte HTML). `GIS:Katualueet` over a 100 m bbox: 20 polygons, 23 attributes including `Katuosalaji` (Ajorata, Jalkakäytävä, Välikaista, Portaat…) and `Pintamateriaali` (Noppakivi, Nupukivi, Luonnonkivi, Betonilaatta). |
| 6 | CityGML texture URIs: only ~10 % resolve | **CONFIRMED (even worse)** | 80 random unique URIs from the tile's 4,699 `imageURI`s, following the 301 redirect: **4 returned 200 and 76 returned 404 (5 %)**. Note that without `-L` every URI returns 301, so a naive check would wrongly count them all as "found". |
| 7 | Potree 1.7 octree of the whole city (24.9 G points) at `/3d/3d-pistepilvi/cloud.js`, browser-streamable | **CONFIRMED** | `cloud.js`: version 1.7, `points: 24906954435`, attributes POSITION_CARTESIAN / COLOR_PACKED / CLASSIFICATION (no intensity or returns), `hierarchyStepSize` 5. Node files `data/r/r.hrc`, `r.bin` and `r0.bin` return 200, and CORS is `*`. `projection` is empty (3877 is implicit). |
| 8 | WMS native resolution ~5 cm (2025 / 2022 TO / 2021) and drone ~1.2 cm | **CONFIRMED** | A different estimator: 1024 px at 1 cm/px over 10 m at 23459700,6704700, with native size ≈ 1/(1−P(neighbour pixel equal)). Results: 2025 5.1 cm, 2022 True ortho 5.2 cm, 2021 5.3 cm, drone 1.2–1.3 cm (with imagery at this point). WMS also sends CORS `*`. |
| 9 | Google Photorealistic 3D Tiles needs a key; the Google mesh exists at Kauppatori | **Key CONFIRMED; mesh location CONFIRMED** | `tile.googleapis.com/v1/3dtiles/root.json` with no key returns 403 PERMISSION_DENIED, and with a bogus key returns 400 API_KEY_INVALID (the service exists). The exported OBJ (`.../obj/30606171737272507-20-1016/model.obj`, 527,279 `v` lines) has vertex radius 6,371,015 m (the rocktree sphere), which maps to **60.4496 N, 22.2641 E**, i.e. the Kauppatori / Aurakatu area. Note: these are NOT WGS84 ECEF. pyproj EPSG:4978 gives a bogus 9 km height, so any pipeline must use the sphere convention. That the API serves the same Turku mesh with a valid key is still UNCERTAIN. |
| 10 | Apple Flyover: Helsinki listed, Turku not | **CONFIRMED** | Parsed the whole `maps-flyover` section of apple.com/ios/feature-availability: "Helsinki, Finland" and "Turin" are present, "Turku" is absent. Turku appears only under Wallet/Maps transit. |
| 11 | NLS endpoints unreachable | **CONFIRMED (today)** | From the Mac, DNS resolves (195.156.69.x) but TCP 443 times out for `avoin-paikkatieto`, `avoin-karttakuva` and `kartta.paikkatietoikkuna.fi`. The NLS data-resolution claims remain UNVERIFIED. |

### Corrections to the report

- **Tile size and storage budget:** typical `pistepilvi` tiles are 130–150 MB (max ~305 MB), not ~96 MB. The whole city is about **170 GB**. Budget about 140 MB per tile for downloads and processing time. This still fits on the remote's ~300 GB, but not alongside large intermediates.
- **Vertical accuracy:** treat the "±2 cm vs surveyed spot heights" as **internal consistency, not absolute accuracy**. The kantakartta spot heights are digitized (mostly 2021-09) and probably derived from this lidar. For collision the *relative* accuracy (1 cm noise, curb steps recoverable) is what matters, and that is confirmed.
- **Ground density:** class 2 alone leaves 46 % of 1 m cells empty in the centre tile. Merging the class-12 ground is mandatory for a gap-free collision heightfield.
- **Curb detection method:** step heights must be measured with per-side slope fits. Naive side medians report 4–5 cm "steps" on plain pavement.
- **Google mesh coordinates:** the earth-reverse-engineering OBJ output is on a 6,371,010 m sphere, not WGS84 ECEF. Convert it with spherical lat/lon before projecting to EPSG:3877.
