"""Measured courtyard crowns, inventory priority and geometry clearances."""
import numpy as np
import shapely

from turku3d.config import VisualTile
from turku3d.coarse.canopy_trees import inventory
from turku3d.vegetation.trees import Tree


def crowns(*centres):
    points = []
    for x, z, height, radius in centres:
        for dx in np.arange(-radius, radius + .1, .5):
            for dz in np.arange(-radius, radius + .1, .5):
                r = np.hypot(dx, dz)
                if r < radius:
                    points.append((x + dx, height - (r / radius) ** 2 * 3, z + dz))
    return np.array(points)


def test_courtyard_canopies_need_no_osm_green_polygon():
    result = inventory(VisualTile(0, 0, 0), crowns((30, 40, 17, 4), (70, 80, 12, 3)),
                       [], lambda x, z: 0, shapely.GeometryCollection(), shapely.GeometryCollection())
    assert len(result) == 2
    assert all(t.measured and t.source == 'lidar' for t in result)
    assert max(t.height for t in result) > 16
    assert max(t.crown_radius for t in result) > 3


def test_register_species_and_trunk_stay_while_dimensions_are_measured():
    known = Tree('puut:lime', 30, 40, 'register', species='Tilia cordata', height=6)
    result = inventory(VisualTile(0, 0, 0), crowns((30, 40, 17, 4)),
                       [known], lambda x, z: 0, shapely.GeometryCollection(), shapely.GeometryCollection())
    assert len(result) == 1
    tree = result[0]
    assert (tree.x, tree.z, tree.species) == (30, 40, 'Tilia cordata')
    assert tree.measured and tree.height > 16
    assert known.height == 6  # cached mapped input remains immutable


def test_lidar_trunks_clear_buildings_water_and_paths():
    result = inventory(VisualTile(0, 0, 0), crowns((30, 40, 17, 4), (70, 80, 12, 3)),
                       [], lambda x, z: 0, shapely.box(25, 35, 35, 45), shapely.box(65, 75, 75, 85))
    assert result == []


def test_single_return_and_low_vegetation_do_not_invent_trees():
    points = np.array([[30, 20, 40], [60, 1, 80]])
    assert inventory(VisualTile(0, 0, 0), points, [], lambda x, z: 0,
                     shapely.GeometryCollection(), shapely.GeometryCollection()) == []


def test_flat_elevated_patch_does_not_invent_a_tree():
    patch = crowns((30, 40, 8, 2))
    patch[:, 1] = 8 + np.linspace(0, .12, len(patch))
    assert inventory(VisualTile(0, 0, 0), patch, [], lambda x, z: 0,
                     shapely.GeometryCollection(), shapely.GeometryCollection()) == []


def test_disconnected_returns_do_not_enlarge_a_crown():
    from turku3d.coarse.canopy_trees import connected_crown

    core = crowns((30, 40, 12, 2))
    detached = crowns((36, 40, 16, 1))
    result = connected_crown(np.concatenate([core, detached]), 30, 40)
    np.testing.assert_array_equal(result, core)


def test_flat_patch_preserves_surveyed_tree_without_remeasuring_it():
    known = Tree('puut:surveyed', 30, 40, 'register', height=12, crown_radius=3)
    patch = crowns((30, 40, 8, 2))
    patch[:, 1] = 8
    result = inventory(VisualTile(0, 0, 0), patch, [known], lambda x, z: 0,
                       shapely.GeometryCollection(), shapely.GeometryCollection())
    assert len(result) == 1
    assert result[0].height == 12 and result[0].crown_radius == 3
    assert not result[0].measured


def test_measured_register_crown_rechecks_visible_trunk_clearance():
    from turku3d.coarse.forests import clears_path, trunk_base_radius
    from turku3d.vegetation.trees import variant

    known = Tree('puut:edge', 30, 40, 'register', species='Tilia cordata',
                 height=6, crown_radius=1, variant=variant('Tilia cordata', 6))
    edge = trunk_base_radius(variant('Tilia cordata', 17)) * 2.5
    paths = shapely.box(30 + edge, 30, 40, 50)
    assert clears_path(known, paths)
    result = inventory(VisualTile(0, 0, 0), crowns((30, 40, 17, 4)),
                       [known], lambda x, z: 0, shapely.GeometryCollection(), paths)
    assert all(clears_path(tree, paths) for tree in result)
    assert all(tree.object_id != known.object_id for tree in result)


def test_canopy_algorithm_changes_invalidate_visual_and_collision_generations(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import canopy_trees, collision_key, packaging

    path = Path(canopy_trees.__file__)
    read = Path.read_bytes
    before = packaging.code_hash(), collision_key.code_hash()
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read(p) + b'\n# changed canopy algorithm\n' if p == path else read(p))
    assert packaging.code_hash() != before[0]
    assert collision_key.code_hash() != before[1]


def test_surveyed_trunk_clears_the_walked_line_not_the_estimated_path_width():
    """A register tree in a pavement grate stays; it only clears the walked line (``walked``)."""
    known = Tree('puut:grate', 30, 40, 'register', height=8, crown_radius=2)
    footway = shapely.LineString([(0, 41.5), (60, 41.5)])
    paths = footway.buffer(1.25)  # the 2.5 m default width covers the trunk
    result = inventory(VisualTile(0, 0, 0), np.empty((0, 3)), [known], lambda x, z: 0,
                       shapely.GeometryCollection(), paths, footway)
    assert [t.object_id for t in result] == ['puut:grate']
    on_line = Tree('puut:line', 30, 41.5, 'register', height=8, crown_radius=2)
    result = inventory(VisualTile(0, 0, 0), crowns((30, 41.5, 8, 2)), [on_line], lambda x, z: 0,
                       shapely.GeometryCollection(), paths, footway)
    assert 'puut:line' not in [t.object_id for t in result]
