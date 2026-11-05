"""City profiles: everything that differs between the cities the generator can build.

One process builds one city. ``current()`` picks the profile from ``TURKU3D_CITY`` or, when that is
unset, from ``$TURKU3D_DATA/city.json`` (``{"city": "espoo"}``); Turku is the default. A data root
holds exactly one city's raw sources, so a mismatch between the two is an error rather than a
silently mixed world.

The pipeline keeps Turku's dataset names as its internal schema (``bldg:Building_LOD2``,
``GIS:Katualueet`` with ``Katuosalaji``/``Pintamateriaali`` ...). A profile maps each internal WFS
layer to the city's own layer (``wfs_names``), marks layers the city does not serve openly
(``wfs_absent``: stored as an empty layer, so a build does not wait on them; OSM fills the gap
where the generator has an OSM path) and derives layers from other city layers (``wfs_derived``,
see ``fetch/adapters.py``). The gap table in ``docs/cities/espoo.md`` lists how each gap is filled.

Ground comes from the city lidar ``maanpinta`` key points (Turku) or from the National Land Survey
2 m DEM resampled into the same ``raw/laz/maanpinta/<block>.laz`` key-point files (``mml-dem2m``),
so the ground, water and collision producers read one format.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

MML_FUNET = "https://mirrors.nic.funet.fi/index/geodata/mml"
GEOFABRIK_FINLAND = "https://download.geofabrik.de/europe/finland-latest.osm.pbf"


@dataclass(frozen=True)
class City:
    name: str
    title: str
    crs_horizontal: str  # GK zone of the city's own data (N2000 heights everywhere in Finland)
    gk_meridian: float  # central meridian of that GK zone (the client's inverse projection)
    origin_e: float  # world origin O: NW corner of one 500 m block, so the grids nest
    origin_n: float
    spawn_e: float  # default player spawn and the generator's prewarm centre
    spawn_n: float
    spawn_latlon: tuple[float, float]
    spawn_label: str
    work_3x3: tuple[float, float, float, float]  # (emin, nmin, emax, nmax) shared input reservoir
    wfs_url: str
    wms_url: str
    wms_referer: str
    ortho_layer: str  # WMS layer of the draft ortho
    ortho_slug: str  # raw/ortho/<slug>
    ground: str  # "city-laz" (maanpinta key points) or "mml-dem2m"
    laz_url: str | None = None  # city lidar tiles; None when the city has no open lidar
    laz_index: str | None = None  # data-manifests/<csv> listing the city lidar tiles
    osm_url: str = GEOFABRIK_FINLAND
    wfs_names: dict[str, str] = field(default_factory=dict)  # internal -> city layer
    wfs_absent: dict[str, str] = field(default_factory=dict)  # internal -> why it is empty
    wfs_derived: dict[str, str] = field(default_factory=dict)  # internal -> adapter name
    water_level: float = 0.0  # m in the city's height datum: open sea/river surface (N2000 0 m in Finland)
    datasets: tuple[dict, ...] = ()  # sources manifest rows (licence, date) of a non-default city
    # Appended for the Dutch cities (turku3d.nl); defaults keep the Finnish profiles unchanged.
    projection: str = "gk"  # client inverse projection: "gk" (gk_meridian) or "rd" (EPSG:28992)
    sample_bbox: tuple[float, float, float, float] | None = None  # first playable area (E/N)

    def remote_layer(self, name: str) -> str:
        return self.wfs_names.get(name, name)


TURKU = City(
    name="turku",
    title="Turku",
    crs_horizontal="EPSG:3877",  # ETRS89 / GK23FIN
    gk_meridian=23.0,
    origin_e=23459500.0,
    origin_n=6704500.0,
    # Office: Läntinen Rantakatu 15 (ARCHITECTURE.md D3, research/ai-textures.md).
    spawn_e=23459668.0,
    spawn_n=6704254.0,
    spawn_latlon=(60.44839, 22.26726),
    spawn_label="Läntinen Rantakatu 15",
    work_3x3=(23459000, 6703500, 23460500, 6705000),
    wfs_url="https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx",
    wms_url="https://turku.asiointi.fi/TeklaOGCWeb/WMS.ashx",
    wms_referer="https://turku.asiointi.fi/IMS/fi",
    ortho_layer="Ilmakuva 2022 True ortho",
    ortho_slug="ortho2022to",
    ground="city-laz",
    laz_url="https://turku.asiointi.fi/3d/{dataset}/{name}.laz",
    laz_index="laz-tiles.csv",
)

ESPOO_WFS = "https://kartat.espoo.fi/teklaogcweb/wfs.ashx"
ESPOO = City(
    name="espoo",
    title="Espoo",
    crs_horizontal="EPSG:3879",  # ETRS89 / GK25FIN, the WFS DefaultSRS of every Espoo layer
    gk_meridian=25.0,
    # O = NW corner of block 25489000_6674000: the Tapiola sample is t0_0_0 .. t0_3_3.
    origin_e=25489000.0,
    origin_n=6674000.0,
    # Tapiontori, the pedestrian square at Tapiola centre (west of the Keskusallas pond).
    spawn_e=25489150.0,
    spawn_n=6673640.0,
    spawn_latlon=(60.17572, 24.80430),
    spawn_label="Tapiontori, Tapiola",
    work_3x3=(25488500, 6672500, 25490000, 6674000),
    wfs_url=ESPOO_WFS,
    wms_url="https://kartat.espoo.fi/teklaogcweb/wms.ashx",
    wms_referer="https://kartat.espoo.fi/IMS/fi",
    ortho_layer="Ortokartta_2024",
    ortho_slug="ortho2024",
    ground="mml-dem2m",
    wfs_names={
        "bldg:Building_LOD2": "bldg:building_lod2",
        "bldg:Building_LOD3": "bldg:building_lod3",
        "frn:CityFurniture_LOD2": "frn:cityfurniture_lod2",
        "tran:Road_LOD3": "tran:road_lod2",
        "veg:PlantCover_LOD2": "veg:plantcover_lod2",
    },
    wfs_absent={
        "GIS:Liikennemerkit": "401 on 2026-11-05; signs come from OSM traffic_sign tags",
        "GIS:Viheralueet": "401 on 2026-11-05; green areas come from OSM landuse/leisure and the ortho",
        "kanta:Pysakki": "not offered; bus stops come from OSM highway=bus_stop",
        "GIS:Varusteet": "not offered (GIS:InfEquipment is the Espoo register, no adapter yet); OSM furniture",
        "GIS:Puut": "not offered; OSM natural=tree and kanta:Lehtipuu are the tree sources",
        "GIS:Polkupyoraparkki": "not offered; OSM amenity=bicycle_parking",
        "GIS:Pysakointiautomaatit": "not offered; OSM vending=parking_tickets",
        "GIS:Follariasemat": "not offered (Espoo city bikes are HSL's); OSM bicycle_rental",
        "GIS:Lupapysakointipaikat": "not offered; no permit-parking kerbs",
    },
    wfs_derived={
        "GIS:Katualueet": "infstreet",  # 401 on 2026-11-05; GIS:InfStreet has the same street parts
    },
    datasets=(
        dict(
            name="Espoo WFS (Tekla)",
            url=ESPOO_WFS,
            layers="bldg:building_lod2, GIS:Rakennukset, kanta:Rakennus, GIS:InfStreet, GIS:Keskilinjat, "
            "kanta:Liikennevayla, kanta:Kevyenliikenteenvayla, kanta:Rakenne, kanta:Vesikohde, "
            "kanta:Korkeuspiste",
            date="fetched 2026-11-05 (live register)",
            licence="CC BY 4.0, (c) Espoon kaupunki",
            licence_source="avoindata.suomi.fi, Espoon kaupungin paikkatietoaineistot (checked 2026-11-03)",
        ),
        dict(
            name="Espoo orthophoto",
            url="https://kartat.espoo.fi/teklaogcweb/wms.ashx",
            layers="Ortokartta_2024",
            date="2024, 30 cm native; requested at 49 cm/px",
            licence="CC BY 4.0, (c) Espoon kaupunki",
            licence_source="kartat.espoo.fi/opendata (checked 2026-11-03)",
        ),
        dict(
            name="MML 2 m DEM (Korkeusmalli 2 m)",
            url=f"{MML_FUNET}/dem2m/2008_latest/",
            layers="dem2m_direct.vrt sheets, EPSG:3067 + N2000",
            date="mirror 2026-11-01; sheet L4131H modified 2025-02-09",
            licence="CC BY 4.0, (c) Maanmittauslaitos",
            licence_source="maanmittauslaitos.fi avoin data licence CC BY 4.0 (checked 2026-11-03)",
        ),
        dict(
            name="MML laser scanning 0.5 p",
            url=f"{MML_FUNET}/laserkeilaus/2008_latest/",
            layers="2008_latest.shp index; Tapiola sheet L4131H3 is the 2008 scan",
            date="2008 scan (the newest 0.5 p over Espoo); index 2026-06-01",
            licence="CC BY 4.0, (c) Maanmittauslaitos",
            licence_source="maanmittauslaitos.fi avoin data licence CC BY 4.0 (checked 2026-11-03)",
        ),
        dict(
            name="OpenStreetMap",
            url=GEOFABRIK_FINLAND,
            layers="Geofabrik Finland snapshot, osmium clip per 2 km square",
            date="dated snapshot, see raw/osm/MANIFEST.jsonl",
            licence="ODbL 1.0 (c) OpenStreetMap contributors",
            licence_source="openstreetmap.org/copyright",
        ),
    ),
)

# -- Netherlands (turku3d.nl): RD New + NAP. Ground, buildings, street surfaces and water are converted
# from AHN, 3DBAG and BGT into the internal raw layout by ``python -m turku3d.nl.convert``.
PDOK_WMS = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
NL_DATASETS = (
    dict(
        name="AHN point cloud (GeoTiles 1 x 1.25 km sub-tiles)",
        url="https://geotiles.citg.tudelft.nl/AHN5_T/",
        layers="AHN5 (2023 flight over Amsterdam/Rotterdam), AHN4 where AHN5 is missing",
        date="AHN5 2023; fetched 2026-11-05",
        licence="CC0 1.0, Rijkswaterstaat / Het Waterschapshuis; tiling GeoTiles (TU Delft)",
        licence_source="ahn.nl open data; geotiles.citg.tudelft.nl (checked 2026-11-05)",
    ),
    dict(
        name="3DBAG",
        url="https://data.3dbag.nl/v20250903/tile_index.fgb",
        layers="CityJSON tiles, LoD2.2 BuildingPart solids with semantic surfaces",
        date="release v2025.09.03",
        licence="CC BY 4.0, 3DBAG by tudelft3d and 3DGI",
        licence_source="docs.3dbag.nl/en/copyright (checked 2026-11-05)",
    ),
    dict(
        name="BGT (Basisregistratie Grootschalige Topografie)",
        url="https://api.pdok.nl/lv/bgt/ogc/v1",
        layers="all 49 collections; wegdeel/ondersteunendwegdeel -> street areas, waterdeel -> water",
        date="live register, fetched 2026-11-05",
        licence="CC0 1.0, Kadaster / PDOK",
        licence_source="pdok.nl BGT OGC API (checked 2026-11-03)",
    ),
    dict(
        name="PDOK Luchtfoto RGB",
        url=PDOK_WMS,
        layers="2025_orthoHR (8 cm national orthophoto), requested at 49 cm/px for the draft ortho",
        date="2025 flight",
        licence="CC BY 4.0, Beeldmateriaal Nederland",
        licence_source="pdok.nl/introductie/-/article/pdok-luchtfoto-rgb-open- (checked 2026-11-03)",
    ),
)


def _nl(name, title, origin, spawn, latlon, label, osm, sample, water):
    e, n = origin
    return City(
        name=name,
        title=title,
        crs_horizontal="EPSG:28992",  # Amersfoort / RD New, NAP heights
        gk_meridian=0.0,
        origin_e=e,
        origin_n=n,
        spawn_e=spawn[0],
        spawn_n=spawn[1],
        spawn_latlon=latlon,
        spawn_label=label,
        work_3x3=(e - 500, n - 1000, e + 1000, n + 500),
        wfs_url="",
        wms_url=PDOK_WMS,
        wms_referer="",
        ortho_layer="2025_orthoHR",
        ortho_slug="ortho2025hr",
        ground="nl-ahn",
        osm_url=osm,
        wfs_derived={
            "bldg:Building_LOD2": "3dbag",
            "GIS:Katualueet": "bgt-streets",
            "kanta:Vesikohde": "bgt-water",
        },
        wfs_absent={
            "GIS:Liikennemerkit": "BGT bord has no sign codes yet; OSM traffic_sign tags",
            "kanta:Pysakki": "OSM highway=bus_stop / railway=tram_stop",
            "GIS:Keskilinjat": "street graph from OSM",
            "GIS:Varusteet": "BGT straatmeubilair not mapped yet",
            "frn:CityFurniture_LOD2": "no 3D furniture register",
            "GIS:Polkupyoraparkki": "OSM amenity=bicycle_parking",
            "GIS:Pysakointiautomaatit": "OSM vending=parking_tickets",
            "GIS:Follariasemat": "OSM bicycle_rental",
            "GIS:Puut": "BGT vegetatieobject_punt not mapped yet; OSM natural=tree",
            "kanta:Korkeuspiste": "AHN ground points are the height source",
            "kanta:Rakenne": "BGT scheiding/kunstwerkdeel not mapped yet",
            "GIS:Lupapysakointipaikat": "no permit-parking kerbs",
        },
        datasets=NL_DATASETS
        + (
            dict(
                name="OpenStreetMap",
                url=osm,
                layers="Geofabrik province extract, osmium clip per 2 km square",
                date="dated snapshot, see raw/osm",
                licence="ODbL 1.0 (c) OpenStreetMap contributors",
                licence_source="openstreetmap.org/copyright",
            ),
        ),
        projection="rd",
        water_level=water,
        sample_bbox=sample,
    )


# Amsterdam: O = NW corner of the block holding Dam square; the sample is the Grachtengordel-West
# between Singel and Prinsengracht with Dam and the Westerkerk (t0_-2_-2 .. t0_1_1).
AMSTERDAM = _nl(
    "amsterdam", "Amsterdam", (121000.0, 487500.0), (121318.0, 487362.0), (52.37310, 4.89260),
    "Dam", "https://download.geofabrik.de/europe/netherlands/noord-holland-latest.osm.pbf",
    (120500.0, 487000.0, 121500.0, 488000.0),
    -0.40,  # NAP: the Amsterdam canal level (stadspeil)
)
# Rotterdam: O = NW corner of the block holding the Markthal; the sample holds Markthal, Blaak,
# Oude Haven, Leuvehaven and the Maas quays at the north end of the Erasmusbrug.
ROTTERDAM = _nl(
    # Spawn on the Binnenrotte square at the Markthal's north arch (the Markthal point itself is inside it).
    "rotterdam", "Rotterdam", (93000.0, 437500.0), (93075.0, 437390.0), (51.92154, 4.48701),
    "Binnenrotte (Markthal)", "https://download.geofabrik.de/europe/netherlands/zuid-holland-latest.osm.pbf",
    (92500.0, 436500.0, 93500.0, 437500.0),
    0.0,  # NAP: tidal Nieuwe Maas, about mean level
)

CITIES: dict[str, City] = {c.name: c for c in (TURKU, ESPOO, AMSTERDAM, ROTTERDAM)}
DEFAULT = TURKU.name
MARKER = "city.json"


def _data_root() -> Path:
    return Path(os.environ.get("TURKU3D_DATA", "~/turku3d/data")).expanduser()


def marked(root: Path | None = None) -> str | None:
    """The city a data root is marked for, or None for an unmarked (Turku) root."""
    path = (root or _data_root()) / MARKER
    try:
        return json.loads(path.read_text())["city"]
    except FileNotFoundError:
        return None


@cache
def current() -> City:
    wanted = os.environ.get("TURKU3D_CITY") or None
    root = marked()
    name = wanted or root or DEFAULT
    if name not in CITIES:
        raise ValueError(f"unknown city {name!r}; known: {', '.join(CITIES)}")
    if (root or DEFAULT) != name:
        raise ValueError(
            f"TURKU3D_CITY={name} but the data root {_data_root()} holds {root or DEFAULT}; "
            "give each city its own TURKU3D_DATA"
        )
    return CITIES[name]
