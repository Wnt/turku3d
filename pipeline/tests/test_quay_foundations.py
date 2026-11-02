import numpy as np

from turku3d import config as C
from turku3d.coarse.underpasses import foundation
from turku3d.geometry_qa import Mesh, quay_floor_gaps
from turku3d.structures.geometry import Faces


def test_raised_quay_floor_is_sealed_to_below_water_without_internal_walls():
    faces = Faces()
    faces.face([[10, 1.3, 10], [10, 1.3, 14], [14, 1.3, 14], [14, 1.3, 10]])
    floor = faces.mesh('tunnel:test:floor', 'paving_slab')
    support = foundation('test', floor, C.VisualTile(0, 0, 0))
    assert len(support.indices) == 8  # four sides; no diagonal wall
    assert support.positions[:, 1].min() == -3
    assert support.positions[:, 1].max() == 1.3
    triangles = support.positions[support.indices]
    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    assert np.all(np.sum(normals[:, [0, 2]] * (triangles.mean(1)[:, [0, 2]] - [12, 12]), axis=1) > 0)
    wet = Faces(); wet.face([[5, 0, 5], [5, 0, 20], [20, 0, 20], [20, 0, 5]])
    water = wet.mesh('water', 'water')
    def qa(mesh, material):
        return Mesh(mesh.object_id, mesh.positions, mesh.indices, dict(material_key=material))
    original = [qa(floor, 'draft_bridge_stone'), qa(water, 'water')]
    assert len(quay_floor_gaps(original)) == 4
    assert not quay_floor_gaps([*original, qa(support, 'draft_bridge_stone')])


def test_foundation_does_not_extrude_artificial_tile_edge():
    faces = Faces()
    faces.face([[0, 1, 10], [0, 1, 14], [4, 1, 14], [4, 1, 10]])
    support = foundation('clipped', faces.mesh('floor', 'paving_slab'), C.VisualTile(0, 0, 0))
    assert len(support.indices) == 6
    assert not np.any(np.all(abs(support.positions[support.indices, 0]) < 1e-7, axis=1))
