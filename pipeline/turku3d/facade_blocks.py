"""Blocks of flats: balconies on the sun-facing tall walls, by period; single-light windows since 1980.

Without a reading or description a block of flats got the sampled archetype's balconies: a random
draw per building (18-55 % by archetype) that painted flat rails on every second bay of EVERY wall,
whatever it faced, with no depth. The judge flagged missing balconies on proven at passage-01,
blocks-02 and blocks-03, and the photographs show balconies as the main relief of such a block.

W2's readings rule (w2-rules 6c0a8090 on local/loop-integration, ``world/kinds/building/archetypes``
``block()``, ``wall_roles()``, ``balcony()``), ported to proven's facade programs:

- Which walls: every tall wall (at least 9 m and 0.6 of the longest, at least 7.5 m high, standing on
  the ground on the footprint's outline) facing within 45 degrees of the block's sunniest such wall
  (nearest south-west). Of the read walls of blocks facing within 45 degrees of the sunniest one, 22
  of 31 have balconies; of those facing away, 9 of 26, which the rule leaves without.
- Which blocks and what type, by the decade (the readings count balconies on seven in ten slab blocks,
  five in six towers, three in four later blocks; the pick is by the register id, as in W2, so the
  same blocks carry them): 1940-1959 six in ten, small cantilevered slabs with a sheet front every
  third bay; 1960-1979 nine in ten, half recessed loggias, half cantilevers, with concrete fronts,
  every second bay; 1980-1999 three in four, glazed with a solid front; since 2000 nine in ten, glazed
  with a glass front. None before 1940 (the town's stone houses have their own vocabulary).
- Windows: since 1980 the street wall's commonest window is one light (7 of 15 read blocks of
  1980-2022; a vent leaf 3), about 1.4 m wide, 1.5 m tall in the eighties, 1.7 m since 2000.

Against the readings W2's automatic tier drew the balconies right on 139 -> 155 of 184 read buildings
with this rule (and the instrument fix); lights 75 -> 79, window width 66 -> 73.

Scope: every block of flats in the city (register class residential, at least three register storeys,
dated 1940 or later), because the 36 full readings behind the rule are from the town and the
suburbs alike. Ranked below AI descriptions, W2 reading rows and reviewed walls: a described or
read building and every reviewed wall keep what they have.

Geometry is real and cheap: per balcony storey a slab and a parapet front with two sides (boxes that
packaging turns into GPU instances, ``package/facade_components.mjs``), glazed fronts as single
quads batched per material; a loggia moves its stack's wall back (``facade_component_geometry``).
"""

from __future__ import annotations

import math
import zlib

import numpy as np

from turku3d import config as C

SOURCE = "block-of-flats rule from W2 readings (w2-rules 6c0a8090)"
SUPPORT = 0.6
# The register's residential class (cls) as proven's attributes carry it: flats (use 1010) and
# houses (1000) both read "1000"; three or more register storeys make it a block of flats.
RESIDENTIAL_CLASS = "1000"
# OSM building values that do not contradict a block of flats.
FLAT_TAGS = {"", "yes", "building", "unknown", "apartments", "residential", "dormitory"}
# Archetypes a dated block of flats from 1940 on samples into; other compositions keep theirs.
ARCHETYPES = {"functionalism", "postwar_plaster", "postwar_brick", "sixties_slab", "panel_seventies",
              "postmodern_brick", "postmodern_plaster", "contemporary_housing"}
FROM_YEAR = 1940
MIN_LEVELS = 3
# Tall walls (W2 wall_roles): metres.
TALL_WIDTH_M, TALL_SHARE, TALL_HEIGHT_M = 9.0, 0.6, 7.5
SUNWARD_DEG = 45.0
# Single-light windows since 1980: width, height by band (metres).
SINGLE_LIGHT = {"eighties": (1.4, 1.5), "new": (1.4, 1.7)}


def band(year: int) -> str:
    return "postwar" if year < 1960 else "sixties" if year < 1980 else "eighties" if year < 2000 else "new"


def _year(attrs):
    from turkutex.facade_archetypes import parsed_year

    return parsed_year(attrs.get("construction_year") or attrs.get("year") or attrs.get("start_date"))


def block_of_flats(attrs: dict) -> bool:
    """A dated block of flats from 1940 on: the register's residential class, three or more register
    storeys, and no OSM building value that says it is something else."""
    year = _year(attrs)
    levels = attrs.get("levels") if attrs.get("levels_source") == "register" else None
    return (
        str(attrs.get("use") or "") == RESIDENTIAL_CLASS
        and str(attrs.get("osm_use") or "").lower() in FLAT_TAGS
        and year is not None and year >= FROM_YEAR
        and isinstance(levels, (int, float)) and levels >= MIN_LEVELS
    )


def is_block(building, program) -> bool:
    attrs = program.get("attributes") or building.attributes or {}
    return (
        not program.get("description_source")
        and program.get("archetype") in ARCHETYPES
        and block_of_flats(attrs)
    )


def _crc(key: str, salt: str) -> int:
    return zlib.crc32(f"building/{key}/{salt}".encode())


def _pick(key: str, salt: str, options):
    return options[_crc(key, salt) % len(options)]


def balcony(key: str, year: int) -> dict | None:
    """The balcony part of a block of its decade, or None (W2 ``archetypes.balcony``, same picks)."""
    chance = _crc(key, "balcony") % 100
    b = band(year)
    if b == "postwar":
        if year < FROM_YEAR or chance >= 60:
            return None
        return dict(type="cantilever", w_m=2.4, depth_m=1.1, every=3, first=1, parapet="solid", parapet_m=1.0,
                    parapet_colour=_pick(key, "rail", ["#d6cdb8", "#b9bcb4", "#8f9a8c", "#c9b99a"]),
                    slab_m=0.14, slab_colour="#c9c6bc")
    if b == "sixties":
        if chance >= 90:
            return None
        colour = _pick(key, "rail", ["#d9d6cc", "#c9c6bc", "#e3e0d6", "#b3ab9c"])
        kind = "recessed" if chance % 2 else "cantilever"
        return dict(type=kind, w_m=3.2, depth_m=1.4, every=2, first=0, parapet="solid", parapet_m=1.05,
                    parapet_colour=colour, slab_m=0.2 if kind == "recessed" else 0.18, slab_colour="#c9c6bc")
    if chance >= (75 if b == "eighties" else 90):
        return None
    if b == "eighties":
        parapet, colour, height = "solid", _pick(key, "rail", ["#e3e0d6", "#d9d6cc", "#c9c3b4", "#b9bcbd"]), 1.05
    else:
        parapet, colour, height = "glass", _pick(key, "rail", ["#a9b6b5", "#9fb0bb", "#b9c3c2"]), 1.1
    return dict(type="glazed", w_m=3.6, depth_m=1.6 if b == "eighties" else 1.8, every=2, first=0,
                parapet=parapet, parapet_m=height, parapet_colour=colour, slab_m=0.22, slab_colour="#d0cfc8")


KIND = {"cantilever": "balcony", "recessed": "loggia", "glazed": "glazed_balcony"}


def _edges(building, vertices):
    """Per shell quad: (edge key, outward unit normal in E/N, standing on the ground) or None."""
    footprint = building.footprint.buffer(0)
    base = float(building.base)
    result = []
    for quad in np.asarray(vertices, float).reshape(-1, 4, 3):
        tangent = quad[1, [0, 2]] - quad[0, [0, 2]]
        length = float(np.linalg.norm(tangent))
        if length < 1e-6:
            result.append(None)
            continue
        tangent /= length
        normal = np.array([tangent[1], -tangent[0]])  # local x/z, as facade_component_geometry.frame
        middle = (quad[0, [0, 2]] + quad[1, [0, 2]]) / 2
        import shapely

        if footprint.contains(shapely.Point(*(middle + 0.3 * normal))):
            normal = -normal
        grounded = float(quad[:2, 1].min()) <= base + 1.0
        result.append((normal[0], -normal[1], grounded))  # E/N: x = E, z = -N
    return result


def _facing(normal) -> float:
    """How directly a wall faces the afternoon sun (south-west): W2's measure."""
    return -0.5 * normal[0] - 0.87 * normal[1]


def _frame(wall, vertices):
    """(offset, sign, width) mapping plan() bounds fractions to this quad's metres, as plan() does."""
    g = wall.get("measured_geometry", wall.get("geometry", {}))
    coordinates = g.get("coordinates") or [g["p0"], g["p1"]]
    a = np.array([coordinates[0][0] - C.ORIGIN_E, 0, C.ORIGIN_N - coordinates[0][1]])
    b = np.array([coordinates[-1][0] - C.ORIGIN_E, 0, C.ORIGIN_N - coordinates[-1][1]])
    tangent = vertices[1] - vertices[0]
    tangent[1] = 0
    tangent /= np.linalg.norm(tangent)
    origin = vertices[0].copy()
    origin[1] = 0
    sign = 1 if (b - a) @ tangent > 0 else -1
    return float((a - origin) @ tangent), sign, float(np.linalg.norm(b - a))


def _single_lights(wall, prior, v):
    """Single-light windows of the band's size on a rule-sampled wall."""
    width, height = prior
    windows = wall["windows"]
    if windows.get("rectangles_m"):
        # A learned layout's positioned openings: the upper storeys' windows become one light of the
        # band's size round their own centres; the street storey keeps its shops and doors.
        ground = float((wall["split"].get("base_height_m") or {}).get("value") or 0)
        windows["rectangles_m"] = [
            [x + w / 2 - width / 2, y, width, height] if y >= ground - 0.2 else [x, y, w, h]
            for x, y, w, h in windows["rectangles_m"]
        ]
    windows["size_m"] = v([width, height])
    windows["divisions"] = v([1, 1])
    windows["transom_fraction"] = v(0)
    windows["pane_split_fraction"] = v(0)


def apply(building, program, vertices) -> dict:
    """Balconies and windows of the block-of-flats rule on the program's rule-sampled walls, in place.

    Reviewed walls (``measured_wall_id``) keep everything. Returns counts for diagnostics."""
    from turkutex.facade_program import value

    attrs = program.get("attributes") or building.attributes or {}
    key = str(attrs.get("register_id") or building.facade_key)
    year = _year(attrs)
    v = lambda x: value(x, SOURCE, SUPPORT)  # noqa: E731
    quads = np.asarray(vertices, float).reshape(-1, 4, 3)
    free = [i for i, w in enumerate(program["walls"]) if not w.get("measured_wall_id")]
    # The archetype's painted rails (every wall, every second bay) give way to the rule.
    for i in free:
        program["walls"][i]["balconies"] = v([])
    lights = SINGLE_LIGHT.get(band(year))
    if lights:
        for i in free:
            if program["walls"][i]["bays"]["count"]:
                _single_lights(program["walls"][i], lights, v)
    part = balcony(key, year)
    stats = dict(walls=0, stacks=0, balconies=0, type=part["type"] if part else None)
    program["block_of_flats"] = stats
    floors = program["storeys"]["value"]
    if not part or not floors or floors < 2:
        return stats
    edges = _edges(building, quads)
    walls = program["walls"]
    candidates = [i for i in free if edges[i] and edges[i][2] and walls[i].get("outline", True)]
    if not candidates:
        return stats
    longest = max(walls[i]["width_m"] for i in candidates)
    tall = [i for i in candidates
            if walls[i]["width_m"] >= max(TALL_WIDTH_M, TALL_SHARE * longest)
            and walls[i].get("layout_height_m", walls[i]["height_m"]) >= TALL_HEIGHT_M]
    if not tall:
        return stats
    sunny = max(tall, key=lambda i: _facing(edges[i]))
    ahead = edges[sunny]
    limit = math.cos(math.radians(SUNWARD_DEG))
    sunward = [i for i in tall if edges[i][0] * ahead[0] + edges[i][1] * ahead[1] >= limit]
    every, first = part["every"], part["first"]
    for i in sunward:
        stacks = _stacks(walls[i], quads[i], building, part, every, first, floors)
        if stacks:
            walls[i].setdefault("geometry_components", [])
            walls[i]["geometry_components"] = list(walls[i]["geometry_components"]) + stacks
            stats["walls"] += 1
            stats["stacks"] += len(stacks)
            stats["balconies"] += sum(s["rows"] for s in stacks)
    return stats


def _stacks(wall, quad, building, part, every, first, floors):
    """One component descriptor per balcony stack on this shell quad, in plan() bounds."""
    base = float(building.base)
    height = float(wall.get("layout_height_m", wall["height_m"]))
    ground = float((wall["split"].get("base_height_m") or {}).get("value") or height / floors)
    upper = (height - ground) / (floors - 1)
    if upper < 2.4:
        return []
    top = float(quad[2:, 1].min()) - base  # the fragment's eaves
    rows = min(floors - 1, int((top - ground + 0.05) // upper))
    if rows < 1:
        return []
    edge_width = float(wall["width_m"])
    offset_on_edge = float(wall.get("uv_offset", 0) or 0)
    fragment = float(np.linalg.norm(quad[1, [0, 2]] - quad[0, [0, 2]]))
    count = int(wall["bays"]["count"] or 0)
    if count < 2:
        return []
    pitch = edge_width / count
    centres = [(k + 0.5) * pitch for k in range(count)]
    width = min(part["w_m"], pitch - 0.3) if every == 1 else min(part["w_m"], 1.25 * pitch)
    if width < 1.2:
        return []
    if "measured_geometry" not in wall and "geometry" not in wall:
        a, b = quad[0], quad[1]
        wall["geometry"] = dict(
            p0=[float(a[0] + C.ORIGIN_E), float(C.ORIGIN_N - a[2])],
            p1=[float(b[0] + C.ORIGIN_E), float(C.ORIGIN_N - b[2])],
            base_h=base,
        )
    offset, sign, frame_width = _frame(wall, quad.copy())
    g = wall.get("measured_geometry", wall.get("geometry", {}))
    frame_base = float(g.get("base_h", base)) - base
    frame_height = float(wall.get("measured_size", [0, wall["height_m"]])[1])
    if frame_width < 1e-6 or frame_height < 1e-6:
        return []
    result = []
    for k, u in enumerate(centres):
        if k % every != first % every:
            continue
        lo, hi = u - width / 2, u + width / 2
        if lo < 0.3 or hi > edge_width - 0.3:
            continue  # keep clear of the block's corners
        left, right = lo - offset_on_edge, hi - offset_on_edge  # quad metres
        if left < 0 or right > fragment:
            continue  # only whole stacks on this fragment
        # plan() insets every rectangle by 4 cm: widen by as much so the stack keeps its width.
        left, right = left - 0.04, right + 0.04
        x0, x1 = sorted([(left - offset) * sign, (right - offset) * sign])
        bottom, stack_top = ground - 0.04 - frame_base, ground + rows * upper + 0.04 - frame_base
        bottom = max(bottom, 0.0)
        stack_top = min(stack_top, frame_height)
        result.append(dict(
            kind=KIND[part["type"]],
            bounds=[x0 / frame_width, bottom / frame_height, (x1 - x0) / frame_width,
                    (stack_top - bottom) / frame_height],
            rows=rows, count=1, depth_m=part["depth_m"], head="flat",
            dimensions_source=SOURCE, parapet=part["parapet"], parapet_m=part["parapet_m"],
            parapet_colour=part["parapet_colour"], slab_m=part["slab_m"], slab_colour=part["slab_colour"],
            glazed=part["type"] == "glazed",
        ))
    return result
