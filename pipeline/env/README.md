# pipeline/env — remote toolchain on osgallery-dev

Everything lives under `~/turku3d/` on the remote (never in `~/leikkipuistodata`).

| Path (remote) | What |
|---|---|
| `~/turku3d/envs/pipeline` | conda-forge env from `environment.yml` + `pip -e ~/turku3d/repo/pipeline` |
| `~/turku3d/envs/pipeline.env` | `source` this: PATH, PROJ_DATA, GDAL_DATA, TURKU3D_DATA, **PYTHONNOUSERSITE=1** |
| `~/turku3d/envs/pipeline.lock.txt` | exact resolved package list (explicit URLs) |
| `~/turku3d/tools/bin/` | micromamba, `gltf-transform`, `gltfpack`, `roofer` (wrapper sets GDAL/PROJ data) |
| `~/turku3d/tools/npm` | @gltf-transform/cli 4.5.1, gltfpack 1.3.0 (system Node 24.18) |
| `~/turku3d/tools/roofer` | 3DBAG roofer v1.0.0 release tarball |

```
make pipeline-bootstrap          # rsync repo, then bash ~/turku3d/repo/pipeline/env/bootstrap-remote.sh (idempotent)
make pipeline-bootstrap-update   # same, re-solves the env against environment.yml
make pipeline-versions           # env/versions.sh: prints versions, exit 1 if anything is missing
ssh osgallery-dev 'source ~/turku3d/envs/pipeline.env && python -c "import pdal, open3d"'
```

Verified 2026-10-25: python 3.12.15, pdal 2.10.2 (+python-pdal 3.5.5), GDAL 3.13, rasterio 1.5,
geopandas 1.2.0, pyarrow 25, shapely 2.2, pyproj 3.8, laspy 2.7 (lazrs + lazrs-parallel), scipy 1.18,
numpy 2.5.4, open3d 0.20.0 (pip `open3d-cpu`), triangle 20250106 (pip), osmium-tool 1.19.1, pyosmium,
trimesh 5.2, mapbox_earcut 2.1, pytest 9.1, node 24.18, gltf-transform 4.5.1, gltfpack 1.3, roofer 1.0.0.

Gotchas found while bootstrapping:
- The remote user has packages in `~/.local/lib/python3.12` (numpy 2.5.1, pillow 12.3, ...). Without
  `PYTHONNOUSERSITE=1` they shadow the env's packages. `pipeline.env` and the bootstrap set it.
- conda-forge `open3d` 0.20 fails at import (`undefined symbol _ZN8filament8Material7Builder7packageEPKvm`,
  built against an older filament). We use the pip wheel `open3d-cpu` instead (the server has no GPU).
- Without `hdf5>=2` the solver picks hdf5 1.14 and the PDAL hdf/icebridge plugins print
  `(PDAL Error) Can't load library ... libhdf5_cpp.so.320` on every pdal import.
- The conda-forge `triangle` package is the C library only; the Python binding comes from pip.
