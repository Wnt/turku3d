# Structures layer

World-local stairs, retaining walls, fences, railings, ramps and quays. This package
writes **intermediate layers only**; the assembler owns shipped `.col`, GLB and
3D Tiles. Coordinates, grids and material IDs come from `turku3d.config` and the
shared material table.

```sh
# From the worktree, with its sandbox.env already exported:
scripts/dev/gate
make -j2 structures-test
make structures-build
# Explicit ground input, if staged at another location:
make structures-build ARGS="--ground-layer $TURKU3D_WORK/build/layers/ground"
```

The native environment from `make pipeline-bootstrap` supplies numpy, scipy,
shapely, geopandas, laspy/lazrs and pytest. The physics target installs the locked
validator Node dependencies if needed. Tests use small synthetic data and never
read raw LAZ or start a server. Ports are not needed. Physics fixtures are written
to `$TURKU3D_WORK/build/structures-tests/scenes.json`.

## Inputs and ground handoff

Raw inputs are read-only:

- `raw/wfs/work-3x3/kanta_Rakenne.parquet`: 66626 stair nosings, supporting 66618
  edges, 66634 wall tops, 66630/66632/66636 fences, 62540 ramp lines.
- `raw/osm/work-3x3/work-3x3-261024.parquet`: uncovered `highway=steps` and structural
  barriers. City geometry wins; only uncovered OSM portions are retained. OSM
  steps use their `width` tag or a 2 m default, which needs real-data review.
- Point clouds in `raw/laz/pistepilvi` or `raw/laz-3x3`: cropped on read to the four
  office visual tiles plus the configured margin. No CRS is inferred from LAZ.
- `$TURKU3D_WORK/build/layers/ground/MANIFEST.json` and `*.col.npz`: authoritative
  ground contacts and ramp triangles (`material=ramp`). Water is excluded from
  ground contact queries; missing coverage is an error, not a nearest fill.

The reader consumes W1-01's `ground/quays.json` directly. Each entry contains
`object_id`, `kind: "quay"`, `centers_xz`, `side_heights` and `normals_xz`. Heights
are N2000 metres; each station has two measured side elevations. Structures uses
the lower/upper elevations without repeating the ground classification. An empty
list explicitly means no classified quays; a missing export is an error. The
unused provisional GeoJSON and material-face decoders have been removed.

```sh
make structures-build ARGS="--ground-layer $HOME/turku3d/wt/w1-01-ground/work/build/layers/ground"
```

The producer sandbox is read-only. Before measuring, the build checks artifact
hashes and reads a consistent copy into `$TURKU3D_WORK/cache/structures-ground/`.
A changed or partially published producer output fails closed. Snapshot evidence
records the source path, manifest/code hashes and upstream acceptance status.
All collision/visual output and reports stay in this sandbox.

Ramp survey lines currently require coverage by ramp-labelled ground triangles.
The real input contains no material-18 triangles, so these lines remain blocking;
reconstructing their footprints from survey edges or agreeing an explicit ramp
mask with W1-01 is still needed. The code does not invent a footprint around an
ambiguous line merely to make coverage pass.

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
- Walls use sampled lower ground and lidar tops, a 0.3 m thickness and mitered
  bends. Independent top-height quantiles must agree within 10 cm. Fences and
  railings use 0.08 m closed profiles, class-1 p95 heights where supported, and
  1.2 m otherwise. The report counts default-height stations.
- Each ground-classified quay gets a closed stone wall and a 1.2 m railing.
  Ramp triangles retain their ground positions exactly.

Real-data accuracy is **not established by the synthetic tests**. Sparse lidar,
uneven landing crossfall, ambiguous survey grouping, and uncovered ramp lines
produce explicit blocking issues. The synthetic and real car checks use a 1,500 kg CCD box at 10 m/s; a full
raycast-vehicle run over the assembled AOI belongs to W1-05. Real probes combine
the supplied ground with generated solid/query geometry: each emitted flight in
both directions, representative wall/fence interiors, and quay crossings every
10 m. Barrier probes cross segment interiors rather than open endpoints. Failed
or unplaceable routes are blocking and retain their measured progress/heights.
`physics-cases.json` and `physics.json` live in the sandbox report directory.

## Output

`$TURKU3D_WORK/build/layers/structures/` contains:

- `c_i_j.col.npz`: float64 `(N,3)` world-local positions, uint32 `(M,3)` indices,
  uint8 `(M,)` materials, including query-only helper triangles.
- `t0_i_j.vis.glb`: world-local metres, one node/mesh per structure component;
  extras contain `layer`, `object_id`, `material_key`, and flight metadata.
  Horizontal UVs follow translated E/N metre axes; wall UVs use along-face and
  above-base metres. Texture binding remains the assembler's responsibility.
- `MANIFEST.json`: input content hashes, parameters, code hash, status, tile list,
  and hashes of every output. `BLOCKED` is not ready to ship.
- `metrics.json` and `report.md`: object counts, per-object evidence and issues.

Material keys are `stone_stairs`, `concrete_wall`, `metal_fence`, `metal_railing`,
`granite_quay`, and `concrete_ramp`. Their texture-library assets are a downstream
integration requirement; the layer GLBs currently use neutral base colours.

Clipping splits each mesh globally against grid planes before assigning cells.
Canonical intersections are shared bit-for-bit, including four-tile corners. Tile
boundary vertices where a structure merely touches an adjacent occupied cell are
retained without duplicate triangles. Assemblers must preserve these boundary
vertices if validating complete vertex-set seams. Tests also compare the actual
referenced intersections before that boundary completion and conserve surface area.

Unchanged, hash-verified layer products are reused before LAZ decompression; input
hashing and survey/ground reads still occur. A damaged output is regenerated, and
stale owned tiles are removed when the manifest is replaced. No missing-input
run publishes a new layer manifest. Every run writes its latest status to
`$TURKU3D_WORK/reports/structures/{metrics.json,report.md}` and exits nonzero for
blocking issues. `PASS` requires both geometry evidence and real-ground Rapier
probes to pass; assembled runtime and full vehicle QA remain W1-05 integration work.
