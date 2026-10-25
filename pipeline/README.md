# pipeline/ (Python package `turku3d`)

Runs on the remote `osgallery-dev` (bulk) and on the Mac (small runs). Python ≥ 3.12.

```
make foundation-venv        # = uv venv --python 3.12 pipeline/.venv && uv pip install -e 'pipeline[dev]'
make foundation-test        # = cd pipeline && .venv/bin/python -m pytest -q tests/test_config.py tests/test_specs.py
make foundation-areas       # regenerate data-manifests/areas/office-block.yaml from config.py
make foundation-where E=23459668 N=6704254   # -> local x/z, visual/collision/LAZ tile ids
```

`turku3d/config.py` is the single source of truth:
- origin O (E 23459500, N 6704500, H 0), CRS strings (`EPSG:3877`, `EPSG:3900`, `EPSG:3877+3900`, `EPSG:4326`)
- `en_to_local` / `local_to_en` (x = E−E0, y = H, z = −(N−N0)); `en_to_wgs84` / `wgs84_to_en` / `local_to_wgs84` / `wgs84_to_local` (pyproj, horizontal only; heights stay N2000)
- grids: `VisualTile` (`t{L}_{i}_{j}`, 250·2^L m), `CollisionTile` (`c_{i}_{j}`, 62.5 m), `LazTile` (`<xmin>_<ymax>`, 500 m, `.url()`), `*_tiles_for_bbox`
- areas: `OFFICE_BLOCK` (Phase 1 AOI, 4×4 collision tiles + 50 m margin), `AREAS`, `area_yaml()`
- paths: `data_root()` (`$TURKU3D_DATA`, else Linux `~/turku3d/data`, macOS `~/turku3d-data`), `raw_dir()`, `cache_dir()`, `dist_dir()`, `REMOTE_HOST`, `LAZ_TILES_CSV`

`turku3d/tiles.py` re-exports the grid API. Heavy deps (laspy, triangle, shapely, …) are in the `lidar` extra; pdal/open3d come from conda-forge on the remote (`~/turku3d/envs/pipeline`, Phase 0 step 2).
Subpackages `ground/ structures/ buildings/ vegetation/ package/ qa/` are empty stubs for the workers that own them.

## Remote pipeline (workstream remote-pipeline; targets in `mk/pipeline.mk`)

The code lives here on the Mac; `make pipeline-sync` rsyncs `pipeline/`, `data-manifests/`, `docs/specs/`
and `docs/ARCHITECTURE.md` to `osgallery-dev:~/turku3d/repo/`, and the remote targets run there over ssh in
`~/turku3d/envs/pipeline` (see `env/README.md`).

```
make pipeline-bootstrap                    # remote env + node tools + roofer (idempotent)
make pipeline-versions                     # verify tool/library versions
make pipeline-test                         # local pytest (gdal/osmium tests skip on the Mac)
make pipeline-test-remote                  # pytest in the remote env (all tests run)
make fetch-aoi                             # fetch LAZ, WFS, WMS orthos, OSM for AREA=office-block + report
make fetch-checksums                       # pull SHA256SUMS + REPORT into data-manifests/checksums/
make pipeline-ssh-run CMD='python -m turku3d.fetch report'
make pipeline-sources                      # regenerate data-manifests/sources.yaml
```

Local extra deps for the fetch tests: `uv pip install --python pipeline/.venv/bin/python -e 'pipeline[dev,fetch]'`.
Fetcher details: `turku3d/fetch/README.md`.
