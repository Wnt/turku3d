"""Replay retained OCI wall evidence without rebuilding tiles or publishing private geometry."""

import argparse
import ast
import collections
import json
import re
from pathlib import Path

import numpy as np
from turku3d.package.gltf import GlbMesh
from turku3d.wall_assembly import assemble_structures
from turku3d.wall_surfaces import validate_meshes


def audit(log, evidence):
    reports = collections.Counter()
    kinds = collections.Counter()
    tiles = collections.Counter()
    for line in log.read_text().splitlines():
        match = re.search(r"wall (neighbour|surface) invariant (t0_[-\d]+_[-\d]+)", line)
        if not match:
            continue
        reports[match[1]] += 1
        tiles[match[2]] += 1
        for pair in ast.literal_eval(line[line.index("[{") :]):
            a, b = pair["first"], pair["second"]
            kind = (
                "roof/building"
                if "_roof/" in a + b
                else "tunnel/building"
                if "tunnel:" in a + b
                else "basic-structure/building"
                if "basic_wall/" in a + b
                else "mixed-pass"
                if ("/basic_" in a) != ("/basic_" in b)
                else "same-pass building/building"
            )
            kinds[kind] += 1
    replay = []
    protected = {"first": [], "full": []}
    for path in sorted(evidence.glob("t0_*.json")):
        record = json.loads(path.read_text())
        points = np.asarray(record["faces"], float).reshape(-1, 3)
        if not len(points):
            continue
        pass_kind = "first" if any("/basic_" in i for i in record["ids"]) else "full"
        mesh = GlbMesh(path.stem, points, np.arange(len(points)).reshape(-1, 3))
        before = validate_meshes([*protected[pass_kind], mesh], path.stem)["violations"]
        remainder = assemble_structures([mesh], protected[pass_kind])
        # Resolve in-tile triangles as well as already admitted neighbours.
        remainder = assemble_structures(remainder)
        after = validate_meshes([*protected[pass_kind], *remainder], path.stem)["violations"]
        replay.append(
            dict(
                tile=path.stem,
                pass_kind=pass_kind,
                faces=len(points) // 3,
                before=before,
                after=after,
            )
        )
        protected[pass_kind].extend(remainder)
    return dict(
        log_reports=reports,
        logged_pair_samples=kinds,
        log_tiles=tiles,
        replay=replay,
        limitation="Legacy records have no generator identity; replay groups inferred passes only. "
        "Boundary records are a subset of full tile geometry; log samples are truncated.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.log, args.evidence)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
