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
