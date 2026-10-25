# Layer interchange v1

`turku3d.layers` is the shared reader/writer. Layers publish under
`$TURKU3D_WORK/build/layers/<layer>/` (`layer_root()`); shared raw data stays read-only.
The assembler consumes only files listed in a completed `MANIFEST.json`.
Missing layers and missing tiles are allowed. A requested L0 with no visual input is an error.

## Collision

`<collision_tile_id>.col.npz` is a pickle-free NumPy archive:

- `positions`: float64 `(N,3)`, engine world-local metres (config.py origin and axes).
- `indices`: uint32 `(M,3)`, outward winding; `material`: uint8 `(M,)`, materials v1.
- `primitives`: optional `(P,)` structured array using exported `PRIMITIVE_DTYPE`:
  `kind U8` (`box`, `capsule`, `cylinder`), `center f64[3]`, `half_extents f64[3]`,
  `radius f64`, `height f64`, `yaw f64` (radians around engine +Y), `material u8`.
  Height is the full cylinder/segment height, excluding capsule caps. Unused dimensions are zero.
  Centers are world-local. Dimensions are positive. No object arrays or pickle.

Use `CollisionLayer(positions, indices, material, primitives)` and
`write_collision(path, layer)` / `read_collision(path)`. Helpers validate without silently
casting or wrapping invalid indices/materials. Empty meshes must retain `(0,3)` shapes.

Cut surfaces (especially ground) have bitwise identical float64 edge coordinates in neighbours.
Set manifest `params.seams` to `continuous` for clipped surfaces or `objects` for whole objects.
The default is `continuous` for ground and `objects` for other layers. The assembler compares
continuous surfaces per layer, before and after float32 conversion. Shipped META carries
`continuous_vertices: [[start,count], ...]` for the standalone seam validator.
Complete objects belong to their footprint-centroid tile, as in col-format.md; they are not
clipped or duplicated merely because their bounds cross an edge. The assembler never welds,
simplifies, or fixes a broken source seam. Shipped float32 precision must remain within 2 mm.

## Visual

`<visual_tile_id>.vis.glb` is self-contained glTF 2.0, Y-up engine world-local metres.
Tile-local float32 buffers with a root translation equal to `tile.origin` are recommended.
One mesh node per object; node extras require strings `layer`, `object_id`, `material_key`.
Use `write_visual(path, list[GlbMesh], root_translation=...)` / `read_visual(path)` (returns bytes).
Other GLB producers may use the same contract. Embedded images are allowed.

Optional node extras: `surface` = `roof` (retained in coarse LODs), `building_id` and
`facade_index` select a unique facade only from W1-09's filtered export under
`$TURKU3D_WORK/textures/assembly/facades/<building_id>/<facade_index>.json`.
The export's `MANIFEST.json` must list `<building_id>_<facade_index>` in `bound` and its
`inventory_sha256` must match the current buildings `facades.geojson`; otherwise keep the
`material_key` library fallback. Raw staging/provisional indices must never be bound.
The required sidecar identifies `building_id`/`facade_index`, provides `files` and SHA-256
`hashes` for albedo/normal/ORM, and declares `qa.gltf_basisu_valid: true`. Its metre-UV
`uv.scale`/`uv.offset` become `KHR_texture_transform` on all four PBR slots, with clamp wrap.
Library paths default to `textures/assembly/library/<material_key>/{albedo,normal,orm}.ktx2`
under `$TURKU3D_WORK`; `material.json` `uv_scale` controls repeating library UVs.
Explicit `--library`/`--facades` paths can select another filtered export.
Assembler `materials.mjs` owns the deliberate W1-03 fallback table; original keys remain in extras.
PNG/JPEG source maps may be encoded by the assembler. Missing library maps preserve the source
material color/image. Ground/roof UVs are planar E/N metres; facade U runs along the facade,
V runs up from its base. Unique ortho/facade images use normalized UVs and must be identified
with `texture_uv: "normalized"`; repeating library maps use metre UVs.

## Manifest

Call `write_manifest(directory, inputs={source: sha256}, params={...}, code=code_hash,
tiles=["c_0_0.col.npz", "t0_0_0.vis.glb"])` **after** writing all outputs.
It atomically publishes a deterministic JSON object with `format: "turku3d-layer"`,
`version: 1`, `layer` (directory basename), `inputs`, `params`, `code` and `tiles`.
`tiles` maps relative filenames to `{sha256, bytes}`. It is the complete inventory for that
layer, not just this invocation. Readers verify hashes; unlisted/stale files are ignored.
`read_manifest(directory, verify=True)` rejects unsupported versions and corrupt files.
Each file is atomically replaced. Producers must not write concurrently with assembly.
