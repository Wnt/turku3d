"""Far ground shared by the distant-scenery producers: colours measured from the near materials, border skirts."""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

# One table for the converter (tools/remote-drafts/scenery.mjs), the survey producers and the client.
PALETTE = Path(__file__).resolve().parents[3] / 'game/src/render/farGroundPalette.json'


@lru_cache(maxsize=1)
def survey_colours():
    """Survey/L2 ground classes (linear RGB): land, street, woods, water."""
    return {key: tuple(value) for key, value in json.loads(PALETTE.read_text())['survey'].items()}


def skirt(grid, count, drop):
    """Two-sided vertical skirt triangles, shape (k, 3, 3), hanging `drop` metres below the outer edges of a
    row-major (count + 1)^2 vertex grid. Coarse borders meet denser neighbours (full tiles, other levels) with
    T-junctions; the skirt closes those cracks without moving any surface vertex."""
    n = count + 1
    grid = np.asarray(grid, dtype=float)
    edges = [(z * n + x, z * n + x + 1) for z in (0, count) for x in range(count)]
    edges += [(z * n + x, (z + 1) * n + x) for x in (0, count) for z in range(count)]
    down = np.array((0.0, drop, 0.0))
    triangles = []
    for i, j in edges:
        a, b = grid[i], grid[j]
        a2, b2 = a - down, b - down
        triangles += [(a, b, b2), (a, b2, a2), (a, b2, b), (a, a2, b2)]
    return np.asarray(triangles, dtype=float)
