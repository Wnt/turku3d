"""Walls whose LoD2 base stands above the open ground get a plinth down to it."""

import numpy as np
import shapely
from turku3d.coarse import footings
from turku3d.coarse.inputs import Building
from turku3d.ground.mesh import Mesh


def ground(height):
    """A flat 40 m dry sheet at `height(x, z)` on a 1 m grid."""
    n = 41
    x, z = np.meshgrid(np.arange(n) - 10.0, np.arange(n) - 10.0)
    positions = np.c_[x.ravel(), height(x.ravel(), z.ravel()), z.ravel()]
    r, c = np.meshgrid(np.arange(n - 1), np.arange(n - 1), indexing="ij")
    v = (r * n + c).ravel()
    indices = np.r_[np.c_[v, v + n, v + 1], np.c_[v + 1, v + n, v + n + 1]].astype(np.uint32)
    return Mesh(positions, indices, np.zeros(len(indices), np.uint8))


def test_plinth_closes_the_slit_under_a_floating_wall_and_faces_outward():
    footprint = shapely.box(0, 0, 10, 6)
    building = Building("b", footprint, 5.09, 13.0, "facade_plaster")
    # Lower quay in front (z < 0), ground at the wall bottom elsewhere.
    sheet = ground(lambda x, z: np.where(z < 0.5, 4.1, 5.09))
    meshes = footings.produce([building], sheet)
    assert len(meshes) == 1
    mesh = meshes[0]
    assert mesh.object_id == "footing:b" and mesh.material == "wall"
    p = mesh.positions[mesh.indices]
    # Only the floating front (z = 0) and the side walls where the quay reaches them get
    # a plinth, from below the quay up to the wall bottom.
    assert p[:, :, 2].max() <= 1.0
    assert p[:, :, 1].max() == 5.09
    assert p[:, :, 1].min() < 4.1
    front = np.all(np.abs(p[:, :, 2]) < 1e-9, axis=1)
    assert np.ptp(p[front][:, :, 0]) > 9.5
    normal = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    centre = p.mean(axis=1)[:, [0, 2]] + 0.05 * normal[:, [0, 2]] / np.linalg.norm(
        normal[:, [0, 2]], axis=1)[:, None]
    assert not shapely.contains_xy(footprint, centre[:, 0], centre[:, 1]).any()  # outward


def test_grounded_walls_and_mapped_passages_get_no_plinth():
    footprint = shapely.box(0, 0, 10, 6)
    building = Building("b", footprint, 5.09, 13.0, "facade_plaster")
    assert footings.produce([building], ground(lambda x, z: np.full_like(x, 5.1))) == []
    sheet = ground(lambda x, z: np.where(z < 0.5, 4.1, 5.09))
    through = shapely.LineString([(5, -5), (5, 10)])
    meshes = footings.produce([building], sheet, [through])
    p = meshes[0].positions
    assert np.all(np.abs(p[:, 0] - 5) >= footings.PASSAGE - footings.STEP - 1e-9)
