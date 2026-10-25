# data-manifests/

Small, committed descriptions of bulk data (the data itself lives in `~/turku3d/data`).

- `laz-tiles.csv`: city lidar index, copied verbatim from `osgallery-dev:~/leikkipuistodata/laz-tiles.csv` (read-only source) on 2026-10-25. 2,564 rows = 1,282 `pistepilvi` + 1,282 `maanpinta`, 500 m tiles named `<xmin>_<ymax>`. The `url` column uses the old `opaskartta.turku.fi` host, which redirects: use `curl -L` or `LazTile.url()` from `turku3d.config`.
  The original source is historical; current downloads use the city URLs in `turku3d.config`.
- `areas/office-block.yaml`: Phase 1 AOI, **generated** by `make foundation-areas` from `pipeline/turku3d/config.py`; a unit test fails if it is stale.
- `sources.yaml` (todo, fetch worker): endpoints, layers, years, licence notes.
- `checksums/`: sha256 lists of raw downloads (written by the fetchers).

- `areas/work-3x3.yaml`: the nine supplied LAZ tiles, E 23459000–23460500, N 6703500–6705000.
- `checksums/REPORT-work-3x3.json`: real fetch coverage and counts; `*.sha256` cover the cached source files.
