import numpy as np
from turku3d.layers import CollisionLayer
from turku3d.package.assemble import collision_cache_key
from turku3d.package.structures import cell_hash, collision_faces


def cheek(x, z):
    return CollisionLayer(
        np.array([[x, 9.296, z], [x, 9.321, z], [x + 0.15, 9.321, z + 1]]),
        np.array([[0, 1, 2]], dtype=np.uint32),
        np.array([19], dtype=np.uint8),
    )


def test_stair_cheek_at_l0_edge_reaches_both_cells_without_duplicate_faces():
    for x, z, owner, neighbour in [
        (250, 357.108, "c_4_5", "c_3_5"),
        (750, 130.092, "c_11_2", "c_12_2"),
    ]:
        layer = cheek(x, z)
        result = collision_faces({owner: layer, "duplicate": layer}, {owner, neighbour})
        assert set(result) == {owner, neighbour}
        for exported in result.values():
            assert len(exported.indices) == 1
            np.testing.assert_array_equal(exported.positions, layer.positions)
            np.testing.assert_array_equal(exported.material, layer.material)


def test_corner_distribution_and_neighbour_cache_identity():
    layer = cheek(250, 250)
    cells = {"c_3_3", "c_4_3", "c_3_4", "c_4_4"}
    before = collision_faces({"c_4_4": layer}, cells)
    assert set(before) == cells
    first = collision_cache_key(
        "c_3_3", {}, "code", {"structures_cell": cell_hash(before["c_3_3"])}
    )
    layer.positions[:, 1] += 0.1
    after = collision_faces({"c_4_4": layer}, cells)
    assert (
        collision_cache_key("c_3_3", {}, "code", {"structures_cell": cell_hash(after["c_3_3"])})
        != first
    )
    assert collision_faces({}, cells) == {}
