"""Later terrain and retaining aprons cannot refill a tunnel's walkable volume."""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import retaining
from turku3d.coarse.build import extrude, grid_mesh
from turku3d.coarse.openings import Openings
from turku3d.coarse.tunnels import produce
from turku3d.ground.mesh import Mesh as Ground
from turku3d.structures.geometry import Faces


def floor():
    faces = Faces()
    faces.face([[0, 1, -1], [0, 1, 1], [10, 2, 1], [10, 2, -1]])
    mesh = faces.mesh('floor', 'road')
    openings = Openings()
    openings.add(mesh, 2.5)
    return openings


def test_height_aware_cut_retains_ground_and_upper_road_and_uvs():
    openings = floor()
    faces = Faces()
    for y in (-1, 2, 6):
        faces.face([[-2, y, -3], [-2, y, 3], [12, y, 3], [12, y, -3]],
                   [[-2, -3], [-2, 3], [12, 3], [12, -3]])
    mesh = faces.mesh('terrain', 'grass')
    cut = openings.structures([mesh])[0]
    np.testing.assert_allclose(cut.uvs, cut.positions[:, [0, 2]])
    tris = cut.positions[cut.indices]
    # The rising floor reaches 1.96 at x=9.6: the last 0.4 m remains
    # below the 4 cm clearance threshold and still supports the pedestrian.
    for y, area in ((-1, 84), (2, 64.8), (6, 84)):
        triangles = tris[np.isclose(tris[:, :, 1], y).all(axis=1)]
        assert abs(shapely.area(shapely.polygons(triangles[:, :, [0, 2]])).sum() - area) < 1e-8
    ground = Ground(mesh.positions, mesh.indices, np.arange(1, len(mesh.indices) + 1, dtype=np.uint8))
    g = openings.ground(ground)
    assert len(g.indices) == len(cut.indices)
    assert set(g.material) == set(ground.material)


def test_vertical_retaining_wall_is_cut_below_upper_support():
    faces = Faces()
    faces.face([[5, -1, -3], [5, -1, 3], [5, 6, 3], [5, 6, -3]])
    mesh = floor().structures([faces.mesh('wall', 'wall')])[0]
    # Triangle centroids in the wall's Y/Z plane cannot lie inside the opening.
    tri = mesh.positions[mesh.indices]
    yz = shapely.polygons(tri[:, :, [1, 2]])
    void = shapely.box(1.54 + 1e-7, -1 + 1e-7, 4 - 1e-7, 1 - 1e-7)
    assert shapely.area(shapely.intersection(yz, void)).sum() < 1e-8
    assert mesh.positions[:, 1].max() == 6
    assert mesh.positions[:, 1].min() == -1


def test_oblique_t0_1_0_passage_remains_free_after_retaining_apron():
    tile = C.VisualTile.parse('t0_1_0')
    # A synthetic retaining edge crossing a quay passage beside the mission marker.
    line = shapely.LineString([(282, 216), (300, 207)])
    pos, idx = grid_mesh(tile, np.full((129, 129), 1.12))
    tunnel = produce(tile, [('quay', line, dict(highway='footway', tunnel='yes',
                     vault='barrel', width='2.4', maxheight='2.7'))],
                     pos, idx, [], extrude, lambda x, z: 1.12)
    edge = shapely.LineString([(275, 216), (305, 203)])
    walls, _ = retaining.produce(tile, [(edge, {'barrier': 'retaining_wall'}, '')],
                                lambda x, z: 1.12 + np.clip((z - 207) / 2, 0, 3))
    assert walls
    protected = tunnel.openings.structures(walls)
    corridor = line.buffer(.65, cap_style='flat')
    before = np.concatenate([m.positions[m.indices] for m in walls])
    after = np.concatenate([m.positions[m.indices] for m in protected])
    def blockers(tris):
        polys = shapely.polygons(tris[:, :, [0, 2]])
        # Intersect the exact triangle/height slab, independently of the clipper.
        from turku3d.coarse.openings import half
        area = 0
        for tri, poly in zip(tris, polys, strict=True):
            if not poly.intersects(corridor):
                continue
            piece = half(half(tri, np.array([0, 1, 0, -1.2]), True),
                         np.array([0, -1, 0, 3.1]), True)
            if len(piece) >= 3:
                area += shapely.Polygon(piece[:, [0, 2]]).intersection(corridor).area
        return area
    assert blockers(before) > 1
    assert blockers(after) < 1e-8
    # Visual geometry and exported collision consume the same clipped triangles.
    from turku3d.coarse.stairs import collision
    ct = C.CollisionTile.at(291.32, 210.84)
    p, ix, _ = collision(ct, protected, np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8))
    assert blockers(p[ix]) < 1e-8
