# Ground layer

The default build produces plain lidar terrain for the four Phase 1 visual tiles and
64 collision tiles. Curbs are deferred by user decision (2026-10-25): the default target
does not fit city curb vectors and curb accuracy does not gate this iteration.

```sh
source ../sandbox.env
make ground-test
make ground-build
make ground-build  # unchanged inputs: hash-verified no-op
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
- `breaklines.json`, `quays.json`, `curbs.geojson`: reserved breakline handoff and
  validation sidecars; empty in the default plain build.

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

Katualueet polygons supply physical materials; natural-stone paving overrides functional
road labels. Outside street polygons, an orthophoto RGB vegetation classifier supplies
grass; remaining unmapped surfaces use paving slabs. This is a conservative fallback,
not a learned surface classifier. Visual materials use the same keys as collision.

Water polygons constrain the shoreline. The separate water sensor/visual surface uses
median class-9 N2000 elevation; ground heights are not interpolated across the river.
If reliable water observations are absent, supply a measured level explicitly:

```sh
make ground-build GROUND_ARGS="--water-level 0.12"
```

Raw returns, selected points and canonical terrain have separate caches in
`$TURKU3D_WORK/cache/ground`. Input hashes include ortho VRT dependencies. Artifact and
QA hashes are checked before returning a no-op. Repairing a missing artifact reuses the
canonical mesh and preserves unchanged artifact bytes and mtimes.

## Measured breakline interface (next iteration, W1-13)

The implementation remains available as an explicit data input:

```sh
make ground-build GROUND_ARGS="--breaklines $TURKU3D_WORK/build/layers/lidar/added-breaklines.json"
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
occlusion; the loader does not refit heights. Exported handoffs use the same schema.

`curbs.py` retains the B3 gradient-snapped double-breakline fitter; `qa.py:evaluate`
retains independent held-out profile diagnostics. Neither is called by the default
build. Those experiments and their previous accuracy targets are deferred, not silently
promoted to current acceptance. `ground_qa.py` owns the plain-ground checks.
