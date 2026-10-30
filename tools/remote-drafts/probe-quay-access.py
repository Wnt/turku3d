"""Auransilta stair/landing body clearance on real OCI first/full collision payloads."""

import importlib.util
from pathlib import Path

import numpy as np
import shapely
from turku3d import config as C
from turku3d.col.format import read_col


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("opening_probe", "probe-tunnel-openings.py")
original_verify = probe.verify

# Surveyed OSM stair-to-quay-to-portal routes from the reference-photo review.
LANDINGS = [
    (
        "market-north",
        [
            (297.3839, 202.4687),
            (298.5982, 204.1647),
            (301.6816, 202.6055),
            (301.2289, 206.1996),
            (299.0642, 207.3122),
        ],
        1.13,
    ),
    ("opposite-south", [(311.7614, 254.1862), (309.926, 250.9122), (311.7232, 249.6841)], 1.32),
    ("market-south", [(273.7547, 219.6117), (283.5782, 214.3721)], 1.15),
]
STAIRS = {"776088937", "776088929", "687325029"}


def verify(tile, inputs, manifest, out, tunnels, raycaster):
    checked = original_verify(tile, inputs, manifest, out, tunnels, raycaster)
    collision = {
        key: read_col((out / entry["url"]).read_bytes())
        for key, entry in manifest["collision"].items()
    }
    casts = {key: raycaster(col) for key, col in collision.items()}
    triangles = np.concatenate(
        [(col.positions + col.origin)[col.indices] for col in collision.values()]
    )
    assert np.isfinite(triangles).all(), "nonfinite collision"
    area2 = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
    )
    assert (area2 > 0).all(), "degenerate packed collision"
    window = shapely.box(*tile.bbox.as_tuple())
    routes = []
    for key, line, tags in inputs.stairs[0]:
        if key not in STAIRS:
            continue
        profile = tags["_profile"]
        routes.append(
            (key, line, lambda s, p=profile: np.interp(s, p["stations"], p["heights"]), 0.32, 0.22)
        )
    routes.extend(
        (key, shapely.LineString(coords), lambda s, h=level: h, 0.20, 0.12)
        for key, coords, level in LANDINGS
    )
    for key, line, expected_at, tolerance, max_step in routes:
        if not line.intersects(window):
            continue
        points = []
        for s in np.arange(0.02, line.length, 0.1):
            x, z = line.interpolate(s).coords[0]
            if not window.contains(shapely.Point(x, z)):
                continue
            expected = expected_at(s)
            ys, _ = casts[C.CollisionTile.at(x, z).id](x, z)
            support = ys[(ys >= expected - 0.10) & (ys <= expected + tolerance)]
            assert len(support), (key, s, "missing tread/landing", expected, ys)
            floor = float(max(support))
            blocked = ys[(ys > floor + 0.35) & (ys < floor + 1.8)]
            assert not len(blocked), (key, s, "overhead obstruction", floor, blocked)
            points.append([x, floor, z])
        if not points:
            continue
        points = np.asarray(points)
        rises = abs(np.diff(points[:, 1]))
        assert not len(rises) or max(rises) <= max_step, (key, "step discontinuity", max(rises))
        bbox = np.asarray(line.bounds)
        near = (triangles[:, :, [0, 2]].max(axis=1) >= bbox[:2] - 1).all(axis=1) & (
            triangles[:, :, [0, 2]].min(axis=1) <= bbox[2:] + 1
        ).all(axis=1)
        local = triangles[near]
        segments = 0
        for lift in (0.35, 1, 1.7):
            body = points + [0, lift, 0]
            for start, end in zip(body[:-1], body[1:], strict=True):
                assert not probe.segment_hits(local, start, end).any(), (
                    key,
                    start,
                    end,
                    "vertical body blocker",
                )
                segments += 1
        checked.append(
            dict(
                route=key,
                support_rays=len(points),
                body_segments=segments,
                max_step_m=float(max(rises)) if len(rises) else 0,
            )
        )
    checked.append(dict(collision_triangles=len(triangles), nonfinite=0, degenerate=0))
    return checked


if __name__ == "__main__":
    probe.verify = verify
    probe.main()
