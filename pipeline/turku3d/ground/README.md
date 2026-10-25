# Ground layer

`python -m turku3d.ground.build` builds the four Phase 1 L0 tiles and their 64 collision
children, using the shared read-only `work-3x3` inputs and the canonical 50 m margin.
It produces **layer intermediates**, never shipped `.col` files or a tileset. W1-04
assembles these with buildings and structures.

```sh
source ../sandbox.env
make ground-test
make ground-build
# Re-run: verifies artifact hashes and returns without rewriting cached tiles.
make ground-build
# Explicit measured N2000 water height, only if automatic lidar support is unavailable:
make ground-build GROUND_ARGS="--water-level 0.12"
```

Outputs use `$TURKU3D_WORK/build/layers/ground/`:

- `c_i_j.col.npz`: float64 world-local `positions`, uint32 triangle `indices`, uint8
  per-triangle `material`; no pickled Python objects.
- `t0_i_j.vis.glb`: world-local glTF meshes with `{layer, object_id, material_key}` extras,
  planar E/N-oriented metre UVs and separate visual curb bevels.
- `quays.json`: lines with fitted steps over 1 m, including centers, normals and side
  elevations, for the structures layer. No quay curb geometry is emitted.
- `MANIFEST.json`: input SHA-256 identities, parameters, code hash, artifact hashes,
  tiles written and acceptance status. A failed acceptance result is explicitly recorded.

`$TURKU3D_WORK/reports/ground/ground-qa.{json,md}` records coverage, pavement noise MAD,
held-out lidar RMSE, curb step/width/control results, triangle budgets and seam equality.
`ground-profiles.png` plots sampled held-out curb profiles. The build exits **2** when
any acceptance check fails; producing files does not imply they are ready to ship.

## Method and validation

Coordinates and tile sizes come only from `turku3d.config`. Lidar is split by a stable
coordinate hash before processing: 80% fitting, 20% withheld. Class 12 is accepted only
within 15 cm of a robust local class-2 plane. Merged returns are denoised with iterative
local-plane residual trimming; class-2 validation returns are never residual-trimmed.

Codes 62520/62522/62523 are sampled every metre. B3 snapping locates the strongest
smoothed lidar-profile gradient within 45 cm, then fits sloping surfaces on both sides. Sloping fitting profiles use +/-35 cm longitudinal strips. Only even stations set
breakline geometry and snapping; odd stations assess the emitted mesh using the separate
20% withheld lidar returns (+/-75 cm support). Ill-conditioned side fits and slopes over
45 degrees are rejected. Source-vector corners retain heights interpolated from fitting
stations; adding a corner never adds a lidar fitting station. Two constrained lines 6 cm apart preserve the
step. Steps below 3 cm are flush; OSM crossing/lowered-kerb semantics distinguish
crossings. Steps above 1 m are handed off as quays.

Triangle `pc` triangulates one canonical graph containing every collision-tile boundary,
curb double line and water shoreline. Base points start at 2 m and refine from 50 cm
observed-cell representatives where interpolation error exceeds 1 cm (stricter than the 2 cm design tolerance). Tile extraction
copies canonical vertices verbatim. The seam check compares complete boundary edges
(including multiplicities and sensor edges), not only approximate vertex positions.

OSM water polygons constrain the ground boundary. Water triangles have material `water`
(sensor class) and one N2000 elevation, with separate shoreline vertices; ground cannot
interpolate over the river. Elevation comes from class-9 returns, otherwise the lowest
supported river-interior lidar mode, or an explicit override. The report records its source.

City polygons supply material IDs; natural-stone paving overrides functional road labels.
An orthophoto RGB vegetation classifier supplies grass outside mapped street polygons;
other unmapped surfaces use generic paving. This conservative fallback is not a learned
surface classifier.

Raw points, selected ground, fitted curb profiles and the canonical CDT are cached separately.
Each collision tile has a manifest cache entry; repairing a missing tile preserves the mtimes
of unchanged collision and visual artifacts.

The raw-point cache and hash registry live in `$TURKU3D_WORK/cache/ground`. Inputs are
never modified. Hashes include the ortho VRT and its referenced TIFFs. Cache validation
checks output and QA bytes, so a missing/corrupt layer artifact triggers repair.
The QA step locates the actual profile transition before fitting its sides; it does not
infer transition width from the configured breakline spacing.

## Phase 0 compatibility

`stub.py` remains the Phase 0 package builder's heightfield provider until W1-04 replaces
that assembler path. The shared material mapping now lives in `materials.py`. Run
`make tile-test` when changing the Phase 0 integration. The ground layer does not change
runtime streaming or publish its own tileset.
