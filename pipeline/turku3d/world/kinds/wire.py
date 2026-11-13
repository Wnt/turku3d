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
import shapely.ops

from ..api import Area, Instances, Output, Stub
from ..frame import to_engine
from ..objects import registers, spans, wires
from ..objects.common import BLACK_STEEL, DARK_PLASTIC, GALVANISED, LENS, box_of, describe, interaction, yaw_of_run, unique
from ..objects.mesh import Mesh
from . import rail

VERSION = 1
RADIUS = {"span": 0.01, "utility": 0.012, "power": 0.014}  # m: a lighting span with its feed cable, a bundled cable, a conductor
DRAWN = 0.02  # m: least drawn radius of a wire (the carrier wire and the cable hung on it read as one line)
STEP = 1.0  # m of wire per tube segment
SEGMENTS = 48
CARRIED = 1.5  # m (plan) from a span within which a lamp hangs on it
EXTEND = 8.0  # m past a surveyed end that a span still reaches over a wide street
SEPARATION = 4.0  # m (plan) from any lamp within which a crossing is lit already
SAME = 2.5  # m from a wire of the wire stage within which a street span is that wire
HANG = 7.0  # m: a lighting span hangs at least this far over the street
DROP = 0.3  # m from the wire to the top of the luminaire
# The contact line of an electrified railway (OSM `electrified=contact_line`). Its masts are the tall columns the
# scan shows beside the track (dev_08: three in 320 m, 58 and 60 m apart, 2.1 to 2.6 m from the centre line, tops at
# 9.1 m); the scan misses many, so the gaps are filled at the nominal span and marked as guessed.
MAST_OFF = (2.0, 6.5)  # m from a track's centre line within which a tall column is a mast of its contact line
MAST_TOPS = (6.0, 12.0)  # m: tops of such a column
MAST_COVER = 0.6  # share of the half-metre levels up to 6 m that hold returns
MAST_SERVES = 7.5  # m from a mast to the centre line of every track its arms reach
SPAN = 58.0  # m between masts where the scan shows none
BEYOND = 6  # filled masts past the outermost measured one, each way
MARGIN = 135.0  # m round the area in which masts are looked for, so that a span's far end is known
CONTACT, SYSTEM, MID = 6.0, 1.4, 0.5  # m: contact wire over the rail head; messenger wire over it at a mast, at mid span
ALONG = 10.0  # m between the points of a contact wire (it follows the track's curve) and between its droppers


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


def mast_columns(points: np.ndarray, lines: list) -> list[dict]:
    """Masts beside tracks in lidar returns ([e, n, height over the ground, class]): half-metre cells MAST_OFF from a
    centre line whose returns rise from under 2 m to a mast's top with most levels in between filled."""
    if not len(points) or not lines:
        return []
    off = shapely.distance(shapely.union_all(lines), shapely.points(points[:, :2]))
    near = points[(off >= MAST_OFF[0]) & (off <= MAST_OFF[1]) & (points[:, 2] > 0.5)]
    if not len(near):
        return []
    key = np.floor(near[:, :2] / 0.5).astype(np.int64)
    order = np.lexsort((key[:, 1], key[:, 0]))
    near, key = near[order], key[order]
    cells, starts, counts = np.unique(key, axis=0, return_index=True, return_counts=True)
    found: list[dict] = []
    for k in np.argsort(-counts, kind="stable"):
        column = near[starts[k]:starts[k] + counts[k]]
        top = float(column[:, 2].max())
        if counts[k] < 8 or not MAST_TOPS[0] <= top <= MAST_TOPS[1] or column[:, 2].min() > 2.0:
            continue
        levels = np.unique(np.floor(column[column[:, 2] < 6.0, 2] / 0.5))
        if len(levels) / 12 < MAST_COVER:
            continue
        e, n = float(column[:, 0].mean()), float(column[:, 1].mean())
        if any(math.hypot(e - m["e"], n - m["n"]) < 3.0 for m in found):
            continue  # the neighbouring cell of a mast already taken
        found.append({"e": round(e, 2), "n": round(n, 2), "top": round(top, 2), "returns": int(counts[k])})
    return found


def _beside(lidar, lines: list, box) -> np.ndarray:
    """The lidar returns within mast distance of the tracks inside a box, read piece by piece along them (the whole
    box of a location holds tens of millions of returns, the strips beside its tracks a few hundred thousand)."""
    frame = shapely.box(*box)
    seen, found = set(), []
    for line in lines:
        inside = line.intersection(frame)
        for part in shapely.get_parts(inside):
            if part.geom_type != "LineString":
                continue
            for start in np.arange(0.0, part.length, 50.0):
                e0, n0, e1, n1 = shapely.ops.substring(part, start, min(start + 50.0, part.length)).bounds
                reach = MAST_OFF[1] + 0.5
                cells = [(i, j) for i in range(math.floor((e0 - reach) / 50), math.floor((e1 + reach) / 50) + 1)
                         for j in range(math.floor((n0 - reach) / 50), math.floor((n1 + reach) / 50) + 1)]
                for i, j in cells:
                    if (i, j) not in seen:
                        seen.add((i, j))
                        found.append(lidar.box(i * 50.0, j * 50.0, (i + 1) * 50.0, (j + 1) * 50.0))
    found = [points for points in found if len(points)]
    return np.concatenate(found) if found else np.zeros((0, 4))


def supports(length: float, measured: list[float]) -> list[tuple[float, bool]]:
    """Stations of a contact line's supports along a track: the measured masts, the gaps between them divided into
    spans near SPAN, and up to BEYOND filled masts past the outermost ones. -> [(station, measured)]."""
    kept: list[float] = []
    for station in sorted(measured):
        if not kept or station - kept[-1] >= SPAN / 3:
            kept.append(station)
    if not kept:
        return []
    out = [(kept[0] - SPAN * k, False) for k in range(BEYOND, 0, -1) if kept[0] - SPAN * k >= 0.0]
    for a, b in zip(kept, kept[1:]):
        out.append((a, True))
        parts = max(1, round((b - a) / SPAN))
        out += [(a + (b - a) * k / parts, False) for k in range(1, parts)]
    out.append((kept[-1], True))
    out += [(kept[-1] + SPAN * k, False) for k in range(1, BEYOND + 1) if kept[-1] + SPAN * k <= length]
    return out


def _catenary(area: Area, sources) -> tuple[list[Stub], shapely.Geometry | None]:
    """The contact line of the electrified tracks: an entity per mast, and per support of a track one with the arm
    from the mast and the wires of the span that follows. Also the electrified centre lines (None without any)."""
    box = area.bbox(MARGIN)
    tracks = [track for track in rail.tracks(box) if track["electrified"]]
    if not tracks:
        return [], None
    electrified = shapely.union_all([shapely.force_2d(track["line"]) for track in tracks])
    merged = shapely.line_merge(electrified)
    lines = sorted((line for line in shapely.get_parts(merged) if line.length >= SPAN / 2), key=lambda line: line.bounds)
    masts = [dict(m, measured=True) for m in mast_columns(_beside(registers.lidar_of(sources), lines, box), lines)]
    stubs = []
    for line in lines:
        mine = {round(float(line.project(shapely.Point(m["e"], m["n"]))), 1): m for m in masts
                if m["measured"] and line.distance(shapely.Point(m["e"], m["n"])) <= MAST_SERVES}
        chain = supports(line.length, list(mine))  # empty without a measured mast: the span rhythm has no anchor
        for k, (station, measured) in enumerate(chain):
            near = min(mine, key=lambda s: abs(s - station))
            mast = mine[near]
            if not measured:
                # A filled mast stands on the side and at the distance of the measured mast nearest along the track.
                at, foot = line.interpolate(near), line.interpolate(station)
                ahead, back = line.interpolate(min(station + 1.0, line.length)), line.interpolate(max(station - 1.0, 0.0))
                tx, ty = ahead.x - back.x, ahead.y - back.y
                norm = math.hypot(tx, ty) or 1.0
                side = 1.0 if (mast["e"] - at.x) * ty - (mast["n"] - at.y) * tx > 0 else -1.0
                reach = float(np.clip(math.hypot(mast["e"] - at.x, mast["n"] - at.y), *MAST_OFF))
                e, n = foot.x + ty / norm * side * reach, foot.y - tx / norm * side * reach
                shared = next((m for m in masts if math.hypot(m["e"] - e, m["n"] - n) <= 4.0), None)
                if shared is None:
                    shared = {"e": round(e, 2), "n": round(n, 2), "top": mast["top"], "measured": False}
                    masts.append(shared)
                mast = shared
            if not area.touches(box_of(mast["e"], mast["n"], 0.5)):
                continue
            mast["used"] = True
            end = chain[k + 1][0] if k + 1 < len(chain) else station
            along = np.linspace(station, end, max(2, int(math.ceil((end - station) / ALONG)) + 1)) if end > station else np.array([station])
            head = rail.level(line, along) + rail.RAIL_TOP
            xy = shapely.get_coordinates(shapely.line_interpolate_point(line, along))
            ground = registers.ground_height(sources, mast["e"], mast["n"])
            if not np.isfinite(head).all() or not math.isfinite(ground):
                continue
            rows = [[round(float(e), 2), round(float(n), 2), round(float(h), 2)] for (e, n), h in zip(xy, head)]
            facts = {
                "at": rows[0], "type": "catenary", "line": rows, "mast": [mast["e"], mast["n"], round(ground, 2)],
                "basis": "OSM electrified track; contact wire at the nominal height over the rail, supports at the masts",
                "guessed": ["height"] + ([] if mast["measured"] else ["supports"]),
                "interaction": interaction("fixed", "capsule", [DRAWN, round(float(end - station), 2)]),
            }
            lo, hi = xy.min(axis=0), xy.max(axis=0)
            reach = (min(lo[0], mast["e"]) - 1.0, min(lo[1], mast["n"]) - 1.0, max(hi[0], mast["e"]) + 1.0, max(hi[1], mast["n"]) + 1.0)
            key = f"{round(rows[0][0] * 10)}_{round(rows[0][1] * 10)}"
            stubs.append(Stub(f"wire/c{key}", tuple(float(v) for v in reach), [{"source": "osm", "key": "railway electrified=contact_line"}], facts))
    for mast in masts:
        ground = registers.ground_height(sources, mast["e"], mast["n"]) if mast.get("used") else math.nan
        if not math.isfinite(ground):
            continue
        top = float(np.clip(mast["top"], CONTACT + SYSTEM + 0.6, MAST_TOPS[1]))
        facts = {
            "at": [mast["e"], mast["n"], round(ground, 2)], "type": "mast", "height": round(top, 2),
            "basis": "lidar: a tall column beside an electrified track" if mast["measured"] else "filled at the span of the measured masts along the track",
            "guessed": [] if mast["measured"] else ["position", "height"],
            "interaction": interaction("fixed", "cylinder", [0.15, round(top, 2)]),
        }
        if mast["measured"]:
            facts["measured"] = {"lidar": {"top": mast["top"], "returns": mast["returns"]}}
        key = f"{round(mast['e'] * 10)}_{round(mast['n'] * 10)}"
        stubs.append(Stub(f"wire/m{key}", box_of(mast["e"], mast["n"], 1.0), [{"source": "turku-lidar:pistepilvi", "key": key}], facts))
    return stubs, electrified


def _catenary_output(description: dict) -> Output:
    """A contact-line mast (a steel H column), or a support's two-tube arm from its mast with the contact and
    messenger wires and their droppers as far as the next support."""
    origin = np.array(to_engine(*description["at"]), dtype=float)
    local = lambda p: np.array(to_engine(*p), dtype=float) - origin  # noqa: E731
    mesh = Mesh()
    if description["type"] == "mast":
        mesh.beam("mast", GALVANISED, [0.0, -0.5, 0.0], [0.0, float(description["height"]), 0.0], 0.24, 0.2, up=(1.0, 0.0, 0.0))
        block = description.get("interaction") or {}
        item = Instances(mesh.parts(), np.array([[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]]), ids=[description["id"]], behaviour="fixed",
                         collision=block.get("collision"))
        return Output(tuple(float(v) for v in origin), [], [item])
    line = [np.array(p, dtype=float) for p in description["line"]]
    hang, carry = np.array([0.0, CONTACT, 0.0]), np.array([0.0, CONTACT + SYSTEM, 0.0])
    foot = local(description["mast"])
    high = np.array([foot[0], carry[1] + 0.5, foot[2]])
    low = np.array([foot[0], hang[1] + 0.6, foot[2]])
    mesh.tube("arm", GALVANISED, [high, carry], 0.03, sides=6)
    mesh.tube("arm", GALVANISED, [low, hang + [0.0, 0.35, 0.0]], 0.025, sides=6)
    mesh.tube("arm", GALVANISED, [carry, hang + [0.0, 0.35, 0.0], hang], 0.02, sides=6)
    if len(line) > 1:
        count = len(line) - 1
        contact = [local(p) + [0.0, CONTACT, 0.0] for p in line]
        messenger = [local(p) + [0.0, CONTACT + MID + (SYSTEM - MID) * (2 * k / count - 1) ** 2, 0.0] for k, p in enumerate(line)]
        mesh.tube("wire", BLACK_STEEL, contact, DRAWN, sides=5)
        mesh.tube("wire", BLACK_STEEL, messenger, DRAWN, sides=5)
        for a, b in list(zip(contact, messenger))[1:-1]:
            mesh.tube("dropper", BLACK_STEEL, [a, b], 0.012, sides=4)
    return Output(tuple(float(v) for v in origin), mesh.parts(), [])


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
        # Spans whose returns the survey classed as vegetation, where the wire stage found nothing.
        known = [shapely.LineString([w["a"][:2], w["b"][:2]]) for w in found]
        more, on_them = spans.in_box(sources, box)
        fresh = [w for w in more if not any(line.distance(shapely.Point((w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2)) <= SAME for line in known)]
        found = found + fresh
        lamps = lamps + [lamp for lamp in on_them if any(lamp["wire"] is w for w in fresh)]
        catenary, electrified = _catenary(area, sources)
        if electrified is not None:
            # What the wire stage found along an electrified track is its contact line, drawn whole from the track.
            found = [w for w in found if not (electrified.distance(shapely.Point(w["a"][:2])) <= MAST_SERVES / 2
                                              and electrified.distance(shapely.Point(w["b"][:2])) <= MAST_SERVES / 2)]
        if not found:
            return unique(catenary)
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
                "lamps": hung, "measured": {"lidar": {"length": round(length, 2), "extended": bool(wire.get("extended")),
                                       **({"returns": wire["returns"], "found": "free-hanging returns across a carriageway"} if "returns" in wire else {})}},
                "guessed": ["lamps"] if any(lamp.get("guessed") for lamp in hung) else [],
                "interaction": interaction("fixed", "capsule", [RADIUS.get(wire["kind"], 0.012), round(length, 2)]),
            }
            key = f"{round(wire['a'][0] * 10)}_{round(wire['a'][1] * 10)}_{round(length * 10)}"
            stubs.append(Stub(f"wire/w{key}", (float(lo[0]) - 0.5, float(lo[1]) - 0.5, float(hi[0]) + 0.5, float(hi[1]) + 0.5),
                              [{"source": "turku-lidar:pistepilvi", "key": key}], facts))
        return unique(stubs + catenary)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        if description.get("type") in ("catenary", "mast"):
            return _catenary_output(description)
        wire = {"a": description["a"], "b": description["b"], "sag": float(description.get("sag", 0.0))}
        origin = np.array(to_engine(*wire["a"]), dtype=float)
        local = lambda p: np.array(to_engine(*p), dtype=float) - origin  # noqa: E731
        a, b = np.array(wire["a"], dtype=float), np.array(wire["b"], dtype=float)
        length = float(np.hypot(*(b - a)[:2]))
        count = int(min(SEGMENTS, max(2, math.ceil(length / STEP))))
        path = [local(_point(wire, k / count)) for k in range(count + 1)]
        mesh = Mesh()
        # Drawn no thinner than DRAWN: a 2 cm wire is under a pixel from 15 m and vanishes where the eye sees a line.
        mesh.tube("wire", BLACK_STEEL, path, max(RADIUS.get(description.get("type"), 0.012), DRAWN), sides=5)
        instances = []
        lamps = description.get("lamps") or []
        if lamps:
            bearing = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))
            rows = [[*local(lamp["at"]), round(yaw_of_run(bearing), 4), 1.0, 1.0, 1.0] for lamp in lamps]
            instances.append(Instances(list(hung_lamp()), np.array(rows, dtype=float),
                                       ids=[f"{description['id']}#lamp{k}" for k in range(len(lamps))], behaviour="fixed"))
        return Output(tuple(float(v) for v in origin), mesh.parts(), instances)
