import json
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, box
from turku3d.bridges.build import Crossing, _triangles, crossings, geometry, profile
from turku3d.bridges.shore import Shore, approaches
from turku3d.col.materials import mat_id


def test_city_footprint_and_osm_bridge_semantics():
    city = gpd.GeoDataFrame(
        {"Katuosalaji": ["Silta"], "Kadunnimi": ["Auransilta"], "feature_key": ["city-1"]},
        geometry=[box(296, 220, 304, 260)],
    ).rename_geometry("geometry_local")
    axis = LineString([(300, 215), (300, 265)])
    osm = pd.DataFrame(
        [
            SimpleNamespace(
                osm_id=1,
                tags=json.dumps({"bridge": "yes", "highway": "secondary", "name": "Auransilta"}),
                geometry_local=axis,
            ).__dict__
        ]
    )
    from turku3d import config as C

    result = crossings(city, osm, C.BBoxLocal(0, 0, 500, 500))
    assert len(result) == 1
    assert crossings(city, osm, C.BBoxLocal(400, 0, 500, 100)) == []
    assert result[0].footprint.equals(city.geometry_local.iloc[0])
    assert result[0].road


def test_concave_deck_with_hole_has_complete_triangles():
    polygon = box(0, 0, 10, 10).difference(box(3, 0, 7, 7)).difference(box(1, 8, 2, 9))
    faces = np.asarray(list(_triangles(polygon)))
    triangles = shapely.polygons(faces)
    assert np.isclose(shapely.area(triangles).sum(), polygon.area, atol=1e-8)
    assert np.all(shapely.covers(polygon, shapely.point_on_surface(triangles)))
    assert shapely.covers(triangles, shapely.Point(8, 5)).any()


def test_deck_profile_prefers_class17_and_generates_walk_drive_surfaces():
    crossing = Crossing(
        "Auransilta", box(295, 220, 305, 260), LineString([(300, 220), (300, 260)]), True, ["osm:1"]
    )
    samples = np.array(
        [[x, 6 + 0.002 * (z - 220), z] for z in np.arange(220, 261) for x in np.arange(297, 304)],
        float,
    )
    points = np.vstack((samples, samples + [0, 2, 0], [[300, 1, 240]]))
    classes = np.r_[np.full(len(samples), 17), np.full(len(samples), 12), [9]]
    stations, heights, source = profile(crossing, points, classes)
    assert source == 17
    assert len(stations) > 3
    assert 5.9 < heights.min() < heights.max() < 6.2
    meshes, report = geometry(crossing, points, classes)
    assert report["deck_source_class"] == 17
    assert {m.metadata.get("material_key") for m in meshes if m.material == "bridge_deck"} == {
        "bridge_asphalt",
        "bridge_stone",
    }
    assert any(m.object_id.endswith(":road") for m in meshes)
    assert any(m.object_id.endswith(":sidewalk") for m in meshes)
    assert any(m.material == "curb" for m in meshes)
    assert any(m.material == "railing" for m in meshes)
    assert any(":pier:" in m.object_id for m in meshes)
    assert all(mat_id(m.material) != 0 for m in meshes)


def test_short_bridge_interpolates_between_ground_abutments():
    crossing = Crossing("path", box(10, 10, 12, 14), LineString([(11, 10), (11, 14)]), False, [])
    points = np.array(
        [
            [x, 3 + (z - 10) * 0.025, z]
            for z in np.linspace(10, 14, 9)
            for x in np.linspace(9.5, 12.5, 7)
        ]
    )
    _, heights, source = profile(crossing, points, np.full(len(points), 2))
    assert source == "abutments"
    assert abs(heights[0] - 3) < 0.05
    assert abs(heights[-1] - 3.1) < 0.05


def test_approach_matches_full_width_shore_contact_and_waterline():
    crossing = Crossing("footbridge", box(0, -1, 20, 1), LineString([(0, 0), (20, 0)]), False, [])
    contact = Shore("start", 10, 0, np.array([-1, 0, 1]), np.array([2.5, 2.55, 2.5]), 0.02, 0, None)
    survey = SimpleNamespace(water=box(10, -10, 20, 10), support=lambda xz, reference: (2.5, 0.0))
    meshes = approaches(crossing, [contact], survey, lambda xz: 3.0)
    deck = next(mesh for mesh in meshes if mesh.material == "bridge_deck")
    for offset, height in zip(contact.offsets, contact.heights, strict=True):
        matches = np.all(np.isclose(deck.positions[:, [0, 2]], [0, offset]), axis=1)
        assert matches.any()
        assert np.allclose(deck.positions[matches, 1], height + 0.02, atol=1e-9)
    waterline = np.isclose(deck.positions[:, 0], 10)
    assert waterline.any()
    assert np.allclose(deck.positions[waterline, 1], 3.0, atol=1e-9)


def test_bridges_are_built_per_tile_from_osm_and_lidar(tmp_path, monkeypatch):
    """An unnamed footbridge over a river in t0_0_0: deck from class-12 lidar, shores on ground."""
    import producer_world as world
    from turku3d import config as C
    from turku3d import tile_layer
    from turku3d.bridges.layer import PRODUCER
    from turku3d.layers import layer_root, read_collision, read_manifest

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_JOBS", "1")
    river = box(0, 100, 250, 140)
    axis = LineString([(125, 92), (125, 148)])
    world.city()
    world.ground(["t0_0_0", "t0_1_0"])
    world.write_osm(
        [
            ({"natural": "water", "water": "river"}, river),
            ({"highway": "footway", "bridge": "yes", "width": "3"}, axis),
        ]
    )
    xs, zs = np.meshgrid(np.arange(80.25, 170, 0.5), np.arange(60.25, 180, 0.5))
    xz = np.c_[xs.ravel(), zs.ravel()]
    wet = shapely.contains_xy(river, xz[:, 0], xz[:, 1])
    deck = shapely.contains_xy(axis.buffer(1.5, cap_style="flat"), xz[:, 0], xz[:, 1])
    points = np.r_[
        np.c_[xz[:, 0], np.where(wet, 0.2, world.GROUND_Y), xz[:, 1]],
        np.c_[xz[deck, 0], np.full(deck.sum(), 4.0), xz[deck, 1]],
    ]
    classes = np.r_[np.where(wet, 9, 2), np.full(deck.sum(), 12)]
    world.lidar(points, classes, C.BBoxLocal(-30, -30, 280, 280))
    out = layer_root() / "bridges"
    tile_layer.run(out, PRODUCER, ["t0_0_0", "t0_1_0"])
    manifest = read_manifest(out)
    assert set(manifest["tiles"]) == {"t0_0_0.vis.glb", "c_2_1.col.npz"}
    assert manifest["params"]["crossings"] == 1 and manifest["params"]["skipped"] == 0
    collision = read_collision(out / "c_2_1.col.npz")
    decks = collision.material == mat_id("bridge_deck")
    heights = collision.positions[collision.indices[decks]][:, :, 1]
    over_water = shapely.contains_xy(
        river, *collision.positions[collision.indices[decks]].mean(axis=1)[:, [0, 2]].T
    )
    assert np.allclose(heights[over_water], 4.0, atol=0.05)
    assert heights.min() >= world.GROUND_Y  # the approach ramps meet the banks
    assert (collision.material == mat_id("railing")).any()
    assert tile_layer.stale(out, PRODUCER, ["t0_0_0", "t0_1_0"]) == []
