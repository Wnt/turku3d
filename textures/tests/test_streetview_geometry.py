import math

import numpy as np

from turkutex.streetview import camera as C
from turkutex.streetview.facades import Facade, merge_edges, point_in_ring, street_facades
from turkutex.streetview.panos import PanoMeta, camera_height, z17_tile
from turkutex.streetview.refs import SelectParams, _sub_facade, select


def synthetic_equirect(W=2048, H=1024, heading=1.0, marker_az=30.0, marker_el=10.0):
    img = np.zeros((H, W, 3), np.uint8)
    m = C.EquirectModel(heading)
    d = np.array([math.sin(math.radians(marker_az)) * math.cos(math.radians(marker_el)),
                  math.cos(math.radians(marker_az)) * math.cos(math.radians(marker_el)),
                  math.sin(math.radians(marker_el))])
    u, v = m.uv_from_dir(d, W, H)
    img[int(v) - 6:int(v) + 7, int(u) - 6:int(u) + 7] = (255, 0, 0)
    return img, m, float(u), float(v)


def test_equirect_google_convention():
    # image centre column is the heading; azimuth grows to the right (clockwise from north)
    m = C.EquirectModel(math.radians(90))
    u, v = m.uv_from_dir(np.array([1.0, 0, 0]), 4096, 2048)  # due east
    assert abs(u - 2048) < 1e-6 and abs(v - 1024) < 1e-6
    u, _ = m.uv_from_dir(np.array([0, -1.0, 0]), 4096, 2048)  # south = heading + 90 deg -> 3/4 W
    assert abs(u - 3072) < 1e-6
    d = m.dir_from_uv(np.array(1000.0), np.array(300.0), 4096, 2048)
    u2, v2 = m.uv_from_dir(d, 4096, 2048)
    assert abs(u2 - 1000) < 1e-6 and abs(v2 - 300) < 1e-6


def test_render_centres_target():
    img, m, _, _ = synthetic_equirect()
    cam = C.PerspectiveCam(np.zeros(3), 30.0, 10.0, 40.0, 201, 151)
    out = C.render(img, m, cam)
    cy, cx = 75, 100
    assert out[cy, cx, 0] > 200 and out[cy, cx, 1] < 50
    # marker absent away from the centre
    assert out[10, 10, 0] < 50


def test_project_matches_rays():
    cam = C.PerspectiveCam(np.array([10.0, 20.0, 2.0]), 123.0, -5.0, 70.0, 320, 200)
    rays = cam.rays()
    for (r, c) in [(0, 0), (199, 319), (100, 160), (37, 250)]:
        p = cam.pos + rays[r, c] * 17.0
        px = cam.project(p[None])[0]
        assert abs(px[0] - (c + 0.5)) < 1e-6 and abs(px[1] - (r + 0.5)) < 1e-6


def test_fit_camera_contains_corners():
    f = Facade("f", np.array([0.0, 0.0]), np.array([20.0, 0.0]), 9.0)  # normal: right of +E = south
    assert abs(f.normal_azimuth_deg - 180) < 1e-9
    corners = f.corners(5.0)
    # seen from the south, p0 (west) is on the viewer's left
    assert corners[0][0] == 0.0 and corners[1][0] == 20.0
    cam = C.fit_camera(np.array([12.0, -15.0, 7.3]), corners)
    px = cam.project(corners)
    assert np.all(px[:, 0] > 0) and np.all(px[:, 0] < cam.width)
    assert np.all(px[:, 1] > 0) and np.all(px[:, 1] < cam.height)
    assert px[0, 0] < px[1, 0]  # bottom-left left of bottom-right
    t, inside = C.facade_hits(cam, corners)
    assert inside[cam.height // 2, cam.width // 2]
    assert abs(t[cam.height // 2, cam.width // 2] - np.linalg.norm(corners.mean(0) - cam.pos)) < 0.5


def test_view_angle():
    assert abs(C.view_angle_deg([0, -10], [0, 0], np.array([0, -1.0]))) < 1e-9
    assert abs(C.view_angle_deg([10, -10], [0, 0], np.array([0, -1.0])) - 45) < 1e-9


def square(x0, y0, s=10.0):
    return np.array([[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s]])  # CCW


def test_merge_edges_and_street_facades():
    ring = np.array([[0, 0], [8, 0.2], [16, 0], [16, 10], [0, 10]], float)  # jog on the south side
    runs = merge_edges(ring)
    lens = sorted(round(float(np.linalg.norm(ring[b] - ring[a])), 1) for a, b in runs)
    assert lens == [10.0, 10.0, 16.0, 16.0]
    fps = [{"id": "A", "ring": ring, "floors": 2, "address": "X 1", "material": None, "year": None},
           {"id": "B", "ring": square(-30, 0), "floors": 1, "address": "Y", "material": None, "year": None}]
    streets = [{"name": "Main", "kind": "x", "line": np.array([[-50.0, -6.0], [50.0, -6.0]])}]
    fs = street_facades(fps, streets)
    a = [f for f in fs if f.props["building_id"] == "A"]
    assert len(a) == 1 and abs(a[0].normal_azimuth_deg - 180) < 2 and abs(a[0].length - 16) < 1e-6
    assert a[0].height_m == round(0.8 + 2 * 3.2, 2) and a[0].props["street"] == "Main"
    assert point_in_ring((5, 5), ring) and not point_in_ring((5, -5), ring)


def test_blocked_facade_is_not_street_facing():
    fps = [{"id": "A", "ring": square(0, 0), "floors": 1, "address": None, "material": None, "year": None},
           {"id": "B", "ring": square(0, -14), "floors": 1, "address": None, "material": None, "year": None}]
    streets = [{"name": "S", "kind": "x", "line": np.array([[-50.0, -20.0], [50.0, -20.0]])}]
    fs = street_facades(fps, streets)
    assert not any(f.props["building_id"] == "A" and abs(f.normal_azimuth_deg - 180) < 5 for f in fs)
    assert any(f.props["building_id"] == "B" and abs(f.normal_azimuth_deg - 180) < 5 for f in fs)


def test_camera_height_by_campaign():
    assert camera_height("google", "2026-07") == 2.31
    assert camera_height("google", "2024-06") == 2.86
    assert camera_height("google", "2019-06") == 1.49
    assert camera_height("google", None) == 2.36
    assert z17_tile(60.44839, 22.26726) == (73643, 37734)


def _ev(pid, E, N, part, ang, score, sec=(0, 10)):
    m = PanoMeta("google", pid, 0, 0, E, N, 7.0, 0, 0, 0, "2026-07", {})
    return {"meta": m, "part": part, "signed_angle_deg": ang, "score": score, "section_m": list(sec)}


def test_select_diversity_and_full_view():
    P = SelectParams(max_views=3)
    evs = [_ev("a", 0, -10, "partial", 0, 0.9, (0, 8)), _ev("b", 1, -10, "partial", 2, 0.85, (1, 9)),
           _ev("c", 20, -10, "partial", 30, 0.6, (12, 20)), _ev("d", 0, -40, "full", 5, 0.3, (0, 30)),
           _ev("e", 2, -41, "full", 6, 0.29, (0, 30))]
    ch = [e["meta"].id for e in select(evs, P)]
    assert ch[0] == "d"           # a full view is always included when one exists
    assert "a" in ch and "c" in ch and "b" not in ch and "e" not in ch


def test_sub_facade_centred_on_camera():
    f = Facade("f", np.array([0.0, 0.0]), np.array([40.0, 0.0]), 5.0)
    g = _sub_facade(f, np.array([30.0, -6.0]), 6.0)
    assert g is not None and abs(np.linalg.norm(g.p1 - g.p0) - 8.4) < 1e-6
    assert abs(g.centre[0] - 30.0) < 1e-6
    g2 = _sub_facade(f, np.array([39.0, -6.0]), 6.0)  # clamped to the facade end
    assert abs(g2.p1[0] - 40.0) < 1e-6
