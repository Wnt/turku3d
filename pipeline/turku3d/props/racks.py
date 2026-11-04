"""Bike racks and city-bike stations in a row along the nearest way.

The rack model (``props.models``) is a row of inverted-U stands along its local X; each U stands
in its local YZ plane, and the station's terminal faces +Z. Register points (GIS:Polkupyoraparkki,
GIS:Follariasemat, OSM bicycle_parking) carry no heading. Before this rule, ``props.orientation``
pointed +Z along the kerb, so every row ran square to the kerb with each U's plane along it: all
13 of Jonni's rack and station edits (sessions 20261103-222447-fnf9i8 and 20261104-201907-o2bcyj)
turn the row by 76-93 degrees, and the survey's elongated returns at racks (stands with parked
bikes) run along the way, 85-88 degrees off the old row and within 8 degrees of his.

One rule, shared by the furniture stage (``coarse.furniture.produce``) and the ``racks`` tile
migration, so a regeneration and a migrated tile turn every rack alike:

* ``rack_path_row``: a footpath within PATH_REACH, nearer than the car street (or the car street
  past KERB_REACH): the row runs along the path;
* ``rack_kerb_row``: else a car street within KERB_REACH: the row runs along it;
* ``rack_way_row``: else the nearest way within WAY_REACH;
* ``rack_kerb_row`` from the register heading without a way in reach: ``props.orientation``'s
  ``kerb_parallel`` (+Z along the nearest road) turned a quarter, so the row runs along that road;
  any other register heading stays (``rack_street_heading``).

The furniture stage itself is not edited: ``coarse/furniture.py`` is in the import closure of the
``floodlights@1`` migration, so touching it would give every later step (``wires@2``: 3-7 min per
surveyed tile) a new generation city-wide. Every generator build ends with the migration chain
(``coarse.build``), so a fresh build and a migrated tile both turn racks through ``place`` here.

The row's front (+Z: the station terminal) faces the car street within KERB_REACH, else the way it
lines up with. Racks never move: only their heading changes.
"""

from __future__ import annotations

import math
from collections import Counter

import shapely
import shapely.ops

from turku3d.props.seating import KERB_REACH, PATH_REACH, WAY_REACH, Ways, _tangent

KINDS = ("bike_rack", "bike_station")
ON_WAY = 0.3  # m: a rack this close to a way's centreline keeps its old front side


def _front(line, x, z, tangent, hint):
    """Unit normal of ``tangent`` pointing from (x, z) toward ``line``; ``hint`` picks the side on it."""
    nx, nz = -tangent[1], tangent[0]
    near = shapely.ops.nearest_points(line, shapely.Point(x, z))[0]
    dx, dz = near.x - x, near.y - z
    side = hint[0] * nx + hint[1] * nz if math.hypot(dx, dz) < ON_WAY else dx * nx + dz * nz
    return (nx, nz) if side >= 0 else (-nx, -nz)


def row_heading(rack, ways):
    """(yaw, rule) of a rack row along its way, or None without a way within WAY_REACH."""
    if ways is None:
        return None
    (path, dpath), (car, dcar) = ways.nearest(rack.x, rack.z)
    if path is not None and dpath <= PATH_REACH and (dpath <= dcar or dcar > KERB_REACH):
        line, rule = path, "rack_path_row"
    elif car is not None and dcar <= KERB_REACH:
        line, rule = car, "rack_kerb_row"
    elif min(dpath, dcar) <= WAY_REACH:
        line, rule = (path, "rack_way_row") if dpath <= dcar else (car, "rack_way_row")
    else:
        return None
    point = shapely.Point(rack.x, rack.z)
    tangent = _tangent(line, point)
    if tangent is None:
        return None
    hint = (math.sin(rack.yaw), math.cos(rack.yaw))
    fx, fz = _front(car if dcar <= KERB_REACH else line, rack.x, rack.z, tangent, hint)
    # three.js rotation.y: local +Z -> (sin yaw, cos yaw), local +X -> (cos yaw, -sin yaw) = the tangent.
    return math.atan2(fx, fz), rule


def place(props, roads, stats=None):
    """Turn every rack and station of ``props`` in place (yaw, orientation); other kinds untouched.

    ``roads`` is the tile's encoded street graph (``inputs.roads["json"]``), or None."""
    stats = Counter() if stats is None else stats
    racks = [p for p in props if p.kind in KINDS]
    ways = Ways(roads) if racks and roads else None
    for rack in racks:
        found = row_heading(rack, ways)
        if found is None and rack.orientation == "kerb_parallel":
            found = rack.yaw - math.pi / 2, "rack_kerb_row"  # local +X onto the old +Z (the kerb)
        if found is None:
            stats["rack_street_heading"] += 1
            continue
        rack.yaw, rack.orientation = found
        stats[rack.orientation] += 1
    return props
