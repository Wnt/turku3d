"""Recess complete program openings without changing their glass/frame layout.

The paint partition already removes the wall behind each opening. The return faces
and inset patch use the existing shared kit material, so relief adds no draw call.
Depth is a bounded appearance prior, not a survey measurement.
Collision stays on the separate, unmodified building shell.
"""

import numpy as np


def offset_parts(points, uv, style, depth, side_style):
    """Displace a paint owner and close its perimeter with correctly wound returns."""
    tangent = points[1] - points[0]
    tangent[1] = 0
    tangent /= np.linalg.norm(tangent)
    outward = np.array([tangent[2], 0, -tangent[0]])
    displaced = points + outward * depth
    yield displaced, uv, style
    for i in range(4):
        j = (i + 1) % 4
        length = float(np.linalg.norm(points[j] - points[i]))
        if length < 0.001:
            continue
        yield (
            np.array([points[i], points[j], displaced[j], displaced[i]]),
            np.array([[0, 0], [length, 0], [length, abs(depth)], [0, abs(depth)]]),
            side_style,
        )


def relief_parts(points, uv, style):
    """Yield a recessed rectangular patch and four returns, or preserve the patch.

    Arched and clipped patches retain their existing treatment until their actual
    perimeter can be extruded. Never manufacture a return across a partition seam.
    """
    projection = style.get("relief_m", 0)
    if projection:
        style = {k: v for k, v in style.items() if k != "relief_m"}
        yield from offset_parts(points, uv, style, projection, style)
        return
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
    depth = min(0.16, width * 0.12)
    reveal = {
        **style, "module": 42 + style["module"] // 6,
        "wall": style["trim"], "base": style["trim"],
        "bands": [0, 0, 0, 0], "detail": [0, 0, 0, 0],
        "origin": [0, 0], "size": [width, height],
    }
    yield from offset_parts(points, uv, style, -depth, reveal)


def composed_parts(points, uv, style, plans):
    """Compose final paint owners with additive and displaced structural surfaces.

    Only displaced patches suppress local relief; neighbours retain their depth.
    """
    from turku3d.facade_component_geometry import surface_parts

    tangent = points[1] - points[0]
    tangent[1] = 0
    tangent /= np.linalg.norm(tangent)
    normal = np.array([tangent[2], 0, -tangent[0]])
    for patch, tex in surface_parts(points, uv, plans):
        if np.all(np.abs((patch - points[0]) @ normal) < 1e-6):
            yield from relief_parts(patch, tex, style)
        else:
            # The structural component already owns this surface's displacement.
            yield patch, tex, {k: v for k, v in style.items() if k != "relief_m"}


def bands(vertices, style, quad):
    """Give existing program cornices and plinths bounded geometric projection.

    Extents and colours come from the same instructions as the shader bands.
    These are appearance priors; no new survey measurements are inferred.
    """
    width = float(np.linalg.norm((vertices[1] - vertices[0])[[0, 2]]))
    base = style["origin"][1]
    height = style["size"][1]
    plinth, cornice = style["bands"][:2]
    blank = 42 + style["module"] // 6 if style["module"] < 42 else style["module"]
    plain = style["detail"][2] % 2 == 1
    opening_bottom = (
        float("inf") if blank == style["module"] or plain
        else 0 if style["module"] % 3
        else style["opening"][2] * style["ground"]
    )
    # Shader-only entrances have no polygon to cut from the band. Keep their
    # existing paint until their exact opening layout is expanded into geometry.
    if 0 < plinth <= opening_bottom:
        part = quad(0, width, base, base + min(plinth, height))
        if part:
            yield *part, {
                **style, "module": blank, "bands": [plinth, 0, 0, 0],
                "detail": [0, 0, 0, 0], "relief_m": 0.045,
            }, 3
    if cornice > 0:
        part = quad(0, width, base + max(0, height - cornice), base + height)
        if part:
            yield *part, {
                **style, "module": style.get("trim_module", blank),
                "wall": style["trim"], "base": style["trim"],
                "bands": [0, 0, 0, 0], "detail": [0, 0, 0, 0],
                "relief_m": min(0.18, max(0.06, cornice * 0.45)),
            }, 3
