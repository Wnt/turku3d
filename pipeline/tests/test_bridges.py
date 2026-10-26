import json
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, box
from turku3d.bridges.build import Crossing, crossings, geometry, profile
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
    result = crossings(city, osm)
    assert len(result) == 1
    assert result[0].footprint.equals(city.geometry_local.iloc[0])
    assert result[0].road


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
        assert np.allclose(deck.positions[matches, 1], height, atol=1e-9)
    waterline = np.isclose(deck.positions[:, 0], 10)
    assert waterline.any()
    assert np.allclose(deck.positions[waterline, 1], 3.0, atol=1e-9)
