# Bridges layer

Build the mapped spans in the four Phase 1 L0 tiles from read-only `work-3x3`
GIS:Katualueet, OSM `bridge=yes` ways and the 2021 city lidar. The producer writes
only `$TURKU3D_WORK/build/layers/bridges/`; the assembler makes the shipped tiles.

```sh
source ../sandbox.env
make bridges-test
make bridges-build
make bridges-textures
scripts/dev/gate --base main
# Stage the already-published inputs read-only when using a fresh sandbox:
for layer in ground buildings props vegetation traffic_static; do
  ln -s "$TURKU3D_DATA/build/layers/$layer" "$TURKU3D_WORK/build/layers/$layer"
done
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make tile-show
# Browser traversal and near-bank screenshots on both backends:
cd game && E2E_TILE_PORT=$((TURKU3D_PORT_BASE + 1)) TILE_DIST="$TURKU3D_WORK/dist/tiles" npx playwright test -c playwright.tile.config.ts tests/e2e-tile/bridge.spec.ts
```

City `Silta` polygons provide the outlines of Auransilta, Kirjastosilta and the
full span of Teatterisilta, including its west shore outside the L0 boundary. OSM supplies crossing names, vehicle access and
unmapped short paths. Named city outlines absorb unnamed OSM ways inside them.
Deck stations use lidar class 17 when enough returns exist; otherwise class 12
returns inside the footprint. Unsupported short spans interpolate between class 2
abutments, rejecting river-bed elevations using the class 9 water level. The real
AOI has no class 17 points. `reports/bridges/report.json` records the source class
and height range for each crossing.

The shore survey samples both ends of each complete river crossing along its
full width. It compares class 2 lidar with the published ground collision mesh,
chooses land contacts agreeing within 6 cm, and records nearby quay top/bottom
elevations. Approach strips connect the lidar deck profile to those contacts and
clear the intervening ground mesh. The three short mapped gangways terminate in
the river and have one land contact each. `reports/bridges/report.json` lists
every contact, waterline station, measured lidar/ground residual and ramp join
error. Teatterisilta requires the lidar tile immediately west of the L0 area;
its west contact is lidar-supported while the current published ground collision
has no x<0 tile. The report marks that contact `ground_available: false`.

Auransilta has separate road and raised sidewalk surfaces, a 12 cm kerb, edge
railings and piers down to the measured water elevation. All deck triangles carry
collision material 24 (`bridge_deck`); railings and kerbs have their own solid
materials. `make bridges-textures` stages read-only, shared ground library
textures: asphalt road, granite stone walks and a smooth painted finish for
steel railings. It also stages the ground splats and array maps for live tile
assembly. The ground and water layers are never modified. Complete bridge
objects follow the layer v1 `seams=objects` ownership rule.

The model is an initial geometric reconstruction. Railing subdivisions follow
city polygon edges and have not been checked against reference photography.
