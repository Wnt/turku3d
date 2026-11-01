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
`water_level`, `poles`, `sources`, `seconds`, and `code`, the producer hash the service compares), `vis.glb`,
`col/c_i_j.col` for all 16 collision cells and `roads.json`; shared KTX2 textures go to `<out>/../shared/`.

- **Streets** (`roads.json`, `manifest.roads` = `{url, sha256, bytes, nodes, edges}`): the tile's OSM street graph
  in the published format (`turku3d.roads`, `turku3d-roadgraph` v1), clipped to the tile and keeping every
  connected piece (`build_graph(..., connected=False)`), so the game joins it to its neighbours' graphs at the
  tile edges. OSM comes from work-3x3 when it holds the tile, else `tile-<id>`, the older per-tile clip
  `coarse-<tile>`, or the `osm` prefetch unit of the tile's block (below).

- **Signs and bus stops**: the cached WFS sign, stop and centreline sources (plus OSM signs when available) feed the shared props reader. Each pole has the same stable `pole_id` as a published tile, and its base sits on the draft ground grid (`pole_base` in its extras, so the client reads no geometry). They have runtime breakable colliders rather than baked primitives. Rebuild a draft after this producer changes with `bash pipeline/env/run.sh python -m turku3d.coarse build t0_2_0 --out "$TURKU3D_WORK/build/coarse/t0_2_0"`.
- **Street furniture and lights** (`furniture.py`, G1): the block's wfs unit also holds `GIS:Varusteet`,
  `frn:CityFurniture_LOD2` (street-light poles), `GIS:Polkupyoraparkki`, `GIS:Pysakointiautomaatit`,
  `GIS:Follariasemat` and `GIS:Puut`. The props reader takes register items first (keyed on the fetch
  `feature_key`; the two street-light registers collapse within 1.5 m), OSM benches, bins, bike parking,
  parking meters and lamps only where the register has no item of the kind within 3 m, and the 30 m
  `lit=yes` lamp rule only at stations with no lamp within 20 m. Bollards stay in the barrier layer.
  Nothing but signs stands on a carriageway: a register point up to 2 m inside one moves just past its
  kerb, a deeper lamp hangs over the street. The stage then moves furniture just inside a building
  footprint outside it (deeper: dropped), mounts register lamps on the facade they touch, moves quay-edge
  items up to 1.5 m inside a water polygon onto the land (deeper: dropped), drops anything on a
  bridge/tunnel deck and sign posts whose code has no face, and sets every base on the draft ground grid. Lamps become the
  manifest's `lights` list (16 B records: post base and head, hung lights, facade lights found along
  every car street; `turku3d/props/lights.py`); the client draws their posts and coronas. Benches,
  tables, waste containers, planters, register bollards and lamp posts get static collision primitives
  in the tile's cells. Register trees (`GIS:Puut`, height from the planting year) join the `trees` input;
  OSM trees within 3 m of one are dropped. The manifest's `furniture` field counts placements, moves and
  drops by reason. `scripts/dev/g1_tile_check.py` compares a built tile with its sources.
- **Ground**: a 128 × 128 cell terrain grid (~2 m) interpolated from ground key points.
  City surface polygons override OSM ground-level road/path corridors and mapped land cover;
  their explicit asphalt, concrete, stone and gravel paving overrides the road-use label,
  so asphalt pedestrian routes do not acquire concrete slab joints.
  green orthophoto pixels only classify the remaining unlabelled ground. Visual material boundaries
  are cut exactly against those polygons, interpolating the original terrain plane and normals.
  Collision retains the original grid. Shared asphalt, paving, grass, gravel, stone and quay PBR
  maps repeat every 2 m; steep faces project onto a vertical plane to avoid stretched bank textures.
  Default paving on near-vertical terrain uses the neutral rock/gravel material in visual output.
  The existing ground library arrays are extracted at 1024 px albedo / 512 px normal / 256 px ORM and encoded once into
  content-addressed shared KTX2 maps. A deterministic procedural floor supports an absent library.
  The albedo texel spacing is 1.95 mm; world-metre UVs retain the same phase on neighbouring tiles.
  Draft ground no longer displays aerial shadows, roofs, cars or blue-white patches as albedo.
  Object support heights use the actual grid triangle, including its diagonal, rather than a
  bilinear patch. The same sampler places vegetation, poles and barriers on visual/collision ground.
  Asphalt removes the generated warm colour cast in linear light, preserving grain luminance;
  stone, paving and vegetation retain their authored colour.
- **Paint and kerbs**: explicit OSM crossing/lane/stop lines are draped on dry road triangles,
  clipped to their exact boundaries and offset 8 mm. Point-only crossings remain unresolved.
  Mapped kerb linework (deduplicated against polygon outlines) becomes an 18 cm wide strip with
  matching collision and a default, unmeasured 12 cm raised profile. Lowered/flush tags and
  mapped crossings leave openings; buildings and water exclude kerbs. No kerbs are inferred
  from road edges. Full strips are clipped after extrusion, with the owning terrain grid sampled
  on both sides of tile seams. Each strip corner follows the ground cross-slope, including
  bridge approach adjustments. Tops and walls split at terrain rows, columns and diagonals,
  so a strip cannot bridge a dip or float between sampled corners. Bridge/tunnel/nonzero-layer
  ways are excluded.
  `marking_triangles`, `kerb_triangles` and `ground_surfaces` record output counts.
- **Water**: open river/sea water uses one fixed N2000 0.0 m surface across source pieces,
  tiles and first/full passes. Shoreline lidar percentiles must not set tile-local levels.
  Survey polygons are dissolved before tile selection; mapped flowing-water interiors
  (inset 3 m to retain surveyed outer banks) close survey breaks under bridges and
  survey-only sliver islands. Complete components prevent tile edges becoming banks.
  Cells inside water polygons become the water sensor over a synthetic bed
  (`ground.water.add_river_bed`), and are drawn as a flat surface at that level (`material_key` water, which the
  game renders as a river) instead of the ortho.
  Isolated compact standing-water polygons (5–5,000 m², span at most 120 m,
  circularity at least 0.15) use their own median ground height in a 3 m outer
  shoreline ring, minus 0.35 m. Complete canonical lidar blocks cover the whole
  shoreline, including across tile boundaries; incomplete evidence retains the
  existing profile. Their bed is at most 0.65 m deep, with a roughly 2 m grassy
  lip and a gradual return to the hillside. Heights are graded on the original
  TIN before vector cuts to avoid steep slivers. The bed is visible where it
  emerges, and natural pond edges have no vertical bank faces. Mapped stone
  edges retain their rim and faces. Connectivity is resolved against the complete
  survey source before selecting tile-local candidates; mapped rivers, streams,
  canals and sea veto pond grading even for small disconnected survey fragments.
  Large/connected water keeps the river/sea profile.
  `python3 scripts/dev/probe_river_surface.py --commit SHA` audits the whole available
  mapped Aura reach, measures changed heights/geometry/prototype artifacts, builds
  representative first/full tiles on OCI and checks current client-facing generations.
  `python3 scripts/dev/probe_river_spawn.py --commit SHA` checks the four spawn
  tiles, counts changed cached tiles and builds only the two affected eastern
  neighbours, first and full. `python3 scripts/dev/probe_ponds.py --commit SHA` measures the reported
  three duck ponds and builds their tile plus an Aura control on deployed OCI.

- **Bridges** (`bridges.py`): transport ways belonging to one `man_made=bridge` outline share one deck;
  absent an outline, overlapping parallel ways at the same layer share an envelope. The main carriageway
  determines the axis even for branched outlines. Full raw ground blocks give neighbouring tiles the same
  profile, and meshes are clipped to the tile instead of repeating entire decks. Only transverse interior
  crossings of lower transport ways request headroom; their elevation is interpolated from outside
  the deck footprint so deck samples cannot be mistaken for railway ground. Boat gangways retain their mapped span and endpoint
  levels. Ordinary approaches seek a mapped continuation-road join within 30 m and 8% overall
  grade, independently on each bank; without that evidence they retain the short blend.
  Lower-route corridors remain unmodified beneath approaches. Outline branches blend back to measured ground.
  Source ship/building footprints cut holes in decks; rails follow the outline with open traffic entrances.
  Mapped branch paths blend toward ground; widened observation bays retain the deck profile.
  Collision clips the detailed railings and deck to each 62.5 m cell instead of repeating a full
  visual tile's structure in every cell. Cuts retain shared boundary vertices and discard only
  boundary ears that collapse below the COL minimum area at cell-local float32 precision.
  Without OSM decks, the existing street-area water-bridge abutment lift remains the fallback.
- **Buildings**: LOD2 footprints whose centroid is in the tile, with the upper roof envelope
  retained as separate sloping/terraced solids. Overlapping lower decks no longer pull down
  a single mean height. Optional cached `raw/laz/pistepilvi` class-6 points refine each plane:
  0.75 m interior inset, equal-weight 1 m cell residual medians, at least eight cells and 50%
  coverage, residual MAD at most 0.35 m. Unsupported faces retain their surveyed LOD2 plane;
  register floors and OSM levels are facade semantics, not competing height measurements.
  Classified sources are never fetched by a draft build. Their arrival/change invalidates both
  prepared WFS data and the service input key. `manifest.roof_heights` records support and
  offsets per building/roof part. The complete building still belongs to its centroid's collision
  tile. Passage ceilings clip away lower roof parts before extrusion; collinear partition ears
  are removed at export precision. Roof boundaries simplify by at most 5 mm before meshing.

- **Trees**: OSM `natural=tree` points on dry ground outside the buildings, drawn with the vegetation layer's
  GPU-instanced prototypes (10 m unless `height` is tagged, conifers by `leaf_type=needleleaved`); each trunk is a
  `tree_trunk` cylinder in its collision tile. They are unmeasured stand-ins for the full layer's register and lidar
  trees.

- **Barriers** (`fences.py`, `manifest.barriers`, `seconds.fences`): OSM fence/wall/retaining wall/city wall,
  hedge, guard rail, handrail and bollard lines/points, including tagged area outlines. Tagged height/material/
  fence type selects cheap chain-link (alpha mask), wood, bars, stone/brick, hedge or steel prototypes (at most nine
  instanced draws per tile). Segments follow grid heights, split at corners, 62.5 m cell edges and every 2.5 m;
  matching solid boxes use the existing fence/wall/railing/vegetation/bollard materials.
  Architectural assembly passes GPU-instanced layers through intact. Reading a unit
  prototype as an ordinary wall loses its instance placements and draws overlapping
  hedge/fence/bollard cubes at the tile origin, which can lie in open water.
  Gates leave openings
  (3 m for an unmeasured point gate). Street-area crossings are clipped with width clearance, except deliberately
  tagged road barriers and bollards on OSM road nodes. Bollard ways place separate posts, 2.5 m apart. The prepared
  `barriers` dataset reuses the existing optional OSM extract; no downloads or runtime game changes.
  Water and building/ship footprints exclude barriers. Elevated bridge railings are supplied by the deck,
  not repeated at ground level. A `retaining_wall` without an explicit above-grade height creates no fence;
  `wall=parapet` defaults to 0.5 m. Bare mapped banks do not create barriers.
  Run `bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_fences.py` for synthetic placement
  and road-clipping checks. `manifest.barriers.seconds` records the extra producer seconds on each built tile.

- **Tunnels** (`tunnels.py`): OSM road/path/rail `tunnel=yes|culvert`, plus
  `covered=yes` with negative `layer`, become box sections with portal wings, solid walls,
  roof, emissive light strips and a road/path floor. Width/maxheight override class defaults
  (7 m / 4.5 m for roads, 3 m / 2.5 m for paths). Full-block lidar samples keep a measured dip;
  buried stretches follow the portal chord. Ground obstructing the clear box is cut with exact
  polygon remainders, while overburden above its roof remains walkable. Building passages split
  the draft shell below the ceiling and retain the upper building. Walls reuse structures
  `profile_wall` and all faces reuse `Faces`; tube collision reaches every intersecting cell.
  `manifest.tunnels` counts ways; `seconds.tunnels` records producer overhead separately.
  Portal floors split along every terrain triangle across their full width. Their 2 m joins
  retain road crossfall up to 10%, reject enclosing banks and overburden, and connect through
  carved 2 m open approach aprons. Ground cuts test the height range at the corridor intersection,
  including triangles whose edges cross the clearance without a vertex inside it.
  Foundations extend below the lowest terrain plane across each
  wall's width, and road graph heights retain the floor's centre profile.
  `tunnel=building_passage` keeps the original ground triangles and relief, with mapped ground-level
  asphalt/paving semantics. Only its building shell is carved: the visible ceiling reuses that
  shell's existing downward collision floor. It adds no slab, freestanding tube, portal wings or
  light strips, so sloping entrances and steps remain continuous with the surrounding ground.
  Carved parts retain the original building's collision owner and appearance metadata, including
  stored per-building facade descriptions, even when a part's centroid crosses a tile boundary.
  Visuals and collision are clipped to the same tile bounds. Tight offset corners use individual wall
  sections in a combined mesh; passage footprint cuts remove sub-0.5 mm slivers before triangulation.
  Optional `roads.json.edgeHeights` stores floor Y per edge vertex, shared with draft bridges.
  Missing remote portal lidar falls back to the local grid; stacked/intersecting tunnels and
  abrupt OSM way splits still need surveyed profiles. Pure synthetic checks:
  `bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_tunnels.py`.

Ground support and kerb cross-slope checks:
`bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_surfaces.py`.

No inferred curbs, surveyed stairs, unique facade maps or LOD parents.

## Service (`service.py`)

`tools/dev/coarse-tiles` (localhost only, port `$TURKU3D_PORT_BASE + 5`, proxied at `/coarse/` by the dev and dist
servers). One first-pass build and at most one full upgrade run at once. Native BLAS/OpenMP/Arrow/LAZ/image thread pools are limited to one thread per worker. The current tile and actual velocity-ray crossings come first, ordered by entry time; surrounding ring/cone tiles follow. The game requests the cells
ahead of a moving player as well as the 3x3 window. Routes and queueing: `tools/dev/README.md`. Built tiles are
reused until the producer code changes, so a producer fix rebuilds each draft the next time the game asks for it.
On main the localhost game service runs it and restarts it when a landing changes a file it loaded
(`tools/dev/localhost/README.md`): `/coarse/status` reports `started` and `modules` (the repository files the process
imported), and `POST /coarse/drain` stops new builds so the running ones finish first.
Manifest requests report `building` until the first worker commits completion. A complete first pass
remains servable during its full upgrade; `version` advances at each publication. First passes retain ground, solid building shells, bridge decks, tunnels, roads and all 16 collision
cells. Flat materials open the boundary before orthophoto sampling, classified survey preparation,
material boundary splitting, kerbs, fences, props, vegetation and facade appearance preparation.
Full drafts restore the detailed materials, surveyed profiles and all decorative/collision additions
in place. A first pass reuses already prepared full survey inputs when available. A first pass has
`pass_kind: "first"`; the replacement has `pass_kind: "full"`. Payload URLs include SHA-256 identities,
so requests already using a first manifest keep reading its immutable files during the upgrade.
The `vis.glb`, `roads.json` and `col/*.col` aliases are retained for offline QA. Shared texture URLs
remain relative to the tile directory. The client also refreshes the road graph with the upgrade.
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
key covers `inputs.py`, the dataset’s own producer dependencies and the raw file content identities, so new data or relevant code prepares again. A surface edit retains prepared roofs, bridges and ground. Material extraction keys cover only the library/fallback generators, so unrelated geometry edits also retain extracted maps. First-pass WFS/roads use separate caches; full WFS reuses the base shells before survey refinement. Roof refinement crops cached class-6 points to the buildings before constructing its search tree.
The prefetch workers prepare the complete tiles within 1 km of the player in the background
(`python -m turku3d.coarse prepare <tile> --root <coarse root>`), so a build reads a few small
pickles instead of decoding LAZ, 5 cm ortho mosaics and whole-area GeoParquet files, and imports
neither geopandas nor rasterio nor laspy. The draft ortho is encoded with the fastest ETC1S effort
(`draftTextures` in the visual packager request, `--clevel 0`). Linear-map UASTC uses lossless Zstd level 6 for drafts instead of level 18; decoded texels and texture dimensions are unchanged. The service builds on persistent
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

Draft walls use a 49-module, content-addressed procedural facade atlas (albedo, normal and ORM)
with an explicit pigment mask in albedo alpha (material remains opaque). Painted wall, trim,
glass and mortar no longer depend on brightness thresholds; neutral white trim is tinted once.
Version 3 keeps separate ground/upper floor heights, including taller mapped shopfronts,
and places one entrance bay per wall instead of repeating a door in every bay. Shop modules
retain glazing in each bay. Rebuild using the commands below; old version 1/2 tiles remain readable.
Atlas samples use gradients from continuous metre UVs before wrapping, preventing coarse mip
stripes at bay boundaries. Blank modules retain a three-metre horizontal scale on wide sheds.
Shared normals use UASTC with Zstd level 6 to preserve smooth plaster relief; albedo/masks and
ORM use ETC1S. These maps are encoded once per kit hash and reused by subsequent tiles.
The atlas is rendered once with `turkutex.facade_fast_render` and reused through `shared/`. Per-building colour,
bay spacing and storey height travel in per-vertex kit attributes, with one shared kit material
per tile/layer (the same package binder as published tiles); all roofs retain the ortho,
including heritage buildings and OSM roof-material tags. Missing/blank imagery and parts
outside the centroid tile use a roof-material palette seeded by building ID; tile-edge
pixels are never stretched over uncovered roof parts. Seven blank modules preserve mapped sheds and garages without cloning residential windows. The game samples the
upper bay repeatedly and the ground-floor row only at the base on both renderer backends. Published
walls without an approved generated, derived or fast map use the same kit. Pure mapping checks:
`PYTHONPATH=pipeline:textures .venv/bin/python -m pytest pipeline/tests/test_facade_kit.py -q`.

The service keeps one low-priority hybrid child warm using the repository's clean
`.venv/bin/python` (Torch and the native geo environment load conflicting OpenMP runtimes).
Run `scripts/dev/wt sync-env` to install the inference dependencies. Existing pinned local
model caches are prepared as described in [textures/CV.md](../../../textures/CV.md).

Within the player's ring/cone, buildings first receive offline hybrid appearance from one
screened panorama wall. Only measured `wall_colour` and `material` fields are
accepted; absent or abstaining fields retain the register value. The current hybrid has no
trim head, so trim explicitly abstains. Storeys, columns and other layout fields stay from the
register. Local inference has no quota cap or model network calls and keeps its models warm.
Reference discovery can still use the existing panorama providers. Neither discovery nor
inference runs inside draft generation, so a draft never waits for these operations.

The queue alternates local jobs with Astra refinement in a fresh process so the Codex CLI
does not inherit the Torch-loaded worker. It checks weekly usage before admitting each call.
Astra prioritizes the highest
viewing-exposure score across eligible ring/cone buildings and selects the highest-scoring
usable wall within that building. When there is no viewing history, it chooses the closest
building wall to the player's latest position, then the closest usable wall in that building;
wall width is the fallback if no position is available. The
background lane refreshes the last 24 hours of current/previous-day session observations at
most once a minute; an existing `telemetry/priority.json` is the fallback when no recent
samples exist. Exposure accumulates screen share and proximity through the existing wall
weight function. Only Astra attempts consume the 12/hour budget and pause
at 60% quota (unknown quota pauses Astra only). Hybrid records stay eligible for Astra;
Astra records, including legacy records without an engine tag, are never replaced by hybrid.
Geometry-only candidates and reference pixels stay under the sandbox coarse root. Validated
parameter JSON goes to `$TURKU3D_WORK/facade-params/<sha256(building-key)>.json`, using the
permanent register id when available. `turku3d.facade_kit.write_description()` is the atomic
writer. Rows retain `engine`, `field_sources` (register/hybrid/astra) and raw hybrid `fields`
(state/value/reason); raw count predictions are evidence only and are never accepted by this
queue. Existing readers remain compatible. Only affected drafts are invalidated; resident
models remain visible until the replacement is ready. `/coarse/status` includes engine,
worker state, last-job seconds and rolling measurements under `facades`. Local completions
and actual Astra invocation attempts are appended to `<coarse root>/.facade-progress.jsonl`.
`hybrid_buildings_per_minute` includes observed idle time, reference acquisition and model
startup; `actual_calls_last_hour` excludes cache hits and quota pauses. Interrupted jobs with
unknown call outcomes are counted separately as `unknown_call_jobs`. The existing 12/hour
admission budget remains a ceiling, not a measured call rate. Published assembly reads the same parameters.

Measure the real local lane without building tiles with:

```sh
source ../sandbox.env
source ~/turku3d/envs/pipeline.env
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- \
  env PYTHONPATH=pipeline:textures .venv/bin/python -m turkutex.facade_queue_benchmark \
  --inventory "$FACADES" --refs "$REFS" \
  --output "$TURKU3D_WORK/reports/queue-benchmark-new" --buildings 3 --astra-calls 0
```

Use a fresh output directory for each run so completed building records do not mask
throughput. Set `--astra-calls 1` when weekly usage is below the queue's 60% pause
threshold and an actual invocation is wanted. The benchmark writes only inside its output directory;
`aggregate.json` reports observed buildings per minute, completed Astra calls in the trailing
hour, successful Astra upgrades and the configured hourly admission cap.


Resolved OSM crossings use `bridge_details.py` for type-dependent deck bodies, edge beams,
open steel balusters (or explicitly tagged stone/concrete parapets), bank abutments and broad
supports outside crossed-way corridors. `bridge:structure` distinguishes beams, arches and
humpbacks; absent underside measurements, thickness and span spacing remain documented type
estimates. The surface uses OSM surface tags; asphalt binds the street's `ground_asphalt`
library, and concrete, stone, steel, wood and ballast have deterministic procedural albedos.
Mapped pedestrian member ways become raised pavements; lane tags place paint, gauge tags
place rails/sleepers, and mapped `highway=street_lamp` nodes place poles. `lit=yes` alone does
not invent lamp locations. Meshes batch by material; paint has no duplicate collider, while
decks, pavement, structural members and visible railing bars supply collision.
Longitudinal crown stations split the top and soffit for triangulation. Only the physical
outline and hole boundaries get vertical slab walls; station cuts do not add internal bulkheads.

`bridge_survey.py` optionally reads existing full city lidar through `turku3d.lidar.TileStore`
into the sandbox cache. It never fetches. Classes 1/2/10/12/17 contribute robust deck profiles;
class 10 is used for bridges in the city source. At least 80% of 3 m stations and both endpoints
must have a narrow dominant height band. Every fifth return is withheld for QA. Measured
crowns replace flat ground-key-point spans; missing coverage and bridges newer than the 2021
flight explicitly retain the fallback. Cross-section occupancy reports a separate width check;
OSM outlines retain ownership. Optional source identities invalidate prepared and served drafts.
Vertical sub-deck lidar clusters supply positive pier evidence; missing clusters retain the
type-based span layout, and mapped lower ways exclude supports. Airborne coverage does not
measure an underside everywhere, so structural thickness is explicitly a type-based estimate.
Fallback headroom remains 4.5 m road / 6 m rail / 2.5 m path. `manifest.seconds.bridges` records
producer time. `roads.json.edgeHeights` follows the shared deck for parallel carriageways and
sidewalks while leaving transverse lower ways at their own elevations. The existing 8 MB visual
budget remains unchanged.

When the full lidar cannot supply a profile, optional cached `kanta:Korkeuspiste` N2000 heights
can anchor the deck interior. The catalogue creation year must not precede the mapped construction year; this is a
freshness filter, not an independently verified survey date. Points must
lie away from edges/ends and mapped lower routes, and have position uncertainty at
most 20 cm. Conflicting heights at one station and profiles steeper than 15% are rejected.
Points within 50 cm of the raw terrain/water height are excluded: map positions alone do not
distinguish water below a bridge from its deck.
Ground approaches stay fixed. These sparse anchors do not certify the whole deck; the audit
reports their stations and source separately from held-out lidar errors. The small spot-height
files also participate in the initial structural pass and both prepared/served cache identities.
Nearby source areas are read together so adjacent tiles use the same bridge anchors.
Generation never fetches them; the WFS prefetcher attempts this optional layer after required
layers, including enrichment of cached blocks whose structural inputs are already ready.
Missing spot heights do not block a build. The input owner can also populate a tile's source with
`bash pipeline/env/run.sh python -m turku3d.fetch wfs --area tile-t0_3_-5 --layers kanta:Korkeuspiste`.


Area vegetation (`forests.py`) uses OSM forest/wood, scrub/heath, parks/gardens and tree rows,
with seeded Poisson-disc fallback (5 m trunk spacing; 180/ha woodland, 40/ha park).
Buildings, city street polygons, OSM road/path corridors and water exclude new trunks.
Foot/cycle-class paths additionally exclude every tree source by its visible mesh envelope:
centre distance must be at least 2 m plus the variant's normalized flared-base radius times
crown scale, and at least 1 m plus crown radius. Car-road clearance is unchanged.
Mapped paths through forest land cover override forest-floor visuals and aerial foliage;
existing explicit surface materials remain. Terrain collision and structural stage keys do not change.
OSM sports tracks, pitches, stadiums, outdoor sports centres, playgrounds, recreation
grounds, BMX/cycling tracks and raceways exclude area-generated trees and bushes,
including canopy candidates. Closed outlines exclude their interior; open lines and
area boundaries have a 3 m margin applied before tile clipping. Explicit mapped
trees and tree rows retain their existing placement rules.
Classes 3–5, when present in the existing ground LAZ, supply canopy positions/heights above
that same ground grid; ground-only `maanpinta` normally needs the fallback. No extra LAZ is fetched.
The budget is 768 trees plus 24 low edge-bush instances per 250 m tile, using the shared
`vegetation.visual.write_instances` LOD contract and prototypes. Collision reuses the existing
trunk cylinders only; bushes and crowns have none. Manifest `vegetation` records the caps and
measured/bush counts; `seconds.vegetation_scatter` records added producer time per tile.
The tree cap was raised after the [Kupittaa render measurements](../../../docs/reports/perf-kupittaa-check.md);
the bush cap and area densities are unchanged.
Pure synthetic checks: `bash pipeline/env/run.sh python -m pytest pipeline/tests/test_coarse.py -k forest -q`.

## Real-data generator sanity check

This manual build/render workflow is outside the gate. It covers the six Aura playtest spots in 12 tiles,
plus Hämeensilta and all eight neighbouring tiles. Outputs, local screenshots and per-type source/output
counts remain under `$TURKU3D_WORK/reports/bridge-sanity/{before,after}`. The render harness uses the game
renderer, post-processing, draft materials, shadow batching and vegetation LOD at 1280×720, with a fixed
camera set. Its isolated-scene counts are comparable before/after, not the playtest's live streaming FPS.

```sh
source ../sandbox.env
# Before baseline: a permanent pre-fix main commit, raw inputs read-only.
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.sanity before --baseline-ref 7f0946a9
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.sanity before --baseline-ref 7f0946a9 --region hame
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.sanity after
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.sanity after --region hame
npm --prefix game run dev -- --host 127.0.0.1
# In another sandbox-sourced shell; reserve BROWSER_BUDGET_MIN = 40 in the task brief.
python3 scripts/dev/host_slots.py browser -- node qa/render/bridge-sanity.mjs before aura
python3 scripts/dev/host_slots.py browser -- node qa/render/bridge-sanity.mjs after aura
python3 scripts/dev/host_slots.py browser -- node qa/render/bridge-sanity.mjs before hame
python3 scripts/dev/host_slots.py browser -- node qa/render/bridge-sanity.mjs after hame
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_bridges.py pipeline/tests/test_coarse_fences.py pipeline/tests/test_coarse_tunnels.py
```

`--counts-only` refreshes source/instance tables without packaging tiles. Historical baseline failures are
recorded rather than hidden; the render harness excludes those same baseline-missing tiles from both
spot performance comparisons, while complete after tile-overview renders include them.

## Roof audit (manual, outside the gate)

```sh
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.roof_audit
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.roof_audit --build --tiles t0_-1_1 t0_-2_2 t0_-2_3
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE" --strictPort
python3 scripts/dev/host_slots.py browser -- node qa/render/roof-sanity.mjs
```

The first command evaluates every cached classified-LAZ L0 cell with prepared LOD2, not only
reported failures. A coordinate hash reserves 20% of roof returns from fitting; before/after
use identical held-out class-6 points inside footprints, inset 0.75 m and above base + 2 m.
The old flat mean, LOD2 envelope and lidar-refined envelope are reported separately.
`WORK/reports/roofs/height-audit.json` retains distributions and per-building evidence;
explicit `--tiles` writes `height-audit-selected.json`. Build outputs and paired screenshots
stay in that same sandbox directory. The renderer also expects the office colour audit
from `turku3d.buildings.roof_audit`. No external map imagery is read.

## Draft texture evidence

Cached Kanta/GIS records enrich facade material, construction era and storeys. Spatially matched
OSM building outlines add cladding, tagged colour and missing layout; shop POIs/building use add
shopfronts. Register storeys and validated parameter-store descriptions remain authoritative.
Missing register fields stay unknown. No reference images or per-building sampled reference colours enter inputs.
The wall fallback samples stable register-ID-conditioned Lab distributions from
`textures/turkutex/wall_palette.json`. Observed era groups use aggregate masked,
ground-illumination-corrected wall evidence; unsupported material/district groups
back off to authored material distributions in the same file. Explicit paint and
stored facade descriptions retain precedence. See `docs/reports/wall-palette-calibration.md`.
Named OSM yellow remains a paint family and resolves through the existing cladding/era ochre
palettes; explicit hexadecimal colours keep their values. Non-retail walls retain regular
register floor spacing; only mapped shops receive the taller ground-floor layout.
The game preserves authored draft colours; the former 55% saturation and blue multiplier are removed.
Full ground keeps only a bounded 0.96–1.06 low-frequency luminance multiplier; material albedo
remains authoritative at distance too.

Manual real-data commands (outside the gate; start the sandbox Vite server for rendering):

```sh
source ../sandbox.env
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.texture_sanity before --baseline-ref 312849f6
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.texture_sanity after
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE" --strictPort
# In another sourced shell, with reports/texture-sanity/views.json populated as below:
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs before 312849f6
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs after
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs before 312849f6 --full
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs after 312849f6 --full
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_surfaces.py pipeline/tests/test_facade_kit.py
```

`views.json` is a local array of `{name,tile,position:[x,y,z],target:[x,y,z],fov,width,height}`
(world-local engine metres, vertical FOV); top/oblique views instead use `{name,tile,target,radius,top}`.
The renderer retains screenshots and frame/GPU/draw measurements under each phase directory.
Append `--root /absolute/build/tree` to render another immutable draft tree with the same views;
the tree contains the tile directories and their `shared/` resources.
`--full` loads the three published office tiles with their ground bindings and water shader.
Private reference comparisons and their calibration caveats are described in the task report;
all reference renders and pairs stay outside the repository.
## Bridge appearance and profile audit

Permanent crossing profiles supplement incomplete OSM material tags without changing
the mapped outline or surveyed road heights. Auransilta retains its mapped beam
structure: steel deck/floor beams, stone supports and light painted railings, as
shown in [Yle's bridge photographs](https://yle.fi/a/3-7357934). Tuomiokirkkosilta
uses stone abutments and exposed concrete arch fascia along the outer deck edges;
the [2025 bridge inventory](https://turunraitiotie.fi/wp-content/uploads/2026/02/Turun-raitiotie_Turun-Tuomiokirkkosillan-vedenalainen-tarkkuusinventointi-1.pdf)
describes the single opening and historical widening. Its internal historic steel
structure is not replaced with invented river piers. Detail dimensions remain
estimates; the city-lidar deck and OSM widths remain authoritative. Profiles use
permanent Wikidata crossing identities and apply in every tile and draft pass.
Other bridges retain their existing profiles. Photos and private references stay local.

Bridge approaches join the same globally aligned ground triangles as the rendered and
collision terrain. Nearest lidar keys can be metres above the bank TIN and must not
set a ramp endpoint. Mapped continuation corridors fill the wedges at skew abutments.
Tuomiokirkkosilta's outer concrete arches have open spandrels and vertical posts.

Browser-free crossing regression (production car, Rapier 0.21, hash-verified OCI COLs):

```sh
node tools/validator/src/bridge-crossing.ts "$TURKU3D_WORK/reports/bridge-crossings"
# Repeat against the frozen downloaded payloads, without network/build waits:
node tools/validator/src/bridge-crossing.ts "$TURKU3D_WORK/reports/bridge-crossings" Auransilta,Tuomiokirkkosilta --cached
node --test tools/validator/test/bridge-crossing.test.ts
```

The live command holds anonymous tile demand and requires full/current manifests;
it never accepts stale fallback. All seven named bridges run in both directions.
The synthetic continuous-join and two-metre-gap case is in the affected quick stage
`coarse-bridge-crossing`; real live builds stay outside the 30-second quick budget.

The manual audit includes Teatterisilta, Myllysilta, Martinsilta, Auransilta, Kirjastosilta,
Tuomiokirkkosilta, Hämeensilta, Aurajoen ratasilta, Raunistulansilta, Betanianpolku and incidental
landings in their tiles. It retains before/after GLBs, collision, source membership, held-out
height/width evidence and top/oblique/street/bank/deck renders in the sandbox. It is outside
the gate. Before changes only the bridge producer to the pinned pre-quality implementation;
other tile layers use the checkout being audited.

```sh
source ../sandbox.env
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.bridge_quality after
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.bridge_quality before
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE" --strictPort
# In a second sandbox-sourced shell:
python3 scripts/dev/host_slots.py browser -- node qa/render/bridge-quality.mjs after
python3 scripts/dev/host_slots.py browser -- node qa/render/bridge-quality.mjs before
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_bridges.py
```

Output: `$TURKU3D_WORK/reports/bridge-quality/{before,after}/{inventory.json,render.json}`
and local PNGs. Targeted `--tiles` reruns retain the other inventory entries, and failed tile
builds give a nonzero exit status. Render after first: street/bank/deck cameras sit 1.7 m above
the actual supporting mesh, and before reuses those exact camera positions. For an isolated supplementary WFS index, append
`--wfs-cache "$TURKU3D_WORK/cache/bridge-wfs"`; it uses the same summaries and source checks,
with shared inputs linked read-only and newly downloaded layers kept in the sandbox.
Private reference pairs/scores require the comparison tool's own coverage and
calibration checks and remain outside this repository. No reference values feed generation.

For a current-generator baseline, pass `before --baseline-ref HEAD` explicitly; the default
baseline is the historical pre-quality producer. Audit the initial structural pass separately
with `--first-pass --out "$TURKU3D_WORK/reports/bridge-first/before"`. Use a separate output
parent to audit cold preparation. Initial and full passes use identical bridge survey inputs
and share the prepared road cache, so a mixed-pass crossing retains one deck profile.
Optional full-resolution roof refinement still waits for the full pass. Full-pass and initial-pass output
must not overwrite each other. Pass that audit parent as the renderer's optional third argument,
for example `node qa/render/bridge-quality.mjs after "$TURKU3D_WORK/reports/bridge-first"`.
For a standalone current-build inspection, use
`node qa/render/bridge-quality.mjs current "$TURKU3D_WORK/reports/bridge-quality/before"`;
it reads that build's inventory and grounds cameras without requiring an after build.
The renderer serves the immutable audit files on sandbox port base +3 while running;
the game development server uses port base +0. HTTP or page errors stop the render immediately.

## Latency diagnostics

`manifest.seconds` separates terrain, bridges, building shells, surface boundary cuts,
material extraction, collision and visual packaging. `package_*` separates binding, facade
atlases, batching, snapshots, texture codecs and compression/round-trip verification.

```sh
source ../sandbox.env
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python tools/perf/draft-speed.py
# First/full snapshots and timing JSON stay in $TURKU3D_WORK/reports/draft-speed.
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_latency.py
```

Full upgrade workers use `nice -n 10`; first-pass workers retain normal priority. Queue ETAs use first-pass times and their dedicated worker, independently of survey upgrades. Producer fixes invalidate failed-build backoff immediately.

### Building presence and footprint selection

The building source reader retains explicit CityGML surface roles and also reads complete
unlabelled `lod1Solid` shells from the same city layer. It classifies their surveyed bottom,
vertical sides and upper faces geometrically; terrain intersection curves and incomplete
solids cannot create buildings. The full buildings reader uses the same classification.
Disconnected ground components retain their own complete outlines and centroid tile ownership,
including surveyed components smaller than 4 m². Individual faces are never clipped by the
input search window. Facade enrichment and surveyed height refinement still run normally.

Synthetic source-selection checks:

```sh
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_building_source_surfaces.py pipeline/tests/test_coarse_roofs.py
```

### Existing stored facades

`turku3d.facade_assets` reads approved per-building facade descriptions and mapped
walls from the shared asset store, falling back to `textures/assembly` on the Mac.
`coarse.stored_facades.wall_meshes` preserves the extrusion geometry and projects
metre UVs from a matching surveyed wall of the same permanent building id. A wall
must be parallel within about 6 degrees, within 65 cm of its surveyed line and
covered for at least 80% of its length; otherwise the normal draft kit remains.
The material packager binds the original stored PBR maps before the generic kit.
No new reference acquisition or model call is involved.

```sh
PYTHONPATH=pipeline:textures .venv/bin/python -m pytest -q pipeline/tests/test_facade_assets.py
# OCI's systemd path watcher runs this after an existing source asset changes:
python -m turku3d.facade_assets --export /srv/turku3d/data/facade-assets
```

Approved `gis-<registry-id>` facade records and draft registry ids resolve to the
same building. Mapped wall primitives keep the original asset id when binding its
sidecar; the draft's collision/building ownership is unchanged. The effective base
material library and own ground arrays participate in the memoized content hash,
so a changed mirrored texture invalidates cached drafts even without a source edit.
Material lookup uses the sandbox assembly, shared facade store or shared texture
assembly; it does not require a full-tile `live` release.


### Incremental rebuilds and quality retention

Each tile stage caches its own code, config and exact input content in
`.stages/<tile>/` beside the service output directories. Cached upstream results feed downstream keys:
unchanged output stops invalidation. Packaging keys emitted meshes, material maps
and stored facade parameters/assets; mesh encoding also caches individual buffers.
Service scheduling edits do not change the producer identity. Geometry/material edits
change the manifest producer hash and request a fresh build.

A requested outdated tile immediately serves retained full visuals with `stale: true`
and their matching last-good collision while rebuilding. Both fragment endpoints prefer
the newest complete full pair, including `last-full.json`, and expose its producer as
`generation`. Changed or unknown input identities still schedule regeneration. New
first-pass collision stays paired with its basic visual and cannot attach under older
full geometry. Incomplete pairs are skipped; a visual without collision is never shown.
Resident clients poll every two seconds and swap prepared visual/collision pairs;
identical collision hashes retain resident colliders. Service logs report errors and
health exposes cumulative build failures and currently failed tile counts. Raw input
identities hash bytes, including rasters referenced by VRTs; timestamps only memoize
hash reads. Git commits and scheduling edits do not invalidate generator output.
Immutable old assets remain addressable for resident clients. Recent unfinished demand
is journaled in `.requests.json`; stage caches live outside the source tree.

Reproduce the real-tile cache comparison (build slot required):

```sh
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.cache_benchmark --help
```

The benchmark compares cold, warm, one-stage-code-changed and clean builds, asserts
identical payload bytes, and writes per-stage timings. See
[the measured results](../../../docs/research/incremental-drafts.md).

## Parked vehicles

Both draft passes publish `vehicles` and `vehicle_models`, using the existing parked-car
manifest contract. The `parking` prepared input retains complete OSM bay/road geometry
and city building/street exclusions. The independent `parked` stage hashes that input,
the actual terrain grid, obstacles and its own placement code. It does not add parked
meshes or boxes to the static tile: the client streams nearby collision-backed actors,
loads shared content-addressed model GLBs, and retains borrowed identities after tile
unload. Saved borrowed cars retain their model URL; trophy trucks use the built-in truck.

Placement reuses the full generator's seeded bay candidates and access exclusions,
adds surveyed buildings, water margins, bridge/tunnel/layer exclusions, barriers,
trunks and poles, checks all four support corners (15 cm maximum variation), and
requires a clear forward or reverse manoeuvre. Whole footprints stay 30 cm inside
the tile so neighbouring builds do not overlap. Slots retain the existing `<source/bay>` identities, including vehicles saved before
the draft-only transition. A SHA-256 draw per viable tile/slot selects the larger trophy truck with
probability 1/1000; an unfitting truck waits for the next fitting bay while ordinary
cars keep filling valid slots. No runtime rarity override is shipped. Tests can inject
`parked.truck_slot`; the existing Developer menu can spawn a truck for a drive check.

Focused checks:

```sh
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_parked.py
npm --prefix game test -- tests/unit/parked-streaming.test.ts tests/unit/coarse-tiles.test.ts tests/unit/vehicle-fleet.test.ts tests/unit/vehicleSave.test.ts
```

The broader original parked-layer integration also runs the Node/Rapier validator.
Prepare its locked dependencies with `npm ci --prefix tools/validator --no-audit --no-fund`
before selecting `pipeline/tests/test_parked.py` for a frozen post-deploy run. The snapshot
copies those dependencies; it does not install missing packages during verification.


### Quay underpasses and stair joins

Normal draft preparation discovers transverse lower walks through masonry bridge abutments
(`underpasses.py`). Explicit OSM tunnels win; complete transverse paths or aligned quay path
ends at opposite bridge edges provide a fallback. Sidewalk/crossing/step ways do not infer
passages. This uses full ways before tile clipping, and feeds both tunnel terrain cuts and
bridge support exclusions. No tile build override or hand-built geometry is required.
Existing photo-backed bridge material profiles identify masonry when OSM omits it.

Auransilta currently resolves the east mapped walk and the west quay gap. Both get stone
barrel vaults, approximately 2.4 m clear width, 2.7 m crown height, 1.5 m spring height and
0.3 m shell thickness; lengths follow OSM (east 12.63 m, west 17.02 m). These dimensions
are estimates, not surveyed clearances. Portal floors blend into the measured quay surface.
The generator adds warm emissive ceiling strips for gameplay visibility.

Reference: [MKFI, 9 July 2011, public domain](https://commons.wikimedia.org/wiki/File:Auransilta_Turku.JPG),
5184 × 3456 px, shows both round-headed openings, grey masonry, low paved quay landings,
and dark interiors. The photograph does not resolve interior lighting or exact dimensions.
Photos remain local; no reference pixels are shipped. The permit seller is inside the west
passage with a compact luminous floor ring and low sign; its dialogue/options are unchanged.

Stairs preserve mapped bends with shared mitered edges, so terrain cuts cannot expose gaps
between independent rectangular treads. One-metre landing aprons blend the full stair width
into ground; visible and collision faces share the same geometry. Terrain triangles whose
centres lie inside a stair cut retain their original material on the exterior remainder,
preventing small unsupported wedges beside the stairs and adjoining quay.

### Tunnel clearance after terrain enrichment

`Tunnels.openings` records convex clearance prisms above the generated tunnel
floors and portal aprons. After bank, stair and retaining geometry, the
`opening_clearance` stage subtracts those volumes from the visible terrain and
its collision, and from stair/retaining meshes. The cut interpolates UVs and
clips vertical faces in 3D, preserving buried ground and upper roads. Building
passages supply equivalent floor prisms while retaining their original relief.
This prevents mapped retaining edges crossing inferred quay arches from growing
solid aprons back through the opening (including the Auransilta market passage).
`test_tunnel_openings.py` covers the oblique passage in `t0_1_0`, vertical walls,
UVs, upper support and the shared visual/collision triangles. The selective OCI
probe also walks collision segments through the actual source passage and audits
existing cached openings without rebuilding unrelated tiles.
