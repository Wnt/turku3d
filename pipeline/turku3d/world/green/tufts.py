"""Unmown ground: the meadow grass and flowers of meadows and wild verges, and the undergrowth of a forest floor.

Where they grow comes from the registers, as the old game's meadow rule had it (`coarse/meadows.py`): the green-area
register (`GIS:Viheralueet`) keeps each part to a class (`Kunnossapitoluokka_koodi`): A1 to A5 (meadows, open areas,
landscape fields) are cut once or twice a summer and EH is not kept at all; a part surveyed as meadow (`Niitty`) is
one whatever its class; M classes and the surface `Metsä` are forest. OpenStreetMap's meadow, grassland, scrub and
heath, and its woods, fill in where the city keeps no ground. Paving, roofs and planting beds are left out.

A cell's census stores this as a raster (`cover`: rows from the south, STEP metres a letter, `m` meadow, `f` forest
floor) with the ground's relief on a coarser grid, so that compiling needs no lidar. The cards themselves (grass
tufts and flowering stems on a meadow, ferns, saplings and thin grass in a forest, on the nature atlas) are not
stored: `ground` makes the raster's covered squares into a flat mesh at the ground (`_TUFT` per vertex: 0 meadow,
1 forest floor) whose material `params.tufts` carries the plants, and the client grows the cards over it
(game/src/world/location/families/sprays.ts) and does not draw the mesh. The ground kind draws the ground under them
as unmown grass.
"""

from __future__ import annotations

import numpy as np

from ..api import Material
from . import lidar, treegen

STEP = 1.25  # metres a raster cell
RELIEF = 2.5  # metres between the relief's nodes
MEADOW_CLASSES = ("A1", "A2", "A3", "A4", "A5", "EH")
OSM_MEADOW = {"landuse": ("meadow",), "natural": ("grassland", "scrub", "heath")}
OSM_FOREST = {"landuse": ("forest",), "natural": ("wood",)}
# What grows on a square metre: (atlas cell, cards, metres across a card (low, high), (brightness, warmth)).
# Many small tufts close to the ground's own green read as long grass; few large pale ones read as weeds on a lawn.
PLANTS = {
    "m": (("grass", 3.2, (0.35, 0.65), (0.8, 0.03)), ("meadow", 0.5, (0.7, 1.1), (0.9, 0.06))),
    "f": (("grass", 1.2, (0.3, 0.5), (0.68, 0.0)), ("rowan", 0.2, (0.45, 0.8), (0.78, 0.0))),
}
FAR = (0.25, 1.5)  # the distant level: this share of the cards, this much larger


def kind_of(use: str, grade: str, surface: str) -> str | None:
    """`m`, `f` or nothing for a part of the green-area register."""
    surface = surface.lower()
    if "mets" in surface or grade.startswith("M"):
        return "f"
    if "niitty" in surface or use == "Niitty" or grade in MEADOW_CLASSES:
        return "m"
    return None


def cover(cell_box, window: lidar.Window, areas: list[tuple[str, object]], bare) -> dict | None:
    """The cell's raster. `areas` are (kind, polygon), the first that covers a point wins; `bare` is a geometry
    nothing grows on (paving, beds, water). Nothing at all gives None."""
    import shapely

    count = int(round((cell_box[2] - cell_box[0]) / STEP))
    e, n = np.meshgrid(cell_box[0] + (np.arange(count) + 0.5) * STEP, cell_box[1] + (np.arange(count) + 0.5) * STEP)
    kinds = np.full((count, count), ".", "U1")
    cell = shapely.box(*cell_box)
    for kind, shape in areas:
        if shape.is_empty or not shape.intersects(cell):
            continue
        kinds[(kinds == ".") & shapely.contains_xy(shape, e, n)] = kind
    if not (kinds != ".").any():
        return None
    if bare is not None and not bare.is_empty:
        kinds[shapely.contains_xy(bare, e, n)] = "."
    rows = np.clip((n / lidar.GRID).astype(int) - window.row0, 0, window.roof.shape[0] - 1)
    cols = np.clip((e / lidar.GRID).astype(int) - window.col0, 0, window.roof.shape[1] - 1)
    kinds[window.roof[rows, cols] >= 1.0] = "."
    if not (kinds != ".").any():
        return None
    nodes = int(round((cell_box[2] - cell_box[0]) / RELIEF)) + 1
    ne, nn = np.meshgrid(cell_box[0] + np.arange(nodes) * RELIEF, cell_box[1] + np.arange(nodes) * RELIEF)
    relief = np.asarray(window.ground_height(ne.ravel(), nn.ravel()), float).reshape(nodes, nodes)
    return {"step": STEP, "rows": ["".join(row) for row in kinds], "reliefStep": RELIEF, "relief": [[round(float(v), 2) for v in row] for row in relief]}


KINDS = ("m", "f")  # the `_TUFT` value of a kind is its index here


def ground(cover: dict) -> dict | None:
    """The covered squares of a cell's raster as a mesh at the ground (metres east, up, north of the cell's south-west
    corner), four vertices a square with `tuft` = the kind's index; or None."""
    kinds = np.array([list(row) for row in cover["rows"]])
    relief = np.asarray(cover["relief"], float)
    step, relief_step = cover["step"], cover["reliefStep"]
    rows, cols = np.nonzero(np.isin(kinds, KINDS))
    if not len(rows):
        return None
    corner = np.array([[0, 0], [1, 0], [0, 1], [1, 1]])
    e = ((cols[:, None] + corner[None, :, 0]) * step).ravel()
    n = ((rows[:, None] + corner[None, :, 1]) * step).ravel()
    u, v = np.clip(e / relief_step, 0, relief.shape[1] - 1.001), np.clip(n / relief_step, 0, relief.shape[0] - 1.001)
    c0, r0 = u.astype(int), v.astype(int)
    fu, fv = u - c0, v - r0
    height = (relief[r0, c0] * (1 - fu) * (1 - fv) + relief[r0, c0 + 1] * fu * (1 - fv) + relief[r0 + 1, c0] * (1 - fu) * fv + relief[r0 + 1, c0 + 1] * fu * fv)
    count = len(rows)
    kind = np.repeat([KINDS.index(k) for k in kinds[rows, cols]], 4).astype(float)
    return {"positions": np.stack([e, height, n], axis=1), "normals": np.tile([[0.0, 1.0, 0.0]], (count * 4, 1)),
            "uvs": np.zeros((count * 4, 2)), "colors": np.ones((count * 4, 1)), "tuft": kind,
            "triangles": (np.array([[0, 2, 1], [1, 2, 3]])[None] + (np.arange(count) * 4)[:, None, None]).reshape(-1, 3)}


def material(far: bool = False) -> Material:
    """The family `tufts` of the ground mesh: what grows on each kind (atlas cell, cards a square metre, metres
    across, brightness and warmth), for the client to grow; the mesh itself is not drawn."""
    plants = [[{"cell": [round(v, 6) for v in treegen.cell_uv(cell)], "density": density, "across": list(across), "tone": list(tone)}
               for cell, density, across, tone in PLANTS[kind]] for kind in KINDS]
    return Material("tufts", {"tufts": {"plants": plants, "share": FAR[0] if far else 1.0, "grow": FAR[1] if far else 1.0,
                                        "vary": list(treegen.CARD_TONE)}}, (0.05, 0.1, 0.03), 1.0)
