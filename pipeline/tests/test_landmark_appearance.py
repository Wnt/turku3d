import json
import struct
from types import SimpleNamespace

import numpy as np
import pytest
from turku3d.landmarks.appearance import roof_mesh, wall_meshes
from turku3d.landmarks.ranking import rank
from turku3d.package.gltf import build_glb


@pytest.fixture(autouse=True)
def isolated_parameter_store(tmp_path, monkeypatch):
    from turku3d import config as C

    monkeypatch.setattr(C, "data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(C, "work_root", lambda: tmp_path / "work")


def building(key="103371570S"):
    return SimpleNamespace(facade_key=key, key=key, base=12.0, top=90.0, attributes={})


def test_unidentified_building_preserves_generic_facade_path():
    assert wall_meshes(building("ordinary"), np.zeros((4, 3)), np.zeros(3)) is None


def test_cathedral_glb_has_physical_maps_and_height_aligned_uv():
    b = building()
    wall = np.array([[0, 12, 0], [15, 12, 0], [15, 75, 0], [0, 75, 0]], float)
    meshes = wall_meshes(b, wall, np.zeros(3))
    m = meshes[0]
    assert m.extras["landmark_tower"]
    assert np.all((m.uvs >= 0) & (m.uvs <= 1))
    assert m.positions[:, 2].max() > 0.2
    data = build_glb(meshes)
    size = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + size])
    mat = doc["materials"][0]
    assert len(doc["images"]) == 3
    assert mat["normalTexture"]["index"] != mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    assert mat["occlusionTexture"] == mat["pbrMetallicRoughness"]["metallicRoughnessTexture"]
    assert np.isfinite(m.positions).all()


def test_opening_is_cut_from_front_wall_and_closed_by_recess():
    import shapely
    from turku3d.landmarks.relief import apply
    from turku3d.package.gltf import GlbMesh

    d = dict(
        windows=dict(
            type="gothic-lancet", width_m=2, height_m=4, spacing_m=5, sill_m=2, mullions=1
        ),
        trim_colour=[210, 204, 181],
    )
    mesh = GlbMesh(
        "wall",
        np.array([[0, 0, 0], [5, 0, 0], [5, 8, 0], [0, 8, 0]], float),
        np.array([[0, 3, 2], [0, 2, 1]]),
        uvs=np.array([[0, 1], [1, 1], [1, 0], [0, 0]]),
    )
    wall, reveals = apply(mesh, 0, d, False)
    triangles = wall.positions[wall.indices]
    front = triangles[np.all(np.abs(triangles[:, :, 2]) < 1e-8, axis=1)]
    inset = triangles[np.all(triangles[:, :, 2] > 0.3, axis=1)]
    sample = shapely.Point(2.5, 3)
    assert not shapely.union_all(shapely.polygons(front[:, :, :2])).covers(sample)
    assert shapely.union_all(shapely.polygons(inset[:, :, :2])).covers(sample)
    assert reveals is not None and len(reveals.indices) > 20
    assert np.isfinite(reveals.positions).all()


def test_roof_triangle_projection_has_no_degenerate_spire_uv():
    b = building()
    p = np.array([[0, 65, 0], [4, 65, 0], [2, 90, 2]], float)
    mesh = roof_mesh(b, p, np.array([[0, 1, 2]]), np.zeros(3))
    assert np.linalg.det(np.stack([mesh.uvs[1] - mesh.uvs[0], mesh.uvs[2] - mesh.uvs[0]])) > 1


def test_coplanar_roof_triangles_keep_continuous_seams():
    p = np.array([[0, 10, 0], [4, 10, 0], [4, 15, 4], [0, 15, 4]], float)
    mesh = roof_mesh(building(), p, np.array([[0, 1, 2], [0, 2, 3]]), np.zeros(3))
    assert np.allclose(mesh.uvs[0], mesh.uvs[3])
    assert np.allclose(mesh.uvs[2], mesh.uvs[4])


def test_mixed_wall_material_mask_preserves_masonry_and_metal():
    import io

    from PIL import Image
    from turku3d.landmarks.procedural import maps

    metal = np.zeros((8, 8))
    metal[:4] = 1
    _, _, orm = maps(np.full((8, 8, 3), 100), np.zeros((8, 8)), 1, 1, metal=metal)
    data = np.asarray(Image.open(io.BytesIO(orm)))
    assert np.all(data[:4, :, 2] > 0) and np.all(data[4:, :, 2] == 0)
    assert data[0, 0, 1] < data[7, 0, 1]


def test_ranking_promotes_evidence_and_reports_unknowns():
    tags = dict(
        building="cathedral",
        amenity="place_of_worship",
        historic="church",
        wikipedia="fi:Turun tuomiokirkko",
        wikidata="Q160165",
    )
    a = rank(tags)
    b = rank(tags, dict(rky="RKY test", monthly_pageviews=20000, height_m=90, visible_streets=4))
    assert a["tier"] == "landmark" and b["score"] > a["score"]
    assert "rky" in a["missing_evidence"] and "rky" not in b["missing_evidence"]
    assert sum(r["points"] for r in b["reasons"]) == b["score"]
    assert rank({"building": "apartments"})["tier"] == "ordinary"


def test_ranked_buildings_consolidate_way_and_relation_without_double_treatment():
    from turku3d.landmarks.measure import consolidate

    rows = [
        dict(id="way/1", building_id="city-1", name="Castle", tags={"name": "Castle"}, score=100),
        dict(id="relation/2", building_id="city-1", name="relation/2", tags={}, score=90),
    ]
    result = consolidate(rows)
    assert len(result) == 1 and result[0]["id"] == "way/1"
    assert result[0]["aliases"] == ["relation/2"]


def test_footprint_rarity_compares_shapes_with_the_city_population():
    from shapely.geometry import box
    from turku3d.landmarks.ranking import footprint_rarity

    rows = [
        dict(
            footprint_wkt=box(i * 30, 0, i * 30 + 10, 10).wkt, tags={"building": "yes"}, evidence={}
        )
        for i in range(100)
    ]
    rows.append(dict(footprint_wkt=box(0, 0, 80, 10).wkt, tags={"building": "yes"}, evidence={}))
    footprint_rarity(rows)
    assert rows[-1]["score"] > rows[0]["score"]
    assert rows[-1]["evidence"]["footprint_rarity"]["matching_shapes"] == 1


def test_vision_parameters_reject_mixed_region_overlapping_window_rows():
    from turku3d.landmarks.descriptions import ROOT
    from turku3d.landmarks.references import validate

    d = json.loads((ROOT / "103371570S.json").read_text())
    d["windows"]["rows_m"] = [4, 6]
    with pytest.raises(ValueError, match="rows overlap"):
        validate(d, d["building_id"])
    d["windows"]["rows_m"] = [4, 10]
    validate(d, d["building_id"])


def test_internal_roof_closure_is_trimmed_without_mutating_collision_shell():
    from shapely.geometry import box
    from turku3d.landmarks.appearance import visible_walls

    b = building()
    b.base = 0
    b.roof_parts = [(box(-10, -10, 10, 10), np.array([0, 0, 12.0]))]
    walls = np.array([[0, 0, 0], [2, 0, 0], [2, 20, 0], [0, 20, 0]], float)
    result = visible_walls(b, walls)
    assert np.all(result[0, :2, 1] == 12)
    assert np.all(walls[:2, 1] == 0)
    b.roof_parts = [(box(0, 0, 2, 2), np.array([0, 0, 12.0]))]
    assert np.array_equal(visible_walls(b, walls)[0], walls)


def test_arch_survives_anisotropic_texture_density():
    from PIL import Image, ImageDraw
    from turku3d.landmarks.procedural import arch

    # A wide opening on a height-capped atlas has unequal horizontal/vertical texel scales.
    image = Image.new("RGB", (220, 60))
    arch(ImageDraw.Draw(image), (10, 5, 210, 55), (255, 255, 255), False, rise=20)
    assert image.getpixel((110, 6)) == (255, 255, 255)
    assert image.getpixel((10, 5)) == (0, 0, 0)


def test_landmark_description_invalidates_only_its_own_tiles(tmp_path, monkeypatch):
    from turku3d.landmarks import descriptions

    monkeypatch.setattr(descriptions, "ROOT", tmp_path)
    a = descriptions.fingerprint([["landmark-a", ""]])
    b = descriptions.fingerprint([["landmark-b", ""]])
    (tmp_path / "landmark-a.json").write_text(
        json.dumps(dict(version=1, building_id="landmark-a", colour=[1, 2, 3]))
    )
    assert descriptions.fingerprint([["landmark-a", ""]]) != a
    assert descriptions.fingerprint([["landmark-b", ""]]) == b
    assert not descriptions.manifest_current(
        dict(landmark_buildings=[["landmark-a", ""]], landmark_params=a)
    )
    assert descriptions.manifest_current(
        dict(landmark_buildings=[["landmark-b", ""]], landmark_params=b)
    )


def test_narrow_tower_wall_keeps_copper_above_its_stage():
    from turku3d.landmarks.appearance import material_wall_parts
    from turku3d.landmarks.descriptions import resolve

    b = building()
    d = resolve(b)
    wall = np.array([[0, 12, 0], [0.5, 12, 0], [0.5, 85, 0], [0, 85, 0]], float)
    parts = list(material_wall_parts(b, wall, d))
    assert {kind for _, kind in parts} == {"stone", "wall", "roof"}
    assert all(
        v[:, 1].min() >= b.base + d["tower"]["copper_start_m"] - 0.001
        for v, kind in parts
        if kind == "roof"
    )


def test_atlas_batches_all_pbr_maps_without_losing_geometry_or_bleeding_edges():
    import io

    from PIL import Image
    from turku3d.landmarks.atlas import batch
    from turku3d.landmarks.procedural import png
    from turku3d.package.gltf import GlbMesh

    meshes = []
    for index, colour in enumerate(((230, 40, 10), (10, 40, 230))):
        albedo = png(np.tile(colour, (32, 32, 1)))
        normal = png(np.tile((128, 128, 255), (32, 32, 1)))
        orm = png(np.tile((220, 180, index * 255), (32, 32, 1)))
        meshes.append(
            GlbMesh(
                str(index),
                np.array([[index, 0, 0], [index + 1, 0, 0], [index, 1, 0]]),
                np.array([[0, 1, 2]]),
                uvs=np.array([[0, 0], [1, 0], [0, 1]]),
                image=albedo,
                normal_image=normal,
                orm_image=orm,
            )
        )
    result = batch(meshes, size=128)
    assert len(result) == 1
    m = result[0]
    assert np.array_equal(m.positions, np.concatenate([x.positions for x in meshes]))
    assert np.array_equal(m.indices, [[0, 1, 2], [3, 4, 5]])
    for data, expected in (
        (m.image, [(230, 40, 10), (10, 40, 230)]),
        (m.orm_image, [(220, 180, 0), (220, 180, 255)]),
        (m.normal_image, [(128, 128, 255)] * 2),
    ):
        atlas = Image.open(io.BytesIO(data))
        for i, colour in enumerate(expected):
            x, y = (m.uvs[i * 3] * atlas.size).astype(int)
            assert atlas.getpixel((x, y)) == colour
            assert atlas.getpixel((x - 4, y - 4)) == colour
