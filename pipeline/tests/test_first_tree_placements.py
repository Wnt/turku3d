import hashlib
from types import SimpleNamespace
import numpy as np
from turku3d.coarse.basic import tree_placements


def test_first_tree_placements_preserve_full_pass_transform_contract():
    tile = SimpleNamespace(origin=(-1750, 0, 1750))
    tree = SimpleNamespace(object_id='register:123', variant='tree_default_fall', species='Tilia cordata',
                           x=-1550.5, y=4.25, z=1820.5, height=16.0, crown_radius=5.0)
    data = tree_placements(tile, [tree])
    node = data['nodes'][0]
    assert data['version'] == 1
    assert node['matrix'][12:15] == list(tile.origin)
    assert node['positions'] == [[199.5, 4.25, 70.5]]
    assert node['scales'] == [[5.0, 16.0, 5.0]]
    assert node['ids'] == ['register:123'] and node['species'] == ['Tilia cordata']
    angle = int(hashlib.sha256(tree.object_id.encode()).hexdigest()[:8], 16) / 2**32 * np.pi
    np.testing.assert_allclose(node['rotations'], [[0, np.sin(angle), 0, np.cos(angle)]])
    assert tree_placements(tile, []) == dict(version=1, nodes=[])


def test_draft_sidecar_uses_same_structure_clearance_as_full_geometry():
    import shapely
    from turku3d.coarse.forests import structure_clearance
    from turku3d.vegetation.trees import Tree

    inputs = SimpleNamespace(barriers=[], retaining=[(
        shapely.LineString([(0, 0), (100, 0)]), {"barrier": "retaining_wall"}, False)],
        attachments=[{"region": shapely.box(8, 8, 12, 12), "kind": "tread"}])
    candidates = [Tree("wall", 5, 0, "register"), Tree("tread", 10, 10, "osm"),
                  Tree("soil", 20, 10, "osm")]
    nodes = tree_placements(SimpleNamespace(origin=(0, 0, 0)),
                            structure_clearance(candidates, inputs))["nodes"]
    assert [identity for node in nodes for identity in node["ids"]] == ["soil"]
