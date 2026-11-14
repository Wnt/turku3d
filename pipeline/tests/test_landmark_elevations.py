import io
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image

from turku3d.facade_assets import best_description
from turku3d.landmarks.appearance import wall_meshes
from turku3d.landmarks.descriptions import resolve
from turku3d.landmarks.elevations import coordinates, texture


def test_reference_consensus_beats_wide_occluded_view():
    def row(key, material, width, source="reference"):
        return dict(facade_id=key, width_m=width, fast_description=dict(
            source=source, description=dict(material=material)))
    rows = [row("a", "brick", 3), row("b", "brick", 5), row("c", "stone", 38),
            row("d", "stone", 50, "building")]
    assert best_description(rows)["facade_id"] == "b"
    assert best_description([rows[0], rows[2]])["facade_id"] == "c"


def test_exported_automatic_choice_is_reselected_but_reviewed_override_survives(tmp_path, monkeypatch):
    from turku3d import facade_assets as assets
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_FACADE_ASSETS", str(tmp_path / "assets"))
    directory = tmp_path / "assets/facade-params"
    directory.mkdir(parents=True)
    path = directory / assets.opaque("b")
    row = dict(source="existing-generated-facade", description=dict(material="metal"))
    path.write_text(json.dumps(row))
    monkeypatch.setattr(assets, "building", lambda _: dict(facades=[dict(metadata=dict(
        facade_id="b_1", width_m=10, fast_description=dict(source="reference",
        description=dict(material="concrete"))))]))
    assert assets.description_row("b")["description"]["material"] == "concrete"
    row["source"] = "reviewed"
    path.write_text(json.dumps(row))
    assert assets.description_row("b")["description"]["material"] == "metal"


def test_fragments_share_measured_elevation_and_do_not_repeat_shopfronts():
    b = SimpleNamespace(key="hall", facade_key="103304670P", base=4.039, attributes={})
    d = resolve(b)
    # Base-face UV sharing is independent of the separately tested relief mesh.
    for elevation in d["elevations"]:
        elevation.pop("relief", None)
    row = d["elevations"][0]
    origin, axis = np.array(row["origin_xz"]), np.array(row["axis_xz"])
    def quad(lo, hi):
        xz = origin + np.array([lo, hi, hi, lo])[:, None] * axis
        return np.c_[xz[:, 0], b.base + np.array([0, 0, 6, 6]), xz[:, 1]]
    left, right = quad(0, 8), quad(8, row["width_m"])
    np.testing.assert_allclose(coordinates(left, b.base, row)[[1, 2]],
                               coordinates(right, b.base, row)[[0, 3]])
    meshes = wall_meshes(b, np.concatenate([left, right]), np.zeros(3), d)
    assert sum(len(m.indices) for m in meshes) == 4
    assert len(meshes) == 1
    albedo, _, _ = texture(json.dumps(d), json.dumps(row))
    image = np.asarray(Image.open(io.BytesIO(albedo)))
    # Brick between openings remains red; the three tall panes remain neutral.
    def patch(x, y):
        px = round(x / row["width_m"] * image.shape[1])
        py = round((1 - y / row["height_m"]) * image.shape[0])
        return image[py-2:py+3, px-2:px+3].mean((0, 1))
    brick = patch(2.5, 2.0)
    assert brick[0] - brick[1] > 30
    for x in (7.3, 8.675, 10.05):
        glass = patch(x, 4.5)
        assert abs(glass[0] - glass[1]) < 15


def test_stone_zones_face_part_of_a_rendered_front_and_courtyard_walls_take_their_own_plane():
    from turku3d.landmarks.elevations import select

    b = SimpleNamespace(key="block", facade_key="103295899S", base=0.0, attributes={})
    d = resolve(b)
    front = dict(origin_xz=[0, 0], axis_xz=[1, 0], normal_xz=[0, -1], width_m=10, height_m=10,
                 plinth_m=0.2, stone_zones=[[0, 10, 0, 3], [6, 2, 3, 7]])
    court = dict(front, origin_xz=[0, 20], stone_zones=[])
    albedo, _, _ = texture(json.dumps(d), json.dumps(front))
    image = np.asarray(Image.open(io.BytesIO(albedo))).astype(float)

    def patch(x, y):
        px = round(x / 10 * image.shape[1])
        py = round((1 - y / 10) * image.shape[0])
        return image[py - 20:py + 21, px - 20:px + 21].reshape(-1, 3)

    render, ground, bay = patch(3, 6), patch(3, 1.5), patch(7, 6)
    # Render is the description's warm, even colour; the stone zones are grey and jointed.
    assert render.mean(0)[0] - render.mean(0)[2] > 40 and render.std(0).max() < 6
    for stone in (ground, bay):
        assert abs(stone.mean(0)[0] - stone.mean(0)[2]) < 25 and stone.std(0).max() > 6

    def wall(z):
        return np.array([[0, 0, z], [4, 0, z], [4, 5, z], [0, 5, z]], float)

    assert select(wall(0.4), [front, court]) is front
    assert select(wall(18.0), [front, court]) is court
    assert select(wall(0.4)[[1, 0, 3, 2]], [front, court]) is None


def test_reviewed_jugendlinna_fronts_are_granite_below_and_in_their_bays():
    d = resolve(SimpleNamespace(key="b", facade_key="103295899S", base=0.0, attributes={}))
    fronts = [row for row in d["elevations"] if row.get("stone_zones")]
    assert len(fronts) == 2
    for row in fronts:
        ground = [z for z in row["stone_zones"] if z[2] == 0]
        assert ground and ground[0][1] >= row["width_m"] - 0.01
        assert sum(z[1] for z in row["stone_zones"] if z[2] > 0) > row["width_m"] / 3
        arched = [o for o in row["openings"] if o.get("shape") == "round" and o["sill_m"] < 1]
        assert len(arched) >= 8 and sum(o.get("kind") == "door" for o in arched) == 1


def test_review_patches_rebuild_the_reviewed_tiles_and_the_street_wall_keeps_its_openings():
    from turku3d.facade_programs import measured
    from turku3d.migrations import block_balconies, registry, review_patches

    assert registry.CHAIN.index(review_patches.MIGRATION) > registry.CHAIN.index(block_balconies.MIGRATION)
    assert review_patches.affected({"tile": "t0_1_-1"})
    assert review_patches.affected({"tile": "t0_0_1", "building_keys": ["x", "103295899S"]})
    assert review_patches.affected({"tile": "t0_-1_1", "building_keys": ["103304638N"]})
    assert not review_patches.affected({"tile": "t0_0_0", "building_keys": ["x"]}) and not review_patches.affected({})
    walls = {w["wall_id"].rsplit("_", 1)[-1]: w for w in measured("103304638N")["walls"] if "_review_" in w["wall_id"]}
    street = walls["street"]["structure"]["floors"][0]["bays"]
    assert street.count("door") == 2 and street.count("window") == 4
    assert set(walls["gable"]["structure"]["floors"][0]["bays"]) == {"blank"}
