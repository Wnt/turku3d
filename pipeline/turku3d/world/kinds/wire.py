"""Overhead wires with the street lamps hung on them: one entity per wire the survey lidar sees.

The lidar detection (`objects/wires.py`, the old generator's stage) gives each wire's two ends, its sag and what
it hangs from, and the lamps it found on it: a compact blob of returns under the wire, or the knot where span
wires meet in mid air. A lamp head is small and dark in the survey, so most spans come out without their lamp;
such a lighting span gets one lamp over each carriageway centreline it crosses (the old generator's rule), marked
as a guess. Central Turku is lit this way: no register knows these lamps.

Schema: docs/specs/street-objects.md.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely

from ..api import Area, Instances, Output, Stub
from ..frame import to_engine
from ..objects import registers, wires
from ..objects.common import BLACK_STEEL, DARK_PLASTIC, LENS, describe, interaction, yaw_of_run, unique
from ..objects.mesh import Mesh

VERSION = 1
RADIUS = {"span": 0.01, "utility": 0.012, "power": 0.014}  # m: a lighting span with its feed cable, a bundled cable, a conductor
STEP = 1.0  # m of wire per tube segment
SEGMENTS = 48
CARRIED = 1.5  # m (plan) from a span within which a lamp hangs on it
EXTEND = 8.0  # m past a surveyed end that a span still reaches over a wide street
SEPARATION = 4.0  # m (plan) from any lamp within which a crossing is lit already
HANG = 7.0  # m: a lighting span hangs at least this far over the street
DROP = 0.3  # m from the wire to the top of the luminaire


def _point(wire: dict, t: float) -> np.ndarray:
    a, b = np.array(wire["a"], dtype=float), np.array(wire["b"], dtype=float)
    p = a + (b - a) * t
    p[2] -= wire["sag"] * 4 * t * (1 - t)
    return p


def _crossings(wire: dict, streets: registers.Streets, lit: list, ground) -> tuple[list[dict], dict]:
    """Lamps over the carriageway centrelines a lampless lighting span crosses, and the wire reaching to them."""
    a, b = np.array(wire["a"], dtype=float), np.array(wire["b"], dtype=float)
    length = float(np.hypot(*(b - a)[:2]))
    if length < 1e-6 or streets.car_tree is None:
        return [], wire
    u = (b - a) / length
    reach = shapely.LineString([a[:2] - u[:2] * EXTEND, b[:2] + u[:2] * EXTEND])
    found = []
    for k in streets.car_tree.query(reach, predicate="intersects"):
        for p in shapely.get_parts(reach.intersection(streets.cars[int(k)])):
            if p.geom_type == "Point":
                found.append(round(float(np.dot([p.x - a[0], p.y - a[1]], u[:2])), 2))
    lamps, along_all = [], []
    for along in sorted(set(found)):
        t = along / length
        q = a + u * along
        q[2] = a[2] if t < 0 else b[2] if t > 1 else q[2] - wire["sag"] * 4 * t * (1 - t)
        if any(math.hypot(q[0] - e, q[1] - n) < SEPARATION for e, n in lit):
            continue
        below = ground(q[0], q[1])
        if not math.isfinite(below) or q[2] - below < HANG:
            continue
        lit.append((q[0], q[1]))
        lamps.append({"at": [round(float(v), 2) for v in q], "basis": "the span crosses a carriageway centreline", "guessed": True})
        along_all.append(along)
    if not lamps:
        return [], wire
    lo, hi = min(0.0, min(along_all)), max(length, max(along_all))
    if lo < 0 or hi > length:
        start, end = a + u * lo, a + u * hi
        start[2], end[2] = (a[2] if lo < 0 else start[2]), (b[2] if hi > length else end[2])
        wire = {**wire, "a": [round(float(v), 2) for v in start], "b": [round(float(v), 2) for v in end], "extended": True}
    return lamps, wire


@lru_cache(maxsize=4)
def hung_lamp() -> tuple:
    """A pendant street luminaire, origin at the wire: hanger, a dark hood and the lit bowl under it (the old
    game's hung lamp, as closed solids)."""
    mesh = Mesh()
    mesh.tube("hanger", BLACK_STEEL, [[0, 0.01, 0], [0, -DROP - 0.02, 0]], 0.012, sides=6)
    mesh.lathe("hood", DARK_PLASTIC, [(0.3, -DROP - 0.23), (0.3, -DROP - 0.2), (0.1, -DROP)], sides=12)
    mesh.lathe("lens", LENS, [(0.2, -DROP - 0.31), (0.28, -DROP - 0.23)], sides=12)
    return tuple(mesh.parts())


class Wire:
    name = "wire"
    code = (Path(__file__).parent.parent / "objects", Path(__file__).parent.parent.parent / "wire_points.py")

    def discover(self, area: Area, sources) -> list[Stub]:
        box = area.bbox(10.0)
        found, lamps = wires.in_box(box, sources.log)
        if not found:
            return []
        streets = registers.Streets(sources, box)
        posts = [(post["e"], post["n"]) for post in registers.lamp_posts(sources, box)]
        ground = lambda e, n: registers.ground_height(sources, e, n)  # noqa: E731
        lit = [tuple(lamp["at"][:2]) for lamp in lamps]
        stubs = []
        for wire in sorted(found, key=lambda w: (w["a"], w["b"])):
            a, b = np.array(wire["a"], dtype=float), np.array(wire["b"], dtype=float)
            lo, hi = np.minimum(a[:2], b[:2]), np.maximum(a[:2], b[:2])
            if not area.touches((lo[0], lo[1], hi[0], hi[1])):
                continue
            chord = shapely.LineString([a[:2], b[:2]])
            hung = []
            for lamp in lamps:
                on = lamp["wire"] is not None and lamp["wire"]["a"] == wire["a"] and lamp["wire"]["b"] == wire["b"]
                if on or (lamp["wire"] is None and chord.distance(shapely.Point(lamp["at"][:2])) <= CARRIED):
                    hung.append({"at": lamp["at"], "basis": "lidar: returns hanging on the wire"})
            carries_post = any(chord.distance(shapely.Point(p)) <= CARRIED for p in posts)
            if wire["kind"] == "span" and not hung and not carries_post \
                    and not any(chord.distance(shapely.Point(p)) <= CARRIED for p in lit):
                hung, wire = _crossings(wire, streets, lit, ground)
            a, b = np.array(wire["a"], dtype=float), np.array(wire["b"], dtype=float)
            length = float(np.hypot(*(b - a)[:2]))
            facts = {
                "at": wire["a"], "a": wire["a"], "b": wire["b"], "sag": wire["sag"], "type": wire["kind"], "ends": wire["ends"],
                "lamps": hung, "measured": {"lidar": {"length": round(length, 2), "extended": bool(wire.get("extended"))}},
                "guessed": ["lamps"] if any(lamp.get("guessed") for lamp in hung) else [],
                "interaction": interaction("fixed", "capsule", [RADIUS.get(wire["kind"], 0.012), round(length, 2)]),
            }
            key = f"{round(wire['a'][0] * 10)}_{round(wire['a'][1] * 10)}_{round(length * 10)}"
            stubs.append(Stub(f"wire/w{key}", (float(lo[0]) - 0.5, float(lo[1]) - 0.5, float(hi[0]) + 0.5, float(hi[1]) + 0.5),
                              [{"source": "turku-lidar:pistepilvi", "key": key}], facts))
        return unique(stubs)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        wire = {"a": description["a"], "b": description["b"], "sag": float(description.get("sag", 0.0))}
        origin = np.array(to_engine(*wire["a"]), dtype=float)
        local = lambda p: np.array(to_engine(*p), dtype=float) - origin  # noqa: E731
        a, b = np.array(wire["a"], dtype=float), np.array(wire["b"], dtype=float)
        length = float(np.hypot(*(b - a)[:2]))
        count = int(min(SEGMENTS, max(2, math.ceil(length / STEP))))
        path = [local(_point(wire, k / count)) for k in range(count + 1)]
        mesh = Mesh()
        mesh.tube("wire", BLACK_STEEL, path, RADIUS.get(description.get("type"), 0.012), sides=5)
        instances = []
        lamps = description.get("lamps") or []
        if lamps:
            bearing = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))
            rows = [[*local(lamp["at"]), round(yaw_of_run(bearing), 4), 1.0, 1.0, 1.0] for lamp in lamps]
            instances.append(Instances(list(hung_lamp()), np.array(rows, dtype=float),
                                       ids=[f"{description['id']}#lamp{k}" for k in range(len(lamps))], behaviour="fixed"))
        return Output(tuple(float(v) for v in origin), mesh.parts(), instances)
