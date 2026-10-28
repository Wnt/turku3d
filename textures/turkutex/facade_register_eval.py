"""Apply the draft's optional OSM register evidence to held-out facade features.

The feature extractor starts from the older WFS-only baseline. This stage reads the
same optional OSM and register inputs as draft buildings, then writes a separate
feature file for scoring against the current production fallback.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow.parquet as pq
import shapely
from turku3d import config as C
from turku3d.coarse.register import enrich, read_osm, read_register
from turku3d.coarse.sources import Sources

from .facade_fast import write_json
from .streetview.facades import load_facades
from .streetview.visibility import file_bounds, overlaps, source_files


def local_footprint(polygons):
    """Map EPSG:3877 E/N polygons to the draft's x/z footprint coordinates."""
    geometry = shapely.union_all(polygons)
    return shapely.transform(
        geometry,
        lambda coordinates: np.column_stack(
            [coordinates[:, 0] - C.ORIGIN_E, C.ORIGIN_N - coordinates[:, 1]]
        ),
    )


def footprints(walls) -> dict:
    ids = {wall.props["building_id"] for wall in walls}
    xy = np.array([wall.centre for wall in walls])
    bounds = (xy[:, 0].min() - 50, xy[:, 1].min() - 50, xy[:, 0].max() + 50, xy[:, 1].max() + 50)
    ground = defaultdict(list)
    seen = set()
    for record in source_files():
        if not (bbox := file_bounds(record)) or not overlaps(bbox, bounds):
            continue
        schema = pq.read_schema(record[0])
        if not {"geometry", "surface_type", "pysyvarakennustunnus"}.issubset(schema.names):
            continue
        rows = pq.read_table(
            record[0],
            columns=["geometry", "surface_type", "pysyvarakennustunnus"],
            filters=[("surface_type", "=", "GroundSurface")],
        ).to_pylist()
        for row in rows:
            bid = str(row["pysyvarakennustunnus"])
            if bid not in ids or (bid, row["geometry"]) in seen:
                continue
            seen.add((bid, row["geometry"]))
            polygon = shapely.force_2d(shapely.from_wkb(row["geometry"]))
            if polygon.geom_type == "Polygon":
                ground[bid].append(polygon)
    return {bid: local_footprint(polygons) for bid, polygons in ground.items()}


def run(args):
    walls = load_facades(args.truth / "candidates.geojson")
    rows = json.loads(args.features.read_text())
    missing = [wall.id for wall in walls if wall.id not in rows]
    if missing:
        raise ValueError(f"{len(missing)} truth walls lack extracted features")
    shapes = footprints(walls)
    sources = Sources()

    @cache
    def tile_inputs(tile):
        osm_path = sources.osm_source(tile)
        osm = read_osm(tile, osm_path) if osm_path else {}
        paths = [
            path
            for name in ("kanta:Rakennus", "GIS:Rakennukset")
            for found, path in [sources.wfs_source(name, tile)]
            if found and path
        ]
        return osm, read_register(paths) if paths else {}

    changes = defaultdict(int)
    for wall in walls:
        fid, bid = wall.id, wall.props["building_id"]
        if bid not in shapes:
            changes["missing_footprint"] += 1
            continue
        tile = C.VisualTile.at(wall.centre[0] - C.ORIGIN_E, C.ORIGIN_N - wall.centre[1])
        osm, register = tile_inputs(tile)
        row = rows[fid]
        building = SimpleNamespace(
            footprint=shapes[bid], attributes=dict(row["attributes"]), facade_key=bid
        )
        enrich(building, register, osm)
        for key in ("material_class", "wall_colour", "levels", "ground_floor", "blank"):
            if building.attributes.get(key) != row["attributes"].get(key):
                changes[key] += 1
        row["attributes"] = building.attributes
    result = dict(
        walls=len(walls),
        footprints=len(shapes),
        source_tiles=tile_inputs.cache_info().currsize,
        changed=dict(changes),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "features.json", rows)
    write_json(args.output / "register-enrichment.json", result)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.truth, args.features, args.output = (
        p.resolve() for p in (args.truth, args.features, args.output)
    )
    if args.features == args.output / "features.json":
        raise ValueError("register enrichment must write to a separate output directory")
    run(args)


if __name__ == "__main__":
    main()
