"""Playground survey import: EPSG:3877 polygons, located outlines and unlocated counts."""

import json
import math

import numpy as np
import pytest
import shapely

from turku3d import config as C
from turku3d import playgrounds as P


def survey(tmp_path, equipment, objects):
    data = tmp_path / "app/data"
    data.mkdir(parents=True)
    lat, lon = C.en_to_wgs84(23460966.0, 6704084.0)
    square = [[lon + dx, lat + dy] for dx, dy in ((0, 0), (4e-4, 0), (4e-4, 2e-4), (0, 2e-4), (0, 0))]
    (data / "sites.json").write_text(json.dumps([{
        "id": "P1", "name": "Testipuisto", "kind": "park", "municipality": "Turku",
        "x": 23460966.0, "y": 6704084.0, "polygon": {"type": "Polygon", "coordinates": [square]},
        "equipment": equipment, "surface_aerial": "sand",
        "big_structures": [{"label": "Köysipyramidi", "height_m": 4.4, "status": "present"},
                           {"label": "Vanha torni", "height_m": 5.0, "status": "removed"}],
    }]))
    (data / "categories.json").write_text(json.dumps(
        [{"id": c, "fi": c, "en": c} for c in ("swing", "slide", "toilet")]))
    objs = tmp_path / "inventory/objects"
    objs.mkdir(parents=True)
    (objs / "P1.json").write_text(json.dumps({"result": {"objects": objects}}))
    (objs / "P1-failed.json").write_text("{}")
    return tmp_path


def outline(category, label, e=23460970.0, n=6704090.0):
    return {"category": category, "label": label, "confidence": 0.9,
            "polygon": [[e, n], [e + 4, n], [e + 4, n + 2], [e, n + 2]]}


def test_import_converts_polygons_and_counts_unlocated_frames(tmp_path):
    root = survey(tmp_path, [
        {"category": "swing", "source": "ortho-inventory", "count": 5, "confidence": 0.8, "detail": ""},
        {"category": "slide", "source": "ortho-inventory", "count": 2, "confidence": 0.8, "detail": ""},
        {"category": "slide", "source": "blog", "confidence": 0.7, "detail": ""},
        {"category": "spring", "source": "osm", "confidence": 0.85,
         "detail": "playground=springy (70 m from site point, nearest)"},
        {"category": "carousel", "source": "ortho-inventory", "count": 1, "confidence": 0.3, "detail": ""},
        {"category": "toilet", "source": "register", "confidence": 1.0, "detail": ""},
    ], [outline("swing", "Kahden istuimen keinu"), outline("slide", "Liukumäki"),
        outline("other", "Pohjoinen puistopenkki"), outline("other", "Maalattu ruutuhyppely")])
    data = P.import_survey(root)
    site = data["sites"][0]
    ring = site["polygons"][0][0]
    assert abs(ring[0][0] - 23460966.0) < 0.5 and abs(ring[0][1] - 6704084.0) < 0.5
    assert site["area_m2"] > 150 and not site["placeholder"]
    assert [o["category"] for o in site["objects"]] == ["swing", "slide", "bench"]
    # 5 seats, 2 on the located frame: one more frame takes two, the odd seat hangs on a frame.
    # A far "nearest" OSM match, a low-confidence guess and toilets place nothing.
    assert site["unlocated"] == {"slide": 1, "swing": 1}
    assert [s["label"] for s in site["big_structures"]] == ["Köysipyramidi"]
    assert data["source"]["files"] == 3  # the failed-run file is not an outline file


def test_seats_from_labels():
    assert P.seats("Kolmen istuimen keinu") == 3
    assert P.seats("Pitkä keinurunko") == 4
    assert P.seats("Eteläinen keinuteline") == 2


def test_prepare_keeps_sites_reaching_the_tile_in_engine_coordinates(tmp_path):
    root = survey(tmp_path, [], [outline("swing", "Keinu")])
    path = P.write(P.import_survey(root), tmp_path / "derived/playgrounds/playgrounds.json")
    x, _, z = C.en_to_local(23460966.0, 6704084.0)
    tile = C.VisualTile.at(x, z)
    sites = P.prepare(tile, path)
    assert [s["id"] for s in sites] == ["P1"]
    (ox, oz) = sites[0]["objects"][0]["polygon"][0]
    assert abs(ox - (23460970.0 - C.ORIGIN_E)) < 1e-6 and abs(oz + (6704090.0 - C.ORIGIN_N)) < 1e-6
    far = C.VisualTile(0, tile.i + 3, tile.j)
    assert P.prepare(far, path) == []


def site_record(tile, objects=(), unlocated=None, surface="sand", size=30.0):
    x0, _, z0 = tile.origin
    return {
        "id": "P9", "name": "Testi", "kind": "park", "municipality": "Turku", "area_m2": size * size,
        "placeholder": False, "surface": surface, "unlocated": unlocated or {}, "big_structures": [
            {"label": "Köysipyramidi", "height_m": 4.6}],
        "point": (x0 + 50, z0 + 50), "polygons": [shapely.box(x0 + 40, z0 + 40, x0 + 40 + size, z0 + 40 + size)],
        "objects": list(objects), "pools": [],
    }


def test_outlines_unlocated_counts_and_edge_benches_are_placed_with_collision():
    from turku3d.coarse import playgrounds as stage

    tile = C.VisualTile(0, 0, 0)
    x0, _, z0 = tile.origin
    swing = {"id": "P9:0", "category": "swing", "label": "Kolmen istuimen keinu",
             "polygon": [(x0 + 45, z0 + 45), (x0 + 50, z0 + 45), (x0 + 50, z0 + 47), (x0 + 45, z0 + 47)]}
    net = {"id": "P9:1", "category": "net", "label": "Köysipyramidi",
           "polygon": [(x0 + 60, z0 + 60), (x0 + 65, z0 + 60), (x0 + 65, z0 + 65), (x0 + 60, z0 + 65)]}
    trunk = (x0 + 55.0, z0 + 50.0)
    result = stage.produce(tile, [site_record(tile, [swing, net], {"slide": 1, "spring": 2})], [],
                           lambda x, z: 10.0, [], [], [], [], [trunk], [])
    items = [i for i in result["items"] if i.kind != "play_pad"]
    by = {}
    for i in items:
        by.setdefault(i.category, []).append(i)
    s = by["swing"][0]
    assert (s.x, s.z) == pytest.approx((x0 + 47.5, z0 + 46)) and s.variant.startswith("l")
    assert "n3" in s.variant and abs(math.cos(s.yaw)) == pytest.approx(1)
    assert "h4.5" in by["net"][0].variant  # lidar height, quantised
    area = shapely.box(x0 + 40, z0 + 40, x0 + 70, z0 + 70)
    placed = by["slide"] + by["spring"]
    assert len(by["slide"]) == 1 and len(by["spring"]) == 2
    for i in placed:
        assert area.covers(shapely.Point(i.x, i.z))
        assert shapely.Point(i.x, i.z).distance(shapely.Polygon(swing["polygon"])) > stage.CLEAR
        assert math.hypot(i.x - trunk[0], i.z - trunk[1]) > stage.TREE_CLEAR
    assert len(by["bench"]) == stage.bench_share(900) and len(by["table"]) == 1
    for b in by["bench"]:
        front = (math.sin(b.yaw), math.cos(b.yaw))
        assert area.contains(shapely.Point(b.x + 3 * front[0], b.z + 3 * front[1]))
    assert sum(len(v) for v in result["collision"].values()) >= 2 * len(items) - 4
    assert result["sites"]["P9"]["swing:outline"] == 1 and result["sites"]["P9"]["slide:count"] == 1
    assert any(i.kind == "play_pad" for i in result["items"])
    assert result["glb"][:4] == b"glTF"


def test_mapped_benches_count_and_only_the_owner_tile_places_counts():
    from turku3d.coarse import playgrounds as stage

    tile = C.VisualTile(0, 0, 0)
    x0, _, z0 = tile.origin
    site = site_record(tile, [], {"sandbox": 1}, size=10.0)
    benches = [("bench", x0 + 41, z0 + 41)]
    result = stage.produce(tile, [site], [], lambda x, z: 0.0, [], [], [], [], [], benches)
    assert [i.category for i in result["items"] if i.kind != "play_pad"] == ["sandbox"]
    alone = stage.produce(tile, [site], [], lambda x, z: 0.0, [], [], [], [], [], [])
    assert [i.category for i in alone["items"] if i.kind != "play_pad"] == ["sandbox", "bench"]
    neighbour = C.VisualTile(0, tile.i + 1, tile.j)
    assert stage.produce(neighbour, [site], [], lambda x, z: 0.0, [], [], [], [], [], [])["items"] == []


def test_unsurveyed_osm_play_area_gets_a_swing_and_sandbox_with_collision():
    from turku3d.coarse import playgrounds as stage

    tile = C.VisualTile(0, 0, 0)
    x0, _, z0 = tile.origin
    area = shapely.box(x0 + 65, z0 + 95, x0 + 80, z0 + 110)
    result = stage.produce(tile, [], [("play-area", area)], lambda x, z: 2.0, [], [], [], [], [], [])
    assert sorted(i.category for i in result["items"]) == ["sandbox", "swing"]
    for cid, primitives in result["collision"].items():
        assert primitives
    assert all(area.covers(shapely.Point(i.x, i.z)) for i in result["items"])


def test_every_model_is_finite_and_keeps_the_furniture_palette():
    from PIL import Image
    import io
    from turku3d.playgrounds import models as M
    from turku3d.props import models as F

    for kind in M.BUILDERS:
        mesh = M.model(kind, "l4w3h2.5r2n2s0t1c1")
        assert len(mesh.indices) and np.isfinite(mesh.positions).all(), kind
        assert mesh.uvs[:, 0].max() < 1
    play = np.asarray(Image.open(io.BytesIO(M.atlas())))
    street = np.asarray(Image.open(io.BytesIO(F.atlas(""))).convert("RGB"))
    assert (play[:, :128] == street[:, :128]).all()
    assert M.model("bench").image == M.atlas()
