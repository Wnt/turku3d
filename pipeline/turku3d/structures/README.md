# Structures layer

World-local stairs, retaining walls, fences, railings, ramps and quays. This package
writes **intermediate layers only**; the assembler owns shipped `.col`, GLB and
3D Tiles. Coordinates, grids and material IDs come from `turku3d.config` and the
shared material table.

```sh
# From the worktree, with its sandbox.env already exported:
scripts/dev/gate
make -j2 structures-test
make structures-build                          # every listed tile; only stale ones rebuild
make structures-build ARGS="--tiles t0_3_0"    # one tile
```

The layer is built per listed L0 tile ([`turku3d.tile_layer`](../tile_layer.py),
`producer.py`). The native environment from `make pipeline-bootstrap` supplies numpy, scipy,
shapely, geopandas, laspy/lazrs and pytest. The physics target installs the locked
validator Node dependencies if needed. Tests use small synthetic data and never
read raw LAZ or start a server. Ports are not needed. Physics fixtures are written
to `$TURKU3D_WORK/build/structures-tests/scenes.json`.

## Inputs and ground handoff

Raw inputs are read-only and come from each tile's own input area (`config.source_area`:
work-3x3 while it holds the tile, else `tile-<id>`):

- `raw/wfs/<area>/kanta_Rakenne.parquet`: the city survey, citywide: 66626 stair nosings,
  supporting 66618 edges, 66634 wall tops, 66630/66632/66636 fences, 62540 ramp lines.
- `raw/osm/<area>/*.parquet`: uncovered `highway=steps` and structural barriers
  (`fence`, `railing`, `guard_rail`, `handrail`, `wall`, `city_wall`, `retaining_wall`).
  City geometry wins; only uncovered OSM portions are retained. OSM steps use their `width`
  tag or a 2 m default.
- Point clouds in `raw/laz/pistepilvi` or `raw/laz-3x3`, cropped to the tile plus 5 m.
- `$TURKU3D_WORK/build/layers/ground/MANIFEST.json` and `*.col.npz`: authoritative
  ground contacts and ramp coverage. Water is excluded from
  ground contact queries; missing coverage is an error, not a nearest fill.

Survey lines are clipped to the tile, so a tile owns the structures inside it; a complete
object that pokes past the tile edge stays whole in the tile's own collision tiles. A tile's
key covers its survey and OSM clips, the LAZ files and the ground collision tiles within 5 m
(plus `quays.json` and `ramps.json`), so adding a tile builds only that tile.

Every object is checked against lidar and the ground (below) and probed with the real-data
Rapier character (`acceptance.validate`). An object that fails either is **omitted** from the
layer and listed under its tile's `issues` in `metrics.json` (`omitted_meshes` counts its
meshes); everything else ships. `params.acceptance_passed` therefore holds for every
published layer: no shipped object failed a check. Per-tile physics evidence is under
`$TURKU3D_WORK/reports/structures/<tile>/`.

The reader consumes W1-01's `ground/quays.json` directly. Each entry contains
`object_id`, `kind: "quay"`, `centers_xz`, `side_heights` and `normals_xz`. Heights
are N2000 metres; each station has two measured side elevations. Structures uses
the lower/upper elevations without repeating the ground classification. An empty
list or missing export means no classified quays, as in plain ground. The
unused provisional GeoJSON and material-face decoders have been removed.

```sh
make structures-build ARGS="--ground-layer $TURKU3D_DATA/build/layers/ground"
```

The producer sandbox is read-only. Before measuring, the build checks artifact
hashes and reads a consistent copy into `$TURKU3D_WORK/cache/structures-ground/`.
A changed or partially published producer output fails closed. Snapshot evidence
records the source path, manifest/code hashes and upstream acceptance status.
All collision/visual output and reports stay in this sandbox.

Ramp survey lines use ground's `ramps.json` measured coverage handoff. The ground
producer distinguishes traversable slopes from steep wall faces and keeps grassy
surfaces as grass. Structures checks each ramp survey line against that handoff;
the ground layer itself owns ramp collision and visuals.

## Geometry and evidence

- Nearby, parallel, overlapping 66626 nosings form flights. The mixed 66618 code
  can support a flight but cannot independently create one. Isolated/bent stair
  lines are reported for review instead of guessing a footprint.
- Lidar landing estimates use local planes, class-2-supported class-12 candidates
  and separate fit/holdout points. Both ends and both width edges are checked.
  Landing errors above 5 cm, ground contact discrepancies above 2 cm, and slopes
  above 40 degrees block acceptance. Steep flights are retained and flagged.
- Riser count is `max(1, round(rise / 0.16))`. A flight has one sloped solid,
  per-tread horizontal/vertical query faces, and closed side cheeks. Query faces
  also supply the visible treads. The shared `stair_tread_helper` class prevents
  treads from blocking the KCC or vehicles; foot IK can raycast them.
- Retaining walls use measured ground surfaces 0.6 m on either side of surveyed
  top lines, a 0.3 m thickness and mitered bends. Standalone OSM walls use
  tagged heights or independent lidar tops. Fences and
  railings use 0.08 m closed profiles, class-1 p95 heights where supported, and
  1.2 m otherwise. The report counts default-height stations.
- Each ground-classified quay gets a closed stone wall and a 1.2 m railing.
  Ground owns ramp collision and visuals.

Real-data accuracy is **not established by the synthetic tests**. Sparse lidar,
uneven landing crossfall, ambiguous survey grouping, and uncovered ramp lines
produce explicit issues; the objects concerned are omitted. The synthetic and real car checks use a 1,500 kg CCD box at 10 m/s; a full
raycast-vehicle run over the assembled AOI belongs to W1-05. Real probes combine
the supplied ground with generated solid/query geometry: each emitted flight in
both directions, retaining walls approached from lower terrain, representative
fence interiors, and quay crossings every
10 m. Barrier probes cross segment interiors rather than open endpoints. An object with a
failed or unplaceable route is omitted; its measured progress/heights stay in the report.
`physics-cases.json` and `physics.json` live in the sandbox report directory.

## Output

`$TURKU3D_WORK/build/layers/structures/` contains:

- `c_i_j.col.npz`: float64 `(N,3)` world-local positions, uint32 `(M,3)` indices,
  uint8 `(M,)` materials, including query-only helper triangles.
- `t0_i_j.vis.glb`: world-local metres, one node/mesh per structure component;
  extras contain `layer`, `object_id`, `material_key`, and flight metadata.
  Horizontal UVs follow translated E/N metre axes; wall UVs use along-face and
  above-base metres. Texture binding remains the assembler's responsibility.
- `t0_i_j.records.json`: the tile's shipped object measurements.
- `MANIFEST.json`: shared `turku3d-layer` v1 inventory, per-tile keys (`tile_builds`),
  parameters including `acceptance_passed` (no shipped object failed a check), object and
  omitted counts, code hash and tile hashes/sizes.
- `metrics.json` and `report.md`: per-tile object counts, evidence and issues (omitted objects).

`make refresh` rebuilds stale tiles of this layer after ground; office assembly requires
`acceptance_passed: true` plus collision and visual tiles.

Material keys are `stone_stairs`, `concrete_wall`, `metal_fence`, `metal_railing`,
`granite_quay`. Their texture-library assets are a downstream
integration requirement; the layer GLBs currently use neutral base colours.

Complete objects (`params.seams=objects`) go to every collision tile of their own L0 tile they
touch and to that tile's visual; no grid clipping or face duplication occurs.
All NPZ, GLB and manifest publication uses `turku3d.layers`; the v1 reader verifies
these files in the synthetic regression. Cross-grid tests preserve every original
triangle exactly, including helpers and cheeks.

Unchanged, hash-verified layer products are reused before LAZ decompression; input
hashing and survey/ground reads still occur. A damaged output is regenerated, and
stale owned tiles are removed when the manifest is replaced. No missing-input
run publishes a new layer manifest. Every run writes its latest status to
`$TURKU3D_WORK/reports/structures/{metrics.json,report.md}` and exits nonzero for
blocking issues. `PASS` requires both geometry evidence and real-ground Rapier
probes to pass; assembled runtime and full vehicle QA remain W1-05 integration work.
