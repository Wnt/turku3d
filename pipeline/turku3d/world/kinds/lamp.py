"""Street lamps: one entity per pole of the street-light register, shaped by what the lidar measures of it.

The register (frn:CityFurniture_LOD2) gives a base point and nothing else. The lidar gives the pole's true axis,
its height and the bearing and reach of its arm; where it shows no pole (scanned before the pole was set, or
hidden under a crown) the family is a guess from the street beside it and is marked so. Families:

- `street-arm`  a tapered steel pole with an arm (curved under 2 m of reach, straight and rising from 2 m) and a flat
                head over the carriageway
- `street-top`  a tall pole with the head on its top
- `park`        a 3 to 6 m post with a post-top lantern
- `span-pole`   a plain pole that carries a lighting span wire (the hung lamps belong to the `wire` kind)

Schema: docs/specs/street-objects.md.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..api import Area, Output, Stub
from ..objects import registers, spans, wires
from ..objects.common import BLACK_STEEL, DARK_PLASTIC, GALVANISED, LENS, box_of, describe, interaction, single, unique
from ..objects.mesh import Mesh

VERSION = 1
STREET_MIN = 6.5  # m: a pole this tall lights a carriageway
HEAD = 0.6  # m: length of a street-light head
STRAIGHT = 2.0  # m: an arm reaching this far is a straight arm, a shorter one the bent top of a curved pole
TALLEST = 13.0  # m: what the lidar shows taller at a register point is a tree or a facade, not the street lamp
NEIGHBOURS = 120.0  # m within which the three nearest fully measured lamps set a hidden top
PARALLEL, ALONG = 0.94, 6.0  # cosine of 20 degrees and m across: two lamps stand along one street
SPAN_REACH = 1.6  # m from a pole to the end of a span wire it carries
SPAN_LIT = (30.0, 4.0)  # m along and across its street within which a lighting span lights a pole's stretch


def _family(measured: dict | None, street: dict | None) -> dict:
    """Family, height and arm of a pole from its lidar measurement, else from the street beside it."""
    if measured and measured["top"] >= 2.6:
        top, arm = measured["top"], measured.get("arm")
        head = measured.get("head")
        if head and top < STREET_MIN <= head["top"] <= TALLEST and street and street["distance"] <= 14:
            # The scan lost the thin shaft and saw the head again metres higher: a street lamp of that height.
            arm = head.get("arm")
            if arm and arm["returns"] >= 5 and arm["reach"] >= 0.8 and abs(arm["height"] - head["top"]) < 1.5:
                return {"family": "street-arm", "height": head["top"], "arm": {"bearing": arm["bearing"], "reach": round(min(arm["reach"], 3.25), 2)},
                        "basis": "lidar: pole foot, head and arm"}
            return {"family": "street-arm", "height": head["top"], "arm": {"bearing": street["bearing"], "reach": 1.5},
                    "basis": "lidar: pole foot and head; arm towards the street is a guess", "guessed": ["arm"]}
        if top >= STREET_MIN:
            if arm and arm["returns"] >= 5 and arm["reach"] >= 0.8 and abs(arm["height"] - top) < 1.5:
                return {"family": "street-arm", "height": round(top, 2), "arm": {"bearing": arm["bearing"], "reach": round(min(arm["reach"], 3.25), 2)},
                        "basis": "lidar: pole and arm"}
            if street and street["distance"] <= 14:
                return {"family": "street-arm", "height": round(top, 2), "arm": {"bearing": street["bearing"], "reach": 1.5},
                        "basis": "lidar: pole; arm towards the street is a guess", "guessed": ["arm"]}
            return {"family": "street-top", "height": round(top, 2), "basis": "lidar: pole"}
        return {"family": "park", "height": round(top, 2), "basis": "lidar: pole"}
    if street and street["distance"] <= 9:
        return {"family": "street-arm", "height": 9.0, "arm": {"bearing": street["bearing"], "reach": 1.5},
                "basis": "register position only; family from the street beside it", "guessed": ["family", "height", "arm"]}
    return {"family": "park", "height": 4.5, "basis": "register position only", "guessed": ["family", "height"]}


def _same_street(a: dict, b: dict) -> bool:
    """Two feet on carriageway centrelines lie along one street: the lines run the same way there and one foot is on
    the other's line produced."""
    (ax, ay), (bx, by) = a["tangent"], b["tangent"]
    dx, dy = b["foot"][0] - a["foot"][0], b["foot"][1] - a["foot"][1]
    return abs(ax * bx + ay * by) >= PARALLEL and abs(dx * ay - dy * ax) <= ALONG


def street_head(mesh: Mesh, at: np.ndarray, forward: np.ndarray) -> None:
    """A flat LED street-light head whose neck is at `at`, pointing along `forward` (horizontal)."""
    side = np.array([forward[2], 0.0, -forward[0]])
    up = np.array([0.0, 1.0, 0.0])
    corners = []
    for sx in (0, 1):  # neck, tip
        for sy in (-1, 1):
            for sz in (-1, 1):
                width = 0.11 if sx == 0 else 0.15
                depth = 0.05 if sx == 0 else 0.035
                corners.append(at + forward * (HEAD * sx) + up * (sy * depth + 0.02) + side * sz * width)
    mesh.hull8("head", DARK_PLASTIC, corners)
    lens = at + forward * (HEAD * 0.56) - up * 0.028
    mesh.box("lens", LENS, lens, [0.2, 0.012, HEAD * 0.62] if abs(forward[2]) > 0.5 else [HEAD * 0.62, 0.012, 0.2])


class Lamp:
    name = "lamp"
    code = (Path(__file__).parent.parent / "objects",)

    def discover(self, area: Area, sources) -> list[Stub]:
        posts = registers.lamp_posts(sources, area.bbox(6.0))
        if not posts:
            return []
        lidar = registers.lidar_of(sources)
        streets = registers.Streets(sources, area.bbox(6.0))
        # Ends of the overhead wires: a register pole that a span wire hangs from carries the span, not an arm.
        strung, _ = wires.in_box(area.bbox(8.0), sources.log)
        across, _ = spans.in_box(sources, area.bbox(SPAN_LIT[0]))
        ends = np.array([w[end][:2] for w in strung + across for end in ("a", "b")] or np.zeros((0, 2)))
        middles = np.array([[(w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2] for w in across] or np.zeros((0, 2)))
        found = []
        for post in posts:
            e, n = post["e"], post["n"]
            if not area.touches(box_of(e, n, 0.5)):
                continue
            measured = lidar.pole_top(e, n, search=1.2 if "digitoitu" in post["layer"] else 0.8)
            if measured and 2.6 <= measured["top"] <= TALLEST:
                e, n = e + measured["offset"][0], n + measured["offset"][1]
            else:
                measured = None
            street = streets.towards_street(e, n)
            facts = _family(measured, street)
            arm_measured = facts["family"] == "street-arm" and "arm" not in facts.get("guessed", [])
            if len(ends) and facts["family"] != "park" and not arm_measured and float(np.hypot(*(ends - [e, n]).T).min()) <= SPAN_REACH:
                facts = {"family": "span-pole", "height": facts["height"], "basis": facts["basis"].split(";")[0] + "; a span wire ends at it",
                         "guessed": [name for name in facts.get("guessed", []) if name not in ("arm", "family")]}
            # A street lit by lamps hung on spans has no arm lamps: a pole there whose head the scan did not see is
            # a plain pole (it carries wires or signs), not a lamp with a guessed arm.
            way = streets.car(e, n, 14.0)
            if way is not None and len(middles) and facts["family"] != "span-pole" and (measured is None or "arm" in facts.get("guessed", [])) \
                    and facts["family"] != "park":
                d = middles - np.array(way["foot"])
                t = np.array(way["tangent"])
                if bool(((np.abs(d @ t) <= SPAN_LIT[0]) & (np.abs(d[:, 0] * t[1] - d[:, 1] * t[0]) <= SPAN_LIT[1])).any()):
                    facts = {"family": "span-pole", "height": facts["height"], "basis": facts["basis"].split(";")[0] + "; its street is lit by span lamps and the scan shows no head",
                             "guessed": [name for name in facts.get("guessed", []) if name != "arm"] + ["family"]}
            found.append((post, e, n, measured, street, facts))
        # A pole standing in a crown shows the scan only its lower part: beside a carriageway whose other lamps are
        # measured taller, it is as tall as they are.
        tall = [(e, n, facts["height"], facts["arm"]["reach"], streets.car(e, n, 14.0)) for _, e, n, measured, _, facts in found
                if measured and facts["family"] == "street-arm" and not facts.get("guessed")]

        def neighbours(e: float, n: float) -> np.ndarray:
            """Height and arm reach of the three nearest fully measured lamps: those of the lamp's own street when
            it has any (one street is lit by one type of lamp; the next street's may be metres taller), else of any."""
            way = streets.car(e, n, 14.0)
            rows = [(math.hypot(te - e, tn - n), height, reach, way is not None and other is not None and _same_street(way, other))
                    for te, tn, height, reach, other in tall]
            rows = [row for row in rows if row[0] <= NEIGHBOURS]
            own = [row for row in rows if row[3]]
            return np.array([row[1:3] for row in sorted(own or rows)[:3]] or np.zeros((0, 2))), bool(own)

        stubs = []
        for post, e, n, measured, street, facts in found:
            hidden = facts["family"] in ("park", "street-top") or "arm" in facts.get("guessed", [])  # no head seen by the scan
            near, own = neighbours(e, n) if street and street["distance"] <= 9.0 else (np.zeros((0, 2)), False)
            if hidden and len(near):
                top = measured["top"] if measured else 0.0
                around = lidar.around(e, n, 1.5)
                crown = int(((around[:, 3] == 5) & (around[:, 2] > top + 0.5)).sum()) if len(around) else 0
                # A measured low post without a crown over it may be a real lantern; a tall pole without its head is not.
                headless = facts["family"] != "park"
                typical = float(np.median(near[:, 0]))
                if (measured is None and own) or (typical - facts["height"] >= 1.0 and (measured is None or headless or crown >= 20)):
                    facts = {"family": "street-arm", "height": round(typical, 2), "arm": {"bearing": street["bearing"], "reach": round(float(np.median(near[:, 1])), 2)},
                             "basis": "register position; height and arm of " + ("its street's" if own else "the nearest") + " other lamps (its top is hidden)", "guessed": ["height", "arm"]}
            if facts["family"] == "street-arm" and "arm" in facts.get("guessed", []) and len(near):
                # A guessed arm is as long as the measured arms of the lamps next along the street.
                facts["arm"] = {**facts["arm"], "reach": round(float(np.median(near[:, 1])), 2)}
            ground = registers.ground_height(sources, e, n)
            if not math.isfinite(ground):
                continue
            height = facts["height"]
            facts.update({
                "at": [round(e, 2), round(n, 2), round(ground, 2)], "finish": "galvanised",
                "register": {"layer": post["layer"], "base_h": post["h"]},
                "interaction": interaction("knock-over", "cylinder", [0.12, height], 40.0 + 12.0 * height),
            })
            if measured:
                facts["measured"] = {"lidar": measured}
            stubs.append(Stub(f"lamp/{post['id']}", box_of(e, n, 3.5), [post["source"]], facts))
        return unique(stubs)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        family = description.get("family", "park")
        height = max(round(float(description.get("height", 4.5)) * 4) / 4, 2.5)
        steel = BLACK_STEEL if description.get("finish") == "black" else GALVANISED
        mesh = Mesh()
        bearing = 0.0
        if family == "street-arm":
            arm = description.get("arm") or {}
            bearing = float(arm.get("bearing", 0.0))
            reach = min(max(round(float(arm.get("reach", 1.5)) * 4) / 4, 0.75), 3.25)
            bend = min(1.8, 0.6 + reach * 0.45)
            y0, run = height - bend, max(reach - HEAD, 0.15)
            base, neck = 0.075, 0.045
            path, radii = [[0, -0.3, 0], [0, 0, 0], [0, 1.0, 0]], [base, base, base * 0.94]
            if reach >= STRAIGHT:
                # A long arm is a straight tube on a straight pole, rising a little to the head (the older street
                # lamp of the wooden-house streets); a short one is the bent top of a curved pole.
                rise = 0.12 * run
                path += [[0, height - rise - 0.45, 0], [0, height - rise - 0.1, 0.1], [0, height, run]]
                radii += [neck, 0.035, 0.03]
            else:
                path.append([0, y0, 0])
                radii.append(neck)
                for k in range(1, 9):
                    t = k / 8 * math.pi / 2
                    path.append([0, y0 + bend * math.sin(t), run * (1 - math.cos(t))])
                    radii.append(neck - 0.015 * k / 8)
            mesh.tube("pole", steel, path, radii, sides=10)
            street_head(mesh, np.array([0.0, height, run]), np.array([0.0, 0.0, 1.0]))
        elif family == "span-pole":
            mesh.tube("pole", steel, [[0, -0.3, 0], [0, 0, 0], [0, height + 0.3, 0]], [0.085, 0.085, 0.05], sides=10)
        elif family == "street-top":
            mesh.tube("pole", steel, [[0, -0.3, 0], [0, 0, 0], [0, height - 0.05, 0]], [0.085, 0.085, 0.04], sides=10)
            street_head(mesh, np.array([0.0, height, -HEAD / 2]), np.array([0.0, 0.0, 1.0]))
        else:
            top = height
            mesh.tube("pole", steel, [[0, -0.3, 0], [0, 0, 0], [0, top - 0.42, 0]], [0.06, 0.06, 0.04], sides=10)
            mesh.lathe("head", DARK_PLASTIC, [(0.04, top - 0.45), (0.07, top - 0.36), (0.07, top - 0.3), (0.26, top - 0.2),
                                              (0.27, top - 0.17), (0.12, top - 0.04), (0.0, top)], sides=12)
            mesh.lathe("lens", LENS, [(0.075, top - 0.31), (0.24, top - 0.215), (0.075, top - 0.215)], sides=12)
        return single(description, mesh.parts(), bearing)
