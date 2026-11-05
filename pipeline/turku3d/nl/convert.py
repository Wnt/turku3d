"""Dutch sources -> the generator's internal raw layout (one city per TURKU3D_DATA root).

The generator reads Turku's dataset shapes as its internal schema (``turku3d.cities``). For a city
with ``ground == "nl-ahn"`` this module produces them from the native downloads of
``turku3d.nl.fetch`` (under ``raw/nl``), fetching what a unit needs on demand:

* ``raw/laz/maanpinta/<block>.laz``: ground key points of one 500 m block. AHN ground (class 2) is
  thinned to the lowest return per ``GROUND_CELL``; AHN water returns (class 9) are kept per
  ``WATER_CELL``; BGT water areas without returns get points at the city's ``water_level``, so the
  canals and harbours read as water in the ground and water-level producers. Every point is class 8
  (model key point), as in the city ground model the producers were written for.
* ``bldg:Building_LOD2`` from 3DBAG LoD2.2 BuildingPart solids: one row per semantic surface
  (RoofSurface/WallSurface/GroundSurface) in RD New + NAP, with ``storeysAboveGround``,
  ``yearOfConstruction`` and ``measuredHeight`` from the 3DBAG/BAG attributes.
* ``GIS:Katualueet`` from BGT ``wegdeel`` and ``ondersteunendwegdeel``: ``Katuosalaji`` from the
  BGT function and ``Pintamateriaali`` from the physical appearance, written as the tokens
  register vocabulary and codes the street producers read (``BGT_KIND``, ``BGT_SURFACE``); the BGT
  values stay in ``bgt_functie``/``bgt_fysiek``.
* ``kanta:Vesikohde`` from BGT ``waterdeel``.

Only current BGT objects (no ``eind_registratie``) are used.
"""

from __future__ import annotations

from turku3d import cities
import gzip
import json
from pathlib import Path

import numpy as np

from turku3d import config as C

GROUND_CELL = 1.5  # m: Turku's key-point files hold ~0.3 points/m2; this keeps ~0.44
WATER_CELL = 4.0
KEY_POINT_CLASS = 8
STREET_COLLECTIONS = ("wegdeel", "ondersteunendwegdeel")
WATER_COLLECTIONS = ("waterdeel",)

# BGT functie -> internal street part (Katuosalaji, Katuosalaji_koodi), Turku's register vocabulary
# (the street, kerb, parking and material producers key on these; see fetch/adapters.py for Espoo).
BGT_KIND = {
    "fietspad": ("Pyörätie", "3"),
    "voetpad": ("Jalkakäytävä", "2"),
    "voetpad op trap": ("Portaat", "8"),
    "voetgangersgebied": ("Kävelytie", "16"),
    "ruiterpad": ("Kävelytie", "16"),
    "verkeerseiland": ("Välikaista", "13"),
    "berm": ("Välikaista", "13"),
    "woonerf": ("Ajorata", "1"),
    "inrit": ("Tonttiliittymä", "7"),
    "parkeervlak": ("Muu liikennealue", "12"),
    "overweg": ("Tasoristeys", "11"),
    "OV-baan": ("Joukkoliikennekatu", "24"),
    "transitie": ("Ajorata", "1"),
    "spoorbaan": ("Muu", "6"),
}
# BGT (plus_)fysiek_voorkomen -> (Pintamateriaali, Pintamateriaali_koodi); first match wins, the
# detailed plus value before the basic one.
BGT_SURFACE = (
    ("gebakken klinkers", ("Maatiili", "21")),  # red clinker brick: street_materials.BRICK
    ("asfalt", ("Asfalttibetoni", "1")),
    ("cementbeton", ("Betoni", "14")),
    ("betonstraatstenen", ("Betonikivi", "9")),
    ("sierbestrating", ("Betonikivi", "9")),
    ("grasbetontegels", ("Betonilaatta", "8")),
    ("tegels", ("Betonilaatta", "8")),
    ("betonplaten", ("Betonilaatta", "8")),
    ("natuursteen", ("Luonnonkivi", "011")),
    ("keien", ("Luonnonkivi", "011")),
    ("hout", ("Puu", "13")),
    ("schelpen", ("Sora", "7")),
    ("grind", ("Sora", "7")),
    ("gravel", ("Sora", "7")),
    ("half verhard", ("Sora", "7")),
    ("onverhard", ("Sora", "7")),
    ("gesloten verharding", ("Asfalttibetoni", "1")),
    ("open verharding", ("Betonikivi", "9")),
)
GREEN = ("groenvoorziening", "gras- en kruidachtigen", "struiken", "bodembedekkers", "heesters")


def native_root() -> Path:
    return C.raw_dir("nl")


class _locked:
    """An exclusive file lock: parallel builds wanting the same unit convert it once."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "w")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        self.handle.close()


def kind(functie: str | None) -> tuple[str, str]:
    f = functie or ""
    if f.startswith("rijbaan"):
        return ("Ajorata", "1")
    return BGT_KIND.get(f, ("Muu", "6"))


def surface(fysiek: str | None, plus: str | None) -> tuple[str, str]:
    for value in (plus, fysiek):
        v = (value or "").casefold()
        for token, found in BGT_SURFACE:
            if token in v:
                return found
    return ("Ei tietoa", "0")


# -- native sources ----------------------------------------------------------------------------------


def _ledger() -> dict:
    path = native_root() / "SOURCES.json"
    return json.loads(path.read_text())["files"] if path.exists() else {}


def _ensure(datasets, bbox, log):
    """Fetch the native files covering an RD bbox that are not on disk yet."""
    from . import fetch as F

    root = native_root()
    root.mkdir(parents=True, exist_ok=True)
    area = F.Area(bbox)
    ledger = F.Ledger(root)
    origin = (C.ORIGIN_E, C.ORIGIN_N)
    if "ahn" in datasets:
        F.fetch_ahn(area, root, ledger, False, log)
    if "3dbag" in datasets:
        F.fetch_3dbag(area, root, ledger, False, log)
    if "bgt" in datasets:
        F.fetch_bgt(area, root, ledger, False, log, origin)


def _files(dataset, bbox, collection=None):
    x0, y0, x1, y1 = bbox
    root = native_root()
    out = []
    for rel, rec in sorted(_ledger().items()):
        if rec["dataset"] != dataset or (collection and rec.get("collection") != collection):
            continue
        b = rec.get("bbox_rd")
        if b and b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0:
            out.append(root / rel)
    return out


def bgt_features(collections, bbox, log=print):
    """Current BGT features of the collections intersecting an RD bbox, deduplicated by lokaal_id."""
    import shapely
    from shapely.geometry import shape

    _ensure(("bgt",), bbox, log)
    window = shapely.box(*bbox)
    seen, out = set(), []
    for collection in collections:
        for path in _files("bgt", bbox, collection):
            for f in json.loads(gzip.open(path).read() if path.suffix == ".gz" else path.read_bytes())["features"]:
                p = f["properties"]
                key = p.get("lokaal_id") or f.get("id")
                if key in seen or p.get("eind_registratie") or not f.get("geometry"):
                    continue
                g = shape(f["geometry"])
                if not g.intersects(window):
                    continue
                seen.add(key)
                out.append((collection, key, p, g))
    return out


# -- ground key points -------------------------------------------------------------------------------


def _lowest_per_cell(xyz, cell):
    if not len(xyz):
        return xyz
    ij = np.floor(xyz[:, :2] / cell).astype(np.int64)
    key = ij[:, 0] * 10_000_000 + ij[:, 1]
    order = np.lexsort((xyz[:, 2], key))
    key = key[order]
    first = np.ones(len(key), bool)
    first[1:] = key[1:] != key[:-1]
    return xyz[order[first]]


def subtile_points(path: Path):
    """(ground, water) points of one AHN sub-tile on the absolute GROUND_CELL / WATER_CELL grids,
    decoded once and kept next to the sub-tile (``<name>.keypoints.npz``); blocks crop from it."""
    import laspy

    cache = path.with_name(f"{path.stem}.keypoints-{GROUND_CELL}-{WATER_CELL}.npz")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        with np.load(cache) as z:
            return z["ground"], z["water"]
    ground, water = [], []
    with laspy.open(path) as reader:
        for chunk in reader.chunk_iterator(4_000_000):
            cls = np.asarray(chunk.classification)
            xyz = np.column_stack([np.asarray(chunk.x), np.asarray(chunk.y), np.asarray(chunk.z)])
            ground.append(_lowest_per_cell(xyz[cls == 2], GROUND_CELL))
            water.append(_lowest_per_cell(xyz[cls == 9], WATER_CELL))
    ground = _lowest_per_cell(np.concatenate(ground), GROUND_CELL)
    water = _lowest_per_cell(np.concatenate(water), WATER_CELL)
    tmp = cache.with_name(cache.name + ".tmp.npz")
    np.savez(tmp, ground=ground, water=water)
    tmp.replace(cache)
    return ground, water


def ground_block(block: C.LazTile, log=print) -> Path:
    """Write ``raw/laz/maanpinta/<block>.laz`` from AHN (+ BGT water) and record it (once, under a lock)."""
    from turku3d.coarse.sources import Sources

    with _locked(native_root() / "locks" / f"ground-{block.name}.lock"):
        if Sources().lidar_present(block):
            return C.raw_dir("laz") / "maanpinta" / f"{block.name}.laz"
        return _ground_block(block, log)


def _ground_block(block: C.LazTile, log=print) -> Path:
    import laspy
    import shapely

    from turku3d.fetch.common import Registry

    b = block.bbox_en
    bbox = (b.emin, b.nmin, b.emax, b.nmax)
    _ensure(("ahn",), bbox, log)
    sources = _files("ahn", bbox)
    if not sources:
        from turku3d.coarse.sources import NoCoverage

        raise NoCoverage(f"{block.name}: no AHN sub-tile")
    ground, water = [], []
    for path in sources:
        g, w = subtile_points(path)
        for xyz, out in ((g, ground), (w, water)):
            keep = (xyz[:, 0] >= b.emin) & (xyz[:, 0] < b.emax) & (xyz[:, 1] > b.nmin) & (xyz[:, 1] <= b.nmax)
            out.append(xyz[keep])
    ground = _lowest_per_cell(np.concatenate(ground), GROUND_CELL)
    water = _lowest_per_cell(np.concatenate(water), WATER_CELL)
    # Open water without returns: key points at the city's water level on a WATER_CELL grid.
    polys = [g for _, _, _, g in bgt_features(WATER_COLLECTIONS, bbox, log)]
    synthetic = np.empty((0, 3))
    if polys:
        wet = shapely.intersection(shapely.union_all(polys), shapely.box(*bbox))
        gx, gy = np.meshgrid(
            np.arange(b.emin + WATER_CELL / 2, b.emax, WATER_CELL),
            np.arange(b.nmin + WATER_CELL / 2, b.nmax, WATER_CELL),
        )
        gx, gy = gx.ravel(), gy.ravel()
        inside = shapely.contains_xy(wet, gx, gy)
        gx, gy = gx[inside], gy[inside]
        if len(gx):
            cells = np.floor(np.column_stack([gx, gy]) / WATER_CELL).astype(np.int64)
            measured = np.floor(water[:, :2] / WATER_CELL).astype(np.int64) if len(water) else cells[:0]
            free = ~np.isin(cells[:, 0] * 10_000_000 + cells[:, 1], measured[:, 0] * 10_000_000 + measured[:, 1])
            synthetic = np.column_stack([gx[free], gy[free], np.full(free.sum(), cities.current().water_level)])
        # Ground returns inside the water areas are boats, pontoons and quay-wall noise.
        if len(ground):
            dry = ~shapely.contains_xy(wet.buffer(-1.0), ground[:, 0], ground[:, 1])
            ground = ground[dry]
    points = np.concatenate([ground, water, synthetic])
    root = C.raw_dir("laz") / "maanpinta"
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"{block.name}.laz"
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.array([np.floor(b.emin), np.floor(b.nmin), 0.0])
    las = laspy.LasData(header)
    las.x, las.y, las.z = points[:, 0], points[:, 1], points[:, 2]
    las.classification = np.full(len(points), KEY_POINT_CLASS, np.uint8)
    tmp = out.with_name(out.name + ".tmp.laz")
    las.write(tmp)
    tmp.replace(out)
    Registry(C.raw_dir("laz")).record(
        out,
        url=None,
        derived_from=[p.name for p in sources],
        ground=int(len(ground)),
        water=int(len(water)),
        water_synthetic=int(len(synthetic)),
    )
    log(f"nl   ground {block.name}: {len(ground)} ground, {len(water)} water, {len(synthetic)} synthetic water")
    from .lidar import pointcloud_block

    pointcloud_block(block, points, log)
    return out


# -- derived WFS layers ------------------------------------------------------------------------------


def _frame(rows, layer):
    import geopandas as gpd

    from turku3d.fetch.wfs import pages_to_frame, to_local

    if not rows:
        return pages_to_frame([], layer)[0], 0
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=C.CRS_HORIZONTAL)
    for c in gdf.columns:
        if c not in ("geometry", "geom_index"):
            gdf[c] = gdf[c].astype("string")
    gdf["geometry_local"] = gpd.GeoSeries(to_local(gdf.geometry.values), crs=None)
    return gdf, gdf["feature_key"].nunique()


def buildings_3dbag(bbox, log=print):
    """Rows of the LoD2.2 semantic surfaces of every 3DBAG building whose footprint meets the bbox."""
    import shapely
    from shapely.geometry import Polygon

    _ensure(("3dbag",), bbox, log)
    window = shapely.box(*bbox)
    rows, seen = [], set()
    for path in _files("3dbag", bbox):
        cj = json.loads(gzip.open(path).read())
        scale = np.array(cj["transform"]["scale"])
        shift = np.array(cj["transform"]["translate"])
        verts = np.asarray(cj["vertices"], dtype=np.float64) * scale + shift
        objects = cj["CityObjects"]
        for oid, obj in objects.items():
            if obj["type"] != "Building" or oid in seen:
                continue
            attrs = obj.get("attributes", {})
            parts = [objects[c] | {"id": c} for c in obj.get("children", []) if c in objects]
            part_rows = []
            for part in parts:
                geom = next((g for g in part.get("geometry", []) if str(g.get("lod")) == "2.2"), None)
                if geom is None or geom["type"] != "Solid":
                    continue
                surfaces = geom.get("semantics", {}).get("surfaces", [])
                values = geom.get("semantics", {}).get("values", [])
                for si, shell in enumerate(geom["boundaries"]):
                    for fi, face in enumerate(shell):
                        idx = values[si][fi] if si < len(values) and fi < len(values[si]) else None
                        stype = surfaces[idx]["type"] if idx is not None else ""
                        rings = [verts[r] for r in face]
                        if len(rings[0]) < 3:
                            continue
                        poly = Polygon(rings[0], [r for r in rings[1:] if len(r) >= 3])
                        part_rows.append(
                            {
                                "geometry": poly,
                                "geom_type": "Polygon",
                                "geom_role": "lod2Solid",
                                "surface_type": stype,
                                "part_id": part["id"],
                                "surface_id": f"{part['id']}-{si}-{fi}",
                            }
                        )
            footprint = [r["geometry"] for r in part_rows if r["surface_type"] == "GroundSurface"]
            if not footprint or not shapely.union_all(
                [shapely.force_2d(g) for g in footprint]
            ).intersects(window):
                continue
            seen.add(oid)
            h0, h70 = attrs.get("b3_h_maaiveld"), attrs.get("b3_h_dak_70p")
            meta = {
                "layer": "bldg:Building_LOD2",
                "feature_key": oid,
                "gml_id": oid,
                "storeysAboveGround": attrs.get("b3_bouwlagen"),
                "yearOfConstruction": attrs.get("oorspronkelijkbouwjaar"),
                "measuredHeight": None if h0 is None or h70 is None else round(h70 - h0, 2),
                "bag_status": attrs.get("status"),
                "roof_type": attrs.get("b3_dak_type"),
            }
            for i, r in enumerate(part_rows):
                rows.append({**meta, "geom_index": i, **r})
    return rows


# Paved BGT terrain outside the street register (squares, yards, quays): street areas of kind "Muu".
PAVED_TERRAIN = ("gesloten verharding", "open verharding", "half verhard")
# OSM surface=* -> (Pintamateriaali, code): the source for polygons whose BGT appearance is generic.
OSM_SURFACE = {
    "asphalt": ("Asfalttibetoni", "1"),
    "paving_stones": ("Betonikivi", "9"),
    "paving_stones:30": ("Betonilaatta", "8"),
    "concrete:plates": ("Betonilaatta", "8"),
    "grass_paver": ("Betonilaatta", "8"),
    "concrete": ("Betoni", "14"),
    "concrete:lanes": ("Betoni", "14"),
    "sett": ("Noppakivi", "6"),
    "cobblestone": ("Noppakivi", "6"),
    "unhewn_cobblestone": ("Nupukivi", "5"),
    "bricks": ("Maatiili", "21"),
    "brick": ("Maatiili", "21"),
    "clinker": ("Maatiili", "21"),
    "gravel": ("Sora", "7"),
    "fine_gravel": ("Kivituhka", "18"),
    "compacted": ("Kivituhka", "18"),
    "pebblestone": ("Sora", "7"),
    "shells": ("Sora", "7"),
    "wood": ("Puu", "13"),
    "metal": ("Metalli", "19"),
}
GENERIC = ("open verharding", "gesloten verharding", "half verhard")


class OsmSurfaces:
    """OSM features with a known ``surface`` over a bbox: areas first, then the way with the longest run inside."""

    def __init__(self, bbox):
        import geopandas as gpd
        import shapely

        self.geoms, self.values, self.areas = [], [], []
        window = shapely.box(*bbox)
        for path in sorted(C.raw_dir("osm").glob("coarse-osm-*/*.parquet")):
            frame = gpd.read_parquet(path, columns=["surface", "geometry"])
            frame = frame[frame.surface.isin(list(OSM_SURFACE))]
            frame = frame[frame.geometry.intersects(window)]
            for g, value in zip(frame.geometry, frame.surface, strict=True):
                self.geoms.append(g)
                self.values.append(value)
                self.areas.append(g.geom_type in ("Polygon", "MultiPolygon"))
        self.tree = shapely.STRtree(self.geoms) if self.geoms else None

    def at(self, polygon):
        if self.tree is None:
            return None
        best, score = None, 0.0
        for i in self.tree.query(polygon, predicate="intersects"):
            g = self.geoms[i]
            if self.areas[i]:
                share = g.intersection(polygon).area / max(polygon.area, 1e-9)
                value = 1e6 * share if share > 0.5 else 0.0
            else:
                value = g.intersection(polygon).length
            if value > score:
                best, score = self.values[i], value
        return OSM_SURFACE[best] if best and score >= (1.0 if not self.areas else 0) else None


def appearance(functie, fysiek, plus, osm=None) -> tuple[str, str]:
    """Surface (Pintamateriaali, code), in precedence: an explicit BGT appearance (plus_fysiek_voorkomen or a
    specific fysiek_voorkomen), the OSM surface tag of the way or area on the polygon, then Dutch street
    convention for generic element paving: clinker brick on carriageways, concrete tiles on footways. Asphalt
    cycle paths are red asphalt (CROW standard). The 2025 ortho is no fallback: it cannot separate brick from
    grey pavers (shadowed spring flight: median sunlit R - B -12 for BGT "gebakken klinkers" vs -3 for footways)."""
    found = surface(fysiek, plus)
    if not plus and (fysiek or "") in GENERIC and osm is not None:
        found = osm
    elif not plus and (fysiek or "") == "open verharding":
        if (functie or "").startswith("rijbaan") or functie in ("woonerf", "fietspad"):
            found = ("Maatiili", "21")
        elif functie in ("voetpad", "voetgangersgebied", "verkeerseiland", None):
            found = ("Betonilaatta", "8")
    if functie == "fietspad" and found[0] == "Asfalttibetoni":
        return ("Asfalttibetoni, punainen (AB 16, rautaoksidi FeO2)", "20")
    return found


def streets_bgt(bbox, log=print):
    osm = OsmSurfaces(bbox)
    rows = []
    for collection, key, p, g in bgt_features(STREET_COLLECTIONS + ("onbegroeidterreindeel",), bbox, log):
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        functie, fysiek = p.get("functie"), p.get("fysiek_voorkomen")
        plus = p.get("plus_fysiek_voorkomen")
        if collection == "ondersteunendwegdeel" and any(t in (fysiek or "") for t in GREEN):
            continue  # planted verges: the ortho and the vegetation producers own them
        if collection == "onbegroeidterreindeel":
            if (fysiek or "") not in PAVED_TERRAIN:
                continue  # sand, bare yards: the ortho classification owns them
            functie = None
        part_kind = kind(functie) if functie is not None else ("Muu", "6")
        for i, part in enumerate(getattr(g, "geoms", [g])):
            material = appearance(functie, fysiek, plus, osm.at(part))
            rows.append(
                {
                    "layer": "GIS:Katualueet",
                    "feature_key": key,
                    "gml_id": key,
                    "geom_index": i,
                    "geometry": part,
                    "geom_type": "Polygon",
                    "geom_role": collection,
                    "surface_type": "",
                    "part_id": "",
                    "surface_id": "",
                    "Katuosalaji": part_kind[0],
                    "Katuosalaji_koodi": part_kind[1],
                    "Pintamateriaaliryhma": "Katuosan pintamateriaalit",
                    "Pintamateriaali": material[0],
                    "Pintamateriaali_koodi": material[1],
                    "Kadunnimi": p.get("openbareruimte_naam") or "",
                    "bgt_functie": functie or collection,
                    "bgt_fysiek": plus or fysiek,
                    "op_talud": p.get("op_talud"),
                }
            )
    return rows


def water_bgt(bbox, log=print):
    rows = []
    for collection, key, p, g in bgt_features(WATER_COLLECTIONS, bbox, log):
        for i, part in enumerate(getattr(g, "geoms", [g])):
            if part.geom_type != "Polygon":
                continue
            rows.append(
                {
                    "layer": "kanta:Vesikohde",
                    "feature_key": key,
                    "gml_id": key,
                    "geom_index": i,
                    "geometry": part,
                    "geom_type": "Polygon",
                    "geom_role": collection,
                    "surface_type": "",
                    "part_id": "",
                    "surface_id": "",
                    "bgt_type": p.get("type") or p.get("class"),
                }
            )
    return rows


def _points(collection, kind, layer, bbox, log, extra):
    rows = []
    for _, key, p, g in bgt_features((collection,), bbox, log):
        if (p.get("plus_type") or p.get("type")) != kind or g.geom_type != "Point":
            continue
        rows.append({"layer": layer, "feature_key": key, "gml_id": key, "geom_index": 0, "geometry": g,
                     "geom_type": "Point", "geom_role": collection, "surface_type": "", "part_id": "",
                     "surface_id": "", **extra(p)})
    return rows


def trees_bgt(bbox, log=print):
    """GIS:Puut (register trees) from BGT vegetatieobject_punt "boom" (no species or planting year in BGT)."""
    return _points("vegetatieobject_punt", "boom", "GIS:Puut", bbox, log,
                   lambda p: {"Kasvilaji": "", "Kasviryhma": "lehtipuu", "Istutusvuosi": ""})


def lamps_bgt(bbox, log=print):
    """Street-light poles (internal frn:CityFurniture_LOD2) from BGT paal "lichtmast"."""
    return _points("paal", "lichtmast", "frn:CityFurniture_LOD2", bbox, log,
                   lambda p: {"Tasonimi": "rr:js,pylväs,valaisin", "bgt_type": "lichtmast"})


ADAPTERS = {"3dbag": buildings_3dbag, "bgt-streets": streets_bgt, "bgt-water": water_bgt,
            "bgt-trees": trees_bgt, "bgt-lamps": lamps_bgt}


def derive(layer, area, adapter: str, log=print) -> dict:
    """Write one internal WFS layer of an area from the Dutch sources (``fetch.wfs.fetch_layer``);
    an existing layer from the same adapter version is kept (the native sources are fixed per fetch)."""
    bbox = area.with_margin.to_en().as_tuple()
    summary = C.raw_dir("wfs") / area.name / f"{layer.slug}.summary.json"
    with _locked(native_root() / "locks" / f"wfs-{area.name}-{layer.slug}.lock"):
        if summary.exists() and summary.with_name(f"{layer.slug}.parquet").exists():
            s = json.loads(summary.read_text())
            if s.get("derived") == adapter and s.get("adapter_version") == ADAPTER_VERSION:
                return s  # another build derived it meanwhile
        return _derive(layer, area, adapter, bbox, log)


ADAPTER_VERSION = 5  # 5: BGT > OSM surface > Dutch paving convention, paved terrain


def _derive(layer, area, adapter, bbox, log):
    from turku3d.fetch.wfs import write_frame

    rows = ADAPTERS[adapter](bbox, log=log)
    gdf, nfeat = _frame(rows, layer.name)
    s = write_frame(layer.name, layer.slug, area, gdf, nfeat, derived=adapter, city=cities.current().name,
                    adapter_version=ADAPTER_VERSION)
    log(f"nl   {layer.name} <- {adapter}: {nfeat} features, {len(gdf)} rows")
    return s


def rederive(log=print, adapters=None) -> int:
    """Derive every profile-derived WFS layer of each converted area that is missing, absent (a gap the
    profile now fills) or from an older adapter version; the tiles over it rebuild."""
    from turku3d.coarse.sources import AREA_PREFIX, block_area
    from turku3d.fetch.sources import WFS_LAYERS as ALL

    count = 0
    for area_dir in sorted(C.raw_dir("wfs").glob(f"{AREA_PREFIX}*")):
        if not area_dir.is_dir():
            continue
        area = block_area(C.LazTile.parse(area_dir.name.removeprefix(AREA_PREFIX)))
        for name, adapter in C.CITY.wfs_derived.items():
            if adapters and adapter not in adapters:
                continue
            layer = next(l for l in ALL if l.name == name)
            summary = area_dir / f"{layer.slug}.summary.json"
            s = json.loads(summary.read_text()) if summary.exists() else {}
            if s.get("derived") == adapter and s.get("adapter_version") == ADAPTER_VERSION:
                continue
            summary.unlink(missing_ok=True)
            derive(layer, area, adapter, log=log)
            count += 1
    return count


if __name__ == "__main__":
    import sys

    err = lambda *a: print(*a, file=sys.stderr)  # noqa: E731
    if sys.argv[1:2] == ["rederive"]:
        print(json.dumps({"rederived": rederive(err, sys.argv[2:] or None)}))
    elif sys.argv[1:2] == ["pointclouds"]:
        from .lidar import pointclouds

        print(json.dumps({"pointclouds": pointclouds(err)}))
    else:
        raise SystemExit("python -m turku3d.nl.convert rederive [ADAPTER ...] | pointclouds")
