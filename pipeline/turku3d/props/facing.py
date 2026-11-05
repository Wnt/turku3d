"""Furniture used from the footway: its front (+Z, the payment side or the face) toward the pedestrian.

``props.orientation.heading`` points the front of every kind without its own rule toward the
nearest carriageway centreline (``nearest_road``). For a parking meter that is right only where
it stands at the back of the footway: an audit of all 50 GIS:Pysakointiautomaatit meters
(Keskusta, VII and VIII districts, Kerttuli, Martti, the hospital and market-hall lots) against
the GIS:Katualueet footway polygons and the sidewalk centrelines found every front toward the
carriageway, and 22 of the 42 meters inside or beside a footway polygon standing on its kerb side,
the payment side over the parking lane: turned about 180 degrees. Info boards, pylons, dispensers
and deep waste containers were turned the same way.

One rule, shared by the furniture stage's tile migration (``migrations.furniture``, which every
generator build ends with) for every kind in KINDS; benches keep ``props.seating`` (lidar, barrier
back, water view), signs and stops face approaching traffic, chargers face the car:

* ``foot_side``: a car street within KERB_REACH and a footpath centreline within PATH_REACH that
  runs along it (within PARALLEL degrees; crossings and side paths do not count): the front is
  square to the street, toward the footway's side of the item. A meter on the kerb edge of the
  footway faces back across it, one at its back edge faces out over it toward the street.
* ``back_to_kerb``: a car street within KERB_REACH without such a footpath, or an item on the
  footpath centreline itself (within ``seating.ON_WAY``): the front faces away from the street.
* ``path_view``: no car street within KERB_REACH: the front faces the nearest footpath within
  PATH_REACH, square to it.
* ``way_view``: else the nearest way within WAY_REACH: the front faces it.
* Without any way in reach the register heading stays (``street_heading``).

Items never move: only their heading changes.
"""

from __future__ import annotations

import math
from collections import Counter

import shapely
import shapely.ops

from turku3d.props.seating import KERB_REACH, ON_WAY, PATH_REACH, WAY_REACH, Ways, _tangent, _toward

KINDS = ("parking_meter", "info_board", "info_pylon", "dispenser", "waste_container")
PARALLEL = 30.0  # deg between a footpath and the street for it to be that street's footway


def _along(ways, car, x, z):
    """Nearest footpath within PATH_REACH of (x, z) that runs along ``car`` there, or None."""
    if ways.path_tree is None:
        return None
    point = shapely.Point(x, z)
    street = _tangent(car, point)
    best = None
    for i in ways.path_tree.query(point.buffer(PATH_REACH)):
        line = ways.paths[int(i)]
        d = line.distance(point)
        tangent = _tangent(line, point)
        if d > PATH_REACH or tangent is None or street is None:
            continue
        if abs(street[0] * tangent[0] + street[1] * tangent[1]) < math.cos(math.radians(PARALLEL)):
            continue
        if best is None or d < best[1]:
            best = (line, d)
    return None if best is None else best[0]


def front(item, ways):
    """(unit front vector, rule) of a footway item, or None without a way in reach."""
    if ways is None:
        return None
    (path, dpath), (car, dcar) = ways.nearest(item.x, item.z)
    hint = (math.sin(item.yaw), math.cos(item.yaw))
    if car is not None and dcar <= KERB_REACH:
        toward = _toward(car, item.x, item.z, hint)
        if toward is None:
            return None
        footway = _along(ways, car, item.x, item.z)
        if footway is not None:
            near = shapely.ops.nearest_points(footway, shapely.Point(item.x, item.z))[0]
            side = (near.x - item.x) * toward[0] + (near.y - item.z) * toward[1]
            if ON_WAY < side < dcar:  # between the item and the street's centreline, not across it
                return toward, "foot_side"
            if side < -ON_WAY:
                return (-toward[0], -toward[1]), "foot_side"
        return (-toward[0], -toward[1]), "back_to_kerb"
    if path is not None and dpath <= PATH_REACH:
        found = _toward(path, item.x, item.z, hint)
        return None if found is None else (found, "path_view")
    if min(dpath, dcar) <= WAY_REACH:
        found = _toward(path if dpath <= dcar else car, item.x, item.z, hint)
        return None if found is None else (found, "way_view")
    return None


def place(props, roads, stats=None):
    """Turn every item of ``props`` whose kind is in KINDS in place (yaw, orientation).

    ``roads`` is the tile's encoded street graph (``inputs.roads["json"]``), or None."""
    stats = Counter() if stats is None else stats
    items = [p for p in props if p.kind in KINDS]
    ways = Ways(roads) if items and roads else None
    for item in items:
        found = front(item, ways)
        if found is None:
            stats["facing_street_heading"] += 1
            continue
        (fx, fz), item.orientation = found
        item.yaw = math.atan2(fx, fz)
        stats[f"facing_{item.orientation}"] += 1
    return props

