"""Read cached register/footprint context for characterized buildings only."""

import math
import re

import pyarrow.parquet as pq
import shapely
from turku3d import config as C
from turku3d.coarse.register import district


def enrich(programs):
    by_id = {str(p["building_id"]): p for p in programs}
    footprints = {}
    for path in sorted((C.data_root() / "raw/wfs").rglob("bldg_Building_LOD2.parquet")):
        names = pq.read_schema(path).names
        columns = [
            k
            for k in (
                "feature_key",
                "pysyvarakennustunnus",
                "geometry_local",
                "usage",
                "function",
                "kiinteistotunnus",
            )
            if k in names
        ]
        pieces = {}
        for batch in pq.ParquetFile(path).iter_batches(columns=columns):
            for row in batch.to_pylist():
                key = str(row.get("pysyvarakennustunnus") or row.get("feature_key"))
                if key not in by_id:
                    continue
                a = by_id[key]["attributes"]
                a["use"] = row.get("usage") or row.get("function") or a.get("use")
                parcel = row.get("kiinteistotunnus")
                if parcel:
                    a["district"] = district(parcel)
                    a["block"] = (
                        str(parcel)[:9]
                        if str(parcel).isdigit()
                        else "-".join(str(parcel).split("-")[:3])
                    )
                if key not in footprints and row.get("geometry_local"):
                    pieces.setdefault(key, []).append(row["geometry_local"])
        for key, pieces_wkb in pieces.items():
            footprints[key] = shapely.union_all(
                shapely.make_valid(shapely.force_2d(shapely.from_wkb(pieces_wkb)))
            )
    roads = {}
    for path in sorted((C.data_root() / "raw/osm").rglob("*.parquet")):
        names = pq.read_schema(path).names
        if not {"tags", "geometry_local"}.issubset(names):
            continue
        import json

        for batch in pq.ParquetFile(path).iter_batches(columns=["tags", "geometry_local"]):
            for row in batch.to_pylist():
                tags = (
                    json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"] or {}
                )
                if (
                    tags.get("highway")
                    in (
                        "primary",
                        "secondary",
                        "tertiary",
                        "residential",
                        "living_street",
                        "service",
                        "pedestrian",
                        "unclassified",
                    )
                    and row["geometry_local"]
                ):
                    roads[row["geometry_local"]] = tags
    lines = [shapely.from_wkb(wkb) for wkb in roads]
    tags = list(roads.values())
    tree = shapely.STRtree(lines)
    for key, p in by_id.items():
        a = p["attributes"]
        a["construction_year"] = a.get("construction_year") or a.get("year")
        a["street"] = re.sub(r"\s+\d.*$", "", a.get("address", "")).strip().casefold() or None
        wall = max(p["walls"], key=lambda w: w["width_m"])
        a["frontage_m"] = wall["width_m"]
        geom = wall.get("geometry", {})
        if "p0" in geom:
            a["E"] = (geom["p0"][0] + geom["p1"][0]) / 2
            a["N"] = (geom["p0"][1] + geom["p1"][1]) / 2
        polygon = footprints.get(key)
        if polygon is None or polygon.area <= 0:
            continue
        a.update(
            E=polygon.centroid.x + C.ORIGIN_E,
            N=C.ORIGIN_N - polygon.centroid.y,
            log_area=math.log(polygon.area),
            footprint_m2=polygon.area,
            compactness=4 * math.pi * polygon.area / polygon.length**2,
        )
        nearby = tree.query(polygon, predicate="dwithin", distance=25)
        if len(nearby):
            nearest = min(nearby, key=lambda i: lines[i].distance(polygon))
            a["street_class"] = tags[nearest]["highway"]
            named = {
                tags[i].get("name")
                for i in nearby
                if tags[i].get("name") and lines[i].distance(polygon) < 15
            }
            a["corner"] = len(named) > 1
    return programs
