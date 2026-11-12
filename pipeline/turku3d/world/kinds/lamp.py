"""Street lamps: one entity per pole of the street-light register, shaped by what the lidar measures of it.

The register (frn:CityFurniture_LOD2) gives a base point and nothing else. The lidar gives the pole's true axis,
its height and the bearing and reach of its arm; where it shows no pole (scanned before the pole was set, or
hidden under a crown) the family is a guess from the street beside it and is marked so. Families:

- `street-arm`  a tapered steel pole with a curved arm and a flat head over the carriageway
- `street-top`  a tall pole with the head on its top
- `park`        a 3 to 6 m post with a post-top lantern
- `wire-hung`   a luminaire on a span wire across the street (discovered from the lidar, see `spans`)

Schema: docs/specs/street-objects.md.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..api import Area, Output, Stub
from ..objects import registers
from ..objects.common import BLACK_STEEL, DARK_PLASTIC, GALVANISED, LENS, box_of, describe, interaction, single
from ..objects.mesh import Mesh

VERSION = 1
STREET_MIN = 6.5  # m: a pole this tall lights a carriageway
HEAD = 0.6  # m: length of a street-light head


def _family(measured: dict | None, street: dict | None) -> dict:
    """Family, height and arm of a pole from its lidar measurement, else from the street beside it."""
    if measured and measured["top"] >= 2.6:
        top, arm = measured["top"], measured.get("arm")
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
        stubs = []
        for post in posts:
            e, n = post["e"], post["n"]
            if not area.touches(box_of(e, n, 0.5)):
                continue
            measured = lidar.pole_top(e, n, search=1.2 if "digitoitu" in post["layer"] else 0.8)
            if measured and measured["top"] >= 2.6:
                e, n = e + measured["offset"][0], n + measured["offset"][1]
            else:
                measured = None
            facts = _family(measured, streets.towards_street(e, n))
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
        return stubs

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
            path.append([0, y0, 0])
            radii.append(neck)
            for k in range(1, 9):
                t = k / 8 * math.pi / 2
                path.append([0, y0 + bend * math.sin(t), run * (1 - math.cos(t))])
                radii.append(neck - 0.015 * k / 8)
            mesh.tube("pole", steel, path, radii, sides=10)
            street_head(mesh, np.array([0.0, height, run]), np.array([0.0, 0.0, 1.0]))
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
