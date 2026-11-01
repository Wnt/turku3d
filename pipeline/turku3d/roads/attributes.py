"""Per-way street attributes of roads.json v2 (docs/specs/roads-json-v2.md §3.3–3.5).

Lanes, carriageway width, speed limit, ambient density and v2 edge flags come from OSM tags; where
a tag is missing the class rules below fill in. Everything is a pure function of one way's tags,
so every tile that sees the way derives identical values.
"""

from __future__ import annotations

import math
import re

from . import CAR, FOOT, ONEWAY

HIGHWAYS = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
    "pedestrian",
    "footway",
    "cycleway",
    "path",
    "steps",
    "track",
    "bridleway",
    "platform",
    "other",
)

EDGE_FLAGS = {
    "PAVED": 1,
    "LIT": 2,
    "BRIDGE": 4,
    "TUNNEL": 8,
    "ROUNDABOUT": 16,
    "LINK": 32,
    "KMH_TAGGED": 64,
    "LANES_TAGGED": 128,
    "WIDTH_TAGGED": 256,
    "START_JUNCTION": 512,
    "END_JUNCTION": 1024,
    "SHARED_SPACE": 2048,
    "PSV_ONLY": 4096,
    "NO_MARKINGS": 8192,
    "SINGLE_TRACK": 16384,
    "SMOOTH": 32768,
}
F = EDGE_FLAGS

MAJOR = {"motorway", "trunk", "primary", "secondary"}
STREET = {"tertiary", "unclassified", "residential"}
UNPAVED = {
    "unpaved",
    "compacted",
    "fine_gravel",
    "gravel",
    "dirt",
    "ground",
    "grass",
    "sand",
    "earth",
    "mud",
    "pebblestone",
    "rock",
    "woodchips",
    "grass_paver",
    "stepping_stones",
}
# Surfaces that take line paint (crosswalks are painted on any paved surface).
SMOOTH = {"asphalt", "concrete", "concrete:plates", "concrete:lanes", "paved", "chipseal"}
LIT = {"yes", "24/7", "automatic", "limited", "interval", "sunset-sunrise", "dusk-dawn"}

DEFAULT_KMH = {
    "motorway": 100,
    "trunk": 80,
    "primary": 50,
    "secondary": 50,
    "tertiary": 40,
    "unclassified": 40,
    "residential": 40,
    "service": 20,
    "living_street": 20,
}
KMH_WORDS = {"fi:urban": 50, "fi:rural": 80, "fi:motorway": 120, "walk": 7, "none": 120}

# car / foot ambient weight before the lane bonus (spec §3.3).
DENSITY = {
    "motorway": (15, 0),
    "trunk": (15, 0),
    "primary": (13, 7),
    "secondary": (11, 7),
    "tertiary": (8, 6),
    "unclassified": (5, 4),
    "residential": (4, 5),
    "living_street": (1, 8),
    "service": (2, 3),
    "pedestrian": (0, 12),
    "footway": (0, 6),
    "steps": (0, 3),
    "cycleway": (0, 3),
    "path": (0, 2),
    "track": (0, 2),
    "bridleway": (0, 1),
    "platform": (0, 6),
}


def base_highway(highway: str | None) -> str:
    return (highway or "").removesuffix("_link")


def highway_index(highway: str | None) -> int:
    return HIGHWAYS.index(highway) if highway in HIGHWAYS else HIGHWAYS.index("other")


def _number(value) -> float | None:
    """'6.5', '6,5 m', '2;3' (largest) -> float; None when there is no usable number."""
    if value is None:
        return None
    found = [float(v.replace(",", ".")) for v in re.findall(r"\d+(?:[.,]\d+)?", str(value))]
    return max(found) if found else None


def _count(value) -> int | None:
    n = _number(value)
    return int(n) if n is not None and 1 <= n <= 12 else None


def kmh(highway: str | None, tags: dict) -> tuple[int, bool]:
    """Posted limit in km/h and whether a tag gave it."""
    for key in ("maxspeed", "zone:maxspeed", "maxspeed:advisory"):
        value = str(tags.get(key, "")).strip().lower()
        if not value or value in ("signals", "variable", "unknown"):
            continue
        if value in KMH_WORDS:
            return KMH_WORDS[value], True
        n = _number(value.removeprefix("fi:"))
        if n is not None and 3 <= n <= 140:
            return int(round(n * (1.609 if "mph" in value else 1))), True
    base = base_highway(highway)
    if highway in DEFAULT_KMH:
        return DEFAULT_KMH[highway], False
    if base in DEFAULT_KMH:
        return DEFAULT_KMH[base], False
    return 10, False


def lanes(highway: str | None, flags: int, tags: dict) -> tuple[int, int, bool, bool]:
    """(forward, backward, tagged, single_track) car lanes along the edge direction (a→b).

    Edge direction is the way direction except ``oneway=-1``, which is reversed (so its OSM
    backward lanes are the edge's forward lanes)."""
    if not flags & CAR:
        return 0, 0, False, False
    total = _count(tags.get("lanes"))
    fwd = _count(tags.get("lanes:forward"))
    bwd = _count(tags.get("lanes:backward"))
    both = _count(tags.get("lanes:both_ways")) or 0
    tagged = any(v is not None for v in (total, fwd, bwd))
    if flags & ONEWAY:
        if tags.get("oneway") == "-1":
            fwd = bwd
        default = 2 if highway in ("motorway", "trunk") else 1
        return fwd or total or default, 0, tagged, False
    if fwd and bwd:
        return fwd, bwd, True, False
    if total:
        total = max(total - both, 1)
        if total == 1:
            return 1, 1, True, True
        if fwd:
            return fwd, max(total - fwd, 1), True, False
        if bwd:
            return max(total - bwd, 1), bwd, True, False
        return math.ceil(total / 2), total // 2, True, False
    return fwd or 1, bwd or 1, tagged, False


def lane_width(highway: str | None) -> float:
    base = base_highway(highway)
    if base in MAJOR:
        return 3.5
    if base in STREET:
        return 3.25
    return 3.0


def width(highway: str | None, tags: dict, fwd: int, bwd: int, single: bool) -> tuple[float, bool]:
    """Carriageway width (m) and whether a tag gave it."""
    for key in ("width", "est_width", "width:carriageway"):
        w = _number(tags.get(key))
        if w is not None and 2.0 <= w <= 40.0:
            return round(w, 1), True
    if not fwd and not bwd:
        w = _number(tags.get("width")) or {"pedestrian": 6.0, "steps": 2.0}.get(highway or "", 2.5)
        return round(min(max(w, 1.0), 20.0), 1), False
    if single:
        return 4.0, False
    if highway in ("service", "living_street") and fwd + bwd == 2 and bwd:
        return 5.0, False
    return round(max(3.0, (fwd + bwd) * lane_width(highway)), 1), False


def density(highway: str | None, flags: int, tags: dict, fwd: int, bwd: int) -> tuple[int, int]:
    car, foot = DENSITY.get(highway or "", DENSITY.get(base_highway(highway), (0, 1)))
    if highway == "service" and tags.get("service") in ("parking_aisle", "driveway"):
        car, foot = 1, 2
    if highway == "footway" and tags.get("footway") == "sidewalk":
        foot = 8
    car += max(0, fwd + bwd - 2)
    car = car if flags & CAR else 0
    foot = foot if flags & FOOT else 0
    return min(car, 15), min(foot, 15)


def edge_flags(highway: str | None, tags: dict) -> int:
    """v2 flags derivable from one way's tags (junction bits are added by the graph)."""
    out = 0
    surface = tags.get("surface")
    paved = surface not in UNPAVED if surface else highway not in ("track", "path", "bridleway")
    if paved:
        out |= F["PAVED"]
    if paved and (surface in SMOOTH or (surface is None and highway in DEFAULT_KMH)):
        out |= F["SMOOTH"]
    if tags.get("lit") in LIT:
        out |= F["LIT"]
    if tags.get("bridge", "no") != "no":
        out |= F["BRIDGE"]
    if tags.get("tunnel", "no") != "no":
        out |= F["TUNNEL"]
    if tags.get("junction") in ("roundabout", "circular"):
        out |= F["ROUNDABOUT"]
    if (highway or "").endswith("_link"):
        out |= F["LINK"]
    if highway in ("living_street", "pedestrian"):
        out |= F["SHARED_SPACE"]
    motor = tags.get("motor_vehicle", tags.get("motorcar"))
    if motor in ("no", "private") and (
        tags.get("psv") in ("yes", "designated") or tags.get("bus") in ("yes", "designated")
    ):
        out |= F["PSV_ONLY"]
    if tags.get("lane_markings") == "no":
        out |= F["NO_MARKINGS"]
    return out


def attributes(highway: str | None, flags: int, tags: dict) -> dict:
    """Every per-way v2 attribute (spec §3.3), before junction flags and setbacks."""
    fwd, bwd, lanes_tagged, single = lanes(highway, flags, tags)
    w, width_tagged = width(highway, tags, fwd, bwd, single)
    speed, speed_tagged = kmh(highway, tags)
    car, foot = density(highway, flags, tags, fwd, bwd)
    eflags = edge_flags(highway, tags)
    eflags |= F["KMH_TAGGED"] if speed_tagged else 0
    eflags |= F["LANES_TAGGED"] if lanes_tagged else 0
    eflags |= F["WIDTH_TAGGED"] if width_tagged else 0
    eflags |= F["SINGLE_TRACK"] if single else 0
    return dict(
        lanes_fwd=fwd,
        lanes_bwd=bwd,
        width=w,
        kmh=speed,
        car_density=car,
        foot_density=foot,
        flags=eflags,
        highway=highway_index(highway),
    )
