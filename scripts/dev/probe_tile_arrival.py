"""One bounded arrival investigation: real OCI builds and real client rendering in parallel."""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def warmed_cache():
    label = "lan-warmed-50"
    command = [
        sys.executable,
        "scripts/dev/host_slots.py",
        "browser",
        "--",
        "node",
        "game/scripts/tile-arrival-profile.mjs",
        label,
        "120",
        "50",
    ]
    run = subprocess.run(command)
    if run.returncode:
        return run.returncode
    path = Path(os.environ["TURKU3D_WORK"]) / "reports/tile-arrival" / label / "result.json"
    result = json.loads(path.read_text())
    frames = sorted(result["frames"])
    p95 = frames[min(len(frames) - 1, int(len(frames) * 0.95))] if frames else None
    p99 = frames[min(len(frames) - 1, int(len(frames) * 0.99))] if frames else None
    count = len(result["crossings"])
    passed = (
        count > 0
        and result["first_before_arrival"] == count
        and result["full_by_arrival_plus_5"] == count
        and not result["errors"]
        and not result["page_errors"]
        and p95 is not None
        and p95 <= 1000 / 30
        and p99 <= 50
    )
    summary = dict(
        crossings=count,
        first_before_arrival=result["first_before_arrival"],
        full_by_arrival_plus_5=result["full_by_arrival_plus_5"],
        frame_ms=dict(samples=len(frames), p95=p95, p99=p99, max=max(frames, default=None)),
        passed=passed,
    )
    path.with_name("summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return int(not passed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit")
    parser.add_argument("--reuse-caches")
    parser.add_argument(
        "--client-only",
        action="store_true",
        help="Serving/client changes do not compile new geometry",
    )
    parser.add_argument("--browser-slot-held", action="store_true")
    parser.add_argument("--label-prefix", default="")
    parser.add_argument("--rounds", type=int, choices=(1, 2), default=1)
    parser.add_argument("--fast-fly", action="store_true")
    parser.add_argument("--tiles", nargs="+", default=["t0_0_0", "t0_0_-2", "t0_1_0"])
    parser.add_argument("--passes", nargs="+")
    parser.add_argument(
        "--warmed-cache",
        action="store_true",
        help="Measure cached arrival and frames without compiling geometry",
    )
    args = parser.parse_args()
    if args.warmed_cache:
        return warmed_cache()
    if not args.client_only and not (args.commit and args.reuse_caches):
        parser.error("--commit and --reuse-caches are required for generator measurements")
    generator = [
        sys.executable,
        "scripts/dev/probe_texture_speed.py",
        "--commit",
        args.commit,
        "--reuse-caches",
        args.reuse_caches,
        "--tiles",
        *args.tiles,
        "--audit",
    ]
    if args.passes:
        generator += ["--passes", *args.passes]

    def render():
        routes = [("drive-50", "120", "50"), ("fly-200", "60", "200")]
        if args.fast_fly:
            routes.append(("fast-fly", "30", "500"))
        for round_index in range(args.rounds):
            for label, seconds, speed in routes:
                label = f"{args.label_prefix}{label}" + (
                    f"-{round_index + 1}" if args.rounds > 1 else ""
                )
                command = ["node", "game/scripts/tile-arrival-profile.mjs", label, seconds, speed]
                if not args.browser_slot_held:
                    command = [
                        sys.executable,
                        "scripts/dev/host_slots.py",
                        "browser",
                        "--",
                        *command,
                    ]
                run = subprocess.run(command)
                if run.returncode:
                    return run.returncode
        return 0

    if args.client_only:
        return render()

    # The outer post-deploy runner owns the hard 600s deadline and one build slot;
    # the browser also takes its GPU slot. OCI and the local renderer are independent.
    with ThreadPoolExecutor(max_workers=2) as pool:
        builds = pool.submit(subprocess.run, generator)
        client = pool.submit(render)
        return int(builds.result().returncode != 0 or client.result() != 0)


if __name__ == "__main__":
    raise SystemExit(main())
