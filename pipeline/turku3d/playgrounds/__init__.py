"""Playground sites and their equipment, imported from Jonni's playground survey (leikkipuistodata).

The survey project (osgallery-dev ``~/leikkipuistodata``) holds, per register site in Turku and
Kaarina, the playground polygon (WGS84), equipment entries with counts from several sources, lidar
heights of tall structures, and located equipment outlines in EPSG:3877 metres
(``inventory/objects/<site>.json``). ``import_survey`` turns those files into one normalised
dataset: sites with EPSG:3877 polygons, located objects, and the equipment counts that no outline
accounts for (``unlocated``). The dataset is private input data: it lives in the shared data root
(``derived/playgrounds/playgrounds.json``) and in the farm's source overlay, never in git.

``prepare`` reads the sites over one tile (engine coordinates) for the coarse generator.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from turku3d import config as C

SCHEMA = "turku3d-playgrounds-1"
DATASET = Path("derived/playgrounds/playgrounds.json")
# Equipment that stands on the ground as a model. Toddler areas, accessibility notes and
# toilets are facts about a site, not objects to place.
PLACED = (
    "swing", "basket_swing", "baby_swing", "slide", "climbing", "net", "tower", "sandbox",
    "spring", "carousel", "seesaw", "zipline", "trampoline", "balance", "gym", "ballcourt",
    "skate", "water", "shelter",
)
MIN_CONFIDENCE = 0.5  # survey entries below this are guesses
PLACEHOLDER_AREA = 150.0  # m²: register "polygons" this small are circles around the point
OBJECT_FILE = re.compile(r"^[A-Z]+\d+\.json$")
# Survey outlines filed as "other" whose Finnish label names a placed kind (first match wins).
# Painted ground games, buildings and sand-play tables stay unplaced.
OTHER_LABELS = (
    ("piknik", "table"),
    ("penkki", "bench"),
    ("maali", "ballcourt"),
    ("kori", "ballcourt"),
    ("leikkimökki", "shelter"),
    ("keinu", "swing"),
    ("liuku", "slide"),
    ("jousi", "spring"),
    ("leikkiväline", "climbing"),
    ("leikkiteline", "climbing"),
    ("leikkirakenne", "climbing"),
    ("leikkikehikko", "climbing"),
    ("leikkivene", "climbing"),
    ("leikkiajoneuvo", "spring"),
)


def dataset_path(root: Path | None = None) -> Path:
    return (root or C.data_root()) / DATASET


def _ring(points):
    import numpy as np

    lon, lat = np.asarray(points, float).T
    e, n = C.wgs84_to_en(lat, lon)
    return [[round(float(a), 2), round(float(b), 2)] for a, b in zip(e, n)]


def _polygons(geometry):
    """WGS84 GeoJSON Polygon/MultiPolygon -> list of polygons, each a list of EPSG:3877 rings."""
    if not geometry:
        return []
    parts = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return [[_ring(ring) for ring in part] for part in parts]


def _area(polygons):
    import shapely

    return sum(shapely.Polygon(p[0], p[1:]).area for p in polygons)


def object_category(category: str, label: str) -> str | None:
    if category != "other":
        return category if category in PLACED else None
    label = label.lower()
    if "hiekkaleik" in label:
        return None
    return next((kind for key, kind in OTHER_LABELS if key in label), None)


# The aerial inventory counts swing seats; a frame carries about two.
SEATED = ("swing", "baby_swing")
SEATS = (("yhden", 1), ("yksi", 1), ("kahden", 2), ("kaksi", 2), ("kolmen", 3), ("kolme", 3),
         ("neljän", 4), ("neljä", 4), ("monipaikka", 4), ("pitkä", 4))
OSM_NEAR = 30.0  # m: an OSM feature matched only as "nearest" counts when this close
OSM_DETAIL = re.compile(r"\((\d+) m from site point, (\w+)\)")


def seats(label: str) -> int:
    """Seats of a swing frame from its Finnish label; two when it does not say."""
    label = (label or "").lower()
    return next((n for key, n in SEATS if key in label), 2)


def expected_counts(equipment) -> dict[str, int]:
    """Objects (swing seats for SEATED) per category the survey sources agree on: the aerial
    inventory's counts, one per OSM feature in or near the site (its capacity in seats), and one
    for a mention by any other source; the largest source wins."""
    by_source = defaultdict(Counter)
    for entry in equipment:
        category = entry.get("category")
        if category not in PLACED or (entry.get("confidence") or 0) < MIN_CONFIDENCE:
            continue
        source = entry.get("source", "")
        if source == "ortho-inventory":
            by_source[source][category] += max(int(entry.get("count") or 1), 1)
        elif source == "osm":
            match = OSM_DETAIL.search(entry.get("detail", ""))
            if match and match.group(2) != "polygon" and int(match.group(1)) > OSM_NEAR:
                continue
            capacity = re.search(r'"capacity": "(\d+)"', entry.get("detail", ""))
            by_source[source][category] += (
                int(capacity.group(1)) if capacity else 2) if category in SEATED else 1
        else:
            by_source[source][category] = max(by_source[source][category], 1)
    result = Counter()
    for counts in by_source.values():
        for category, n in counts.items():
            result[category] = max(result[category], n)
    return dict(sorted(result.items()))


def unlocated_counts(expected: dict, objects: list) -> dict[str, int]:
    """Expected objects no outline accounts for. Swing seats become frames: with a located frame
    a single extra seat is taken to hang on it."""
    found = Counter(o["category"] for o in objects)
    result = {}
    for category, n in expected.items():
        if category in SEATED:
            have = sum(seats(o["label"]) for o in objects if o["category"] == category)
            missing = max(0, n - have)
            frames = missing // 2 if found[category] else -(-missing // 2)
        else:
            frames = n - found[category]
        if frames > 0:
            result[category] = frames
    return result


def import_survey(root: Path) -> dict:
    """The normalised dataset from a leikkipuistodata checkout."""
    root = Path(root)
    files = [root / "app/data/sites.json", root / "app/data/categories.json"]
    sites = json.loads(files[0].read_text())
    categories = json.loads(files[1].read_text())
    objects_dir = root / "inventory/objects"
    located = {}
    for path in sorted(objects_dir.iterdir()):
        if OBJECT_FILE.match(path.name):
            files.append(path)
            result = json.loads(path.read_text()).get("result") or {}
            located[path.stem] = result.get("objects") or []
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    out_sites, totals, unlocated_totals = [], Counter(), Counter()
    for site in sites:
        polygons = _polygons(site.get("polygon"))
        objects = []
        for k, obj in enumerate(located.get(site["id"], ())):
            category = object_category(obj.get("category", ""), obj.get("label", ""))
            ring = obj.get("polygon") or []
            if category is None or len(ring) < 3:
                continue
            objects.append(dict(
                id=f"{site['id']}:{k}", category=category, label=obj.get("label", ""),
                polygon=[[round(float(e), 2), round(float(n), 2)] for e, n in ring],
                confidence=obj.get("confidence"),
            ))
        found = Counter(o["category"] for o in objects)
        expected = expected_counts(site.get("equipment") or ())
        unlocated = unlocated_counts(expected, objects)
        if site.get("pools") and not found["water"]:
            unlocated["water"] = max(unlocated.get("water", 0), 1)
        totals.update(found)
        unlocated_totals.update(unlocated)
        area = _area(polygons)
        out_sites.append(dict(
            id=site["id"], name=site.get("name", ""), kind=site.get("kind", ""),
            municipality=site.get("municipality", ""),
            point=[round(float(site["x"]), 2), round(float(site["y"]), 2)],
            polygons=polygons, area_m2=round(area, 1), placeholder=area < PLACEHOLDER_AREA,
            surface=site.get("surface_aerial"),
            objects=objects, expected=expected, unlocated=dict(sorted(unlocated.items())),
            big_structures=[
                {k: s.get(k) for k in ("label", "height_m", "top_m", "footprint_m2", "source")}
                for s in site.get("big_structures") or () if s.get("status", "present") == "present"
            ],
            pools=[{"name": p.get("name"), "point": [p["x"], p["y"]]} for p in site.get("pools") or ()
                   if "x" in p and "y" in p],
        ))
    return dict(
        schema=SCHEMA, crs="EPSG:3877",
        source=dict(project="leikkipuistodata", sha256=digest.hexdigest(), files=len(files)),
        categories=[{k: c[k] for k in ("id", "fi", "en")} for c in categories if c["id"] in PLACED],
        counts=dict(sites=len(out_sites), objects=sum(totals.values()),
                    located=dict(sorted(totals.items())), unlocated=dict(sorted(unlocated_totals.items()))),
        sites=out_sites,
    )


def write(dataset: dict, path: Path) -> Path:
    if "raw" in Path(path).parts:
        raise ValueError("the playground dataset is derived data, never written under raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    temp.write_text(json.dumps(dataset, ensure_ascii=False, separators=(",", ":")) + "\n")
    temp.replace(path)
    return path


def source_paths(root: Path | None = None) -> list[Path]:
    """The dataset file when the data root (or the farm's source overlay) carries it."""
    path = dataset_path(root)
    return [path] if path.is_file() else []


def _local(points):
    return [(e - C.ORIGIN_E, -(n - C.ORIGIN_N)) for e, n in points]


def prepare(tile: C.VisualTile, path: Path, margin: float = 30.0) -> list[dict]:
    """Sites whose polygons or objects reach the tile, in engine x/z (z = south)."""
    import shapely

    data = json.loads(Path(path).read_text())
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unknown playground schema {data.get('schema')!r}")
    box = shapely.box(*tile.bbox.buffered(margin).as_tuple())
    result = []
    for site in data["sites"]:
        polygons = [shapely.Polygon(_local(p[0]), [_local(r) for r in p[1:]]) for p in site["polygons"]]
        objects = [{**o, "polygon": _local(o["polygon"])} for o in site["objects"]]
        point = _local([site["point"]])[0]
        shapes = [*polygons, *(shapely.Polygon(o["polygon"]) for o in objects), shapely.Point(point)]
        if not any(box.intersects(s) for s in shapes):
            continue
        result.append({
            **{k: site[k] for k in ("id", "name", "kind", "municipality", "area_m2", "placeholder",
                                     "surface", "unlocated", "big_structures")},
            "point": point,
            "polygons": [shapely.make_valid(p) for p in polygons],
            "objects": objects,
            "pools": [{"name": p["name"], "point": _local([p["point"]])[0]} for p in site["pools"]],
        })
    return result
