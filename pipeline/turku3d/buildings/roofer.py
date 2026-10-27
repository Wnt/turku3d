"""Native roofer execution and CityJSONSeq 2.2 surface extraction."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from turku3d import config as C

from .shells import Surface

FLAGS = ["--srs", C.CRS_HORIZONTAL, "--id-attribute", "building_id", "--lod22", "--no-clip-terrain"]


def version() -> str:
    return subprocess.check_output(["roofer", "--version"], text=True).strip()


def run(pointclouds, footprints, output, jobs=4):
    output.mkdir(parents=True, exist_ok=True)
    # One directory is one pointcloud source in roofer. Passing adjacent LAZ
    # files as separate sources can make its epoch selector discard neighbours.
    pointcloud_dir = output / "pointclouds"
    pointcloud_dir.mkdir(exist_ok=True)
    wanted = {Path(p).name: Path(p).resolve() for p in pointclouds}
    for p in pointcloud_dir.iterdir():
        if p.name not in wanted:
            if not p.is_symlink():
                raise ValueError(f"unexpected file in roofer input staging: {p}")
            p.unlink()
    for name, target in wanted.items():
        link = pointcloud_dir / name
        if link.is_symlink() and link.resolve() != target:
            link.unlink()
        if not link.exists():
            link.symlink_to(target)
    command = [
        "roofer",
        "-j",
        str(jobs),
        *FLAGS,
        str(pointcloud_dir.resolve()),
        str(footprints.resolve()),
        str(output.resolve()),
    ]
    with (output / "run.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=output)
    (output / "execution.json").write_text(
        json.dumps({"command": command, "version": version()}, indent=2) + "\n"
    )
    return command


def features(paths):
    """{building id: (transform, CityJSONFeature)} from CityJSONSeq files, one line per building."""
    result = {}
    for path in sorted(paths):
        transform = {"scale": [1, 1, 1], "translate": [0, 0, 0]}
        for line in Path(path).read_text().splitlines():
            data = json.loads(line)
            if data.get("type") == "CityJSON":
                transform = data.get("transform", transform)
                continue
            result[str(data["id"])] = (transform, data)
    return result


def surfaces(transform, feature):
    """LoD 2.2 surfaces of one feature, grouped by the building that owns each part."""
    results = {}
    if not feature.get("vertices"):
        return results
    v = np.array(feature["vertices"], dtype=float) * transform["scale"] + transform["translate"]
    v = np.column_stack(C.en_to_local(v[:, 0], v[:, 1], v[:, 2]))
    for key, obj in feature["CityObjects"].items():
        bid = str(obj.get("parents", [key])[0])
        for geom in obj.get("geometry", []):
            if str(geom.get("lod")) != "2.2":
                continue
            semantics = geom.get("semantics", {})
            values = semantics.get("values", [])
            boundaries = geom["boundaries"]
            if geom["type"] == "Solid":
                parts = [
                    (s, values[k][i] if values else None)
                    for k, shell in enumerate(boundaries)
                    for i, s in enumerate(shell)
                ]
            elif geom["type"] == "MultiSurface":
                parts = [(s, values[i] if values else None) for i, s in enumerate(boundaries)]
            else:
                raise ValueError(f"unsupported roofer geometry {geom['type']}")
            for rings, semantic in parts:
                kind = (
                    semantics["surfaces"][semantic]["type"]
                    if semantic is not None
                    else "WallSurface"
                )
                results.setdefault(bid, []).append(Surface([v[r] for r in rings], kind))
    return results


def metadata(feature):
    return {
        key: obj.get("attributes", {})
        for key, obj in feature.get("CityObjects", {}).items()
        if obj.get("type") == "Building"
    }
