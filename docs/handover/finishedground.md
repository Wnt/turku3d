# Handover: finishedground (construction-era LiDAR ground replaced by the finished surface)

2026-11-05, native Claude agent, branch `local/finished-ground`. Jonni: "The market square must have been under
renovation during the LiDAR capture … deep ditches around the main streets of it. We need to flatten this out and
make it respect the real, finished state."

Merged: integration ee964b27 (`finished-ground@1`, in train5) and ee8cc54d (`finished-ground@2`, the post-pass
spot fix).

## Finished-state sources

| Source | Date | Resolution | Use |
|---|---|---|---|
| City lidar `raw/laz/maanpinta` (class 8 key points) | 2021-04-17 | ~1 key point per 90 m² on flat ground, dense at breaks | the ground being corrected |
| `GIS:Katualueet` `Valmistumisvuosi`, `Peruskorjausvuosi` (also `Olotila`, `Pintamateriaali`) | register, current | polygons | **finished-state zones**: parts completed or renovated in 2021 or later. Kauppatori and its streets: 2021–2022; Itäinen Rantakatu: 2024 |
| `kanta:Korkeuspiste` spot heights with `alkuPvm` | 2021-08 … 2023 (as-built) | points, cm | **finished heights**: seeds and soft constraints of the fill. 1840 of 1910 farm WFS areas have them |
| `kanta:Liikennevayla` / `Rakenne` / `Kevyenliikenteenvayla` `alkuPvm` | redrawn 2022-11 … 2024 | lines | confirm the as-built kerbs and walls round Kauppatori; not used by the rule |
| `bldg:Building_LOD2` GroundSurface | mixed | polygons + one base height | footprints only (free cells in the fill, walls in the detection). Base heights are unreliable: 5.5 m next to 0.00 and 2.79 m on the same street |
| `tran:Road_LOD3` draped road TIN | `creationDate` 2009, draped on the 2021 lidar | TIN | **not finished state**: it matches the 2021 lidar in changed Kauppatori cells (median 0.11 m), trenches included |
| MML DEM 2 m (Funet `dem2m_direct.vrt`, sheet L3324B) | scanned 2009 (`laserkeilaus/2008_latest/2009/L332`) | 2 m | pre-renovation, older than the city lidar; not used |
| Ortho 2022 true ortho, 2025 (office block only), drone | 2022 / 2025 / mixed | 5 cm | visual check only. In 2022 the square's east edge is still a building site |
| OSM `surface`, `construction=*`, `highway=construction` | 2026-10-24 extract | – | no construction tags at Kauppatori; not used |
| `GIS:Liikennemerkit` | – | points | no date fields; not used |

## Rule (`pipeline/turku3d/ground/finished.py`)

1. **Zones.** Katualueet parts with `max(Valmistumisvuosi, Peruskorjausvuosi) >= 2021`. Water (`kanta:Vesikohde`)
   is never a zone. Stairs, bridges, bridge ramps, tunnels and underpasses keep their relief. So do the first 40 m of
   every street part that touches a tunnel, underpass or bridge ramp, plus 3 m around all of these (@2).
2. **Construction ground.** On a 1 m TIN of the key points, inside a zone, ground counts as construction-era when it
   is one of:
   - more than 0.4 m below its grey closing (1/2/4 m rasters, radii 4–40 m). A closing leaves steps and planes alone.
   - more than 0.4 m above its opening at radius 3 m (spoil heaps).
   - more than 0.4 m off a post-lidar spot height within 12 m.

   A building stands at the median ground of a 6 m ring round it, so an excavation against a facade is a pit. Moored
   LoD2 ships are no walls. A site needs at least 8 m² of construction ground. It grows 2 m, then up to 5 rounds of
   15 m through street space (any street part plus 6 m). In each round it takes ground that is depressed and lies more
   than 0.4 m below the site's own thin-plate continuation (buildings at wall height in that trial).
3. **Finished surface.** A least-squares thin-plate fill with membrane tension 0.3 runs over the site and every
   footprint touching it (no ground is observed under a building). It passes through the spot heights (weight 3) and
   meets the surrounding lidar. Everything else keeps its lidar.
4. **Seams.** Canonical windows (one level-0 tile + 25 m band + 60 m pad, from complete source rows, deduplicated by
   feature key and geometry) are blended by a partition of unity. Seam vertices of neighbouring tiles match to
   2e-15 m. Solved windows are cached by evidence digest (`$TURKU3D_SHARED_CACHE` or `$TURKU3D_WORK/cache`
   `/finished-ground/<code>/`).
5. **Generator.** The `finished_ground` dataset in `coarse/inputs.py` reads Katualueet, Korkeuspiste, LoD2,
   Vesikohde and key-point blocks over the tile ± 345 m. `with_finished_ground` replaces the ground key points in
   changed ground with a 2 m lattice before terrain, collision, kerbs, lane heights and placement run.
   - The kerb seam sampler of a changed tile reads its own finished points across the seam. It never prepares its
     neighbours' finished ground: that hung the tunnels stage until the farm's 1800 s timeout.
   - Street profiles touching finished ground are dropped.
   - `manifest.finished_ground` (non-public) has the report; `manifest.finished_ground_rule` is 2.
6. **Served tiles.** `finished-ground@N` is a `Rebuild`. It rebuilds a tile whose ground an earlier rule changed
   (`finished_ground` non-null) or whose ground this rule changes, computed by the same dataset function on the base's
   source version and cached as the generator's prepared input. Every other tile keeps its payloads byte for byte.

## Evidence (local, not in git)

Scratch: `/private/tmp/claude-501/-Users-wnt-Documents-game-test/6ca29930-fe72-4a33-a5d4-16a89f9c9d99/scratchpad/fg/`.

- **Served rebuilds.** Test alias `finishedground-m5`, rebuild revision `8ffc21eb` (rule @1), compared with train4 by
  `verify.py` (collision floor along profiles):
  - **t0_0_-1, Kauppatori: PASS.**
    - The floor follows the expected surface: median 3 mm off; p95 0.32/0.02/0.005 m.
    - Trench floors 0.58/1.14/1.63 m → 5.18/3.66/6.19 m.
    - Largest 1 m step on a profile: 1.34/2.40/0.71 m → 0.45/0.16/0.02 m.
  - **t0_1_-1, Eerikinkatu excavation east of the square: PASS.** Excavation floor 1.67–2.63 m → 5.24–5.98 m;
    median 2–5 mm off expected.
  - t0_0_0 also built. t0_-1_-1 hit the farm timeout under load and was not retried.
- **Spot heights in changed ground, t0_0_-1.**
  - Rule as run: median error 0.34 → 0.03 m, max 3.66 → 0.23 m.
  - Hold-out without spot constraints: median 0.25 → 0.16 m, max 3.66 → 1.03 m.
- **Profiles:** `kauppatori_profiles.png` / `.json` (three lines across the trenches and the excavation).
- **Hillshade before/after with change overlay:** `t0_0_-1_before_after.png`, `t0_1_-1_before_after.png`,
  `t0_0_1_before_after.png` (quay), `t0_-1_-4_before_after.png`, `t0_5_0_before_after.png`.
- **Renders on the Air:** `render/shots/<view>-before.png` / `-after.png` (overview from Jonni's camera, top-down,
  four street-level views); `render/render.mjs`.
- **Drive sweep** (`tools/drivetest` on structures.json of Eerikinkatu, Aurakatu, Kauppiaskatu, Yliopistonkatu,
  Linnankatu; 53 lanes; after = the rebuilt tiles mixed with train4 elsewhere): `drive/before`, `drive/mixed`.
  - All lanes: fail 25 → 20, stuck 30 → 17, wheel-lift lanes 21 → 9.
  - Lanes in rebuilt tiles (34): stuck 21 → 10, wheel-lift 19 → 7, vertical acceleration median 7.3 → 2.3 m/s²,
    max 88 → 67.
  - Remaining stuck lanes: the Aurakatu/Yliopistonkatu NW corner (its site continues into the unbuilt t0_-1_-1), and
    Kauppiaskatu SE and Yliopistonkatu with low vertical acceleration (obstacles on the lane, not ground).
- **City survey** (`python -m turku3d.ground.finished survey`). The 553 tiles a zone can reach out of the whole city
  (2903 zone polygons among 35,495 street parts; every other tile is unchanged by construction).
  - Done: 96 of those tiles (centre first) before the farm host had to be freed. 83 changed, 396,249 m² in all,
    including ground under buildings that touch a site.
  - 34 sites of at least 500 m² (`out/city.jsonl`, `out/city-partial.sites.json` on osgallery-dev under
    `~/turku3d-local/finishedground/`).
  - The one false positive found, the Myllyahde tunnel approach (up to 9.4 m), is fixed in @2.
- **Unaffected tiles are byte-identical by construction.** `apply` returns the same key-point array when no zone
  reaches the tile, and the Rebuild leaves its payloads alone. The control tile t0_3_3 came out unchanged, and the
  results are identical on the Mac and osgallery-dev.

## Open

1. The rest of the city survey (457 tiles, about 20 CPU-s each): run it on the 64-core host with the command below and
   `--tiles-file` of the zone-reachable tiles (`candidates.py` in the scratch folder).
2. Lane obstacles at Kauppiaskatu SE (E 23459784 N 6704557) and Yliopistonkatu (E 23459690 N 6704715): stuck with low
   vertical acceleration. Props or barriers on the lane, not ground.
3. Construction-era ground outside street parts (patrol: Linnakaupunki pit, E 23457591 N 6703377): out of the rule's
   scope. It needs plot-level finished-state evidence.

## Commands

```sh
python -m turku3d.ground.finished survey OUT.jsonl --jobs N [--tiles-file FILE] [tiles ...]
bash pipeline/env/run.sh python -m pytest -q -n 6 pipeline/tests/test_finished_ground.py   # ≈10 s
```
