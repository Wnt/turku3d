"""Walking junctions must remain open in both stair railing visuals and collision."""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import stair_rails, stairs
from turku3d.col.materials import mat_id


def test_joined_path_width_clips_visible_and_solid_rails_and_keeps_the_flight():
    line = shapely.LineString([(20, 10), (20, 20)])
    tags = {"handrail": "yes", "_profile": {
        "stations": [0, 1, 8, 10], "heights": [1, 1, 4, 4],
    }}
    rows = [("flight", line, tags)]
    path = shapely.LineString([(0, 10), (40, 10)])
    stair_rails.attach(rows, [(path, {"highway": "footway", "width": "3.5"})])
    meshes, _ = stairs.produce(C.VisualTile(0, 0, 0), (rows, []), lambda x, z: 1)
    rails = [m for m in meshes if m.material == "railing"]
    assert rails and all(m.visual and m.metadata["collision"] for m in rails)
    for mesh in rails:
        assert not shapely.intersects_xy(path.buffer(1.75), mesh.positions[:, 0], mesh.positions[:, 2]).any()
        assert mesh.positions[:, 2].min() > 11.75
        assert mesh.positions[:, 2].max() <= 18.03  # no rails across the upper flat landing
        assert mesh.positions[:, 1].max() > 5  # rising handrails are retained
    pos, ix, material = stairs.collision(
        C.CollisionTile(0, 0), meshes,
        np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8),
    )
    solid = pos[ix[material == mat_id("railing")]]
    assert len(solid) == sum(len(m.indices) for m in rails)
    assert solid[:, :, 2].min() > 11.75
    assert solid[:, :, 2].max() <= 18.03


def test_clearance_ignores_steps_unconnected_paths_and_overhead_walkways():
    line = shapely.LineString([(20, 10), (20, 20)])
    crossing = shapely.LineString([(0, 10), (40, 10)])
    rows = [("flight", line, {})]
    stair_rails.attach(rows, [
        (crossing, {"bridge": "yes"}),
        (crossing, {"layer": "1"}),
        (crossing, {"step_count": "10"}),
        (shapely.LineString([(0, 15), (40, 15)]), {}),
    ])
    assert "_rail_clearance" not in rows[0][2]
    # Area paths have real outlines, rather than an invented centreline width.
    area = shapely.box(18, 8, 22, 11)
    stair_rails.attach(rows, [(area, {"highway": "pedestrian"})])
    assert rows[0][2]["_rail_clearance"].covers(area)


def test_wide_entrance_flight_width_and_axis_come_from_lidar_between_side_walls():
    import numpy as np
    from turku3d.coarse.stair_survey import flight

    # A 9 m wide flight rising 0.75 m in x 0-1.6 between walls at z = 0 and z = 9.
    x, z = np.meshgrid(np.arange(-4, 6, 0.2), np.arange(-6, 15, 0.2))
    x, z = x.ravel(), z.ravel()
    h = np.where(z < 0, 8.0, np.where(z > 9, 5.0 + 0.02 * x, 5.0 + np.clip(x, 0, 1.6) / 1.6 * 0.75))
    points = np.c_[x, h, z]
    walls = shapely.MultiLineString([[(-4, 0), (6, 0)], [(-4, 9), (6, 9)]])
    # Mapped as a short way 2 m from one wall, 15 degrees off the true ascent.
    skew = np.radians(15)
    a = np.array([0.0, 2.0]); b = a + 1.6 * np.array([np.cos(skew), np.sin(skew)])
    line, width = flight(shapely.LineString([a, b]), points, walls)
    direction = np.diff(np.asarray(line.coords), axis=0)[0] / line.length
    assert abs(width - 9) <= 0.5 and abs(direction[1]) < 0.05
    assert abs(np.asarray(line.centroid.coords)[0][1] - 4.5) <= 0.5
    # Without side structures at both measured ends the width stays mapped.
    assert flight(shapely.LineString([a, b]), points, shapely.LineString([(-4, 0), (6, 0)])) is None
