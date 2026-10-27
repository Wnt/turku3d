# Local data pipeline

The complete pipeline runs on the Mac. Shared inputs: `$TURKU3D_DATA/raw`
(default `~/turku3d/data/raw`). Workers only read them. Writable products:
`$TURKU3D_WORK/{build,cache,dist,reports}` (default `~/turku3d/data`).
See [env/README.md](env/README.md) for the native arm64 toolchain.
Shared point access, profiles, surfaces and relief detection are documented in
[the lidar toolkit](turku3d/lidar/README.md).

```sh
make pipeline-bootstrap pipeline-versions
make pipeline-test                        # offline, synthetic fixtures, 4 pytest workers
make fetch-aoi AREA=work-3x3               # prepare shared data once, intentionally writes raw/
make fetch-checksums AREA=work-3x3         # export small provenance files for git
make refresh                              # rebuild stale derived layers and bindings
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-show                            # sandbox port base + 1
make qa-refs                              # regenerate crops with corrected grid heading
make collision-audit                      # layer collider/visual check near published spawn
make collision-sweep                      # headless KCC grid sweep + ortho overlay
```

`make refresh` builds ground, buildings, structures, bridges, roads, surrounding
massing, props, vegetation, traffic_static and the ground/facade texture bindings
in the sandbox. Before a stale product is rebuilt, a linked sandbox
directory is copied locally so the producer cannot write into shared live data.
Bridge layers are rebuilt when their declared source hashes
or code change. The office `make tile-build` requires every one of those layer
manifests in the sandbox; run `make refresh` first when one is missing.

`make tile` checks the cached input registry without network access, builds locally, verifies
manifest hashes and every shared collision seam, runs the Node/Rapier validator and Chrome e2e
on WebGPU and WebGL2. Missing inputs fail with the exact preparation command.
The playable area is the L0 tile list `$TURKU3D_DATA/playable-tiles.json`
(`{"level": 0, "tiles": [...], "core_bbox_en": [...]}`; migrated to the four office tiles when
missing). A sandbox's `$TURKU3D_WORK/playable-tiles.json` shadows it. `config.playable_tile_ids()`,
the `playable-tiles` area (`config.area("playable-tiles")`), refresh and the live build follow it.
Core tiles (`config.CORE_TILES`) require every layer in `make tile-build`; other listed tiles require
ground (their `.vis.glb` and all 16 colliders), buildings and roads. `work-3x3` is the larger
shared input area; a tile beyond it reads its own `tile-<id>` input area.

### Growing the playable area: `make tile-add T=t0_i_j`

```sh
source ../sandbox.env
make tile-add T=t0_2_0          # sandbox: list, fetch, seed, refresh, assemble the new tile
cat $TURKU3D_WORK/reports/tile-add/t0_2_0.json   # stage and layer seconds, tile size, layers
bash pipeline/env/run.sh python -m turku3d.region list-tile t0_2_0   # orchestrator: list it for live
```

`make tile-add` installs the validator Node dependencies before refresh, since the structures
producer runs Rapier acceptance while building the layer.

`add-tile` accepts an L0 tile that shares an edge with a listed tile and whose input area lies in
the city lidar coverage (`data-manifests/laz-tiles.csv`). A tile's input area,
`config.tile_input_area(tile)` (named `tile-<id>`), is its ground envelope: the tile plus 112.5 m,
which is one ring of collision tiles and the 50 m margin. `config.source_area(tile)` is
`work-3x3` while that holds the input area; `work-3x3` stays the union cache, not a boundary.
Otherwise it is the tile's own area. `add-tile` checks that area's inputs and fetches the missing ones
read-through into `$TURKU3D_DATA/raw` (`python -m turku3d.fetch all --area tile-t0_3_0`: LAZ
tiles intersecting it, WFS layers, both orthophotos and OSM by bbox). Ortho chunks are aligned
to the origin, so a chunk another area already holds is cloned, not downloaded. The report's
`inputs` records the area, missing files, fetch seconds and bytes. Ground and ground textures
read each tile's own source area, and the colour calibration stays on `work-3x3`. It
appends the tile to `$TURKU3D_WORK/playable-tiles.json`. It clones the published layers, textures and tiles into an
empty sandbox, then runs `turku3d.refresh`, the fast facade pass for the new tile and
`turku3d.package.assemble --area playable-tiles` for the whole list.

The `facades-fast` stage runs `turkutex.facade_fast` plan, refs, describe, render and publish
(`textures/README.md`, "Fast facade pass") with the textures venv: the tile's street walls with no
generated or derived map get rough procedural maps from one cached gpt-6-astra/low call per wall,
bound in the sandbox's `textures/assembly`. The report's `facades_fast` has each step's counts and
seconds; `$TURKU3D_WORK/textures/fast/<tile>/` keeps the run and staging.

### Per-tile producers

[`turku3d.tile_layer`](turku3d/tile_layer.py) runs a producer once per listed L0 tile. A tile's
build reads that tile's own inputs (`tile_layer.wfs(tile, name)`, `tile_layer.osm(tile)`, from
`config.source_area(tile)`) and owns exactly the tile's layer files: `<tile>.vis.glb`, the
`.col.npz` of its sixteen collision tiles and `<tile>.records.json` (its objects or clipped
ways). The layer MANIFEST's `tile_builds` keeps each tile's key, a hash of everything the build
reads, so refresh (`refresh.per_tile`) and the producer rebuild only stale tiles; layer-wide
inventories (`props/instances.json`, `roads/roadgraph.json`) are combined from the records.
Placement-dependent keys include the published collision tiles within 20 m
(`placement.inputs.context_key`), so an added tile also rebuilds a neighbour only when the
neighbour's placement context changed.

| Producer | Inputs (per tile) | Cache key | Time per tile |
|---|---|---|---|
| ground | LAZ under the envelope, WFS streets/water/buildings, OSM, ortho | `ground/MANIFEST.json` `tile_builds` | 82–160 s |
| roads | OSM ways (`tile_layer.osm`) | OSM clip + code | < 1 s (+8 s reading the work-3x3 clip) |
| props | WFS register points, OSM lamps/bollards, placement context | sources + collision within 20 m + footprints + code | 30–60 s |

The other list-following producers (buildings, outer massing, vegetation, parked cars) record
the list they were built for in `<layer>/PLAYABLE_TILES.json` and rerun for another list.
Structures and bridges stay on the measured office core.

Only the new tile is built. Ground meshes each listed L0 tile in its own envelope: its 16
collision tiles, one ring of collision tiles and the 50 m margin (`ground.build.envelope`).
Constraint segments close every collision tile, so a collision tile depends only on the samples
inside it. A tile built alone therefore matches a tile built in any other envelope, and
separately built neighbours share their seams bytewise
(`test_tiles_meshed_in_different_envelopes_are_byte_identical`). Tile meshes are written in
canonical vertex order. The Aura level is measured once over the office core. The layer
manifest's `tile_builds` keeps every tile's build key and file hashes, so a seeded sandbox or the
integrator rebuilds only tiles whose inputs changed. Margin collision tiles of unlisted
neighbours ship from the first listed tile whose envelope holds them (`ground.build.owners`).
Ground refinement interpolates the mesh's own triangles through a grid of triangle buckets
(`ground.mesh.HeightQuery`), not matplotlib's trapezoid map, which took most of a new tile's
ground time. Buildings key every building by its footprint and the lidar tiles under it and
keep its roofer reconstruction and selected shell in the layer (`buildings/README.md`), so a
new tile runs roofer and selection only for its own buildings and their neighbours stay
byte-identical; collision tiles index their own buildings, so their bytes stay too.
Ground textures keep an ortho key and a tile key in `<tile>/.build-key.json`. A tile with an
unchanged tile key is reused, and one with unchanged orthos rebuilds only its splats. Assembly
keys each visual tile by its own layer files, its ground binding and the facade data of its
own buildings: the buildings its layer GLBs carry, plus those whose facades are centred in the
tile (density planning ranks them there, so a seam neighbour's facades count too). Their bound
facade IDs and exported facade files enter the key; the shared library maps stay global. Each
collision tile is keyed by its layer files, with sandbox-relative paths. A buildings rebuild
for a new tile therefore repacks only that tile, the seam neighbours whose buildings changed
and the 500 m and 1 km parents above them.
Coarse visual-only L0 leaves fill the other quadrants of a 500 m parent that contains a new tile
(massing and coarse DTM, no collision) so REPLACE refinement leaves no hole. `list-tile` appends
to `$TURKU3D_DATA/playable-tiles.json` and, when the sandbox has a fast pass for the tile, publishes
its maps into `$TURKU3D_DATA/facade-approvals/assembly` under the export lock (walls missing from
the approved inventory or holding a generated or derived map are skipped). The next integration
then builds and publishes the tile.

`collision-audit` reads `$TURKU3D_WORK/build/layers` and the spawn in
`$TURKU3D_WORK/dist/tiles/manifest.json`; it writes `reports/collision-audit/issues.json`
and `map.png`. It compares sampled solid wall triangles and every solid primitive against
their layer GLB, including GPU-instanced vegetation, and flags steep geometry labelled
as walkable pavement. It exits nonzero when candidates remain. `collision-sweep` uses the same
published `.col` files and the game's Rapier character controller to walk a 5 m grid
through the 40 m spawn circle, with a 2 m grid within 12 m. Its `sweep.json` records
movement stops; `map.png` overlays them on the 2025 ortho. Red marks need inspection,
orange marks a visible wall, building, curb or street prop. Unexplained stops make the
sweep fail. `make playtest` includes this real-tile sweep; run `make collision-audit-test`
for the synthetic fixture.

`turku3d.config` is the only coordinate/grid definition: EPSG:3877 + N2000, engine origin
E=23459500, N=6704500, H=0; x=east, y=up, z=south. Visual L0 is 250 m, collision is 62.5 m.
`data_root()`, `work_root()`, `raw_dir()`, `build_dir()`, `cache_dir()` and `dist_dir()` implement the W0-A contract.

```sh
make foundation-where E=23459668 N=6704254
make foundation-areas
bash pipeline/env/run.sh python -m turku3d.config --area-yaml work-3x3
```

The Phase 0 builder still produces coarse heightfield ground with orthophoto textures;
curbs, structures and building shells belong to the Wave 1 layers. It caches one canonical
area heightfield keyed by source file identities and interpolation code. Independent L0 builds
slice that same grid, avoiding repeated LAZ decompression/TIN construction and seam steps.

## Coarse tiles (localhost draft tier)

`turku3d.coarse` builds one 250 m L0 tile in seconds in the published tile format (`vis.glb`
with meshopt + KTX2, sixteen `.col` files, a manifest fragment with `"coarse": true`), so the
game streams it unchanged while the full pipeline has not built that tile yet:

```sh
bash pipeline/env/run.sh python -m turku3d.coarse build t0_2_0          # -> $TURKU3D_WORK/coarse/t0_2_0/
bash pipeline/env/run.sh python -m turku3d.coarse build t0_4_0 --out /tmp/t
tools/dev/coarse-tiles                                                  # localhost service, port base + 5
```

Inputs come from the shared raw cache only; a build never downloads (`turku3d/coarse/README.md`):
the `maanpinta` ground key-point LAZ, CityGML LOD2, `GIS:Katualueet` and `kanta:Vesikohde` via
WFS, and a 512 px (49 cm/px) 2022 true-ortho chunk per tile. The service's prefetcher fetches them
ahead of the player in 500 m blocks. Ground is a 250/128 m (~2 m) grid TIN interpolated from the key points and textured
with the ortho; buildings are LOD2 footprints extruded to their area-weighted mean roof height
(library render walls, ortho roofs); street polygons set collision materials (road, sidewalk,
cobblestone...), green ortho pixels grass, water polygons the water sensor over a synthetic bed.
The coarse tier has no curbs, stairs, retaining walls, bridges, props, trees, parked cars,
facade maps, roof shapes or LOD parents. Outside the city lidar index (`laz-tiles.csv`) the
build fails with a reason: the MML 2 m DEM needs an API key, which this project does not use.
Coarse tiles are never written to `$TURKU3D_DATA/live` or any published layer. Tests:
`pipeline/tests/test_coarse.py` (synthetic inputs, service queue and routes) and
`pipeline/tests/test_coarse_prefetch.py` (prefetch area, unit order, LRU prune, pending-data).

## Selective quality checks

From the repository root, run `scripts/dev/gate --base HEAD` for local changes or
`scripts/dev/gate --all` for the complete offline suite. See the [quality gate guide](../scripts/dev/README.md)
for dependency setup, path selection, caches, sandbox ports and Git hooks.

The [buildings layer](turku3d/buildings/README.md) reads both city LoD2 and roofer LoD2.2,
compares final snapped shells against class-6 lidar, and writes facade-ready layer artifacts:

```sh
make -j2 buildings-test                   # synthetic geometry and Rapier regression tests
make buildings-build                     # full build; nonzero if shell coverage fails
make buildings-validate                  # 2,000 walkers against the exported NPZ triangles
```
Wave 1 structures: [turku3d/structures/README.md](turku3d/structures/README.md).
`make -j2 structures-test` checks synthetic geometry, layer seams and Rapier traversal;
`make structures-build` consumes the completed ground layer and writes intermediate
structures plus an explicit acceptance report.
`make refresh REFRESH_TARGET=facades` rebuilds stale building shells and migrates
approved facade maps by building ID and wall geometry. The assembly export keeps
`APPROVED_INVENTORY.geojson` as the approved source geometry; the first refresh
captures it from the existing building layer before rebuilding. Reindexed, moved,
and split walls reuse their KTX2 maps with UVs projected onto the new baseline.
The migration report is `$TURKU3D_WORK/reports/facade-refresh.json` and lists
genuinely new street walls, ambiguous matches, and maps rejected because the
rebuilt wall no longer fits the approved photo. Approved maps
without a safe successor are retained under `textures/assembly/retired/<inventory-hash>/`.
`make refresh` runs only in a sandbox, detaches linked output directories when a producer
needs to rebuild them, and prints the cached/rebuilt status and elapsed seconds for each step.
Rerunning it after a failure reuses products whose declared inputs and code are unchanged.
