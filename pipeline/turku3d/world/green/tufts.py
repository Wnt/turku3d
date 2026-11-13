"""Unmown ground: the meadow grass and flowers of meadows and wild verges, and the undergrowth of a forest floor.

Where they grow comes from the registers, as the old game's meadow rule had it (`coarse/meadows.py`): the green-area
register (`GIS:Viheralueet`) keeps each part to a class (`Kunnossapitoluokka_koodi`): A1 to A5 (meadows, open areas,
landscape fields) are cut once or twice a summer and EH is not kept at all; a part surveyed as meadow (`Niitty`) is
one whatever its class; M classes and the surface `Metsä` are forest. OpenStreetMap's meadow, grassland, scrub and
heath, and its woods, fill in where the city keeps no ground. Paving, roofs and planting beds are left out.

A cell's census stores this as a raster (`cover`: rows from the south, STEP metres a letter, `m` meadow, `f` forest
floor) with the ground's relief on a coarser grid, so that compiling needs no lidar. `cards` grows the raster into
leaf cards of the nature atlas: grass tufts and flowering stems on a meadow, ferns, saplings and thin grass in a
forest. The ground kind draws the ground under them as unmown grass.
"""

from __future__ import annotations

import numpy as np

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


def cards(cover: dict, seed: int, far: bool = False) -> dict | None:
    """Leaf cards over a cell's raster, in metres east, up (the stored ground level) and north of the cell's
    south-west corner: {"positions", "normals", "uvs", "colors", "wind", "triangles"} for the foliage family."""
    kinds = np.array([list(row) for row in cover["rows"]])
    relief = np.asarray(cover["relief"], float)
    step, relief_step = cover["step"], cover["reliefStep"]
    rng = np.random.default_rng([seed, 811, int(far)])
    parts = []
    for kind, plants in PLANTS.items():
        rows, cols = np.nonzero(kinds == kind)
        if not len(rows):
            continue
        for cell, density, across, tone in plants:
            expected = density * step * step * (FAR[0] if far else 1.0)
            each = rng.poisson(expected, len(rows))
            total = int(each.sum())
            if not total:
                continue
            e = (np.repeat(cols, each) + rng.random(total)) * step
            n = (np.repeat(rows, each) + rng.random(total)) * step
            # The ground under each card, between the relief's nodes.
            u, v = np.clip(e / relief_step, 0, relief.shape[1] - 1.001), np.clip(n / relief_step, 0, relief.shape[0] - 1.001)
            c0, r0 = u.astype(int), v.astype(int)
            fu, fv = u - c0, v - r0
            ground = (relief[r0, c0] * (1 - fu) * (1 - fv) + relief[r0, c0 + 1] * fu * (1 - fv) + relief[r0 + 1, c0] * (1 - fu) * fv + relief[r0 + 1, c0 + 1] * fu * fv)
            size = rng.uniform(*across, total) * (FAR[1] if far else 1.0)
            yaw = rng.uniform(0, 2 * np.pi, total)
            side = np.stack([np.cos(yaw), np.zeros(total), np.sin(yaw)], axis=1)
            lean = rng.normal(0.0, 0.12, (total, 2))
            up = treegen.unit(np.stack([lean[:, 0], np.ones(total), lean[:, 1]], axis=1))
            foot = np.stack([e, ground - 0.04, n], axis=1)
            grid = np.array([[-0.5, 0.0], [0.5, 0.0], [-0.5, 1.0], [0.5, 1.0]])
            positions = foot[:, None] + side[:, None] * (grid[:, 0][None, :, None] * size[:, None, None]) + up[:, None] * (grid[:, 1][None, :, None] * size[:, None, None])
            u0, u1, v0, v1 = treegen.cell_uv(cell)
            uv = np.tile(np.stack([u0 + (u1 - u0) * (grid[:, 0] + 0.5), v1 + (v0 - v1) * grid[:, 1]], axis=1), (total, 1))
            vary = rng.uniform(-1.0, 1.0, (total, 2))
            # COLOR_0 of the foliage family: ambient occlusion (dark at the foot), brightness, warmth.
            shade = np.tile(np.array([0.55, 0.55, 1.0, 1.0]), total)
            colors = np.stack([shade, np.repeat(tone[0] * (1 + 1.5 * treegen.CARD_TONE[0] * vary[:, 0]), 4),
                               np.repeat(1 + tone[1] + treegen.CARD_TONE[1] * vary[:, 1], 4)], axis=1)
            wind = np.stack([np.full(total * 4, 0.05), np.tile(grid[:, 1] * 0.6, total), np.repeat(rng.random(total), 4), np.ones(total * 4)], axis=1)
            parts.append({"positions": positions.reshape(-1, 3), "normals": np.tile(np.array([[0.0, 1.0, 0.0]]), (total * 4, 1)), "uvs": uv,
                          "colors": colors, "wind": wind,
                          "triangles": (np.array([[0, 1, 2], [1, 3, 2]])[None] + (np.arange(total) * 4)[:, None, None]).reshape(-1, 3)})
    return treegen.join(parts)
