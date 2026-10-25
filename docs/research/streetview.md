# Street-level 360 imagery and depth for Turku

Research date: 2026-10-25. Target: a browser game (Chrome, WebGPU/WebGL2) of Turku, built from lidar first. Street-level panos are an **offline** input: they supply facade textures, street-furniture detection and splat training. Nothing here is streamed raw to the browser.

Legend: **[VERIFIED]** means I ran it in this session. **[UNVERIFIED]** means it comes from docs or memory and was not run.

Working dir on remote: `osgallery-dev:~/turku3d/research/streetview/` (venv `.venv`, ~3.8 GB because of torch; data ~390 MB).
Local copies: `docs/research/streetview/` (previews, scripts in `scripts/`).

---

## TL;DR

| Question | Answer |
|---|---|
| Google Street View panos downloadable in 2026? | **Yes [VERIFIED]**. `streetlevel` 0.12.13 got a **16384x8192** equirect of a **2026-07** capture in 2.5 s. |
| Google depth ("dm" plane depth) still obtainable? | **Yes [VERIFIED]**. It works via `find_panorama_by_id(..., download_depth=True)` and returns a 512x256 ray-distance map. Depth parsed for 282 of 302 panos in a 460x460 m area; 20 failed in the decoder (empty or short blob). |
| Google depth quality | Coarse planar proxy: a ground plane plus a few facade planes, sky = -1. Useful for pose sanity checks and occlusion masks, **useless for curbs or stairs**. |
| Coverage, central Turku | 302 panos in 460x466 m. Nearest-neighbour spacing is a **median of 10.0 m (p10 7.7, p90 10.4)**, about **10 panos per 100 m of street per capture pass**. Current imagery: 125+3 from 2026-07/08, 60 from 2024-06, the rest 2009-2022. Historical layers go back to 2009, with up to 10 time layers per spot. |
| Apple Look Around | **Works in Turku [VERIFIED]**. 36 panos in the z17 tile at the centre, 33 of them captured **2026-06-23**. 6 HEIC faces downloaded; face 0 is 5632x4864. |
| Mapillary | Dense coverage seen in the web map **[VERIFIED visually]**. The API needs a free client token; unauthenticated calls give 500/403 [VERIFIED]. |
| KartaView | API **broken** from both hosts [VERIFIED]: TLS failure locally (curl exit 60), empty body on the remote. Skip it. |
| Monocular metric depth | DepthAnything-V2-Metric-Outdoor-Large on cubemap faces runs at about 15 s/face on 10 CPU cores [VERIFIED]. **Raw metric scale is off about 3.3x** (needs scaling). After median scaling, median AbsRel vs Google depth is 0.098. Always rescale against lidar. |
| Google pose vs Turku lidar | Skyline matching against a 0.5 m lidar DSM puts the pose within **about 1 m XY and about 1-1.4° yaw** [VERIFIED, 1 pano]. Google `elevation` (7.37) minus lidar ground (4.93) is **2.44 m**, a plausible camera height. That suggests the field is orthometric camera height (N2000-like); this rests on one sample. |

---

## 1. Google Street View: tools and a real Turku test

### 1.1 Tools

| Tool | Status | Notes |
|---|---|---|
| **sk-zk/streetlevel** (`pip install streetlevel`, v0.12.13) | **VERIFIED, works** | Google SV, Apple Look Around, Bing Streetside, Yandex, Naver, Kakao, Baidu, Ja, Mapy.cz. It has metadata, historical layers, links, coverage tiles, full-res stitching and **Google depth decoding** (`streetlevel/streetview/depth.py`, derived from GSVPanoDepth.js). |
| robolyst/streetview (`pip install streetview`) | UNVERIFIED | Simpler search plus tile download; no depth. |
| proog128/GSVPanoDepth.js | UNVERIFIED (logic verified via streetlevel port) | The original JS decoder of the base64 zlib-less "dm" plane format. |
| cplusx/google-street-view-panorama-download, juliusmh/gsv-depth etc. | UNVERIFIED | Older; many rely on the dead `cbk0.google.com/cbk?output=json&dm=1` endpoint. streetlevel uses the newer `photometa`/`GetMetadata` protobuf-JSON RPC, which still returns depth. |
| Official Street View Static API / Tiles API (`tile.googleapis.com/v1/streetview`) | UNVERIFIED | Needs an API key and costs money. The Map Tiles API has `streetview/metadata` and tiles but **no depth**. Not needed. |

Important API detail [VERIFIED]: `streetview.find_panorama(lat, lon, ...)` **does not accept `download_depth`** any more. The SingleImageSearch call returns a different depth format, so the library drops depth there. Look up the pano ID first, then call `find_panorama_by_id(id, download_depth=True)`.

### 1.2 Working code (pano + depth)

```bash
ssh osgallery-dev
mkdir -p ~/turku3d/research/streetview && cd ~/turku3d/research/streetview
python3 -m venv .venv && .venv/bin/pip install streetlevel pillow numpy pyproj scipy
```

```python
# dl.py  (run: .venv/bin/python dl.py <panoid> 5)
import sys, json, numpy as np
from PIL import Image
from streetlevel import streetview
from pyproj import Transformer

lon, lat = Transformer.from_crs(3877, 4326, always_xy=True).transform(23459500, 6704500)
p0   = streetview.find_panorama(lat, lon, radius=80)            # nearest pano (no depth here)
pano = streetview.find_panorama_by_id(p0.id, download_depth=True)
img  = streetview.get_panorama(pano, zoom=5)                    # 16384x8192 (zoom 5); zoom 4 = 8192x4096
img.save(f"pano_{pano.id}_z5.jpg", quality=92)
d = pano.depth.data.astype(np.float32)                         # (256, 512) metres along ray, -1 = sky/no plane
np.save(f"depth_{pano.id}.npy", d)
print(pano.id, pano.date, pano.lat, pano.lon, pano.elevation, pano.heading, pano.pitch, pano.roll,
      [(h.id, str(h.date)) for h in pano.historical])
```

Real output [VERIFIED]:

```
nearest 9oQUgUbnSDpde6Vhe1T8xA 60.450063751930735 22.264955679465796 2026-07
elev 7.3666 heading 0.9693 rad pitch 0.0238 roll 0.0270
image_sizes ... 8192x4096, 16384x8192
depth (512, 256) min 1.83 m max 105.4 m, sky fraction 0.41
historical [('xe4RV7d6Isse4420nR9pQg','2023-05'), ('lj7dQB_AdqubccI5Ka8z9A','2022-10'), ('Nb9Xt8amYTusRITAXv3umQ','2021-09')]
address 16 Eriksgatan (Eerikinkatu 16, the Kauppahalli corner)
```

ETRS-GK23 position of that pano: x 23459543.47, y 6704442.92.

Files on the remote, under `~/turku3d/research/streetview/`:
- `pano_9oQUgUbnSDpde6Vhe1T8xA_z5.jpg` (16384x8192, 10.7 MB)
- `depth_9oQUgUbnSDpde6Vhe1T8xA.npy`
- `meta_9oQUgUbnSDpde6Vhe1T8xA.json` (incl. 26 neighbours and links)

Local previews: `streetview/pano_9oQUgUbnSDpde6Vhe1T8xA_preview.jpg`, `streetview/pano_crop_fullres.jpg` (1:1 crop; real detail, not an upscale), `streetview/depth_9oQUgUbnSDpde6Vhe1T8xA_preview.png`.

Conventions [VERIFIED empirically]:
- Equirect column u maps to azimuth `heading + (u/W - 0.5)*2π`, measured clockwise from north; the image centre is the `heading`.
- Row v maps to elevation `(0.5 - v/H)*π`.
- The depth map uses the **same orientation as the image, with no horizontal flip**. The comparison against DAv2 shows building masses line up only when unflipped.
- Depth values are **distances along the ray**, not planar z.
- The camera height implied by the depth's ground plane is 1.83 m. That is lower than the lidar-implied 2.44 m, so Google's ground plane is a rough proxy.

What Google depth looks like: see `streetview/depth_compare_google_vs_dav2.png` (top). It contains about 5 smooth planes, has no trees, poles or people, and its ground plane runs straight under the curb. **It cannot drive curb, stair or ramp collision.** Use it for pose sanity, sky masks and coarse occlusion masks only.

### 1.3 Robustness notes
- 20 of 302 `find_panorama_by_id(..., download_depth=True)` calls raised errors inside `depth.py`: 19 had an empty bit string, 1 an index out of bounds. **Wrap the call in try/except and retry with `download_depth=False`.**
- Rate: 302 metadata+depth calls took 4 min with 6 threads. No blocking was observed. Keep it at 4 to 6 parallel requests.
- Download speed: the full 16K pano (512 tiles) stitched in 2.5 s from the remote.

---

## 2. Coverage density and dates (central Turku) [VERIFIED]

Method: `streetview.get_coverage_tile(x, y)` on a 3x3 block of z17 tiles around lat 60.4506 / lon 22.2642, followed by per-pano `find_panorama_by_id` (`scripts/coverage.py`, `scripts/dates.py`).

- **302 panos** in a 461x466 m area, and 246 within a 500x500 m box centred on 23459500 / 6704500.
- **Spacing**: nearest-neighbour distance has a median of 10.0 m (p10 7.7 m, p90 10.4 m). That is about **10 panos per 100 m of street per drive pass**. Wide streets driven in both directions roughly double this.
- **Current (top) imagery date** per pano:

  | date | count |
  |---|---|
  | 2026-07 / 2026-08 | 125 / 3 |
  | 2024-06 | 60 |
  | 2022-10 | 18 |
  | 2020-09 | 1 |
  | 2019-05 / 2019-06 | 12 / 28 |
  | 2017-06 / 2017-09 | 24 / 1 |
  | 2014-07 / 2014-08 | 1 / 5 |
  | 2009-06 | 4 |

- **All captures, including historical layers**:

  | year | captures |
  |---|---|
  | 2009 | 171 |
  | 2011 | 104 |
  | 2014 | 207 |
  | 2017 | 230 |
  | 2019 | 152 |
  | 2020 | 126 |
  | 2021 | 129 |
  | 2022 | 167 |
  | 2023 | 74 |
  | 2024 | 87 |
  | 2026 | 160 |

  The median pano has 3 to 4 time layers; up to 10.
- **Resolution**: 219 panos have a max width of 16384 and 63 have 13312 (older cameras).
- **Source**: all `launch` (Google car or trekker), with no third-party photospheres in this set.
- **Elevation field** across the area: 6.25 to 24.6 m. That is consistent with N2000 heights in the centre (Aura riverside to the hill).

Multiple time layers help: several dates at the same spot give different lighting and occluders (parked cars, shop fronts). That is useful for texture median-filtering and de-lighting.

---

## 3. Alternatives and complements

### 3.1 Apple Look Around [VERIFIED]
```python
from streetlevel import lookaround
from pillow_heif import register_heif_opener; register_heif_opener()   # pip install pillow-heif
tile = lookaround.get_coverage_tile_by_latlon(60.4506, 22.2642)        # returns CoverageTile; use .panos
p = tile.panos[0]          # id, build_id, lat, lon, date (2026-06-23 09:37), coverage_type CAR, elevation 7.16
auth = lookaround.Authenticator()
for face in range(6):
    open(f"la_{p.id}_f{face}.heic","wb").write(lookaround.get_panorama_face(p, face, 0, auth))
```
- Panos per z17 tile [VERIFIED]: Turku centre 36, Turku Kauppatori 106, Helsinki 1426, Tampere 658. Turku dates: 33 from 2026-06, 2 from 2024-06, 1 from 2025-07.
- Spacing is about 4.5 m (the car series above is spaced 2 s apart). That is denser than Google.
- Faces at zoom 0, in pixels:

  | face | size |
  |---|---|
  | 0 | 5632x4864 |
  | 1 | 3072x4864 |
  | 2 | 5632x4864 |
  | 3 | 3072x4864 |
  | 4 (top) | 2048x2048 |
  | 5 (bottom) | 3584x4096 |

  The total resolution is higher than Google (roughly 17K equirect equivalent). Preview: `streetview/lookaround_face0_preview.jpg`.
- Faces are **not cube faces**. They are sphere segments with per-face yaw, pitch, FOV and camera metadata. `streetlevel.lookaround.reproject` (needs torch) converts them to equirect [UNVERIFIED in this run; `to_equirectangular` is not exported at top level in 0.12.13].
- **No depth** is available from Look Around imagery. Apple's own mesh is not exposed.
- Value: a second, independent and newer set of facade photos. The different camera height and time help de-occlusion and texture median-ing.

### 3.2 Mapillary
- Coverage [VERIFIED visually, screenshot `streetview/mapillary_coverage_turku_centre.jpg`]: dense green sequences on essentially every street and walkway in the centre (Kauppatori, Aurakatu, Eerikinkatu, the river banks). That includes **pedestrian-only paths that Google cars can't reach**.
- API [VERIFIED that a token is required]: `https://graph.mapillary.com/images?bbox=...` returns 500 without a token, and `https://tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}` returns 403.
- Next step [UNVERIFIED]: create a free client token at mapillary.com/dashboard/developers.
  ```
  GET https://graph.mapillary.com/images?access_token=MLY|...&bbox=22.255,60.445,22.275,60.455&fields=id,captured_at,computed_geometry,computed_compass_angle,camera_type,is_pano,thumb_original_url,sfm_cluster,mesh,atomic_scale&limit=2000
  ```
  - `sfm_cluster.url` points to OpenSfM reconstruction JSON (a sparse point cloud plus camera poses).
  - `mesh.url` gives a per-image coarse depth mesh.
  - `camera_type=spherical` / `is_pano=true` filters for 360s.
  - Tools: `mapillary/mapillary-python-sdk`, `mapillary_tools`, OpenSfM [UNVERIFIED].
- Quality varies a lot (phones, GoPro Max, Insta360). It is a good fill-in for pedestrian areas and courtyards.

### 3.3 KartaView
`https://api.openstreetcam.org/2.0/photo/?lat=..&lng=..&radius=..` fails with a TLS error locally (curl exit 60) and returns an empty body on the remote [VERIFIED]. Treat it as dead.

### 3.4 Monocular metric depth for equirect panos

Tested [VERIFIED]: `depth-anything/Depth-Anything-V2-Metric-Outdoor-Large-hf` via HF `transformers` 5.20 / torch 2.14 on CPU (`scripts/mono.py`).
- Pipeline: 4096x2048 equirect → `py360convert.e2c` → 4 horizontal 1024 px 90° faces → model → planar z converted to ray distance (`z*sqrt(1+x²+y²)`) → `py360convert.c2e` → 512x256.
- Speed: 12 to 18 s per face on 10 CPU cores, 1 m 51 s total. On the M1 Max (MPS) expect about 1 s per face [UNVERIFIED].
- Result against Google depth (42k valid px, rows near the poles and the car masked):
  - raw AbsRel **2.34**: the outdoor metric model, trained on VKITTI, overestimates by about 3.3x on these faces;
  - median scale google/mono **0.299**;
  - after scaling, median AbsRel **0.098** and correlation 0.86.
  - Visual: `streetview/depth_compare_google_vs_dav2.png` (bottom). It has crisp trees, poles, people and the van, but scale steps show at the cube seams.
- **Conclusion**: monocular "metric" output is not metric enough. Always solve a per-face (or per-pano) scale and shift against lidar ranges rendered from the refined pose:
  ```python
  # lidar_r: lidar range image rendered at the pano pose (pose.py render()); mono: model ray-distance (same grid)
  m = np.isfinite(lidar_r) & (mono > 0) & (lidar_r < 40)
  A = np.c_[1/mono[m], np.ones(m.sum())]        # fit in inverse depth (disparity) - robust for far range
  s, t = np.linalg.lstsq(A, 1/lidar_r[m], rcond=None)[0]
  mono_metric = 1/(s/mono + t)
  # then RANSAC/Huber it, per cube face, and only trust it where lidar is sparse (facades, under eaves, curb faces)
  ```
- Other candidates to try, all [UNVERIFIED] in this run:

  | model | notes |
  |---|---|
  | Depth Anything V3 | Metric and multi-view variants (2025). Multi-view mode could produce consistent depth across neighbouring panos. |
  | Apple Depth Pro | Sharp boundaries; predicts focal length. Run it per cube face. |
  | UniDepth V2 | Has a camera model input, so it handles wide FOV. |
  | MoGe-2 (Microsoft) | Metric point maps, very good geometry. Per perspective face. |
  | Depth Any Camera (DAC) | Trained to handle equirect/fisheye directly; no cube seams. |
  | Panorama-specific: DepthAnywhere, PanDA, "Depth Any Panoramas", UniK3D (spherical camera support) | Avoid seam issues. |

  For the game, choose by sharpness of curb and step edges after lidar rescaling, not by benchmark AbsRel. GPU recommended; the remote has none, the M1 Max via MPS is OK for tens to hundreds of panos.

### 3.5 Pano pose refinement against lidar [VERIFIED, 1 pano]
First attempt (`scripts/pose.py`): render the airborne lidar into an equirect range map and compare it with Google depth. **It does not work well.**
- Airborne lidar has very few facade points, so roofs are seen "through" walls (`~/turku3d/research/w0-gate/docs/research/streetview/lidar_vs_pano_overlay.jpg`).
- Google depth is only planes.
- The optimum hit the search border.

Working approach (`scripts/skyline.py`): **skyline matching**.
- **Observed skyline**: from the photo's blue-sky mask, take per column the first row with at least 6 consecutive non-sky pixels.
- **Predicted skyline**:
  1. Build a 0.5 m max-z DSM from 4 lidar tiles (13.2M points within 220 m).
  2. Ray-march 2048 azimuths out to 200 m.
  3. Take `max(atan((z_dsm - z_cam)/d))` per azimuth.
- **Search** dx, dy in ±4 m (0.5 m steps), camera height h in {2, 2.5, 3}, and yaw in ±30 px rolls.
- **Result**:

  | pose | median skyline error |
  |---|---|
  | No correction | 17.5 px (3.1°) |
  | Best: dx +1.0 m, dy -0.5 m, h 2.5 m, yaw +1.05° | **9.4 px (1.66°)** |

  The top 4 solutions all lie within 1 m / ±1.4°. Overlay: `streetview/skyline_fit.jpg` (yellow = lidar skyline, red = photo skyline). The building cornice matches to a few pixels; residuals are mostly tree crowns, since the 2021 lidar and 2026 leaves differ.
- Lidar ground under the pano is 4.93 m and Google `elevation` is 7.37, which gives **camera height 2.44 m**. This matches the h = 2.5 search optimum.

Production pose pipeline (recommended):
1. **Init** from Google lat/lon/heading/pitch/roll and `elevation`. Treat `elevation` as orthometric camera Z, pending more samples.
2. **Skyline + DSM** coarse search (above). Fill holes in the DSM first (median filter) to remove the yellow speckle.
3. **Fine 6-DoF**: edge alignment between the pano and a lidar intensity/RGB render, or PnP from features.
   - Option A: match SuperPoint+LightGlue features from the pano's cube faces against an orthophoto-textured lidar render.
   - Option B: COLMAP/hloc rig bundle adjustment across all panos of a street, with lidar-rendered depth priors and GPS priors. Better: it gives globally consistent poses.
4. Add **pitch/roll refinement** using vertical facade edges (vanishing-point check) once yaw and XY are fixed.

---

## 4. How the panos would be used in the pipeline

The browser constraint means all of this is **offline baking**. The browser receives KTX2/Basis-compressed facade atlases, meshopt/Draco meshes, and optionally splat tiles.

1. **Facade texture projection** onto lidar-derived building meshes:
   - For each facade triangle/texel, choose the best pano. Score it by the angle between the view ray and the facade normal (prefer frontal), by distance (closest), by occlusion test against the lidar mesh plus a mono-depth "foreground" mask (cars, people, trees), and by a recent date.
   - Blend 2 to 4 candidates with weights; use **median across time layers** to kill transient occluders.
   - Implementation: Blender (bpy, on the M1) with UV-projection from equirect via a custom shader, or a Python rasteriser (numpy/numba) writing per-texel colour. Output 2048² KTX2 UASTC/ETC1S atlases per building block.
   - Pixel budget: a 16K pano gives ~45 px per degree, about 2.6 cm/px at 10 m, so roughly 2 to 4 cm/px on street-level facades. Upper floors are worse and skewed; fill with Look Around and the orthophoto.
   - De-lighting: Turku captures are sunny with hard shadows. Prefer overcast layers from the history, or run an intrinsic-decomposition/de-shadow model before baking.
2. **Curb/stair/street-furniture detection** (for collision):
   - Run segmentation on the cube faces: Mask2Former/OneFormer on Mapillary Vistas classes (curb, curb cut, sidewalk, bike lane, pole, bench, bollard, trash can, fire hydrant, traffic sign) [UNVERIFIED in this run], or open-vocab SAM 2 + GroundingDINO.
   - Back-project the labels with lidar-scaled mono depth into ETRS-GK23. Fuse votes across neighbouring panos (about 10 m apart) into 3D points per class.
   - Use them to **classify and snap lidar ground breaklines**: curb lines become height steps of 10 to 15 cm, stairs get a step count from the image, and ramps/curb cuts become gaps in the curb.
   - Airborne lidar at about 100 pts/m² resolves curbs only weakly. The pano labels give the plan-view line, and the lidar or mono depth gives the height.
   - Furniture becomes instanced props with simple colliders (bench box, pole capsule, bollard cylinder) in the browser physics (Rapier WASM, for example).
3. **Gaussian splatting**:
   - Convert the panos to cube faces or perspective crops, get poses from step 3.5 or COLMAP, and train with gsplat or nerfstudio splatfacto. Use lidar points as initialisation and depth supervision. Mask sky, the car and people.
   - Needs a GPU, so not the remote. Use the M1 (slow), rent a cloud GPU, or try `OpenSplat` (supports Metal/CPU) [UNVERIFIED].
   - Problem: Google panos are about 10 m apart, which is too sparse for crisp splats. Combine them with Look Around (~4.5 m), Mapillary and the user's own walk-around 360 video.
   - Browser rendering: compress to `.spz`/`.ksplat`/SOG and stream per block. Viewers: Spark (sparkjs.dev, three.js), mkkellogg GaussianSplats3D, PlayCanvas SuperSplat/engine [UNVERIFIED].
   - Practical use: **background/vista splats or hero areas**, with the lidar mesh as collision. Don't use splats for collision.
4. **Mono depth as lidar complement**: fill facade geometry under eaves and inside arcades, add recesses for shop windows and doorways, and capture curb face heights. Always scale-correct against lidar as in 3.4.

---

## 5. Recommended next steps
1. Batch-download all Google panos within the starter block (about 250, roughly 2.7 GB at 16K) with depth and all historical IDs. Use 8K for non-hero areas.
2. Get a Mapillary token and pull `is_pano` images plus `sfm_cluster` for the block.
3. Pull Look Around for the same block and reproject it to equirect (needs torch; already in the venv).
4. Run skyline pose refinement for every pano (about 1.5 min each on the remote; parallelise per pano across the 10 cores). Then run a COLMAP/hloc global BA with lidar priors on the M1.
5. Prototype facade projection on one building (Kauppahalli / Eerikinkatu 16) in Blender, then export glTF + KTX2 and check it in Chrome.

## Files
- Remote `~/turku3d/research/streetview/`:
  - pano: `pano_9oQUgUbnSDpde6Vhe1T8xA_z5.jpg`
  - depth: `depth_*.npy`, `mono_*.npy`
  - coverage: `coverage_z17.json`, `coverage_z17_meta.json`
  - Look Around faces: `la_*_f*.heic`
  - lidar tiles: `lidar/pistepilvi_23459500_6704500.laz`, `lidar/pistepilvi_23459000_6704500.laz`, `lidar/pistepilvi_23459000_6705000.laz`
  - scripts
- Local `docs/research/streetview/`: previews, comparison images, `meta_9oQUgUbnSDpde6Vhe1T8xA.json`, `scripts/*.py`.
- LAZ download note: `opaskartta.turku.fi/3d/...` now **302-redirects to `turku.asiointi.fi/3d/...`**, so use `curl -L`.

---

## Verification

An independent adversarial check ran on 2026-10-25 on `osgallery-dev:~/turku3d/research/streetview/verify/`; local copies of the scripts and images are in `docs/research/streetview/verify/`. Every check below was run by the verifier, using different panos or a different method from the original wherever possible.

| # | Claim | Verdict | How it was checked |
|---|---|---|---|
| 1 | Full-res 16384x8192 GSV panos are downloadable (streetlevel 0.12.13) | **CONFIRMED** | Downloaded a *different* pano, `staCISwu-xHr70y_JtFYdA` (2024-06), at zoom 5: 16384x8192 in 2.8 s. 0.12.13 is still the latest version on PyPI (`pip index versions streetlevel`). |
| 2 | Google depth is still obtainable via `find_panorama_by_id(id, download_depth=True)`; `find_panorama()` has no `download_depth` | **CONFIRMED** | `inspect.signature`: `find_panorama(lat, lon, radius, locale, search_third_party, session)` and `find_panorama_by_id(panoid, download_depth=False, ...)`. Depth came back for 12 further panos, including a 2009 one and historical layer `Nb9Xt8amYTusRITAXv3umQ` (2021-09): all 256x512. |
| 3 | Depth map uses "the same orientation as the image, no horizontal flip" | **REFUTED** | Compared the photo's blue-sky mask with depth `< 0` on 7 panos (2017-2026). `pano.depth.data[:, ::-1]` aligns at zero shift on all 7, with IoU 0.85-0.91 against 0.60-0.80 un-flipped; the un-flipped best fit needs odd shifts of 8-64 px. The original pano agrees: AbsRel against DAv2 is 0.042 when flipped vs 0.099 un-flipped, and the per-quadrant scale is constant flipped (0.285-0.288) but steps un-flipped (0.281-0.309). The "cube-seam scale steps" in the report are largely this orientation error. **Fix: always use `np.fliplr(pano.depth.data)` with streetlevel 0.12.13.** |
| 4 | Google depth is "about 5 smooth planes", a coarse proxy unusable for curbs/stairs | **CONFIRMED in conclusion, REFUTED in detail** | Hooked `depth.parse_planes`: 11 panos carry **54-175 planes** each, with 12-47 near-horizontal planes in the lower hemisphere. The ground is broken into sub-planes 2-7 cm apart in height, with tilts of 1-3 deg that mix in noise. There is no clean ~12-15 cm curb step, and at 512x256 a pixel spans about 0.7 deg. So the depth is still not a collision source. It is a little richer than "5 planes", though, and a per-plane height clustering could at best hint at sidewalk-vs-road. |
| 5 | Coverage: 302 panos, NN spacing median 10.0 m; newest imagery 128 x 2026, 60 x 2024-06 | **CONFIRMED numbers, but misleading** | Recomputed from `coverage_z17_meta.json`: NN median 9.96 m (p10 7.68, p90 10.44); extent 460.8x466 m; 282 panos with depth; 219 at 16384 and 63 at 13312. However, **38 of the coverage-tile pano IDs are not the newest layer at their spot**. For example `staCISwu…` is 2024-06, but its history holds `siJ0GnTGPU3F8b4YQfsoHQ` from 2026-07. Counted by the newest layer per spot, **160 spots have 2026 imagery** (154 + 6) and only 32 have 2024-06 as their newest. The 20 depth failures also have no metadata at all (date `None`). **Fix: when batch-downloading, pick `max(historical, key=date)` per spot instead of the coverage-tile ID.** |
| 6 | Google `elevation` is orthometric camera height (one sample: 2.44 m) | **CONFIRMED, with a correction** | Took the median of lidar class-2 ground within 1.5 m for **168 panos** across the 3 local LAZ tiles. `elevation - ground` gives a median of 2.36 m (p10 1.72, p90 2.89). The offset is per campaign: 2026 2.31 ± 0.13, 2024 2.86 ± 0.04, 2022 2.85 ± 0.06, 2019 1.49 ± 0.46. An ellipsoidal height would sit about 18 m higher (the geoid in Turku), so this is orthometric (N2000-like). Camera height is rig-dependent, so calibrate one offset per capture date. Do not assume 2.44 m. |
| 7 | Skyline matching gives a pose within about 1 m and about 1-1.4 deg | **REFUTED as a general claim** | Re-ran the unmodified `skyline.py` on 3 more 2026-07 panos (4096 px previews, the same 3 lidar tiles). `ExvAQ…`: 24.2 → 15.7 px (2.75 deg) at dx +0.5 / dy -2.0; the solution is not unique, and the top 6 solutions span 1 m and 1.4 deg of yaw. `vqltt…`: 40.6 → 26.3 px (4.6 deg), with h at the search border (3.0). `XQusj…`: **failed outright**, because fewer than 200 valid sky columns came out of the RGB sky threshold. The 9.4 px result was the best case; 1 of 4 panos reached about 1.7 deg. The method needs a robust sky segmenter (e.g. a segmentation model), hole-filled DSM, a finer search and a wider h range. Treat Google's absolute pose accuracy against lidar as **unknown (roughly 1-2 m, a few degrees)** until a feature-based or BA method is tested. |
| 8 | Apple Look Around covers Turku with 2026-06 captures | **CONFIRMED** | Fetched 3 other z17 tiles. Cathedral: 230 panos (136 x 2024-06, 81 x 2026-06). Castle/harbour area: 35, all 2026-06. Office block: 36 (33 x 2026-06), which reproduces the original. |
| 9 | Mapillary API needs a token | **CONFIRMED** | `graph.mapillary.com/images?bbox=…` returned 500 and `tiles.mapillary.com/.../14/9205/4561` returned 403 from the remote. The web-app HTML exposes no static `MLY\|` token. |
| 10 | KartaView API is non-functional, treat it as dead | **REFUTED (API), CONFIRMED (not useful)** | `api.openstreetcam.org` still fails with curl exit 60, but the current host **`https://api.kartaview.org/2.0/photo/?lat=60.4506&lng=22.2642&radius=300` works** (HTTP 200, apiCode 600). It returned only 5 photos, all 2020 flat (`PLANE`), with no 360s. So the API works, but coverage is too sparse to matter. Skip it for that reason. |
| 11 | LAZ URLs on opaskartta.turku.fi 302-redirect to turku.asiointi.fi | **CONFIRMED (it is a 301)** | `curl -sIL https://opaskartta.turku.fi/3d/pistepilvi/23464000_6708500.laz` → `HTTP/2 301`, then `location: https://turku.asiointi.fi/...`, then 200 with 110 MB. `curl -L` is still the fix. |
| 12 | DAv2-Metric-Outdoor raw scale is about 3.3x too far | **CONFIRMED** | Recomputed from the saved `.npy` files: median Google/mono 0.299 (3.34x) un-flipped and 0.287 (3.48x) with the corrected orientation. The scaled-AbsRel figure should be quoted as **0.042** (flipped), not 0.098. It was only compared against Google's planar depth, so it says nothing about curb-edge sharpness. |

### Corrections to apply to the report
- **Depth orientation (most important):** the depth from streetlevel 0.12.13 must be flipped horizontally (`d[:, ::-1]`) to align with the equirect image. Every downstream use is affected: occlusion masks, sky masks, mono-depth rescaling and pose checks. The "scale steps at cube seams" were mostly caused by the misalignment.
- **Google depth content:** 54-175 planes per pano, not about 5. It is still unusable for collision.
- **Newest imagery:** the coverage-tile IDs are not always the newest layer. 160 of 282 spots have 2026 imagery. Select the newest historical ID per spot.
- **Camera height:** it is campaign-dependent: 2026 ≈ 2.31 m, 2022/2024 ≈ 2.86 m, 2019 ≈ 1.5 m (n = 168). Calibrate it per capture date.
- **Pose accuracy:** the skyline method failed on or underperformed for 3 of 3 additional panos. "Within about 1 m / 1.4 deg" is not established. Budget for a feature-based or bundle-adjustment registration step.
- **KartaView:** the API is alive at `api.kartaview.org`, but has almost no Turku coverage.
- The LAZ redirect is a 301, not a 302.
- Minor: `lidar/` on the remote holds 3 LAZ tiles, not the 4 mentioned in the summary.
