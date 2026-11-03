"""Quay profile blending and lower-passage floor ownership."""

import numpy as np
import shapely

from turku3d.coarse import walkable


def patch(key, z, height, width=3):
    return dict(
        key=key,
        line=shapely.LineString([(0, z), (12, z)]),
        width=width,
        knots=np.array([0.0, 12.0]),
        cross=np.array([-7.5, -4.5, -1.5, 0, 1.5, 4.5, 7.5]),
        heights=np.full((2, 7), height),
    )


def test_outer_bank_blends_continuously_between_walks_and_flat_caps_end():
    scene = [patch("lower", 0, 1), patch("upper", 6, 4)]
    q = np.array([[6, 2, z] for z in (2.999, 3, 3.001)])
    h = walkable.grade(q, scene)[:, 1]
    assert np.ptp(h) < 0.01
    outside = walkable.grade(np.array([[-0.1, 8, 0]]), scene)
    assert outside[0, 1] == 8


def test_vault_floor_anchors_choose_lower_quay_not_parallel_upper_walk():
    from turku3d.coarse.underpasses import floor_anchors

    line = shapely.LineString([(0, 3), (12, 3)])
    levels, supported = floor_anchors(line, [patch("upper", 5, 6), patch("lower", 3, 1.2)], [7, 7])
    np.testing.assert_allclose(levels, 1.2)
    assert supported.all()


def test_vault_masonry_faces_outward_on_top_sides_and_both_portals():
    from turku3d.coarse.underpasses import enclosure

    for reverse in (False, True):
        centres = np.array([[2., 10.], [10., 10.], [18., 10.]])
        if reverse:
            centres = centres[::-1]
        direction = centres[1] - centres[0]
        direction /= np.linalg.norm(direction)
        normal = np.array([-direction[1], direction[0]])
        mesh = enclosure("test", centres, np.tile(normal, (3, 1)), np.full(3, 2.),
                         2.4, 2.7, lambda x,z: 6.)
        tri = mesh.positions[mesh.indices]
        normals = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
        middle = tri.mean(axis=1)
        top = np.all(np.isclose(tri[:, :, 1], 6), axis=1)
        assert top.sum() == 4
        assert (normals[top, 1] > 0).all()
        roof_uv = mesh.uvs[mesh.indices[top]]
        a, b = roof_uv[:, 1]-roof_uv[:, 0], roof_uv[:, 2]-roof_uv[:, 0]
        # Every horizontal triangle must cover a genuine two-dimensional stone patch.
        uv_area = abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0])/2
        np.testing.assert_allclose(uv_area, normals[top, 1]/2)
        side = (np.all(np.isclose(abs(tri[:, :, 2]-10), 1.5), axis=1)
                & (abs(normals[:, 1]) < 1e-10))
        assert side.any()
        assert (np.sum(normals[side][:, [0, 2]] * (middle[side][:, [0, 2]]-[10, 10]), axis=1) > 0).all()
        for endpoint, outward in ((centres[0], -direction), (centres[-1], direction)):
            cap = np.all(np.isclose(tri[:, :, 0], endpoint[0]), axis=1)
            assert cap.any()
            assert (normals[cap][:, [0, 2]] @ outward > 0).all()


def test_vault_enclosure_source_invalidates_visual_and_collision_tiles(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import underpasses, packaging, collision_key

    source = Path(underpasses.__file__)
    read = Path.read_bytes
    before = packaging.code_hash(), collision_key.code_hash()
    monkeypatch.setattr(Path, "read_bytes", lambda p: read(p) +
                        (b"\n# changed enclosure\n" if p == source else b""))
    assert packaging.code_hash() != before[0]
    assert collision_key.code_hash() != before[1]
