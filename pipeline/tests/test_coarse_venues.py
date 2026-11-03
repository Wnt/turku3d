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
    marks = V.line_marks(TILE, v)
    regions = V.ground_regions(TILE, v)
    ids = [m for _, m in regions]
    assert SM.TRACK in ids and SM.PITCH_LIGHT in ids and SM.PITCH_DARK in ids
    stripes = [g for g, m in regions if m in (SM.PITCH_LIGHT, SM.PITCH_DARK)]
    assert 16 <= len(stripes) <= 20 and abs(sum(g.area for g in stripes) - 6000) < 1
    # 9 lane lines around the oval plus the football markings.
    lanes = [g for g, w in marks if w == V.LINE]
    assert len(lanes) == 9 and sum(g.length for g in lanes) > 9 * 400
    # One merged paint mesh of strips (2 triangles per <= 2 m) draped over a 1 m-high plane.
    xs, zs = np.meshgrid(np.linspace(0, 250, 65), np.linspace(0, 250, 65))
    ground = np.c_[xs.ravel(), np.full(xs.size, 1.0), zs.ravel()]
    q = (np.arange(64)[:, None] * 65 + np.arange(64)[None]).ravel()
    tris = np.r_[np.c_[q, q + 65, q + 1], np.c_[q + 1, q + 65, q + 66]].astype(np.uint32)
    mesh = V.paint(TILE, ground, tris, marks)
    length = sum(g.length for g, _ in marks if g.geom_type == "LineString")
    assert len(mesh.indices) < 1.4 * length
    y = mesh.positions[:, 1]
    assert np.allclose(y, 1.0 + V.PAINT_LIFT)
    tri = mesh.positions[mesh.indices]
    assert (np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 1] > 0).all()
    meshes, count = V.goals(TILE, v, lambda x, z: 0.0)
    assert count == 2 and meshes


def test_stand_roof_is_a_low_canopy_from_covering_parts_only():
    """Small LoD2 facets (none covering a fifth of the stand) never extrapolate into a wedge."""
    v = venue()
    b = stand_building()
    strips = [shapely.box(x, 40, x + 15, 56) for x in range(20, 110, 15)]
    planes = [np.array([0.0, 0.0, 29.0])] * len(strips)
    planes[0] = np.array([1.0, 0.0, 9.0])  # a 45-degree end facet: 29 m to 44 m across its strip
    b.roof_parts = list(zip(strips, planes))
    roof = {m.object_id.split(":", 2)[2]: m for m in V.grandstand(b, v.fields, lambda x, z: 10.0)}["roof:roof"]
    seats = {m.object_id.split(":", 2)[2]: m for m in V.grandstand(b, v.fields, lambda x, z: 10.0)}["seat:stair_slope"]
    assert roof.positions[:, 1].max() <= 29.0 + 0.5
    assert roof.positions[:, 1].min() > seats.positions[:, 1].max() + 2.0

    steep = stand_building()
    steep.roof_parts = [(steep.footprint, np.array([0.0, -1.0, 29.0 + 56.0]))]  # 45 degrees across
    roof = {m.object_id.split(":", 2)[2]: m for m in V.grandstand(steep, v.fields, lambda x, z: 10.0)}["roof:roof"]
    top = roof.positions[:, 1]
    assert top.max() - top.min() <= V.ROOF_PITCH * 16.5 + V.ROOF_THICK + 0.05
