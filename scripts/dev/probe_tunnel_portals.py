"""Real-tile portal builds and packed collision evidence against frozen OCI source."""

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from deploy_drafts import HOME
from facade_publish import remote
from turku3d import config as C
from turku3d.coarse import tunnels
from turku3d.coarse.build import build
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources, _Files
from turku3d.col.format import read_col


def vertical_hits(triangles, point):
    xz = triangles[:, :, [0, 2]]
    use = (xz.min(axis=1) <= point + 1e-6).all(axis=1) & (xz.max(axis=1) >= point - 1e-6).all(
        axis=1
    )
    tri = triangles[use]
    a, b, d = (
        tri[:, 1][:, [0, 2]] - tri[:, 0][:, [0, 2]],
        tri[:, 2][:, [0, 2]] - tri[:, 0][:, [0, 2]],
        point - tri[:, 0][:, [0, 2]],
    )
    determinant = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    valid = abs(determinant) > 1e-10
    tri, a, b, d, determinant = (v[valid] for v in (tri, a, b, d, determinant))
    u = (d[:, 0] * b[:, 1] - d[:, 1] * b[:, 0]) / determinant
    v = (a[:, 0] * d[:, 1] - a[:, 1] * d[:, 0]) / determinant
    inside = (u >= -1e-5) & (v >= -1e-5) & (u + v <= 1.00001)
    height = tri[:, 0, 1] + u * (tri[:, 1, 1] - tri[:, 0, 1]) + v * (tri[:, 2, 1] - tri[:, 0, 1])
    return height[inside]


def audit_collision(tile, inputs, manifest, out):
    from turku3d.coarse.bridge_join import terrain_sampler
    from turku3d.coarse.build import grid_mesh
    from turku3d.coarse.extrusion import extrude

    ground = terrain_sampler(inputs.points)
    p, ix = grid_mesh(tile, inputs.heights)
    ways = [w for w in inputs.tunnels if "306412192" in w[0].split("+")]
    geometry = tunnels.produce(
        tile, ways, p, ix, [], extrude, tunnels.ground_sampler(ground), ground
    )
    decoded, rays, floors = {}, 0, {}
    for line, stations, heights, width in geometry.profiles:
        key = next(key for key, axis, _ in ways if axis.equals(line))
        floors[key] = [float(heights[0]), float(heights[-1])]
        if "306412192" in key.split("+"):
            assert 3 < heights[0] < 3.5 and 4.5 < heights[-1] < 5.3, floors
        clearance = 4.5 if "306412192" in key.split("+") else 2.5
        for station in np.arange(1, line.length - 1, 3):
            point = np.array(line.interpolate(float(station)).coords[0])
            tangent = np.array(line.interpolate(float(station + 0.1)).coords[0]) - point
            normal = np.array([-tangent[1], tangent[0]]) / np.linalg.norm(tangent)
            for across in (0, -width / 2 + 0.5, width / 2 - 0.5):
                q = point + normal * across
                if not tile.bbox.contains(*q):
                    continue
                cell = C.CollisionTile.at(*q).id
                if cell not in decoded:
                    col = read_col(out / manifest["collision"][cell]["url"])
                    decoded[cell] = col.world_positions()[col.indices]
                hit = vertical_hits(decoded[cell], q)
                floor = float(np.interp(station, stations, heights))
                assert np.any(abs(hit - floor) < 0.2), (key, station, across, floor, hit.tolist())
                assert not np.any((hit > floor + 0.25) & (hit < floor + clearance - 0.25)), (
                    key,
                    station,
                    across,
                    floor,
                    hit.tolist(),
                )
                assert np.any(abs(hit - (floor + clearance)) < 0.25), (
                    key,
                    station,
                    across,
                    hit.tolist(),
                )
                rays += 1
    assert rays > 10
    return dict(clearance_rays=rays, profile_ends=floors)


def source_receipt(commit):
    names = [
        "pipeline/turku3d/coarse/" + name + ".py"
        for name in ("portals", "tunnels", "road_top", "build", "packaging")
    ]
    expected = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in names}
    program = (
        "import hashlib,json,pathlib; root=pathlib.Path(" + repr(f"{HOME}/sources/{commit}") + "); "
        "print(json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in "
        + repr(names)
        + "}))"
    )
    actual = json.loads(remote("python3 -c " + shlex.quote(program)))
    assert actual == expected, (actual, expected)
    return dict(commit=commit, matching_source_files=len(names), oci_builds_started=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--wfs-cache", type=Path)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if not args.local:
        result = dict(commit=args.commit, builds=[])
        for name in ("t0_-6_7", "t0_-6_6"):
            row = json.loads(
                subprocess.check_output(
                    [
                        sys.executable,
                        "scripts/dev/probe_drafts.py",
                        "--commit",
                        args.commit,
                        "--tile",
                        name,
                        "--first-budget",
                        "45",
                        "--full-budget",
                        "150",
                    ],
                    text=True,
                )
            )
            root = f"{HOME}/sources/{args.commit}"
            program = """import sys,json
from pathlib import Path
sys.path.insert(0, 'scripts/dev')
from probe_tunnel_portals import audit_collision
from turku3d import config as C
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import Sources
tile=C.VisualTile.parse(TILE)
out=Path(OUTPUT)
inputs=gather(tile,Path('/srv/turku3d/work/coarse'),Sources())
print(json.dumps(audit_collision(tile, inputs, json.loads((out/'manifest.json').read_text()), out)))
""".replace("TILE", repr(name)).replace("OUTPUT", repr(row["output"]))
            command = shlex.join([f"{root}/tools/remote-drafts/run.sh", "python", "-c", program])
            row["packed_clearance"] = json.loads(
                remote("TURKU3D_DRAFT_SOURCE=" + shlex.quote(root) + " " + command)
            )
            result["builds"].append(row)
            print(json.dumps(row), flush=True)
        print(json.dumps(result), flush=True)
        return
    if args.root is None or args.wfs_cache is None:
        parser.error("--local requires --root and --wfs-cache")
    args.root.mkdir(parents=True, exist_ok=True)
    sources = Sources()
    sources.wfs = _Files(args.wfs_cache, "*/*.summary.json")
    report = dict(source=source_receipt(args.commit), builds=[])
    for name in ("t0_-6_7", "t0_-6_6"):
        tile = C.VisualTile.parse(name)
        for first in (True, False):
            start = time.monotonic()
            inputs = gather(tile, args.root, sources, first_pass=first)
            out = args.root / name / ("first" if first else "full")
            manifest = build(name, out, inputs=inputs, sources=sources, first_pass=first)
            assert manifest["pass_kind"] == ("first" if first else "full")
            assert manifest["buildings"] == len(inputs.buildings)
            assert len(manifest["collision"]) == 16
            assert all(
                (out / entry["url"]).is_file()
                for entry in [*manifest["visual"].values(), *manifest["collision"].values()]
            )
            row = dict(
                tile=name,
                first_pass=first,
                seconds=round(time.monotonic() - start, 3),
                buildings=manifest["buildings"],
                collision=16,
                **audit_collision(tile, inputs, manifest, out),
            )
            report["builds"].append(row)
            print(json.dumps(row), flush=True)
            (args.root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
