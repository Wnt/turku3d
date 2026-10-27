"""Synthetic barrier placement: corners, road clearance, gates and collision cells."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.fences import collision, place, segments, style
from turku3d.col.materials import mat_id


def test_segments_split_corners_cells_and_ground_height():
    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(60, 20), (66, 20), (66, 25)])
    batch = place(
        tile, [(line, {"barrier": "fence", "height": "2 m"}, False)], [], lambda x, z: x / 10
    )
    assert batch
    assert sum(s.length for s in batch) == 11
    assert all(s.length <= 2.5 for s in batch)
    for s in batch:
        assert s.height >= 2
        assert np.isclose(s.y, (s.x - abs(np.cos(s.yaw)) * s.length / 2) / 10)
        half_x = abs(np.cos(s.yaw)) * s.length / 2
        assert not s.x - half_x < 62.5 < s.x + half_x
    cols = [p for ct in tile.collision_tiles() for p in collision(batch, ct)]
    assert len(cols) == len(batch)
    assert all(p.material == mat_id("fence") for p in cols)


def test_road_clipping_preserves_touching_and_deliberate_barriers():
    window = shapely.box(0, 0, 100, 100)
    road = shapely.box(40, 0, 60, 100)
    empty = shapely.GeometryCollection()
    line = shapely.LineString([(10, 30), (90, 30)])
    pieces = list(segments(line, road, empty, window))
    assert pieces
    for p, q in pieces:
        assert not shapely.LineString([p, q]).intersects(road)
    assert np.isclose(sum(np.linalg.norm(q - p) for p, q in pieces), 59.3)
    explicit = list(segments(line, road, empty, window, on_road=True))
    assert np.isclose(sum(np.linalg.norm(q - p) for p, q in explicit), 80)
    tangent = shapely.LineString([(10, 20), (40, 20), (10, 50)])
    assert list(segments(tangent, road, empty, window))


def test_gate_opening_and_area_outline_and_bollard_posts():
    tile = C.VisualTile(0, 0, 0)
    outline = shapely.box(10, 10, 30, 30).boundary
    gate = shapely.Point(20, 10)
    batch = place(
        tile,
        [(outline, {"barrier": "hedge"}, False), (gate, {"barrier": "gate"}, True)],
        [],
        lambda x, z: 5,
    )
    assert all(s.kind == "hedge" for s in batch)
    assert np.isclose(sum(s.length for s in batch), 77)
    assert all(np.isclose(s.height, 1.4) for s in batch)
    posts = place(
        tile,
        [(shapely.LineString([(10, 50), (20, 50)]), {"barrier": "bollard"}, True)],
        [],
        lambda x, z: 0,
    )
    assert len(posts) == 5
    assert all(s.length == 0.22 for s in posts)
    assert style({"barrier": "fence", "material": "wood"}) == "wood"
    assert style({"barrier": "wall", "material": "brick"}) == "brick"
