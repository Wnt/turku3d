"""Build the reported quay and opposite-bank neighbour first/full, then audit their collision."""

import argparse
import json
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    result = dict(commit=args.commit, builds=[], audits=[])
    for tile in ("t0_-1_1", "t0_0_1"):
        output = subprocess.check_output(
            [
                sys.executable,
                "scripts/dev/probe_drafts.py",
                "--commit",
                args.commit,
                "--tile",
                tile,
                "--first-budget",
                "75",
                "--full-budget",
                "150",
            ],
            text=True,
        )
        row = json.loads(output)
        result["builds"].append(row)
        print(json.dumps(dict(build=row)), flush=True)
        command = shlex.join(
            [
                f"{source}/tools/remote-drafts/run.sh",
                "python",
                "-m",
                "turku3d.coarse.walkable_audit",
                "--root",
                "/srv/turku3d/work/coarse",
                "--output",
                row["output"],
            ]
        )
        audit = json.loads(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command))
        result["audits"].append(audit)
        print(json.dumps(dict(audit=audit)), flush=True)
    result["tiles_changed"] = [row["tile"] for row in result["audits"]]
    print(json.dumps(result), flush=True)
    misses = sum(a["collision_ray_misses"] for a in result["audits"])
    assert misses == 0, f"Shipped river walk collision has {misses} misses"
    holes = sum(a["collision_holes"] for a in result["audits"])
    assert holes == 0, f"Shipped river walk collision has {holes} holes larger than 1 cm²"
    focus = next(
        p["recorded_reach"] for a in result["audits"] for p in a["paths"] if "recorded_reach" in p
    )
    assert focus["after"]["max_step_25cm_m"] < 0.05, focus
    assert focus["after"]["max_dent_2m_m"] < 0.05, focus
    assert sum(a["stairs"]["lidar_flights"] for a in result["audits"]) > 0


if __name__ == "__main__":
    main()
