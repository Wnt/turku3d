"""Office assembly rejects missing or unaccepted required layers."""

import pytest
from turku3d.package.assemble import OFFICE_LAYERS, require_office_layers


def complete():
    manifests = {
        name: {"params": {}, "tiles": {"c_1_1.col.npz": {}, "t0_0_0.vis.glb": {}}}
        for name in OFFICE_LAYERS
    }
    manifests["structures"]["params"]["acceptance_passed"] = True
    return manifests


def test_complete_office_layers_pass():
    require_office_layers(complete())


@pytest.mark.parametrize("name", sorted(OFFICE_LAYERS))
def test_each_missing_office_layer_fails_assembly(name):
    manifests = complete()
    del manifests[name]
    with pytest.raises(ValueError, match=f"Missing required office-block layers: {name}; run"):
        require_office_layers(manifests)


def test_missing_layers_are_listed_together():
    with pytest.raises(ValueError, match="bridges.*structures.*vegetation.*make refresh"):
        require_office_layers({"ground": {}, "buildings": {}})


def test_office_structures_require_real_acceptance():
    manifests = complete()
    manifests["structures"]["params"] = {}
    with pytest.raises(ValueError, match="structures failed real-data acceptance"):
        require_office_layers(manifests)


@pytest.mark.parametrize("name", ["bridges", "structures", "vegetation"])
def test_office_layer_without_tiles_fails_assembly(name):
    manifests = complete()
    manifests[name]["tiles"] = {"t0_0_0.vis.glb": {}}
    with pytest.raises(ValueError, match=f"{name} layer has no .col.npz tiles"):
        require_office_layers(manifests)
    manifests[name]["tiles"] = {}
    with pytest.raises(ValueError, match=f"{name} layer has no .vis.glb or .col.npz tiles"):
        require_office_layers(manifests)
