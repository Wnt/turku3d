# Ground layer

The default build produces plain lidar terrain for the four Phase 1 visual tiles and
their collision tiles plus a 50 m contact margin (100 collision tiles). This gives
bridge ends outside the visual grid, including Teatterisilta's west shore, ground
collision. To add measured curbs around the office, run the lidar relief
producer and pass its complete breakline file to the ground builder:

```sh
source ../sandbox.env
make ground-test
make ground-build
make ground-build  # unchanged inputs: hash-verified no-op
bash pipeline/env/run.sh python -m turku3d.lidar relief --scope office-block
make ground-build GROUND_ARGS="--breaklines $TURKU3D_WORK/reports/lidar/office-block/breaklines.json"
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
```

Outputs are under `$TURKU3D_WORK/build/layers/ground/`, following the shared
[layer contract](../../../docs/specs/layers.md):

- `c_i_j.col.npz`: float64 world-local positions, uint32 triangle indices and one uint8
  material per triangle. The assembler owns shipped `.col` files.
- `t0_i_j.vis.glb`: world-local metre geometry, planar E/N metre UVs and mesh extras
  `{layer, object_id, material_key}`. The assembler binds textures.
- `MANIFEST.json`: source identities, parameters, code hash, layer file hashes, tile
  cache identities and acceptance status.
- `breaklines.json`, `quays.json`, `curbs.geojson`: measured breakline handoff and
  validation sidecars; empty in the default plain build.
- `ramps.json`: each surveyed 62540 line, its length covered by measured ground
  triangles, and whether those triangles form a walkable ramp or a blocking wall.
  Grass slopes retain grass material; paved slopes up to 40 degrees use ramp
  material. Taller steep faces keep visible wall material.

QA is written to `$TURKU3D_WORK/reports/ground/ground-qa.{json,md}`. Acceptance checks
point coverage, materials, triangle budget, bitwise seams, interior open edges and
constant water elevation. Real shipped collision is also checked for holes and
below-ground behaviour with the Rapier validator. A failed layer check exits 2 and
remains a failed result on cached reruns.

## Ground and materials

Coordinate and tile definitions come from `turku3d.config`. Coordinate hashing withholds
20% of lidar returns before ground selection. Class 2 plus class 12 within 15 cm of a
robust local class-2 surface are denoised using local-plane residuals. Raw data is read-only.

Triangle `pc` triangulates one canonical graph containing every tile boundary and water
shoreline. Base points start at 2 m and refine from observed 0.5 m cell representatives
where error exceeds 2 cm. Neighbouring tiles copy the same canonical float64 vertices.
No independently rounded edge heights can introduce seams.
Constraint intersections use a 5 mm precision grid to merge nearly coincident
lidar/vector endpoints before triangulation; this prevents sub-mm faces rejected
by the shipped collision format.
Ramp classification runs on that canonical mesh after triangulation. The 62540
WFS source is included in the ground manifest, while unchanged lidar selection
and CDT geometry caches remain reusable.

Katualueet polygons supply physical materials; natural-stone paving overrides functional
road labels. Outside street polygons, an orthophoto RGB vegetation classifier supplies
grass; remaining unmapped surfaces use paving slabs. This is a conservative fallback,
not a learned surface classifier. Visual materials use the same keys as collision.
Measured wall and step breaklines retain their lidar rise instead of being clamped as
curbs. Terrain faces steeper than 45 degrees with over 35 cm of relief cannot carry
a walkable pavement material; they use the wall material in both collision and the
matching visual GLB. This keeps river retaining faces visible as blockers.

Water polygons constrain the shoreline. Unsupported shoreline constraint heights are
bounded to nearby observed ground returns, preventing a fitted bank plane from creating
high pavement where the nearest lidar support is several metres away. Mapped bridge
footprints cut land triangles within 2 m of the water under decks, retaining
ground at surveyed abutments and the water surface beneath.
The separate water sensor/visual surface uses
median class-9 N2000 elevation; ground heights are not interpolated across the river.
The solid river bed required by `materials.md` is an explicitly synthetic collision-only
fallback 3 m below that water level, with closed bank faces. It is not measured bathymetry
and is omitted from visuals. This prevents unbounded falls through the sensor without
interpolating land elevations across the river. Its provenance is recorded in the report.
If reliable water observations are absent, supply a measured level explicitly:

```sh
make ground-build GROUND_ARGS="--water-level 0.12"
```

Raw returns, selected points and canonical terrain have separate caches in
`$TURKU3D_WORK/cache/ground`. Input hashes include ortho VRT dependencies. Artifact and
QA hashes are checked before returning a no-op. Repairing a missing artifact reuses the
canonical mesh and preserves unchanged artifact bytes and mtimes.

## Measured breakline interface

The implementation remains available as an explicit data input:

```sh
make ground-build GROUND_ARGS="--breaklines $TURKU3D_WORK/reports/lidar/office-block/breaklines.json"
```

A JSON list contains entries such as:

```json
{
  "object_id": "lidar:curb:42",
  "kind": "curb",
  "centers_xz": [[168, 246], [170, 246]],
  "normals_xz": [[0, 1], [0, 1]],
  "side_heights": [[4.1, 4.25], [4.12, 4.27]]
}
```

Coordinates are engine-local metres, heights absolute N2000. All three arrays have the
same length >=2; normals are unit x/z vectors. Side heights are **minus normal, plus
normal**. Kinds are `curb`, `curb_dropped`, `crossing`, `quay`. Additional provenance,
confidence and relief-class fields are permitted. Export separate objects across
occlusion; the loader does not refit heights and ignores fragments shorter than
5 cm. Raised `curb` heights are clamped to
6–20 cm about their measured midpoint; dropped curbs and quays retain measured
heights. The collision strip carries `curb` material and nearby lower/upper sides
carry `road`/`sidewalk`. Exported handoffs use the same schema.

Measure actual collision mesh steps against a plain baseline:

```sh
bash pipeline/env/run.sh python -m turku3d.ground.curb_coverage \
  --before "$TURKU3D_WORK/build/layers/ground-before" \
  --after "$TURKU3D_WORK/build/layers/ground" \
  --breaklines "$TURKU3D_WORK/reports/lidar/office-block/breaklines.json" \
  --output "$TURKU3D_WORK/reports/ground/curb-coverage.json"
```

`curbs.py` retains the B3 gradient-snapped double-breakline fitter; `qa.py:evaluate`
retains independent held-out profile diagnostics. Neither is called by the default
build. `ground_qa.py` checks topology, coverage and budgets for both build modes;
`curb_coverage.py` measures the actual curb step length for the measured mode.
