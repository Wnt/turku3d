"""Real-data Rapier probes over generated structures and the supplied ground triangles."""

import json
import subprocess

import numpy as np

from turku3d import config as C
from turku3d.col.materials import mat_id


def prepare_cases(meshes, ground, measurements):
    triangles = np.concatenate([ground.triangles, *(m.positions[m.indices] for m in meshes)])
    material = np.concatenate(
        [
            ground.materials,
            *(np.full(len(m.indices), mat_id(m.material), np.uint8) for m in meshes),
        ]
    )
    low = triangles[:, :, [0, 2]].min(axis=1)
    high = triangles[:, :, [0, 2]].max(axis=1)
    cases, issues = [], []
    for measured in measurements:
        kind = measured["kind"]
        if kind not in ("stairs", "wall", "fence", "railing", "quay"):
            continue
        routes = []
        try:
            if kind == "stairs":
                bottom, top = np.array(measured["bottom"]), np.array(measured["top"])
                direction = top[[0, 2]] - bottom[[0, 2]]
                direction /= np.linalg.norm(direction)
                a, b = bottom[[0, 2]] - direction * 0.6, top[[0, 2]] + direction * 0.6
                start = [a[0], ground.height(a, reference=bottom[1]), a[1]]
                target = [b[0], ground.height(b, reference=top[1]), b[1]]
                routes = [{"start": start, "target": target}, {"start": target, "target": start}]
            else:
                points = np.asarray(measured["profile_xz"])
                heights = np.asarray(measured["upper_y"]) - measured["lower_y"]
                segments = np.diff(points, axis=0)
                lengths = np.linalg.norm(segments, axis=1)
                distance = np.r_[0, np.cumsum(lengths)]
                if kind == "wall":
                    eligible = np.flatnonzero((heights[:-1] + heights[1:]) / 2 > 0.35)
                    if not len(eligible):
                        continue  # Low walls are not required to block the capsule.
                    k = int(eligible[len(eligible) // 2])
                    stations = [(distance[k] + distance[k + 1]) / 2]
                elif kind == "quay":
                    inset = min(1.5, distance[-1] / 2)
                    stations = np.arange(inset, distance[-1] - inset + 1e-8, 10).tolist()
                else:
                    stations = [distance[-1] / 2]
                for station in stations:
                    k = min(
                        int(np.searchsorted(distance, station, side="right")) - 1, len(segments) - 1
                    )
                    centre = points[k] + segments[k] * (station - distance[k]) / lengths[k]
                    tangent = segments[k] / lengths[k]
                    normal = np.array([-tangent[1], tangent[0]])
                    a, b = centre - normal * 2, centre + normal * 2
                    ah, bh = ground.height(a), ground.height(b)
                    # Retaining walls block ascent from the lower side; a character
                    # can step off the upper side. Railings and fences are tested
                    # from upper ground, using measured contact on both sides.
                    if (kind == "wall" and ah > bh) or (kind != "wall" and ah < bh):
                        a, b, ah, bh = b, a, bh, ah
                    routes.append({"start": [a[0], ah, a[1]], "target": [b[0], bh, b[1]]})
        except ValueError as error:
            issues.append({"object_id": measured["object_id"], "reason": f"physics route: {error}"})
            continue
        for k, route in enumerate(routes):
            ends = np.asarray([route["start"], route["target"]])[:, [0, 2]]
            pmin, pmax = ends.min(axis=0) - 2, ends.max(axis=0) + 2
            mask = np.all(high >= pmin, axis=1) & np.all(low <= pmax, axis=1)
            cases.append(
                {
                    "object_id": f"{measured['object_id']}:route:{k}",
                    "kind": kind,
                    "triangles": triangles[mask].tolist(),
                    "material": material[mask].tolist(),
                    "routes": [route],
                }
            )
    return cases, issues


def validate(meshes, ground, measurements, report_dir):
    cases, issues = prepare_cases(meshes, ground, measurements)
    source, output = report_dir / "physics-cases.json", report_dir / "physics.json"
    source.write_text(json.dumps(cases) + "\n")
    output.unlink(missing_ok=True)
    result = subprocess.run(
        [
            "node",
            str(C.REPO_ROOT / "tools/validator/src/structures-cli.ts"),
            str(source),
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1) or not output.is_file():
        raise ValueError(f"structures physics validator failed: {result.stdout}\n{result.stderr}")
    report = json.loads(output.read_text())
    issues.extend(
        {"object_id": r["object_id"], "reason": "real-data Rapier traversal failed"}
        for r in report["results"]
        if not r["passed"]
    )
    return report, issues
