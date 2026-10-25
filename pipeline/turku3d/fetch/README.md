# turku3d.fetch — cached, checksummed, resumable fetchers

Run on the remote (bulk data in `~/turku3d/data/raw/`) via make from the Mac; each target rsyncs the
repo first. `AREA` defaults to `office-block` (Phase 1 AOI: 250 m core + 50 m margin, from config.py).

```
make fetch-aoi           # laz + wfs + wms + osm + report   (all idempotent; a re-run takes ~15 s)
make fetch-laz | fetch-wfs | fetch-wms | fetch-osm | fetch-report
make fetch-checksums     # pull raw/*/SHA256SUMS + REPORT-<area>.json into data-manifests/checksums/
make pipeline-ssh-run CMD='python -m turku3d.fetch verify'                 # re-hash every raw file
make pipeline-ssh-run CMD='python -m turku3d.fetch wfs --layers GIS:Puut'  # one layer
```

| Step | Output under `raw/` | Notes |
|---|---|---|
| laz | `laz/{pistepilvi,maanpinta}/<xmin>_<ymax>.laz` | `curl -fL -C -` into `.part`, HEAD size check, then sha256 |
| wfs | `wfs/<area>/gml/<layer>/<bbox>.gml` pages, `wfs/<area>/<layer>.parquet` + `.summary.json` | the server caps at **1000 features per request** (no paging in WFS 1.0.0), so bboxes that hit the cap are split into quadrants until they don't; features are de-duplicated by gml:id or XML hash. GeoParquet: `geometry` EPSG:3877 (+Z), `geometry_local` = (x, z[, H]) engine coords, one row per geometry, attributes as strings. The BBOX filter returns whole intersecting features, so geometries reach beyond the area. |
| wms | `ortho/<slug>/<area>/<i>_<j>.tif` + `<area>.vrt`, `<area>.chunks.json`, `<area>.summary.json` | 2000 px chunks aligned to O; 5 cm for 2022 true ortho / 2025, 1.25 cm for drone; tiled GeoTIFF EPSG:3877 with an internal mask (transparent or pure-white pixels = no data); empty chunks are recorded, not stored |
| osm | `osm/geofabrik/finland-YYMMDD.osm.pbf`, `osm/<area>/<area>-YYMMDD.{osm.pbf,parquet,summary.json}` | `finland-latest` resolved to the dated file, md5-checked, clipped with `osmium extract --strategy smart` (area + margin + 100 m); smart keeps complete ways/multipolygons, so some geometries extend km away |
| report | `REPORT-<area>.json` | point count/classes/density in the core, 1 m class-2 cell coverage, WFS counts per layer, ortho mosaic size + estimated native resolution, OSM counts |

Each kind keeps `raw/<kind>/SHA256SUMS` (sha256sum -c compatible) and `MANIFEST.jsonl` (url, bytes,
sha256, time, HTTP metadata). A file is skipped when its size matches the registry (`--verify` re-hashes).
Endpoints and layer lists: `sources.py` (→ `data-manifests/sources.yaml`, `make pipeline-sources`).
