# Street richness baseline and G1 approval request

Phase 0 of `~/turku3d/plans/street-richness-plan.md`, measured on 2026-11-01. Five fixed street cameras,
a scorecard that reads only tile data, today's numbers on the published tiles, and the counts Jonni needs
to approve G1 (street furniture and lamps). No generator code was changed.

**Summary.** None of the five cameras has a lamp or a piece of street furniture within 50 m today, and
none has a parked car. Signs, trees and road paint are present. With G1 in place, three of the four
street cameras would pass the G1 scorecard check (at least 3 lamps and 2 furniture items within 50 m).
In the centre 3×3 tiles, G1 would add about 1,056 props, roughly 117 per tile, plus 315 register
trees. Its build-time cost is estimated at +0.3 to +0.7 s per tile.

**Frame cost today.** On this M1 Max at medium, the street cameras cost 4.5 to 11.6 ms of GPU time
per frame at p50 (p95 at most 15.2 ms). That is well inside the 33.3 ms frame of the 30+ FPS target.
Across both tiers, each frame submits 277 to 636 draws.

## Cameras

The cameras are defined in `game/src/qa/street-cameras.json`, which also records why each spot was
picked, its OSM way, its street stretch and its `?cam=` pose.

- **Position and view.** Each street camera stands on the street centreline, with the eye at ground
  + 1.7 m (ground taken from the published tile ground mesh). It looks along the block it stands in.
- **Conventions.** `yaw_deg` is the grid bearing (0 = north, engine −z), `fov` is vertical, and both
  follow the QA `?cam=` contract.
- **Use.** Each camera is used at 13:00 and 22:00, at medium and high quality.

| Id | Street (OSM way) | Engine pos | Yaw | Tile | Why |
|---|---|---|---:|---|---|
| riverside | Läntinen Rantakatu (4690693) | 174.21, 5.39, 258.17 | 62.9 | t0_0_1 | The centreline point nearest the address E 23459668 N 6704254 (13.7 m away). The camera looks upstream toward Auransilta. The stretch is the block from Kristiinankatu to Aurakatu (203 m). |
| shopping | Yliopistonkatu, pedestrian (4215942) | 17.61, 9.16, −105.9 | 236.0 | t0_0_-1 | The point with the most shops and cafés within 50 m (15) on the block from Aurakatu to Puolalankatu (101 m). Its 50 m disc crosses the x = 0 tile seam. |
| residential | Heikkilänkatu, Port Arthur (5034817) | −1064.91, 9.86, 317.01 | 146.7 | t0_-5_1 | The nearest residential street with mapped kerb parking (`parking:both=lane`, parallel) to the Port Arthur place node. The camera stands mid-block between Rauhankatu and Puutarhakatu (113 m). |
| park-edge | Puolalanpuisto (232702577) | −201.13, 26.36, −405.87 | 235.9 | t0_-1_-2 | The only car street that follows the Puolalanpuisto boundary for its whole length, with the park on the left and buildings on the right. The camera stands mid-block (165 m). |
| kupittaa | Kupittaanpuisto lit footway (112315737) | 1375, 19, 625 | 14.4 | t0_5_2 | The camera from `perf-kupittaa-check.md`, unchanged, with fov 65 and pitch −0.32°. The eye is 1.83 m above the 17.17 m ground. There is no car street within 60 m, so the per-100 m figures use the nearest lit path. No targets apply. |

The bench screenshots confirm each view. One adjustment is worth considering: at 13:00 the residential camera looks south-east almost straight into the sun (azimuth 176°), which washes out the right-hand facades. Turning it to yaw 326.7°, looking north-west towards Rauhankatu, would avoid the glare.

## How the scorecard counts

`scripts/dev/richness_scorecard.py` reads the same files the client streams: `<tile>/manifest.json`,
the visual GLB including its shared `.bin` buffers, `roads.json`, and the manifest's `vehicles`. It reads
them either from the published service with read-only GETs or from a local tile directory with the same
layout (`--tiles`, for example osgallery-dev `~/turku3d-local/tiles/`). The targets live in
`scripts/dev/richness_targets.toml`.

**Categories**

- **Lamps.** Props with `prop_type` lamp. Bridge-deck lamps are merged into the bridge furniture mesh,
  and tunnel strips are not street lamps, so neither is counted.
- **Furniture.** Prop kinds bench, bin, bike rack or station, parking meter, bollard and post. Today
  that means only the barrier layer's instanced bollards.
- **Trees.** Vegetation instances that are not bushes, deduplicated across their mesh parts.
- **Parked cars.** The manifest's `vehicles`.
- **Sign poles.** Props with `prop_type` sign or bus_stop.
- **Paint.** Road-paint triangles, in m² and in metres of a standard 0.12 m line.
- **Road length.** `roads.json` CAR edges.
- **Moving cars and walkers.** These read 0 until those systems exist. `--spawner-state` feeds their
  positions in.

**Areas and seams**

- **Two areas.** "Within 50 m" is a horizontal disc around the camera. "Per 100 m, both sides" is a
  flat-capped band of `half_width_m` (10 to 15 m) around the camera's street polyline, divided by the
  stretch length.
- **Seams.** Objects are deduplicated by identity across tiles. Paint and the street graph are clipped
  to each tile's own half-open square, so a feature on a seam is counted once.

## Baseline on the published tiles

Generation `28ba24e011a4a9c4`, all tiles full pass, `https://turku.lab.madekivi.fi/coarse/`.

Within 50 m of the camera:

| Camera | Lamps | Furniture | Trees | Parked cars | Sign poles | Paint (line m / m²) | Car road m | Moving cars | Walkers |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| riverside | 0 | 0 | 6 | 0 | 0 | 0 / 0.0 | 223 | 0 | 0 |
| shopping | 0 | 0 | 0 | 0 | 3 | 126 / 15.1 | 35 | 0 | 0 |
| residential | 0 | 0 | 5 | 0 | 2 | 0 / 0.0 | 140 | 0 | 0 |
| park-edge | 0 | 0 | 53 | 0 | 3 | 0 / 0.0 | 100 | 0 | 0 |
| kupittaa | 0 | 0 | 73 | 0 | 0 | 0 / 0.0 | 0 | 0 | 0 |

Per 100 m of street, both sides. Targets are in brackets; parked-car targets apply only where kerb
parking is mapped.

| Camera | Street (stretch m) | Lamps | Furniture | Trees | Parked cars | Sign poles | Paint line m | Moving cars | Walkers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| riverside | Läntinen Rantakatu (203) | 0 (5) | 0 (15) | 3.45 | 0 (n/a) | 1.97 | 0 | 0 | 0 |
| shopping | Yliopistonkatu (101) | 0 (7) | 0.99 (12) | 0 | 0 (n/a) | 0.99 | 0 | 0 | 0 |
| residential | Heikkilänkatu (113) | 0 (4) | 0 (6) | 0 | 0 (12–15) | 2.65 | 141 | 0 | 0 |
| park-edge | Puolalanpuisto (164) | 0 (3) | 0 (8) | 16.41 | 0 (n/a) | 2.43 | 0 | 0 | 0 |
| kupittaa | lit footway (120) | 0 | 0 | 14.17 | 0 | 0 | 0 | 0 | 0 |

**Observations**

- Every lamp and furniture target fails today.
- Paint coverage on t0_0_0 is 203.8 m², which is 1,698 line metres over 1.87 km of CAR edges, or about
  910 line metres per car km. Crosswalks dominate it.
- Heikkilänkatu is the only camera street with mapped kerb parking (`parking:both=lane`, parallel), yet
  t0_-5_1 holds 3 parked cars, all about 177 m from the camera. That is a lead for G2, not part of G1.

## Frame cost at the cameras (M1 Max bench)

**How it was measured**

- **Probe.** `?bench=camera&id=<id>&at=<HH:MM>&quality=<tier>` (`game/src/qa/bench.ts`) places the
  camera from `street-cameras.json` and renders through the QA path. It uses the play-mode pixel ratio
  with the adaptive scale fixed at 1.
- **Settle and window.** The probe waits until the resident geometries, textures and pipelines have not
  changed for 10 s and no download has been running for more than 1.5 s. It then warms 120 frames and
  measures 240.
- **Counters.**
  - GPU time is the whole-frame timestamp interval (`render/gpuTimer.ts`).
  - CPU time is the main thread's time per frame. With the frame rate unlocked the main thread also
    waits on the GPU queue, so CPU time tracks GPU time here and is an upper bound on CPU cost.
  - Draws are counted as they are submitted and attributed by render target. "Reflection" is the river
    reflector; "post" is GTAO, TRAA, bloom and output.
- **Driver.** `game/scripts/street-bench.mjs` served a production build next to a read-only proxy to
  the published tiles (generation `28ba24e011a4a9c4`). Chrome for Testing 156.0.8078.73 ran headless
  on Metal WebGPU at 1600×913, device pixel ratio 2, with vsync and the frame-rate limit off. The pixel
  ratio was 1.59 and the drawing buffer 2541×1450.
- **Runs.** Every configuration ran twice. Both GPU and CPU values are listed (r1 / r2).
- **Checks.** In all 42 runs the window held 240 GPU samples, settled before the 180 s limit, and
  created no pipelines while measuring.

**Caveats**

- **Shared host.** Other agents were working on this Mac throughout, with load averages between 9 and
  135, so the r1/r2 spread (up to about 40 %) is host noise. Draws, instances, triangles and pipelines
  are deterministic.
- **Night.** 22:00 renders today's unsupported night: the sun is hidden below −2°, so no CSM pass runs.
  Phase 1 replaces this look.
- **Resident tiles.** The QA path keeps 17 draft tiles resident: the camera's 3×3 window plus the ring
  the client prepares around it.

A screenshot of each camera, time and tier is kept locally, not in git.

| Camera | Time | Tier | Draws per frame: total (prepass / beauty / CSM / reflection / post) | Instances | Triangles | GPU p50 ms | GPU p95 ms | CPU p50 ms | Pipelines |
|---|---|---|---|---:|---:|---|---|---|---:|
| riverside | 13:00 | medium | 582 (155 / 159 / 164 / 89 / 15) | 2,627 | 3.55 M | 7.76 / 10.02 | 9.14 / 12.62 | 7.47 / 8.87 | 64 |
| riverside | 13:00 | high | 636 (157 / 161 / 214 / 89 / 15) | 2,739 | 3.76 M | 7.82 / 8.97 | 9.03 / 10.49 | 7.50 / 8.65 | 64 |
| riverside | 22:00 | medium | 418 (155 / 159 / 0 / 89 / 15) | 2,442 | 2.84 M | 4.78 / 6.19 | 6.45 / 7.41 | 4.67 / 5.87 | 59 |
| riverside | 22:00 | high | 422 (157 / 161 / 0 / 89 / 15) | 2,442 | 2.85 M | 5.40 / 5.68 | 5.72 / 6.94 | 4.77 / 5.83 | 59 |
| shopping | 13:00 | medium | 477 (132 / 132 / 198 / 0 / 15) | 1,265 | 7.81 M | 7.84 / 11.37 / 11.56 | 8.13 / 13.06 / 13.16 | 7.22 / 10.36 / 10.69 | 63 |
| shopping | 13:00 | high | 540 (131 / 131 / 263 / 0 / 15) | 1,330 | 9.43 M | 9.97 / 12.59 | 10.61 / 14.76 | 9.14 / 11.52 | 63 |
| shopping | 22:00 | medium | 279 (132 / 132 / 0 / 0 / 15) | 1,067 | 3.98 M | 6.44 / 8.09 | 7.44 / 10.27 | 5.92 / 6.78 | 46 |
| shopping | 22:00 | high | 277 (131 / 131 / 0 / 0 / 15) | 1,067 | 3.98 M | 7.54 / 8.18 | 7.86 / 9.08 | 6.75 / 7.34 | 46 |
| residential | 13:00 | medium | 501 (134 / 134 / 218 / 0 / 15) | 5,503 | 5.14 M | 6.67 / 7.20 | 7.96 / 8.45 | 6.41 / 6.85 | 59 |
| residential | 13:00 | high | 577 (139 / 139 / 284 / 0 / 15) | 5,625 | 5.90 M | 8.00 / 7.06 | 9.47 / 8.19 | 7.13 / 6.79 | 59 |
| residential | 22:00 | medium | 283 (134 / 134 / 0 / 0 / 15) | 5,285 | 2.55 M | 4.55 / 5.13 | 5.69 / 6.32 | 4.02 / 4.25 | 43 |
| residential | 22:00 | high | 293 (139 / 139 / 0 / 0 / 15) | 5,285 | 2.57 M | 5.43 / 5.96 | 6.63 / 7.67 | 4.91 / 5.38 | 43 |
| park-edge | 13:00 | medium | 533 (150 / 150 / 218 / 0 / 15) | 3,030 | 7.79 M | 8.08 / 8.64 | 8.89 / 10.04 | 7.03 / 7.62 | 64 |
| park-edge | 13:00 | high | 589 (151 / 151 / 272 / 0 / 15) | 3,249 | 8.46 M | 10.26 / 10.57 | 11.57 / 12.32 | 9.41 / 9.88 | 64 |
| park-edge | 22:00 | medium | 315 (150 / 150 / 0 / 0 / 15) | 2,485 | 4.23 M | 9.91 / 6.69 | 12.16 / 7.97 | 8.83 / 5.88 | 46 |
| park-edge | 22:00 | high | 317 (151 / 151 / 0 / 0 / 15) | 2,485 | 4.24 M | 8.41 / 7.17 | 9.67 / 8.18 | 7.78 / 6.71 | 46 |
| kupittaa | 13:00 | medium | 591 (140 / 142 / 132 / 162 / 15) | 9,272 | 3.50 M | 10.66 / 7.57 | 15.19 / 8.74 | 10.88 / 7.31 | 61 |
| kupittaa | 13:00 | high | 621 (140 / 142 / 162 / 162 / 15) | 9,695 | 3.78 M | 12.15 / 9.44 | 14.62 / 12.00 | 10.38 / 9.06 | 61–63 |
| kupittaa | 22:00 | medium | 459 (140 / 142 / 0 / 162 / 15) | 8,789 | 3.04 M | 8.28 / 5.22 | 10.85 / 6.68 | 6.03 / 5.06 | 56 |
| kupittaa | 22:00 | high | 459 (140 / 142 / 0 / 162 / 15) | 8,789 | 3.07 M | 11.18 / 6.39 | 14.09 / 8.17 | 9.73 / 5.84 | 56 |

**Notes on the table**

- **Shopping, 13:00 medium.** One run of the matrix settled while a cold tile was still downloading:
  33 draws and 18 pipelines. That run is replaced by two reruns (the second and third values), made
  after the probe learned to treat a running download as unsettled.
- **Kupittaa versus `perf-kupittaa-check.md`.** That report measured 109–145 draws and 2.6 ms of GPU
  time at the same camera. Today the same camera submits 591 draws. The difference comes mainly from
  three things:
  - 17 resident tiles here, against 9 there;
  - the water reflection: 162 draws, re-rendered every frame because the QA path is deterministic,
    whereas play skips reflection updates for a still camera;
  - today's tile content.

  The two sets of frame costs are not directly comparable.

## G1 approval request

`scripts/dev/g1_counts.py` reads only the raw data already on disk under `~/turku3d/data/raw`. It reuses
`turku3d.props.classify` and the existing 30 m lit-street rule (`LIT_STREETS`, `LAMP_SPACING`,
`LAMP_REACH`). It does not change the generator.

**Rules modelled**

- Register data comes first.
- The OSM fallback is applied per kind (`highway=street_lamp`, `amenity=bench`, `waste_basket`,
  `bicycle_parking`, `vending=parking_tickets`, `barrier=bollard`, `natural=tree`). It is dropped
  when the same kind already lies within 3 m.
- The 30 m `lit=yes` rule applies only where neither source has a lamp. The analysis counts this two
  ways:
  - **Per station.** No register or OSM lamp within 20 m, which is today's code.
  - **Per tile.** No lamp anywhere in the tile.

### Layers

| Layer | On disk | What it gives in the centre 3×3 |
|---|---|---|
| `GIS:Varusteet` | work-3x3 only, 1,243 points | Benches, bins and other register furniture. It has no lamp ("valaisin") rows. |
| `GIS:Polkupyoraparkki` | work-3x3 only, 139 | Bike racks |
| `GIS:Follariasemat` | work-3x3 only, 46 | Bike-share stations |
| `GIS:Pysakointiautomaatit` | work-3x3 only, 38 | Parking meters |
| `GIS:Puut` | work-3x3 only, 2,987 | Register trees |
| `frn:CityFurniture_LOD2` | **not on disk anywhere**, and not defined in `pipeline/turku3d/fetch/sources.py` | Would be the main lamp source (about 1,800 poles/km² per plan §1). Not fetched for this report. |
| OSM clips | All tiles | Lamps, `lit=yes` ways, fallback furniture and trees |

The register layers are **not on disk for t0_5_2 (Kupittaa) or t0_-5_1 (the residential camera)**.
Their coarse block folders hold only the six coarse WFS layers. Their figures are OSM plus the rule,
so they are lower bounds.

### Expected counts per tile

**Reading the table**

- "Props added" is lamps plus furniture.
- Bollards are left out because today's barrier layer already draws the OSM bollards.
- Per 100 m uses the OSM street centreline length in the tile, from primary to living_street plus
  pedestrian. Service roads are left out.

| Tile | Street m | Lamps: register / OSM / rule = total (per-tile rule) | Furniture: bench / bin / bike / meter / other = total | Furniture within 15 m of a street | Trees today + register | Props added | Per 100 m: lamps / furniture |
|---|---:|---|---|---:|---|---:|---|
| t0_-1_-1 | 688 | 0 / 4 / 24 = 28 (4) | 6 / 13 / 8 / 3 / 4 = 34 | 34 | 58 + 22 | 62 | 4.07 / 4.94 |
| t0_0_-1 | 798 | 0 / 13 / 16 = 29 (13) | 25 / 31 / 17 / 0 / 37 = 110 | 87 | 24 + 52 | 139 | 3.63 / 13.78 |
| t0_1_-1 | 876 | 0 / 11 / 30 = 41 (11) | 7 / 25 / 11 / 1 / 7 = 51 | 43 | 21 + 10 | 92 | 4.68 / 5.82 |
| t0_-1_0 | 908 | 0 / 1 / 30 = 31 (1) | 6 / 17 / 26 / 2 / 8 = 59 | 57 | 17 + 27 | 90 | 3.41 / 6.49 |
| t0_0_0 | 834 | 0 / 28 / 25 = 53 (28) | 12 / 35 / 13 / 2 / 13 = 75 | 56 | 39 + 36 | 128 | 6.35 / 8.99 |
| t0_1_0 | 1,223 | 0 / 14 / 27 = 41 (14) | 38 / 34 / 7 / 2 / 49 = 130 | 122 | 111 + 35 | 171 | 3.35 / 10.63 |
| t0_-1_1 | 887 | 0 / 25 / 21 = 46 (25) | 28 / 18 / 6 / 0 / 29 = 81 | 71 | 73 + 22 | 127 | 5.19 / 9.13 |
| t0_0_1 | 608 | 0 / 26 / 12 = 38 (26) | 39 / 22 / 10 / 0 / 37 = 108 | 71 | 385 + 40 | 146 | 6.25 / 17.77 |
| t0_1_1 | 818 | 0 / 1 / 27 = 28 (1) | 17 / 20 / 6 / 2 / 28 = 73 | 36 | 328 + 71 | 101 | 3.42 / 8.93 |
| t0_5_2 (Kupittaa) | 70 | 0 / 29 / 2 = 31 (29) | 36 / 12 / 0 / 0 / 0 = 48 | 1 | 443 + 0 | 79 | not meaningful: a park tile |

**Centre 3×3 totals**

- **Totals over 7.64 km of street:**
  - 335 lamps: 123 OSM and 212 from the rule. The per-tile rule gives only 123.
  - 721 furniture items: 178 benches, 215 bins, 84 bike racks, 20 bike-share stations, 12 parking
    meters and 212 other.
  - 1,056 props added.
  - 315 register trees, taking the trees in the window from 1,056 to 1,371. No tile comes near the
    768-tree cap.
- **Per 100 m:** 4.4 lamps (1.6 with the per-tile rule) and 9.4 furniture items, of which 7.6 stand
  within 15 m of a street.
- **The "other" furniture** (212) breaks down as 91 ash trays, 57 boards, 36 life-saving posts,
  15 tables and 13 others. The current `classify` maps all of them to the generic `post` model.
- **The 3 m rule removed:** 709 OSM trees, 117 OSM benches, 47 OSM bins, 40 duplicate bike racks and
  2 OSM lamps.

Through the per-tile G1 additions in `g1_counts.py`, today's parked cars and sign poles kept, the
scorecard at the cameras with G1 in place would read:

| Camera | Lamps 50 m | Furniture 50 m | Lamps / 100 m (target) | Furniture / 100 m (target) | G1 check (≥ 3 lamps, ≥ 2 furniture) |
|---|---:|---:|---|---|---|
| riverside | 9 | 16 | 7.89 (5, ok) | 17.27 (15, ok) | pass |
| shopping | 4 | 11 | 2.97 (7, below) | 11.87 (12, below) | pass |
| residential | 4 | 1 | 3.53 (4, below) | 0.88 (6, below) | **fail on furniture**: the register is not on disk there |
| park-edge | 10 | 8 | 6.08 (3, ok) | 3.04 (8, below) | pass |
| kupittaa | 2 | 3 | 1.67 | 0.83 | no targets |

The 30 m rule puts lamps on one side of a lit street only, so it gives at most about 3.3 lamps per
100 m. The shopping target of 7 per 100 m needs either `frn:CityFurniture_LOD2` or the G5 street
grammar.

### Cache-invalidation scope

G1 edits `coarse/sources.py`, `coarse/inputs.py` and `props/*`. All three are among the producer paths
in `coarse/packaging.py`, and none is visual-only in `coarse/collision_key.py`. So both the visual
generator code and the collision code change, and **every cached tile (about 2,900 on OCI) becomes
stale once**.

The per-stage content hashes keep the rebuild narrow. These stages re-run:

- **Prepared inputs.** `poles` (new raw paths and props code) and `trees` (when `GIS:Puut` feeds it).
- **Stage `props`.** Its key holds the props code, so it re-runs on every tile.
- **Stages `vegetation` and `parked`.** Only where their tree or pole input lists change.
- **`collision_<cell>`.** Only cells whose content changes: new lamp and bench primitives, and new
  tree trunks.
- **The visual package.** Re-runs wherever a layer changed.

Terrain, buildings, surfaces, markings, kerbs, bridges, tunnels, fences and building visuals stay cached.

Two follow-on effects:

- **Clients.** Collision cells whose bytes do not change keep their sha256, so clients do not fetch
  them again.
- **Fetching.** Adding the five layers to the required `WFS_LAYERS` would also change `input_key` for
  every tile, and would mark each 500 m block's `wfs` unit missing until the prefetcher has fetched it.
  The full pass would then wait for that fetch. Fetching the layers for all cached blocks *before* the
  code switch avoids a second rebuild and the wait.

### Build-time delta per tile

The steady-state cost is estimated at **+0.3 to +0.7 s per tile on this M1 Max**, within the plan's
2 s limit. Each figure below was measured on this Mac by calling the current, unchanged readers, or
taken from the local builds' `stage-times.json`:

| Part | Measured | Estimated delta |
|---|---|---|
| Pole input preparation | Today's coarse pole reader: 0.03–0.06 s warm. The full register reader on t0_0_0: 0.16 s cold, 0.06 s warm. Cold parquet loads of the five new layers: 3–6 ms each. | +0.03–0.13 s, once per tile (the prepared input is cached). |
| `props` stage (`visual_bytes`) | 1–3 ms for 84–238 props, against 5–110 today. | +0.002 s |
| Visual package | Props GLB grows by +44 to +126 KB raw, against a tile visual of 0.3–14 MB (about 1–2 %). Package takes 0.1–6.7 s locally. | ≤ +0.1 s (scaled with bytes) |
| `vegetation` | 0.2–1.2 s for 17–400 trees locally; G1 adds 10–71 register trees per tile. | +0.05–0.3 s (linear in trees) |
| `parked` | 0.2–1.7 s; the pole mask list grows by 60–170 points of 0.6 m buffers. | < +0.1 s |
| Collision cells | All 16 cells take 0.03–0.33 s in total. | < +0.1 s |

**One-off rebuild.** Local rebuilds of the same shape (vegetation, parked and package re-run, the rest
cached) took 2.9–10.1 s per tile. For about 2,900 OCI tiles on the 4-slot prebuild, that is roughly
1 to 2 hours if OCI runs at M1 Max speed. OCI timings are not measured here; the post-deploy run would
measure them.

### Frame budget (30+ FPS on the M1 Max at medium)

- **Draws.** `package/materials.mjs` merges all non-sign furniture into a single `props_palette`
  material, so G1 adds **at most one prop batch per tile**. That is +9 beauty draws plus the prepass in
  the 3×3 window, with shadows in the existing tile shadow batches.
- **Triangles.** The current prop models have 36 to 276 triangles each. G1 adds about 100 k triangles
  over the centre 3×3 window.
- **Against today's frame.** The street cameras already submit 477 to 591 draws and 3.5 to 7.8 M
  triangles per frame at medium by day, at 6.7 to 11.6 ms GPU p50 (see the bench section). G1's additions
  are at most one batch per resident tile in the prepass and in the beauty pass. With 17 resident tiles
  that is +34 draws (6 to 7 %), and about +0.2 M triangles (3 to 5 %), so the cameras stay far inside
  33.3 ms.
- **Measurement.** Rerun the bench at the same cameras after G1 for the real delta.

### Points to settle in the G1 implementation

1. **Lamp source.** Without `frn:CityFurniture_LOD2` the register contributes no lamps. The layer
   needs a fetch definition and a CityGML reader. Decide whether it is part of G1 or a G1b.
2. **OSM bollards.** Today's barrier layer already draws them. Once the coarse kind filter at
   `inputs.py:609` goes, `osm_props` would add them a second time as props. Exclude them from props.
3. **Varusteet "post" rows.** The 212 ash trays, boards, life-saving posts and tables should either
   get their own kinds and models or be skipped. They would otherwise all render as the generic post.
4. **Repeated register ids.** 13 Varusteet rows share an `Id`, and `read_sources` deduplicates on that
   id, so it would drop 11 real items. Key on `feature_key` and leave duplicates to the 3 m rule.
5. **Lit rule interpretation.** Choose per station (today's 20 m reach: 335 lamps in the 3×3) or per
   tile (123 lamps). This report recommends per station.
6. **Rebuild window.** This phase is local-only, so build G1 first on the local tiles on osgallery-dev
   (`~/turku3d-local/tiles/`). Compare with `richness_scorecard.py --tiles` and with this report's
   per-tile counts. Production rollout is a separate decision, outside freeze windows.

## Reproduce

```sh
python3 scripts/dev/richness_scorecard.py --cache /tmp/rs --json /tmp/rs/baseline.json --markdown /tmp/rs/baseline.md
python3 scripts/dev/richness_scorecard.py --tiles ~/turku3d-local/tiles     # local copies, same layout
bash pipeline/env/run.sh python scripts/dev/g1_counts.py --cache /tmp/rs --json /tmp/rs/g1.json --markdown /tmp/rs/g1.md
python3 scripts/dev/tests/run.py test_richness_scorecard.py

# Bench (own ports, never 5171/5175): build into a private work root, then run the matrix
cd game && TURKU3D_WORK=/tmp/rs/work node scripts/build.mjs
TURKU3D_WORK=/tmp/rs/work node scripts/street-bench.mjs --dist /tmp/rs/work/build/game --out /tmp/rs/bench \
  --port-base 5480 --repeat 2            # or --cameras riverside --at 13:00 --quality medium
# Manual: open <game>/?bench=camera&id=riverside&at=13:00&quality=medium and copy the JSON it shows
```
