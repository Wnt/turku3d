# Amsterdam and Rotterdam

The first Dutch cities. Profiles: `pipeline/turku3d/cities/__init__.py` (`AMSTERDAM`, `ROTTERDAM`); one
process builds one city (`TURKU3D_CITY` or the data root's `city.json`), as for Espoo (`docs/cities/espoo.md`).

| | Amsterdam | Rotterdam |
|---|---|---|
| CRS | EPSG:28992 (Amersfoort / RD New) + NAP | same |
| Origin O | E 121000, N 487500 (NW corner of the block holding Dam) | E 93000, N 437500 (block holding the Markthal) |
| Centre spawn | Dam: E 121318 N 487362 (engine x 318, z 138, tile `t0_1_0`); the client starts at the square's east side by the Rokin-Damrak carriageway, E 121345 N 487335 heading 345 (x 345, z 165), so the own car parks beside the player | Binnenrotte at the Markthal arch: E 93075 N 437390, heading 180 (x 75, z 110, `t0_0_0`; the Markthal point itself is inside the building) |
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
- **Street areas** (`GIS:Katualueet` <- `bgt-streets`): `wegdeel`, `ondersteunendwegdeel` and paved
  `onbegroeidterreindeel` polygons with Turku's register vocabulary and codes (`BGT_KIND`): `rijbaan *` ->
  Ajorata, `fietspad` -> Pyörätie, `voetpad` -> Jalkakäytävä, `voetpad op trap` -> Portaat, `parkeervlak` ->
  Muu liikennealue, paved terrain -> Muu. The surface (`appearance`) takes, in order: an explicit BGT appearance
  (`plus_fysiek_voorkomen`, or a specific `fysiek_voorkomen`: `gebakken klinkers` -> Maatiili, `asfalt` ->
  Asfalttibetoni, `betonstraatstenen` -> Betonikivi, `tegels` -> Betonilaatta, `natuursteen` -> Luonnonkivi);
  else the OSM `surface=*` of the area or way on the polygon (`OSM_SURFACE`: sett -> Noppakivi, paving_stones ->
  Betonikivi, bricks -> Maatiili ...); else Dutch convention for generic element paving (clinker brick on
  carriageways, concrete tiles on footways). Asphalt cycle paths are red asphalt. The 2025 ortho is not used
  for this: the shadowed spring flight does not separate brick from grey pavers. Planted verges are left to
  the ortho and vegetation producers. The BGT values stay in `bgt_functie`/`bgt_fysiek`.
- **Water** (`kanta:Vesikohde` <- `bgt-water`): `waterdeel` polygons.
- **Register trees** (`GIS:Puut` <- `bgt-trees`): BGT `vegetatieobject_punt` "boom" (no species or year).
- **Street lights** (`frn:CityFurniture_LOD2` <- `bgt-lamps`): BGT `paal` "lichtmast".
- **Point cloud** (`raw/laz/pistepilvi/<block>.laz`, `turku3d.nl.lidar`): AHN reclassified into Turku's classes
  (class 1 split by height above the block's ground; vehicles on carriageways kept as 1), written with the
  ground block, so roof refinement, LiDAR trees, canopy and walls run as in Turku.
- Layers with no Dutch source yet are empty (`wfs_absent`, reasons in the profile); OSM fills them where the
  generator has an OSM path (stops, bike parking, signs).

Dam t0_1_0 after these rules: 96/96 3DBAG buildings, 52 trees (was 0), 90 street lights, cobblestone ground
on Dam from OSM `surface=sett` (16,800 cells, was 993), roof heights with LiDAR class-6 plane evidence.

## Gaps (next work)

- BGT `scheiding` (quay walls, fences), `kunstwerkdeel`, `overbruggingsdeel` (bridge decks; bridges come from
  OSM), `straatmeubilair` and `bord` are fetched but not mapped.
- Amsterdam's own tree register (species, sizes) would be a better `GIS:Puut` source than BGT.
- Amsterdam's canal water is drawn at the city level (NAP -0.40); polder water elsewhere is not handled.
- The 2025 ortho is not a true ortho, so buildings lean on the ground texture.

## 5 km cache and window preparation

Measured from the home lab on 2026-11-05: AHN GeoTiles ~30 MB/s per stream (~59 MB/s for two cities),
basisdata.nl AHN sheets ~92 MB/s with 4 streams, 3DBAG ~4 MB/s, BGT OGC API request-bound (~2.5 MB/s at
4 threads, ~9 MB/s at 12-16). That is below the lab's 80-100 MB/s upload to UpCloud, so the 5 km datasets are
cached on `/bulk/turku3d/cities/<city>/raw` and staged from there.

| 5 km radius | Amsterdam (Dam) | Rotterdam (Markthal) |
|---|---|---|
| AHN5 sub-tiles | 81 files, 20.5 GB | 81 files, 21.3 GB |
| 3DBAG tiles | 126, 0.17 GB | 85, 0.14 GB |
| BGT (gzip, current objects) | 356 blocks, ~1.5 GB | 357 blocks, ~1.3 GB |
| OSM province extract | 0.19 GB | 0.21 GB |
| Ortho (draft, 49 cm/px) | ~1,420 tiles, ~0.85 GB (8 cm full: ~5.7 GB) | same |
| Converted (ground + point cloud + layers) | ~40 MB + ~35 MB per 500 m block | same |
| Total raw | ~23 GB | ~24 GB |

`nl.prepare CENTRE --rings R` fetches and converts every input of rings 0..R (plus the seam ring's ground),
so builds of those tiles need no network; `--shard K/N` splits it by block across processes.

## Commands

```sh
tools/cities/setup-root.sh amsterdam                      # data/work roots, raw on /bulk/turku3d/cities
tools/cities/run.sh amsterdam python -m turku3d.nl.fetch amsterdam              # 1 km² sample sources
tools/cities/run.sh amsterdam python -m turku3d.nl.fetch amsterdam --radius 5000 --plan   # size estimate
tools/cities/run.sh amsterdam python -m turku3d.nl.fetch amsterdam --radius 5000          # 5 km cache
tools/cities/run.sh amsterdam python -m turku3d.nl.prepare t0_1_0 --rings 3 --shard 0/3   # (x3 shards)
tools/cities/run.sh amsterdam python -m turku3d.nl.convert rederive            # after an adapter change
tools/cities/run.sh amsterdam python -m turku3d.coarse build t0_1_0             # offline once prepared
tools/cities/run.sh amsterdam python -m turku3d.nl.check ~/turku3d-local/cities/amsterdam/work/coarse t0_1_0
tools/cities/install-units.sh amsterdam 7980              # tile service :7980 + preview page :7981
```
