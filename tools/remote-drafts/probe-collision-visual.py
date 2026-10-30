"""Real first/full OCI builds with primitive-to-visible-geometry checks."""

import argparse
import importlib.util
import json
from pathlib import Path

from turku3d.coarse.visual_parity import audit

spec = importlib.util.spec_from_file_location(
    "probe_builds", Path(__file__).with_name("probe-builds.py")
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def verify(root, manifest):
    result = audit(root, manifest)
    if result["unmatched"]:
        raise RuntimeError(
            f"{manifest['tile']} {manifest['pass_kind']}: invisible primitives: {result}"
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tile", action="append", required=True)
    args = parser.parse_args()
    for tile in args.tile:
        result = probe.run(args.root, tile_id=tile, first_budget=45, full_budget=120, verify=verify)
        print(json.dumps(result), flush=True)
