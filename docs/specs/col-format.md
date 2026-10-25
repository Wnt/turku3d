# `.col` collision tile format, v1.0

Status: spec v1.0 (2026-10-25). Implementations: `pipeline/turku3d/col/` (Python writer + reader; `turku3d.package.col_format` re-exports it), `game/src/shared/col/colFormat.ts` (TypeScript reader, used by the game's physics worker through `game/src/physics/colTile.ts` and by `tools/validator`). See ARCHITECTURE.md D4/D5 for why this is not glTF.

One file = one **collision tile** `c_{i}_{j}` (62.5 m, see [tiles.md](tiles.md)). It holds one static triangle soup with a material ID per triangle, plus a list of compound primitives (boxes, capsules, cylinders). Materials and their collision classes are in [materials.md](materials.md).

## 1. General rules

- **Byte order:** little-endian everywhere.
- **Units/axes:** metres, engine axes (glTF Y-up): `x` east, `y` up (N2000 height), `z` south. See `pipeline/turku3d/config.py`.
- **Tile-local positions:** every position is relative to the header `origin` (float64). For collision tile `c_{i}_{j}`, `origin = (i * 62.5, 0, j * 62.5)` in engine coordinates. World-local position = `origin + position` (add in float64, or give the Rapier collider the translation `origin` and keep the float32 vertices as they are, which is the recommended way).
- **Alignment:** every section starts at a multiple of 8 bytes from the start of the file; padding bytes are zero.
- **Integrity:** the header carries the total file length and a CRC-32 (IEEE, as in zlib `crc32`) of the whole file computed with the CRC field set to zero.
- **File extension / MIME:** `.col`, served as `application/octet-stream` (brotli on the wire is the server's job, see `tools/dev`).

## 2. Layout

```
offset 0              header            96 bytes
offset 96             section table     section_count x 24 bytes
offset (8-aligned)    section payloads  in table order, each 8-aligned
```

### 2.1 Header (96 bytes)

| Offset | Type | Field | Value / meaning |
|---:|---|---|---|
| 0 | `char[4]` | `magic` | `"TCOL"` = bytes `54 43 4F 4C` |
| 4 | `u16` | `version_major` | `1`. A reader rejects any other major |
| 6 | `u16` | `version_minor` | `0`. A reader accepts any minor of its major (additive changes only) |
| 8 | `u32` | `header_bytes` | `96`. The section table starts here; readers must use this field, not a constant |
| 12 | `u32` | `flags` | bit 0 `FIX_INTERNAL_EDGES`: build the trimesh with Rapier `TriMeshFlags.FIX_INTERNAL_EDGES` (writer sets it if any triangle has a `FIX_EDGES` material). bit 1 `AOI_BORDER`: the tile touches the edge of the built area (open edges expected there). Other bits 0; readers ignore unknown bits |
| 16 | `f64` | `origin_x` | engine x of the tile-local frame |
| 24 | `f64` | `origin_y` | engine y (always `0.0` in v1) |
| 32 | `f64` | `origin_z` | engine z |
| 40 | `i32` | `tile_i` | collision tile index i |
| 44 | `i32` | `tile_j` | collision tile index j |
| 48 | `f32[3]` | `bbox_min` | tile-local AABB of all triangles and primitives |
| 60 | `f32[3]` | `bbox_max` | |
| 72 | `u32` | `vertex_count` | number of vertices |
| 76 | `u32` | `triangle_count` | number of triangles |
| 80 | `u32` | `primitive_count` | number of compound primitives |
| 84 | `u16` | `section_count` | entries in the section table |
| 86 | `u16` | `material_table_version` | `1` = materials v1 |
| 88 | `u32` | `file_bytes` | total file length in bytes |
| 92 | `u32` | `crc32` | CRC-32 of the whole file with these 4 bytes set to zero |

### 2.2 Section table entry (24 bytes)

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | `char[4]` | `tag` | section kind (below) |
| 4 | `u16` | `encoding` | `0` RAW, `1` MESHOPT_VERTEX, `2` MESHOPT_INDEX (triangles) |
| 6 | `u16` | `section_flags` | `0` in v1 |
| 8 | `u32` | `offset` | byte offset of the payload from the start of the file (multiple of 8) |
| 12 | `u32` | `byte_length` | encoded payload length (excluding padding) |
| 16 | `u32` | `element_count` | number of decoded elements |
| 20 | `u32` | `element_stride` | decoded bytes per element |

### 2.3 Sections

| Tag | Required | Decoded content | `element_count` | `stride` | Allowed encodings |
|---|---|---|---|---:|---|
| `POSN` | yes (may be empty) | `f32 x, y, z` per vertex, tile-local | `vertex_count` | 12 | 0, 1 |
| `INDX` | yes (may be empty) | `u32` vertex indices, 3 per triangle | `triangle_count * 3` | 4 | 0, 2 |
| `MATL` | yes (may be empty) | `u8` material ID per triangle, same order as `INDX` | `triangle_count` | 1 | 0 |
| `PRIM` | yes (may be empty) | 48-byte primitive records (§4) | `primitive_count` | 48 | 0 |
| `META` | no | UTF-8 JSON object, provenance (§6) | byte length | 1 | 0 |

- Each tag appears at most once. Writers emit them in the order above.
- A reader **skips unknown tags** (forward compatibility) but **rejects a known tag with an unknown encoding**.
- For RAW sections, `byte_length == element_count * element_stride`.

## 3. Triangle mesh rules

- **Winding:** the front face normal is `normalize(cross(b - a, c - a))` and points **out of the solid** (up for ground, outwards for building shells). Rapier does not need it, but the validator and the curb-strike module use the normal.
- Every index `< vertex_count`. No degenerate triangles (area ≥ 1e-6 m²). No duplicate triangles.
- Vertices are shared (indexed mesh, not a soup of unshared triangles): the CDT ground must be a connected manifold inside the tile.
- **Seams:** ground vertices on a tile edge lie exactly on tile-local `x ∈ {0, 62.5}` or `z ∈ {0, 62.5}`, and the matching vertex in the neighbour tile has bitwise identical world-local coordinates (`origin + position` in float64). No skirts. Open edges are allowed only on the AOI border (`AOI_BORDER` flag).
- **Objects that cross tile edges** (building shells, walls, stair flights) are cut only if they are part of the ground CDT. Anything else is stored whole in the tile that contains its footprint centroid, so the bbox may extend up to 64 m beyond the 62.5 m square; the streaming radius accounts for that.
- **Material:** every triangle has an ID from materials v1; `0` (`unknown`) and 200–255 are not allowed in shipped tiles.
- **Budgets:** ≤ 150,000 triangles and ≤ 1 MB file size per tile (Phase 1 acceptance, Task 1.10).
- The writer does **not** quantise or simplify positions: `POSN` decodes to the exact float32 values the writer had (meshopt vertex coding is lossless). Round-trip tests compare bitwise.

## 4. Compound primitives (`PRIM`, 48 bytes each)

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | `u8` | `kind` | `1` box, `2` capsule, `3` cylinder. Others reserved; a reader skips a record with an unknown kind and logs it |
| 1 | `u8` | `material` | material ID (materials v1) |
| 2 | `u16` | `flags` | `0` in v1 |
| 4 | `f32[3]` | `center` | tile-local centre |
| 16 | `f32[4]` | `rotation` | unit quaternion `(x, y, z, w)` in engine axes; identity = `(0,0,0,1)` |
| 32 | `f32[4]` | `params` | box: `(half_x, half_y, half_z, 0)`; capsule: `(half_height, radius, 0, 0)`; cylinder: `(half_height, radius, 0, 0)` |

Capsules and cylinders are aligned with their local **Y** axis (Rapier convention); `half_height` excludes the capsule caps. Typical use: fences and street furniture as boxes, tree trunks as capsules, poles and bollards as cylinders, retaining-wall cap boxes (D5).

## 5. Meshopt encoding

- Codec: [meshoptimizer](https://github.com/zeux/meshoptimizer) vertex and index codecs (the same codecs as glTF `EXT_meshopt_compression` modes `ATTRIBUTES` and `TRIANGLES`, without filters).
- `POSN` with encoding 1: `meshopt_encodeVertexBuffer(positions, vertex_count, 12)`. Decode with `MeshoptDecoder.decodeVertexBuffer(target, vertex_count, 12, source)` (npm `meshoptimizer`, the same decoder the glTF loader uses).
- `INDX` with encoding 2: `meshopt_encodeIndexBuffer(indices, triangle_count * 3, 4)`. Decode with `MeshoptDecoder.decodeIndexBuffer(target, triangle_count * 3, 4, source)`. The index codec keeps triangle order and winding, but **may rotate the three indices of a triangle**; `MATL` is per triangle, so it stays valid.
- Codec versions are detected by the decoder from the first payload byte; writers may use any version the npm decoder `meshoptimizer@>=0.22` reads.
- Writers should run `optimizeVertexCache` + `optimizeVertexFetch` on the mesh first (and permute `MATL` with the triangles). Do not use `simplify`.
- Encoding 0 (RAW) is always valid. Phase 0 stubs may ship RAW; Phase 1 tiles use 1/2.

## 6. `META` JSON (optional)

```json
{ "tile": "c_2_3", "built": "2026-10-25T12:00:00Z", "code": "<git sha>",
  "inputs": { "laz/23459500_6704500.laz": "<sha256>", "...": "..." },
  "steps": { "cdt": "<step version>", "...": "..." }, "notes": "free text" }
```
Readers must not depend on it. Keep it under 4 kB.

## 7. Reader algorithm (normative)

```
read header; check magic == "TCOL", version_major == 1, file_bytes == buffer length
check crc32(buffer with bytes 92..95 zeroed) == crc32 field   (optional in release, required in tests)
for each of section_count entries at header_bytes + k*24:
    if tag unknown: skip
    if encoding not allowed for tag: reject tile
    decode payload into a typed array of element_count * element_stride bytes
check len(POSN)/12 == vertex_count, len(INDX)/4 == 3*triangle_count, len(MATL) == triangle_count
split triangles by material class (materials.md): solid / query / sensor -> one trimesh collider each
build primitives as separate colliders with their material's class
collider translation = (origin_x, origin_y, origin_z)
```

The physics worker receives the whole file as a transferable `ArrayBuffer` and does all decoding in the worker.

## 8. Versioning

- `version_major` changes only for incompatible layout changes. Readers reject unknown majors.
- `version_minor` changes for additive changes: new optional section tags, new primitive kinds, new header flag bits, new encodings for new tags. A v1.0 reader still loads a v1.x file, minus the parts it does not know.
- Materials are versioned separately (`material_table_version`); the table is append-only.
- This document is the source of truth; change it first, then the writer and the readers, then add a round-trip test fixture.
