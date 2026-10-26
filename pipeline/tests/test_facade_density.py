import json

import pytest
from turku3d.package.assemble import facade_density_floor


def test_optional_facade_density_floor_is_validated(tmp_path):
    wall = tmp_path / "facades/b"
    wall.mkdir(parents=True)
    meta = wall / "0.json"
    meta.write_text(json.dumps({"min_pixels_per_metre": 32}))
    assert facade_density_floor(tmp_path / "facades", "b_0") == 32
    meta.write_text(json.dumps({"min_pixels_per_metre": 2}))
    with pytest.raises(ValueError, match="invalid minimum facade density"):
        facade_density_floor(tmp_path / "facades", "b_0")
