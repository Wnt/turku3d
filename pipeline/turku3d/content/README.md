# Gameplay content from published layers

## Delivery address pool (`addresses.py`)

The tile build (`make tile-build`, `turku3d.package.assemble`) derives the pizza delivery pool from
the buildings layer and the road graph on every run, so a new tile adds its addresses without a
code change. The pool is published beside the road graph as the content-addressed
`dist/tiles/addresses/<sha256[:16]>.json` and recorded as `manifest.addresses` (`url`, `sha256`,
`bytes`, `count`). Like the road graph it is not part of tile cache keys.

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_addresses.py   # synthetic layer, < 1 s
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"                                  # publishes the pool
```

Sources, per address:

1. Every building in `buildings/metrics.json` whose `semantics.address` (city register or OSM) is
   set, and every city register address point (`raw/wfs/*/GIS_Osoitteet.parquet`) on or within
   30 m of a footprint; corner buildings get one entry per street address.
2. Street-side walls: a ground-floor wall is street-side when the walkable network crosses the
   strip it sweeps along its outward normal, from 2 m behind the wall's line (OSM traces sidewalks
   along the facades) to 12 m in front of it, or when `facades.geojson` marks it `street_facing`.
3. The door: walls that face the address's own street come first. That street is the address
   without its house number, matched against the named OSM highway centre lines (`name`) of the
   road graph's OSM snapshot (`roads/MANIFEST.json` `inputs.osm.path`); a wall faces it when that
   centre line crosses its front strip within 25 m and the walkable network is in front of it.
   Within that group, then among the other street-side walls: the best OSM entrance (`main`,
   `shop`, `yes`/`home`, `secondary`, `staircase`; never `garage`, `service` or `exit`) on the
   wall, near the register point when there is one; otherwise the wall nearest to the register
   point, or the widest wall.
4. The drop-off is 2.5 m in front of the door (4 m or 1.5 m when that spot is inside a footprint).
   A door with no spot outside every footprint, such as one on a wall shared with a neighbour, is
   skipped for the next one. The drop-off must lie within 40 m of the walkable network, in one of
   the area's L0 tiles and at least 60 m from the pizzeria by route.
5. `foot` and `car` are shortest road-graph paths from the pizzeria's snap point to the drop-off's
   (car oneways honoured; `car` is null when no drivable edge is within 80 m or no path exists).

Several addresses of one building with the same best door share a drop-off point. The `id` is the
ASCII slug of the address (`lantinen-rantakatu-13a`), stable across rebuilds, so saved games keep
their delivered set.

```json
{"format": "turku3d-addresses", "version": 1, "frame": "engine-xz", "origin": [77.1, 187.6],
 "tiles": {"t0_0_0": 31, ...},
 "addresses": [{"id", "address", "building", "tile", "point": [x, z], "door": "entrance" | "wall",
                "street": [x, z], "foot": metres, "car": metres | null}, ...]}
```

`origin` is the pizzeria (`PIZZERIA`, the same point as `game/src/gameplay/content/locations.ts`);
the game rejects a pool built from another origin. Addresses are sorted by `foot`.
