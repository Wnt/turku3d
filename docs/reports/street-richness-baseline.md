# Street richness baseline and G1 approval request

Phase 0 of `~/turku3d/plans/street-richness-plan.md`, measured on 2026-11-01. Five fixed street cameras,
a scorecard that reads only tile data, today's numbers on the published tiles, and the counts Jonni needs
to approve G1 (street furniture and lamps). No generator code was changed.

**Summary.** None of the five cameras has a lamp or a piece of street furniture within 50 m today, and
none has a parked car. Signs, trees and road paint are present. With G1 in place, three of the four
street cameras would pass the G1 scorecard check (at least 3 lamps and 2 furniture items within 50 m).
In the centre 3×3 tiles, G1 would add about 1,056 props, roughly 117 per tile, plus 315 register
trees. Its build-time cost is estimated at +0.3 to +0.7 s per tile.

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
  over the centre 3×3 window. For comparison, the Kupittaa frame totals about 0.5 M triangles and
  costs 5.9 ms of work on the M1 Max, so G1 fits comfortably within 33.3 ms.
- **Measurement.** The bench probe, or Jonni's manual run at these cameras, gives the real number.

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
```
