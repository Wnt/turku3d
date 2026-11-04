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
    assert np.allclose(m.uvs[:, 0], m.positions[:, 0] / 15)
    assert np.allclose(m.uvs[:, 1], 1 - (m.positions[:, 1] - 12) / 63)
    data = build_glb(meshes)
    size = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + size])
    mat = doc["materials"][0]
    assert len(doc["images"]) == 3
    assert mat["normalTexture"]["index"] != mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    assert mat["occlusionTexture"] == mat["pbrMetallicRoughness"]["metallicRoughnessTexture"]
    assert np.isfinite(m.positions).all()


def test_roof_triangle_projection_has_no_degenerate_spire_uv():
    b = building()
    p = np.array([[0, 65, 0], [4, 65, 0], [2, 90, 2]], float)
    mesh = roof_mesh(b, p, np.array([[0, 1, 2]]), np.zeros(3))
    assert np.linalg.det(np.stack([mesh.uvs[1] - mesh.uvs[0], mesh.uvs[2] - mesh.uvs[0]])) > 1


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



def test_reviewed_parameters_survive_automatic_reference_refresh(tmp_path, monkeypatch):
    from turku3d import config as C
    from turku3d.landmarks import descriptions

    reviewed = tmp_path / "reviewed"
    reviewed.mkdir()
    monkeypatch.setattr(descriptions, "ROOT", reviewed)
    data = C.data_root() / "landmark-descriptions"
    data.mkdir(parents=True)
    automatic = dict(version=1, building_id="new", reference_match="rejected", reference_confidence=0)
    (data / "new.json").write_text(json.dumps(automatic))
    assert descriptions.resolve(building("new")) is None
    automatic.update(reference_match="verified", reference_confidence=0.8, colour=[4, 5, 6])
    (data / "new.json").write_text(json.dumps(automatic))
    assert descriptions.resolve(building("new"))["colour"] == [4, 5, 6]
    (reviewed / "new.json").write_text(json.dumps(dict(version=1, building_id="new", colour=[1, 2, 3])))
    assert descriptions.resolve(building("new"))["colour"] == [1, 2, 3]
    override = C.work_root() / "landmark-descriptions"
    override.mkdir(parents=True)
    (override / "new.json").write_text(json.dumps(dict(version=1, building_id="new", colour=[7, 8, 9])))
    assert descriptions.resolve(building("new"))["colour"] == [7, 8, 9]


def test_automatic_seed_yields_to_measured_masonry_class(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from turku3d import config as C
    from turku3d.landmarks import descriptions

    reviewed = tmp_path / "reviewed"
    reviewed.mkdir()
    monkeypatch.setattr(descriptions, "ROOT", reviewed)
    data = C.data_root() / "landmark-descriptions"
    data.mkdir(parents=True, exist_ok=True)
    seed = dict(version=1, building_id="fort", reference_match="verified", reference_confidence=0.98)
    (data / "fort.json").write_text(json.dumps(seed))
    castle = SimpleNamespace(facade_key="fort", attributes=dict(construction_year=1280, footprint_m2=4193))
    house = SimpleNamespace(facade_key="fort", attributes=dict(construction_year=1905, footprint_m2=400))
    assert descriptions.resolve(castle) is None
    assert descriptions.resolve(house)["building_id"] == "fort"
    (reviewed / "fort.json").write_text(json.dumps(dict(version=1, building_id="fort", colour=[1, 2, 3])))
    assert descriptions.resolve(castle)["colour"] == [1, 2, 3]


def test_automatic_seed_yields_to_a_classical_order_named_by_reviewed_walls(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from turku3d import config as C
    from turku3d.landmarks import descriptions

    reviewed = tmp_path / "reviewed"
    reviewed.mkdir()
    monkeypatch.setattr(descriptions, "ROOT", reviewed)
    data = C.data_root() / "landmark-descriptions"
    data.mkdir(parents=True, exist_ok=True)
    attrs = dict(construction_year=1903, material_class="brick", use="2120", footprint_m2=1200)
    for key in ("103277355K", "civic-seed"):
        seed = dict(version=1, building_id=key, reference_match="verified", reference_confidence=0.96)
        (data / f"{key}.json").write_text(json.dumps(seed))
    # The library's reviewed wall names giant pale pilasters: its measured order wins.
    assert descriptions.resolve(SimpleNamespace(facade_key="103277355K", attributes=attrs)) is None
    # A rule-only civic order is a prior, not a measurement: the verified seed still applies.
    plain = SimpleNamespace(facade_key="civic-seed", attributes=attrs)
    assert descriptions.resolve(plain)["building_id"] == "civic-seed"


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


def test_automatic_seed_yields_to_a_public_monolith_with_photographed_blank_walls(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from turku3d import config as C
    from turku3d import facade_monolith
    from turku3d.landmarks import descriptions

    reviewed = tmp_path / "reviewed"
    reviewed.mkdir()
    monkeypatch.setattr(descriptions, "ROOT", reviewed)
    data = C.data_root() / "landmark-descriptions"
    data.mkdir(parents=True, exist_ok=True)
    seed = dict(version=1, building_id="museum", reference_match="verified", reference_confidence=0.97)
    (data / "museum.json").write_text(json.dumps(seed))
    museum = SimpleNamespace(facade_key="museum", attributes=dict(
        construction_year=1965, use="2120", osm_use="civic", entrance_use="cafe", footprint_m2=2083))
    blank = dict(blank=True, panels=True)
    monkeypatch.setattr(facade_monolith, "evidence", lambda key, observed: blank)
    assert descriptions.resolve(museum) is None  # the measured monolith class owns its walls
    blank = dict(blank=False, panels=False)
    monkeypatch.setattr(facade_monolith, "evidence", lambda key, observed: blank)
    assert descriptions.resolve(museum)["building_id"] == "museum"  # no blank evidence: seed applies


def test_unmeasured_window_rows_follow_the_storeys():
    from turku3d.landmarks.procedural import window_rows

    windows = dict(sill_m=5.7, height_m=2.6, rows_m=[])
    # The estimator's empty answer means uncertain rows: one row per register storey through the sill.
    assert window_rows(windows, 19.6, 4) == [0.8, 5.7, 10.6, 15.5]
    assert window_rows(windows, 20.4) == [2.3, 5.7, 9.1, 12.5, 15.9]  # 3.4 m storeys without a register value
    # A one-storey hall keeps its single measured row; tall openings never overlap.
    assert window_rows(dict(sill_m=3.0, height_m=7.0, rows_m=[]), 14.0, 1) == [3.0]
    assert window_rows(dict(windows, rows_m=[6, 2]), 19.6, 4) == [2.0, 6.0]
    assert window_rows(dict(sill_m=4.5, height_m=4.6), 30.0, 2) == [4.5]


def test_narrow_survey_pieces_share_their_facade_line_openings(tmp_path):
    from shapely.geometry import box
    from turku3d import config as C
    from turku3d.landmarks.descriptions import ROOT

    d = json.loads((ROOT / "103371570S.json").read_text())
    d.update(building_id="lined", wall_material="render")
    d["windows"].update(rows_m=[], sill_m=1.0, width_m=1.0, height_m=1.6, spacing_m=2.0)
    d["tower"]["minimum_height_m"] = 99
    store = C.work_root() / "landmark-descriptions"
    store.mkdir(parents=True)
    (store / "lined.json").write_text(json.dumps(d))
    b = SimpleNamespace(facade_key="lined", key="lined", base=0.0, top=9.0, attributes={"levels": 3},
                        footprint=box(0, -8, 12, 0), roof_parts=[])
    # Twelve 1 m pieces of the z=0 facade, outward-wound (outward normal +z).
    walls = np.array([[[x + 1, 0, 0], [x, 0, 0], [x, 9, 0], [x + 1, 9, 0]] for x in range(12)], float)
    meshes = wall_meshes(b, walls, np.zeros(3))
    lit = [m for m in meshes if not m.repeat]
    assert len(lit) == 1 and len(lit[0].positions) == 48, "all pieces sample one facade-line texture"
    from io import BytesIO

    from PIL import Image

    albedo = np.asarray(Image.open(BytesIO(lit[0].image)).convert("L"), float)
    assert (albedo < 60).mean() > 0.05, "the shared texture carries window openings"
    # Atlas UVs stay continuous across the piece seams: no two pieces repeat the same strip.
    centres = sorted(lit[0].uvs.reshape(12, 4, 2)[:, :, 0].mean(axis=1))
    assert np.all(np.diff(centres) > 0)


def test_landmark_window_rebuild_selects_only_tiles_with_described_landmarks():
    from turku3d import config as C
    from turku3d.landmarks.descriptions import ROOT
    from turku3d.migrations import landmark_windows, registry

    assert registry.CHAIN[-1] is landmark_windows.MIGRATION
    store = C.work_root() / "landmark-descriptions"
    store.mkdir(parents=True)
    (store / "shown.json").write_text(
        json.dumps(dict(json.loads((ROOT / "103371570S.json").read_text()), building_id="shown")))
    affected = landmark_windows.MIGRATION.affected
    assert affected({"landmark_buildings": [["plain", ""], ["shown", "way/1"]]})
    assert not affected({"landmark_buildings": [["plain", ""], ["other", "way/2"]]})
    assert not affected({})
