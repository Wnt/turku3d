"""Finnish traffic sign faces by sign code (Road Traffic Act 729/2018 numbering, A–I), drawn as flat geometry.

`face(code, label, note, blue)` returns the plate outline and its paint layers in metres, centred on the plate.
`face_mesh` turns the layers into non-overlapping coloured triangles, so a face is one coplanar mesh that needs
neither textures nor depth offsets. Every code of the city's sign register gets at least the face of its class (the
right shape and colours); the codes that street views show most get their symbol too. Drawings are original and
schematic, sized as the normal urban sign (0.64 m discs and squares, 0.9 m triangles).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
import shapely
import shapely.prepared
from shapely import affinity

from .glyphs import text_shape, text_width
from .mesh import srgb

RED, YELLOW, BLUE = srgb(196, 30, 36), srgb(250, 196, 0), srgb(0, 78, 158)
WHITE, BLACK, GREEN, BROWN = srgb(240, 240, 238), srgb(22, 22, 24), srgb(0, 120, 70), srgb(118, 72, 34)
GREY, ORANGE, SIGNAL_GREEN = srgb(150, 152, 155), srgb(235, 120, 20), srgb(40, 170, 80)
DISC, SQUARE, TRIANGLE, PLATE_W = 0.64, 0.64, 0.90, 0.64
OLD_CODES = {"511": "E1", "521": "E2", "331": "C17", "333": "C19", "363b": "C34", "872": "H24", "332": "C18"}


@dataclass
class Face:
    code: str
    outline: shapely.Polygon  # the plate
    layers: list[tuple[tuple[float, float, float], object]] = field(default_factory=list)  # paint order, bottom first
    additional: bool = False  # an additional plate (hangs under a main sign)
    known: bool = True  # False: only the class face was drawn (the symbol is not in the catalogue)

    @property
    def size(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.outline.bounds
        return x1 - x0, y1 - y0


# -- shapes ------------------------------------------------------------------------------------------------------

def disc(radius: float, centre=(0.0, 0.0)):
    return shapely.Point(centre).buffer(radius, quad_segs=10)


def rect(width: float, height: float, centre=(0.0, 0.0), corner: float = 0.0):
    box = shapely.box(centre[0] - width / 2, centre[1] - height / 2, centre[0] + width / 2, centre[1] + height / 2)
    return box.buffer(-corner).buffer(corner, quad_segs=3) if corner else box


def triangle(side: float, down: bool = False, corner: float = 0.03):
    height = side * math.sqrt(3) / 2
    shape = shapely.Polygon([(-side / 2, -height / 2), (side / 2, -height / 2), (0, height / 2)])
    shape = shape.buffer(-corner).buffer(corner, quad_segs=3)
    return affinity.scale(shape, 1, -1, origin=(0, 0)) if down else shape


def inset(shape, distance: float):
    return shape.buffer(-distance, join_style="mitre") if distance else shape


def stroke(points, width: float, flat: bool = False):
    return shapely.LineString(points).buffer(width / 2, quad_segs=3, cap_style="flat" if flat else "round")


def arrow(points, width: float, head: float | None = None):
    """An arrow along a polyline; its head is at the last point."""
    head = head or width * 2.6
    (ax, ay), (bx, by) = points[-2], points[-1]
    length = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / length, (by - ay) / length
    neck = (bx - ux * head, by - uy * head)
    shaft = shapely.LineString([*points[:-1], neck]).buffer(width / 2, quad_segs=2, cap_style="flat", join_style="mitre")
    tip = shapely.Polygon([(bx, by), (neck[0] - uy * head * 0.62, neck[1] + ux * head * 0.62),
                           (neck[0] + uy * head * 0.62, neck[1] - ux * head * 0.62)])
    return shaft.union(tip)


def place(shape, scale: float = 1.0, centre=(0.0, 0.0), mirror: bool = False, turn: float = 0.0):
    shape = affinity.scale(shape, -scale if mirror else scale, scale, origin=(0, 0))
    if turn:
        shape = affinity.rotate(shape, turn, origin=(0, 0))
    return affinity.translate(shape, centre[0], centre[1])


# -- pictograms, each about 1 unit tall and centred ---------------------------------------------------------------

def walker():
    parts = [disc(0.085, (0.03, 0.40)), stroke([(0.0, 0.27), (0.03, 0.0)], 0.13),
             stroke([(0.03, 0.0), (0.15, -0.2), (0.2, -0.44)], 0.075), stroke([(0.02, 0.0), (-0.1, -0.22), (-0.25, -0.42)], 0.075),
             stroke([(0.0, 0.25), (0.15, 0.1), (0.27, 0.13)], 0.06), stroke([(0.0, 0.25), (-0.16, 0.12), (-0.2, -0.03)], 0.06)]
    return shapely.union_all(parts)


def bicycle():
    wheels = [disc(0.2, (x, -0.13)).difference(disc(0.155, (x, -0.13))) for x in (-0.3, 0.3)]
    frame = [stroke(p, 0.045) for p in ([(-0.3, -0.13), (-0.08, 0.17), (0.2, 0.17), (0.3, -0.13)],
                                        [(-0.08, 0.17), (0.0, -0.13), (-0.3, -0.13)], [(0.0, -0.13), (0.2, 0.17)],
                                        [(-0.17, 0.24), (-0.03, 0.24)], [(-0.1, 0.24), (-0.08, 0.17)],
                                        [(0.2, 0.17), (0.17, 0.3), (0.29, 0.33)])]
    return shapely.union_all(wheels + frame)


def car():
    body = shapely.Polygon([(-0.42, -0.1), (-0.42, 0.04), (-0.27, 0.08), (-0.16, 0.23), (0.14, 0.23), (0.27, 0.08),
                            (0.42, 0.04), (0.42, -0.1)]).buffer(0.02)
    windows = shapely.union_all([shapely.Polygon([(-0.2, 0.09), (-0.13, 0.19), (-0.02, 0.19), (-0.02, 0.09)]),
                                 shapely.Polygon([(0.02, 0.09), (0.02, 0.19), (0.12, 0.19), (0.2, 0.09)])])
    return body.difference(windows).union(disc(0.09, (-0.25, -0.11))).union(disc(0.09, (0.25, -0.11)))


def bus():
    body = rect(0.9, 0.42, (0, 0.04), 0.05)
    windows = shapely.union_all([rect(0.13, 0.13, (x, 0.11)) for x in (-0.32, -0.16, 0.0, 0.16)] + [rect(0.1, 0.24, (0.34, 0.05))])
    return body.difference(windows).union(disc(0.085, (-0.26, -0.18))).union(disc(0.085, (0.24, -0.18)))


def truck():
    return shapely.union_all([rect(0.5, 0.36, (-0.14, 0.08)), rect(0.22, 0.26, (0.26, 0.03), 0.03),
                              disc(0.085, (-0.26, -0.13)), disc(0.085, (0.24, -0.13))])


def wheelchair():
    return shapely.union_all([disc(0.07, (-0.05, 0.4)), stroke([(-0.05, 0.3), (-0.03, 0.02), (0.18, 0.02), (0.28, -0.25)], 0.07),
                              stroke([(-0.04, 0.17), (0.14, 0.17)], 0.06),
                              disc(0.25, (-0.05, -0.15)).difference(disc(0.19, (-0.05, -0.15))).difference(rect(0.4, 0.35, (0.13, 0.05)))])


def letter(text: str, height: float, weight: float = 0.2, centre=(0.0, 0.0)):
    return text_shape(text, height, weight, centre)


def text_block(lines: list[str], width: float, height: float, centre=(0.0, 0.0), weight: float = 0.14):
    """Lines of text fitted into a width x height box."""
    lines = [line for line in lines if line.strip()][:4]
    if not lines:
        return shapely.Polygon()
    cap = min(height / (len(lines) * 1.55), 0.085)
    longest = max(text_width(line, 1.0) for line in lines)
    cap = min(cap, width / longest) if longest else cap
    step = cap * 1.55
    top = centre[1] + step * (len(lines) - 1) / 2
    return shapely.union_all([text_shape(line, cap, weight, (centre[0], top - k * step)) for k, line in enumerate(lines)])


# -- classes -------------------------------------------------------------------------------------------------------

def warning(symbol=None) -> tuple[object, list]:
    outer = triangle(TRIANGLE)
    layers = [(RED, outer), (YELLOW, affinity.translate(affinity.scale(outer, 0.7, 0.7, origin=(0, 0)), 0, -0.035))]
    if symbol is not None:
        layers.append((BLACK, symbol))
    return outer, layers


def prohibition(symbol=None, inner=YELLOW, slash: bool = False) -> tuple[object, list]:
    outer = disc(DISC / 2)
    layers = [(RED, outer), (inner, disc(DISC / 2 * 0.76))]
    if symbol is not None:
        layers.append((BLACK, symbol))
    if slash:
        layers.append((RED, stroke([(-0.19, 0.19), (0.19, -0.19)], 0.055, flat=True).intersection(outer)))
    return outer, layers


def mandatory(symbol=None) -> tuple[object, list]:
    outer = disc(DISC / 2)
    layers = [(WHITE, outer), (BLUE, disc(DISC / 2 - 0.012))]
    if symbol is not None:
        layers.append((WHITE, symbol))
    return outer, layers


def panel(width: float, height: float, colour=BLUE, rim=WHITE, corner: float = 0.03) -> tuple[object, list]:
    outer = rect(width, height, corner=corner)
    return outer, [(rim, outer), (colour, inset(outer, 0.014))]


def zone(inner_layers: list, grey: bool = False) -> tuple[object, list]:
    """A zone sign: the round sign on a white plate over ALUE / ZON."""
    outer = rect(0.64, 0.9, corner=0.03)
    layers = [(BLACK, outer), (WHITE, inset(outer, 0.012))]
    for colour, shape in inner_layers:
        tone = (GREY if colour == RED else WHITE if colour in (YELLOW, BLUE) else colour) if grey else colour
        layers.append((tone, place(shape, 0.8, (0, 0.14))))
    layers.append((BLACK, text_block(["ALUE", "ZON"], 0.4, 0.2, (0, -0.3), 0.16)))
    if grey:
        layers.append((BLACK, shapely.union_all([stroke([(-0.3 + d, -0.43), (0.24 + d, 0.43)], 0.012, flat=True) for d in (-0.05, 0.0, 0.05, 0.1)]).intersection(outer)))
    return outer, layers


def speed(label: str, default: str) -> str:
    found = re.search(r"(\d+)\s*km", label or "")
    return found.group(1) if found else default


def bracket(label: str) -> str:
    found = re.search(r"\(([^)]*\d[^)]*)\)", label or "")
    return found.group(1).replace("km/h", "").strip().upper() if found else ""


def main_face(code: str, label: str) -> tuple[object, list, bool] | None:
    """(outline, layers, known) of a main sign."""
    head = code.split(".")[0]
    series = code[:1]
    if series == "A":
        symbols = {
            "A10": shapely.union_all([place(disc(0.09), 1, (x, -0.13)).intersection(rect(0.4, 0.1, (x, -0.08))) for x in (-0.1, 0.1)] + [rect(0.44, 0.03, (0, -0.14))]),
            "A15": place(walker(), 0.36, (0, -0.07)), "A17": shapely.union_all([place(walker(), 0.3, (-0.08, -0.08)), place(walker(), 0.22, (0.1, -0.11))]),
            "A1": arrow([(0.06, -0.2), (0.06, -0.05), (-0.06, 0.06)] if code.endswith(".2") else [(-0.06, -0.2), (-0.06, -0.05), (0.06, 0.06)], 0.05),
        }
        if head == "A23":
            outer, layers = warning(rect(0.11, 0.3, (0, -0.07), 0.02))
            layers += [(RED, disc(0.035, (0, 0.02))), (YELLOW, disc(0.035, (0, -0.07))), (SIGNAL_GREEN, disc(0.035, (0, -0.16)))]
            return outer, layers, True
        symbol = symbols.get(head)
        outer, layers = warning(symbol if symbol is not None else shapely.union_all([rect(0.05, 0.2, (0, -0.02)), disc(0.03, (0, -0.19))]))
        return outer, layers, symbol is not None or head == "A33"
    if head == "B1" or head == "B2":
        outer = affinity.rotate(rect(0.5, 0.5, corner=0.03), 45, origin=(0, 0))
        layers = [(WHITE, outer), (BLACK, inset(outer, 0.02)), (WHITE, inset(outer, 0.03)), (YELLOW, inset(outer, 0.075))]
        if head == "B2":
            layers.append((BLACK, shapely.union_all([stroke([(-0.26 + d, -0.2 - d), (0.2 + d, 0.26 - d)], 0.012, True) for d in (-0.04, -0.02, 0.0, 0.02, 0.04)]).intersection(outer)))
        return outer, layers, True
    if head == "B5":
        outer = triangle(TRIANGLE, down=True)
        return outer, [(RED, outer), (YELLOW, affinity.translate(affinity.scale(outer, 0.7, 0.7, origin=(0, 0)), 0, 0.035))], True
    if head == "B6":
        outer = shapely.Polygon([(0.32 * math.cos(math.radians(22.5 + 45 * k)), 0.32 * math.sin(math.radians(22.5 + 45 * k))) for k in range(8)])
        return outer, [(WHITE, outer), (RED, inset(outer, 0.015)), (WHITE, letter("STOP", 0.13, 0.17))], True
    if series == "B":
        outer = triangle(TRIANGLE, down=True)
        return outer, [(RED, outer), (YELLOW, affinity.scale(outer, 0.7, 0.7, origin=(0, 0)))], False
    if series == "C":
        if head == "C17":
            outer = disc(DISC / 2)
            return outer, [(RED, outer), (YELLOW, rect(0.46, 0.11))], True
        if head in ("C37", "C38", "C39", "C40", "C41", "C43"):
            bars = [stroke([(-0.2, 0.2), (0.2, -0.2)], 0.06, True)] + ([stroke([(-0.2, -0.2), (0.2, 0.2)], 0.06, True)] if head in ("C37", "C41") else [])
            outer, layers = prohibition(inner=BLUE)
            layers.append((RED, shapely.union_all(bars)))
            if head in ("C39", "C40"):
                outer, layers = zone(layers, grey=head == "C40")
            return outer, layers, True
        if head in ("C32", "C33", "C34", "C35"):
            digits = speed(label, "40")
            outer, layers = prohibition(letter(digits, 0.24 if len(digits) < 3 else 0.19, 0.2))
            if head in ("C33", "C35"):
                layers = [(WHITE, outer), (BLACK, disc(DISC / 2).difference(disc(DISC / 2 - 0.012))), (GREY, letter(digits, 0.24, 0.2)),
                          (BLACK, shapely.union_all([stroke([(-0.24 + d, -0.2), (0.16 + d, 0.2)], 0.012, True) for d in (0.0, 0.04, 0.08)]))]
            if head in ("C34", "C35"):
                outer, layers = zone(layers, grey=head == "C35")
            return outer, layers, True
        symbols = {
            "C2": shapely.union_all([place(car(), 0.36, (0, -0.07)), place(bicycle(), 0.26, (0, 0.11))]),
            "C3": place(truck(), 0.38), "C8": place(truck(), 0.38), "C9": place(bus(), 0.4), "C12": place(bicycle(), 0.42),
            "C13": place(walker(), 0.36), "C18": arrow([(0.07, -0.16), (0.07, 0.05), (-0.14, 0.05)], 0.055),
            "C19": arrow([(-0.07, -0.16), (-0.07, 0.05), (0.14, 0.05)], 0.055),
        }
        text = bracket(label)
        if head in ("C21", "C22", "C23", "C24", "C25", "C26", "C27") and text:
            outer, layers = prohibition(text_block([text], 0.36, 0.2, weight=0.18))
            return outer, layers, True
        symbol = symbols.get(head)
        outer, layers = prohibition(symbol, slash=head in ("C18", "C19"))
        if head == "C8":
            layers[2] = (ORANGE, layers[2][1])
        return outer, layers, symbol is not None or head == "C1"
    if series == "D":
        up, right, left = [(0, -0.2), (0, 0.22)], [(-0.21, 0), (0.22, 0)], [(0.21, 0), (-0.22, 0)]
        turn_right, turn_left = [(-0.08, -0.2), (-0.08, 0.06), (0.2, 0.06)], [(0.08, -0.2), (0.08, 0.06), (-0.2, 0.06)]
        arrows = {
            "D1.1": [right], "D1.2": [left], "D1.3": [up], "D1.4": [turn_right], "D1.5": [turn_left],
            "D1.6": [up, [(0, -0.2), (0, -0.02), (0.2, -0.02)]], "D1.7": [up, [(0, -0.2), (0, -0.02), (-0.2, -0.02)]],
            "D1.8": [[(0, -0.2), (0, 0.02), (0.2, 0.02)], [(0, -0.2), (0, 0.02), (-0.2, 0.02)]],
            "D1.9": [up, [(0, -0.2), (0, -0.02), (0.2, -0.02)], [(0, -0.2), (0, -0.02), (-0.2, -0.02)]],
            "D3.1": [[(-0.16, 0.16), (0.17, -0.17)]], "D3.2": [[(0.16, 0.16), (-0.17, -0.17)]],
            "D3.3": [[(0, 0.2), (0.19, -0.14)], [(0, 0.2), (-0.19, -0.14)]],
        }
        if code in arrows or head in arrows:
            outer, layers = mandatory(shapely.union_all([arrow(points, 0.065) for points in arrows.get(code) or arrows[head]]))
            return outer, layers, True
        if head == "D2":
            ring = disc(0.17).difference(disc(0.11)).difference(shapely.Polygon([(0, 0), (0.3, 0.1), (0.3, -0.1)]))
            outer, layers = mandatory(ring)
            return outer, layers, True
        divider_h, divider_v = rect(0.52, 0.02), rect(0.02, 0.52)
        symbols = {
            "D4": place(walker(), 0.4), "D5": place(bicycle(), 0.44),
            "D6": shapely.union_all([place(bicycle(), 0.3, (0, 0.14)), place(walker(), 0.24, (0, -0.15)), divider_h]),
            "D7.1": shapely.union_all([place(bicycle(), 0.24, (-0.15, 0)), place(walker(), 0.3, (0.14, 0)), divider_v]),
            "D7.2": shapely.union_all([place(bicycle(), 0.24, (0.15, 0)), place(walker(), 0.3, (-0.14, 0)), divider_v]),
        }
        symbol = symbols.get(code) or symbols.get(head)
        outer, layers = mandatory(symbol)
        return outer, layers, symbol is not None
    if series == "E":
        if head == "E1":
            outer, layers = panel(SQUARE, SQUARE)
            mark = shapely.Polygon([(-0.25, -0.22), (0.25, -0.22), (0, 0.25)])
            return outer, layers + [(WHITE, mark), (BLACK, place(walker(), 0.3, (0, -0.06)))], True
        if head in ("E2", "E3"):
            outer, layers = panel(SQUARE, SQUARE)
            return outer, layers + [(WHITE, letter("P", 0.4, 0.2))], True
        if head == "E4":
            outer, layers = panel(PLATE_W, 0.32)
            cars = shapely.union_all([rect(0.1, 0.2, (x, 0), 0.02) for x in (-0.18, 0.0, 0.18)])
            return outer, layers + [(WHITE, affinity.rotate(cars, -25 if code.endswith(".3") else 0, origin=(0, 0)))], True
        if head in ("E6", "E7"):
            outer, layers = panel(SQUARE, SQUARE)
            return outer, layers + [(WHITE, rect(0.46, 0.46, corner=0.02)), (BLACK, place(bus(), 0.42))], True
        if head == "E8":
            outer, layers = panel(SQUARE, SQUARE)
            return outer, layers + [(WHITE, letter("TAXI", 0.13, 0.17))], True
        if head == "E9":
            outer, layers = panel(SQUARE, SQUARE)
            return outer, layers + [(WHITE, place(bus(), 0.4, (0, 0.08))), (WHITE, letter("TAXI", 0.07, 0.16, (0, -0.17)))], True
        if head == "E13":
            outer, layers = panel(SQUARE, SQUARE)
            return outer, layers + [(WHITE, place(bicycle(), 0.36, (0.04, 0))), (WHITE, rect(0.025, 0.5, (-0.24, 0)))], True
        if head == "E14":
            outer, layers = panel(0.9, 0.3)
            return outer, layers + [(WHITE, arrow([(-0.36, 0), (0.38, 0)], 0.13, 0.2))], True
        if head in ("E24", "E25", "E26", "E27"):
            outer, layers = panel(0.9, 0.64)
            figures = [place(walker(), 0.4, (-0.22, 0)), place(walker(), 0.26, (-0.02, -0.07))]
            if head in ("E24", "E25"):
                figures.append(place(car(), 0.36, (0.25, -0.1)))
            layers.append((WHITE, shapely.union_all(figures)))
            if head in ("E25", "E27"):
                layers.append((RED, stroke([(-0.42, -0.28), (0.42, 0.28)], 0.045, True).intersection(outer)))
            return outer, layers, True
        outer, layers = panel(SQUARE, SQUARE)
        return outer, layers, False
    if series == "F":
        if head == "F24":
            outer, layers = panel(SQUARE, SQUARE)
            return outer, layers + [(WHITE, rect(0.1, 0.34, (0, -0.07))), (RED, rect(0.3, 0.1, (0, 0.15)))], True
        if head in ("F46", "F47"):
            outer, layers = panel(SQUARE, SQUARE)
            return outer, layers + [(WHITE, letter("P", 0.36, 0.2))], True
        if head in ("F13", "F16", "F19", "F20"):
            colour, ink = (BLUE, WHITE) if head in ("F19", "F20") else (WHITE, BLACK)
            outer = shapely.Polygon([(-0.5, -0.11), (0.38, -0.11), (0.5, 0), (0.38, 0.11), (-0.5, 0.11)])
            bars = shapely.union_all([rect(0.5, 0.035, (-0.14, 0.03)), rect(0.32, 0.03, (-0.23, -0.04))])
            return outer, [(ink if colour == BLUE else BLACK, outer), (colour, inset(outer, 0.012)), (ink, bars)], True
        if head in ("F29", "F30", "F31", "F32", "F28"):
            colour = {"F29": RED, "F30": YELLOW, "F31": WHITE, "F32": BLUE, "F28": GREEN}[head]
            outer = rect(0.4, 0.26, corner=0.02)
            return outer, [(BLACK if colour in (YELLOW, WHITE) else WHITE, outer), (colour, inset(outer, 0.012)),
                           (BLACK if colour in (YELLOW, WHITE) else WHITE, rect(0.2, 0.08))], True
        outer, layers = panel(0.9, 0.4)
        bars = shapely.union_all([rect(0.6, 0.04, (-0.05, 0.08)), rect(0.44, 0.04, (-0.13, -0.06))])
        return outer, layers + [(WHITE, bars)], False
    if series == "G":
        if head in ("G28", "G23", "G41"):
            outer, layers = panel(SQUARE, SQUARE, colour=BROWN)
            return outer, layers + [(WHITE, disc(0.12).difference(disc(0.07)))], False
        outer, layers = panel(SQUARE, SQUARE)
        return outer, layers + [(WHITE, rect(0.42, 0.42, corner=0.02)), (BLACK, disc(0.07))], False
    return None


def additional_face(code: str, label: str, note: str, blue: bool) -> tuple[object, list, bool]:
    """An additional plate: black on yellow, or white on blue under a blue sign."""
    head = code.split("_")[0].split(".")[0]
    full = code.split("_")[0]
    ground, ink = (BLUE, WHITE) if blue else (YELLOW, BLACK)
    lines = [line.strip() for line in (note or "").replace("\r", "").split("\n") if line.strip()]
    lines = [line for line in lines if line.lower() not in ("sininen", "siirtokehoitus")]

    def plate(height: float, width: float = PLATE_W):
        outer = rect(width, height, corner=0.02)
        return outer, [(ink, outer), (ground, inset(outer, 0.01))]

    if head in ("H9", "H10", "H11"):
        outer, layers = plate(0.42, 0.32)
        shapes = {"H9": [arrow([(0, 0), (0, 0.16)], 0.05), arrow([(0, 0), (0, -0.16)], 0.05)],
                  "H10": [arrow([(0, -0.15), (0, 0.16)], 0.05)], "H11": [arrow([(0, 0.15), (0, -0.16)], 0.05)]}[head]
        return outer, layers + [(ink, shapely.union_all(shapes))], True
    if head in ("H1", "H2"):
        outer, layers = plate(0.22)
        return outer, layers + [(ink, arrow([(-0.2, 0), (0.22, 0)], 0.045))], True
    if head == "H12":
        symbols = {"H12.1": car, "H12.2": bus, "H12.3": truck, "H12.7": wheelchair, "H12.8": bicycle, "H12.9": bicycle, "H12.10": bicycle}
        symbol = symbols.get(full)
        outer, layers = plate(0.32)
        if symbol is not None:
            layers.append((ink, place(symbol(), 0.24 if full != "H12.7" else 0.26)))
        return outer, layers, symbol is not None
    if head == "H19":
        outer, layers = plate(0.32)
        dial = rect(0.2, 0.22, (-0.16, 0), 0.02)
        layers += [(WHITE if not blue else WHITE, dial), (BLUE if not blue else ground, disc(0.07, (-0.16, -0.02)))]
        layers.append((ink, text_block(lines[:2] or ["2 H"], 0.26, 0.2, (0.13, 0))))
        return outer, layers, True
    if head == "H23":
        outer, layers = plate(0.32)
        return outer, layers + [(ink, shapely.union_all([place(bicycle(), 0.2, (0.02, 0)), arrow([(-0.24, -0.1), (-0.24, 0.11)], 0.03),
                                                         arrow([(0.26, 0.1), (0.26, -0.11)], 0.03)]))], True
    if head == "H22":
        outer, layers = plate(0.5, 0.5)
        return outer, layers + [(ink, shapely.union_all([stroke([(0, -0.2), (0, 0.0), (0.18, 0.12)], 0.07, True), stroke([(0, 0), (0, 0.2)], 0.025, True),
                                                         stroke([(0, 0), (-0.18, 0.0)], 0.025, True)]))], True
    defaults = {"H20": ["MAKSULLINEN", "AVGIFTSBELAGD"], "H17": ["8 - 17"], "H18": ["2 H"], "H3": ["0 - 50 M"], "H4": ["100 M"],
                "H13": ["P"], "H14": ["A"]}
    words = (defaults.get(head, []) if head == "H20" else []) + lines if head == "H20" else (lines or defaults.get(head, []))
    words = words[:4]
    outer, layers = plate({0: 0.2, 1: 0.2, 2: 0.3, 3: 0.4, 4: 0.5}[len(words)])
    width, height = outer.bounds[2] * 2 - 0.06, outer.bounds[3] * 2 - 0.05
    if words:
        layers.append((ink, text_block(words, width, height)))
    else:
        layers.append((ink, shapely.union_all([rect(0.4, 0.025, (0, 0.035)), rect(0.28, 0.025, (0, -0.035))])))
    return outer, layers, bool(words)


def normal_code(code: str) -> str:
    """The register's code in the current numbering (it still holds a few pre-2020 numbers)."""
    code = (code or "").strip()
    base = code.split("_")[0]
    return OLD_CODES.get(base, OLD_CODES.get(code, base))


def is_sign(code: str) -> bool:
    """Whether a register row is a sign face at all (the register also lists ticket machines)."""
    return normal_code(code)[:1] in "ABCDEFGH" and not normal_code(code)[:1].isdigit()


def face(code: str, label: str = "", note: str = "", blue: bool = False) -> Face | None:
    code = normal_code(code)
    if code[:1] == "H":
        outline, layers, known = additional_face(code, label, note, blue or (note or "").strip().lower() == "sininen")
        return Face(code, outline, layers, additional=True, known=known)
    found = main_face(code, label)
    if found is None:
        return None
    return Face(code, found[0], found[1], known=found[2])


def is_blue(code: str) -> bool:
    """Whether additional plates under this main sign are white on blue."""
    code = normal_code(code)
    return code[:1] in "EFG" or code.split(".")[0] in ("C37", "C38", "C39") and False


def face_mesh(face: Face) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(positions (n, 2), triangles, colours (n, 3)) of the face as one planar partition: the linework of every
    layer is noded together and each cell takes the colour of the topmost layer over it, so neighbouring cells share
    their border vertices exactly (no overlap, no T-junction) and the outer border is the plate outline."""
    layers = [(colour, shape.intersection(face.outline)) for colour, shape in face.layers if shape is not None and not shape.is_empty]
    # Noded on a 0.1 mm grid, the precision the packer snaps to, so no two border vertices merge afterwards.
    lines = shapely.set_precision(shapely.union_all([face.outline.boundary] + [shape.boundary for _, shape in layers]), 1e-4)
    lines = shapely.set_precision(shapely.union_all([lines]), 1e-4)
    cells = shapely.polygonize([lines])
    prepared = [(colour, shapely.prepared.prep(shape)) for colour, shape in layers]
    positions, triangles, colours, count = [], [], [], 0
    for cell in cells.geoms:
        if cell.area < 1e-8:
            continue
        inner = cell.representative_point()
        if not face.outline.contains(inner):
            continue
        colour = next((colour for colour, shape in reversed(prepared) if shape.contains(inner)), GREY)
        # A constrained Delaunay triangulation keeps every border vertex (ear clipping drops collinear ones, which
        # would leave T-junctions against the neighbouring cells).
        with np.errstate(invalid="ignore"):
            corners = shapely.get_coordinates(shapely.constrained_delaunay_triangles(cell)).reshape(-1, 4, 2)[:, :3]
        u, v = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
        cross = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
        corners[cross < 0] = corners[cross < 0][:, ::-1]
        corners = corners[np.abs(cross) > 1e-12]
        points = np.round(corners.reshape(-1, 2), 4)
        faces = np.arange(len(points)).reshape(-1, 3)
        positions.append(points)
        triangles.append(faces + count)
        colours.append(np.tile(colour, (len(points), 1)))
        count += len(points)
    if not positions:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3))
    return np.concatenate(positions), np.concatenate(triangles), np.concatenate(colours)


def border_edges(positions: np.ndarray, triangles: np.ndarray) -> list[tuple[int, int]]:
    """Directed edges (a, b) of a planar mesh that only one triangle uses: its outer border (and holes)."""
    keys = {tuple(p): k for k, p in reversed(list(enumerate(map(tuple, np.round(positions, 4)))))}
    weld = np.array([keys[tuple(p)] for p in np.round(positions, 4)])
    count: dict[tuple[int, int], int] = {}
    directed = []
    for a, b, c in triangles:
        for i, j in ((a, b), (b, c), (c, a)):
            wi, wj = int(weld[i]), int(weld[j])
            count[(min(wi, wj), max(wi, wj))] = count.get((min(wi, wj), max(wi, wj)), 0) + 1
            directed.append((wi, wj))
    return [(i, j) for i, j in directed if count[(min(i, j), max(i, j))] == 1]
