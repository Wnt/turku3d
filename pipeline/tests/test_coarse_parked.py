"""Draft parking safety, deterministic rarity and shared asset delivery."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import parked
from turku3d.parked.placement import Feature, footprint

TILE = C.VisualTile(0, 3, 0)


def fixture():
    return [Feature("lot", shapely.box(770, 30, 970, 130), {"amenity": "parking"})]


def test_deterministic_safe_and_supported():
    features = fixture() + [
        Feature("building", shapely.box(820, 40, 850, 70), {"building": "yes"}),
        Feature("road", shapely.LineString([(750, 100), (1000, 100)]), {"highway": "residential"}),
    ]
    water = [shapely.box(890, 30, 920, 70)]
    heights = np.zeros((129, 129))
    cars = parked.place(TILE, (features, []), heights, water)
    assert len(cars) > 10
    assert cars == parked.place(TILE, (features[::-1], []), heights, water)
    shapes = []
    for car in cars:
        shape = footprint(car["x"], car["z"], car["yaw"], car["width"], car["length"])
        assert shapely.box(*TILE.bbox.buffered(-0.3).as_tuple()).covers(shape)
        assert not shape.intersects(features[1].geometry.buffer(0.5))
        assert not shape.intersects(features[2].geometry.buffer(3.35))
        assert not shape.intersects(water[0])
        assert all(shape.distance(other) >= 0.55 for other in shapes)
        shapes.append(shape)
    steep = np.tile(np.arange(129) * 0.3, (129, 1))
    assert not parked.place(TILE, (features, []), steep, water)


def test_truck_draw_is_one_in_thousand_and_fits_next_bay(monkeypatch):
    count = sum(parked.truck_slot(TILE, str(i)) for i in range(100_000))
    assert 70 <= count <= 130
    # Oversized truck skips a narrow strip and is carried to a later broad one.
    features = [
        Feature("a", shapely.box(770, 20, 790, 22.0), {"amenity": "parking"}),
        Feature("b", shapely.box(800, 100, 980, 106), {"parking": "street_side"}),
    ]
    monkeypatch.setattr(parked, "truck_slot", lambda tile, key, seed: key.startswith("a:"))
    cars = parked.place(TILE, (features, []), np.zeros((129, 129)))
    trucks = [c for c in cars if c["model"] == "truck"]
    assert len(trucks) == 1 and trucks[0]["source"] == "b"
    assert (trucks[0]["width"], trucks[0]["height"], trucks[0]["length"]) == parked.TRUCK_SIZE
    assert cars == parked.place(TILE, (features[::-1], []), np.zeros((129, 129)))


def test_shared_models_have_content_addresses_and_skip_builtin_truck(tmp_path):
    cars = [{"model": "sedan", "color": "blue"}, {"model": "truck", "color": "blue"}]
    entries = parked.publish_models(cars, tmp_path)
    assert set(entries) == {"sedan/blue"}
    entry = entries["sedan/blue"]
    data = (tmp_path / "shared" / entry["url"].split("/")[-1]).read_bytes()
    assert data[:4] == b"glTF" and len(data) == entry["bytes"]
    from turku3d.coarse.service import SHARED

    assert SHARED.fullmatch(entry["url"].split("/")[-1])
    assert not SHARED.fullmatch("../secret.glb")
