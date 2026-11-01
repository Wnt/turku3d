"""Load the six bundled Kenney CC0 OBJ car bodies, retaining their palette UVs."""

import io
from functools import cache
from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).with_name("assets")
# Width (including mirrors), height, length in metres; model forward is +Z.
MODELS = {
    "sedan": (1.85, 1.48, 4.55),
    "hatchback-sports": (1.80, 1.46, 4.15),
    "suv": (1.96, 1.78, 4.70),
    "van": (1.98, 2.05, 5.00),
    "sedan-sports": (1.84, 1.36, 4.60),
    "suv-luxury": (1.98, 1.72, 4.95),
}
# Share of each body among parked cars.
MIX = {
    "sedan": 0.20,
    "hatchback-sports": 0.18,
    "suv": 0.17,
    "van": 0.10,
    "sedan-sports": 0.17,
    "suv-luxury": 0.18,
}
COLORS = {
    "silver": (165, 174, 181),
    "white": (224, 228, 225),
    "charcoal": (54, 61, 69),
    "blue": (38, 78, 126),
    "red": (138, 38, 39),
    "beige": (154, 143, 118),
}


@cache
def model(name):
    vertices, uvs, normals, pos, uv, norm, indices = [], [], [], [], [], [], []
    for line in (ASSETS / f"{name}.obj").read_text().splitlines():
        s = line.split()
        if not s:
            continue
        if s[0] == "v":
            vertices.append([float(v) for v in s[1:4]])
        elif s[0] == "vt":
            uvs.append([float(s[1]), 1 - float(s[2])])
        elif s[0] == "vn":
            normals.append([float(v) for v in s[1:4]])
        elif s[0] == "f":
            start = len(pos)
            for vertex in s[1:]:
                v, t, n = (int(i) - 1 for i in vertex.split("/"))
                pos.append(vertices[v])
                uv.append(uvs[t])
                norm.append(normals[n])
            indices.extend((start, start + i, start + i + 1) for i in range(1, len(s) - 2))
    pos = np.asarray(pos, dtype=np.float64)
    lo, hi = pos.min(0), pos.max(0)
    scale = np.asarray(MODELS[name]) / (hi - lo)
    pos = (pos - [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]) * scale
    norm = np.asarray(norm) / scale
    norm /= np.linalg.norm(norm, axis=1)[:, None]
    return pos, np.asarray(indices, np.uint32), norm, np.asarray(uv)


@cache
def palette(name, color):
    image = Image.open(ASSETS / "colormap.png").convert("RGB")
    a = np.asarray(image).copy()
    # Authored paint swatch; leave glass, tyres, lamps and trim pixels untouched.
    column = {
        "sedan": 6,
        "suv": 3,
        "hatchback-sports": 3,
        "van": 7,
        "sedan-sports": 6,
        "suv-luxury": 4,
    }[name]
    shade = np.linspace(1.0, 0.78, 128)[:, None, None]
    a[128:256, column * 64 : (column + 1) * 64] = np.asarray(COLORS[color])[None, None, :] * shade
    stream = io.BytesIO()
    Image.fromarray(a).save(stream, format="PNG")
    return stream.getvalue()
