# Tiles: ids, `tileset.json`, `manifest.json`

Status: spec v1 (2026-10-25). Source of truth for the numbers: `pipeline/turku3d/config.py` (ARCHITECTURE.md D3/D4).

## 1. Frames

- Origin O = EPSG:3877 E 23459500, N 6704500, H 0 (N2000).
- **Engine frame** (three.js, glTF, `.col`, physics): Y-up, `x = E − 23459500`, `y = H`, `z = −(N − 6704500)`.
- **3D Tiles frame** (`tileset.json` bounding volumes and transforms only): Z-up, `X = E − 23459500`, `Y = N − 6704500`, `Z = H`.
  This is exactly what 3D Tiles produces from Y-up glTF content (the spec's glTF y-up → z-up rotation maps engine `(x, y, z)` to `(x, −z, y)`). In three.js the renderer group gets `tiles.group.rotation.x = −Math.PI / 2`, which maps the 3D Tiles frame back to the engine frame. No geographic transform is used (no ECEF, no `root.transform`). [V] 3DTilesRendererJS 0.5.3 applies the glTF Y-up → Z-up rotation to GLB content by default; verified in the game e2e test with an asymmetric dev tile (the NE marker at tile-local x 240, z 10 ends up at engine +x, −z) and with the pipeline stub tiles (`make tile`).

## 2. Tile grids and ids

All grids are aligned to O. `i = floor(x / size)` grows **east**, `j = floor(z / size)` grows **south**. A tile covers `x ∈ [i·s, (i+1)·s)`, `z ∈ [j·s, (j+1)·s)`. Its tile-local origin is the corner `(i·s, 0, j·s)` in engine coordinates, i.e. the NW map corner `(E0 + i·s, N0 − j·s)`. Indices can be negative.

| Grid | Size | Id | Example (office, x 168, z 246) |
|---|---|---|---|
| Visual L0 | 250 m | `t0_{i}_{j}` | `t0_0_0` |
| Visual L1 | 500 m (= one LAZ tile) | `t1_{i}_{j}` | `t1_0_0` |
| Visual L2 | 1 km | `t2_{i}_{j}` | `t2_0_0` |
| Visual L3 | 2 km | `t3_{i}_{j}` | `t3_0_0` |
| Collision | 62.5 m (4 × 4 per L0) | `c_{i}_{j}` | `c_2_3` |
| LAZ (city) | 500 m | `<xmin>_<ymax>` (EPSG:3877 ints) | `23459500_6704500` |

- Parent of `tL_i_j` is `t(L+1)_{⌊i/2⌋}_{⌊j/2⌋}` (floor division, also for negatives). The visual L0 parent of `c_i_j` is `t0_{⌊i/4⌋}_{⌊j/4⌋}`.
- The Phase 1 AOI (`data-manifests/areas/office-block.yaml`, generated from config.py) is the 4 × 4 collision block `c_1_2 … c_4_5` (engine x 62.5–312.5, z 125–375; E 23459562.5–23459812.5, N 6704125–6704375), centred within 20 m of the office. Because the visual grid is aligned to O, it overlaps the four L0 tiles `t0_0_0, t0_1_0, t0_0_1, t0_1_1`; the margin (50 m) stays inside LAZ tile `23459500_6704500`.
- Python: `VisualTile.parse("t0_1_0")`, `CollisionTile.at(x, z)`, `LazTile.at_en(e, n)`, `OFFICE_BLOCK.collision_tiles`. CLI: `make foundation-where E=… N=…`.

## 3. `dist/` layout

```
dist/tiles/
├── tileset.json                     3D Tiles 1.0 (visual only)
├── manifest.json                    everything (visual + collision), hashes, sizes
├── visual/{L}/t{L}_{i}_{j}.glb      glTF 2.0 + EXT_meshopt_compression + KHR_texture_basisu
├── collision/t0_{I}_{J}/c_{i}_{j}.col   grouped by the L0 visual parent (16 per folder)
├── roads/<sha256[:16]>.json         street routing graph (pipeline/turku3d/roads/README.md)
└── textures/…                       shared KTX2 (optional; trim sheets / library materials)
```

URLs are relative to `dist/tiles/` and use `/` separators. Ids are case-sensitive and contain only `[a-z0-9_-]`.

### 3.1 Visual GLB conventions

- Vertex positions are tile-local (relative to the tile origin `(i·s, 0, j·s)`), float32.
- The tile origin is carried as the **translation of a single root node** (doubles in JSON). Do not put the offset in `tileset.json` `transform`.
- Steps: KTX2 first, then meshopt (no simplify, no join, no `KHR_mesh_quantization` on positions for L0).
- Budget: L0 GLB ≤ 8 MB (brotli on the wire).
- Node `extras`: `{ "tile": "t0_0_0", "objects": [...] }` is reserved for QA back-mapping (building/material ids).
- Object nodes carry `extras: {kind, id, tile, material?}` (`kind` ∈ building | ground | prop | vegetation | water | other). The game's QA ID pass (`?pass=id`, qa/README.md §2) turns each distinct `(kind, id, tile)` into one flat ID colour and lists it in `window.__qaIdTable`.
- `make tile` assembles published layers through `turku3d.package.assemble`: KTX2 before lossless meshopt, with no L0 simplification or joining. The Phase 0 stub survives only in `pipeline/tests/fixtures/`.

## 4. `tileset.json`

3D Tiles **1.0** (`asset.version: "1.0"`, avoids the 3DTilesRendererJS 1.1 warning).

```json
{
  "asset": { "version": "1.0", "generator": "turku3d <git sha>", "extras": { "frame": "turku3d-local-zup", "origin_en": [23459500, 6704500, 0], "crs": "EPSG:3877+3900" } },
  "geometricError": 512,
  "root": {
    "boundingVolume": { "box": [cx, cy, cz, hx, 0, 0, 0, hy, 0, 0, 0, hz] },
    "geometricError": 512,
    "refine": "REPLACE",
    "children": [
      {
        "boundingVolume": { "box": [125, -125, 15, 125, 0, 0, 0, 125, 0, 0, 0, 25] },
        "geometricError": 0,
        "content": { "uri": "visual/0/t0_0_0.glb" },
        "extras": { "id": "t0_0_0" }
      }
    ]
  }
}
```

- Bounding boxes are in the **3D Tiles frame** (Z-up, Y = north). For tile `tL_i_j` with size `s`, height range `[hmin, hmax]`: `cx = (i + 0.5)·s`, `cy = −(j + 0.5)·s`, `cz = (hmin + hmax)/2`, `hx = hy = s/2`, `hz = (hmax − hmin)/2` (clamp `hz ≥ 1`).
- Hierarchy: L3 → L2 → L1 → L0, quadtree; missing children are simply absent. Leaves (L0) have `geometricError: 0`.
- Initial `geometricError` per level (tunable, metres): L0 `0`, L1 `16`, L2 `64`, L3 `256`, root `512`.
- `refine: "REPLACE"` everywhere. Every tile with content has `extras.id`.
- Phase 0/1 may ship a flat tileset (root without content + L0 children).

## 5. `manifest.json`

One file for the whole `dist/tiles/` tree. The game reads it at start-up to know which collision tiles exist (collision tiles are not in `tileset.json`); the sync/validate tools use the hashes.

```json
{
  "format": "turku3d-manifest",
  "version": 1,
  "built": "2026-10-25T12:00:00Z",
  "code": "<git sha>",
  "origin_en": [23459500.0, 6704500.0, 0.0],
  "crs": "EPSG:3877+3900",
  "grid": { "visual_l0": 250.0, "collision": 62.5, "levels": 4 },
  "materials_version": 1,
  "col_version": [1, 0],
  "areas": ["office-block"],
  "tileset": { "url": "tileset.json", "sha256": "…", "bytes": 1234 },
  "visual": {
    "t0_0_0": { "url": "visual/0/t0_0_0.glb", "sha256": "…", "bytes": 5123456,
                "level": 0, "tris": 812345, "h": [1.2, 48.0] }
  },
  "collision": {
    "c_2_3": { "url": "collision/t0_0_0/c_2_3.col", "sha256": "…", "bytes": 734211,
               "tris": 121003, "prims": 412, "h": [1.2, 30.5], "flags": 1 }
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `format`, `version` | string, int | `"turku3d-manifest"`, `1`. Readers reject other formats / higher majors |
| `built`, `code` | ISO-8601 UTC, git sha | provenance |
| `origin_en`, `crs`, `grid` | | must equal config.py; the game asserts this |
| `materials_version`, `col_version` | int, [major, minor] | versions of [materials.md](materials.md) / [col-format.md](col-format.md) used |
| `areas` | string[] | built area names (`data-manifests/areas/*.yaml`) |
| `visual[id]` | object | `url` (relative), `sha256` (hex of the file bytes), `bytes`, `level`, `tris`, `h` = [min, max] height N2000 |
| `collision[id]` | object | `url`, `sha256`, `bytes`, `tris`, `prims`, `h`, `flags` (= `.col` header flags) |
| `vehicles` | object[] | Parked vehicle `object_id`, `model`, `color`, engine-local base `x,y,z`, visual `yaw`, `width,height,length` in metres, and `clear_front_m,clear_rear_m`: the straight body-width distance to the first solid obstacle or other parked car ahead/behind, capped at 30 m (`pipeline/turku3d/placement/manoeuvre.json`). These are runtime rigid bodies, so their former `traffic_static` meshes and boxes are omitted from shipped tiles. |
| `vehicle_models[model/color]` | object | Relative `url`, `sha256`, and `bytes` for the shared glTF model and paint variant. |
| `roads` | object, optional | Street routing graph for the minimap and route guidance: `url`, `sha256`, `bytes`, `nodes`, `edges`. Format: [roads README](../../pipeline/turku3d/roads/README.md). |

- Keys are tile ids; entries sorted by id for stable diffs.
- Caching: clients may append `?h=<first 8 hex of sha256>` to URLs for cache busting; the server ignores the query.
- The manifest is written last by the packaging step (atomic rename), so a half-written `dist/` is never advertised.
