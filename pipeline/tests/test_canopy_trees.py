"""Measured courtyard crowns, inventory priority and geometry clearances."""
import numpy as np
import pytest
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


@pytest.mark.parametrize("name", ["canopy_trees", "canopy_context"])
def test_canopy_algorithm_changes_invalidate_visual_and_collision_generations(monkeypatch, name):
    from pathlib import Path
    import importlib
    from turku3d.coarse import collision_key, packaging

    path = Path(importlib.import_module(f"turku3d.coarse.{name}").__file__)
    read = Path.read_bytes
    before = packaging.code_hash(), collision_key.code_hash()
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read(p) + b'\n# changed canopy algorithm\n' if p == path else read(p))
    assert packaging.code_hash() != before[0]
    assert collision_key.code_hash() != before[1]


def test_border_tree_keeps_the_same_support_and_dimensions_as_an_interior_tree():
    empty = shapely.GeometryCollection()
    def measure(x):
        return inventory(VisualTile(0, 0, 0), crowns((x, 100, 17, 5)),
                         [Tree('puut:lime', x, 100, 'register', species='Tilia cordata')],
                         lambda x, z: 0, empty, empty)[0]
    interior, border = measure(125), measure(249)
    assert border.support == interior.support
    assert border.height == interior.height
    assert border.crown_radius == interior.crown_radius


def test_adjacent_tiles_measure_competing_trunks_before_half_open_ownership():
    empty = shapely.GeometryCollection()
    points = crowns((247, 100, 17, 5), (253, 100, 14, 5))
    left = Tree('puut:left', 247, 100, 'register', species='Tilia cordata')
    right = Tree('puut:right', 253, 100, 'register', species='Acer platanoides')
    context = [(t.object_id, t.x, t.z, 10, False, t.species) for t in [left, right]]
    reference = inventory(VisualTile(1, 0, 0), points, [left, right], lambda x, z: 0, empty, empty)
    split = [*inventory(VisualTile(0, 0, 0), points, [left], lambda x, z: 0, empty, empty, context=context),
             *inventory(VisualTile(0, 1, 0), points, [right], lambda x, z: 0, empty, empty, context=context)]
    record = lambda trees: {t.object_id: (t.height, t.crown_radius, t.support) for t in trees}
    assert len(split) == 2
    assert record(split) == record(reference)


def test_trunk_exactly_on_boundary_belongs_only_to_the_neighbour():
    empty = shapely.GeometryCollection()
    points = crowns((250, 100, 17, 5))
    tree = Tree('puut:boundary', 250, 100, 'register')
    context = [('puut:boundary', 250, 100, 10, False, '')]
    assert inventory(VisualTile(0, 0, 0), points, [], lambda x, z: 0, empty, empty, context=context) == []
    right = inventory(VisualTile(0, 1, 0), points, [tree], lambda x, z: 0, empty, empty, context=context)
    assert [t.object_id for t in right] == ['puut:boundary']


def test_halo_terrain_samples_do_not_flatten_a_slope_at_the_tile_edge():
    from turku3d.coarse.canopy_trees import terrain_sampler
    ground = np.array([(x, x * .5, z) for x in range(230, 271) for z in range(90, 111)])
    height = terrain_sampler(ground, lambda x, z: min(x, 250) * .5)
    assert abs(height(260, 100) - 130) < .01
    assert abs(height(249, 100) - 124.5) < .01


def test_ineligible_neighbour_does_not_steal_the_owned_trees_crown_returns():
    empty = shapely.GeometryCollection()
    blocked = shapely.box(251, 96, 256, 104)
    points = crowns((247, 100, 17, 6), (253, 100, 14, 4))
    left = Tree('puut:left', 247, 100, 'register')
    right = Tree('puut:building', 253, 100, 'register')
    eligible = lambda tree: not blocked.covers(shapely.Point(tree.x, tree.z))
    reference = inventory(VisualTile(1, 0, 0), points, [left, right], lambda x, z: 0,
                          blocked, empty, eligible=eligible)
    split = inventory(VisualTile(0, 0, 0), points, [left], lambda x, z: 0, blocked, empty,
                      context=[('puut:building', 253, 100, 10, False, '')], eligible=eligible)
    expected = next(tree for tree in reference if tree.object_id == left.object_id)
    actual = next(tree for tree in split if tree.object_id == left.object_id)
    assert (actual.height, actual.crown_radius, actual.support) == (expected.height, expected.crown_radius, expected.support)


def test_batched_terrain_sampling_preserves_scalar_results_and_distant_fallback():
    from turku3d.coarse.canopy_trees import terrain_sampler
    ground = np.array([[0, 2, 0], [1, 3, 0], [0, 4, 1], [1, 5, 1]])
    at = terrain_sampler(ground, lambda x, z: 99)
    x, z = np.array([0, .3, 100]), np.array([0, .4, 100])
    assert np.allclose(at(x, z), [at(a, b) for a, b in zip(x, z)])
    assert at(x, z)[-1] == 99
    assert np.array_equal(terrain_sampler([], lambda x, z: x + z)(x, z), x + z)
