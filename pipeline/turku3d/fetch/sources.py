"""Data sources: endpoints, layers, years and notes (ARCHITECTURE.md D2, research/turku-data.md).

`python -m turku3d.fetch.sources` prints data-manifests/sources.yaml (generated; a test checks it is current).
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import cities

# The current city's endpoints (turku3d.cities); the layer table below is the internal schema.
CITY = cities.require_applied()
WFS_URL = CITY.wfs_url
WMS_URL = CITY.wms_url
LAZ_URL = CITY.laz_url or ""
GEOFABRIK_FINLAND = CITY.osm_url

# The server returns at most this many features per GetFeature (verified 2026-10-25: 1000 at any bbox
# size), and WFS 1.0.0 has no startIndex. Paging is therefore spatial: split the bbox until a page < cap.
WFS_FEATURE_CAP = 1000
WMS_MAX_PX = 4096


@dataclass(frozen=True)
class WfsLayer:
    name: str
    version: str  # "1.0.0" (Tekla GML2, <Filter><BBOX>) or "1.1.0" (CityGML, bbox=)
    role: str
    expect_401: bool = False

    @property
    def slug(self) -> str:
        return self.name.replace(":", "_")

    @property
    def remote(self) -> str:
        """The current city's name for this layer."""
        return CITY.remote_layer(self.name)


def _k(name, role, **kw):
    return WfsLayer(name, "1.0.0", role, **kw)


def _c(name, role):
    return WfsLayer(name, "1.1.0", role)


WFS_LAYERS: tuple[WfsLayer, ...] = (
    # kantakartta (base map)
    _k("kanta:Liikennevayla", "62520 curb / carriageway edge lines (also quay edges)"),
    _k("kanta:Kevyenliikenteenvayla", "62522/62523 sidewalk and cycleway edges"),
    _k(
        "kanta:Rakenne",
        "stairs 66626, step edges 66618, retaining-wall top 66634, fences, ramps 62540",
    ),
    _k("kanta:Rakennus", "building footprints 66000/66001 + storeys, facade material"),
    _k("kanta:Erityisrakenne", "special structures"),
    _k("kanta:Korkeuspiste", "spot heights (digitised; not an independent accuracy check)"),
    _k("kanta:Korkeuskayra", "contours"),
    _k("kanta:Jyrkanne", "cliffs"),
    _k("kanta:Kivi", "boulders"),
    _k("kanta:Vesikohde", "water"),
    _k("kanta:Raide", "rails"),
    _k("kanta:Lehtipuu", "deciduous trees"),
    _k("kanta:Havupuu", "conifers"),
    _k("kanta:Lehtipuurivi", "tree rows"),
    _k("kanta:Pensas", "bushes"),
    _k("kanta:Pensasaita", "hedges"),
    _k("kanta:Kasviryhma", "plant groups"),
    _k("kanta:Viherkaista", "green strips"),
    _k("kanta:Pysakki", "stops"),
    _k("kanta:Porttaali", "portals / gantries"),
    _k("kanta:Polku", "paths"),
    _k("kanta:LuokittelematonViiva", "unclassified lines (401 on 2026-10-25)", expect_401=True),
    _k(
        "kanta:Sahkoverkonosa",
        "electricity network incl. lamp posts (401 on 2026-10-25)",
        expect_401=True,
    ),
    # street registers
    _k("GIS:Katualueet", "street-part polygons: Katuosalaji (incl. Portaat), Pintamateriaali"),
    _k("GIS:Keskilinjat", "street centrelines per street part (road graph)"),
    _k("GIS:Varusteet", "street furniture points (model, manufacturer)"),
    _k("GIS:Puut", "tree register: species, planting year"),
    _k("GIS:Liikennemerkit", "traffic signs with Finnish codes"),
    _k("GIS:Rakennukset", "building register"),
    _k("GIS:Osoitteet", "address points"),
    _k("GIS:Nopeusrajoitusalueet", "speed limit areas"),
    _k("GIS:Raitiotielinjaus", "tram alignment"),
    _k("GIS:Viheralueet", "green areas"),
    _k("GIS:Polkupyoraparkki", "bike parking"),
    _k("GIS:Pysakointiautomaatit", "parking meters"),
    _k("GIS:Lupapysakointipaikat", "permit parking kerb lines with payment zone"),
    _k("GIS:Follariasemat", "city bike stations"),
    # CityGML 2.0
    _c("bldg:Building_LOD2", "CityGML LoD2 building solids (roofs vs lidar median +0.8 cm)"),
    _c("bldg:Building_LOD3", "CityGML LoD3 (finer roofs, no facade detail)"),
    _c("tran:Road_LOD3", "draped road TIN with katuosalaji (semantics only, too coarse for curbs)"),
    _c("veg:PlantCover_LOD2", "park TIN areas"),
    _c(
        "frn:CityFurniture_LOD2",
        "street-light poles: N2000 base points; Tasonimi rr:js / Kiilai:uv-pylväs (both registers, ~1 m apart)",
    ),
)


@dataclass(frozen=True)
class WmsLayer:
    name: str
    slug: str
    px_m: float  # request resolution (<= native, so no loss)
    native_cm: float  # measured in research (turku-data.md §2.3 + verification)
    year: str
    compress: str  # GeoTIFF compression
    note: str


TURKU_WMS_LAYERS: tuple[WmsLayer, ...] = (
    WmsLayer(
        "Ilmakuva 2022 True ortho",
        "ortho2022to",
        0.05,
        5.5,
        "2022",
        "DEFLATE",
        "true ortho, no building lean: primary roof/ground texture",
    ),
    WmsLayer("Ilmakuva 2025", "ortho2025", 0.05, 5.2, "2025", "DEFLATE", "most recent"),
    WmsLayer(
        "Turun lennokkikuvaukset",
        "drone",
        0.0125,
        1.2,
        "mixed",
        "JPEG",
        "drone mosaic, patchy coverage, mixed dates (some winter)",
    ),
)


ESPOO_WMS_LAYERS: tuple[WmsLayer, ...] = (
    WmsLayer(
        "Ortokartta_2024",
        "ortho2024",
        0.30,
        30.0,
        "2024",
        "DEFLATE",
        "Espoo orthophoto (open data page: 30 cm); the draft ortho",
    ),
)
# PDOK Luchtfoto RGB (CC BY 4.0): the 2025 national 8 cm orthophoto, in RD New.
NL_WMS_LAYERS: tuple[WmsLayer, ...] = (
    WmsLayer(
        "2025_orthoHR", "ortho2025hr", 0.08, 8.0, "2025", "DEFLATE",
        "PDOK national 8 cm orthophoto (not true ortho: buildings lean); the draft ortho",
    ),
)
CITY_WMS_LAYERS = {
    "turku": TURKU_WMS_LAYERS,
    "espoo": ESPOO_WMS_LAYERS,
    "amsterdam": NL_WMS_LAYERS,
    "rotterdam": NL_WMS_LAYERS,
}
# Second imagery source when the draft ortho is blank over a tile (Turku: the 2025 flight).
FALLBACK_WMS = {"turku": "ortho2025"}.get(CITY.name)
WMS_LAYERS: tuple[WmsLayer, ...] = CITY_WMS_LAYERS[CITY.name]


def sources_yaml() -> str:
    def q(s: str) -> str:
        return '"' + s.replace('"', '\\"') + '"'

    turku = cities.TURKU
    out = [
        "# GENERATED by `python -m turku3d.fetch.sources` -- do not edit; edit pipeline/turku3d/fetch/sources.py",
        "crs: EPSG:3877+3900",
        "laz:",
        f"  url: {q(turku.laz_url)}",
        "  datasets: [pistepilvi, maanpinta]",
        "  index: data-manifests/laz-tiles.csv",
        "  epoch: 2021-04-17",
        '  notes: "use curl -L (opaskartta host 301-redirects); no CRS VLR, EPSG:3877 assumed; 130-150 MB/tile"',
        '  licence: "CC BY 4.0, (c) Turun kaupunki [V]"',
        '  licence_source: "avoindata.suomi.fi, Turun kaupungin kaupunkitietomalli (checked 2026-11-03)"',
        "wfs:",
        f"  url: {q(turku.wfs_url)}",
        "  format: GML only (no JSON)",
        f"  feature_cap_per_request: {WFS_FEATURE_CAP}",
        '  paging: "spatial quadtree split until a page has < cap features; dedupe by gml:id or content hash"',
        "  layers:",
    ]
    for L in WFS_LAYERS:
        out.append(
            f"    - {{name: {q(L.name)}, version: {q(L.version)}, role: {q(L.role)}"
            + (", expect_401: true" if L.expect_401 else "")
            + "}"
        )
    out += [
        "wms:",
        f"  url: {q(turku.wms_url)}",
        f"  max_px: {WMS_MAX_PX}",
        "  layers:",
    ]
    for W in TURKU_WMS_LAYERS:
        out.append(
            f"    - {{name: {q(W.name)}, slug: {W.slug}, request_px_m: {W.px_m}, native_cm: {W.native_cm}, "
            f"year: {q(W.year)}, compress: {W.compress}, note: {q(W.note)}}}"
        )
    out += [
        "osm:",
        f"  url: {q(turku.osm_url)}",
        '  notes: "latest redirects to a dated file; md5 checked; clipped with osmium extract --strategy smart"',
        '  licence: "ODbL 1.0 (c) OpenStreetMap contributors"',
        "# Other cities (turku3d.cities; one TURKU3D_DATA root per city). Internal layer -> city layer,",
        "# layers the city does not serve (empty, filled from OSM) and layers derived by fetch/adapters.py.",
        "cities:",
    ]
    for city in cities.CITIES.values():
        if city is turku:
            continue
        out += [
            f"  {city.name}:",
            f"    crs: {city.crs_horizontal}+3900",
            f"    origin_en: [{city.origin_e}, {city.origin_n}, 0.0]",
            f"    ground: {city.ground}",
            f"    ortho: {{layer: {q(city.ortho_layer)}, slug: {city.ortho_slug}}}",
            "    wfs_names:",
            *(f"      {q(k)}: {q(v)}" for k, v in city.wfs_names.items()),
            "    wfs_absent:",
            *(f"      {q(k)}: {q(v)}" for k, v in city.wfs_absent.items()),
            "    wfs_derived:",
            *(f"      {q(k)}: {q(v)}" for k, v in city.wfs_derived.items()),
            "    datasets:",
        ]
        for d in city.datasets:
            out.append("      - {" + ", ".join(f"{k}: {q(v)}" for k, v in d.items()) + "}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(sources_yaml(), end="")
