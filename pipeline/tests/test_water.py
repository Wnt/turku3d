"""Water bodies: OSM areas, one level each from lidar, flat sensors at their own level."""

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, box
from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.ground.mesh import build_mesh, tile_mesh
from turku3d.ground.points import Surface


class ConstantMaterials:
    def lookup(self, xz):
        return np.full(len(xz), mat_id("road"), np.uint8)


def test_each_water_body_is_a_flat_sensor_at_its_own_level():
    """A river at 0.2 m and a pond at 6.5 m in one envelope: each body keeps its own plane."""
    from turku3d.ground.ground_qa import evaluate_ground
    from turku3d.ground.water import add_river_bed

    x, z = np.meshgrid(np.arange(0, 125.01, 1.0), np.arange(0, 62.51, 1.0))
    surface = Surface(np.c_[x.ravel(), 7 + 0.01 * x.ravel(), z.ravel()])
    river, pond = box(5, 5, 40, 55), box(80, 20, 95, 35)
    tiles = [C.CollisionTile(0, 0), C.CollisionTile(1, 0)]
    mesh, _ = build_mesh(
        surface,
        C.BBoxLocal(0, 0, 125, 62.5),
        tiles,
        [],
        shapely.union_all([river, pond]),
        [(river, 0.2), (pond, 6.5)],
        ConstantMaterials(),
    )
    centers = mesh.positions[mesh.indices].mean(axis=1)
    wet = mesh.material == mat_id("water")
    for polygon, level in ((river, 0.2), (pond, 6.5)):
        inside = wet & shapely.contains_xy(polygon, centers[:, 0], centers[:, 2])
        assert inside.any() and np.all(mesh.positions[mesh.indices[inside], 1] == level)
    bed = add_river_bed(mesh)
    assert {-2.8, 3.5} <= set(np.round(bed.positions[:, 1], 6))
    accepted = np.c_[x.ravel(), np.zeros(x.size), z.ravel()]
    meshes = {t: tile_mesh(bed, t) for t in tiles}
    report = evaluate_ground(
        meshes,
        accepted,
        shapely.union_all([river, pond]),
        shapely.Polygon(),
        C.BBoxLocal(0, 0, 125, 62.5),
    )
    assert report["checks"]["water_plane"]
    assert report["metrics"]["water"]["levels_m"] == [0.2, 6.5]


def test_water_bodies_and_their_levels_from_lidar(tmp_path, monkeypatch):
    import producer_world as world
    from turku3d.ground.water import bodies, level_from_points

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    river = shapely.Polygon([(0, 0), (200, 0), (200, 40), (0, 40)])
    pond = box(300, 300, 312, 310)
    path = world.write_osm(
        [
            ({"natural": "water", "water": "river"}, river),
            ({"waterway": "riverbank"}, box(0, 40, 50, 60)),
            ({"landuse": "basin"}, pond),
            ({"natural": "wood"}, box(0, 100, 50, 150)),
            ({"natural": "water"}, LineString([(0, 0), (10, 0)])),
        ]
    )
    found = bodies(path)
    assert list(found) == ["way/1", "way/2", "way/3"]
    assert list(bodies(path, C.BBoxLocal(250, 250, 400, 400))) == ["way/3"]
    rng = np.random.default_rng(5)
    xz = rng.uniform([290, 290], [322, 320], (4000, 2))
    inside = shapely.contains_xy(pond, xz[:, 0], xz[:, 1])
    heights = np.where(inside, 6.4, 7.1)
    points = np.c_[xz[:, 0], heights, xz[:, 1]]
    classes = np.where(inside, 9, 2)
    level, source = level_from_points(pond, points, classes)
    assert level == pytest.approx(6.4) and "class-9" in source
    level, source = level_from_points(pond, points, np.full(len(points), 2))
    assert level == pytest.approx(6.4) and "interior" in source
    small = box(300, 300, 303, 303)
    level, source = level_from_points(small, points[~inside], classes[~inside])
    assert level == pytest.approx(6.9) and "shore" in source
