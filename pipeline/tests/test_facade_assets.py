"""Stored facade identity, shared descriptions and geometry-preserving wall binding."""

import json
from types import SimpleNamespace

import numpy as np
import shapely
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
    b = SimpleNamespace(key="wfs-part", facade_key="stable", base=3, top=15,
                        footprint=shapely.box(0, 0, 10, 2), attributes={"levels": 4})
    positions = np.concatenate([wall(), wall() + [0, 0, 2]])
    indices = np.array([[0, 3, 2], [0, 2, 1], [4, 7, 6], [4, 6, 5]], np.uint32)
    meshes = wall_meshes(b, positions, np.zeros(3))
    assert [m.extras["material_key"] for m in meshes] == ["facade_stored", "facade_kit"]
    actual = np.concatenate([m.positions[m.indices] for m in meshes])
    np.testing.assert_array_equal(actual, positions[indices])
    assert meshes[0].extras["building_id"] == "stable"
    assert meshes[0].extras["facade_index"] == 7


def test_tessellated_program_coalesces_only_equal_consecutive_vertex_ranges(monkeypatch):
    from turku3d import facade_program_geometry as pg
    from turku3d import facade_programs as fp
    from turku3d.coarse import stored_facades as sf

    monkeypatch.setattr(sf, "building", lambda key: {})
    monkeypatch.setattr(fp, "measured", lambda key: {})
    monkeypatch.setattr(fp, "resolve", lambda *args: dict(walls=[{}], storeys={"value": 4}))
    monkeypatch.setattr(fp, "compile_wall", lambda *args: {"origin": [0, 0]})
    styles = [{"module": m, "origin": [0, 3], "size": [10, 12]} for m in (1, 1, 2, 2, 1)]
    parts = [(wall() + [i * 10, 0, 0], np.ones((4, 2)) * i, style)
             for i, style in enumerate(styles)]
    monkeypatch.setattr(pg, "wall_parts", lambda *args: iter(parts))
    b = SimpleNamespace(key="part", facade_key="stable", base=3, top=15)
    mesh = wall_meshes(b, wall(), np.zeros(3))[0]
    rows = mesh.extras["facade_kit_walls"]
    assert mesh.extras["facade_kit"]["count"] == 4
    assert [(r["start"], r["count"], r["module"]) for r in rows] == [(0, 8, 1), (8, 8, 2), (16, 4, 1)]
    expanded = [None] * len(mesh.positions)
    for row in rows:
        expanded[row["start"]:row["start"] + row["count"]] = [
            {k: v for k, v in row.items() if k not in {"start", "count"}}
        ] * row["count"]
    assert expanded == [style for style in styles for _ in range(4)]
    np.testing.assert_array_equal(mesh.positions, np.concatenate([p for p, _, _ in parts]))
    np.testing.assert_array_equal(mesh.uvs, np.concatenate([uv for _, uv, _ in parts]))
    np.testing.assert_array_equal(mesh.indices, np.concatenate([
        np.array([[0, 3, 2], [0, 2, 1]]) + i * 4 for i in range(len(parts))
    ]))


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
    b = SimpleNamespace(key="part", facade_key="b", base=3, top=15,
                        footprint=shapely.box(0, 0, 10, 2), attributes={"levels": 4})
    meshes = sf.wall_meshes(b, wall(), np.zeros(3))
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


def test_draft_register_rows_end_at_eave_without_splitting_wall_geometry(monkeypatch):
    from turku3d.coarse import stored_facades as sf

    monkeypatch.setattr(sf, "building", lambda key: {})
    b = SimpleNamespace(key="gable", facade_key="gable", base=3, top=18,
                        footprint=shapely.box(0, 0, 10, 2), attributes={"levels": 4, "use": "1150"})
    # Four authoritative floors fit below the eave at 14; the shader blanks the gable.
    positions = wall()
    positions[2:, 1] = [18, 14]
    meshes = wall_meshes(b, positions, np.zeros(3))
    assert len(meshes) == 1
    np.testing.assert_array_equal(meshes[0].positions, positions)
    style = meshes[0].extras["facade_kit"]
    assert style["origin"][1] == 3 and style["size"][1] == 11
    assert abs(style["ground"] + 3 * style["upper"] - 11) < 1e-6
    assert style["ground"] > style["upper"]  # supported business ground floor
    # Conflicting height does not silently discard any of the four register floors.
    positions[2:, 1] = [6.5, 5.5]
    meshes = wall_meshes(b, positions, np.zeros(3))
    assert len(meshes) == 1
    style = meshes[0].extras["facade_kit"]
    assert abs(style["ground"] + 3 * style["upper"] - 2.5) < 1e-6
    assert meshes[0].extras["facade_program"]["storeys"]["value"] == 4
    np.testing.assert_array_equal(meshes[0].positions, positions)


def test_gable_segments_share_eave_even_when_high_segment_fits_more_rows(monkeypatch):
    from turku3d.coarse import stored_facades as sf
    from turku3d.coarse.extrusion import extrude
    from turku3d.coarse.inputs import Building

    monkeypatch.setattr(sf, "building", lambda key: {})
    footprint = shapely.Polygon([(0, 0), (10, 0), (10, 5), (10, 10), (0, 10)])
    b = Building("slope", footprint, 0, 18, "facade_brick")
    b.attributes = {"levels": 3, "material_class": "brick"}
    b.roof_parts = [(footprint, np.array([0, 0.8, 10]))]
    _, _, _, _, walls, _, indices = extrude(b)
    meshes = wall_meshes(b, walls, np.zeros(3))
    assert len(meshes) == 1 and meshes[0].positions[:, 1].max() == 18
    for style in meshes[0].extras["facade_kit_walls"]:
        assert style["size"][1] == 10  # all segments share this ring's eave
        assert abs(style["ground"] + 2 * style["upper"] - 10) < 1e-6
    # Shader floor limits leave the complete wall geometry and area intact.
    def area(points, triangles):
        a, b, c = points[triangles].transpose(1, 0, 2)
        return np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2
    assert abs(sum(area(m.positions, m.indices) for m in meshes) - area(walls, indices)) < 1e-6


def test_mixed_reviewed_section_overrides_map_but_keeps_hidden_ground(monkeypatch):
    from turku3d import facade_programs as fp
    from turku3d.coarse import stored_facades as sf
    from turku3d.facade_sections import prepare
    from turku3d.wall_surfaces import validate_meshes
    from turkutex.facade_structure import compile_structure

    row = dict(wall_id='observed', width_m=10, height_m=12, base_h=3,
               p0=[C.ORIGIN_E, C.ORIGIN_N], p1=[C.ORIGIN_E + 10, C.ORIGIN_N], normal=[0, 1])
    form = dict(wall_id='observed', render_deferred='mixed niches', facade_observations=dict(
        sections=[dict(position='upper', architectural_bays=3,
                       bay_kinds=['window', 'blind niche', 'window'],
                       heads=['flat', 'round niche inset', 'flat'],
                       surrounds=['triangular pediment'] * 3, material='render')],
        confidence=dict(bays=.9)))
    observed = prepare(dict(building_id='stable', storeys=dict(value=2),
                            walls=[compile_structure(row, form, 2)]))
    monkeypatch.setattr(fp, 'measured', lambda _: observed)
    d = renderer().register_description('stable', 'stable', {}, 10, 12)
    d['wall_colour'] = '#B98B7E'
    monkeypatch.setattr(fp, 'read_description', lambda _: d)
    monkeypatch.setattr(sf, 'building', lambda _: dict(facades=[facade()]))
    b = SimpleNamespace(key='part', facade_key='stable', base=3, top=15,
                        footprint=shapely.box(0, 0, 10, 2), attributes=dict(levels=2))
    meshes = wall_meshes(b, wall(), np.zeros(3))
    diagnostic = meshes[0].extras['facade_program']['walls'][0]
    assert diagnostic['paint_source'] == 'reviewed-program'
    assert diagnostic['wall']['colour']['value'] == '#B98B7E'
    stored = [m for m in meshes if m.extras['material_key'] == 'facade_stored']
    assert stored and all(m.positions[:, 1].max() <= 9 for m in stored)
    kit = next(m for m in meshes if m.extras['material_key'] == 'facade_kit')
    styles = kit.extras['facade_kit_walls']
    windows = [s for s in styles if s['detail'][3] > 0 and s['detail'][3] % 4 == 1]
    assert len(windows) == 2
    assert all(s['origin'][1] >= 9 for s in windows)
    niches = [s for s in styles if s['module'] >= 42 and s['wall'] == '#aa8074']
    assert niches and all(s['detail'][3] == 0 for s in niches)
    assert validate_meshes(meshes, 'fixture')['violations'] == 0

    # A partial material observation has no authority to replace mapped openings.
    form['facade_observations']['sections'] = [dict(position='upper', material='brick')]
    observed = prepare(dict(building_id='stable', storeys=dict(value=2),
                            walls=[compile_structure(row, form, 2)]))
    meshes = wall_meshes(b, wall(), np.zeros(3))
    assert all(m.extras['material_key'] == 'facade_stored' for m in meshes)
    area = sum(shapely.Polygon(m.positions[:, [0, 1]]).area for m in meshes)
    assert area == 120


def test_bundled_description_is_normal_fallback_with_alias_and_tile_hash(tmp_path, monkeypatch):
    monkeypatch.setenv('TURKU3D_WORK', str(tmp_path / 'work'))
    monkeypatch.setenv('TURKU3D_DATA', str(tmp_path / 'data'))
    monkeypatch.setenv('TURKU3D_FACADE_ASSETS', str(tmp_path / 'assets'))
    monkeypatch.setattr(C, 'REPO_ROOT', tmp_path)
    directory = tmp_path / 'textures/library/facade-params'
    directory.mkdir(parents=True)
    before = parameter_hash(['other'])
    d = renderer().register_description('b_0', 'b', {}, 10, 12)
    record = dict(building_key='b', description=d, width_m=10)
    (directory / opaque('b')).write_text(json.dumps(record))
    assert read_description('gis-b') == d
    assert parameter_hash(['other']) == before
    own = parameter_hash(['b'])
    d['wall_colour'] = '#B98B7E'
    (directory / opaque('b')).write_text(json.dumps(record))
    assert parameter_hash(['b']) != own
    shared = tmp_path / 'data/facade-params'
    shared.mkdir(parents=True)
    d['wall_colour'] = '#123456'
    (shared / opaque('b')).write_text(json.dumps(record))
    assert read_description('b')['wall_colour'] == '#123456'
