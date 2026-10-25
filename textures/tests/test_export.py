import json

import numpy as np
from turkutex.export import matches
from turkutex.streetview.facades import Facade, load_facades


def test_building_schema_and_safe_geometry_match(tmp_path):
    path = tmp_path / "facades.geojson"
    path.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[10, 20], [14, 20]]},
                        "properties": {
                            "object_id": "kanta1/facade/8",
                            "building_id": "kanta1",
                            "facade_index": 8,
                            "base_height": 3,
                            "top_height": 9,
                            "normal": [0, 0, 1],
                            "material_key": "facade_plaster",
                        },
                    }
                ]
            }
        )
    )
    wall = load_facades(path)[0]
    assert wall.id == "kanta1_8"
    assert wall.height_m == 6 and wall.base_h == 3
    assert wall.normal_side == "right"
    old = Facade("old", np.array([10, 20]), np.array([14, 20]), 6, 3, {"building_id": "kanta1"})
    assert matches(old, wall)
    old.height_m = 4
    assert not matches(old, wall)
    old.height_m = 6
    old.base_h = None
    assert not matches(old, wall)
