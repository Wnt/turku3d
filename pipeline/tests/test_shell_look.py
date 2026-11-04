"""shell-look@1: block LOD shells take their HD buildings' measured roof and wall colours and kit grid."""

import numpy as np

from turku3d.migrations import core
from turku3d.migrations.shell_look import ROOF, _array, _views, recolour


def shell(points, colours, faces):
    """A blocks.py-style shell GLB: float POSITION, NORMAL, COLOR_0, uint32 indices, one buffer."""
    pos = np.asarray(points, "<f4"); col = np.asarray(colours, "<f4"); idx = np.asarray(faces, "<u4").ravel()
    nrm = np.zeros_like(pos)
    parts = [pos.tobytes(), nrm.tobytes(), col.tobytes(), idx.tobytes()]
    views, offset = [], 0
    for p in parts:
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(p)}); offset += len(p)
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": offset}], "bufferViews": views,
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5126, "count": len(pos), "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5126, "count": len(pos), "type": "VEC3"},
                         {"bufferView": 3, "componentType": 5125, "count": len(idx), "type": "SCALAR"}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "COLOR_0": 2}, "indices": 3}]}]}
    return core.glb_pack(doc, b"".join(parts))


WALL = (0.6, 0.58, 0.54)


def test_roofs_and_walls_take_their_own_buildings_colours():
    # Two buildings side by side (block offset 100, 0, 50): a roof and a wall triangle each.
    pts = [[0, 10, 0], [4, 10, 0], [0, 10, 4], [0, 0, 0], [4, 0, 0], [0, 10, 0],
           [20, 10, 0], [24, 10, 0], [20, 10, 4], [20, 0, 0], [24, 0, 0], [20, 10, 0]]
    cols = [ROOF] * 3 + [WALL] * 3 + [ROOF] * 3 + [WALL] * 3
    data = shell(pts, cols, [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11]])
    children = [{"box": [100, 50, 105, 55], "roof": [0.5, 0.4, 0.3], "wall": [0.7, 0.6, 0.5], "grid": [3.0, 3.4, 0.5, 0.6]},
                {"box": [120, 50, 125, 55], "roof": [0.1, 0.1, 0.4], "wall": None, "grid": None}]
    out = recolour(data, [100, 0, 50], children)
    doc, binary = core.glb_chunks(out)
    views = _views(doc, binary)
    prim = doc["meshes"][0]["primitives"][0]["attributes"]
    colour, grid = _array(doc, views, prim["COLOR_0"]), _array(doc, views, prim["_SHELL_GRID"])
    np.testing.assert_allclose(colour[0], [0.5, 0.4, 0.3], atol=1e-6)
    np.testing.assert_allclose(colour[3], [0.7, 0.6, 0.5], atol=1e-6)
    np.testing.assert_allclose(colour[6], [0.1, 0.1, 0.4], atol=1e-6)
    np.testing.assert_allclose(colour[9], WALL, atol=1e-6)  # no wall measurement: the pigment stays
    np.testing.assert_allclose(grid[3], [3.0, 3.4, 0.5, 0.6], atol=1e-6)
    assert not grid[0].any() and not grid[9].any()  # roofs and non-kit walls carry no grid
    assert recolour(out, [100, 0, 50], children) == recolour(out, [100, 0, 50], children)  # deterministic


def test_a_shell_without_matching_buildings_is_unchanged():
    data = shell([[0, 0, 0], [1, 0, 0], [0, 0, 1]], [ROOF] * 3, [[0, 1, 2]])
    assert recolour(data, [0, 0, 0], [{"box": [500, 500, 510, 510], "roof": [1, 1, 1], "wall": None, "grid": None}]) is None
