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

CITIES: dict[str, City] = {c.name: c for c in (TURKU, ESPOO)}
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
