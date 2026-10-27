# Coarse (draft) tiles

One 250 m L0 tile in the published tile format, built in seconds from the fast sources, for the localhost game beyond
the published tiles (`game/src/world/coarseTiles.ts`). The full pipeline replaces a draft when the tile is published.

```sh
bash pipeline/env/run.sh python -m turku3d.coarse build t0_2_0           # raw cache only; exit 3 = pending
bash pipeline/env/run.sh python -m turku3d.coarse build t0_9_0 --fetch   # hand run: fetch what is missing
bash pipeline/env/run.sh python -m turku3d.coarse fetch lidar/23460000_6704500   # one prefetch unit
tools/dev/coarse-tiles                                                   # service, port base + 5
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse.py pipeline/tests/test_coarse_prefetch.py
```

## Output (`build.py`)

`<out>/manifest.json` (format `turku3d-coarse-tile` v1: `visual`, `collision`, `roads`, `buildings`, `bridges`,
`water_level`, `sources`, `seconds`, and `code`, the producer hash the service compares), `vis.glb`,
`col/c_i_j.col` for all 16 collision cells and `roads.json`; shared KTX2 textures go to `<out>/../shared/`.

- **Streets** (`roads.json`, `manifest.roads` = `{url, sha256, bytes, nodes, edges}`): the tile's OSM street graph
  in the published format (`turku3d.roads`, `turku3d-roadgraph` v1), clipped to the tile and keeping every
  connected piece (`build_graph(..., connected=False)`), so the game joins it to its neighbours' graphs at the
  tile edges. OSM comes from work-3x3 when it holds the tile, else `tile-<id>`, the older per-tile clip
  `coarse-<tile>`, or the `osm` prefetch unit of the tile's block (below).

- **Ground**: a 128 x 128 cell grid (~2 m) interpolated from the key points, textured with the ortho. Visual and
  collision use the same vertices, so the drawn ground is the collision ground. Street-area polygons set the
  collision material; green ortho pixels are grass, the rest paving.
- **Water**: cells inside water polygons become the water sensor at the shoreline level over a synthetic bed
  (`ground.water.add_river_bed`), and are drawn as a flat surface at that level (`material_key` water, which the
  game renders as a river) instead of the ortho.
- **Bridges**: `maanpinta` has no points on a deck, so a street-area bridge (`Silta`) over water would lie at river
  level between quay-high road ends. `bridge_decks` lifts each deck patch that touches water to the line between
  its abutments (the 90th percentile of the dry ground 5–25 m beyond each end, inside the deck's corridor), raising
  the low approach cells in the corridor with it; decks over dry land stay put. A water polygon under a deck never
  turns it into the sensor. Tuomiokirkkosilta (`t0_2_-1`) now spans the Aura at 6.8–7.0 m instead of 0.2 m.
- **Buildings**: LOD2 footprints whose centroid is in the tile, extruded to the mean roof height; the collision shell
  goes to the centroid's collision tile.

- **Trees**: OSM `natural=tree` points on dry ground outside the buildings, drawn with the vegetation layer's
  GPU-instanced prototypes (10 m unless `height` is tagged, conifers by `leaf_type=needleleaved`); each trunk is a
  `tree_trunk` cylinder in its collision tile. They are unmeasured stand-ins for the full layer's register and lidar
  trees.

- **Barriers** (`fences.py`, `manifest.barriers`, `seconds.fences`): OSM fence/wall/retaining wall/city wall,
  hedge, guard rail, handrail and bollard lines/points, including tagged area outlines. Tagged height/material/
  fence type selects cheap chain-link (alpha mask), wood, bars, stone/brick, hedge or steel prototypes (at most nine
  instanced draws per tile). Segments follow grid heights, split at corners, 62.5 m cell edges and every 2.5 m;
  matching solid boxes use the existing fence/wall/railing/vegetation/bollard materials. Gates leave openings
  (3 m for an unmeasured point gate). Street-area crossings are clipped with width clearance, except deliberately
  tagged road barriers and bollards on OSM road nodes. Bollard ways place separate posts, 2.5 m apart. The prepared
  `barriers` dataset reuses the existing optional OSM extract; no downloads or runtime game changes.
  Run `bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_fences.py` for synthetic placement
  and road-clipping checks. `manifest.barriers.seconds` records the extra producer seconds on each built tile.

No curbs, stairs, other structures, props, facade maps or LOD parents.

## Service (`service.py`)

`tools/dev/coarse-tiles` (localhost only, port `$TURKU3D_PORT_BASE + 5`, proxied at `/coarse/` by the dev and dist
servers). Up to four builds run at once (`WORKERS`), the ring first, then the path ahead; the game requests the cells
ahead of a moving player as well as the 3x3 window. Routes and queueing: `tools/dev/README.md`. Built tiles are
reused until the producer code changes, so a producer fix rebuilds each draft the next time the game asks for it.
On main the localhost game service runs it and restarts it when a landing changes a file it loaded
(`tools/dev/localhost/README.md`): `/coarse/status` reports `started` and `modules` (the repository files the process
imported), and `POST /coarse/drain` stops new builds so the running ones finish first.
Payload requests (`.col`, GLB and roads) use the immutable in-memory servable set: a successful build or manifest
request admits a tile, and a manifest request that finds it stale removes it. They never recheck manifests or raw
inputs and hold no builder lock while reading or sending bytes. `/coarse/want` returns `queued` and `building`
(running tile ids), so the badge counts only queued and running builds, including builds finishing after a turn.

## Inputs (`sources.py`, `inputs.py`)

A build never downloads. It reads the shared raw cache under `$TURKU3D_DATA/raw` and, when
something is missing, exits with code 3 and `{"pending": [units]}`. Inputs are organised in
*units*: one dataset over one 500 m block of the city lidar grid (four L0 tiles), named
`<dataset>/<block>`, for example `ortho/23460000_6704500`.

| Dataset | Unit files under `raw/` | Typical size |
|---|---|---|
| `lidar` | `laz/maanpinta/<block>.laz` (ground key points; a tile reads every block under it + 20 m) | 0.8–1.6 MB |
| `ortho` | `ortho/ortho2022to/coarse-<block>{/,.vrt,.chunks.json,.summary.json}`: one 512 px chunk per L0 tile | 2.3–5.9 MB |
| `wfs` | `wfs/coarse-<block>/`: `bldg:Building_LOD2`, `GIS:Katualueet`, `kanta:Vesikohde` | 3–7 MB |
| `osm` | `osm/coarse-<block>/`: an osmium clip of the cached Finland snapshot (street graph; local) | |

Any prepared area that covers a tile serves it as well (`work-3x3`, `tile-<id>`, the older
per-tile `coarse-t0_i_j` areas). The disk checks read file stats and the small chunk and summary
JSON files (re-listed at most once a second), so the service runs them on every request. Outside
the city lidar index (`data-manifests/laz-tiles.csv`) a tile fails with a reason: the MML 2 m DEM
needs an API key, which this project does not use.

### Prepared inputs

Each dataset is reduced once per tile to what the build needs and cached under
`<coarse root>/.inputs/<tile>/<dataset>.<key>.pkl`: `ground` (key points and the 129 x 129 grid
heights over their TIN), `ortho` (512 px RGB), `wfs` (building shells, street polygons with their
collision material, water polygons), `roads` (`roads.json` of the tile's OSM street graph) and
`trees` (the tile's OSM `natural=tree` points). The
key covers `inputs.py` and the raw files' path, size and mtime, so new data or code prepares again.
The prefetch workers prepare the complete tiles within 1 km of the player in the background
(`python -m turku3d.coarse prepare <tile> --root <coarse root>`), so a build reads a few small
pickles instead of decoding LAZ, 5 cm ortho mosaics and whole-area GeoParquet files, and imports
neither geopandas nor rasterio nor laspy. The draft ortho is encoded with the fastest ETC1S effort
(`draftTextures` in the visual packager request, `--clevel 0`). The service builds on persistent
workers (`python -m turku3d.coarse worker`, one per build slot) that keep the producer imported and
one `node visual.mjs --serve` packager running, so a tile pays neither interpreter nor node start-up
(each about 0.3 s CPU and, on a host at load 250, 3-5 s of wall time).

## Prefetcher (`prefetch.py`)

The game sends `GET /coarse/want?x=&z=&vx=&vz=` once a second (engine metres and m/s). The
prefetch area is a 500 m ring around the player plus a 40° cone along the velocity, 500 m deep
on foot (up to 6 m/s) and growing linearly to 1.5 km at the car's top speed (27 m/s). Every
missing unit whose block intersects the area is queued band by band of 500 m of how soon the
player reaches it (moving: the distance along the path plus twice the distance beside it): within a
band lidar first (it gates ground and collision), then ortho, WFS and OSM, nearest block first.
Units a waiting build needs jump the queue. OSM is optional: a draft built before its clip lands has
no streets and is rebuilt with them afterwards (its manifest's input identity changes). One OSM clip
covers a 2 km square, since a clip mostly reads the Finland snapshot (about two minutes of CPU at
any size). Two downloads run at once, each in a child process under `nice -n 10`
(`python -m turku3d.coarse fetch <unit>`); a unit on disk is never fetched again, a failed one
is retried after two minutes. `/coarse/status` reports under `prefetch` the queue, the downloads
in flight, per dataset the units, bytes and seconds fetched, the most recent units and the cache.

A tile requested while its units are missing answers `202 {"state": "pending-data", "missing":
[...]}` immediately; other tiles keep building meanwhile, and the tile is queued again as soon as
its last unit lands.
Once a build succeeds or a request confirms the tile is built, its pending-input summary is cleared.

## Measuring a drive (`drive.py`)

`python -m turku3d.coarse.drive` replays the game hook's requests without a browser against a
running service: once a second `/coarse/want` with the position and velocity, then the polls of the
3x3 window and of the cells 30 s ahead. It prints, for the tiles that entered the window after the
first kilometre, how many were built and how long after entering, and the raw MB fetched per km.

```sh
tools/dev/coarse-tiles &   # with an empty $TURKU3D_WORK/coarse for a cold run
bash pipeline/env/run.sh python -m turku3d.coarse.drive --port $((TURKU3D_PORT_BASE+5)) \
  --start=0,125 --velocity=-27,0 --seconds 200 --log "$TURKU3D_WORK/reports/drive.jsonl"
```

Measured on 2026-10-27 at 27 m/s over 5.4 km from the core, host load 200-290 (on 10 cores):

| Drive | Tiles after 1 km | Built during the drive | Under 10 s | Longest wait | Mean build | Raw MB/km |
|---|---|---|---|---|---|---|
| west, final code | 54 | 54 | 54 | 2.1 s | 3.4 s | 26.5 |
| south, persistent workers | 47 | 47 | 46 | 12.0 s | 5.4 s | 26.5 |
| south-east, final code | 55 | 24 | 14 | 39 s | 8.5 s | 16.8 |
| south-west, before the workers | 65 | 30 | 1 | 35 s | 13.5 s | 40.3 |

Through the dense south-east the two downloads fall behind after about 2.8 km (a WFS block takes
5-8 s, an ortho block 4-6 s at that load), so tiles wait for data there.

## Disk bound

Every fetched unit is recorded in `raw/prefetch/ledger.json` (paths, bytes, fetch time, last use).
After each landing the ledger keeps the newest 20 GB of prefetched units and deletes the least
recently used ones, together with their fetch-registry records (`MANIFEST.jsonl`, `SHA256SUMS`).
A unit is used when a plan includes it again. Files the prefetcher did not fetch, units being
fetched or planned, and blocks under a listed playable tile's input area are never pruned.

## Shared facade floor

Draft walls use a 28-module, content-addressed procedural facade atlas (albedo, normal and ORM),
rendered once with `turkutex.facade_fast_render` and reused through `shared/`. Per-building colour,
bay spacing and storey height cost only parameters; roofs retain the ortho. The game samples the
upper bay repeatedly and the ground-floor row only at the base on both renderer backends. Published
walls without an approved generated, derived or fast map use the same kit. Pure mapping checks:
`PYTHONPATH=pipeline:textures .venv/bin/python -m pytest pipeline/tests/test_facade_kit.py -q`.
