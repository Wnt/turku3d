"""Street graphs of a location: what the game's start pose, map, routes, traffic, walkers and kerb parking read.

The old generator's graph, not a new one: `turku3d.roads.tile_graph` builds `turku3d-roadgraph` v1 revision 2
(docs/specs/roads-json-v2.md: lanes, widths, speeds, junctions, signals, crossings, stable edge ids) per 250 m tile
of the game's streaming grid, and every consumer in the client already reads that per tile. Here the same code
runs on the world pipeline's OpenStreetMap table, for each 250 m tile the location's ground touches, cut to the
ground cells the location has (a street ends in a seam node where the location ends, as at a tile seam), with
heights sampled from the ground entities' own rasters. Not carried over yet: the centring of car edges on the surveyed carriageway
(`roads.centring`) and bridge-deck levels.

`location_roads` returns {tile id `t0_<i>_<j>`: {url, sha256, bytes, nodes, edges, carEdges}}, the manifest's `roads`.
"""

from __future__ import annotations

import json
import math

import numpy as np

from ..config import BBoxLocal
from .collision import CELL, cell_of
from .frame import ORIGIN

TILE = 250.0


class GroundHeights:
    """Height (engine y) of the location's ground at engine x, z: the nearest sample of the ground entity that owns
    the 62.5 m cell; NaN where the location has no ground."""

    def __init__(self, store, rows: list[dict]):
        self.store = store
        self.rows = {cell_of(row["origin"][0] + 1, row["origin"][2] + 1): row for row in rows if row["kind"] == "ground" and "heightfield" in row}
        self.rasters: dict[tuple[int, int], np.ndarray] = {}

    def box(self) -> tuple[float, float, float, float]:
        i = [cell[0] for cell in self.rows]
        j = [cell[1] for cell in self.rows]
        return min(i) * CELL, min(j) * CELL, (max(i) + 1) * CELL, (max(j) + 1) * CELL

    def __call__(self, x: float, z: float) -> float:
        cell = cell_of(x, z)
        row = self.rows.get(cell)
        if row is None:
            # A node on the outer edge of the last cell belongs to the cell inside.
            cell = cell_of(x - 0.01, z - 0.01)
            row = self.rows.get(cell)
            if row is None:
                return math.nan
        field = row["heightfield"]
        if cell not in self.rasters:
            columns, lines = field["size"]
            self.rasters[cell] = np.frombuffer((self.store.root / field["url"]).read_bytes(), "<f4").reshape(lines, columns)
        raster = self.rasters[cell]
        c = min(max(round((x - field["origin"][0]) / field["spacing"]), 0), raster.shape[1] - 1)
        r = min(max(round((z - field["origin"][1]) / field["spacing"]), 0), raster.shape[0] - 1)
        return float(raster[r, c])


def features_local(sources, box: tuple[float, float, float, float]):
    """OSM features around the engine box as `tile_graph.graph_inputs` takes them (geometry in engine x/z)."""
    import shapely

    from turku3d.roads.tile_graph import POINT_CONTEXT

    x0, z0, x1, z1 = box
    reach = POINT_CONTEXT + 10.0
    rows = sources.osm.features((ORIGIN[0] + x0 - reach, ORIGIN[1] - z1 - reach, ORIGIN[0] + x1 + reach, ORIGIN[1] - z0 + reach))
    rows = rows[rows.geometry.geom_type.isin(["Point", "LineString", "Polygon", "MultiPolygon"])]
    local = shapely.transform(rows.geometry.values, lambda c: np.column_stack([c[:, 0] - ORIGIN[0], ORIGIN[1] - c[:, 1]]))
    for geom, tags, highway, osm_type, osm_id in zip(local, rows["tags"], rows["highway"], rows["osm_type"], rows["osm_id"], strict=True):
        highway = highway if isinstance(highway, str) and highway != "nan" else None
        yield geom, (json.loads(tags) if isinstance(tags, str) and tags else {}), highway, osm_type, (osm_id if osm_id == osm_id else None)


def within_ground(ways, points, crossings, ground):
    """The graph inputs cut to the ground the location has: no street runs where nothing carries a car."""
    import shapely

    from turku3d.roads.tile_graph import Way

    kept = []
    for way in ways:
        if len(way.coords) < 2:
            continue
        for piece in shapely.get_parts(shapely.LineString(way.coords).intersection(ground)):
            if piece.geom_type == "LineString" and piece.length >= 0.1:
                kept.append(Way([(x, z) for x, z in piece.coords], way.flags, way.way, way.highway, way.tags))
    inside = shapely.contains_xy(ground, [p[0] for p in points], [p[1] for p in points]) if points else []
    return kept, [p for p, keep in zip(points, inside, strict=True) if keep], [c for c in crossings if c[0].intersects(ground)]


def location_roads(store, rows: list[dict], sources, log=print) -> dict:
    import shapely

    from turku3d.roads import CAR, encode
    from turku3d.roads.tile_graph import NODE_FLAGS, build_tile_graph, graph_inputs, validate, with_heights

    heights = GroundHeights(store, rows)
    if not heights.rows:
        return {}
    x0, z0, x1, z1 = heights.box()
    ground = shapely.union_all([shapely.box(i * CELL, j * CELL, (i + 1) * CELL, (j + 1) * CELL) for i, j in heights.rows])
    ways, points, crossings = within_ground(*graph_inputs(features_local(sources, (x0, z0, x1, z1))), ground)
    edge_of_ground = ground.boundary
    out, edges, car, leaving = {}, 0, 0, 0
    for i in range(math.floor(x0 / TILE), math.ceil(x1 / TILE)):
        for j in range(math.floor(z0 / TILE), math.ceil(z1 / TILE)):
            box = BBoxLocal(max(i * TILE, x0), max(j * TILE, z0), min((i + 1) * TILE, x1), min((j + 1) * TILE, z1))
            graph = build_tile_graph(ways, points, crossings, box, f"location-t0_{i}_{j}", TILE)
            if not graph["edges"]:
                continue
            # A street cut by the location's edge leaves the loaded area there, as at a tile seam: it is no dead end.
            nodes, flags = graph["nodes"], graph["nodeFlags"]
            for k, flag in enumerate(flags):
                if flag & NODE_FLAGS["DEAD_END"] and edge_of_ground.distance(shapely.Point(nodes[2 * k], nodes[2 * k + 1])) < 0.25:
                    flags[k] = flag & ~NODE_FLAGS["DEAD_END"] | NODE_FLAGS["SEAM"]
                    leaving += 1
            validate(graph)
            raw = with_heights({"json": encode(graph)}, heights)["json"]
            cars = sum(1 for edge in graph["edges"] if edge[2] & CAR)
            out[f"t0_{i}_{j}"] = {**store.put(raw, "json"), "nodes": len(nodes) // 2, "edges": len(graph["edges"]), "carEdges": cars}
            edges += len(graph["edges"])
            car += cars
    log(f"  roads     {len(out)} tiles, {edges} edges ({car} for cars), {leaving} streets leave the location, {sum(v['bytes'] for v in out.values()) / 1e6:.2f} MB")
    return out
