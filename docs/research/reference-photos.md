# Reference photos for visual QA: turku.360tour.fi and Google Maps user photos

Date: 2026-10-25. Pilot area: Läntinen Rantakatu 15, Turku (60.44839, 22.26726).
Each claim is tagged **[VERIFIED]** (I ran it in this session) or **[UNVERIFIED]**.

## TL;DR

| Source | Usable for pilot? | Best assets | Key |
|---|---|---|---|
| turku.360tour.fi (3DVista) | Yes, but only aerial (drone) views near the pilot area | "View over the River Aura" is a drone pano **133 m** from the pilot point. It looks straight at the Läntinen Rantakatu frontage (May 2016). | none |
| Google Maps photo spheres (user 360s) | **Yes, the best source** | **73** third-party 360s within about 300 m (2015 to 2026). Nearest: a 13500x6750 pano 20 m away (indoors, Pharmacy Museum), a 2020 drone pano over the river 49 m away, and 2023 river-level boat panos 50 m away. | none (`streetlevel`) |
| Google Maps place photos | Partly | Full-resolution originals (3000x4000 and up, camera EXIF kept, GPS removed). For restaurants they are mostly food and interiors. Museums and landmarks have more exterior shots. | none (internal web endpoints, ToS-grey) |

## 1. turku.360tour.fi (3DVista)

### Where the data lives [VERIFIED]
- `index.html` loads `lib/tdvplayer.js` and `script.js`. `script.js` is the generic 3DVista runtime and holds no panorama list.
- The tour definition is in **`script_general.js`** (682 KB, "Generated with v2020.5.9, Wed Jan 20 2021"). It is one big object literal passed to `TDV.PlayerAPI.defineScript(b)`. Labels are separate, in **`locale/fi.txt`** as `panorama_<ID>.label = ...`. `locale/en.txt` returns 404.
- There is no map, floor plan or geo data in the tour. All 299 hotspots have `"maps": []`, and there are no `latitude`/`longitude` keys.
- Each `Panorama` has `frames[0].{front,back,left,right,top,bottom}.levels[]` (`TiledImageResourceLevel`: url template `media/panorama_<ID>_0/<f|b|l|r|u|d>/<level>/{row}_{column}.jpg`, width, rowCount, colCount). Tiles are 512 px. **Level 0 is the highest resolution.**
- Tiles were last modified on 2021-03-10 according to the HTTP `Last-Modified` header. The server is Apache. robots.txt returns 404.

### Inventory: 21 panoramas [VERIFIED]
Run `tools/refs/tdv360.py list` to generate `data/refs/360tour/inventory.json`. Two sources feed the "camera", "date" and GPS columns:
- **EXIF in each `media/panorama_<ID>_t.jpg` thumbnail.** The 256x128 thumbnails keep the full EXIF/IPTC from Photoshop.
- **The Photoshop "slice name".** This is the original file name, for example `Jokiranta_VanhaSuurtori_360_valmis`, and it identifies the place.

FC300X is a DJI Phantom 3 and FC6310 is a DJI Phantom 4 Pro, so both are drones. DC-GH5 and the Canon 5D3 are ground cameras.

| Label | Face px (L0) | Camera | Captured | EXIF GPS | Dist. to pilot | Kind / source name |
|---|---|---|---|---|---|---|
| View over the River Aura | 4608 | FC300X | 2016-05-12 20:16 | 60.44722, 22.26778 | **133 m** | aerial, `Turku_360panorama_toukouu12_jokiranta` |
| Summer Night City | 6656 | FC6310 | 2018-08-16 22:16 | 60.44734, 22.26547 | 152 m | aerial at night, `Aerial_Taiteidenyo_teatterisilta360` |
| Samppalinna Park | 6656 | FC6310 | 2018-05-28 22:03 | 60.44581, 22.26719 | 287 m | aerial |
| Vartiovuori Observatory and Park | 6656 | FC6310 | 2018-06-19 | 60.44832, 22.27677 | 522 m | aerial |
| Turku Art Museum | 4608 | FC300X | 2015-09-11 | 60.45374, 22.26226 | 655 m | aerial |
| The Magical Winter City | 4608 | FC300X | 2018-01-21 | 60.45323, 22.27525 | 694 m | aerial over the cathedral, winter |
| Åbo Akademi University Campus | 4608 | FC300X | 2015-09-11 | 60.45777, 22.27834 | 1.2 km | aerial |
| Turku Science Park | 7680 | FC6310 | 2019-08-27 | 60.45105, 22.29406 | 1.5 km | aerial (Kupittaa) |
| Wintertime Ruissalo Nature Park | 4608 | FC300X | 2018-03-02 | 60.42679, 22.18589 | 5 km | aerial |
| Ruissalo Beach and Camping | 4608 | FC300X | 2018-05-12 | 60.42106, 22.09229 | 10 km | aerial |
| **Old Beauties by the River Aura** | **8192** | DC-GH5 | 2018-09-16 17:05 | none | about 0.7 km (riverside quay at Vanha Suurtori) | **ground**, `Jokiranta_VanhaSuurtori_360_valmis` |
| Summer Time City | 8192 | DC-GH5 | 2018-09-16 16:52 | none | about 0.8 km | ground, `Tuomiokirkko_puisto_360` (Cathedral park / Per Brahe) |
| The River Aura on Ice | **11264** | Canon 5D3 | 2018-02-06 21:09 | none | central river | ground or low height, night, winter, `Jokiranta_...` |
| The Oak Groves of Ruissalo | 7680 | DC-GH5 | 2018-09-23 | none | far | ground |
| Starshine by the Sea | 6656 | Canon 5D3 | 2018-10-10 | none | far | ground, night (Kuuvannokka) |
| Fly-fishing in Halistenkoski | 7168 | FC6310 | 2018-07-06 | none | far | aerial |
| University Campus Area (+ Autumn) | 7680 | none / FC6310 | 2018 / 2019-10-07 | none | about 1 km | aerial (Yliopistonmäki) |
| Paavo Nurmi Sports Arena | 7680 | none | about 2018 | none | far | aerial |
| Turku Castle and the Harbour | 7680 | none | about 2018 | none | about 3 km | aerial, evening |
| Seili Island | 7680 | none | about 2018 | none | far | ground |

- Levels per pano are 5 or 6, for example 4608/2560/1536/1024/512 or 11264/5632/3072/1536/1024/512.
- The capture era is **2015 to 2019**, published in 2021. The photographer and copyright holder is **Mika Kurkilahti / A1 Media Oy** (from the EXIF Artist and IPTC By-line). Use the images as internal QA references only. Do not ship them.
- 14 of the 21 panoramas are aerial. They are good for roofline, massing and river-edge checks, and weak for street-level facade detail.

### Geolocating the panoramas without GPS
- **[VERIFIED] Thumbnail EXIF GPS** covers 11 of 21. The drone GPS altitude field is unreliable: some values are negative, which looks like a reference-flag misuse.
- **[VERIFIED] Photoshop slice names** give the place for the rest. For example, "Jokiranta_VanhaSuurtori" puts Old Beauties on the riverbank at Vanha Suurtori (confirmed visually: cobbled quay, steps, red-brick building).
- **[VERIFIED data, UNVERIFIED method] Hotspot bearings.** Every pano has `link_*` hotspots with yaw and pitch that point at the *visible location* of other spots, for example `link_tuomiokirkko_talvi@40°/+2°`, `link_teatterisilta@-83°/-27°` and `link_vartiovuori@77°/+4°`. With 2 or more known landmark positions, the camera position and the yaw-to-north offset can be resected. Pitch plus distance also gives drone height. All hotspots are dumped in `data/refs/360tour/tour.json`.
- **[VERIFIED, approximate] Heading.** In "View over the River Aura", tour yaw ≈ true bearing, so the offset is about 0°. I checked this two ways:
  - The cathedral spire sits at yaw ≈ +46°, and its geographic bearing from the EXIF position is ≈ 46°.
  - A rectilinear render at yaw −12° (the computed bearing to the pilot point), pitch −25°, frames the Läntinen Rantakatu block with the Qwensel house and Pharmacy Museum.

  Do not assume the same offset for other panos. Solve it per pano with the hotspot method.

### Downloads and stitching [VERIFIED]
Both panoramas below were downloaded at level 0. The seam check is the mean |Δ| across the 12 cube edges, on a 0 to 255 scale.

| Pano | Tiles | Time | Seam check | Outputs |
|---|---|---|---|---|
| View over the River Aura (`panorama_B5D39CE1_...`) | 486 | 5 s | 0.3 to 10 | 6 faces at 4608², `equirect_14476.jpg` (14476x7238), 4096 preview, view `view_y-12_p-25_f60_1600x1200.jpg` aimed at the pilot block |
| Old Beauties by the River Aura (`panorama_8356AAED_...`) | 1536 | 14 s | 1.8 to 12.7 | 6 faces at 8192², `equirect_16384.jpg`, 4096 preview, view at yaw −137 |

- **Cube conventions** (the same numbers the tour uses for hotspots):
  - yaw 0 is the centre of face `f`, and yaw increases clockwise toward `r`. Pitch is positive upward.
  - `u` has its bottom edge on `f`'s top edge. `d` has its top edge on `f`'s bottom edge.
  - I confirmed the `d` orientation numerically: matching `d` row 0 against the bottom row of `f` gives Δ = 5.6 for the chosen rotation, against 39 to 55 for the other three rotations.
- Visual check: both equirects are seamless. The nadir of the drone pano is a blurred patch made by the photographer.
- Files are under `data/refs/360tour/<pano_id>/`, about 250 MB for the two, including the tiles.

### Tooling
- `tools/refs/tdv360.py` is the reusable downloader, written in Python with numpy and Pillow only.
  - `list` builds the inventory: labels, levels, EXIF GPS/date/camera, source name, and distance to `--near`.
  - `fetch` handles one pano. It downloads tiles in parallel (cached and resumable) and assembles the faces. Optional outputs are `--equirect` and `--view yaw,pitch,hfov,WxH` rectilinear renders. These renders are the format to pair with a game camera that sits at the same position and orientation.
  - `--base` makes it work for other 3DVista tours that use the same `script_general.js` layout. That generality is UNVERIFIED.
- `tools/refs/tdv_extract.js` dumps the full tour graph to `tour.json` with Node: panoramas, adjacency, and hotspot yaw/pitch/labels. It evaluates `script_general.js` in a `vm` sandbox with stubbed `TDV`.

## 2. Google Maps user-contributed and business photos

### 2a. User photo spheres via `streetlevel` [VERIFIED]
`streetview.find_panorama(lat, lon, radius, search_third_party=True)` returns third-party panos with no key. These have `CIHM0og...` ids, and new uploads have `CIABIh...` ids. It returns only the nearest pano per call, so `tools/refs/gmaps_photospheres.py` queries a grid and removes duplicates.

- The grid run was ±300 m with a 60 m step, 121 queries in 13 s. It found **73 unique third-party panos**: 9 within 100 m and 25 within 200 m. The list is in `data/refs/gmaps_spheres/photospheres.json` and includes id, lat/lon, date, size, heading, uploader and permalink.
- Capture dates run from 2015 to **2026-10-09** (360 Alpine Vision AG). Sizes range from 4096 to 13500 px wide. A few are partial panos, for example 12288x3088.
- The four nearest are downloaded at zoom 4 in `data/refs/gmaps_spheres/`:
  - `CIHM0ogKEICAgICc39LG_QE`, 20 m, 2020-05-06, Insite Finland, 13500x6750 at full size. It is **an interior** of the Pharmacy Museum / Qwensel House, which is linked to that place.
  - `CIHM0ogKEICAgICc39LfsgE`, 49 m, 2020-05-06, 8192x4096. It is a **drone 360 over the Aura** showing the pilot frontage. This is a very good reference.
  - `CIHM0ogKEICAgIDJgurEqwE` and `...qm6wE`, 50 m, 2023-06-16, IT Åbo Akademi, 7680x3840. They were **shot from a boat at water level**, facing the Läntinen Rantakatu embankment and facades. These are very good references.
- Metadata includes `heading`, which gives the north alignment of the pano. That alignment is UNVERIFIED for user uploads, because users often have bad compass data.
- Full-resolution download works (`get_panorama(p, zoom=max)`), the same as the earlier finding in `docs/research/streetview.md`.
- Install gotcha: streetlevel 0.12.13 needs `pyfrpc`, which fails to build on Python 3.14, and `pyexiv2`, whose wheel needs a missing `libINIReader`. The fix is Python 3.12, `--no-deps`, and a stub `pyexiv2`. The recipe is in the script docstring.

### 2b. Business / user flat photos without a key [VERIFIED, ToS-grey]
`tools/refs/gmaps_place_photos.py` does this in three steps:
1. **Place discovery.** `https://www.google.com/search?tbm=map&q=<category>&pb=!4m12!1m3!1d1500!2d<lon>!3d<lat>...` returns about 20 places with feature id `0x...:0x...`, name, categories, address and lat/lon. "restaurant", "museum" and "shop" each returned 20 or more near the pilot. The script re-sorts the results by distance.
2. **Photo list.** `https://www.google.com/maps/place/data=!4m2!3m1!1s<fid>` returns HTML that contains a `/maps/preview/place?...` URL. Fetching that returns 5 to 70 photo URLs: 70 for the Pharmacy Museum, 10 to 13 for restaurants. Hosts include `lh3.googleusercontent.com/grass-cs/...`.
3. **Original image.** Appending **`=s0`** to a photo URL returns the original upload, for example 4000x3000 or 4284x5712. Camera EXIF and DateTimeOriginal are kept. **GPS is stripped.**

Results for the pilot area:
- Pharmacy Museum (Läntinen Rantakatu 13): mostly interiors, plus one courtyard and street shot from 2023-01.
- Pub Niska, Sharqi, Chingon: food, receipts and interiors, with one outdoor street photo.

So business photos are low-yield for facade QA. Landmarks such as the cathedral, Vanha Suurtori, river and bridges should do better. That last point is UNVERIFIED: I did not run those categories.

Only the first batch of photos is in the preview payload. Paging through all of a place's photos needs another internal RPC, which is UNVERIFIED and not attempted. Requests were a few dozen in total and none were blocked. These are undocumented endpoints, and bulk use violates the Google Maps ToS. Keep volumes tiny, cache everything, and use the photos only as private QA references.

### 2c. Other routes (not run)
- **Places API (New) Place Photos** needs an API key with billing enabled. Each Place Details call returns up to 10 photo references, and the maximum image size is 4800 px. **[UNVERIFIED]**: no key was created, per the instructions.
- **Open-source scrapers [UNVERIFIED]:**
  - `gosom/google-maps-scraper` (Go, Playwright; has an images option)
  - `omkarcloud/google-maps-scraper`
  - Various Selenium/Playwright "google maps photos" scrapers

  All of them drive a headless browser over the same internal endpoints as 2b, so 2b's plain HTTP approach is lighter.
- **Paid services [UNVERIFIED]:** SerpApi `google_maps_photos` engine, Apify "Google Maps Scraper". Not used.

## Recommendation for the QA loop
1. For the **pilot**, use these, ranked:
   - The Google photo spheres `...c39LfsgE` (drone, 2020) and `...JgurEqwE` / `...qm6wE` (river level, 2023)
   - 360tour "View over the River Aura" (drone, 2016)

   Render matching rectilinear views with `tdv360.py --view`. The same `view()` function can be applied to any equirect.
2. Pose each reference by solving the heading from 2 or more landmarks: the cathedral spire, the bridge ends, or hotspot bearings. Then place the game camera at the pano lat/lon, with height from the EXIF or the pitch to known points.
3. Treat capture dates (2015 to 2023) as a source of expected differences: boats, scaffolding, foliage, signage. Tell the model in the QA prompt to ignore transient objects.

## Exact commands
```bash
# 360tour inventory + downloads (system python3 with numpy+pillow)
python3 -I tools/refs/tdv360.py list  --out data/refs/360tour --near 60.44839,22.26726
python3 -I tools/refs/tdv360.py fetch --out data/refs/360tour --pano B5D39CE1 --equirect --view=-12,-25,60,1600x1200
python3 -I tools/refs/tdv360.py fetch --out data/refs/360tour --pano "Old Beauties" --equirect --eq-width 16384 --view -137,5,70,1600x1200
node tools/refs/tdv_extract.js data/refs/360tour/raw/script_general.js data/refs/360tour/raw/locale/fi.txt data/refs/360tour/tour.json

# Google photo spheres (venv recipe in the script docstring)
.venv-sl/bin/python tools/refs/gmaps_photospheres.py --center 60.44839,22.26726 --half 300 --step 60 --out data/refs/gmaps_spheres --download 4 --zoom 4

# Google place photos (keyless, ToS-grey, keep tiny)
python3 -I tools/refs/gmaps_place_photos.py --center 60.44839,22.26726 --query restaurant --out data/refs/gmaps_places --places 3 --photos 8
python3 -I tools/refs/gmaps_place_photos.py --fid 0x468c76fc772a9751:0x9ea21e2e451378ab --out data/refs/gmaps_places --photos 8
```
