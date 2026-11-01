"""Draft parking safety, deterministic rarity and shared asset delivery."""

import numpy as np
import pytest
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


def test_cars_on_a_slope_rest_on_the_ground_and_lean_with_it():
    grade = 0.06  # rising towards +x (east)
    heights = np.tile(np.arange(129) * grade * TILE.size / 128, (129, 1))
    cars = parked.place(TILE, (fixture(), []), heights)
    assert len(cars) > 10
    for car in cars:
        fx, fz = np.sin(car["yaw"]), np.cos(car["yaw"])
        rx = -fz  # real-world right of the heading
        assert car["pitch"] == pytest.approx(np.arctan(grade * fx), abs=2e-4)  # nose up when facing uphill
        assert car["roll"] == pytest.approx(np.arctan(-grade * rx), abs=2e-4)  # leaning towards the low side
        assert car["y"] == pytest.approx(grade * (car["x"] - TILE.origin[0]), abs=1e-3)


def kerb_scene():
    """Street A runs east along z = 100 (its real-world right is the south, +z side), crossed by
    B at x = 900; both carriageways are 10 m wide with 3 m pavements."""
    from turku3d.parked.kerbs import Post

    a = shapely.LineString([(760, 100), (900, 100), (990, 100)])  # OSM ways share junction nodes
    b = shapely.LineString([(900, 10), (900, 100), (900, 190)])
    carriage = shapely.union_all([a.buffer(5, cap_style="flat"), b.buffer(5, cap_style="flat")])
    pavement = shapely.union_all(
        [a.buffer(8, cap_style="flat"), b.buffer(8, cap_style="flat")]
    ).difference(carriage)
    features = [
        Feature("osm:way:1", a, {"highway": "residential", "name": "A", "parking:right": "lane"}),
        Feature("osm:way:2", b, {"highway": "residential", "name": "B", "parking:both": "no"}),
    ]
    city = [
        Feature("city:1", carriage, {"Katuosalaji_koodi": "1"}),
        Feature("city:2", pavement, {"Katuosalaji_koodi": "2"}),
    ]
    posts = [
        Post(850, 106.5, frozenset({"C38"})),  # no parking, south kerb
        Post(800, 93.5, frozenset({"E1"})),  # crossing, north kerb
        Post(870, 93.5, frozenset({"E2"})),  # parking, north kerb, westbound traffic
    ]
    return features, city, {"posts": posts, "stops": [], "permits": []}


def test_kerb_bays_follow_the_kerb_evidence_and_statutory_gaps():
    features, city, kerb = kerb_scene()
    cars = parked.place(TILE, (features, city), np.zeros((129, 129)), kerb=kerb)
    south = [c for c in cars if c["z"] > 100]
    north = [c for c in cars if c["z"] < 100]
    assert len(south) >= 15 and len(north) >= 5
    for car in cars:
        shape = footprint(car["x"], car["z"], car["yaw"], car["width"], car["length"])
        # Inside the carriageway, 25 to 35 cm off the kerb face (bays fit a 2 m wide body), never
        # in the junction or within 5 m of it.
        assert city[0].geometry.buffer(0.01).covers(shape)
        assert 4.65 - 1e-6 <= abs(car["z"] - 100) + car["width"] / 2 <= 4.75 + 1e-6
        assert abs(car["x"] - 900) > 10
        assert car["zone"] in parked.PEAK and 0 <= car["occ"] < parked.PEAK[car["zone"]]
    for car in south:  # parking:right=lane; eastbound traffic on the south side
        assert car["source"] == "kerb:osm" and car["yaw"] == pytest.approx(np.pi / 2, abs=1e-3)
        assert abs(car["x"] - 850) > 10 - car["length"] / 2  # C38
    for car in north:  # the E2 zone runs west, with the traffic, from the post to the way's end
        assert car["source"] == "kerb:sign" and car["yaw"] == pytest.approx(-np.pi / 2, abs=1e-3)
        assert car["x"] < 870 and abs(car["x"] - 800) > 7.5 - car["length"] / 2  # E1 crossing
    reverse = parked.place(TILE, (features[::-1], city[::-1]), np.zeros((129, 129)), kerb=kerb)
    assert cars == reverse


def test_slots_give_way_to_lamps_and_furniture_by_their_footprints():
    from turku3d.coarse.furniture import ground_shape
    from turku3d.props import Prop

    heights = np.zeros((129, 129))
    free = parked.place(TILE, (fixture(), []), heights)
    lamp = Prop("lamp", "lamp", free[0]["x"], 0, free[0]["z"])  # a lot lamp inside a bay
    bench = Prop("bench", "bench", free[5]["x"], 0, free[5]["z"], yaw=0.3)
    cars = parked.place(TILE, (fixture(), []), heights, poles=[lamp, bench])
    assert 0 < len(cars) < len(free)
    for car in cars:
        shape = footprint(car["x"], car["z"], car["yaw"], car["width"], car["length"])
        assert shape.distance(shapely.Point(lamp.x, lamp.z)) >= 0.6 - 1e-6
        assert shape.distance(ground_shape(bench)) >= 0.6 - 1e-6
    # Street lights 0.35 m past the kerb face keep every kerb bay beside them.
    features, city, kerb = kerb_scene()
    zeros = np.zeros((129, 129))
    bays = parked.place(TILE, (features, city), zeros, kerb=kerb)
    lamps = [Prop(f"lamp-{x}", "lamp", x, 0, 105.35) for x in range(770, 990, 25)]
    assert parked.place(TILE, (features, city), zeros, poles=lamps, kerb=kerb) == bays
