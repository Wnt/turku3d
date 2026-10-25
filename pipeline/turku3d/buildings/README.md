# Buildings layer

Run from the repository root with the sandbox environment loaded:

```sh
source /Users/wnt/turku3d/wt/w1-03-buildings/sandbox.env
make buildings-test
make buildings-build
make buildings-validate
bash pipeline/env/run.sh python -m turku3d.buildings.audit
```

`buildings-build` runs native roofer with four jobs. To reuse a completed reconstruction:

```sh
bash pipeline/env/run.sh python -m turku3d.buildings \
  --roofer-output "$TURKU3D_WORK/cache/buildings/roofer"
```

Inputs are immutable `raw/wfs/work-3x3/{kanta_Rakennus,bldg_Building_LOD2,GIS_Keskilinjat}.parquet`,
class-6 points in `raw/laz/pistepilvi`, OSM GeoParquet, and `raw/ortho/ortho2022to/work-3x3`.
The WFS cache preserves CityGML polygon rings, holes and semantic surface types.
The area is the four visual tiles intersecting `config.OFFICE_BLOCK`, plus `config.MESH_MARGIN`.
Buildings touching the area retain their complete footprints, including parts outside the margin.
Footprint inventory includes kantakartta codes 66000 and 66001; no building is removed because
lidar or a candidate model is missing. Shared data is never modified.

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
A valid shell with failing residuals is exported with explicit degradation and the
strict build exits 1. No inferred building heights silently pass accuracy acceptance.

## Artifacts

All output is under `$TURKU3D_WORK/build/layers/buildings`:

- `c_i_j.col.npz`: float64 world-local positions, uint32 triangle indices, uint8 materials.
  The optional uint32 `building_index` per triangle indexes
  `metrics.json.collision_building_ids` for isolated validation. Walls and base use `building`; roofs use `roof`. Each triangle is cut once at global grid
  planes; canonical cached intersections give bitwise-identical shared boundary vertices.
  The complete building is closed; tile fragments intentionally have open clipping boundaries.
- `t0_i_j.vis.glb`: world-local geometry, metre UVs, one mesh/node per planar surface.
  Whole objects belong to the visual tile containing the footprint representative point.
  Assembler culling bounds must include the complete object, which can cross the tile edge.
- `facades.geojson`: [facade schema](../../../docs/specs/facades.md).
- `footprints.geojson`: every expected footprint in engine x/z, including missing shells.
- `metrics.json`, `metrics.md`: per-candidate metrics, choice, semantics and acceptance failures.
- `MANIFEST.json`: input SHA256s, parameters, source hash, revision, tile and artifact lists.
- `walkers.json`: generated by `buildings-validate`.

The layer never writes shipped `.col`, tilesets, texture assets or compressed delivery GLBs.
The assembler binds material keys and handles final compression.

The exported-data audit checks collision closure, winding, volume, the 2,000-triangle
budget after clipping, bitwise tile seams, and finite visual attributes/metadata.

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
