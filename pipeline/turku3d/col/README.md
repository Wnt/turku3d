# turku3d.col — `.col` collision tiles (Python writer + reader)

Implements [docs/specs/col-format.md](../../../docs/specs/col-format.md) v1.0 and the materials table
[docs/specs/materials.md](../../../docs/specs/materials.md) v1. The TypeScript reader is in
`game/src/shared/col/`, the Rapier validator in `tools/validator/`.

```
make col-deps          # pipeline venv (foundation) + npm ci in tools/validator (meshoptimizer encoder for the writer)
make col-test-py       # = cd pipeline && .venv/bin/python -m pytest -q turku3d/col/tests
make col-test          # Python tests + Python->TS round trip + validator fixture tests
make col-materials     # regenerate materials_v1.json and game/src/shared/col/materials.ts from materials.md
make col-fixtures      # regenerate tools/validator/fixtures (deterministic, byte-identical)
cd pipeline && .venv/bin/python -m turku3d.col.roundtrip --out /tmp/rt   # round-trip cases + expected.json
```

```python
from turku3d.col import ColTile, Primitive, write_col, read_col
from turku3d.col.materials import mat_id
tile = ColTile.for_tile(i, j, world_local_xyz_f64, tri_indices, tri_materials,
                        primitives=[Primitive.cylinder(mat_id("pole"), (x, y, z), 2.0, 0.08)],
                        aoi_border=False, meta={"tile": f"c_{i}_{j}"})
write_col(tile, f"c_{i}_{j}.col")          # encoding="auto": meshopt if the encoder is available, else RAW
t = read_col("c_1_2.col")                    # checks magic, major, file_bytes, CRC, encodings, counts
```

Files

| File | What |
|---|---|
| `format.py` | `ColTile`, `Primitive`, `write_col`, `read_col`, `check_tile` (spec §3 rules), `canonical_triangles` |
| `meshopt.py` | meshopt **decoder** in pure Python/numpy (port of the npm reference decoder: vertex v0/v1, index v1) and the **encoder** bridge |
| `meshopt_bridge.mjs` | Node script that runs the official npm `meshoptimizer` WASM encoder (`reorderMesh`, `encodeVertexBufferLevel`, `encodeIndexBuffer`) |
| `materials.py` | parser for materials.md, generator for `materials_v1.json` + `materials.ts`, `mat_id`, `material_class` |
| `fixtures.py` | synthetic validator fixtures (curbs 12/25/40 cm, stair, buildings, water, primitives; 4 negative variants) |
| `roundtrip.py` | Python→TS round-trip cases |
| `tests/test_col.py` | pytest (15 tests) |

Writer behaviour
- Validates spec §3 before writing (index range, degenerate area < 1e-6 m², duplicate triangles, materials
  0 / unassigned / 200-255, ≤ 150k triangles, ≤ 1 MB). `strict=False` only for negative fixtures.
- `FIX_INTERNAL_EDGES` is set automatically when a triangle has a `FIX_EDGES` material (curb, curb_dropped, quay); `AOI_BORDER` from `aoi_border=True`.
- With meshopt: `reorderMesh` (vertex cache + fetch), MATL permuted with the triangles, unreferenced
  vertices dropped, vertex codec **v0** by default (readable by every npm decoder ≥ 0.22; `vertex_version=1` is ~5 % smaller).
  The `ColTile` object is updated in place to the written order. Positions are never quantised.
- Empty meshes are written RAW. bbox covers triangles and (rotated) primitives.

meshopt encoder: why Node
- PyPI `meshoptimizer` (0.2.30a0, sdist only) does not build on this Mac: the Command Line Tools linker
  cannot read the macOS 27 SDK `.tbd` files (`tapi error: malformed file`), so no native extension or
  ctypes library can be linked here at all. The Node bridge uses the same WASM the game decodes with.
- It needs `node` (24 on both machines) and `tools/validator/node_modules/meshoptimizer` (`make col-deps`)
  or `$TURKU3D_MESHOPT_DIR` pointing at an npm `meshoptimizer` package directory.
- Without them `write_col(encoding="auto")` writes RAW (valid per spec §5) and prints a warning;
  `encoding="meshopt"` raises. Reading never needs Node.
