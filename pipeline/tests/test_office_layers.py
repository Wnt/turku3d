"""Assembly rejects missing or unaccepted required layers, per area: core tiles need every
layer, expansion tiles ground, buildings and roads."""

import pytest
from turku3d import config as C
from turku3d.package.assemble import EXPANSION_LAYERS, OFFICE_LAYERS, require_layers

CORE = ["t0_0_0"]


def complete():
    manifests = {
        name: {"params": {}, "tiles": {"c_1_1.col.npz": {}, "t0_0_0.vis.glb": {}}}
        for name in OFFICE_LAYERS
    }
    manifests["structures"]["params"]["acceptance_passed"] = True
    return manifests


def expansion_ground(tile_id):
    tile = C.VisualTile.parse(tile_id)
    names = [f"{tile_id}.vis.glb"] + [f"{c.id}.col.npz" for c in tile.collision_tiles()]
    return {"params": {}, "tiles": dict.fromkeys(names, {})}


def test_complete_office_layers_pass():
    require_layers(complete(), CORE)


@pytest.mark.parametrize("name", sorted(OFFICE_LAYERS))
def test_each_missing_office_layer_fails_assembly(name):
    manifests = complete()
    del manifests[name]
    with pytest.raises(ValueError, match=f"Missing required layers: {name}; run"):
        require_layers(manifests, CORE)


def test_missing_layers_are_listed_together():
    with pytest.raises(ValueError, match="bridges.*structures.*vegetation.*make refresh"):
        require_layers({"ground": {}, "buildings": {}}, CORE)


def test_office_structures_require_real_acceptance():
    manifests = complete()
    manifests["structures"]["params"] = {}
    with pytest.raises(ValueError, match="structures failed real-data acceptance"):
        require_layers(manifests, CORE)


@pytest.mark.parametrize("name", ["bridges", "structures", "vegetation"])
def test_office_layer_without_tiles_fails_assembly(name):
    manifests = complete()
    manifests[name]["tiles"] = {"t0_0_0.vis.glb": {}}
    with pytest.raises(ValueError, match=f"{name} layer has no .col.npz tiles"):
        require_layers(manifests, CORE)
    manifests[name]["tiles"] = {}
    with pytest.raises(ValueError, match=f"{name} layer has no .vis.glb or .col.npz tiles"):
        require_layers(manifests, CORE)


def test_expansion_tile_needs_only_ground_buildings_and_roads():
    manifests = {name: {"params": {}, "tiles": {}} for name in EXPANSION_LAYERS}
    manifests["ground"] = expansion_ground("t0_2_0")
    require_layers(manifests, ["t0_2_0"])
    del manifests["roads"]
    with pytest.raises(ValueError, match="Missing required layers: roads"):
        require_layers(manifests, ["t0_2_0"])


def test_expansion_tile_without_its_ground_fails():
    manifests = {name: {"params": {}, "tiles": {}} for name in EXPANSION_LAYERS}
    manifests["ground"] = expansion_ground("t0_2_0")
    del manifests["ground"]["tiles"]["c_8_3.col.npz"]
    with pytest.raises(ValueError, match="t0_2_0: ground layer lacks c_8_3.col.npz"):
        require_layers(manifests, ["t0_2_0"])


def test_mixed_request_keeps_every_core_layer_required():
    manifests = {name: {"params": {}, "tiles": {}} for name in EXPANSION_LAYERS}
    manifests["ground"] = expansion_ground("t0_2_0")
    with pytest.raises(ValueError, match="bridges"):
        require_layers(manifests, ["t0_0_0", "t0_2_0"])
