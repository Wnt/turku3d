"""Sports venue class: grandstand detection and stand geometry, track lanes, pitch stripes and lines."""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import street_materials as SM
from turku3d.coarse import venues as V
from turku3d.coarse.inputs import Building

TILE = C.VisualTile.parse("t0_0_0")


def stand_building(key="B", use="2550"):
    footprint = shapely.geometry.polygon.orient(shapely.box(20, 40, 110, 56), 1.0)
    b = Building(key, footprint, 10.0, 29.0, "facade_plaster")
    b.attributes = dict(use=use)
    b.roof_parts = [(footprint, np.array([0.0, 0.0, 29.0]))]
    return b


def venue(**kw):
    track = shapely.Polygon(shapely.box(30, 60, 150, 140).buffer(10).exterior,
                            [shapely.box(30, 60, 150, 140).exterior])
    pitch = shapely.box(40, 70, 140, 130)
    v = V.Venues(tracks=[(track, {"leisure": "track", "lanes": "8"})],
                 pitches=[(pitch, {"leisure": "pitch", "sport": "soccer", "surface": "grass"})],
                 fields=[track, pitch], **kw)
    return v


def test_register_sports_building_facing_a_field_is_a_grandstand_not_a_hall():
    v = venue()
    hall = Building("H", shapely.geometry.polygon.orient(shapely.box(160, 160, 200, 190), 1.0), 10, 20, "x")
    hall.attributes = dict(use="2550")
    flat = stand_building("F", use="1010")
    assert V.grandstands([stand_building(), hall, flat], v) == {"B": "register"}
    osm = V.Venues(stands=[(shapely.box(20, 40, 110, 56), {"building": "grandstand"})])
    assert V.grandstands([flat], osm) == {"F": "osm"}


def test_grandstand_rows_face_the_field_under_a_roof():
    v = venue()
    meshes = V.grandstand(stand_building(), v.fields, lambda x, z: 10.0)
    by = {m.object_id.split(":", 2)[2]: m for m in meshes}
    seats, aisles, roof = by["seat:stair_slope"], by["paint:stair_slope"], by["roof:roof"]
    # Rows rise away from the field (z = 56 front, z = 40 back) and stay under the roof slab.
    treads = seats.positions[np.abs(seats.positions[:, 1] - seats.positions[:, 1].max()) < 1e-6]
    assert treads[:, 2].max() < 50 and seats.positions[:, 1].min() > 10 + 1.0
    assert roof.positions[:, 1].min() > seats.positions[:, 1].max() + 2.0
    assert len(aisles.indices) and all(m.metadata["collision"] == (m.material != "street_furniture")
                                       for m in meshes)
    # Single-sided faces: every tread faces up.
    tri = seats.positions[seats.indices]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    flat = np.abs(normal[:, 1]) > 1e-9
    assert (normal[flat, 1] > 0).all()


def test_track_lanes_and_pitch_stripes():
    v = venue()
    lines = V.line_polygons(TILE, v)
    regions = V.ground_regions(TILE, v)
    ids = [m for _, m in regions]
    assert SM.TRACK in ids and SM.PITCH_LIGHT in ids and SM.PITCH_DARK in ids
    stripes = [g for g, m in regions if m in (SM.PITCH_LIGHT, SM.PITCH_DARK)]
    assert 16 <= len(stripes) <= 20 and abs(sum(g.area for g in stripes) - 6000) < 1
    # 9 lane lines around the oval plus the football markings.
    assert sum(g.area for g in lines) > 9 * 0.05 * 400
    meshes, count = V.goals(TILE, v, lambda x, z: 0.0)
    assert count == 2 and meshes
