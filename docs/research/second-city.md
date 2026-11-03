# Second city: data availability and licences ("second-city")

Date: 2026-11-03. The question was which city is easiest to add next, outside Turku (another major
Finnish city or a European one), and under which licences its data can be published.

Each claim is tagged **[VERIFIED]** (probed with `curl` or read from the publisher today) or
**[UNVERIFIED]** (from search results or catalogue text that was not re-checked). Probe commands are
listed at the end. No downloaded data is kept in the repository.

---

## 0. Bottom line

- **Easiest next city: Espoo.** It runs the same Tekla/Trimble WFS server software as Turku, and Turku's
  layer names and schemas carry over. Two adapters are missing: street surfaces and keyless ground heights.
- **Best Finnish game city: Helsinki.** It has the richest data, uses the same coordinate system as Espoo
  (ETRS-GK25, N2000), and can join the same world second. It runs different server software, so most
  layers need a new adapter.
- **Easiest outside Finland: Tallinn.** It has nationwide LOD2 buildings, lidar and orthophotos with no
  key needed. Heights are EH2000, close to N2000, so the 0 m sea surface still holds.
- **Best data in Europe: the Netherlands** (3DBAG, AHN, BGT, 8 cm ortho). It needs the most
  adaptation: RD New + NAP, Dutch vocabularies, and inland water that is not at 0 m.
- **Every source checked allows published derivatives** (meshes, collision and textures from lidar)
  with attribution. See §4.
- **Side finding:** Finland's national 2 m DEM and 0.5 p lidar are on the Funet mirror with no API
  key (§2.3). The statement in `pipeline/turku3d/coarse/README.md` that the MML 2 m DEM needs an API
  key is out of date.

## 1. What a new city must supply

The draft generator (`pipeline/turku3d/coarse/sources.py`) builds a tile from four units per 500 m block:

| Dataset | Turku source | Role |
|---|---|---|
| `lidar` (required) | `turku.asiointi.fi/3d/maanpinta/<block>.laz` ground key points; optional `pistepilvi` class 6 | Ground grid, collision; roof-plane refinement |
| `ortho` (required) | Tekla WMS `Ilmakuva 2022 True ortho`, 512 px per 250 m tile (49 cm/px) | Green-pixel ground classification |
| `wfs` (required) | `bldg:Building_LOD2`, `GIS:Katualueet`, `kanta:Vesikohde`, `GIS:Liikennemerkit`, `kanta:Pysakki`, `GIS:Keskilinjat` (+ optional `kanta:Korkeuspiste`) | Buildings, street surfaces and their collision material, water, signs, stops |
| `osm` (optional) | Geofabrik Finland snapshot, osmium clip | Street graph, kerbs, bridges, tunnels, barriers, trees |

The facade kit reads register fields `julkisivumateriaali` and `kerrosluku` (`coarse/register.py`).
Street materials come from Finnish tokens in `pipeline/turku3d/ground/materials.py::surface_material`.

City coupling, counted with `git grep -l`:
- **Coarse files:** 3 use the CRS/origin constants, 10 the Turku lidar names or the LAZ index, 3 the
  Tekla/WMS endpoints, 6 the N2000 water level, and 2 the Finland OSM snapshot.
- **Client:** `game/src/core/origin.ts` (E0/N0), `game/src/core/geographic.ts` (EPSG:3877 inverse
  TM), `sun.ts`, `save.ts`, `gameplay/startPoints.ts` and `gameplay/content/places.ts`.

## 2. Finnish candidates

### 2.1 Espoo (ETRS-GK25, EPSG:3879)

- **[VERIFIED] Same server as Turku.** `https://kartat.espoo.fi/teklaogcweb/wfs.ashx` is a Tekla
  "Espoo WFS Server" with the same `kanta:`/`GIS:` layer families. `GIS:Katualueet` has Turku's schema
  (`Katuosalaji(_koodi)`, `Pintamateriaali(ryhma/_koodi)` …); only Turku's `Kadunnimi` is missing.
- **[VERIFIED] GetFeature status by layer:**
  - HTTP 200: `bldg:building_lod2` (CityGML 2.0, lower-case name), `kanta:Liikennevayla`,
    `kanta:Kevyenliikenteenvayla`, `kanta:Rakenne`, `kanta:Vesikohde`, `kanta:Korkeuspiste`,
    `kanta:Lehtipuu`, `kanta:Rakennus`, `GIS:Keskilinjat`, `GIS:Rakennukset`, `GIS:InfStreet`,
    `tran:road_lod2`.
  - HTTP 401 although listed in GetCapabilities: `GIS:Katualueet`, `GIS:Liikennemerkit`,
    `GIS:Viheralueet`.
  - Not offered: `kanta:Pysakki`, `GIS:Varusteet`, `GIS:Puut`.
- **[VERIFIED] Street surfaces need an adapter for `GIS:InfStreet`.** It has street-part polygons
  with `PARTCLASSTEXT` (e.g. `Kevyt liikenne`), `USETYPETEXT` (e.g. `Puistoraitti`) and
  `PAVINGCLASSTEXT` (e.g. `Kivituhka / Sora / Murske`). The full paving vocabulary was not enumerated.
  Signs and stops come from OSM.
- **[VERIFIED] Orthophoto:** WMS `https://kartat.espoo.fi/teklaogcweb/wms.ashx` has `Ortokartta`
  and yearly layers through `Ortokartta_2024`. `Ortokartta_2026_rajoitettu` is restricted. The open
  data page gives the 2024 orthophoto as 30 cm, enough for drafts at 49 cm/px.
- **[VERIFIED] No open city lidar** on `kartat.espoo.fi/opendata`. Ground would come from §2.3, and
  roofs keep their surveyed LOD2 planes (the existing fallback).
- **Effort:** the CRS profile, a ground-from-DEM adapter, an `InfStreet` material adapter, and the
  layer-name map. The building, water, centreline and kerb/stair/wall parsers are reused.

### 2.2 Helsinki (ETRS-GK25, EPSG:3879, N2000)

- **[VERIFIED] GeoServer WFS** `https://kartta.hel.fi/ws/geoserver/avoindata/wfs` (JSON output works):
  - `YLRE_Katuosat_alue` (street parts: `paatyyppi`, `alatyyppi`, `materiaali`), plus
    `YLRE_Katualue_alue` and `YLRE_Katu_ja_viherosat_*`.
  - `Rakennukset_alue_rekisteritiedot` (`c_julkisivu` facade material, `i_kerrlkm` floors,
    `c_rakeaine`, `c_kayttark` …).
  - `Puurekisteri_piste` trees, and water polygons in `Maavesi_*` / `Merialue_kantakartasta`.
  - No traffic-sign layer and no lidar tile index were found in this WFS.
- **[VERIFIED] Street materials already match our tokens.** In a sample of 400 `YLRE_Katuosat_alue`
  features, `materiaali` values were `Asfalttibetoni`, `Betonikivi`, `Sora`, `Nupukivi`, `Kenttäkivi`,
  `Betoni - valettu`, `Sirotepintaus`, `Luonnontilainen` and `Istutusalue - nurmi`. These match the
  existing Finnish tokens; `paatyyppi`/`alatyyppi` include `Ajorata`, `Jalankulkualue`, `Koroke` and
  `Yhdistetty jk ja pp`.
- **[VERIFIED] 3D:** `https://kartta.hel.fi/3d/citydb-wfs/wfs` (WFS 2.0) serves `bldg:Building` from a
  3DCityDB. **[UNVERIFIED]** CityGML LoD1/LoD2 files are also offered as 4 km² map sheets.
- **[VERIFIED, catalogue] Lidar** from 2021, 2017 and 2015: "Maanpinta" (ground only, like Turku's
  `maanpinta`) and "Kaikki pisteet" LAZ per GK25 sheet, EPSG:3879 + N2000. The download is through the
  `kartta.hel.fi` map UI; **no scriptable URL pattern was found**. §2.3 is the fallback.
- **[UNVERIFIED] Orthophoto:** WMS `https://kartta.hel.fi/ws/geoserver/avoindata/wms`, with layers
  such as `Ortoilmakuva_2025_5cm` (catalogue listing).
- **Effort:** new fetch adapters for buildings (3DCityDB GML), street parts, water and the register,
  plus the same CRS profile as Espoo.

### 2.3 National Land Survey (MML) data without an API key

- **[VERIFIED] Mirror:** `https://mirrors.nic.funet.fi/index/geodata/mml/`.
  - `dem2m/dem2m_direct.vrt` is a nationwide 2 m DEM VRT (EPSG:3067 TM35FIN, dated 2026-11-01). A
    range request reads it, so GDAL `/vsicurl/` can window it.
  - `laserkeilaus/2008_latest/` holds 0.5 p lidar by year, 2008–2022, with a `2008_latest.shp` index
    dated 2026-06-01.
- This makes any Finnish city, and Turku outside the city lidar index, buildable from ground heights
  without a key.

### 2.4 Tampere and Oulu

**[UNVERIFIED]** Searches found no open 3D building model or lidar for either city. Tampere's
GeoServer has plan layers and older aerial photos.

## 3. European candidates

### 3.1 Tallinn (Estonia)

- **[VERIFIED] 3D buildings:** `geoportaal.maaamet.ee` "Download 3D data" offers LOD2 buildings as
  CityGML, OBJ and gdb. The 2026 edition covers Estonia with 856,360 objects; Tallinn has 49,816
  objects (31.5 MB CityGML).
- **[UNVERIFIED]** The Land Board also provides:
  - lidar as LAZ 1.4 in 1 km² tiles, with class 6 for buildings;
  - DEMs from the 2021–2024 scans;
  - orthophotos at 10–16 cm in urban areas, via WMS.
  The CRS is EPSG:3301 (L-EST97, Lambert conformal conic) with EH2000 heights.
- **Gaps:** no city street-surface polygons were found, so surfaces would come from OSM. The client
  needs a Lambert projection.

### 3.2 Netherlands (Amsterdam, Rotterdam, Utrecht)

- **[VERIFIED] BGT:** the PDOK OGC API `https://api.pdok.nl/lv/bgt/ogc/v1` is CC0 and needs no key.
  Its 49 collections include:
  - `wegdeel`, with `functie` and `fysiek_voorkomen` (sample: `rijbaan lokale weg`, `open verharding`);
  - `overbruggingsdeel`, `tunneldeel`, `waterdeel`, `scheiding_*`;
  - `paal`, `bord`, `straatmeubilair`, `vegetatieobject_*`.
- **[VERIFIED] Orthophoto:** PDOK WMTS `Actueel_orthoHR` is the 8 cm national orthophoto, with
  `Fees` none and `AccessConstraints` none.
- **[VERIFIED] 3DBAG** offers LoD1.2/1.3/2.2 buildings through WFS, 3D Tiles 1.1, CityJSON, GeoPackage
  and OBJ. It is produced by the TU Delft 3D geoinformation group and 3DGI, the group behind roofer.
- **[UNVERIFIED]** AHN lidar is CC0 (secondary sources).
- **Gaps:**
  - New CRS (RD New + NAP) and Dutch vocabularies throughout.
  - Canals and polders are not at 0 m, so the fixed N2000 0 m water surface rule breaks.

### 3.3 Vienna

**[UNVERIFIED]** The city's building model (Baukörpermodell) is LOD1.4 prisms, not LOD2 roofs. Its
geodata is CC BY 4.0 per INSPIRE metadata. It is not a good first candidate.

## 4. Licences

| Source | Licence | Notes |
|---|---|---|
| Turku: city information model (WFS CityGML, 2021 lidar LAZ and ground points), base map, street areas, traffic signs, aerial photos 2018/2021/2022, drone imagery | CC BY 4.0 **[VERIFIED, avoindata.suomi.fi]** | No 2025-specific photo entry was found; "Turun seudun ilmakuva" is CC BY 4.0 |
| Espoo: 3D city model, 3D buildings, orthophotos, public-areas register | CC BY 4.0 **[VERIFIED]** | The 401 layers are not open data |
| Helsinki: 3D models, lidar, orthophotos, public-areas register, buildings | CC BY 4.0 **[VERIFIED]** | Credit "© City of Helsinki, City Survey Services"; some regional orthophotos are jointly owned (capital-region cities, HSL, HSY) |
| MML 2 m DEM and 0.5 p lidar | CC BY 4.0 **[VERIFIED]** | Credit the NLS, the dataset name and its date |
| Estonian Land Board | Own open-data licence, 01.01.2025 **[VERIFIED, licence PDF]** | Allows derivatives, combination, commercial use and redistribution. The reference must name the Board, the dataset and its date. Attach the licence link when passing data to third parties. Remove the reference on written request. |
| NL BGT / AHN | CC0 (BGT **[VERIFIED]**, AHN **[UNVERIFIED]**) | |
| 3DBAG; PDOK orthophotos | CC BY 4.0 / CC BY **[VERIFIED]** | |
| OpenStreetMap | ODbL 1.0 | Game visuals are a Produced Work (credit). Served road graphs are Derivative Databases (ODbL). |

**Publishing lidar derivatives is allowed.** CC BY 4.0 §2 grants the right to produce and share
Adapted Material, commercially as well, and §4 covers sui generis database rights. The conditions in
§3(a) are:
- identify the source and keep its notices;
- link to the licence;
- indicate the changes;
- add no terms or technical measures that restrict the licensed data.

The licence implies no endorsement. Turku's attribution was completed on this branch in commit
`9fd84d0e`: start-screen links, a "derived from" wording, and the ODbL note in `NOTICE.md`.

## 5. Platform work for any second city

1. Turn the module constants in `pipeline/turku3d/config.py` into a per-city profile: CRS, origin,
   500 m block grid, source endpoints and layer-name map, OSM extract, water datum.
2. Parameterise the client projection: the GK central meridian for Finnish cities; Lambert or
   oblique stereographic projections for Tallinn or the Netherlands. Also update origin, sun, save,
   start points and places.
3. Give each city its own namespace on the OCI draft service (`/coarse/<city>/`), its own multiplayer
   rooms, and its own credits on the start screen.

Suggested first step: a local Espoo one-tile test (Tapiola) built from the Tekla WFS, an
`InfStreet` adapter and the keyless 2 m DEM. Production generator changes need Jonni's OK.

## 6. Probe commands

```sh
E=https://kartat.espoo.fi/teklaogcweb/wfs.ashx
curl -s "$E?service=WFS&request=GetCapabilities" | grep -o '<Name>[^<]*'
curl -s -o /dev/null -w '%{http_code}\n' \
  "$E?service=WFS&version=1.1.0&request=GetFeature&typeName=GIS:Katualueet&maxFeatures=3"   # 401
curl -s "$E?service=WFS&version=1.1.0&request=GetFeature&typeName=GIS:InfStreet&maxFeatures=3"
curl -s "https://kartat.espoo.fi/teklaogcweb/wms.ashx?service=WMS&request=GetCapabilities" | grep -o '<Name>Ortokartta[^<]*'
curl -s "https://kartta.hel.fi/ws/geoserver/avoindata/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=avoindata:YLRE_Katuosat_alue&count=400&outputFormat=application/json&propertyName=paatyyppi,alatyyppi,materiaali"
curl -s "https://kartta.hel.fi/3d/citydb-wfs/wfs?service=WFS&request=GetCapabilities&version=2.0.0"
curl -s -r 0-1500 https://mirrors.nic.funet.fi/index/geodata/mml/dem2m/dem2m_direct.vrt
curl -s "https://api.pdok.nl/lv/bgt/ogc/v1/collections/wegdeel/items?f=json&limit=1"
curl -sL -G https://avoindata.suomi.fi/data/api/3/action/package_search \
  --data-urlencode 'q=Turun kaupungin kaupunkitietomalli' --data-urlencode rows=1   # license_title
```

## Sources

- Espoo: [3D services](https://kartat.espoo.fi/3D/services_en.html), [open data](https://kartat.espoo.fi/opendata/),
  [3D model in the catalogue](https://avoindata.suomi.fi/data/en_GB/dataset/espoon-3d-kaupunkimalli)
- Helsinki: [3D models](https://avoindata.fi/data/en/dataset/helsingin-3d-kaupunkimalli),
  [lidar](https://hri.fi/data/en/dataset/helsingin-laserkeilausaineistot),
  [public-areas register](https://hri.fi/data/en/dataset/6a67a371-b065-40d2-81ad-420049ecbf00),
  [orthophotos](https://avoindata.suomi.fi/data/en_GB/dataset/helsingin-ortoilmakuvat)
- MML: [Funet mirror](https://mirrors.nic.funet.fi/index/geodata/mml/),
  [lidar 0.5 p](https://avoindata.suomi.fi/data/en_GB/dataset/laserkeilausaineisto-05-p-2008-2019)
- Turku: [city information model in the catalogue](https://avoindata.suomi.fi/data/en_GB/dataset?q=Turun+kaupungin+kaupunkitietomalli)
- Estonia: [3D download](https://geoportaal.maaamet.ee/eng/Download-3D-data-p837.html),
  [elevation data](https://geoportaal.maaamet.ee/eng/Spatial-Data/Elevation-data-p308.html),
  [open data licence](https://geoportaal.maaamet.ee/opendata-licence)
- Netherlands: [BGT OGC API](https://api.pdok.nl/lv/bgt/ogc/v1),
  [PDOK aerial photo WMTS](https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?request=GetCapabilities&service=wmts),
  [PDOK aerial photo licence](https://www.pdok.nl/introductie/-/article/pdok-luchtfoto-rgb-open-),
  [3DBAG webservices](https://docs.3dbag.nl/en/delivery/webservices/), [3DBAG copyright](https://docs.3dbag.nl/en/copyright/)
- Vienna: [building model](https://www.wien.gv.at/stadtentwicklung/stadtvermessung/geodaten/bkm/),
  [INSPIRE record](https://inspire-geoportal.ec.europa.eu/srv/api/records/338b3ca5-2153-451f-b6e1-3df0c89aefbc)
- Licences: [CC BY 4.0 legal code](https://creativecommons.org/licenses/by/4.0/legalcode),
  [OSM Produced Work guideline](https://wiki.openstreetmap.org/wiki/Open_Data_License/Produced_Work_-_Guideline)
