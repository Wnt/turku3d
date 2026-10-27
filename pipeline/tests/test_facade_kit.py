"""Pure facade parameter mapping and opaque store keys; no tiles, services or encoders."""

from turku3d.facade_kit import parameters, store_path


def test_parameters_keep_storeys_and_material():
    p = parameters("example", 12.8, 18, {"levels": 4, "material_class": "brick"})
    assert 4 <= p["module"] < 8
    assert p["storey"] == 3.2
    assert p["bay"] == 3
    assert p == parameters("example", 12.8, 18, {"levels": 4, "material_class": "brick"})


def test_parameter_key_is_stable_and_cannot_escape(tmp_path):
    path = store_path("../../unsafe/id", tmp_path)
    assert path.parent == tmp_path
    assert path == store_path("../../unsafe/id", tmp_path)
    assert path != store_path("unsafe/id", tmp_path)
