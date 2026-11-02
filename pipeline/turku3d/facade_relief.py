"""Recess complete program openings without changing their glass/frame layout.

The paint partition already removes the wall behind each opening. The return faces
and inset patch use the existing shared kit material, so relief adds no draw call.
Depth is a bounded appearance prior, not a survey measurement.
Collision stays on the separate, unmodified building shell.
"""

import numpy as np


def opening_parts(points, uv, style):
    """Yield a recessed rectangular patch and four returns, or preserve the patch.

    Arched and clipped patches retain their existing treatment until their actual
    perimeter can be extruded. Never manufacture a return across a partition seam.
    """
    kind = style.get("detail", [0, 0, 0, 0])[3]
    width, height = style.get("size", [0, 0])
    origin = np.asarray(style.get("origin", [0, 0]))
    complete = (
        kind in (1, 2)
        and style["module"] < 42
        and width >= 0.3 and height >= 0.4
        and np.allclose(uv.min(axis=0), origin, atol=0.001, rtol=0)
        and np.allclose(uv.max(axis=0), origin + [width, height], atol=0.001, rtol=0)
        and np.allclose(points[:2, 1], points[0, 1], atol=0.001, rtol=0)
        and np.allclose(points[2:, 1], points[2, 1], atol=0.001, rtol=0)
    )
    if not complete:
        yield points, uv, style
        return
    tangent = points[1] - points[0]
    tangent /= np.linalg.norm(tangent)
    outward = np.array([tangent[2], 0, -tangent[0]])
    depth = min(0.16, width * 0.12)
    inset = points - outward * depth
    yield inset, uv, style
    reveal = {
        **style, "module": 42 + style["module"] // 6,
        "wall": style["trim"], "base": style["trim"],
        "bands": [0, 0, 0, 0], "detail": [0, 0, 0, 0],
        "origin": [0, 0], "size": [width, height],
    }
    for i in range(4):
        j = (i + 1) % 4
        length = float(np.linalg.norm(points[j] - points[i]))
        yield (
            np.array([points[i], points[j], inset[j], inset[i]]),
            np.array([[0, 0], [length, 0], [length, depth], [0, depth]]),
            reveal,
        )
