from collections import Counter
from types import SimpleNamespace

import numpy as np
import shapely

from turku3d.landmarks.descriptions import resolve
from turku3d.landmarks.relief import extrude, meshes
from turku3d.layers import read_visual, write_visual


def test_extruded_profile_is_closed_outward_and_retains_its_hole():
    polygon = shapely.box(0, 0, 4, 3).difference(shapely.box(1, 1, 2, 2))
    front, sides = extrude(polygon, .05, .25)
    triangles = (np.concatenate([front, sides]) * [1, 1, -1]).reshape(-1, 3, 3)
    volume = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6
    np.testing.assert_allclose(volume, polygon.area * .2)
    edges = Counter((tuple(a), tuple(b)) for triangle in triangles
                    for a, b in zip(triangle, np.roll(triangle, -1, axis=0), strict=True))
    assert all(count == 1 and edges[b, a] == 1 for (a, b), count in edges.items())
    projected = shapely.union_all(shapely.polygons(front.reshape(-1, 3, 3)[:, :, :2]))
    assert projected.symmetric_difference(polygon).area < 1e-9


def test_fragment_anchor_emits_a_spanning_component_once_and_keeps_provenance(tmp_path):
    b = SimpleNamespace(key="part", facade_key="103304670P", base=0, attributes={})
    d = resolve(b)
    d["elevations"] = [dict(origin_xz=[0, 0], axis_xz=[1, 0], normal_xz=[0, -1],
        width_m=10, height_m=10, relief=[dict(polygon_m=[[1, 1], [7, 1], [7, 3], [1, 3]],
        anchor_m=[2, 2], depth_m=.2, material="trim")])]
    left = np.array([[0, 0, 0], [5, 0, 0], [5, 5, 0], [0, 5, 0]], float)
    right = left + [5, 0, 0]
    a, c = meshes(b, [left], np.zeros(3), d), meshes(b, [right], np.zeros(3), d)
    assert sum(len(m.indices) for m in a) == 12
    assert not c
    assert all(m.extras["facade_relief"] and m.extras["building_id"] == b.facade_key for m in a)
    assert all(np.isfinite(m.uvs).all() for m in a)
    output = tmp_path / "relief.vis.glb"
    write_visual(output, a)
    assert read_visual(output)
    # The texture origin must not bury relief inside an offset surveyed wall.
    moved = meshes(b, [left + [0, 0, -.3]], np.zeros(3), d)
    np.testing.assert_allclose(np.concatenate([m.positions for m in moved]),
                               np.concatenate([m.positions for m in a]) + [0, 0, -.3])


def test_measured_front_relief_has_a_bounded_geometry_cost():
    b = SimpleNamespace(key="hall", facade_key="103304670P", base=4.039, attributes={})
    d = resolve(b); row = d["elevations"][0]
    xz = np.array(row["origin_xz"]) + np.array([0, row["width_m"], row["width_m"], 0])[:, None] * row["axis_xz"]
    y = b.base + row["base_offset_m"] + np.array([0, 0, row["height_m"], row["height_m"]])
    wall = np.c_[xz[:, 0], y, xz[:, 1]]
    result = meshes(b, [wall], np.zeros(3), d)
    count = sum(len(m.indices) for m in result)
    assert 500 < count < 2500
    assert all(np.isfinite(m.positions).all() for m in result)
