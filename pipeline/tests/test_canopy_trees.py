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
