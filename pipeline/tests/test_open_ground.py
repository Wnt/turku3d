"""OSM raised parts open the ground floor: cut walls, pilotis, soffit, closing wall, collision."""

import numpy as np
import pytest
import shapely
from turku3d.coarse.inputs import Building
from turku3d.coarse.register import lower_edge, open_ground
from turku3d.facade_component_geometry import geometry
from turku3d.open_ground import edge_plans, interior_quads


def building():
    footprint = shapely.geometry.polygon.orient(shapely.box(0, 0, 20, 10), 1.0)
    b = Building("b1", footprint, 0.0, 12.0, "facade_plaster")
    b.open_ground = [(shapely.box(0, 0, 10, 10), 3.0)]
    ring = np.asarray(footprint.exterior.coords)
    walls = np.concatenate(
        [[[p[0], 0, p[1]], [q[0], 0, q[1]], [q[0], 12, q[1]], [p[0], 12, p[1]]] for p, q in zip(ring[:-1], ring[1:])]
    ).astype(float)
    return b, walls


def test_osm_lower_edges_and_ground_parts():
    assert lower_edge({"min_height": "4.5"}) == 4.5
    assert lower_edge({"building:min_level": "2"}) == 6.0
    assert lower_edge({"building:min_level": "0"}) is None and lower_edge({"height": "9"}) is None
    raised = [(shapely.box(0, 0, 10, 10), 3.0), (shapely.box(0, 0, 10, 20), 6.0)]
    pieces = open_ground(raised, [shapely.box(0, 15, 10, 20)])
    # The lowest raised edge owns overlapping area; a ground part closes its own area.
    assert [round(p.area) for p, _ in pieces] == [100, 50] and [h for _, h in pieces] == [3.0, 6.0]


def test_open_edge_cuts_wall_and_adds_pilotis_soffit_and_closing_wall():
    b, walls = building()
    quads = walls.reshape(-1, 4, 3)
    long_face = next(q for q in quads if abs(q[0, 2]) < 1e-9 and abs(q[1, 2]) < 1e-9)
    plans = edge_plans(long_face, b)
    assert len(plans) == 1 and plans[0]["through"]
    left, bottom, right, top = plans[0]["metric"]
    span = sorted([left, right])
    assert span[1] - span[0] == pytest.approx(10, abs=0.3) and top == pytest.approx(3.0)
    meshes, boxes = geometry(long_face, plans, "b1", np.zeros(3))
    assert sum(m.extras["material_key"] == "component_column" for m in meshes) == len(boxes) >= 2
    inner = interior_quads(b, walls)
    soffits = [q for q in inner if np.ptp(q[:, 1]) < 1e-9]
    closing = [q for q in inner if np.ptp(q[:, 1]) > 1]
    assert sum(shapely.Polygon(q[:, [0, 2]]).area for q in soffits) == pytest.approx(100)
    for q in soffits:
        assert np.cross(q[3] - q[0], q[2] - q[0])[1] < 0  # faces down
    assert len(closing) == 1 and np.allclose(closing[0][:, 0], 10)
    assert np.cross(closing[0][3] - closing[0][0], closing[0][2] - closing[0][0])[0] < 0  # faces the open side


def test_collision_opens_the_edge_and_closes_the_soffit():
    from turku3d.facade_component_geometry import collision_walls

    b, walls = building()
    b.facade_key = "no-program-test-building"
    positions, indices = collision_walls(b, walls)
    tris = positions[indices]
    centres = tris.mean(axis=1)
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    vertical = np.abs(normals[:, 1]) < 1e-9
    open_front = vertical & (np.abs(centres[:, 2]) < 0.01) & (centres[:, 0] > 0.5) & (centres[:, 0] < 9.5) & (centres[:, 1] < 2.8)
    closed_front = vertical & (np.abs(centres[:, 2]) < 0.01) & (centres[:, 0] > 10.5)
    assert not open_front.any() and closed_front.any()
    assert (~vertical & np.isclose(centres[:, 1], 3.0)).any()  # soffit ceiling


def test_soffit_follows_local_terrain_not_the_lowest_building_base():
    from turku3d.coarse.register import open_ground_levels
    from turku3d.open_ground import pieces

    b, _ = building()
    points = np.array([[x, 2.0, z] for x in (1, 5, 9, 11) for z in (-1, 5, 11)], float)
    open_ground_levels(b, points)
    assert pieces(b)[0][1] == pytest.approx(5.0)  # terrain 2.0 + lower edge 3.0


def test_raised_part_above_a_lower_shell_or_without_volume_above_stays_closed():
    b, walls = building()
    low = walls.copy()
    low[low[:, 1] > 0, 1] = 2.8  # LoD2 shell lower than the mapped soffit
    assert not any(edge_plans(q, b) for q in low.reshape(-1, 4, 3))
    assert interior_quads(b, low) == []
    b.top = 4.0  # only 1 m would remain above the soffit
    assert interior_quads(b, walls) == []


def test_shallow_overhang_strip_is_a_covered_recess_not_an_open_ground_floor():
    b, walls = building()
    b.open_ground = [(shapely.box(0, 0, 20, 1.9), 3.0)]  # cantilever 1.9 m deep along the front
    assert interior_quads(b, walls) == []
    b.open_ground = [(shapely.box(0, 0, 6, 6), 3.0)]  # covered corner recess, two adjacent sides
    assert interior_quads(b, walls) == []
    # A cantilevered wing open on its long face and both ends: seen through only lengthwise.
    wing = shapely.geometry.polygon.orient(shapely.Polygon([(0, 0), (20, 0), (20, 10), (14, 10), (14, 13), (4, 13), (4, 10), (0, 10)]), 1.0)
    b.footprint = wing
    b.open_ground = [(shapely.box(4, 10, 14, 13), 3.0)]
    assert interior_quads(b, walls) == []
    assert not any(edge_plans(q, b) for q in walls.reshape(-1, 4, 3))
