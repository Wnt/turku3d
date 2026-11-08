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


def test_mapped_retaining_wall_takes_its_measured_step_and_keeps_unmeasured_parts():
    import shapely
    from turku3d import config as C
    from turku3d.coarse import fence_survey
    from turku3d.lidar import PointCloud
    from turku3d.lidar.cache import POINT_DTYPE

    # A 1.5 m street wall at a hill foot: the upper ground keeps rising behind it.
    x, z = np.meshgrid(np.arange(2, 30, 0.2), np.arange(-4, 4.01, 0.2))
    x, z = x.ravel(), z.ravel()
    h = np.where(z > 0, 1.5 + 0.7 * z, 0.0) + np.where(x > 14, np.nan, 0)
    keep = np.isfinite(h)  # no returns along the far part of the wall
    records = np.zeros(int(keep.sum()), POINT_DTYPE)
    records["xyz"] = np.c_[x[keep], h[keep], z[keep]]
    records["classification"] = 2
    records["return_number"] = records["number_of_returns"] = 1

    class Store:
        def tile(self, t):
            return PointCloud(records)

    tile = C.VisualTile(0, 0, 0)
    available = {b.name for b in C.laz_tiles_for_bbox(tile.bbox.buffered(30))}
    line = shapely.LineString([(4, 0), (28, 0)])
    out = fence_survey.retaining_steps(line, {"barrier": "retaining_wall"}, False, Store(),
                                       available, shapely.GeometryCollection())
    surveyed = [(g, t) for g, t, _ in out if "_survey" in t]
    assert surveyed
    for g, t in surveyed:
        levels = np.asarray(t["_survey"]["heights"])
        np.testing.assert_allclose(np.abs(np.diff(levels, axis=1)), 1.5, atol=0.15)
    # The unmeasured far part keeps the mapped line for the fallback emitter.
    rest = [g for g, t, _ in out if "_survey" not in t]
    assert rest and max(g.bounds[2] for g in rest) == 28
    # A short unmeasured end (no ground returns beside a building) keeps the measured step.
    short = shapely.LineString([(4, 0), (16, 0)])
    out = fence_survey.retaining_steps(short, {"barrier": "retaining_wall"}, False, Store(),
                                       available, shapely.GeometryCollection())
    assert all("_survey" in t for _, t, _ in out)
    end = [t for g, t, _ in out if g.bounds[2] == 16]
    assert end
    np.testing.assert_allclose(np.abs(np.diff(end[0]["_survey"]["heights"], axis=1)), 1.5,
                               atol=0.15)


def test_a_fence_outlining_a_shrub_or_flower_bed_is_low_unless_tagged():
    from turku3d.coarse.fences import BED_FENCE, DEFAULTS, height

    assert height({"barrier": "fence"}) == DEFAULTS["fence"]
    for bed in ({"natural": "scrub"}, {"natural": "heath"}, {"landuse": "flowerbed"}):
        assert height({"barrier": "fence", **bed}) == BED_FENCE
    assert height({"barrier": "fence", "natural": "scrub", "height": "2"}) == 2.0
    assert height({"barrier": "wall", "natural": "scrub"}) == DEFAULTS["wall"]


def test_a_guard_rail_on_the_carriageway_edge_stays_and_is_cut_only_well_inside_it():
    # Etelatuulentie (Espoo): the surveyed carriageway reaches to within 0.2-0.7 m of the OSM guard rail along its
    # embankment edge; the fence clearance removed the rail there and cars tipped over the edge.
    tile = C.VisualTile(0, 0, 0)
    road = shapely.box(40, 0, 60, 100)
    edge = shapely.LineString([(39.8, 10), (39.8, 90)])  # 0.2 m outside the carriageway
    rails = place(tile, [(edge, {"barrier": "guard_rail"}, False)], [(road, 0)], lambda x, z: 0)
    assert np.isclose(sum(s.length for s in rails), 80)
    assert all(s.kind == "rail" for s in rails)
    fence = place(tile, [(edge, {"barrier": "fence"}, False)], [(road, 0)], lambda x, z: 0)
    assert not fence  # other barriers keep their clearance
    across = shapely.LineString([(30, 50), (70, 50)])  # a rail drawn across the carriageway is cut inside it
    cut = place(tile, [(across, {"barrier": "guard_rail"}, False)], [(road, 0)], lambda x, z: 0)
    assert np.isclose(sum(s.length for s in cut), 21)
    assert not any(40.5 < s.x < 59.5 for s in cut)
