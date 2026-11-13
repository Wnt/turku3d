"""The city's milieu typing of its centre as a building fact, and which milieu types are the town.

Turku types the zones of its centre by milieu (WFS ``GIS:Turun_keskustan_miljootyypitys``, field
``Tyyppi``: business centre, historic Turku, open blocks, hill quarters, wooden town, public
buildings, post-war blocks, ...; 33 zones, about 8 km²). ``export`` fetches the layer once into a
city-wide table (``derived/milieus/milieus.json``: data root and farm source overlay, never git);
the tile's appearance dataset reads it and ``coarse.register.enrich`` gives every building the type
of the zone holding it as the register fact ``milieu`` (none outside every zone).

Which types are "the town" is one data file, ``town-milieus.json`` beside this module, with the
reason for each type. Rules whose evidence was photographed in the town apply there only
(``in_town``); changing the set is editing that file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

from turku3d import config as C

SCHEMA = "turku3d-milieus-1"
LAYER, FIELD = "GIS:Turun_keskustan_miljootyypitys", "Tyyppi"
DATASET = Path("derived/milieus/milieus.json")
TOWN_FILE = Path(__file__).with_name("town-milieus.json")
# EPSG:3877 box holding every zone of the typing (it covers the centre only), with margin.
CITY_BOX = (23450000.0, 6692000.0, 23470000.0, 6733000.0)


@lru_cache(maxsize=1)
def town() -> frozenset[str]:
    """The milieu types that are the town (``town-milieus.json``)."""
    return frozenset(json.loads(TOWN_FILE.read_text())["town"])


def in_town(attributes: dict) -> bool:
    return attributes.get("milieu") in town()


def dataset_path(root: Path | None = None) -> Path:
    return (root or C.data_root()) / DATASET


def source_paths(root: Path | None = None) -> list[Path]:
    """The table when the data root (or the farm's source overlay) carries it."""
    path = dataset_path(root)
    return [path] if path.is_file() else []


def zones_from_pages(pages: list[Path]) -> list[dict]:
    """One zone per feature: its id, type and polygon (EPSG:3877 WKT, cm), from WFS GML pages."""
    import shapely

    from turku3d.fetch.wfs import parse_features

    features = {}
    for page in pages:
        for key, _, attrs, rows in parse_features(page.read_bytes(), LAYER):
            kind = attrs.get(FIELD)
            polygons = [
                shapely.make_valid(shapely.force_2d(r["geometry"]))
                for r in rows
                if r["geometry"].geom_type in ("Polygon", "MultiPolygon")
            ]
            if kind and polygons:
                ident = str(attrs.get("ID") or key)
                features.setdefault(ident, (kind, []))[1].extend(polygons)
    zones = []
    for ident, (kind, polygons) in sorted(features.items()):
        shape = shapely.set_precision(shapely.union_all(polygons), 0.01)
        if not shape.is_empty:
            zones.append(dict(id=ident, type=kind, wkt=shape.wkt))
    return zones


def fetch(work: Path, box=CITY_BOX, log=print) -> list[Path]:
    """Quadtree-paged GML pages of the layer over the box, kept in ``work`` (never the raw cache)."""
    from turku3d.fetch.common import Registry
    from turku3d.fetch.sources import WFS_LAYERS
    from turku3d.fetch.wfs import fetch_pages

    layer = next(layer for layer in WFS_LAYERS if layer.name == LAYER)
    pages = Path(work) / "pages"
    return fetch_pages(layer, box, pages, Registry(pages), log=log)


def export(pages: list[Path]) -> dict:
    zones = zones_from_pages(pages)
    digest = hashlib.sha256()
    for page in sorted(pages):
        digest.update(page.read_bytes())
    return dict(
        schema=SCHEMA,
        layer=LAYER,
        field=FIELD,
        crs="EPSG:3877",
        source=dict(pages=len(pages), sha256=digest.hexdigest()),
        count=len(zones),
        zones=zones,
    )


def write(dataset: dict, path: Path) -> Path:
    if "raw" in Path(path).parts:
        raise ValueError("the milieu table is derived data, never written under raw/")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    temp.write_text(json.dumps(dataset, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
    temp.replace(path)
    return path


def read(path: Path) -> list[tuple]:
    """(polygon in the engine's x/z frame, milieu type) of every zone."""
    import shapely

    from turku3d.fetch.wfs import to_local

    dataset = json.loads(Path(path).read_text())
    if dataset.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} dataset")
    shapes = to_local([shapely.from_wkt(zone["wkt"]) for zone in dataset["zones"]])
    return [(shape, zone["type"]) for shape, zone in zip(shapes, dataset["zones"], strict=True)]


def milieu_of(footprint, zones: list[tuple]) -> str | None:
    """The type of the zone holding the footprint's representative point; the smallest where zones overlap."""
    point = footprint.representative_point()
    holding = [(zone.area, kind) for zone, kind in zones if zone.covers(point)]
    return min(holding)[1] if holding else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True, help="directory for the fetched GML pages")
    parser.add_argument("--out", type=Path, help="default: $TURKU3D_DATA/derived/milieus/milieus.json")
    args = parser.parse_args(argv)
    dataset = export(fetch(args.work))
    path = write(dataset, args.out or dataset_path())
    counts = {}
    for zone in dataset["zones"]:
        counts[zone["type"]] = counts.get(zone["type"], 0) + 1
    print(json.dumps(dict(path=str(path), count=dataset["count"], types=counts, source=dataset["source"]),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
