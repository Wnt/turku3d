# Input fetchers

Run locally with the native environment. Shared raw input preparation is explicit; normal worker
builds only check and read the prepared cache.

```
make fetch-aoi           # laz + wfs + wms + osm + report   (all idempotent; explicit preparation/refresh)
make fetch-laz | fetch-wfs | fetch-wms | fetch-osm | fetch-report
make fetch-checksums     # export raw/*/SHA256SUMS + REPORT-<area>.json into data-manifests/checksums/
bash pipeline/env/run.sh python -m turku3d.fetch verify                 # re-hash every raw file
bash pipeline/env/run.sh python -m turku3d.fetch wfs --layers GIS:Puut  # one layer
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


## Working area and cached builds

`make fetch-aoi AREA=work-3x3` prepares the nine LAZ tiles' 1.5 × 1.5 km envelope.
Existing `raw/laz-3x3/*.laz` files are checksum-verified and hard-linked into the standard
pistepilvi layout; they are never downloaded again. Maanpinta, every configured WFS layer,
2022 true ortho, 2025 ortho, drone imagery and the OSM clip use the same named area.
The known restricted WFS layers remain explicitly reported as unauthorized.

Working-area WMS chunks are 4000 px (within the service's 4096 px limit). Blank chunks
expire after one hour; legacy undated blanks retry immediately. Nonblank chunks retain
checksum-backed caching. `fetch-aoi` is the explicit refresh operation; `make tile` never
hits the network or writes shared inputs. OSM reuses its dated, verified Finland snapshot.

```sh
make fetch-aoi AREA=work-3x3
make fetch-report AREA=work-3x3
make fetch-checksums AREA=work-3x3
bash pipeline/env/run.sh python -m turku3d.fetch verify
```
