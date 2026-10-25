# data-manifests/

Small, committed descriptions of bulk data (the data itself lives on `osgallery-dev:~/turku3d/data`).

- `laz-tiles.csv`: city lidar index, copied verbatim from `osgallery-dev:~/leikkipuistodata/laz-tiles.csv` (read-only source) on 2026-10-25. 2,564 rows = 1,282 `pistepilvi` + 1,282 `maanpinta`, 500 m tiles named `<xmin>_<ymax>`. The `url` column uses the old `opaskartta.turku.fi` host, which redirects: use `curl -L` or `LazTile.url()` from `turku3d.config`.
  Refresh: `scp osgallery-dev:leikkipuistodata/laz-tiles.csv data-manifests/laz-tiles.csv`
- `areas/office-block.yaml`: Phase 1 AOI, **generated** by `make foundation-areas` from `pipeline/turku3d/config.py`; a unit test fails if it is stale.
- `sources.yaml` (todo, fetch worker): endpoints, layers, years, licence notes.
- `checksums/`: sha256 lists of raw downloads (written by the fetchers).
