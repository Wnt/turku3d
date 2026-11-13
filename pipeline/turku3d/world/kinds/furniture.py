"""Street furniture from the city's registers: benches, bins, bike racks, city-bike stations, ticket machines,
boards and the other small fixed things, one entity each.

Positions and types are the registers' (`GIS:Varusteet`, `GIS:Polkupyoraparkki`, `GIS:Follariasemat`,
`GIS:Pysakointiautomaatit`). The models and the reading of the register labels are the old game's
(`turku3d.props.models`, `turku3d.props.VARUSTEET`), rebuilt as closed solids and shared instanced assets. A city-bike station is drawn as its rack only: the
street photos show no terminal at the registered stations.
Headings are rules, marked as guesses: a rack's row runs along the nearest way, a bench turns its back to the
carriageway, the rest face the street.

Schema: docs/specs/street-objects.md.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from ..api import Area, Material, Output, Stub
from ..objects import registers
from ..objects.common import BLACK_STEEL, GALVANISED, box_of, describe, interaction, painted_metal, painted_wood, single, unique
from ..objects.mesh import Mesh, rgb_hex

VERSION = 1

# Register labels (type, species and model, lower case) to the model drawn; None: not drawn. First match wins.
LABELS = (
    ("seinätuhkakuppi", None), ("tuhkakuppi", "ashtray"), ("pelastustikas", None), ("pelastus", "lifebuoy"),
    ("syväsäiliö", "waste-container"), ("roskasäiliö", "waste-container"), ("molok", "waste-container"),
    ("deepload", "waste-container"), ("rosk", "bin"), ("kakkapussi", "dispenser"), ("latauspiste", "charger"),
    ("pyloni", "info-pylon"), ("viitta", "guide-post"), ("talvikunnossapito", "path-sign"), ("talvihoito", "path-sign"),
    ("opaste", "info-board"), ("opastaulu", "info-board"), ("nimikyltti", "info-board"), ("kalusteryhm", "table"),
    ("pöytä", "table"), ("poyta", "table"), ("leikki", None), ("penkki", "bench"), ("sohva", "bench"), ("tuoli", "bench"),
    ("istutus", "planter"), ("kukka", "planter"), ("amme", "planter"), ("pollari", "bollard"), ("pyör", "bike-rack"),
    ("pyor", "bike-rack"), ("wc", None), ("katos", None), ("keinu", None), ("peli", None),
)
BIN_PAINT = (("vihreä", "green"), ("musta", "black"))
KNOCKED = {"bin", "ashtray", "path-sign", "dispenser", "guide-post", "bollard"}  # the rest stay put when hit
# Collision box (x, y, z) per model, metres.
SIZE = {
    "bench": (1.8, 0.9, 0.6), "bin": (0.56, 0.9, 0.56), "waste-container": (1.6, 1.22, 1.6), "ashtray": (0.22, 1.04, 0.22),
    "table": (1.8, 0.76, 1.5), "info-board": (1.25, 1.9, 0.1), "info-pylon": (0.55, 2.4, 0.25), "guide-post": (0.8, 2.6, 0.8),
    "path-sign": (0.42, 1.55, 0.1), "lifebuoy": (0.72, 1.7, 0.2), "dispenser": (0.22, 1.2, 0.2), "charger": (0.36, 1.4, 0.26),
    "planter": (1.2, 0.63, 1.2), "bollard": (0.2, 0.95, 0.2), "bike-station": (2.4, 0.8, 0.7), "ticket-machine": (0.4, 1.7, 0.3),
}
STAND = 0.8  # m between the stands of a bike rack

GREY = painted_metal("#6f7377")
GREEN = painted_metal("#2f6b3c")
ORANGE = Material("plastic", {"colour": "#e8641b"}, rgb_hex("#e8641b"), 0.6, 0.0)
CREAM = Material("plastic", {"colour": "#e9e4d2"}, rgb_hex("#e9e4d2"), 0.6, 0.0)
SCREEN = Material("plastic", {"colour": "#15181c"}, rgb_hex("#15181c"), 0.3, 0.0)
WOOD = painted_wood("#8a6a45", "weathered")
SHELTER_STEEL = painted_metal("#2c3033")
# The building family's glass with a frosted interior: a pale pane without a room behind it.
SHELTER_PANE = Material("glass", {"colour": "#aebcc2", "finish": "gloss", "interior": "frosted"}, rgb_hex("#aebcc2"), 0.15, 0.0, double_sided=True)


def classify(row: dict) -> tuple[str | None, str]:
    """(model, variant) of a register row: the bin's paint, the rack's number of stands."""
    if row["kind"]:
        model = row["kind"]
    else:
        label = " ".join((row["type"], row["species"], row["model"])).casefold()
        model = next((model for needle, model in LABELS if needle in label), None)
    variant = ""
    if model == "bin":
        variant = next((paint for needle, paint in BIN_PAINT if needle in row["model"].casefold()), "")
    elif model == "bike-rack":
        try:
            places = int(float(row["places"] or 0))
        except ValueError:
            places = 0
        variant = str(min(8, max(2, (places + 1) // 2))) if places else "3"  # one stand holds two bikes
    return model, variant


def _post(mesh: Mesh, part: str, material: Material, x: float, z: float, radius: float, height: float, base: float = 0.0) -> None:
    mesh.tube(part, material, [[x, base, z], [x, base + height, z]], radius, sides=10)


@lru_cache(maxsize=256)
def model_parts(model: str, variant: str = "") -> tuple:
    """One shared asset per model and variant: base at y = 0, front towards +Z, a rack's row along X."""
    m = Mesh()
    if model == "bench":
        for x in (-0.65, 0.65):
            m.box("frame", GREY, [x, 0.23, 0], [0.08, 0.46, 0.50])
        for z in (-0.20, -0.06, 0.08, 0.22):
            m.box("slats", WOOD, [0, 0.47, z], [1.8, 0.07, 0.11])
        for y in (0.68, 0.83):
            m.box("slats", WOOD, [0, y, -0.24], [1.8, 0.12, 0.06])
        for x in (-0.76, 0.76):
            m.box("frame", GREY, [x, 0.65, 0], [0.05, 0.05, 0.58])
    elif model == "bin":
        body = {"green": GREEN, "black": BLACK_STEEL}.get(variant, GALVANISED)
        _post(m, "body", body, 0, 0, 0.24, 0.82)
        _post(m, "lid", GREY, 0, 0, 0.28, 0.08, 0.82)
        m.box("slot", SCREEN, [0, 0.68, 0.238], [0.28, 0.16, 0.015])
    elif model == "waste-container":
        _post(m, "body", WOOD, 0, 0, 0.75, 1.12)
        _post(m, "lid", GALVANISED, 0, 0, 0.8, 0.1, 1.12)
        m.box("hatch", SCREEN, [0, 0.95, 0.76], [0.45, 0.3, 0.04])
    elif model == "ashtray":
        _post(m, "post", GALVANISED, 0, 0, 0.04, 0.8)
        _post(m, "bowl", GREY, 0, 0, 0.11, 0.26, 0.78)
    elif model == "table":
        m.box("top", WOOD, [0, 0.73, 0], [1.8, 0.06, 0.76])
        for z in (-0.62, 0.62):
            m.box("seats", WOOD, [0, 0.44, z], [1.8, 0.05, 0.26])
        for x in (-0.7, 0.7):
            m.box("frame", GALVANISED, [x, 0.36, 0], [0.08, 0.72, 1.5])
    elif model == "info-board":
        for x in (-0.58, 0.58):
            m.box("posts", GALVANISED, [x, 0.95, 0], [0.07, 1.9, 0.07])
        m.box("board", BLACK_STEEL, [0, 1.4, 0], [1.25, 0.9, 0.05])
        m.box("sheet", CREAM, [0, 1.4, 0.031], [1.15, 0.8, 0.012])
    elif model == "info-pylon":
        m.box("body", BLACK_STEEL, [0, 1.2, 0], [0.55, 2.4, 0.25])
        m.box("sheet", CREAM, [0, 1.35, 0.131], [0.45, 1.6, 0.012])
    elif model == "guide-post":
        _post(m, "post", GALVANISED, 0, 0, 0.05, 2.6)
        m.box("arms", CREAM, [0.38, 2.35, 0], [0.7, 0.16, 0.025])
        m.box("arms", CREAM, [0, 2.1, 0.38], [0.025, 0.16, 0.7])
    elif model == "path-sign":
        _post(m, "post", GALVANISED, 0, 0, 0.03, 1.55)
        m.box("plate", GREY, [0, 1.35, 0.04], [0.42, 0.3, 0.02])
    elif model == "lifebuoy":
        m.box("post", GREY, [0, 0.85, 0], [0.09, 1.7, 0.09])
        m.box("case", ORANGE, [0, 1.3, 0.1], [0.72, 0.72, 0.13])
    elif model == "dispenser":
        _post(m, "post", GALVANISED, 0, 0, 0.04, 1.15)
        m.box("box", GREEN, [0, 1.0, 0.07], [0.22, 0.32, 0.12])
    elif model == "charger":
        m.box("body", GREY, [0, 0.7, 0], [0.36, 1.4, 0.26])
        m.box("screen", SCREEN, [0, 1.1, 0.136], [0.22, 0.26, 0.012])
    elif model == "planter":
        m.box("box", GREY, [0, 0.3, 0], [1.2, 0.6, 1.2])
        m.box("soil", GREEN, [0, 0.615, 0], [1.06, 0.03, 1.06])
    elif model in ("bike-rack", "bike-station"):
        count = int(variant) if variant.isdigit() else 3
        for x in ([(k - (count - 1) / 2) * STAND for k in range(count)] if model == "bike-rack" else (-0.9, 0.0, 0.9)):
            # An inverted U of bent tube standing across the row.
            path = [[x, -0.1, -0.30], [x, 0.66, -0.30]]
            path += [[x, 0.66 + 0.12 * math.sin(a), -0.18 - 0.12 * math.cos(a)] for a in (math.pi / 6, math.pi / 3, math.pi / 2)]
            path += [[x, 0.66 + 0.12 * math.sin(a), 0.18 + 0.12 * math.cos(a)] for a in (math.pi / 2, math.pi / 3, math.pi / 6)]
            path += [[x, 0.66, 0.30], [x, -0.1, 0.30]]
            m.tube("stands", GALVANISED, path, 0.024, sides=8)
    elif model == "ticket-machine":
        m.box("body", GREY, [0, 0.85, 0], [0.4, 1.7, 0.3])
        m.box("screen", SCREEN, [0, 1.3, 0.156], [0.27, 0.28, 0.012])
        m.box("sign", painted_metal("#0a4ea1"), [0, 1.6, 0.156], [0.3, 0.2, 0.012])
    elif model == "bus-shelter":
        # A city bus shelter on the register's footprint (variant "length x depth"): a dark steel frame under a flat
        # roof, panes on the back and both ends, a bench along the back; the open long side is the front.
        length, depth = (float(v) for v in variant.split("x"))
        for x in (-length / 2 + 0.04, length / 2 - 0.04):
            for z in (-depth / 2 + 0.04, depth / 2 - 0.04):
                m.box("frame", SHELTER_STEEL, [x, 1.15, z], [0.07, 2.3, 0.07])
        m.box("roof", SHELTER_STEEL, [0, 2.36, 0.1], [length + 0.2, 0.1, depth + 0.4])
        m.box("rails", SHELTER_STEEL, [0, 0.18, -depth / 2 + 0.04], [length - 0.1, 0.05, 0.05])
        m.box("panes", SHELTER_PANE, [0, 1.25, -depth / 2 + 0.04], [length - 0.15, 2.05, 0.012])
        for x in (-length / 2 + 0.04, length / 2 - 0.04):
            m.box("panes", SHELTER_PANE, [x, 1.25, 0], [0.012, 2.05, depth - 0.15])
        m.box("bench", WOOD, [0, 0.46, -depth / 2 + 0.3], [min(length - 1.0, 2.4), 0.05, 0.36])
        for x in (-0.8, 0.8):
            m.box("bench-legs", SHELTER_STEEL, [x, 0.22, -depth / 2 + 0.3], [0.05, 0.44, 0.3])
    else:  # bollard
        _post(m, "post", BLACK_STEEL, 0, 0, 0.09, 0.95)
    return tuple(m.parts())


class Furniture:
    name = "furniture"
    code = (Path(__file__).parent.parent / "objects",)

    def discover(self, area: Area, sources) -> list[Stub]:
        rows = registers.furniture(sources, area.bbox(2.0))
        shelters = registers.bus_shelters(sources, area.bbox(4.0))
        if not rows and not shelters:
            return []
        streets = registers.Streets(sources, area.bbox(2.0))
        stubs = []
        for row in shelters:
            e, n = row["e"], row["n"]
            ground = registers.ground_height(sources, e, n)
            if not area.touches(box_of(e, n, 0.5)) or not math.isfinite(ground):
                continue
            # The open side is the long side nearer the carriageway (a guess: the registers do not say).
            street = streets.towards_street(e, n, reach=25.0)
            sides = ((row["axis"] + 90.0) % 360, (row["axis"] + 270.0) % 360)
            bearing = min(sides, key=lambda side: abs((side - street["bearing"] + 180.0) % 360 - 180.0)) if street else sides[0]
            facts = {
                "at": [e, n, round(ground, 2)], "model": "bus-shelter", "variant": f"{row['length']}x{row['depth']}",
                "bearing": round(bearing, 1), "basis": "canopy outline of the base map beside a bus stop point; open towards the carriageway",
                "register": {"layer": "kanta:Rakennus", "type": "katos", "length": row["length"], "depth": row["depth"]},
                "guessed": ["bearing", "model"],
                "interaction": interaction("fixed", "box", [row["length"], 2.4, row["depth"]]),
            }
            stubs.append(Stub(f"furniture/{row['id']}", box_of(e, n, 4.0), [row["source"]], facts))
        for row in rows:
            e, n = row["e"], row["n"]
            if not area.touches(box_of(e, n, 0.5)):
                continue
            model, variant = classify(row)
            if model is None:
                continue
            ground = registers.ground_height(sources, e, n)
            if not math.isfinite(ground):
                continue
            guessed = []
            if model in ("bike-rack", "bike-station"):
                way = streets.any(e, n, 25.0)
                # The row (asset +X) runs along the way: the front (+Z) is a quarter turn to its left.
                bearing = (math.degrees(math.atan2(way["tangent"][0], way["tangent"][1])) - 90.0) % 360 if way else 0.0
                basis = "row along the nearest way"
            else:
                street = streets.towards_street(e, n)
                bearing = street["bearing"] if street else 0.0
                if model == "bench":
                    bearing = (bearing + 180.0) % 360
                basis = "back to the carriageway" if model == "bench" else "facing the carriageway"
            guessed.append("bearing")
            size = (max(1, int(variant or 3)) * STAND, 0.8, 0.7) if model == "bike-rack" else SIZE[model]
            layer = row["layer"].split(":")[-1]
            facts = {
                "at": [e, n, round(ground, 2)], "model": model, "variant": variant, "bearing": round(bearing, 1), "basis": basis,
                "register": {"layer": row["layer"], "type": row["type"], "species": row["species"], "model": row["model"],
                             "maker": row["maker"], "places": row["places"]},
                "guessed": guessed,
                "interaction": interaction("knock-over" if model in KNOCKED else "fixed", "box", size,
                                           25.0 if model in KNOCKED else None),
            }
            stubs.append(Stub(f"furniture/{layer[:3].lower()}{row['id']}", box_of(e, n, 2.0), [row["source"]], facts))
        return unique(stubs)

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        return describe(self.name, VERSION, stub, stored)

    def compile(self, description: dict) -> Output:
        parts = model_parts(description.get("model", "bollard"), str(description.get("variant", "")))
        return single(description, list(parts), float(description.get("bearing", 0.0)))
