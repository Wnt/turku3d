# Amsterdam and Rotterdam

The first Dutch cities. Profiles: `pipeline/turku3d/cities/__init__.py` (`AMSTERDAM`, `ROTTERDAM`); one
process builds one city (`TURKU3D_CITY` or the data root's `city.json`), as for Espoo (`docs/cities/espoo.md`).

| | Amsterdam | Rotterdam |
|---|---|---|
| CRS | EPSG:28992 (Amersfoort / RD New) + NAP | same |
| Origin O | E 121000, N 487500 (NW corner of the block holding Dam) | E 93000, N 437500 (block holding the Markthal) |
| Centre spawn | Dam: E 121318 N 487362 (engine x 318, z 138, tile `t0_1_0`) | Binnenrotte at the Markthal arch: E 93075 N 437390, heading 180 (x 75, z 110, `t0_0_0`; the Markthal point itself is inside the building) |
| Sample (1 km²) | E 120500-121500, N 487000-488000 = `t0_-2_-2` .. `t0_1_1`: Dam, Singel, Herengracht, Keizersgracht, Prinsengracht, Westerkerk | E 92500-93500, N 436500-437500 = `t0_-2_0` .. `t0_1_3`: Markthal, Blaak, Oude Haven, Leuvehaven, Boompjes, north end of the Erasmusbrug |
| Water level | NAP -0.40 m (Amsterdam canal level) | NAP 0.0 m (tidal Nieuwe Maas) |
| Tile service | `http://192.168.1.114:7980/coarse/` (preview page :7981) | `:7990/coarse/` (preview :7991) |

## Sources

| Dataset | Source | Date | Licence |
|---|---|---|---|
| Point cloud | AHN5 LAZ, GeoTiles 1 x 1.25 km sub-tiles of the AHN sheets (`geotiles.citg.tudelft.nl/AHN5_T/`; AHN4 where AHN5 is missing) | AHN5 2023 flight | CC0 1.0 (AHN); tiling GeoTiles, TU Delft |
| Buildings | 3DBAG v2025.09.03 CityJSON tiles (`data.3dbag.nl/v20250903/tile_index.fgb`), LoD2.2 BuildingPart solids | 2025-09-03 release | CC BY 4.0 3DBAG (tudelft3d, 3DGI) |
| Topography | BGT OGC API (`api.pdok.nl/lv/bgt/ogc/v1`), all 49 collections per 500 m block, current objects only | live register, fetched 2026-11-05 | CC0 1.0 Kadaster / PDOK |
| Orthophoto | PDOK Luchtfoto RGB WMS `2025_orthoHR` (8 cm), requested at 49 cm/px | 2025 | CC BY 4.0 Beeldmateriaal Nederland |
| OSM | Geofabrik `noord-holland` / `zuid-holland` snapshot, osmium clip per 2 km square | 2026-11-03 | ODbL 1.0 |

Native downloads live in `raw/nl/` of the city's data root (on the bulk pool,
`/bulk/turku3d/cities/<city>/raw/nl`) with `SOURCES.json` recording every file's URL, licence, bytes,
sha256 and fetch date. The rows are also in `data-manifests/sources.yaml` under `cities:`.

## How the sources become the internal schema (`turku3d.nl.convert`)

- **Ground** (`ground = "nl-ahn"`): per 500 m block, AHN ground (class 2) thinned to the lowest return per
  1.5 m cell plus AHN water returns (class 9) per 4 m cell, written as class 8 key points in
  `raw/laz/maanpinta/<block>.laz`. BGT `waterdeel` cells without returns get key points at the city's water
  level, and ground returns more than 1 m inside water (boats, pontoons) are dropped, so canals and harbours
  read as water in the ground and water-level producers. Each AHN sub-tile is decoded once into
  `<name>.keypoints-*.npz` beside it.
- **Buildings** (`bldg:Building_LOD2` <- `3dbag`): one row per LoD2.2 semantic surface (Roof/Wall/Ground),
  `storeysAboveGround` = `b3_bouwlagen`, `yearOfConstruction` = `oorspronkelijkbouwjaar`,
  `measuredHeight` = `b3_h_dak_70p - b3_h_maaiveld`.
- **Street areas** (`GIS:Katualueet` <- `bgt-streets`): `wegdeel` and `ondersteunendwegdeel` polygons with
  Turku's register vocabulary and codes (`BGT_KIND`, `BGT_SURFACE`): `rijbaan *` -> Ajorata, `fietspad` ->
  Pyörätie, `voetpad` -> Jalkakäytävä, `voetpad op trap` -> Portaat, `parkeervlak` -> Muu liikennealue ...;
  `gebakken klinkers` -> Maatiili (brick paving finish), `asfalt` -> Asfalttibetoni, `betonstraatstenen` ->
  Betonikivi, `tegels` -> Betonilaatta, `natuursteen`/`keien` -> Luonnonkivi. Planted verges are left to the
  ortho and vegetation producers. The BGT values stay in `bgt_functie`/`bgt_fysiek`.
- **Water** (`kanta:Vesikohde` <- `bgt-water`): `waterdeel` polygons.
- Layers with no Dutch source yet are empty (`wfs_absent`, reasons in the profile); OSM fills them where the
  generator has an OSM path (stops, bike parking, trees, signs).

## Gaps (next work)

- No `pistepilvi`: roof-plane refinement, LiDAR trees and wires are off. AHN class 1 holds vegetation
  unclassified; a height-above-ground split would feed them.
- BGT `scheiding` (quay walls, fences), `kunstwerkdeel`, `overbruggingsdeel` (bridge decks), `straatmeubilair`,
  `bord` and `vegetatieobject_punt` (register trees) are fetched but not yet mapped.
- Amsterdam's canal water is drawn at the city level (NAP -0.40); polder water elsewhere is not handled.
- The 2025 ortho is not a true ortho, so buildings lean on the ground texture.

## Commands

```sh
tools/cities/setup-root.sh amsterdam                      # data/work roots, raw on /bulk/turku3d/cities
tools/cities/run.sh amsterdam python -m turku3d.nl.fetch amsterdam              # 1 km² sample sources
tools/cities/run.sh amsterdam python -m turku3d.nl.fetch amsterdam --radius 5000 --plan   # size estimate
tools/cities/run.sh amsterdam python -m turku3d.nl.fetch amsterdam --radius 5000          # 5 km cache
tools/cities/run.sh amsterdam python -m turku3d.coarse build t0_1_0 --fetch     # one tile (fetches on demand)
tools/cities/run.sh amsterdam python -m turku3d.nl.check ~/turku3d-local/cities/amsterdam/work/coarse t0_1_0
tools/cities/install-units.sh amsterdam 7980              # tile service :7980 + preview page :7981
```
