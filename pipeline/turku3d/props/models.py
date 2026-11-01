"""Original low-poly furniture and small procedural Finnish sign atlases (CC0)."""

from __future__ import annotations

import io
import math
from functools import cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from turku3d.package.gltf import GlbMesh

SUPPORTED_SIGNS = {"E1", "E2", "E6", "C37", "C38", "B5", "B6", "D3.1", "H20"}
# Palette columns of the shared prop atlas (16 px each): steel, wood, light grey, yellow, black,
# cream, park green, lifebuoy orange.
COLORS = [
    "#454e53",
    "#a97943",
    "#d0d6d3",
    "#f4c72d",
    "#242b2e",
    "#f3f1dd",
    "#3e6a47",
    "#d8582a",
]
STEEL, WOOD, GREY, YELLOW, BLACK, CREAM, GREEN, ORANGE = range(len(COLORS))
BIN_BODY = {"": STEEL, "green": GREEN, "black": BLACK}


@cache
def atlas(code: str = "") -> bytes:
    im = Image.new("RGB", (256, 128), COLORS[0])
    d = ImageDraw.Draw(im)
    for k, color in enumerate(COLORS):
        d.rectangle((k * 16, 0, k * 16 + 15, 127), fill=color)
    blue, red, yellow = "#0057a5", "#d9202c", "#ffda36"
    d.rectangle((128, 0, 255, 127), fill=COLORS[2])
    if code in ("C37", "C38"):
        d.ellipse((133, 5, 250, 122), fill=red, outline=yellow, width=2)
        d.ellipse((145, 17, 238, 110), fill=blue)
        d.line((153, 101, 231, 23), fill=red, width=11)
        if code == "C37":
            d.line((153, 23, 231, 101), fill=red, width=11)
    elif code == "B5":
        d.polygon([(132, 8), (252, 8), (192, 120)], fill=red)
        d.polygon([(150, 19), (234, 19), (192, 98)], fill=yellow)
    elif code == "B6":
        octagon = [
            (
                192 + 60 * math.cos(math.tau * (i + 0.5) / 8),
                64 + 60 * math.sin(math.tau * (i + 0.5) / 8),
            )
            for i in range(8)
        ]
        d.polygon(octagon, fill=red, outline="white", width=4)
        d.text((192, 64), "STOP", font=ImageFont.load_default(size=30), anchor="mm", fill="white")
    elif code in ("E1", "E2", "E6", "D3.1", "H20"):
        d.rectangle((131, 3, 252, 124), fill=blue, outline="white", width=4)
        if code == "D3.1":
            d.rectangle((128, 0, 255, 127), fill=COLORS[2])
            d.ellipse((132, 4, 251, 123), fill=blue, outline="white", width=3)
        if code == "E1":
            d.polygon([(192, 14), (142, 108), (242, 108)], fill="white")
            for x in range(154, 231, 16):
                d.line((x, 98, x + 6, 90), fill="#151515", width=5)
            d.ellipse((186, 40, 197, 51), fill="black")
            d.line((191, 55, 184, 74, 171, 88), fill="black", width=6)
            d.line((184, 74, 205, 89), fill="black", width=6)
            d.line((176, 64, 188, 60, 206, 70), fill="black", width=5)
        elif code == "E6":
            d.rectangle((143, 17, 240, 111), fill=yellow)
            d.rounded_rectangle((157, 34, 227, 88), radius=7, fill="black")
            d.rectangle((162, 40, 221, 62), fill=yellow)
            for x in (166, 210):
                d.ellipse((x, 79, x + 10, 97), fill="black")
        elif code == "D3.1":
            d.line((170, 29, 217, 91), fill="white", width=13)
            d.polygon([(194, 88), (226, 102), (224, 68)], fill="white")
        else:
            font = ImageFont.load_default(size=84 if code == "E2" else 45)
            d.text((192, 64), "P" if code == "E2" else "€", font=font, anchor="mm", fill="white")
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


class Shape:
    def __init__(self):
        self.positions, self.indices, self.uvs = [], [], []

    def face(self, points, color=0, texture=False):
        start = len(self.positions)
        self.positions.extend(points)
        self.uvs.extend(
            [(0.505, 0.99), (0.995, 0.99), (0.995, 0.01), (0.505, 0.01)]
            if texture
            else [(float((color * 16 + 8) / 256), 0.5)] * len(points)
        )
        self.indices.extend((start, start + i, start + i + 1) for i in range(1, len(points) - 1))

    def box(self, center, size, color=0):
        x, y, z = center
        a, b, c = np.array(size) / 2
        p = [
            (x - a, y - b, z - c),
            (x + a, y - b, z - c),
            (x + a, y + b, z - c),
            (x - a, y + b, z - c),
            (x - a, y - b, z + c),
            (x + a, y - b, z + c),
            (x + a, y + b, z + c),
            (x - a, y + b, z + c),
        ]
        for face in [
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 4, 7, 3),
            (1, 2, 6, 5),
            (3, 7, 6, 2),
            (0, 1, 5, 4),
        ]:
            self.face([p[i] for i in face], color)

    def cylinder(self, x, z, radius, height, color=0, base=0):
        n = 10
        a = [
            (
                x + radius * math.cos(i * 2 * math.pi / n),
                base,
                z + radius * math.sin(i * 2 * math.pi / n),
            )
            for i in range(n)
        ]
        b = [(p[0], base + height, p[2]) for p in a]
        self.face(a, color)
        self.face(b[::-1], color)
        for i in range(n):
            j = (i + 1) % n
            self.face([a[i], b[i], b[j], a[j]], color)


def stands(variant: str) -> list[float]:
    """Stand x positions of a bike rack: the register's place count sets the length."""
    n = int(variant) if variant.isdigit() else 3
    return [(k - (n - 1) / 2) * 0.8 for k in range(n)]


@cache
def model(kind: str, code: str = "") -> GlbMesh:
    """One prototype per kind and variant (``code``: sign face, bin colour, bike-rack stands)."""
    s = Shape()
    if kind == "bench":
        for x in (-0.65, 0.65):
            s.box((x, 0.23, 0), (0.08, 0.46, 0.50))
        for z in (-0.20, -0.06, 0.08, 0.22):
            s.box((0, 0.47, z), (1.8, 0.07, 0.11), 1)
        for y in (0.68, 0.83):
            s.box((0, y, -0.24), (1.8, 0.12, 0.06), 1)
        for x in (-0.76, 0.76):
            s.box((x, 0.65, 0), (0.05, 0.05, 0.58))
    elif kind == "bin":
        s.cylinder(0, 0, 0.24, 0.82, BIN_BODY.get(code, STEEL))
        s.cylinder(0, 0, 0.28, 0.08, GREY, 0.82)
        s.box((0, 0.68, 0.238), (0.28, 0.16, 0.015), BLACK)
    elif kind == "waste_container":
        # Deep collection container: only the wood-clad top 1.2 m stands above the paving.
        s.cylinder(0, 0, 0.75, 1.12, WOOD)
        s.cylinder(0, 0, 0.8, 0.1, STEEL, 1.12)
        s.box((0, 0.95, 0.76), (0.45, 0.3, 0.04), BLACK)
    elif kind == "ashtray":
        s.cylinder(0, 0, 0.04, 0.8, STEEL)
        s.cylinder(0, 0, 0.11, 0.26, GREY, 0.78)
    elif kind == "table":
        s.box((0, 0.73, 0), (1.8, 0.06, 0.76), WOOD)
        for z in (-0.62, 0.62):
            s.box((0, 0.44, z), (1.8, 0.05, 0.26), WOOD)
        for x in (-0.7, 0.7):
            s.box((x, 0.36, 0), (0.08, 0.72, 1.5), STEEL)
    elif kind == "info_board":
        for x in (-0.58, 0.58):
            s.box((x, 0.95, 0), (0.07, 1.9, 0.07), STEEL)
        s.box((0, 1.4, 0), (1.25, 0.9, 0.05), BLACK)
        s.box((0, 1.4, 0.03), (1.15, 0.8, 0.012), CREAM)
    elif kind == "info_pylon":
        s.box((0, 1.2, 0), (0.55, 2.4, 0.25), BLACK)
        s.box((0, 1.35, 0.13), (0.45, 1.6, 0.012), CREAM)
    elif kind == "guide_post":
        s.cylinder(0, 0, 0.05, 2.6, STEEL)
        s.box((0.38, 2.35, 0), (0.7, 0.16, 0.025), CREAM)
        s.box((0, 2.1, 0.38), (0.025, 0.16, 0.7), CREAM)
    elif kind == "path_sign":
        s.cylinder(0, 0, 0.03, 1.55, STEEL)
        s.box((0, 1.35, 0.04), (0.42, 0.3, 0.02), GREY)
    elif kind == "lifebuoy":
        s.box((0, 0.85, 0), (0.09, 1.7, 0.09), GREY)
        s.box((0, 1.3, 0.1), (0.72, 0.72, 0.13), ORANGE)
        s.box((0, 1.3, 0.17), (0.32, 0.32, 0.01), GREY)
    elif kind == "dispenser":
        s.cylinder(0, 0, 0.04, 1.15, STEEL)
        s.box((0, 1.0, 0.07), (0.22, 0.32, 0.12), GREEN)
    elif kind == "charger":
        s.box((0, 0.7, 0), (0.36, 1.4, 0.26), GREY)
        s.box((0, 1.1, 0.135), (0.22, 0.26, 0.012), BLACK)
        s.box((0, 0.75, 0.135), (0.08, 0.12, 0.03), GREEN)
    elif kind == "planter":
        s.box((0, 0.3, 0), (1.2, 0.6, 1.2), GREY)
        s.box((0, 0.61, 0), (1.06, 0.03, 1.06), GREEN)
    elif kind in ("bike_rack", "bike_station"):
        for x in stands(code) if kind == "bike_rack" else (-0.9, 0, 0.9):
            for z in (-0.30, 0.30):
                s.cylinder(x, z, 0.035, 0.78, 2)
            s.box((x, 0.78, 0), (0.07, 0.07, 0.67), 2)
        if kind == "bike_station":
            s.box((-1.5, 0.65, 0), (0.35, 1.3, 0.25), 3)
            s.box((-1.5, 1, 0.135), (0.23, 0.25, 0.02), 4)
    elif kind == "parking_meter":
        s.box((0, 0.85, 0), (0.4, 1.7, 0.3))
        s.box((0, 1.3, 0.16), (0.27, 0.28, 0.02), 4)
        s.box((0, 1.6, 0.16), (0.3, 0.2, 0.02), 3)
    elif kind == "lamp":
        s.cylinder(0, 0, 0.085, 6, 2)
        s.box((0, 5.94, 0.55), (0.10, 0.12, 1.2), 2)
        s.box((0, 5.89, 1.05), (0.35, 0.13, 0.65), 5)
    elif kind in ("sign", "bus_stop"):
        s.cylinder(0, 0, 0.035, 2.7, 2)
        if code in ("C37", "C38", "D3.1"):
            outline = [
                (0.32 * math.cos(i * math.tau / 24), 0.32 * math.sin(i * math.tau / 24))
                for i in range(24)
            ]
        elif code == "B5":
            outline = [(-0.33, 0.30), (0, -0.33), (0.33, 0.30)]
        elif code == "B6":
            outline = [
                (
                    0.33 * math.cos(math.tau * (i + 0.5) / 8),
                    0.33 * math.sin(math.tau * (i + 0.5) / 8),
                )
                for i in range(8)
            ]
        else:
            outline = [(-0.33, -0.33), (0.33, -0.33), (0.33, 0.33), (-0.33, 0.33)]
        front = [(x, 2.4 + y, 0.065) for x, y in outline]
        back = [(x, 2.4 + y, 0.035) for x, y in outline]
        s.face(front, 2)
        s.uvs[-len(outline) :] = [
            (0.5 + (x / 0.66 + 0.5) * 0.5, 0.5 - y / 0.66) for x, y in outline
        ]
        s.face(back[::-1], 2)
        for i in range(len(outline)):
            j = (i + 1) % len(outline)
            s.face([back[i], back[j], front[j], front[i]], 2)
    else:
        s.cylinder(0, 0, 0.09, 0.95)
        s.cylinder(0, 0, 0.092, 0.10, 5, 0.75)
    return GlbMesh(
        f"{kind}_{code}",
        np.array(s.positions),
        np.array(s.indices),
        uvs=np.array(s.uvs),
        image=atlas(code if kind in ("sign", "bus_stop") else ""),
        extras={
            "layer": "props",
            "object_id": f"prototype:{kind}:{code}",
            "material_key": f"props_{kind}",
            "texture_uv": "normalized",
        },
    )
