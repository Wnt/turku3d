"""gaps: which buildings and streets of Turku no vision reading covers yet, and the readings that fill them.

    python -m turku3d.observe gaps city                 # the city's buildings and streets, from the registers
    python -m turku3d.observe gaps strata               # strata, coverage by readings and by the rules, the gaps
    python -m turku3d.observe gaps pick [--max 100]     # one reading per gap, largest first: list.json
    python -m turku3d.observe gaps prepare              # photos for the list (run on another host: on-host air)
    python -m turku3d.observe gaps run [--dry-run]      # the readings, with the existing readers; stores the results

No model call except in `run` without `--dry-run`. Everything is written under --out (default
~/turku3d/loops/w2/gaps): city/ (the tables), strata.json, coverage.md, list.json, prepared/ (photos, made off the
Mac) and runs/. None of it belongs in Git.

A building stratum is what the automatic tier would draw a building as and where: the style type it infers from
the register (`archetypes.infer`), the decade band, the storey class and the area type round it (the fresh-batch
draw's rule: floor area by use within 150 m). A street stratum is the street's class, the area type, the age of
the buildings along it and whether trees line it. A stratum is covered when a stored reading (full, light, hand or
converted from the old facade programs) holds one of its buildings or one of its street views, and known from the
data when its parent (style type and decade band) has readings the automatic tier already draws right. The rest
are the gaps; a street view covers its street stratum and the gap buildings it shows.
"""

from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import shapely

from turku3d import config

LOOPS = Path.home() / "turku3d/loops/w2"
OUT = LOOPS / "gaps" if LOOPS.is_dir() else None  # elsewhere (a host preparing photos): $TURKU3D_WORK/gaps
CELL_M = 2000.0  # the city's box is fetched in squares of this size, each paged by the WFS quadtree
FETCH_THREADS = 4  # requests at once to the city's WFS
AREA_M = 150.0  # area type: floor area by use within this radius (qa/views/random_streets.py)
WORK_HEAVY = {"691", "692", "699", "613", "1160", "1150", "1100", "1120"}  # industry, storage, education, health
LAYERS = {  # name: (WFS version, what it gives)
    "bldg:Building_LOD0": ("1.1.0", "every modelled building: footprint, use code, year, storeys, floor area"),
    "kanta:Rakennus": ("1.0.0", "facade material by permanent building id; footprints the model lacks"),
    "GIS:Kaupunginosat": ("1.0.0", "districts"),
    "GIS:Turun_keskustan_miljootyypitys": ("1.0.0", "milieu types of the centre"),
    "GIS:Katualueet": ("1.0.0", "street parts (carriageway, footway, verge) and their surface material"),
    "GIS:Kuntajako": ("1.0.0", "municipalities: the city's WFS also serves Kaarina's buildings and districts"),
}
MUNICIPALITY = "Turku"
BUILDING_LAYERS = ("bldg:Building_LOD0", "kanta:Rakennus", "GIS:Kaupunginosat", "GIS:Turun_keskustan_miljootyypitys")


def _layer(name: str):
    from turku3d.fetch.sources import WfsLayer

    return WfsLayer(name, LAYERS[name][0], LAYERS[name][1])


def fetch(name: str, box: tuple, pages: Path, log=print) -> list[Path]:
    """Every page of one layer over the box: squares of CELL_M fetched FETCH_THREADS at once, each paged by the
    WFS quadtree of turku3d.fetch.wfs (pages kept, a second run reads them)."""
    from turku3d.fetch import wfs as gml
    from turku3d.fetch.common import Registry

    layer, folder = _layer(name), pages / name.replace(":", "_")
    registry = Registry(folder)
    cells = [(e, n, min(e + CELL_M, box[2]), min(n + CELL_M, box[3]))
             for e in np.arange(box[0], box[2], CELL_M) for n in np.arange(box[1], box[3], CELL_M)]
    started, done = time.time(), []

    def one(cell):
        found = gml.fetch_pages(layer, cell, folder, registry, log=lambda *_: None)
        done.append(cell)
        if len(done) % 25 == 0:
            log(f"gaps: {name} {len(done)}/{len(cells)} squares, {time.time() - started:.0f} s")
        return found

    with ThreadPoolExecutor(FETCH_THREADS) as pool:
        return sorted({p for found in pool.map(one, cells) for p in found})


def _frame(pages: list[Path], name: str):
    from turku3d.fetch import wfs as gml

    frame, _ = gml.pages_to_frame(pages, name)
    return frame


def _missing(value) -> bool:
    try:
        return value is None or bool(value != value) or str(value) in ("<NA>", "nan", "")
    except TypeError:  # pandas' NA
        return True


def _num(value):
    if _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _year(*values) -> int | None:
    for value in values:
        if _missing(value):
            continue
        found = re.search(r"(1[5-9]\d\d|20\d\d)", str(value or ""))
        if found:
            return int(found.group(1))
    return None


def city(out: Path, log=print) -> dict:
    """The city's buildings (one row each: key, position, footprint, register facts, district, milieu) and its
    streets (OSM street lines in the municipality) as parquet under out/city."""
    import geopandas as gpd
    import pandas as pd

    from turku3d.world.sources.osm import city_extent

    box = city_extent()
    pages = out / "city/pages"
    tables = {}
    for name in BUILDING_LAYERS:
        started = time.time()
        found = fetch(name, box, pages, log)
        tables[name] = _frame(found, name)
        log(f"gaps: {name}: {len(found)} pages, {tables[name]['feature_key'].nunique() if len(tables[name]) else 0} "
            f"features, {time.time() - started:.0f} s")
    lod0 = tables["bldg:Building_LOD0"]
    lod0 = lod0[lod0.geom_type.isin(["Polygon", "MultiPolygon"])]
    rows = []
    for key, part in lod0.groupby("feature_key"):
        a = part.iloc[0]
        foot = shapely.make_valid(shapely.force_2d(shapely.unary_union(list(part.geometry))))
        if foot.is_empty:
            continue
        get = lambda k: (None if k not in a.index or pd.isna(a[k]) else a[k])  # noqa: E731
        prt = get("pysyvarakennustunnus")
        rows.append({
            "key": prt or "gml-" + re.sub(r"[^A-Za-z0-9._-]", "-", a["gml_id"] or key), "prt": prt,
            "source": "lod0", "use": get("käyttötarkoitus") or get("function"), "cls": get("class"),
            "year": _year(get("yearOfConstruction"), get("valmistunut")),
            "storeys": _num(get("kerrosluku")) or _num(get("storeysAboveGround")),
            "height_m": _num(get("measuredHeight")), "floor_m2": _num(get("kerrosala")),
            "address": get("taydellinen_osoite_fi"), "geometry": foot})
    buildings = gpd.GeoDataFrame(rows, geometry="geometry", crs=config.CRS_HORIZONTAL)
    kanta = tables["kanta:Rakennus"]
    kanta = kanta[kanta.geom_type.isin(["Polygon", "MultiPolygon"])]
    material = {}
    extra = []
    covered = shapely.STRtree(list(buildings.geometry))
    for key, part in kanta.groupby("feature_key"):
        a = part.iloc[0]
        prt = a.get("rakennustunnus")
        if isinstance(prt, str) and prt and isinstance(a.get("julkisivumateriaali"), str):
            material[prt] = a["julkisivumateriaali"]
        if str(a.get("kplkoodi")) != "66000":  # main buildings only: 66001 are outbuildings, kept out of the gaps
            continue
        foot = shapely.make_valid(shapely.force_2d(shapely.unary_union(list(part.geometry))))
        if foot.is_empty or foot.area < 40:
            continue
        hits = covered.query(foot, predicate="intersects")  # the same building when LoD2 covers 0.3 of it (building_shell)
        if len(hits) and sum(buildings.geometry.iloc[i].intersection(foot).area for i in hits) > 0.3 * foot.area:
            continue
        extra.append({"key": prt if isinstance(prt, str) and prt else "kanta-" + key, "prt": prt if isinstance(prt, str) else None,
                      "source": "kanta", "use": None, "cls": None, "year": _year(a.get("kottovuosi")),
                      "storeys": _num(a.get("kerrosluku")), "height_m": None, "floor_m2": None,
                      "address": " ".join(x for x in (a.get("teksti"), a.get("osoitenumero")) if isinstance(x, str)) or None,
                      "geometry": foot})
    if extra:
        buildings = pd.concat([buildings, gpd.GeoDataFrame(extra, geometry="geometry", crs=config.CRS_HORIZONTAL)],
                              ignore_index=True)
    buildings["material"] = buildings["prt"].map(material)
    buildings = buildings.drop_duplicates("key").reset_index(drop=True)
    centre = buildings.geometry.representative_point()
    buildings["E"], buildings["N"] = centre.x.round(1), centre.y.round(1)
    buildings["footprint_m2"] = buildings.geometry.area.round(1)
    for name, column, field in (("GIS:Kaupunginosat", "district", "Nimi_FIN"),
                                ("GIS:Turun_keskustan_miljootyypitys", "milieu", "Tyyppi")):
        zones = tables[name]
        zones = zones[zones.geom_type.isin(["Polygon", "MultiPolygon"])].dissolve(by="feature_key", aggfunc="first")
        joined = gpd.sjoin(gpd.GeoDataFrame(geometry=centre, crs=config.CRS_HORIZONTAL), zones[[field, "geometry"]],
                           how="left", predicate="within")
        buildings[column] = joined[~joined.index.duplicated()][field].reindex(buildings.index).values
    (out / "city").mkdir(parents=True, exist_ok=True)
    buildings.to_parquet(out / "city/buildings.parquet")
    zones = tables["GIS:Kaupunginosat"]
    zones[zones.geom_type.isin(["Polygon", "MultiPolygon"])].dissolve(by="feature_key", aggfunc="first")[
        ["Nimi_FIN", "geometry"]].to_parquet(out / "city/districts.parquet")
    return {"buildings": len(buildings), "from_lod0": int((buildings.source == "lod0").sum()),
            "from_kanta": int((buildings.source == "kanta").sum()), "with_material": int(buildings.material.notna().sum()),
            "districts": int(buildings.district.nunique())}


# ---------------------------------------------------------------------------------------------------------------
# Strata

SKIP_STYLES = {"outbuilding", "garage"}  # sheds and garages: the rules carry them, no reading is spent on one
MIN_FOOTPRINT_M2 = 40.0
NEAR_YEAR_M = 60.0  # an undated building takes the year of its dated neighbours this near (building_shell's rule)


def decade(year) -> str:
    if not year:
        return "undated"
    year = int(year)
    if year < 1900:
        return "-1899"
    if year < 1920:
        return "1900-19"
    return f"{min(year, 2029) // 10 * 10}s"


def storeys_class(storeys) -> str:
    if not storeys or storeys < 1:
        return "?"
    storeys = int(round(storeys))
    return "1" if storeys == 1 else "2" if storeys == 2 else "3-4" if storeys <= 4 else "5-7" if storeys <= 7 else "8+"


def _use(cls, fn, storeys, area) -> str | None:
    """houses, apartments or work, as the fresh-batch draw counts floor area (qa/views/random_streets.py)."""
    cls = str(cls or "")
    if cls == "1000":
        return "houses" if str(fn) == "1000" or (storeys or 0) <= 2 else "apartments"
    if cls in ("0", "999", "", "None", "nan") or (cls == "1150" and area < 200):
        return None
    return "work"


def area_types(points: np.ndarray, frame) -> list:
    """The area type at each point (E, N): centre, apartments, houses or work from the floor area by use of the
    buildings within AREA_M (random_streets.area_type), None where nothing is built."""
    feet = np.array(list(frame.geometry))
    area = frame.footprint_m2.to_numpy(float)
    floor = np.where(frame.floor_m2.fillna(0).to_numpy(float) > 0, frame.floor_m2.fillna(0).to_numpy(float),
                     area * np.maximum(1.0, frame.storeys.fillna(1).to_numpy(float)))
    uses = [_use(c, f, s, a) for c, f, s, a in zip(frame.cls, frame.use, frame.storeys, area, strict=True)]
    index = {"houses": 0, "apartments": 1, "work": 2}
    which = np.array([index.get(u, -1) for u in uses])
    heavy = np.array([str(c) in WORK_HEAVY for c in frame.cls])
    tree = shapely.STRtree(feet)
    discs = shapely.buffer(shapely.points(points), AREA_M, quad_segs=8)
    at, hit = tree.query(discs, predicate="intersects")
    sums = np.zeros((len(points), 4))
    keep = which[hit] >= 0
    np.add.at(sums, (at[keep], which[hit][keep]), floor[hit][keep])
    np.add.at(sums, (at[keep & heavy[hit]], 3), floor[hit][keep & heavy[hit]])
    out = []
    disc_area = math.pi * AREA_M ** 2
    for houses, flats, work, heavy_floor in sums:
        total = houses + flats + work
        if total <= 0:
            out.append(None)
        elif total / disc_area >= 0.8 and heavy_floor < 0.5 * total and houses + flats >= 0.15 * total:
            out.append("centre")
        else:
            out.append(("houses", "apartments", "work")[int(np.argmax([houses, flats, work]))])
    return out


def styled(frame):
    """Adds year_near, style (what the automatic tier infers), decade, storeys class and area type."""
    from turku3d.world.kinds.building import archetypes

    points = np.column_stack([frame.E.to_numpy(float), frame.N.to_numpy(float)])
    years = frame.year.to_numpy(float)
    dated = np.flatnonzero(np.isfinite(years) & (years > 0))
    tree = shapely.STRtree(shapely.points(points[dated]))
    near = np.full(len(frame), np.nan)
    undated = np.flatnonzero(~(np.isfinite(years) & (years > 0)))
    at, hit = tree.query(shapely.points(points[undated]), predicate="dwithin", distance=NEAR_YEAR_M)
    for i in np.unique(at):
        found = dated[hit[at == i]]
        d = np.hypot(*(points[found] - points[undated[i]]).T)
        weight = 1.0 / (5.0 + d) ** 2
        order = np.argsort(years[found])
        run = np.cumsum(weight[order])
        near[undated[i]] = years[found][order][np.searchsorted(run, run[-1] / 2)]
    frame["year_near"] = near
    styles = []
    for row in frame.itertuples():
        ring = [list(p) for p in (row.geometry.exterior.coords if row.geometry.geom_type == "Polygon"
                                  else max(row.geometry.geoms, key=lambda g: g.area).exterior.coords)]
        register = {k: v for k, v in {"use": row.use, "material": row.material,
                                      "year": int(row.year) if row.year and row.year == row.year else None,
                                      "year_near": int(row.year_near) if row.year_near == row.year_near else None,
                                      "storeys": int(row.storeys) if row.storeys and row.storeys == row.storeys else None,
                                      "height_m": row.height_m if row.height_m == row.height_m else None}.items()
                    if v not in (None, "")}
        styles.append(archetypes.infer({"id": f"building/{row.key}", "register": register,
                                        "shell": {"surfaces": [{"kind": "ground", "rings": [ring]}]}}))
    frame["style"] = styles
    # The year and storeys the automatic tier draws with: an undated building takes its neighbours' year, and
    # one without a storey count the count its modelled height holds (3 m a storey).
    drawn_year = frame.year.fillna(frame.year_near)
    frame["decade"] = [decade(y if y == y else None) for y in drawn_year]
    frame["era"] = [era20(y) for y in drawn_year]
    storeys = frame.storeys.where(frame.storeys >= 1, np.nan).fillna((frame.height_m / 3.0).round().clip(lower=1))
    frame["storeys_class"] = [storeys_class(s if s == s else None) for s in storeys]
    frame["area_type"] = [a or "rural" for a in area_types(points, frame)]
    return frame


def municipality(out: Path, log=print):
    """The municipality's outline (GIS:Kuntajako), kept as out/city/municipality.parquet."""
    import geopandas as gpd

    from turku3d.world.sources.osm import city_extent

    path = out / "city/municipality.parquet"
    if not path.exists():
        found = _frame(fetch("GIS:Kuntajako", city_extent(), out / "city/pages", log), "GIS:Kuntajako")
        found = found[found.geom_type.isin(["Polygon", "MultiPolygon"]) & (found.Kuntajako == MUNICIPALITY)]
        gpd.GeoDataFrame(geometry=[shapely.unary_union(list(found.geometry))], crs=config.CRS_HORIZONTAL).to_parquet(path)
    return gpd.read_parquet(path).geometry.iloc[0]


STRATUM = ("style", "decade", "storeys_class", "area_type")
PARENT = ("style", "era")  # the level `against` measures the automatic tier at


def readings(store: Path) -> dict:
    """{building key: kind of the best stored reading}: full (from panoramas), hand, converted (from the old
    facade programs, themselves vision readings), light (a judged photo read at a glance, confidence 0.5 or
    more). A colour measured without a model is no reading."""
    rank = {"full": 0, "hand": 1, "converted": 2, "light": 3}
    found: dict = {}

    def note(key, kind):
        if key not in found or rank[kind] < rank[found[key]]:
            found[key] = kind

    for path in sorted((store / "building").glob("*.json")):
        doc = json.loads(path.read_text())
        sources = {p.get("source") for p in doc.get("provenance", [])}
        if doc.get("tier") == "auto":
            continue
        kind = ("full" if "panorama" in sources else "hand" if "hand" in sources else
                "converted" if "facade-program" in sources else "light" if "photo" in sources else None)
        if kind:
            note(path.stem, kind)
    for path in sorted((store / "location").glob("*.json")):
        for item in json.loads(path.read_text()).get("items", []):
            if item.get("kind") == "building" and item.get("entity") and (item.get("conf") or 0) >= 0.5:
                note(item["entity"].removeprefix("building/"), "light")
    return found


def known_parents(against_csv: Path, least: int = 3, agree: float = 0.8) -> dict:
    """{(style, era): {n, agree}} from `observe against`: how well the automatic tier draws the read buildings of
    each parent stratum on the attributes a street view shows first (style group, wall family, storeys, ground
    storey, roof form, balconies). A parent with `least` readings and `agree` on average is known from the data."""
    import csv

    from turku3d.world.kinds.building import priors

    group = lambda s: "wooden" if s in priors.WOODEN else "old_masonry" if s in priors.OLD_MASONRY else "modern"  # noqa: E731
    checks = [
        lambda r: None if not r["style_read"] else group(r["style_auto"]) == group(r["style_read"]),
        lambda r: None if not r["wall_family_read"] else r["wall_family_auto"] == r["wall_family_read"],
        lambda r: None if not r["storeys_read"] else r["storeys_auto"] == r["storeys_read"],
        lambda r: None if not r["ground_read"] else r["ground_auto"] == r["ground_read"],
        lambda r: None if not r["roof_form_read_class"] else r["roof_form_shell"] == r["roof_form_read_class"],
        lambda r: (r["balconies_auto"] not in ("", "0")) == (r["balconies_read"] not in ("", "0")),
    ]
    parents: dict = {}
    with against_csv.open() as handle:
        for row in csv.DictReader(handle):
            verdicts = [v for v in (check(row) for check in checks) if v is not None]
            if verdicts:
                parents.setdefault((row["style_auto"], row["era"]), []).append(sum(verdicts) / len(verdicts))
    return {k: {"n": len(v), "agree": round(sum(v) / len(v), 2), "known": len(v) >= least and sum(v) / len(v) >= agree}
            for k, v in parents.items()}


def era20(year) -> str:
    from .against import era

    return era(int(year) if year and year == year else None)


# ---------------------------------------------------------------------------------------------------------------
# Streets

STREET_CLASSES = {"primary": "main", "secondary": "main", "tertiary": "main", "residential": "local",
                  "unclassified": "local", "living_street": "local", "pedestrian": "pedestrian"}
SEGMENT_M = 80.0  # streets are cut into pieces of at most this length
FRONT_M = 35.0  # a building this near a street piece fronts it
TREE_M = 10.0  # a tree this near the street line stands in the street
TREES_PER_100M = 3.0  # this many street trees per 100 m (or a mapped tree row) and the street is tree-lined
PART_M = 14.0  # street parts (GIS:Katualueet) this near the line make its kerb profile
FOOTWAYS = {"Jalkakäytävä", "Yhdistetty kevyen liikenteen väylä", "Pyörätie", "Kävelykatu"}
PAVED = {"Betonikivi", "Betonilaatta", "Luonnonkivi", "Liuskekivi", "Noppakivi", "Nupukivi", "Graniittilaatta",
         "Kenttäkivi", "Mukulakivi"}
VIEW_TO_STREET_M = 25.0  # a street view covers the street piece its camera stands on, this near


def street_age(year) -> str:
    if year is None or year != year:
        return "open"
    return "-1939" if year < 1940 else "1940-69" if year < 1970 else "1970-99" if year < 2000 else "2000-"


def streets(out: Path, buildings, log=print):
    """The city's street pieces (OSM streets inside the districts, cut to SEGMENT_M) with class, area type, the
    age of the buildings along them, trees and the footway profile from the city's street parts."""
    import geopandas as gpd
    from shapely.ops import substring

    from turku3d.world.sources import Sources
    from turku3d.world.sources.osm import city_extent

    box = city_extent()
    city_shape = municipality(out, log)
    osm = Sources(log=lambda *_: None).osm.features(box)
    lines = osm[osm.highway.isin(list(STREET_CLASSES)) & osm.geom_type.isin(["LineString", "MultiLineString"])]
    tags = [json.loads(t) if isinstance(t, str) else (t or {}) for t in lines.tags]
    keep = [("tunnel" not in t) and t.get("access") not in ("private", "no") for t in tags]
    lines = lines[keep]
    rows = []
    for line in lines.itertuples():
        parts = shapely.get_parts(line.geometry)
        for part in parts:
            count = max(1, math.ceil(part.length / SEGMENT_M))
            for k in range(count):
                piece = substring(part, k * part.length / count, (k + 1) * part.length / count)
                mid = piece.interpolate(0.5, normalized=True)
                if not city_shape.contains(mid):
                    continue
                rows.append({"osm_id": int(line.osm_id), "name": line.name if isinstance(line.name, str) else None,
                             "highway": line.highway, "class": STREET_CLASSES[line.highway],
                             "length_m": round(piece.length, 1), "E": round(mid.x, 1), "N": round(mid.y, 1),
                             "geometry": piece})
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=config.CRS_HORIZONTAL)
    log(f"gaps: {len(frame)} street pieces, {frame.length_m.sum() / 1000:.0f} km")
    points = np.column_stack([frame.E.to_numpy(float), frame.N.to_numpy(float)])
    frame["area_type"] = [a or "rural" for a in area_types(points, buildings)]
    # Age of the buildings along: their median year weighted by footprint.
    feet = shapely.STRtree(list(buildings.geometry))
    at, hit = feet.query(np.array(list(frame.geometry)), predicate="dwithin", distance=FRONT_M)
    years = buildings.year.fillna(buildings.year_near).to_numpy(float)
    weight = buildings.footprint_m2.to_numpy(float)
    ages, fronting = [None] * len(frame), [0] * len(frame)
    order = np.argsort(at, kind="stable")
    at_sorted, hit_sorted = at[order], hit[order]
    starts = np.searchsorted(at_sorted, np.arange(len(frame) + 1))
    for i in range(len(frame)):
        found = hit_sorted[starts[i]:starts[i + 1]]
        found = found[np.isfinite(years[found])]
        if len(found):
            o = np.argsort(years[found])
            run = np.cumsum(weight[found][o])
            ages[i] = float(years[found][o][np.searchsorted(run, run[-1] / 2)])
        fronting[i] = int(len(hit_sorted[starts[i]:starts[i + 1]]))
    frame["front_year"] = ages
    frame["fronting"] = fronting
    frame["age"] = [street_age(y) for y in ages]
    # Trees: OSM's trees (in Turku imported from the city's register) and tree rows.
    trees = osm[(osm.natural == "tree") & (osm.geom_type == "Point")]
    rows_ = osm[(osm.natural == "tree_row")]
    tree_index = shapely.STRtree(list(trees.geometry))
    t_at, _ = tree_index.query(np.array(list(frame.geometry)), predicate="dwithin", distance=TREE_M)
    counts = np.bincount(t_at, minlength=len(frame))
    row_index = shapely.STRtree(list(rows_.geometry)) if len(rows_) else None
    lined = np.zeros(len(frame), bool)
    if row_index is not None:
        r_at, _ = row_index.query(np.array(list(frame.geometry)), predicate="dwithin", distance=TREE_M)
        lined[np.unique(r_at)] = True
    frame["trees"] = np.where(lined | (counts * 100.0 / np.maximum(frame.length_m.to_numpy(float), 1.0) >= TREES_PER_100M),
                              "trees", "bare")
    # Footway profile from the city's street parts.
    pages = fetch("GIS:Katualueet", box, out / "city/pages", log)
    parts = _frame(pages, "GIS:Katualueet")
    parts = parts[parts.geom_type.isin(["Polygon", "MultiPolygon"])].drop_duplicates("feature_key")
    kinds = parts.Katuosalaji.astype(str).to_numpy()
    surface = parts.Pintamateriaali.astype(str).to_numpy()
    p_index = shapely.STRtree(list(parts.geometry))
    p_at, p_hit = p_index.query(np.array(list(frame.geometry)), predicate="dwithin", distance=PART_M)
    profile = ["no footway"] * len(frame)
    mapped = np.zeros(len(frame), bool)
    mapped[np.unique(p_at)] = True
    best: dict = {}
    for i, j in zip(p_at, p_hit, strict=True):
        if kinds[j] in FOOTWAYS:
            kind = "paved footway" if surface[j] in PAVED else "gravel footway" if surface[j] in ("Sora", "Kivituhka") else "asphalt footway"
            rank = {"paved footway": 0, "asphalt footway": 1, "gravel footway": 2}[kind]
            if i not in best or rank < best[i][0]:
                best[i] = (rank, kind)
    for i, (_, kind) in best.items():
        profile[i] = kind
    frame["profile"] = [p if mapped[i] else "unmapped" for i, p in enumerate(profile)]
    frame.to_parquet(out / "city/streets.parquet")
    return frame


STREET_STRATUM = ("class", "area_type", "age", "trees", "profile")
PILOT_TAGS = {"pilot", "pilot-street"}


def read_views(store: Path) -> list[dict]:
    """Street views whose photographs have been read: every view of a stored location reading and the judged
    pilot views (their buildings read in full)."""
    from .panoramas import REPO

    found = []
    for path in sorted((store / "location").glob("*.json")):
        for view in json.loads(path.read_text()).get("views", []):
            pose = view.get("pose") or {}
            if "E" in pose:
                found.append({"id": view.get("id"), "E": pose["E"], "N": pose["N"], "from": path.stem})
    rows = REPO / "qa/views/views.jsonl"
    for line in rows.read_text().splitlines() if rows.exists() else []:
        row = json.loads(line) if line.strip() else {}
        if PILOT_TAGS & set(row.get("tags", [])) and row["id"] not in {v["id"] for v in found}:
            found.append({"id": row["id"], "E": row["pose"]["E"], "N": row["pose"]["N"], "from": "pilot"})
    return found


def _share(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def strata(out: Path, store: Path, against_csv: Path, log=print) -> dict:
    """Strata of buildings and streets, what covers each, and the gaps by size: city/buildings-strata.parquet,
    city/streets.parquet, strata.json and coverage.md under `out`."""
    import geopandas as gpd

    buildings = styled(gpd.read_parquet(out / "city/buildings.parquet"))
    reads = readings(store)
    buildings["reading"] = buildings.key.map(reads)
    town = municipality(out, log)
    buildings["in_town"] = shapely.contains_xy(town, buildings.E.to_numpy(float), buildings.N.to_numpy(float))
    buildings["eligible"] = (~buildings["style"].isin(list(SKIP_STYLES)) & (buildings.footprint_m2 >= MIN_FOOTPRINT_M2)
                             & buildings.in_town)
    parents = known_parents(against_csv)
    matched = sum(k in set(buildings.key) for k in reads)
    log(f"gaps: {len(buildings)} buildings, {int(buildings.eligible.sum())} eligible; {len(reads)} stored readings, "
        f"{matched} of them on a city building")
    rows = []
    eligible = buildings[buildings.eligible]
    total = len(eligible)
    for values, group in eligible.groupby(list(STRATUM), dropna=False):
        stratum = dict(zip(STRATUM, (None if v != v else v for v in values), strict=True))
        kinds = group.reading.value_counts().to_dict()
        parent = {}
        for era, n in group.era.value_counts().items():
            found = parents.get((stratum["style"], era))
            if found:
                parent = {**found, "era": era, "share_of_stratum": round(n / len(group), 2)}
                break
        status = "read" if kinds else "data" if parent.get("known") else "gap"
        rows.append({"stratum": stratum, "buildings": len(group), "share": _share(len(group), total),
                     "floor_m2": round(float(group.floor_m2.fillna(0).sum())), "readings": kinds, "parent": parent,
                     "status": status, "districts": group.district.value_counts().head(3).to_dict()})
    rows.sort(key=lambda r: -r["buildings"])
    buildings["stratum"] = ["|".join(str(v) for v in values) for values in zip(*(buildings[c] for c in STRATUM), strict=True)]
    status = {"|".join(str(r["stratum"][c]) for c in STRATUM): r["status"] for r in rows}
    buildings["status"] = [status.get(s, "skip") if e else "skip" for s, e in zip(buildings.stratum, buildings.eligible, strict=True)]
    buildings.to_parquet(out / "city/buildings-strata.parquet")
    pieces = gpd.read_parquet(out / "city/streets.parquet") if (out / "city/streets.parquet").exists() else streets(out, buildings, log)
    pieces["stratum"] = ["|".join(str(v) for v in values) for values in zip(*(pieces[c] for c in STREET_STRATUM), strict=True)]
    views = read_views(store)
    index = shapely.STRtree(list(pieces.geometry))
    seen = set()
    for view in views:
        near = index.query(shapely.Point(view["E"], view["N"]), predicate="dwithin", distance=VIEW_TO_STREET_M)
        if len(near):
            j = min(near, key=lambda j: pieces.geometry.iloc[j].distance(shapely.Point(view["E"], view["N"])))
            seen.add(pieces.stratum.iloc[j])
            view["stratum"] = pieces.stratum.iloc[j]
    street_rows = []
    length = float(pieces.length_m.sum())
    for values, group in pieces.groupby(list(STREET_STRATUM), dropna=False):
        key = "|".join(str(v) for v in values)
        street_rows.append({"stratum": dict(zip(STREET_STRATUM, (None if v != v else v for v in values), strict=True)),
                            "length_m": round(float(group.length_m.sum())), "share": _share(float(group.length_m.sum()), length),
                            "pieces": len(group), "status": "read" if key in seen else "gap",
                            "views": [v["id"] for v in views if v.get("stratum") == key]})
    street_rows.sort(key=lambda r: -r["length_m"])
    pieces["status"] = ["read" if s in seen else "gap" for s in pieces.stratum]
    pieces.to_parquet(out / "city/streets.parquet")
    summary = {
        "made": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "buildings": {"city": len(buildings), "eligible": total, "strata": len(rows),
                      **{s: {"strata": sum(r["status"] == s for r in rows),
                             "buildings": sum(r["buildings"] for r in rows if r["status"] == s),
                             "share": _share(sum(r["buildings"] for r in rows if r["status"] == s), total)}
                         for s in ("read", "data", "gap")},
                      "readings": len(reads), "readings_on_city_buildings": matched},
        "streets": {"km": round(length / 1000, 1), "pieces": len(pieces), "strata": len(street_rows), "views_read": len(views),
                    **{s: {"strata": sum(r["status"] == s for r in street_rows),
                           "km": round(sum(r["length_m"] for r in street_rows if r["status"] == s) / 1000, 1),
                           "share": _share(sum(r["length_m"] for r in street_rows if r["status"] == s), length)}
                       for s in ("read", "gap")}},
        "parents": {f"{k[0]}|{k[1]}": v for k, v in sorted(parents.items())},
    }
    (out / "strata.json").write_text(json.dumps({"summary": summary, "buildings": rows, "streets": street_rows},
                                                indent=1, ensure_ascii=False))
    return summary


# ---------------------------------------------------------------------------------------------------------------
# The list

LIGHT_WEIGHT = 0.25  # a street view's glance at a gap building is worth this much of a full reading of it
FRONT_VIEW_M = 25.0  # buildings this near a street piece, and within VIEW_REACH_M of its middle, are taken to show
VIEW_REACH_M = 45.0  # in its street view until the view is made (a light reading names the near ones with confidence)
VIEW_STRATA = 3  # building strata one street view is counted to cover at most (its largest)
SHOWN_SHARE = 0.03  # of a made street view's pixels: a building this large in it counts as glanced at
STREET_SIDE_M = 20.0  # a building read from panoramas stands this near a street
ALTERNATES = 2  # other buildings of the stratum prepared too, in case the first is hidden
FRESH = LOOPS / "fresh/batch-01"  # judged cold and public: its photographs are preferred
MIN_BUILDINGS = 15  # a building gap smaller than this is not worth a reading of its own
MIN_STREET_M = 1500.0  # nor a street gap shorter than this
FULL_USD, LIGHT_USD = 0.9, 0.12  # notional cost of a full and a light reading (w2-observe's measurements)


def fresh_views(folder: Path = FRESH) -> list[dict]:
    """The judged views of a public fresh batch: camera, image, what it aims at."""
    rows = folder / "views.jsonl"
    if not rows.exists():
        return []
    found = []
    for line in rows.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            found.append({"id": row["id"], "E": row["pose"]["E"], "N": row["pose"]["N"], "area": row.get("area"),
                          "street": row["id"].endswith("_street"), "image": row.get("image")})
    return found


def pick(out: Path, limit: int = 100, log=print) -> dict:
    """At most `limit` readings, each filling the largest gap left, greedily: a building reading covers its
    stratum; a street view covers its street stratum and, at LIGHT_WEIGHT, the gap buildings fronting it."""
    import geopandas as gpd

    data = json.loads((out / "strata.json").read_text())
    buildings = gpd.read_parquet(out / "city/buildings-strata.parquet")
    pieces = gpd.read_parquet(out / "city/streets.parquet")
    total_b = int(buildings.eligible.sum())
    total_s = float(pieces.length_m.sum())
    # Value is a share of what is unknown: a building stratum counts as its part of all gap buildings, a street
    # stratum as its part of all gap street length, so that the coarser street strata do not crowd out buildings.
    gap_b = sum(r["buildings"] for r in data["buildings"] if r["status"] == "gap")
    gap_s = sum(r["length_m"] for r in data["streets"] if r["status"] == "gap")
    b_share = {"|".join(str(r["stratum"][c]) for c in STRATUM): r["buildings"] / gap_b
               for r in data["buildings"] if r["status"] == "gap" and r["buildings"] >= MIN_BUILDINGS}
    s_share = {"|".join(str(r["stratum"][c]) for c in STREET_STRATUM): r["length_m"] / gap_s
               for r in data["streets"] if r["status"] == "gap" and r["length_m"] >= MIN_STREET_M}
    # How far each building stands from a street, and which gap buildings front each street piece.
    index = shapely.STRtree(list(pieces.geometry))
    feet = np.array(list(buildings.geometry))
    nearest = index.query_nearest(feet, return_distance=True, all_matches=False)
    street_m = np.full(len(buildings), np.inf)
    street_m[nearest[0][0]] = nearest[1]
    buildings["street_m"] = street_m
    gap_rows = np.flatnonzero(buildings.stratum.isin(list(b_share)).to_numpy() & buildings.eligible.to_numpy())
    at, hit = index.query(feet[gap_rows], predicate="dwithin", distance=FRONT_VIEW_M)
    mids = shapely.points(pieces.E.to_numpy(float), pieces.N.to_numpy(float))
    fronting: dict = {}
    for i, j in zip(at, hit, strict=True):
        if shapely.distance(feet[gap_rows[i]], mids[j]) <= VIEW_REACH_M:
            fronting.setdefault(int(j), set()).add(buildings.stratum.iloc[gap_rows[i]])
    by_street = {s: np.flatnonzero((pieces.stratum == s).to_numpy()) for s in s_share}
    # Fresh-batch photographs already judged: a street view there is preferred (its photo exists, and its reading
    # characterises the batch).
    fresh = fresh_views()
    fresh_piece = {}
    for view in fresh:
        if view["street"]:
            near = index.query(shapely.Point(view["E"], view["N"]), predicate="dwithin", distance=VIEW_TO_STREET_M)
            if len(near):
                j = int(min(near, key=lambda j: pieces.geometry.iloc[j].distance(shapely.Point(view["E"], view["N"]))))
                fresh_piece[j] = view
    # How much of each building stratum is known: 0, LIGHT_WEIGHT after a street view's glance, 1 after a full
    # reading. A full reading is worth what it adds; so is a glance.
    level = dict.fromkeys(b_share, 0.0)
    picks, done_s, per_district = [], set(), {}
    # The reader works on LoD2 walls: a footprint the city has not modelled cannot be read (it still counts).
    can_read = buildings.eligible & (buildings.street_m <= STREET_SIDE_M) & (buildings.source == "lod0")
    readable = {s for s in b_share if ((buildings.stratum == s) & can_read).any()}

    def building_candidates(stratum):
        rows = buildings[(buildings.stratum == stratum) & can_read]
        foot = np.log(rows.footprint_m2.clip(lower=1))
        storeys = rows.storeys.fillna(rows.storeys.median() if rows.storeys.notna().any() else 1)
        typical = (np.abs(foot - foot.median()) + 0.3 * np.abs(storeys - storeys.median())
                   + 0.02 * rows.street_m + 0.4 * rows.district.map(lambda d: per_district.get(d, 0)).fillna(0)
                   + 0.5 * rows.address.isna())  # an address helps whoever checks the reading
        return rows.assign(typical=typical).sort_values("typical")

    def glance(s):
        return max(0.0, LIGHT_WEIGHT - level[s]) * b_share[s]

    def piece_value(j):
        shows = sorted((s for s in fronting.get(int(j), set()) if glance(s) > 0), key=lambda s: -glance(s))[:VIEW_STRATA]
        return sum(glance(s) for s in shows), shows

    while len(picks) < limit:
        best = None
        for stratum in readable:
            gain = (1.0 - level[stratum]) * b_share[stratum]
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, "building", stratum)
        for stratum, share in s_share.items():
            if stratum in done_s:
                continue
            values = [(piece_value(j)[0] + (0.25 * share if int(j) in fresh_piece else 0.0), int(j)) for j in by_street[stratum]]
            extra, j = max(values)
            if best is None or share + extra > best[0]:
                best = (share + extra, "street", stratum, j)
        if best is None:
            break
        if best[1] == "building":
            stratum = best[2]
            rows = building_candidates(stratum)
            first = rows.iloc[0]
            per_district[first.district] = per_district.get(first.district, 0) + 1
            level[stratum] = 1.0
            picks.append({
                "kind": "building", "value_pct": round(100 * best[0], 2), "stratum": dict(zip(STRATUM, stratum.split("|"), strict=True)),
                "covers": {"buildings": [stratum], "street": None},
                "size": {"buildings": round(b_share[stratum] * gap_b), "street_m": 0},
                "building": _building(first),
                "alternates": [_building(r) for r in rows.iloc[1:1 + ALTERNATES].itertuples()],
            })
        else:
            stratum, j = best[2], best[3]
            _, shows = piece_value(j)
            piece = pieces.iloc[j]
            done_s.add(stratum)
            for s in shows:
                level[s] = max(level[s], LIGHT_WEIGHT)
            district = _district_at(buildings, piece.E, piece.N)
            per_district[district] = per_district.get(district, 0) + 1
            # The street the view stands on: the pieces of the same OSM way, merged, the part through the piece.
            way = shapely.line_merge(shapely.unary_union(list(pieces.geometry[pieces.osm_id == piece.osm_id])))
            way = min(shapely.get_parts(way), key=lambda g: g.distance(shapely.Point(piece.E, piece.N)))
            picks.append({
                "kind": "street", "value_pct": round(100 * best[0], 2),
                "stratum": dict(zip(STREET_STRATUM, stratum.split("|"), strict=True)),
                "covers": {"buildings": sorted(shows), "street": stratum},
                "size": {"buildings": round(sum(b_share[s] for s in shows) * gap_b), "street_m": round(s_share[stratum] * gap_s)},
                "piece": {"name": piece["name"], "highway": piece.highway, "E": float(piece.E), "N": float(piece.N),
                          "district": district, "osm_id": int(piece.osm_id),
                          "line": [[round(x, 1), round(y, 1)] for x, y in way.coords]},
                "fresh_view": fresh_piece.get(int(j)),
            })
    done_b = {s for s, v in level.items() if v > 0}
    for n, entry in enumerate(picks, 1):
        entry["n"] = n
        entry["id"] = f"gap_{n:03d}"
    before = data["summary"]
    covered_b = sum(r["buildings"] for r in data["buildings"] if r["status"] in ("read", "data"))
    covered_s = sum(r["length_m"] for r in data["streets"] if r["status"] == "read")
    by_full = {s for p in picks if p["kind"] == "building" for s in p["covers"]["buildings"]}
    add_b = sum(round(b_share[s] * gap_b) for s in done_b)
    add_full = sum(round(b_share[s] * gap_b) for s in by_full)
    add_s = sum(round(s_share[s] * gap_s) for s in done_s)
    summary = {
        "made": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "readings": len(picks),
        "buildings_readings": sum(p["kind"] == "building" for p in picks),
        "street_views": sum(p["kind"] == "street" for p in picks),
        "building_strata_filled": len(done_b), "street_strata_filled": len(done_s),
        "coverage_before": {"buildings_pct": _share(covered_b, total_b), "streets_pct": _share(covered_s, total_s)},
        "coverage_after": {"buildings_pct": _share(covered_b + add_b, total_b), "streets_pct": _share(covered_s + add_s, total_s),
                           "buildings_pct_full_readings_only": _share(covered_b + add_full, total_b)},
        "notional_usd": round(sum(FULL_USD if p["kind"] == "building" else LIGHT_USD for p in picks), 2),
        "strata_before": before,
    }
    (out / "list.json").write_text(json.dumps({"summary": summary, "picks": picks}, indent=1, ensure_ascii=False))
    return summary


def _district_at(buildings, e, n) -> str | None:
    d = np.hypot(buildings.E.to_numpy(float) - e, buildings.N.to_numpy(float) - n)
    return buildings.district.iloc[int(np.argmin(d))]


def _building(row) -> dict:
    get = (lambda k: getattr(row, k)) if isinstance(row, tuple) else (lambda k: row[k])
    value = lambda k: None if _missing(get(k)) else get(k)  # noqa: E731
    return {"key": get("key"), "address": value("address"), "E": float(get("E")), "N": float(get("N")),
            "district": value("district"), "year": value("year"), "storeys": value("storeys"), "style": get("style"),
            "use": value("use"), "material": value("material"), "footprint_m2": float(get("footprint_m2")),
            "street_m": round(float(get("street_m")), 1)}


# ---------------------------------------------------------------------------------------------------------------
# Photos, made off the Mac

PREPARE_TIMEOUT_S = 15 * 60  # one building's views (observe views); a stalled download gives way to the next
STREET_CANDIDATES = 4  # newest panoramas on the street whose light is measured before one is chosen
OVERCAST_GOOD = 0.5
REPO_ROOT = Path(__file__).resolve().parents[3]


def _views(key: str, e: float, n: float, root: Path) -> dict | None:
    """Views and crops of one building (observe views), in a process of its own; its record or None."""
    import subprocess
    import sys

    packet = root / "buildings" / key / "views.json"
    if not packet.exists():
        try:
            subprocess.run([sys.executable, "-m", "turku3d.observe", "views", "--building", key, "--near", f"{e},{n}",
                            "--out", str(root), "--prepare-jobs", "1"], cwd=REPO_ROOT, timeout=PREPARE_TIMEOUT_S,
                           capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            return None
    return json.loads(packet.read_text()) if packet.exists() else None


def _street_view(entry: dict, refs: Path, light_cache) -> tuple[dict | None, str]:
    """An along-street view at the pick's street piece, from the most overcast of the newest panoramas there
    (qa/views/street_refs.street_view makes the crop)."""
    import sys

    sys.path[:0] = [str(REPO_ROOT / "qa"), str(REPO_ROOT / "textures"), str(REPO_ROOT / "qa/views")]
    from pilot_refs import candidates
    from street_refs import NEAR_M, ON_STREET_M, street_view
    from turkutex.streetview.panos import camera_height

    from .colour_light import LIGHT_GOOGLE_ZOOM, LIGHT_LOOKAROUND_ZOOM, measure

    cache, refreshed = light_cache
    piece = entry["piece"]
    line, name = shapely.LineString(piece["line"]), piece.get("name")
    name = name if isinstance(name, str) else None
    point = line.interpolate(line.project(shapely.Point(piece["E"], piece["N"])))
    found = [m for m in candidates(cache, point.x, point.y, 0, NEAR_M, refreshed) if m.source == "lookaround" or len(m.id) == 22]
    ground = sorted(m.elevation - camera_height(m.source, m.date) for m in found)
    ground = ground[len(ground) // 2] if ground else 0.0
    pool = [m for m in found if abs(m.elevation - camera_height(m.source, m.date) - ground) < 3
            and line.distance(shapely.Point(m.E, m.N)) <= ON_STREET_M and (m.date or "") >= "2024"]
    if not pool:
        return None, f"no panorama since 2024 on the street near {name or 'an unnamed street'}"
    pool.sort(key=lambda m: ((m.date or "")[:7], -math.hypot(m.E - point.x, m.N - point.y)), reverse=True)
    light = {}
    for m in pool[:STREET_CANDIDATES]:
        try:
            from PIL import Image

            path = (cache.lookaround_equirect(m, zoom=LIGHT_LOOKAROUND_ZOOM) if m.source == "lookaround"
                    else cache.google_image(m.id, zoom=LIGHT_GOOGLE_ZOOM))
            with Image.open(path) as im:
                light[m.id] = measure(np.asarray(im.convert("RGB")))["overcast"]
        except Exception:  # noqa: BLE001 - a panorama that cannot be fetched says nothing about its light
            light[m.id] = None
    order = sorted(pool[:STREET_CANDIDATES], key=lambda m: -(light.get(m.id) or 0.0)) + pool[STREET_CANDIDATES:]
    every = {m.id for m in pool}
    for m in order:
        view, said = street_view(line, point, None, entry["id"], entry["id"], cache, refreshed, refs, tags=["gaps"],
                                 min_date="2024", what=name or "an unnamed street", skip=every - {m.id})
        if view:
            view["image"] = str((refs / f"{entry['id']}.jpg").resolve())
            view["source"]["overcast"] = light.get(m.id)
            return view, said
    return None, said


def prepare(out: Path, root: Path, jobs: int = 3, log=print) -> dict:
    """Photos for every pick of out/list.json, on this machine: a building's views and crops (observe views) for
    it and its alternates, the one kept that the street sees enough of in the most overcast light; a street view
    from the most overcast recent panorama, and the buildings it shows. Writes out/prepared/prepared.json and
    out/prepared/views.jsonl; crops stay under root/buildings, panoramas in this machine's cache."""
    from turkutex.streetview.panos import PanoCache

    from . import colour_light, light, lod2
    from .__main__ import READ_AREA_M2

    listing = json.loads((out / "list.json").read_text())
    folder = out / "prepared"
    refs = folder / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    state_path = folder / "prepared.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    started = time.time()
    wanted = [(b["key"], b["E"], b["N"]) for p in listing["picks"] if p["kind"] == "building"
              for b in [p["building"], *p["alternates"][:1]]]
    with ThreadPoolExecutor(jobs) as pool:
        records = dict(zip([k for k, _, _ in wanted], pool.map(lambda w: _views(w[0], w[1], w[2], root), wanted), strict=True))
    log(f"gaps: views of {len(wanted)} buildings in {time.time() - started:.0f} s")
    overcast = colour_light.of_root(root)
    for p in listing["picks"]:
        if p["kind"] != "building":
            continue
        options = []
        for b in [p["building"], *p["alternates"][:1]]:
            record = records.get(b["key"])
            if not record or not record.get("crops"):
                continue
            light_of = [overcast.get(f"{c['panorama']['provider']}:{c['panorama']['id']}", {}).get("overcast") for c in record["crops"]]
            light_of = [x for x in light_of if x is not None]
            sky = round(float(np.mean(light_of)), 2) if light_of else None
            area = record["street"]["area_m2"]
            options.append({"key": b["key"], "E": b["E"], "N": b["N"], "crops": len(record["crops"]), "street_area_m2": area,
                            "frontage_m": record["street"]["frontage_m"], "overcast": sky,
                            "dates": sorted({c["panorama"]["date"] for c in record["crops"]}),
                            "ok": area >= READ_AREA_M2,
                            "score": round(min(1.0, area / 150.0) * (0.5 + 0.5 * (sky if sky is not None else 0.5)), 3)})
        good = [o for o in options if o["ok"]]
        chosen = max(good, key=lambda o: o["score"]) if good else None
        state[p["id"]] = {"kind": "building", "chosen": chosen, "options": options,
                          "ready": chosen is not None, "why": None if chosen else "no candidate the street sees enough of"}
    for stale in set(state) - {p["id"] for p in listing["picks"]}:
        del state[stale]
    light_cache = (PanoCache(), set())
    rows = []
    for p in listing["picks"]:
        if p["kind"] != "street":
            continue
        sig = f"{p['covers']['street']}@{p['piece']['osm_id']}"
        if state.get(p["id"], {}).get("sig") == sig and state[p["id"]].get("ready"):
            continue  # made by an earlier run for the same pick
        if p.get("fresh_view"):
            state[p["id"]] = {"kind": "street", "ready": True, "view": p["fresh_view"]["id"], "fresh": True,
                              "location": p["fresh_view"].get("area"), "sig": sig}
            continue
        try:
            view, said = _street_view(p, refs, light_cache)
        except Exception as error:  # noqa: BLE001 - one street must not stop the others
            view, said = None, f"{type(error).__name__}: {error}"[:200]
        if view is None:
            state[p["id"]] = {"kind": "street", "ready": False, "why": said, "sig": sig}
            continue
        pose = view["pose"]
        scene = lod2.scene_around(pose["E"], pose["N"], light.REACH_M + 50)
        shown = [{"key": b["key"], "share": b["share"], "distance_m": b["distance_m"]} for b in light.visible(view, scene)]
        rows.append(view)
        state[p["id"]] = {"kind": "street", "ready": True, "view": view["id"], "said": said, "shows": shown,
                          "overcast": view["source"].get("overcast"), "date": view.get("capture_date"),
                          "location": p["id"], "sig": sig}
        state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    old = [json.loads(x) for x in (folder / "views.jsonl").read_text().splitlines() if x.strip()] if (folder / "views.jsonl").exists() else []
    merged = {r["id"]: r for r in old + rows}
    (folder / "views.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged.values()))
    state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    return {"ready": sum(s["ready"] for s in state.values()), "of": len(listing["picks"]), "seconds": round(time.time() - started)}


# ---------------------------------------------------------------------------------------------------------------
# Running the readings

HOSTS = {"air": "jonni@192.168.1.22", "osg": "osgallery-dev"}  # scripts/dev/on-host's names
HOST_WORK = "turku3d-w2/{sandbox}/work"  # on-host's work directory, under the host's home
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def prepare_on(host: str, out: Path, jobs: int, log=print) -> int:
    """`prepare` on another machine through scripts/dev/on-host: the list goes there, the photo choices and the
    street views come back to out/prepared; building crops and panoramas stay on the host until `run`."""
    import os
    import subprocess

    sandbox = os.environ["TURKU3D_SANDBOX"]
    remote = HOST_WORK.format(sandbox=sandbox) + "/gaps"
    subprocess.run([*SSH, HOSTS[host], f"mkdir -p {remote}"], check=True, timeout=60)
    subprocess.run(["rsync", "-a", "-e", " ".join(SSH), str(out / "list.json"), f"{HOSTS[host]}:{remote}/list.json"],
                   check=True, timeout=300)
    done = subprocess.run([str(REPO_ROOT / "scripts/dev/on-host"), host, "--no-merge", "--pull", "gaps/prepared", "--",
                           "python", "-m", "turku3d.observe", "gaps", "prepare", "--jobs", str(jobs)], cwd=REPO_ROOT)
    pulled = config.work_root() / "gaps/prepared"
    if pulled.resolve() != (out / "prepared").resolve() and pulled.exists():
        subprocess.run(["rsync", "-a", f"{pulled}/", f"{out / 'prepared'}/"], check=True)
    state = out / "prepared/prepared.json"
    if state.exists():
        payload = json.loads(state.read_text())
        payload_host = {"host": host, "root": HOST_WORK.format(sandbox=sandbox) + "/observe"}
        (out / "prepared/host.json").write_text(json.dumps(payload_host))
        log(f"gaps: {sum(s['ready'] for s in payload.values())} of {len(payload)} picks have their photos")
    return done.returncode


def plan(out: Path) -> dict:
    """What `run` would do, and whether every input is in place: one row per pick of the list."""
    import shutil

    listing = json.loads((out / "list.json").read_text())
    folder = out / "prepared"
    state = json.loads((folder / "prepared.json").read_text()) if (folder / "prepared.json").exists() else {}
    host = json.loads((folder / "host.json").read_text()) if (folder / "host.json").exists() else None
    views = {}
    for path in (folder / "views.jsonl", FRESH / "views.jsonl"):
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    views.setdefault(row["id"], row)
    local = config.work_root() / "observe/buildings"
    remote_files: dict = {}
    if host:  # every file the packets name, listed on the host in one call
        import subprocess

        keys = [s["chosen"]["key"] for s in state.values() if s.get("kind") == "building" and s.get("chosen")]
        if keys:
            listing_cmd = "cd " + host["root"] + "/buildings && for k in " + " ".join(keys) + "; do ls $k 2>/dev/null | sed \"s|^|$k/|\"; done"
            found = subprocess.run([*SSH, HOSTS[host["host"]], listing_cmd], capture_output=True, text=True, timeout=120)
            for line in found.stdout.splitlines():
                key, _, name = line.partition("/")
                remote_files.setdefault(key, set()).add(name)
    rows = []
    for p in listing["picks"]:
        s = state.get(p["id"], {})
        row = {"id": p["id"], "kind": p["kind"], "value_pct": p["value_pct"], "problems": []}
        if not s.get("ready"):
            row["problems"].append(s.get("why") or "not prepared")
        elif p["kind"] == "building":
            key = s["chosen"]["key"]
            row.update(key=key, near=f"{s['chosen']['E']},{s['chosen']['N']}", crops=s["chosen"]["crops"],
                       overcast=s["chosen"]["overcast"])
            from . import lod2

            if key not in lod2.scene_around(s["chosen"]["E"], s["chosen"]["N"], 400).buildings:  # as describe --near finds it
                row["problems"].append("the key is not in the LoD2 scene describe --near builds")
            packet = local / key / "views.json"
            if packet.exists():
                record = json.loads(packet.read_text())
                names = {p_.name for p_ in (local / key).iterdir()}
                where = "here"
            elif key in remote_files:
                names, where, record = remote_files[key], host["host"], None
            else:
                names, where, record = set(), None, None
                row["problems"].append("packet neither here nor on the host")
            if "views.json" not in names:
                row["problems"].append("no views.json")
            if record is not None:
                wanted = {Path(c["file"]).name for c in record["crops"] + record["contexts"]}
                missing = wanted - names
                if missing:
                    row["problems"].append(f"{len(missing)} crop files missing")
            row["packet"] = where
        else:
            view = views.get(s["view"])
            if view is None:
                row["problems"].append("view row missing")
            else:
                image = Path(view["image"])
                image = image if image.is_absolute() else config.data_root().parent / image
                if not image.exists() and (folder / "refs" / image.name).exists():
                    image = folder / "refs" / image.name
                if not image.exists():
                    row["problems"].append(f"image missing: {image}")
                row.update(view=s["view"], image=str(image), location=s.get("location") or p["id"],
                           overcast=s.get("overcast") or (view.get("source") or {}).get("overcast"))
        rows.append(row)
    claude = shutil.which("claude")
    return {"rows": rows, "claude": claude, "ready": sum(not r["problems"] for r in rows), "of": len(rows),
            "notional_usd": round(sum(FULL_USD if r["kind"] == "building" else LIGHT_USD for r in rows if not r["problems"]), 2)}


def run(out: Path, dry: bool, jobs: int, budget: float, only: set | None, log=print) -> int:
    """The readings of the list with the existing readers (observe describe for a building, observe light for a
    street view), largest gap first, `jobs` at once, no new reading started once `budget` USD is spent. A
    building's packet is fetched from the host that prepared it first; stored results go through the store."""
    import subprocess
    import sys

    planned = plan(out)
    rows = [r for r in planned["rows"] if not only or r["id"] in only]
    for r in rows:
        log(json.dumps({k: r[k] for k in ("id", "kind", "key", "view", "location", "packet", "overcast", "problems") if k in r},
                       ensure_ascii=False))
    ready = [r for r in rows if not r["problems"]]
    log(json.dumps({"ready": len(ready), "of": len(rows), "claude": planned["claude"],
                    "notional_usd": round(sum(FULL_USD if r["kind"] == "building" else LIGHT_USD for r in ready), 2),
                    "dry_run": dry}))
    if not planned["claude"]:
        log("gaps: the claude CLI is not on PATH: the readers cannot run here")
        return 1
    if dry:
        return 0 if len(ready) == len(rows) else 2
    host = json.loads((out / "prepared/host.json").read_text()) if (out / "prepared/host.json").exists() else None
    local = config.work_root() / "observe/buildings"
    fetch = [r["key"] for r in ready if r["kind"] == "building" and r.get("packet") not in ("here", None)]
    if fetch and host:
        local.mkdir(parents=True, exist_ok=True)
        for key in fetch:  # crops only, a few MB a building; the panoramas stay on the host
            subprocess.run(["rsync", "-a", "-e", " ".join(SSH), f"{HOSTS[host['host']]}:{host['root']}/buildings/{key}",
                            f"{local}/"], check=True, timeout=600)
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime())
    logs = out / "runs" / stamp
    logs.mkdir(parents=True, exist_ok=True)
    views_file = logs / "views.jsonl"
    rows_by_id = {}
    for path in (out / "prepared/views.jsonl", FRESH / "views.jsonl"):
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    rows_by_id.setdefault(row["id"], row)
    with views_file.open("w") as handle:
        for r in ready:
            if r["kind"] == "street":
                handle.write(json.dumps({**rows_by_id[r["view"]], "image": r["image"]}, ensure_ascii=False) + "\n")
    spent = [0.0]
    import threading

    lock = threading.Lock()

    def one(r):
        with lock:
            if spent[0] >= budget:
                return {"id": r["id"], "skipped": "budget"}
        if r["kind"] == "building":
            command = [sys.executable, "-m", "turku3d.observe", "describe", "--building", r["key"], "--near", r["near"],
                       "--prepared", "--jobs", "1", "--no-review"]
        else:
            command = [sys.executable, "-m", "turku3d.observe", "light", "--location", r["location"], "--view", r["view"],
                       "--views-file", str(views_file)]
        started = time.time()
        done = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=3600)
        (logs / f"{r['id']}.log").write_text(done.stdout + "\n--- stderr ---\n" + done.stderr)
        cost = 0.0
        for line in done.stdout.splitlines():
            try:
                found = json.loads(line)
            except ValueError:
                continue
            if isinstance(found, dict):
                cost = max(cost, float(found.get("cost_usd") or 0.0))
        with lock:
            spent[0] += cost
        line = {"id": r["id"], "kind": r["kind"], "exit": done.returncode, "cost_usd": round(cost, 3),
                "seconds": round(time.time() - started), "spent_usd": round(spent[0], 2)}
        log(json.dumps(line))
        return line

    with ThreadPoolExecutor(max(1, jobs)) as pool:
        lines = list(pool.map(one, ready))
    (logs / "summary.json").write_text(json.dumps({"lines": lines, "spent_usd": round(spent[0], 2)}, indent=1))
    log(json.dumps({"done": sum(x.get("exit") == 0 for x in lines), "failed": sum(x.get("exit") not in (0, None) for x in lines),
                    "skipped": sum("skipped" in x for x in lines), "spent_usd": round(spent[0], 2), "logs": str(logs)}))
    return 0


# ---------------------------------------------------------------------------------------------------------------
# The report

def _key(stratum: dict) -> str:
    return " · ".join(str(v) for v in stratum.values())


def prepared_coverage(out: Path, data: dict, listing: dict, state: dict) -> dict:
    """Coverage after the picks whose photos are ready: a building pick fills its stratum; a street view its street
    stratum and, at a glance, the gap strata of the buildings its view shows (at least SHOWN_SHARE of the picture;
    the estimate of the list where no view was made here, as for a fresh-batch photo)."""
    import geopandas as gpd

    strata_of = dict(zip(*(gpd.read_parquet(out / "city/buildings-strata.parquet", columns=["key", "stratum"])[c]
                           for c in ("key", "stratum")), strict=True))
    gap_b = {"|".join(str(r["stratum"][c]) for c in STRATUM): r["buildings"] for r in data["buildings"] if r["status"] == "gap"}
    gap_s = {"|".join(str(r["stratum"][c]) for c in STREET_STRATUM): r["length_m"] for r in data["streets"] if r["status"] == "gap"}
    total_b = data["summary"]["buildings"]["eligible"]
    total_s = sum(r["length_m"] for r in data["streets"])
    full, glance, streets, ready = set(), set(), set(), 0
    for p in listing["picks"]:
        s = state.get(p["id"], {})
        if not s.get("ready"):
            continue
        ready += 1
        if p["kind"] == "building":
            full.add(p["covers"]["buildings"][0])
            continue
        streets.add(p["covers"]["street"])
        shown = [strata_of.get(b["key"]) for b in s.get("shows", []) if b["share"] >= SHOWN_SHARE] if "shows" in s else p["covers"]["buildings"]
        glance |= {x for x in shown if x in gap_b}
    before_b = data["summary"]["buildings"]["read"]["buildings"] + data["summary"]["buildings"]["data"]["buildings"]
    before_s = sum(r["length_m"] for r in data["streets"] if r["status"] == "read")
    return {"ready": ready, "buildings_pct": _share(before_b + sum(gap_b[x] for x in full | glance), total_b),
            "buildings_pct_full": _share(before_b + sum(gap_b[x] for x in full), total_b),
            "streets_pct": _share(before_s + sum(gap_s[x] for x in streets), total_s),
            "glanced_strata": len(glance - full), "full_strata": len(full)}


def report(out: Path) -> Path:
    """coverage.md: the strata, what covers them, the gaps by size, the list and its state, coverage before and
    after. Made from strata.json, list.json and prepared/prepared.json; no model call."""
    data = json.loads((out / "strata.json").read_text())
    listing = json.loads((out / "list.json").read_text()) if (out / "list.json").exists() else None
    state = json.loads((out / "prepared/prepared.json").read_text()) if (out / "prepared/prepared.json").exists() else {}
    s = data["summary"]
    b, st = s["buildings"], s["streets"]
    lines = [
        "# Gaps in what is known: strata, coverage and the list of readings", "",
        f"Made {s['made']} by `python -m turku3d.observe gaps strata|pick|prepare` (no model call). "
        f"Data: {out}.", "",
        "## Strata", "",
        f"- **Buildings** of Turku: {b['city']} in the city's WFS (LoD0 model + register footprints, Kaarina's left "
        f"out); {b['eligible']} count (sheds, garages and footprints under {MIN_FOOTPRINT_M2:.0f} m2 left to the "
        f"rules). A stratum is the style the automatic tier infers (`archetypes.infer`), the decade (an undated "
        f"building takes its neighbours'), the storeys class (1, 2, 3-4, 5-7, 8+) and the area type within 150 m "
        f"(centre, apartments, houses, work, rural): {b['strata']} strata.",
        f"- **Streets**: OSM streets in Turku cut into pieces of at most {SEGMENT_M:.0f} m, {st['km']} km. A stratum "
        f"is the class (main, local, pedestrian), the area type, the age of the buildings along it (-1939, 1940-69, "
        f"1970-99, 2000-, open), trees (OSM's trees, 3 per 100 m) and the footway profile from the city's street "
        f"parts (paved, asphalt, gravel, none, unmapped): {st['strata']} strata.", "",
        "## Coverage before the list", "",
        "| | strata | buildings or km | share |", "| --- | ---: | ---: | ---: |",
        f"| buildings: a stored reading in the stratum | {b['read']['strata']} | {b['read']['buildings']} | {b['read']['share']} % |",
        f"| buildings: known from the data (no reading in the stratum; the automatic tier draws its parent right) | "
        f"{b['data']['strata']} | {b['data']['buildings']} | {b['data']['share']} % |",
        f"| buildings: gap | {b['gap']['strata']} | {b['gap']['buildings']} | {b['gap']['share']} % |",
        f"| streets: a read street view in the stratum | {st['read']['strata']} | {st['read']['km']} km | {st['read']['share']} % |",
        f"| streets: gap | {st['gap']['strata']} | {st['gap']['km']} km | {st['gap']['share']} % |", "",
        f"Readings: {b['readings']} stored buildings (full, hand, converted from the old facade programs, light at "
        f"confidence 0.5 or more), all on city buildings; {st['views_read']} read street views. Known from the data: "
        f"the parent (style, 20-year era) has 3 readings or more and the automatic tier agrees with them on 0.8 or "
        f"more of style group, wall family, storeys, ground storey, roof form and balconies (`observe against`).", "",
        "Parents measured by `against`: " + "; ".join(f"{k} {v['agree']} (n {v['n']}){' known' if v['known'] else ''}"
                                                    for k, v in s["parents"].items() if v["n"] >= 2) + ".", "",
        "## Largest gaps", "", "| buildings | stratum (style · decade · storeys · area) | parent agreement |",
        "| ---: | --- | --- |",
    ]
    for r in [r for r in data["buildings"] if r["status"] == "gap"][:30]:
        parent = r["parent"]
        agreement = f"{parent['agree']} over {parent['n']} ({parent['era']})" if parent else "no reading"
        lines.append(f"| {r['buildings']} | {_key(r['stratum'])} | {agreement} |")
    lines += ["", "| km | street stratum (class · area · age · trees · profile) |", "| ---: | --- |"]
    for r in [r for r in data["streets"] if r["status"] == "gap"][:20]:
        lines.append(f"| {r['length_m'] / 1000:.1f} | {_key(r['stratum'])} |")
    prepared = prepared_coverage(out, data, listing, state) if listing and state else None
    if listing:
        m = listing["summary"]
        lines += ["", "## The list", "",
                  f"{m['readings']} readings: {m['buildings_readings']} full building readings and {m['street_views']} "
                  f"street views (a light reading: the street type, and at a glance the gap buildings it shows). "
                  f"Chosen greedily, largest gap first: a building stratum is worth its share of all gap buildings, a "
                  f"street stratum its share of all gap street length, a street view's glance at a building stratum "
                  f"{LIGHT_WEIGHT} of a full reading (at most {VIEW_STRATA} strata a view). Notional cost about "
                  f"{m['notional_usd']} USD.", "",
                  "| | buildings | streets |", "| --- | ---: | ---: |",
                  f"| before | {m['coverage_before']['buildings_pct']} % | {m['coverage_before']['streets_pct']} % |",
                  f"| after the list | {m['coverage_after']['buildings_pct']} % (full readings alone "
                  f"{m['coverage_after']['buildings_pct_full_readings_only']} %) | {m['coverage_after']['streets_pct']} % |",
                  *([f"| after the picks whose photos are ready ({prepared['ready']} of {m['readings']}), with the "
                     f"buildings each street view really shows | {prepared['buildings_pct']} % (full readings alone "
                     f"{prepared['buildings_pct_full']} %) | {prepared['streets_pct']} % |"] if prepared else []), "",
                  "| id | kind | value % | stratum | where | photo |", "| --- | --- | ---: | --- | --- | --- |"]
        for p in listing["picks"]:
            ready = state.get(p["id"], {})
            if p["kind"] == "building":
                chosen = ready.get("chosen") or {}
                where = f"{p['building']['address'] or p['building']['key']} ({p['building']['district']})"
                photo = (f"{chosen.get('crops')} crops, {chosen.get('street_area_m2')} m2 seen, overcast {chosen.get('overcast')}"
                         + (f", alternate {chosen['key']}" if chosen.get("key") and chosen["key"] != p["building"]["key"] else "")
                         if chosen else ready.get("why") or "not prepared")
            else:
                where = f"{p['piece']['name'] or 'unnamed'} ({p['piece']['district']})"
                photo = (("fresh view " + ready["view"]) if ready.get("fresh") else
                         f"{ready.get('view')} {ready.get('date')}, overcast {ready.get('overcast')}, shows {len(ready.get('shows', []))} buildings"
                         if ready.get("ready") else ready.get("why") or "not prepared")
            lines.append(f"| {p['id']} | {p['kind']} | {p['value_pct']} | {_key(p['stratum'])} | {where} | {photo} |")
    path = out / "coverage.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main(argv=None) -> int:
    import argparse

    from turku3d.world.store import data_root

    ap = argparse.ArgumentParser(prog="python -m turku3d.observe gaps", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["city", "strata", "pick", "prepare", "run", "report"])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--store", type=Path, default=None, help="descriptions root (default the shared store)")
    ap.add_argument("--against", type=Path, default=None,
                    help="buildings.csv of `observe against` (default $TURKU3D_WORK/observe/against/buildings.csv)")
    ap.add_argument("--max", type=int, default=100, help="pick: readings at most")
    ap.add_argument("--host", choices=sorted(HOSTS), help="prepare: on that machine (scripts/dev/on-host)")
    ap.add_argument("--jobs", type=int, default=3, help="prepare: buildings at once; run: readings at once")
    ap.add_argument("--dry-run", action="store_true", help="run: check every input and say what would run")
    ap.add_argument("--budget", type=float, default=120.0, help="run: no reading started once this many USD are spent")
    ap.add_argument("--only", nargs="*", help="run: only these pick ids (gap_NNN)")
    a = ap.parse_args(argv)
    a.out = a.out or config.work_root() / "gaps"
    a.out.mkdir(parents=True, exist_ok=True)
    store = a.store or data_root() / "descriptions"
    if a.step == "city":
        print(json.dumps(city(a.out)), flush=True)
    elif a.step == "strata":
        against_csv = a.against or config.work_root() / "observe/against/buildings.csv"
        print(json.dumps(strata(a.out, store, against_csv), ensure_ascii=False), flush=True)
    elif a.step == "pick":
        print(json.dumps(pick(a.out, a.max), ensure_ascii=False), flush=True)
        print(report(a.out), flush=True)
    elif a.step == "report":
        print(report(a.out), flush=True)
    elif a.step == "prepare" and a.host:
        return prepare_on(a.host, a.out, a.jobs)
    elif a.step == "prepare":
        print(json.dumps(prepare(a.out, config.work_root() / "observe", a.jobs)), flush=True)
    elif a.step == "run":
        return run(a.out, a.dry_run, a.jobs, a.budget, set(a.only) if a.only else None)
    return 0
