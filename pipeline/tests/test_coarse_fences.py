"""Synthetic barrier placement: corners, road clearance, gates and collision cells."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.fences import collision, place, segments, style
from turku3d.col.materials import mat_id


def test_barrier_collision_keeps_matching_visuals_through_both_passes(tmp_path):
    from turku3d.coarse.basic import visual
    from turku3d.coarse.fences import write_instances
    from turku3d.col.format import ColTile, write_col
    from turku3d.collision_audit import visual_vertices
    from turku3d.stage_cache import StageCache
    from turku3d.wall_assembly import assemble_layers
    from turku3d.wall_surfaces import layer_meshes

    tile = C.VisualTile(0, -1, 1)
    batch = place(
        tile,
        [
            (
                shapely.LineString([(-175, 356), (-150, 340)]),
                {"barrier": "fence", "height": "2"},
                False,
            )
        ],
        [],
        lambda x, z: 4.7,
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    path = stage / "fences.vis.glb"
    write_instances(path, tile, batch)
    original = path.read_bytes()
    cells = [
        (ct, ColTile.for_tile(ct.i, ct.j, [], [], [], primitives=collision(batch, ct)))
        for ct in tile.collision_tiles()
    ]
    cache = StageCache(tmp_path / "cache")
    # Exercise the warm cache as well: typed collision and visual files must stay paired.
    for _ in range(2):
        layers = assemble_layers(tile, [path], [], [], tmp_path, stage, cache)
        assert path in layers
        assert path.read_bytes() == original
        assert not layer_meshes([path])  # A prototype is not a wall at the tile origin.
    basic = tmp_path / "basic.glb"
    basic.write_bytes(visual(tile, cells)[0])
    from turku3d.coarse.visual_parity import audit

    manifest = dict(tile=tile.id, visual={tile.id: dict(url=str(path))}, collision={})
    for ct, col in cells:
        filename = ct.id + ".col"
        (tmp_path / filename).write_bytes(write_col(col, encoding="raw", optimize=False))
        manifest["collision"][ct.id] = dict(url=filename)
    for product in (basic, path):
        manifest["visual"][tile.id]["url"] = str(product)
        assert not audit(tmp_path, manifest)["unmatched"]
    # Removing the placed layer reproduces the published failure and must be red.
    from turku3d.package.gltf import build_glb

    missing = tmp_path / "missing.glb"
    missing.write_bytes(build_glb([]))
    manifest["visual"][tile.id]["url"] = str(missing)
    assert len(audit(tmp_path, manifest)["unmatched"]) == len(batch)
    from scipy.spatial import cKDTree

    for product in (basic, path):
        vertices, _ = visual_vertices(product)
        tree = cKDTree(vertices)
        for ct, col in cells:
            for primitive in col.primitives:
                # Both sides of both end posts must appear at the actual collider pose.
                corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
                points = corners * primitive.params[:3]
                q = np.array(primitive.rotation)
                cross = np.cross(q[:3], points)
                points += 2 * (q[3] * cross + np.cross(q[:3], cross))
                points += np.array(primitive.center) + ct.origin
                assert tree.query(points)[0].max() < 0.002


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


def test_tunnel_mouth_excludes_walls_panels_and_explicit_road_barriers():
    tile = C.VisualTile(0, 0, 0)
    tunnel = (
        "road",
        shapely.LineString([(20, 20), (80, 20)]),
        {"highway": "secondary", "tunnel": "yes", "lanes": "3"},
    )
    records = [
        (shapely.LineString([(18, 5), (18, 35)]), {"barrier": "fence"}, True),
        (
            shapely.LineString([(20, 5), (20, 35)]),
            {"barrier": "retaining_wall", "height": "4"},
            False,
        ),
        (shapely.Point(20, 20), {"barrier": "bollard"}, True),
        (shapely.LineString([(50, 15), (50, 25)]), {"barrier": "fence"}, False),
    ]
    batch = place(tile, records, [], lambda x, z: 0, tunnels=[tunnel])
    assert any(np.isclose(s.x, 50) for s in batch)  # Keep barriers on overburden inland.
    assert batch  # Preserve barriers outside the tunnel mouth.
    opening = shapely.box(16, 15, 24, 25)
    for s in batch:
        half = np.array([np.cos(s.yaw), -np.sin(s.yaw)]) * s.length / 2
        axis = shapely.LineString([np.array([s.x, s.z]) - half, np.array([s.x, s.z]) + half])
        assert not axis.buffer(0.15).intersects(opening)
    assert len([p for ct in tile.collision_tiles() for p in collision(batch, ct)]) == len(batch)
