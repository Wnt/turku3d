# Parked cars (`traffic_static`)

Run from a sourced sandbox, with the existing native pipeline environment:

```sh
bash pipeline/env/run.sh python -m turku3d.parked                    # every listed tile; only stale ones rebuild
bash pipeline/env/run.sh python -m turku3d.parked --tiles t0_3_0     # one tile
bash pipeline/env/run.sh python -m turku3d.parked.verify
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_parked.py
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
```

The layer is built per listed L0 tile ([`turku3d.tile_layer`](../tile_layer.py), `layer.py`,
seed 16). A tile reads its own OSM clip and city `GIS_Katualueet` (`config.source_area`) and the
published collision heights, and places cars whose whole footprint lies in the tile at least
`SEAM_CLEARANCE` (0.3 m) from its edges, so cars of separately built tiles keep the 0.55 m
spacing at every seam. Its key covers those sources, the assets, the seed, the code and the
placement context (every other layer's collision within 20 m and the props and trees there).
Outputs are `$TURKU3D_WORK/build/layers/traffic_static`: shared-publisher NPZ box primitives,
GLBs (one mesh per car), `t0_i_j.records.json`, a completed `MANIFEST.json` (with
`params.placements_sha256`), and `placements.json` combined from every tile's cars.
The assembler discovers this layer. It publishes `placements.json` as `manifest.vehicles`
and shared model/paint GLBs as `manifest.vehicle_models`, omitting the static car meshes
and boxes from shipped tiles so each car can move without duplicate collision.
`clear_front_m` / `clear_rear_m` count the other parked cars of the same tile.

Four CC0 Kenney Car Kit 3.1 models are bundled as authored OBJ geometry: sedan,
hatchback, SUV and van. Dimensions are normalized to real passenger-vehicle sizes;
six seeded paint colours retain the original tyres, glass, lamps and trim.
The source palette is embedded in each visual object. Material 28 is the existing
solid street-furniture metal material, so both the character and driven vehicle collide with it.

Placement uses explicit surface parking polygons and parallel on-street parking
lanes. Narrow polygons use 6.2 m parallel bays; lots use 2.65 m bay width and 6 m
access aisles. Occupancy is 80%, keyed by seed and stable source/candidate ID.
Complete oriented footprints must fit the bay and AOI, clear other cars by 0.55 m,
and avoid buildings, sidewalks/cycleways, mapped paths, roads, access roads,
entrances, crossings (5 m buffer), and a 12 m player/driven-car spawn envelope.
Underground/rooftop/multistorey parking is excluded. Geometry order cannot affect
seeded results. Objects crossing tile boundaries have exactly one centroid owner.

The [shared placement validator](../placement/README.md) samples the published collision
at the centre and four base corners, enforces the 0.15 m support limit, snaps height,
checks other layer inventories, and repairs small offsets within 2 m. Missing support
or unsafe placements are omitted and reported. Residual hard violations fail the build.
The original seeded bay, lane and access exclusions remain in force.

Every car must also be able to drive out (`boxed_in` otherwise). The manoeuvre envelope in
[`../placement/manoeuvre.json`](../placement/manoeuvre.json) is measured against the collision
of every other published layer (buildings, structures, props, bridges, vegetation, ground):
its footprint plus 1.5 m on each side, and an exit lane of that width reaching 6 m ahead of
**or** behind it, must hold no wall, fence, railing, building, pole, trunk, bollard or street
furniture between 0.25 m above its base and its roof. A nose-in car against a wall passes; a
car with a fence behind and a wall ahead does not. The validator first tries the 2 m repair
grid, then drops the car with its violations in `reports/placement/traffic_static/<tile>/placement.json`.
`placements.json` (and `manifest.vehicles`) record `clear_front_m` / `clear_rear_m`: the
straight body-width distance to the first obstacle or other parked car, capped at 30 m.
The real-tile e2e drives the car with the longest clear reverse path.

`make refresh` rebuilds this layer last, after every collider it checks, whenever its code,
the envelope, the placement code or any input layer manifest changes.

## Asset provenance

[Kenney Car Kit](https://kenney.nl/assets/car-kit), CC0 1.0, downloaded 2026-10-25.
Original license: `assets/LICENSE.txt`. Archive:
`https://kenney.nl/media/pages/assets/car-kit/1a312ec241-1775131960/kenney_car-kit.zip`
SHA-256: `fac7dacac5c7874348cf19729af3ef205f3d366493edaf0a827d93f4fdf3d0c4`.
Files copied verbatim from `Models/OBJ format/{sedan,hatchback-sports,suv,van}.obj`
and `Models/OBJ format/Textures/colormap.png`. The OBJ loader reads authored normals,
UVs and triangulates polygon faces; no external MTL lookup is needed. No paid tools.

## Verification and first-version evidence

`python -m turku3d.parked.verify` checks the placement audit hash, unique IDs,
whole-footprint access clearance and spacing, then writes temporary `.col` fixtures
under `$TURKU3D_WORK/reports/parked/collision`. `tools/validator/parked.mjs` decodes
these with the shared reader and builds them with `buildTileColliders`. Every box
must block both player and vehicle collision-group rays and capsule/chassis sweeps.
Run `npm ci --prefix tools/validator` and `npm ci --prefix game` during environment
setup; the gate never installs dependencies. The synthetic pytest also invokes
this same Rapier check for all four models and multiple yaw angles.

Office core after the manoeuvre envelope (seed 16, 2026-10-26, survey-backed structures):
**15 cars**. `osm:way:1413262871:0:1:0` (van) is dropped: a fence 0.5–2.1 m behind it and a
building wall 3.1–3.9 m ahead box it in, and no repair offset within 2 m clears both.

Earlier office-core evidence (seed 16, 2026-10-25): **16 cars**, four models, six colours;
**32 rays + 32 shape sweeps pass**, zero mapped access obstructions/overlaps.
Twelve cars occupy the mapped parking lot north of the office; four occupy smaller
street-side/courtyard parking polygons. Street segments tagged `parking:both=no`
remain empty. Screenshot: `$TURKU3D_WORK/reports/parked/parked-street.png`;
overview: `parked-aoi.png`; audit: `physics.json` in the same directory.
The screenshot is a Chrome layer preview with measured ground and building-shell
context, before facade/ground texture binding. It is not a performance benchmark.

The normal gate is `scripts/dev/gate --base main`. Main includes focused routing
for parked producer edits; package-data changes still select the whole pipeline.
For scoped verification after a rebase, run the pytest command above with `-n 2`
(at most three test workers), then the published-layer verification command.
