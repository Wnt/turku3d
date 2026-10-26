# Buildings layer

Run from the repository root with the sandbox environment loaded:

```sh
source ../sandbox.env
make buildings-test
make buildings-build
make buildings-outer
make buildings-validate
bash pipeline/env/run.sh python -m turku3d.buildings.audit
bash pipeline/env/run.sh python -m turku3d.buildings.coverage
```

`buildings-build` runs native roofer with four jobs. By default it reuses a content-keyed
reconstruction and class-6 point arrays in `$TURKU3D_DATA/cache/buildings`, shared between
sandboxes. The cache uses locks and publishes point arrays atomically. An explicit
`--roofer-output` must point to a completed CityJSONSeq directory, not its parent.

Inputs are immutable `raw/wfs/work-3x3/{kanta_Rakennus,GIS_Rakennukset,bldg_Building_LOD2,GIS_Keskilinjat}.parquet`,
class-6 points in `raw/laz/pistepilvi`, OSM GeoParquet, and `raw/ortho/ortho2022to/work-3x3`.
The WFS cache preserves CityGML polygon rings, holes and semantic surface types.
The default producer region is `config.PLAYABLE_TILES`, the four complete L0 tiles.
The inventory keeps the established kantakartta IDs and adds GIS building outlines absent
from that source. Three GIS records in the playable tiles are points with no surveyed
outline; each receives a 6 m square, labelled placeholder and is reported as point-only.
`--tiles t0_i_j [...]` selects another tile or region. Buildings touching selected tiles
retain their complete footprints, including parts outside the boundary. A cross-tile
building is owned by the requested tile containing the largest footprint area, so a
four-tile build includes its visual shell.
Footprint inventory includes kantakartta codes 66000 and 66001; no building is removed because
lidar or a candidate model is missing. Shared data is never modified.

`make buildings-outer` uses the immutable work-3x3 GIS inventory and nine lidar
tiles to publish visual-only massing outside the playable square under
`$TURKU3D_WORK/build/layers/buildings_outer`. Roof heights come from class-6 returns
inside each outline, or nearby class-6 returns when an outline has no returns;
bases use the nearest class-2 ground return. Its `metrics.json` reports source
records, lidar extrusions, missing height evidence and counts per L0 source tile.
Packaging the playable four tiles includes surrounding T1/T2 parents containing
those L0 building visuals.

## Geometry and selection

CityGML and roofer LoD2.2 polygons use dominant-plane earcut triangulation, including holes.
Boundary columns snap to kantakartta within 1 m; all heights in a column receive the same
horizontal displacement. Shared surface coordinates are welded as float64 values without independently rounding
projected edge coordinates (which would introduce slivers).
Existing vertices on triangulated edges split T junctions. Concave surfaces never use fan
triangulation. A candidate must have nondegenerate triangles, consistent outward winding,
closed manifold edges, positive volume, at most 2,000 triangles and footprint-boundary
Hausdorff distance ≤ 0.3 m. A failed candidate stays in the report with its reason.

Selection measures exact Euclidean point-to-triangle distances against **all class-6
points inside the reference footprint**, after snapping. Facades and roof steps are
included: a vertical roof-only distance mis-scores wall returns as roof errors. An AABB
tree prunes only triangles whose bounding boxes cannot improve the current distance;
tests compare it with exhaustive triangle queries. The old vertical-roof statistics
remain in `roof_vertical`, including infinite uncovered points. Neither metric trims
outliers or downsamples lidar. Both use linearly interpolated empirical percentiles.
Candidates satisfying the unchanged 10 cm median / 25 cm p75 limits rank first.

When both source models fail geometry or residual checks, the builder also evaluates a
kantakartta footprint extrusion. Its roof height is the median elevated class-6 height
inside that footprint. Where class 6 is absent, it first seeks a local, near-horizontal
plane in overlap-class (12) returns: 1,000 seeded RANSAC trials, 8 cm tolerance,
≥20 points, ≥10% support and ≥50% footprint-area coverage. Class 12 may contain
vegetation, so this remains a provisional height proxy and is never scored as class-6
accuracy evidence. If no plane is supported, it uses the nearest class-6 neighbourhood
within 100 m. Its base uses roofer's lidar ground estimate. The report
records source, point count, height range, support distance, reason and selected status.
An existing closed model remains selected if it fits better than the extrusion.
Nearby points **never** count as in-footprint residual evidence. No evidence means a
hard error, not an assumed storey height. Every fallback passes the same manifold,
triangle-budget and footprint checks. Coverage and measured accuracy are separate:
`pass` retains strict accuracy acceptance and `shell_coverage_pass` checks all shells.
A valid shell with failing residuals is exported with explicit degradation. Under the
current release scope, build exit status and manifest `params.geometry_acceptance` follow
complete shell coverage; `params.accuracy_acceptance` and `metrics.json.pass` retain the
unchanged residual check. No inferred heights silently pass accuracy acceptance.

## Artifacts

For four-corner roofs with supported class-6 returns, the fallback fits a closed
gable, hip or two-level roof from height profiles. Other roofs use the measured flat
extrusion. If no usable model
or lidar shell exists, the fallback publishes a closed footprint placeholder using
building levels or 10 m above lidar ground. Its walls carry amber `WORK IN PROGRESS`
artwork with building ID and an OSM or kantakartta street address where available. Placeholder facades
are excluded from photo binding.

`python -m turku3d.buildings.coverage` audits delivered visual GLBs against all
`GIS_Rakennukset` polygon and point records intersecting the selected tiles and writes
`coverage.json` and a top-down `coverage.png` under
`$TURKU3D_WORK/reports/building-coverage/`. Use `--layer`, `--tiles`, and `--out`
to compare another layer or region. Green means model, blue lidar shell, amber
placeholder, and red missing.

All output is under `$TURKU3D_WORK/build/layers/buildings`:

- `c_i_j.col.npz`: float64 world-local positions, uint32 triangle indices, uint8 materials.
  The optional uint32 `building_index` per triangle indexes
  `metrics.json.collision_building_ids` for isolated validation. Walls and base use `building`; roofs use `roof`. Each closed shell is stored once in its footprint-centroid collision tile, per
  the shared object ownership contract. No grid clipping creates delivery slivers.
- `t0_i_j.vis.glb`: world-local geometry, metre UVs, one mesh/node per planar surface.
  Whole objects belong to the visual tile containing the footprint representative point.
  Assembler culling bounds must include the complete object, which can cross the tile edge.
- `facades.geojson`: [facade schema](../../../docs/specs/facades.md).
- `footprints.geojson`: every expected footprint in engine x/z, including missing shells.
- `metrics.json`, `metrics.md`: per-candidate metrics, choice, semantics and acceptance failures.
- `MANIFEST.json`: shared `turku3d-layer` v1, published by `turku3d.layers.write_manifest`.
  Inputs map paths to SHA256; tiles map filenames to verified byte counts and hashes.
  Acceptance and revision are in `params`; detailed metrics and facade inventories are sidecars.
- `walkers.json`: generated by `buildings-validate`.

The layer never writes shipped `.col`, tilesets, texture assets or compressed delivery GLBs.
The assembler binds material keys and handles final compression.

The exported-data audit checks collision closure, winding, volume, the 2,000-triangle
budget, continuous tile seams where applicable, and finite visual attributes/metadata.

The Rapier check consumes the **exported NPZ triangles** through a temporary JSON bridge.
It uses triangle provenance to test each building independently, including nested parts.
Enclosing neighbours cannot hide an open wall. It runs 2,000 seeded 10-second walkers with the project's character controller and rejects
any entry, not just final positions inside a footprint. A horizontal support pad at each
shell base isolates building blocking from ground availability. The integrated ground,
vehicle and streaming checks remain the assembler/game validator's responsibility.

Tests use a two-building fixture with a concavity, courtyard, grid crossings, plus a
negative open-wall Rapier fixture. They do not read real city data. Node dependencies:
`npm ci --prefix tools/validator --ignore-scripts`.

The current measurements, degraded building IDs and exact verification results are in
[the measured report](../../../docs/reports/buildings.md).

Shared manifest and assembler verification (reuses the published layer):

```sh
bash pipeline/env/run.sh python -c 'from turku3d.layers import layer_root, read_manifest; m = read_manifest(layer_root() / "buildings"); print(m["format"], len(m["tiles"]))'
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
```
