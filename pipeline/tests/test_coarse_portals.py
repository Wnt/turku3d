"""Real failure modes: overburden floor samples, open cut humps and grey terrain ears."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import portals, surfaces, tunnels
from turku3d.coarse.build import grid_mesh
from turku3d.coarse.extrusion import extrude


def buried():
    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(20, 30), (60, 30)])

    def source(x, z):
        return 12 if 20 < x <= 60 else 2 + 0.02 * x

    def rendered(x, z):
        return 12 if 18 <= x <= 62 else 2 + 0.02 * x

    axis = np.linspace(0, 250, 129)
    heights = np.array([[rendered(x, z) for x in axis] for z in axis])
    p, ix = grid_mesh(tile, heights)
    result = tunnels.produce(
        tile,
        [("buried", line, dict(highway="residential", tunnel="yes", width="8"))],
        p,
        ix,
        [],
        extrude,
        source,
        rendered,
    )
    return tile, line, result


def test_open_road_anchors_replace_overburden_and_ignore_coarse_portal_hump():
    _, line, result = buried()
    _, stations, heights, width = result.profiles[0]
    np.testing.assert_allclose(heights[[0, -1]], [2.4, 3.2], atol=0.01)
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    assert floor.positions[:, 1].max() < 3.5
    frames = [m for m in result.meshes if ":portal:" in m.object_id]
    assert len(frames) == 2 and all(m.metadata["approach_anchor"] for m in frames)
    assert sum(":wing:" in m.object_id for m in result.meshes) == 4
    opening = line.buffer(width / 2 - 1e-5, cap_style="flat")
    for mesh in result.meshes:
        if mesh.object_id.endswith((":floor", "ground-edge")) or mesh.metadata.get("lamp"):
            continue
        for tri in mesh.positions[mesh.indices]:
            ds = shapely.line_locate_point(line, shapely.points(tri[:, [0, 2]]))
            if np.min(tri[:, 1] - np.interp(ds, stations, heights)) < 4.49:
                assert shapely.Polygon(tri[:, [0, 2]]).intersection(opening).area < 1e-8


def test_anchor_follows_bent_mapped_continuation():
    line = shapely.LineString([(20, 30), (60, 30)])
    approach = shapely.LineString([(20, 30), (17, 30), (17, 20)])

    def source(x, z):
        return 2 + (30 - z) * 0.02 if abs(x - 17) < 0.01 else 12

    ends, fixed = portals.anchors(line, {"_approaches": [approach, None]}, source, [12, 2])
    assert fixed.tolist() == [True, False]
    assert 1.9 < ends[0] < 2.1


def test_cut_terrain_reuses_ground_palette_and_never_tunnel_concrete():
    tile, _, result = buried()
    ground = result.terrain_visuals(tile, np.full((32, 32, 3), [70, 130, 50], np.uint8))
    assert ground
    assert all(m.extras["layer"] == "ground" for m in ground)
    assert surfaces.KEYS.index("grass") in surfaces.channels(ground)
    assert not any(m.name == "tunnel:ground-edge" for m in result.visuals(tile))


def test_portal_header_follows_bank_cross_section_including_middle_peak():
    left, right = np.array([[20.0, 34], [60, 34]]), np.array([[20.0, 26], [60, 26]])

    def ground(x, z):
        return 11 + max(0, 3 - abs(z - 29.296875))

    frames = portals.frames(
        "bank",
        left,
        right,
        np.array([[0.0, 1], [0, 1]]),
        np.array([2.0, 2]),
        np.array([2.0, 2]),
        ground,
        250 / 128,
        4.5,
    )
    assert abs(frames[0].positions[:, 1].max() - 14) < 0.01
    assert frames[0].positions[:, 1].min() < 2


def test_curved_road_and_cycleway_share_one_open_shell_with_low_curb():
    line = shapely.LineString([(30, 20), (30, 35), (45, 55)])
    walk = shapely.LineString(list(line.offset_curve(-6.5).coords)[::-1])
    ways = [
        ("motor", line, dict(highway="residential", tunnel="yes", layer="-1", width="8")),
        ("cycle", walk, dict(highway="cycleway", tunnel="yes", layer="-1", width="3")),
    ]
    merged = tunnels.merge_parallel(ways)
    assert len(merged) == 1
    assert set(merged[0][0].split("+")) == {"motor", "cycle"}
    assert len(merged[0][1].coords) > 2
    width, height = tunnels.dimensions(merged[0][2])
    assert 11 < width < 14 and height == 4.5
    tile = C.VisualTile(0, 0, 0)
    p, ix = grid_mesh(tile, np.full((129, 129), 2.0))
    result = tunnels.produce(tile, merged, p, ix, [], extrude, lambda x, z: 2)
    assert len(result.profiles) == 1
    assert sum(":portal:" in m.object_id for m in result.meshes) == 2
    divider = next(m for m in result.meshes if m.object_id.endswith(":divider"))
    assert abs(divider.positions[:, 1].max() - 2.12) < 1e-8
