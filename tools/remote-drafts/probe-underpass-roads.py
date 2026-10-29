"""Owned first/full OCI builds and shipped collision checks for two real underpasses."""

import argparse
import importlib
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import forests, road_top
from turku3d.coarse.build_health import BuildHealth
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources
from turku3d.coarse.tunnels import dimensions
from turku3d.col.format import read_col
from turku3d.col.materials import mat_id
from turku3d.parked.placement import footprint


def hits(col, x, z):
    triangles = (col.positions + col.origin)[col.indices]
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    u, v = b[:, [0, 2]] - a[:, [0, 2]], c[:, [0, 2]] - a[:, [0, 2]]
    q = np.array([x, z]) - a[:, [0, 2]]
    det = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    valid = abs(det) > 1e-9
    safe = np.where(valid, det, 1)
    s = (q[:, 0] * v[:, 1] - q[:, 1] * v[:, 0]) / safe
    t = (u[:, 0] * q[:, 1] - u[:, 1] * q[:, 0]) / safe
    valid &= (s >= -1e-7) & (t >= -1e-7) & (s + t <= 1 + 1e-7)
    return a[valid, 1] + s[valid] * (b - a)[valid, 1] + t[valid] * (c - a)[valid, 1], col.materials[
        valid
    ]


def verify(tile, inputs, manifest, out, captured, plants):
    tunnels, positions, sample, mask = captured
    cols = {
        key: read_col((out / entry["url"]).read_bytes())
        for key, entry in manifest["collision"].items()
    }
    roads = [
        (key, line, tags)
        for key, line, tags in inputs.tunnels
        if "road_top" in tags and shapely.box(*tile.bbox.as_tuple()).covers(line)
    ]
    assert roads, f"No complete source underpass in {tile.id}"
    checks = []
    for key, line, tags in roads:
        width, clearance = dimensions(tags)
        profile = next(p for p in tunnels.profiles if p[0].equals(line))
        floor = next(m for m in tunnels.meshes if m.object_id == f"tunnel:{key}:floor")
        roof = next(m for m in tunnels.meshes if m.object_id == f"tunnel:{key}:roof")
        tri = roof.positions[roof.indices]
        assert (np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 1] < 0).all()
        np.testing.assert_allclose(
            roof.positions[:, 1], [sample(x, z) - 0.3 for x, _, z in roof.positions], atol=0.002
        )
        error, gap = [], []
        for distance in np.linspace(2, line.length - 2, 21):
            centre = np.array(line.interpolate(distance).coords[0])
            direction = np.array(line.interpolate(distance + 0.1).coords[0]) - centre
            normal = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction)
            for offset in (-width / 4, 0, width / 4):
                x, z = centre + normal * offset
                top = sample(x, z)
                ys, materials = hits(cols[C.CollisionTile.at(x, z).id], x, z)
                assert len(ys), (key, distance, "missing collision")
                deck = abs(ys - top) < 0.005
                assert deck.any(), (key, distance, "missing road top", ys, top)
                assert (materials[deck] != mat_id("wall")).all(), (
                    "Concrete slab replaced road material"
                )
                ceiling = abs(ys - (top - 0.3)) < 0.005
                assert ceiling.any(), (key, distance, "missing ceiling")
                expected_floor = np.interp(distance, profile[1], profile[2])
                floor_hits = ys[(ys < top - 0.4) & (abs(ys - expected_floor) < 0.15)]
                assert len(floor_hits), (key, distance, "missing floor", ys, expected_floor)
                bottom = max(floor_hits)
                assert top - 0.3 - bottom >= clearance - 0.01, (key, distance, top, bottom)
                assert not ((ys > bottom + 0.02) & (ys < top - 0.32)).any(), (
                    key,
                    distance,
                    "passage obstructed",
                    ys,
                )
                error.append(float(min(abs(ys[deck] - top))))
                gap.append(float(top - 0.3 - bottom))
        for mesh in tunnels.meshes:
            if mesh.object_id.startswith(f"tunnel:{key}:") and not mesh.object_id.endswith(
                ":floor"
            ):
                assert max(y - sample(x, z) for x, y, z in mesh.positions) < 0
        checks.append(
            dict(
                id=key,
                name=tags.get("name"),
                rays=len(gap),
                clearance_min=min(gap),
                road_collision_error_max=max(error),
                floor_triangles=len(floor.indices),
                ceiling_triangles=len(roof.indices),
            )
        )
    if manifest["pass_kind"] == "full":
        for tree in plants:
            assert mask.distance(shapely.Point(tree.x, tree.z)) > tree.crown_radius
        for car in manifest["vehicles"]:
            assert not mask.intersects(
                footprint(car["x"], car["z"], car["yaw"], car["width"], car["length"])
            )
        expected_poles = sum(mask.distance(shapely.Point(p.x, p.z)) > 0.5 for p in inputs.poles)
        assert manifest["poles"] == expected_poles
        assert manifest["underpass_road_decks"] == sum(
            "road_top" in tags and line.intersects(shapely.box(*tile.bbox.as_tuple()))
            for _, line, tags in inputs.tunnels
        )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    os.environ["TURKU3D_WORK"] = str(args.root)
    os.environ["TURKU3D_BROWSER_DIR"] = str(args.root / "browser")
    for tile in ("t0_6_6", "t0_5_9"):
        source = args.assets / ".inputs" / tile
        if source.exists():
            shutil.copytree(source, args.root / ".inputs" / tile)
    for pattern in ("shared", ".surface-library*"):
        for source in args.assets.glob(pattern):
            if source.is_dir():
                shutil.copytree(source, args.root / source.name)
    module = importlib.import_module("turku3d.coarse.build")
    original, scatter = road_top.stage, forests.produce
    captured, plants = [], []

    def capture(*a, **kw):
        result = original(*a, **kw)
        captured[:] = result
        return result

    def vegetation(*a, **kw):
        result = scatter(*a, **kw)
        plants[:] = result[0] + result[1]
        return result

    road_top.stage, forests.produce = capture, vegetation
    telemetry = BuildHealth(args.root)
    report = dict(root=str(args.root), runs=[])
    for tile_id in ("t0_6_6", "t0_5_9"):
        tile = C.VisualTile.parse(tile_id)
        for kind in ("first", "full"):
            started = time.monotonic()
            try:
                inputs = gather(tile, args.root, Sources(), first_pass=kind == "first")
                out = args.root / tile_id
                manifest = module.build(tile_id, out, inputs=inputs, first_pass=kind == "first")
                elapsed = time.monotonic() - started
                telemetry.record(kind, "built", elapsed)
                assert manifest["pass_kind"] == kind
                assert len(manifest["collision"]) == 16
                assert manifest["buildings"] == len(inputs.buildings)
                for entry in [*manifest["visual"].values(), *manifest["collision"].values()]:
                    assert (out / entry["url"]).is_file()
                checks = verify(tile, inputs, manifest, out, captured, plants)
                assert elapsed < (90 if kind == "first" else 180), (tile_id, kind, elapsed)
                row = dict(
                    tile=tile_id,
                    kind=kind,
                    seconds=round(elapsed, 3),
                    tunnels_seconds=manifest["seconds"]["tunnels"],
                    buildings=len(inputs.buildings),
                    collision=16,
                    underpasses=checks,
                    trees=manifest.get("trees", 0),
                    poles=manifest.get("poles", 0),
                    cars=len(manifest.get("vehicles", [])),
                )
                report["runs"].append(row)
                print(json.dumps(row), flush=True)
                (args.root / "result.json").write_text(json.dumps(report, indent=2))
            except Exception as error:
                telemetry.record(kind, "FAILED", time.monotonic() - started, type(error).__name__)
                raise


if __name__ == "__main__":
    main()
