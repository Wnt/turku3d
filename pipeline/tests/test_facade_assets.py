"""Stored facade identity, shared descriptions and geometry-preserving wall binding."""

import json
from types import SimpleNamespace

import numpy as np
from turku3d import config as C
from turku3d.coarse.stored_facades import match_wall, wall_meshes
from turku3d.facade_assets import export, opaque
from turku3d.facade_kit import parameter_hash, read_description, renderer


def wall():
    return np.array([[0, 3, 0], [10, 3, 0], [10, 15, 0], [0, 15, 0]], float)


def facade():
    return dict(
        coordinates=[[C.ORIGIN_E, C.ORIGIN_N], [C.ORIGIN_E + 10, C.ORIGIN_N]],
        base_height=3,
        metadata=dict(facade_index=7),
    )


def test_matching_keeps_metres_and_rejects_unrelated_walls():
    f = facade()
    result = match_wall(wall(), [f])
    assert result[0] is f
    np.testing.assert_array_equal(result[1], [[0, 0], [10, 0], [10, 12], [0, 12]])
    shifted = wall() + [0, 0, 2]
    assert match_wall(shifted, [f]) is None
    short = facade()
    short["coordinates"][1][0] = C.ORIGIN_E + 2
    assert match_wall(wall(), [short]) is None


def test_binding_keeps_geometry_and_leaves_unmatched_faces_on_kit(monkeypatch):
    from turku3d.coarse import stored_facades as sf

    monkeypatch.setattr(
        sf, "building", lambda key: dict(facades=[facade()]) if key == "stable" else {}
    )
    b = SimpleNamespace(key="wfs-part", facade_key="stable", base=3, top=15)
    positions = np.concatenate([wall(), wall() + [0, 0, 2]])
    indices = np.array([[0, 3, 2], [0, 2, 1], [4, 7, 6], [4, 6, 5]], np.uint32)
    meshes = wall_meshes(b, positions, np.zeros((8, 2)), indices, np.zeros(3), {})
    assert [m.extras["material_key"] for m in meshes] == ["facade_stored", "facade_kit"]
    actual = np.concatenate([m.positions[m.indices] for m in meshes])
    np.testing.assert_array_equal(actual, positions[indices])
    assert meshes[0].extras["building_id"] == "stable"
    assert meshes[0].extras["facade_index"] == 7


def test_shared_descriptions_override_register_and_invalidate_only_own_tile(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_FACADE_ASSETS", str(tmp_path / "store"))
    directory = tmp_path / "store/facade-params"
    directory.mkdir(parents=True)
    before = parameter_hash(["a"])
    d = renderer().register_description("b_0", "b", {}, 10, 12)
    (directory / opaque("b")).write_text(json.dumps(dict(building_key="b", description=d)))
    assert parameter_hash(["a"]) == before
    assert read_description("b") == d
    assert parameter_hash(["b"]) != parameter_hash(["missing"])


def test_export_does_not_copy_unapproved_or_reference_assets(tmp_path):
    source, target = tmp_path / "source", tmp_path / "export"
    source.mkdir()
    (source / "MANIFEST.json").write_text(json.dumps(dict(bound=["b_0"])))
    (source / "APPROVED_INVENTORY.geojson").write_text(
        json.dumps(
            dict(
                features=[
                    dict(
                        properties=dict(building_id="b", facade_index=0, base_height=3),
                        geometry=dict(coordinates=facade()["coordinates"]),
                    )
                ]
            )
        )
    )
    (source / "facades/b").mkdir(parents=True)
    (source / "facades/b/0.json").write_text(json.dumps(dict(method="street-photo")))
    (source / "facades/b/private-reference.jpg").write_bytes(b"private")
    index = export(target, source)
    assert index["buildings"] == {}
    assert not list(target.rglob("*.jpg"))


def test_survey_offset_preserves_source_clamp_and_outward_side():
    f = facade()
    f["metadata"]["uv"] = dict(scale=[-0.1, -1 / 12], offset=[1, 1])
    result = match_wall(wall() + [-0.1, 0, 0], [f])
    uv = result[1]
    transformed = uv * [-0.1, -1 / 12] + [1, 1]
    assert np.all((transformed >= 0) & (transformed <= 1))
    f["normal"] = [0, 0, 1]
    assert match_wall(wall(), [f]) is None
    f["normal"] = [0, 0, -1]
    assert match_wall(wall(), [f]) is not None


def test_gis_fallback_assets_keep_stable_registry_lookup_and_original_binding(
    tmp_path, monkeypatch
):
    from turku3d import facade_assets as assets
    from turku3d.coarse import stored_facades as sf

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_FACADE_ASSETS", str(tmp_path))
    (tmp_path / "buildings").mkdir()
    (tmp_path / "facade-params").mkdir()
    (tmp_path / "index.json").write_text("{}")
    f = facade()
    f["metadata"].update(facade_id="gis-b_7", building_id="gis-b")
    (tmp_path / "buildings" / opaque("gis-b")).write_text(json.dumps(dict(facades=[f])))
    d = renderer().register_description("gis-b_7", "gis-b", {}, 10, 12)
    (tmp_path / "facade-params" / opaque("gis-b")).write_text(
        json.dumps(dict(building_key="gis-b", description=d))
    )
    assert assets.building("b")["facades"] == [f]
    assert read_description("b") == d
    assert assets.stored_request(["b", "gis-b"])["facadeBindings"] == ["gis-b_7"]
    b = SimpleNamespace(key="part", facade_key="b", base=3, top=15)
    meshes = sf.wall_meshes(
        b, wall(), np.zeros((4, 2)), np.array([[0, 3, 2], [0, 2, 1]]), np.zeros(3), {}
    )
    assert meshes[0].extras["building_id"] == "gis-b"
    assert meshes[0].extras["facade_index"] == 7


def test_shared_texture_edits_invalidate_drafts_without_full_tile_release(tmp_path, monkeypatch):
    from turku3d.facade_assets import material_library

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_FACADE_ASSETS", str(tmp_path / "store"))
    library = tmp_path / "store/library/plaster"
    library.mkdir(parents=True)
    texture = library / "albedo.ktx2"
    texture.write_bytes(b"first-generated-map")
    assert material_library() == library.parent
    before = parameter_hash(["building"])
    texture.write_bytes(b"new-generated-map")
    assert parameter_hash(["building"]) != before
    arrays = tmp_path / "data/textures/library"
    arrays.mkdir(parents=True)
    before = parameter_hash(["building"])
    (arrays / "albedo.array.ktx2").write_bytes(b"own-ground-library")
    assert parameter_hash(["building"]) != before
