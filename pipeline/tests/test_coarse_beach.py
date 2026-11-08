"""Sand beaches: sand to the waterline, no invented trees, the measured crowns kept (Kansanpuisto, t0_-18_10)."""
import colorsys
from dataclasses import replace

import numpy as np
import producer_world as world
import shapely
from turku3d import config as C
from turku3d.coarse import garden_finishes, ground_finishes, surfaces
from turku3d.coarse.forests import produce, read_areas
from turku3d.coarse.inputs import Inputs
from turku3d.coarse.street_materials import FINISH_KEYS, SAND

TILE = C.VisualTile(0, 0, 0)
BEACH = shapely.box(40, 40, 160, 120)
WATER = shapely.box(0, 128, 250, 250)  # the sea, 8 m past the beach's outline


def test_a_park_beach_is_sand_to_the_water_and_invents_no_trees(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path))
    osm = world.write_osm([
        ({"leisure": "park"}, shapely.box(0, 0, 250, 128)),
        ({"natural": "beach", "name": "uimaranta"}, BEACH),  # no surface tag: sand
        ({"natural": "beach", "surface": "pebblestone"}, shapely.box(200, 20, 240, 60)),
        ({"leisure": "playground", "surface": "sand"}, shapely.box(10, 10, 20, 20)),
    ])
    regions = garden_finishes.osm_regions(TILE, osm)
    sand = shapely.union_all([g for g, f in regions if f == SAND])
    assert sand.covers(BEACH) and sand.covers(shapely.box(10, 10, 20, 20))
    assert not sand.intersects(shapely.box(201, 21, 239, 59))  # a pebble beach keeps its own surface
    path = shapely.box(90, 118, 93, 128)
    shore = garden_finishes.shore_regions(regions, [WATER], path)
    strip = shore[0][0]
    assert strip.covers(shapely.Point(100, 124)) and strip.covers(shapely.Point(42, 126))
    assert strip.intersection(path).area < 1e-6 and strip.intersection(WATER).area < 1e-6
    assert not strip.covers(shapely.Point(100, 20)) and not strip.covers(shapely.Point(15, 125))  # inland, or past its end
    assert garden_finishes.shore_regions(regions, [], None) == []

    areas = read_areas(TILE, osm)
    assert {kind for _, _, kind, _ in areas} == {"park", "grounds", "open"}
    empty = Inputs(np.empty((0, 3)), np.empty((0, 0)), np.empty((0, 0, 3)), [], [], [], None, {}, {})
    canopy = np.array([[60.0, 14.0, 60.0], [20.0, 14.0, 60.0]])  # a crown on the beach, one in the park
    trees, _ = produce(TILE, replace(empty, forests=areas, canopy=canopy), lambda x, z: 0, [])
    assert trees and any(not BEACH.buffer(3).covers(shapely.Point(t.x, t.z)) for t in trees)
    on_beach = [t for t in trees if BEACH.covers(shapely.Point(t.x, t.z))]
    assert on_beach == []
    # A measured lidar crown on the beach (vegetation_canopy) is a real tree and stays.
    cells = np.arange(-2.5, 2.51, 0.5)
    crowns = np.array([[100 + dx, 12 - 0.4 * (dx * dx + dz * dz), 80 + dz] for dx in cells for dz in cells
                       if dx * dx + dz * dz <= 6.25])  # one dome-shaped crown on the 0.5 m lidar cells
    measured, _ = produce(TILE, replace(empty, forests=areas, vegetation_canopy=crowns), lambda x, z: 0.0, [])
    assert any(t.measured and BEACH.covers(shapely.Point(t.x, t.z)) for t in measured)


def test_sand_is_a_procedural_finish_with_the_panoramas_tone():
    assert FINISH_KEYS[SAND] == "sand"
    albedo = ground_finishes.maps("sand", 256)["albedo"].reshape(-1, 3).mean(axis=0) / 255
    hue, light, saturation = colorsys.rgb_to_hls(*albedo)
    # Kansanpuisto beach panorama: hue 31-34 deg, saturation 0.09-0.12, sunlit lightness 0.44-0.5.
    assert 25 < hue * 360 < 40 and .07 < saturation < .16 and .38 < light < .52
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float)
    mesh = surfaces.visuals(TILE, p, np.array([[0, 2, 1]]), np.array([SAND]), np.array([True]))[0]
    assert mesh.extras["material_key"] == "draft_sand"
