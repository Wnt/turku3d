"""Stair handrails stop at measured flights and connected walking corridors."""

import numpy as np
import shapely
from shapely.ops import substring


def attach(rows, paths):
    """Keep full path widths at stair junctions, including area footways.

    Only ground-level non-step paths supply clearance. A nearby unconnected
    path or a bridge passing above the flight must not remove its guarding.
    """
    from .stairs import number

    corridors = []
    for geom, tags in paths:
        if (
            tags.get("bridge", "no") != "no"
            or tags.get("tunnel", "no") not in ("no", "building_passage")
            or tags.get("layer", "0") not in ("0", "")
            or tags.get("step_count", "0") not in ("0", "")
        ):
            continue
        if geom.geom_type == "LineString" and geom.is_ring and tags.get("area") == "yes":
            geom = shapely.make_valid(shapely.Polygon(geom))
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            area = geom
        elif geom.geom_type in ("LineString", "MultiLineString"):
            width = np.clip(number(tags.get("width"), 3), 0.5, 30)
            area = geom.buffer(width / 2, cap_style="flat", join_style="mitre")
        else:
            continue
        corridors.append((geom, area))
    for _, line, tags in rows:
        joined = [area for geom, area in corridors if geom.distance(line.boundary) < 0.15]
        if joined:
            # Include tube radius so neither the visual nor solid railing
            # protrudes into the walking corridor after centreline clipping.
            tags["_rail_clearance"] = shapely.union_all(joined).buffer(0.06)


def produce(key, side, distances, knots, levels, clearance=None, *, suspended=False):
    from .bridge_details import handrail

    edge = shapely.LineString(side)
    station = np.r_[0, np.cumsum(np.linalg.norm(np.diff(side, axis=0), axis=1))]
    heights = np.interp(distances, knots, levels)
    rail = edge
    if not suspended:
        rising = np.flatnonzero(np.diff(levels) > 0.045)
        if not len(rising):
            return []
        ends = np.interp(knots[[rising[0], rising[-1] + 1]], distances, station)
        rail = substring(edge, *ends)
    if clearance is not None:
        rail = rail.difference(clearance)
    meshes = []
    for part in shapely.get_parts(rail):
        if part.geom_type != "LineString" or part.length < 0.05:
            continue
        coords = np.asarray(part.coords)
        along = np.r_[0, np.cumsum(np.linalg.norm(np.diff(coords, axis=0), axis=1))]
        source = shapely.line_locate_point(edge, shapely.points(coords))
        meshes.extend(handrail(key, part, (along, np.interp(source, station, heights))))
    return meshes
