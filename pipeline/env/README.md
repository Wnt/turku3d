# Native macOS arm64 toolchain

Run from any worktree, including a clean shell:

```sh
make pipeline-bootstrap
make pipeline-versions
bash pipeline/env/run.sh python -m pytest -q -n 4 pipeline/tests
```

Everything installs under `~/turku3d/`, without sudo or shell profile edits:

- `tools/bin/micromamba`: micromamba 2.8.1; package cache in `tools/mamba`.
- `envs/pipeline`: Python 3.12, PDAL/python-pdal, GDAL, rasterio, geopandas, pyarrow,
  scipy, laspy/lazrs, shapely, pyproj, osmium, pytest and pytest-xdist from conda-forge.
- `pipeline/env/osx-arm64.lock.txt`: exact conda package URLs; `triangle==20250106` is installed
  with pip (conda's `triangle` is the C library, not its Python binding).
- `tools/npm`: pinned gltf-transform 4.5.1, gltfpack 1.3.0, meshoptimizer 1.3.0.
- `tools/roofer-1.0.0`: [official macOS arm64 release](https://github.com/3DBAG/roofer/releases/tag/v1.0.0).
  No source build is needed. The launcher selects the bundled GDAL/PROJ data.
- `tools/ktx-4.4.2`: [KTX-Software arm64 package](https://github.com/KhronosGroup/KTX-Software/releases/tag/v4.4.2),
  extracted with `pkgutil --expand-full` into the user-owned prefix, including all component payloads.
- `envs/pipeline.env`: tool paths only. It preserves all `TURKU3D_*` sandbox settings.

`run.sh` selects the current worktree with `PYTHONPATH`, so shared tools never import another
worker's editable install. `make pipeline-bootstrap-update` re-solves `environment.yml`; review
and copy `~/turku3d/envs/pipeline.lock.txt` to the committed lock after a deliberate update.
The bootstrap is idempotent. Node 24 and the macOS command-line tools must already be installed.
