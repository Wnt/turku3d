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
  (`ground.water.add_river_bed`).
- **Bridges**: `maanpinta` has no points on a deck, so a street-area bridge (`Silta`) over water would lie at river
  level between quay-high road ends. `bridge_decks` lifts each deck patch that touches water to the line between
  its abutments (the 90th percentile of the dry ground 5–25 m beyond each end, inside the deck's corridor), raising
  the low approach cells in the corridor with it; decks over dry land stay put. A water polygon under a deck never
  turns it into the sensor. Tuomiokirkkosilta (`t0_2_-1`) now spans the Aura at 6.8–7.0 m instead of 0.2 m.
- **Buildings**: LOD2 footprints whose centroid is in the tile, extruded to the mean roof height; the collision shell
  goes to the centroid's collision tile.

No curbs, stairs, structures, props, vegetation, facade maps or LOD parents.

## Service (`service.py`)

`tools/dev/coarse-tiles` (localhost only, port `$TURKU3D_PORT_BASE + 5`, proxied at `/coarse/` by the dev and dist
servers). Up to four builds run at once (`WORKERS`), nearest to the player first; the game requests the cells
ahead of a moving player as well as the 3x3 window. Routes and queueing: `tools/dev/README.md`. Built tiles are
reused until the producer code changes, so a producer fix rebuilds each draft the next time the game asks for it.

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

## Prefetcher (`prefetch.py`)

The game sends `GET /coarse/want?x=&z=&vx=&vz=` once a second (engine metres and m/s). The
prefetch area is a 500 m ring around the player plus a 60° cone along the velocity, 500 m deep
on foot (up to 6 m/s) and growing linearly to 1.5 km at the car's top speed (27 m/s). Every
missing unit whose block intersects the area is queued: all lidar first (it gates ground and
collision), then ortho, then WFS, then OSM, nearest block first within a dataset. Units a waiting build
needs jump the queue. Two downloads run at once, each in a child process under `nice -n 10`
(`python -m turku3d.coarse fetch <unit>`); a unit on disk is never fetched again, a failed one
is retried after two minutes. `/coarse/status` reports under `prefetch` the queue, the downloads
in flight, per dataset the units, bytes and seconds fetched, the most recent units and the cache.

A tile requested while its units are missing answers `202 {"state": "pending-data", "missing":
[...]}` immediately; other tiles keep building meanwhile, and the tile is queued again as soon as
its last unit lands.

## Disk bound

Every fetched unit is recorded in `raw/prefetch/ledger.json` (paths, bytes, fetch time, last use).
After each landing the ledger keeps the newest 20 GB of prefetched units and deletes the least
recently used ones, together with their fetch-registry records (`MANIFEST.jsonl`, `SHA256SUMS`).
A unit is used when a plan includes it again. Files the prefetcher did not fetch, units being
fetched or planned, and blocks under a listed playable tile's input area are never pruned.
