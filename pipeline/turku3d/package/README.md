# turku3d.package — dist/tiles packaging (Phase 0 stub chain)

Writes the shippable tree of [docs/specs/tiles.md](../../../docs/specs/tiles.md) §3–§5:
`visual/{L}/t*.glb`, `collision/t0_I_J/c_i_j.col`, `tileset.json`, `manifest.json` (written last, atomically).

| File | What |
|---|---|
| `gltf.py` | numpy-only GLB writer/reader (tile-local positions, origin in the root node translation, node `extras`, embedded JPEG/PNG) |
| `manifest.py` | `DistTree` (add visual/collision files, keeps entries of earlier builds, `commit()` rewrites a flat 3D Tiles 1.0 `tileset.json` and the manifest), `verify_tree` (re-hash) |
| `col_format.py` | re-export of `turku3d.col` under the name used in the specs |
| `stub_tile.py` | Phase 0 stub tile: maanpinta ground key points → 1.25 m heightfield (`turku3d.ground.stub`) → 16 `.col` tiles (materials from GIS:Katualueet, else grass) + one ground GLB draped with the 2022 true ortho |

```bash
# remote (real data in ~/turku3d/data/raw; output ~/turku3d/data/dist/tiles)
source ~/turku3d/envs/pipeline.env && cd ~/turku3d/repo/pipeline
python -m turku3d.package.stub_tile --tile t0_0_0 [t0_0_1 ...] [--area office-block] [--no-ortho] [--out DIR]
# Mac, synthetic inputs (no raw data, no geopandas/rasterio):
cd pipeline && .venv/bin/python -m turku3d.package.stub_tile --synthetic --out /tmp/dist/tiles
# whole chain from the repo root (fetch cached -> build -> sync -> validator -> Chrome):
make tile T=t0_0_0
make tile-test        # = pipeline/.venv/bin/python -m pytest -q tests/test_package.py
```

Stub limits: no curbs, stairs, buildings, trees or water; the river is interpolated across between the banks
(key points have no water returns); no meshopt/KTX2 in the GLB; one heightfield shared by visual and collision.
`.col` tiles are meshopt-encoded when `node` + npm `meshoptimizer` are available (`TURKU3D_MESHOPT_DIR`, set in
the remote `pipeline.env`), else RAW.
