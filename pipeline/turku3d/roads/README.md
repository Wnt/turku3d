# Road graph layer

Builds the street routing graph used by the in-game minimap and route guidance
(`game/src/gameplay/router.ts`) from the read-only OSM snapshot
`$TURKU3D_DATA/raw/osm/work-3x3/work-3x3-261024.parquet` (`geometry_local` is already in engine x/z).

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.roads          # playable-tiles: engine x/z 0..500 m
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_roads.py
make refresh                                               # rebuilds it when code or OSM changes
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"            # publishes it with the tiles
```

Output: `$TURKU3D_WORK/build/layers/roads/{roadgraph.json,MANIFEST.json}`. The manifest has no
tiles; `params.graph` records the graph file's SHA-256 and size, plus node/edge counts and the
walkable/drivable kilometres. The real playable area currently gives about 830 nodes,
1,000 edges, 16 km walkable and 5 km drivable in 35 kB (14 kB gzip).

## Graph

Ways are clipped to the area core. Nodes are way endpoints, clip points and vertices visited by
two or more ways or twice by one way. Shared OSM nodes project to identical coordinates, so
coordinates rounded to 0.1 m identify them; ways that only cross (bridges over streets) stay
unconnected. Each mode keeps only its largest connected network, so a snap never lands on an
isolated courtyard path or a car-only fragment behind a pedestrian street.

```json
{"format": "turku3d-roadgraph", "version": 1, "frame": "engine-xz", "area": "playable-tiles",
 "bbox": [xmin, zmin, xmax, zmax], "bits": {"foot": 1, "car": 2, "oneway": 4, "class_shift": 3,
 "classes": ["path", "service", "street", "major"]},
 "nodes": [x0, z0, x1, z1, ...], "edges": [[a, b, flags, length_m, x, z, ...], ...]}
```

An edge row holds its end node indices, flag bits, polyline length and interior vertices.
`oneway` means car travel only from `a` to `b`; `oneway=-1` ways are reversed. Flags:

| OSM `highway` | foot | car | class |
|---|---|---|---|
| motorway, trunk (+ links) | no | yes | major |
| primary, secondary (+ links) | yes | yes | major |
| tertiary, unclassified, residential, living_street | yes | yes | street |
| service | yes | yes | service |
| footway, path, pedestrian, steps, cycleway, track, bridleway, platform | yes | no | path |

`access=no|private`, `area=yes` and indoor ways (corridors) are excluded; `foot=no` and
`motor_vehicle`/`motorcar`/`vehicle=no` clear the mode; `junction=roundabout` implies oneway.

Draft tiles (`turku3d.coarse`) reuse `osm_ways` and `build_graph(..., connected=False)` to emit one graph per
250 m tile that keeps every piece; the game merges them with the published graph.

## Publication

The assembler (`turku3d.package.assemble`) copies the verified graph to the content-addressed
`dist/tiles/roads/<sha256[:16]>.json` and records `manifest.roads` (`url`, `sha256`, `bytes`,
`nodes`, `edges`). The graph is not part of tile cache keys: a graph-only change rewrites the
manifest without re-packing tiles. The live release carries it with the tile dist.
