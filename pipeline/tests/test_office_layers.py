"""Office assembly rejects missing or unaccepted required layers."""

import pytest
from turku3d.package.assemble import require_office_layers


def test_office_assembly_requires_bridges_and_structures():
    with pytest.raises(ValueError, match="bridges.*structures.*make refresh"):
        require_office_layers({"ground": {}, "buildings": {}})
    with pytest.raises(ValueError, match="bridges layer has no visual or collision tiles"):
        require_office_layers(
            {
                "ground": {},
                "buildings": {},
                "bridges": {"tiles": {}},
                "structures": {
                    "params": {"acceptance_passed": True},
                    "tiles": ["c_1_1.col.npz", "t0_0_0.vis.glb"],
                },
            }
        )
