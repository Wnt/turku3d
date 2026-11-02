"""Build one immutable landmark asset from cached survey geometry, without a tile rebuild.

The raw PNG PBR maps make this useful for live development; normal full-tile
packaging still compresses the same material outputs for streaming.
"""

import argparse
import hashlib
import json
import pickle
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.coarse.extrusion import extrude
from turku3d.coarse.stored_facades import assemble_walls
from turku3d.package.gltf import build_glb
from turku3d.stage_cache import content_key, source_key

from .appearance import roof_mesh, wall_meshes
from .descriptions import resolve, source_paths


def build(building, root):
    description = resolve(building)
    if description is None:
        raise ValueError(f"No landmark description for {building.facade_key}")
    package = Path(__file__).parents[1]
    code = source_key(
        *source_paths(),
        Path(__file__),
        Path(C.__file__),
        *[
            package / name
            for name in (
                "coarse/extrusion.py",
                "coarse/roofs.py",
                "coarse/stored_facades.py",
                "wall_surfaces.py",
                "package/gltf.py",
            )
        ],
    )
    key = content_key([building, description, code])
    directory = root / str(building.facade_key)
    directory.mkdir(parents=True, exist_ok=True)
    record = directory / f"{key}.json"
    if record.exists():
        result = json.loads(record.read_text())
        (directory / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    p = building.footprint.centroid
    tile = C.VisualTile.at(p.x, p.y)
    origin = np.asarray(tile.origin)
    shell = extrude(building)
    walls = assemble_walls([(building, shell)])[0]
    meshes = [
        roof_mesh(building, shell[0], shell[1], origin, description),
        *wall_meshes(building, walls, origin, description),
    ]
    data = build_glb(
        meshes, root_translation=origin, root_name="landmark_" + str(building.facade_key)
    )
    digest = hashlib.sha256(data).hexdigest()
    path = directory / f"{digest}.glb"
    path.write_bytes(data)
    result = dict(
        building_id=str(building.facade_key),
        recipe_hash=key,
        sha256=digest,
        url=path.name,
        bytes=len(data),
        meshes=len(meshes),
        triangles=sum(len(mesh.indices) for mesh in meshes),
        description_hash=description["content_hash"],
        origin=list(map(float, origin)),
    )
    record.write_text(json.dumps(result, indent=2) + "\n")
    (directory / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--building-id", required=True)
    p.add_argument("--cache-root", type=Path, action="append", required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    files = [path for root in a.cache_root for path in root.glob("**/.inputs/*/wfs.*.pkl")]
    for path in sorted(set(files), key=lambda path: path.stat().st_mtime, reverse=True):
        value = pickle.loads(path.read_bytes())["value"]
        for building in value.get("buildings", []):
            if str(building.facade_key) == a.building_id:
                print(json.dumps(build(building, a.out)))
                return
    raise ValueError(f"Building {a.building_id} not present in prepared survey caches")


if __name__ == "__main__":
    main()
