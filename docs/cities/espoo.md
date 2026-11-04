# Espoo

The second city the generator builds. Profile: `pipeline/turku3d/cities/__init__.py` (`ESPOO`); one process
builds one city, selected by `TURKU3D_CITY=espoo` or the data root's `city.json`. Turku stays the default.

| | |
|---|---|
| CRS | EPSG:3879 (ETRS89 / GK25FIN) + N2000, the WFS DefaultSRS of every Espoo layer |
| Origin O | E 25489000, N 6674000 (NW corner of block 25489000_6674000) |
| Centre spawn | Tapiontori, Tapiola: E 25489150 N 6673640 (engine x 150, z 360, tile t0_0_1) |
| Sample | 2 x 2 blocks E 25489000-25490000, N 6673000-6674000 = L0 `t0_0_0` .. `t0_3_3` (Tapiola centre, Keskusallas, Otsolahti) |
| Client | `?city=espoo` (`game/src/core/city.ts`): origin, spawn, GK zone, north bearing, own save key |

## Sources

| Dataset | Source | Date | Licence |
|---|---|---|---|
| Buildings | Espoo WFS `bldg:building_lod2` (CityGML 2.0, read as the internal `bldg:Building_LOD2`) | live register, fetched 2026-11-05 | CC BY 4.0 Espoon kaupunki |
| Building registers | `kanta:Rakennus`, `GIS:Rakennukset` | live | CC BY 4.0 Espoon kaupunki |
| Street parts | `GIS:InfStreet` -> derived `GIS:Katualueet` (below) | live | CC BY 4.0 Espoon kaupunki |
| Centrelines, kerbs, structures, water, spot heights | `GIS:Keskilinjat`, `kanta:Liikennevayla`, `kanta:Kevyenliikenteenvayla`, `kanta:Rakenne`, `kanta:Vesikohde`, `kanta:Korkeuspiste` | live | CC BY 4.0 Espoon kaupunki |
| Orthophoto | Espoo WMS `Ortokartta_2024` (30 cm native, requested at 49 cm/px) | 2024 | CC BY 4.0 Espoon kaupunki |
| Ground | MML 2 m DEM, `mirrors.nic.funet.fi/index/geodata/mml/dem2m/` (keyless) | mirror 2026-11-01 (Tapiola sheet L4131H: 2025-02-09) | CC BY 4.0 Maanmittauslaitos |
| Point cloud (reference) | MML 0.5 p LAZ `laserkeilaus/2008_latest/` | 2008 scan, the newest 0.5 p over Espoo | CC BY 4.0 Maanmittauslaitos |
| OSM | Geofabrik Finland snapshot, osmium clip per 2 km square | dated snapshot | ODbL 1.0 |

The same rows are in `data-manifests/sources.yaml` under `cities: espoo`.

## Ground

Espoo has no open city lidar. `fetch/dem.py` samples the 2 m DEM (EPSG:3067, read through GDAL `/vsicurl/`
from the mirror's VRT, so only the window's bytes move) at the centres of a 2 m grid in GK25 and writes the
block as `raw/laz/maanpinta/<block>.laz`, class 8 key points at 1 mm scale, the format of Turku's city
`maanpinta` blocks (about 62,500 points per block against Turku's ~360,000). Every ground, water and
collision producer reads it unchanged.

The 0.5 p LAZ over Tapiola is the 2008 scan: Tapiola centre, the metro and Länsiväylä have been rebuilt since,
so it is fetched as reference only and not used as `pistepilvi` (roof-plane refinement and LiDAR trees/wires
would fit 2008 objects). Roofs keep their surveyed LoD2 planes, the existing fallback. The MML 5 p scans are
not on the keyless mirror.

## Gaps and how each is filled

| Internal layer (Turku schema) | Espoo | Filled by |
|---|---|---|
| `GIS:Katualueet` street parts | 401 | **derived** from `GIS:InfStreet` (`fetch/adapters.py`): `PARTCLASSTEXT`/`USETYPETEXT` -> `Katuosalaji` (+ Turku code), `PAVINGCLASSTEXT` -> `Pintamateriaali` (+ code); `Tunnus`/`Kadunnimi` from `LABEL`. Unseen values -> `Muu`/`Ei tietoa`, counted in the summary's `unmapped` |
| `GIS:Liikennemerkit` signs | 401 | empty layer; OSM `traffic_sign` (the props reader already merges OSM signs) |
| `GIS:Viheralueet` green areas | 401 | not read by the draft generator; OSM landuse/leisure and the ortho's green pixels |
| `kanta:Pysakki` stops | not offered | empty layer; OSM `highway=bus_stop` |
| `GIS:Varusteet` furniture | not offered | empty layer; OSM benches, bins, lamps (`GIS:InfEquipment` is Espoo's register: a later adapter) |
| `GIS:Puut` trees | not offered | empty layer; OSM `natural=tree` |
| `GIS:Polkupyoraparkki`, `GIS:Pysakointiautomaatit`, `GIS:Follariasemat` | not offered | empty layers; OSM equivalents |
| `GIS:Lupapysakointipaikat` | not offered | empty; no permit-parking kerbs |
| Street lights `frn:CityFurniture_LOD2` | `frn:cityfurniture_lod2` | city layer (renamed) |
| City lidar `pistepilvi` | none | none (see Ground) |

An empty layer is stored with `status: empty` and its reason in `gap`, so a build does not wait for it.

### InfStreet -> Katualueet

| PARTCLASSTEXT / USETYPETEXT | Katuosalaji (code) |
|---|---|
| Ajorata / Pysäköintialue | Muu liikennealue (12) |
| Ajorata / Liikenteenjakaja | Välikaista (13) |
| Ajorata / other (Pääkatu, Kokoojakatu, Tontti-/asunto-/liityntä-/pihakatu, Pääväylä - maantie) | Ajorata (1) |
| Kevyt liikenne / Erotus/Välikaista | Välikaista (13) |
| Kevyt liikenne / Jalkakäytävä | Jalkakäytävä (2) |
| Kevyt liikenne / Pyörätie | Pyörätie (3) |
| Kevyt liikenne / Yhdistetty pyörätie ja jalkakäytävä (and other) | Yhdistetty kevyen liikenteen väylä (4) |
| Kevyt liikenne / Puistoraitti, Tori/Aukio | Kävelytie (16) |
| Kevyt liikenne / Polkupyörä-, Potkulautapysäköinti | Muu (6) |

| PAVINGCLASSTEXT | Pintamateriaali (code) |
|---|---|
| Asfalttibetoni AB | Asfalttibetoni (1) |
| Betoni | Betoni (14) |
| Luonnonkivi | Luonnonkivi (011) |
| Kenttäkivi | Kenttäkivi (4) |
| Puu | Puu (13) |
| Kivituhka / Sora / Murske | Kivituhka (18) |
| Nurmikivi | Nurmikivi (10) |
| Metalli | Metalli (19) |

## Commands (osgallery-dev or the UpCloud staging host)

```sh
tools/cities/setup-root.sh espoo                     # data root, raw on /bulk/turku3d/cities/espoo/raw
tools/cities/run.sh espoo python tools/cities/fetch.py --bbox 25489000 6673000 25490000 6674000 --mml-laz
tools/cities/run.sh espoo python -m turku3d.coarse build t0_1_1 --out "$HOME/turku3d-local/cities/espoo/tiles/t0_1_1"
```
