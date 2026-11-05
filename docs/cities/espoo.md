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

## Point cloud

`fetch/mml_lidar.py` writes `raw/laz/pistepilvi/<block>.laz` from the MML sheets under each block (EPSG:3067 ->
GK25, N2000; the per-block median of MML ground returns against the ground key points is -0.02 .. 0.00 m).
MML 2008 has classes 1/2/3/9 only, so each block is reclassified against today's sources: returns inside a
current LoD2 footprint more than 2 m above ground are building (6) unless that building's returns disagree with
its LoD2 roof by more than 2.5 m (built or rebuilt since 2008: its returns are dropped; Tapiola centre: 21-22 of
44-51 buildings per block); other unclassified returns are vegetation by height (3/4/5), dropped over today's
carriageways and within 1 m of a building. Roof refinement, LiDAR tree crowns and the fence/stair/retaining
surveys then run unchanged. At ~1 pt/m² (Turku ~30) fewer roof planes reach the refinement's support limits and
overhead wires are not resolvable. Espoo's own open lidar is ground only ("Maanpintapisteet 2017", model key
points like Turku's maanpinta) and is downloadable only through the IMS map UI; it would replace the DEM ground.

## Turku's coarse stages and their Espoo inputs

| Stage | Turku input | Espoo input |
|---|---|---|
| Ground grid, collision | city lidar maanpinta key points | MML 2 m DEM as key points (Espoo 2017 key points when scriptable) |
| Ground materials | GIS:Katualueet paving > OSM surface/landuse > ortho green | InfStreet -> GIS:Katualueet paving > the same OSM tags > Ortokartta_2024 |
| Kerbs, paint, crossings | OSM kerb/crossing/lane ways | same OSM (Espoo kanta:Liikennevayla kerb lines are fetched as in Turku) |
| Water | kanta:Vesikohde + OSM water, N2000 0 m | same layers, N2000 0 m |
| Bridges, tunnels | OSM decks/tunnels, GIS:Katualueet Silta, lidar profiles | same OSM; InfStreet has no Silta class; MML ground |
| Buildings | LoD2 + pistepilvi roof refinement + kanta:Rakennus/GIS:Rakennukset | Espoo LoD2 + reclassified MML returns + the same registers |
| Facades | facade programs from registers and OSM | same (no Turku per-building characterizations) |
| Trees | GIS:Puut + OSM natural=tree + LiDAR crowns (trees-lidar@1) | InfVegetationPoint/kanta trees -> GIS:Puut + OSM + crowns from MML class 5 |
| Furniture, lights | GIS:Varusteet, frn:CityFurniture_LOD2, bike/parking registers, OSM | InfEquipment -> GIS:Varusteet, frn:cityfurniture_lod2, OSM |
| Signs, stops | GIS:Liikennemerkit, kanta:Pysakki, OSM | OSM only (401 / not offered) |
| Stairs, retaining walls, spot heights | kanta:Rakenne, kanta:Korkeuspiste, lidar surveys | same layers, MML returns |
| Barriers, gardens, forests, venues | OSM (+ lidar heights) | same OSM, MML returns |
| Wires (wires@2) | pistepilvi wire returns | none resolvable at ~1 pt/m² |
| Playgrounds | Turku survey (private) | OSM leisure=playground only |

## Gaps and how each is filled

| Internal layer (Turku schema) | Espoo | Filled by |
|---|---|---|
| `GIS:Katualueet` street parts | 401 | **derived** from `GIS:InfStreet` (`fetch/adapters.py`): `PARTCLASSTEXT`/`USETYPETEXT` -> `Katuosalaji` (+ Turku code), `PAVINGCLASSTEXT` -> `Pintamateriaali` (+ code); `Tunnus`/`Kadunnimi` from `LABEL`. Unseen values -> `Muu`/`Ei tietoa`, counted in the summary's `unmapped` |
| `GIS:Liikennemerkit` signs | 401 | empty layer; OSM `traffic_sign` (the props reader already merges OSM signs) |
| `GIS:Viheralueet` green areas | 401 | not read by the draft generator; OSM landuse/leisure and the ortho's green pixels |
| `kanta:Pysakki` stops | not offered | empty layer; OSM `highway=bus_stop` |
| `GIS:Varusteet` furniture | not offered | **derived** from `GIS:InfEquipment` (Tyyppi/Varustelaji/Malli <- EQUIPMENTCLASSTEXT/EQUIPMENTGROUPTEXT/MODELTEXT; the props reader's label rules classify benches, bins, bike racks, signs posts); OSM where the register has nothing within 3 m |
| `GIS:Puut` trees | not offered | **derived** from `GIS:InfVegetationPoint` trees (species, planting year) plus `kanta:Lehtipuu`/`kanta:Havupuu` points the register lacks (1.5 m dedupe); OSM trees within 3 m are dropped as in Turku |
| `GIS:Polkupyoraparkki`, `GIS:Pysakointiautomaatit`, `GIS:Follariasemat` | not offered | empty layers; OSM equivalents |
| `GIS:Lupapysakointipaikat` | not offered | empty; no permit-parking kerbs |
| Street lights `frn:CityFurniture_LOD2` | `frn:cityfurniture_lod2` | city layer (renamed) |
| City lidar `pistepilvi` | none open | **derived** from MML 0.5 p (2008, ~1 pt/m²) reclassified to Turku's classes, `fetch/mml_lidar.py` (see Point cloud) |

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
