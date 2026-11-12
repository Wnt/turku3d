"""OpenStreetMap features anywhere in the city, from one table cut once from the national extract.

The city's extent is that of its lidar tile index. `Osm.features(box)` reads rows of
sources/osm/<city>-<date>.parquet; the table is built when it is missing (a few minutes, once per extract date):

1. cut: nodes inside the extent, the ways that use them with all their nodes, and the multipolygon relations of
   those ways with all their members (sources/osm/<city>-<date>.osm.pbf);
2. table: one row per tagged point, line and area with `osm_type`, `osm_id`, every tag in `tags` (JSON), the common
   tags as columns, geometry in the city's survey frame and its box in `e0, n0, e1, n1`, sorted by kilometre square
   so a box read touches a few row groups.

A closed way is an area unless it is tagged as a linear thing (road, barrier, railway, waterway) without `area=yes`.
"""

from __future__ import annotations

import csv
import fcntl
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import shapely

from ... import cities, config
from ...fetch.osm import TAG_COLUMNS

MARGIN = 500.0
LINEAR = ("highway", "barrier", "railway", "waterway")
ROW_GROUP = 4000


def city_extent() -> tuple[float, float, float, float]:
    """The box of the city's lidar tile index (e0, n0, e1, n1)."""
    with open(config.LAZ_TILES_CSV, newline="") as stream:
        rows = [(float(r["xmin"]), float(r["ymin"]), float(r["xmax"]), float(r["ymax"])) for r in csv.DictReader(stream)]
    box = np.array(rows)
    return float(box[:, 0].min()), float(box[:, 1].min()), float(box[:, 2].max()), float(box[:, 3].max())


def cut(national: Path, box_wgs84: tuple[float, float, float, float], out: Path, log=print) -> None:
    """Writes the part of the national file that the box needs (see the module text)."""
    import osmium

    west, south, east, north = box_wgs84
    source = str(national)
    ids = osmium.IdTracker()
    started = time.perf_counter()
    for node in osmium.FileProcessor(source, osmium.osm.NODE):
        if west <= node.lon <= east and south <= node.lat <= north:
            ids.add_node(node.id)
    log(f"  osm cut: nodes in the extent, {time.perf_counter() - started:.0f} s")
    ways = osmium.IdTracker()
    for way in osmium.FileProcessor(source, osmium.osm.WAY).with_filter(ids.contains_filter()):
        ways.add_way(way.id)
        ids.add_way(way.id)
    for relation in osmium.FileProcessor(source, osmium.osm.RELATION).with_filter(ways.contains_filter()):
        if relation.tags.get("type") == "multipolygon":
            ids.add_relation(relation.id)
            ids.add_references(relation)
    ids.complete_backward_references(source, relation_depth=0)
    tmp = out.with_name(f".{out.name}.tmp.osm.pbf")
    tmp.unlink(missing_ok=True)
    with osmium.SimpleWriter(str(tmp)) as writer:
        for thing in osmium.FileProcessor(source).with_filter(ids.id_filter()):
            writer.add(thing)
    tmp.replace(out)
    log(f"  osm cut: {out.name} {out.stat().st_size / 1e6:.0f} MB, {time.perf_counter() - started:.0f} s")


def table(extract: Path, out: Path, crs: str, log=print) -> None:
    """Writes the feature table of an extract (see the module text)."""
    import geopandas as gpd
    import osmium
    import pyarrow.parquet as pq

    started = time.perf_counter()
    kinds, keys, tags, shapes = [], [], [], []
    reader = (osmium.FileProcessor(str(extract)).with_locations().with_areas()
              .with_filter(osmium.filter.EmptyTagFilter()).with_filter(osmium.filter.GeoInterfaceFilter()))
    for thing in reader:
        row = dict(thing.tags)
        linear = any(key in row for key in LINEAR) and row.get("area") != "yes"
        if thing.is_area():
            if thing.from_way() and linear:
                continue
            kind, key = ("way" if thing.from_way() else "relation"), thing.orig_id()
        elif thing.is_way():
            if thing.is_closed() and not linear:
                continue  # its area row follows
            kind, key = "way", thing.id
        elif thing.is_node():
            kind, key = "node", thing.id
        else:
            continue
        kinds.append(kind)
        keys.append(key)
        tags.append(row)
        shapes.append(json.dumps(thing.__geo_interface__["geometry"]))
    frame = gpd.GeoDataFrame(
        {"osm_type": kinds, "osm_id": np.array(keys, dtype=np.int64),
         "tags": [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in tags],
         **{column: [row.get(column) for row in tags] for column in TAG_COLUMNS}},
        geometry=shapely.from_geojson(shapes), crs=config.CRS_WGS84).to_crs(crs)
    frame = frame[~(frame.geometry.is_empty | frame.geometry.isna())]
    bounds = frame.geometry.bounds
    for name, column in zip(("e0", "n0", "e1", "n1"), ("minx", "miny", "maxx", "maxy")):
        frame[name] = bounds[column].values
    square = np.floor((frame.e0 + frame.e1) / 2000).astype(np.int64) * 100000 + np.floor((frame.n0 + frame.n1) / 2000).astype(np.int64)
    frame = frame.iloc[np.argsort(square.values, kind="stable")].reset_index(drop=True)
    tmp = out.with_name(f".{out.name}.tmp")
    frame.to_parquet(tmp, index=False, row_group_size=ROW_GROUP)
    tmp.replace(out)
    count = pq.ParquetFile(out).metadata.num_rows
    log(f"  osm table: {out.name} {count} features, {out.stat().st_size / 1e6:.0f} MB, {time.perf_counter() - started:.0f} s")


class Osm:
    def __init__(self, cache: Path, sources):
        self.cache = cache
        self.sources = sources

    def national(self) -> Path:
        """The newest national extract: the raw mirror's, or one downloaded into the cache."""
        found = sorted([*(config.raw_dir("osm") / "geofabrik").glob("*.osm.pbf"), *self.cache.glob("national-*.osm.pbf")],
                       key=lambda path: path.name.rsplit("-", 1)[1])
        if found:
            return found[-1]
        out = self.cache / f"national-{time.strftime('%y%m%d')}.osm.pbf"
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_name(f".{out.name}.part")
        url = cities.current().osm_url
        self.sources.log(f"  fetching {url}")
        subprocess.run(["curl", "-fL", "--retry", "3", "-C", "-", "--max-time", "3600", "-sS", "-o", str(part), url], check=True)
        part.replace(out)
        return out

    def path(self) -> Path:
        """The city's feature table, built when it is missing (one builder at a time; the others wait for it)."""
        name = cities.current().name
        found = sorted(self.cache.glob(f"{name}-*.parquet"))
        if found:
            return found[-1]
        from pyproj import Transformer

        self.cache.mkdir(parents=True, exist_ok=True)
        with open(self.cache / f".{name}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            found = sorted(self.cache.glob(f"{name}-*.parquet"))
            if found:
                return found[-1]
            national = self.national()
            date = national.name.rsplit("-", 1)[1].split(".")[0]
            e0, n0, e1, n1 = city_extent()
            to_wgs = Transformer.from_crs(config.CRS_HORIZONTAL, config.CRS_WGS84, always_xy=True)
            lon, lat = to_wgs.transform([e0 - MARGIN, e1 + MARGIN, e0 - MARGIN, e1 + MARGIN], [n0 - MARGIN, n0 - MARGIN, n1 + MARGIN, n1 + MARGIN])
            extract = self.cache / f"{name}-{date}.osm.pbf"
            if not extract.exists():
                cut(national, (min(lon), min(lat), max(lon), max(lat)), extract, self.sources.log)
            out = self.cache / f"{name}-{date}.parquet"
            table(extract, out, config.CRS_HORIZONTAL, self.sources.log)
            return out

    def features(self, box: tuple[float, float, float, float]):
        """Rows whose geometry intersects the survey box (e0, n0, e1, n1): a GeoDataFrame in the survey frame."""
        import geopandas as gpd

        with self.sources.timed("osm"):
            frame = gpd.read_parquet(self.path(), filters=[("e1", ">=", box[0]), ("e0", "<=", box[2]), ("n1", ">=", box[1]), ("n0", "<=", box[3])])
            return frame[frame.geometry.intersects(shapely.box(*box))].reset_index(drop=True)
