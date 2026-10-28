def test_retained_collision_key_tracks_its_current_inputs():
    from copy import deepcopy

    from turku3d.package.assemble import collision_cache_key

    manifests = {
        "ground": {
            "tiles": {
                "c_3_0.col.npz": {"sha256": "requested"},
                "c_4_0.col.npz": {"sha256": "neighbour-before"},
            }
        }
    }
    first = collision_cache_key("c_4_0", manifests, "code", {})
    changed = deepcopy(manifests)
    changed["ground"]["tiles"]["c_4_0.col.npz"]["sha256"] = "neighbour-after"
    assert collision_cache_key("c_4_0", changed, "code", {}) != first
    assert collision_cache_key("c_3_0", changed, "code", {}) == collision_cache_key(
        "c_3_0", manifests, "code", {}
    )
    del changed["ground"]["tiles"]["c_4_0.col.npz"]
    assert collision_cache_key("c_4_0", changed, "code", {}) != first


def test_unrequested_retained_neighbour_is_checked_against_current_sources():
    from turku3d import config as C
    from turku3d.package.assemble import collision_cells

    requested = {C.CollisionTile(3, 0)}
    available = {"c_3_0", "c_4_0", "c_5_0"}
    layers = {"ground": {"c_3_0": object(), "c_4_0": object(), "c_9_0": object()}}
    assert [c.id for c in collision_cells(requested, available, layers)] == ["c_3_0", "c_4_0"]
