"""Playground survey import: EPSG:3877 polygons, located outlines and unlocated counts."""

import json

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
