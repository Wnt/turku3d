# Parked cars (`traffic_static`)

Run from a sourced sandbox, with the existing native pipeline environment:

```sh
bash pipeline/env/run.sh python -m turku3d.parked --seed 16
bash pipeline/env/run.sh python -m turku3d.parked.verify
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_parked.py
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
```

The default build covers the 250 m office core (`c_1_2` through `c_4_5`).
The producer reads the latest cached `raw/osm/work-3x3/*.parquet`, city
`GIS_Katualueet.parquet`, and published ground/structures collision heights. `--osm PATH`
pins a particular snapshot. It never downloads or writes raw data. Outputs are
`$TURKU3D_WORK/build/layers/traffic_static`: shared-publisher NPZ box primitives,
GLBs (one mesh per car), a completed `MANIFEST.json`, and `placements.json`.
The assembler discovers this layer. It publishes `placements.json` as `manifest.vehicles`
and shared model/paint GLBs as `manifest.vehicle_models`, omitting the static car meshes
and boxes from shipped tiles so each car can move without duplicate collision.

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

Office-core evidence (seed 16, 2026-10-25): **16 cars**, four models, six colours;
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
