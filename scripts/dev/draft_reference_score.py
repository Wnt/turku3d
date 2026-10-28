#!/usr/bin/env python3
"""Build immutable draft pairs and print private, matched reference measurements."""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

TILES = ("t0_1_0", "t0_2_-1", "t0_-1_1", "t0_-2_2", "t0_3_-5")
REPO = Path(__file__).resolve().parents[2]
# Street-visible texture and bridge errors come first. Roof/height scores are information.
METRIC_ORDER = (
    "ground_colour_dE76",
    "facade_colour_dE76",
    "bridge_deck_existence",
    "bridge_height_mae_m",
    "duplicated_structures",
    "missing_structures",
    "ground_height_rmse_m",
    "road_deck_existence",
    "road_height_mae_m",
    "footprint_iou",
    "masked_dsm_rmse_m",
    "roof_height_mae_m",
    "roof_form_agreement",
    "roof_colour_dE76",
)
INFORMATIONAL = {
    "masked_dsm_rmse_m",
    "roof_height_mae_m",
    "roof_form_agreement",
    "roof_colour_dE76",
}


def metric_priority(name):
    return METRIC_ORDER.index(name) if name in METRIC_ORDER else len(METRIC_ORDER)


def private_tool():
    value = os.environ.get("GMAPS", str(Path.home() / "turku3d/private/gmaps-extract/gmaps"))
    return shutil.which(value)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def snapshot(version, target, repo=REPO):
    """A ref or a dirty worktree, captured once; builders never read a moving checkout."""
    source = repo if version == "worktree" else Path(version).expanduser()
    if (source / ".draft-source.json").is_file():
        shutil.copytree(source, target, symlinks=True)
        return json.loads((target / ".draft-source.json").read_text())
    if source.is_dir():
        source = source.resolve()
        head = git(source, "rev-parse", "HEAD").decode().strip()
        names = git(source, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        target.mkdir()
        for name in set(names.decode().strip("\0").split("\0")):
            path = source / name
            if not name or not path.exists():
                continue
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                dest.symlink_to(os.readlink(path))
            else:
                shutil.copy2(path, dest)
        identity = {"worktree": str(source), "head": head}
    else:
        head = git(repo, "rev-parse", "--verify", version + "^{commit}").decode().strip()
        with tarfile.open(fileobj=io.BytesIO(git(repo, "archive", head))) as archive:
            archive.extractall(target, filter="data")
        identity = {"commit": head}
    # JS packagers resolve packages relative to their source tree. No install or shared writes.
    for relative in ("node_modules", "game/node_modules"):
        dest = target / relative
        dependencies = repo / relative
        if not dest.exists() and dependencies.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(dependencies.resolve(), target_is_directory=True)
    (target / ".draft-source.json").write_text(json.dumps(identity) + "\n")
    return identity


def build_version(side, version, directory, tiles, repo=REPO):
    root = directory / side
    root.mkdir()
    source = root / "source"
    identity = snapshot(version, source, repo)
    env = os.environ.copy()
    env.update(
        REPO=str(source),
        TURKU3D_WORK=str(root),
        PYTHONPATH=str(source / "pipeline"),
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        VECLIB_MAXIMUM_THREADS="1",
    )
    seconds = {}
    # Each side owns its prepared cache and shared textures. Parallelise sides, not writes
    # to a side's texture library. Admission is per build, never across both workers.
    for tile in tiles:
        command = [
            sys.executable,
            str(repo / "scripts/dev/host_slots.py"),
            "build",
            "--",
            "bash",
            str(source / "pipeline/env/run.sh"),
            "python",
            "-m",
            "turku3d.coarse",
            "build",
            tile,
            "--out",
            str(root / "coarse" / tile),
        ]
        with (root / f"{tile}.build.log").open("w") as log:
            subprocess.run(command, cwd=source, env=env, stdout=log, stderr=log, check=True)
        manifest = json.loads((root / "coarse" / tile / "manifest.json").read_text())
        seconds[tile] = manifest["seconds"]["total"]
    return {"source": identity, "seconds": seconds}


def metric_rows(tile, result, noise=0.00001):
    """Only paired, finite observations with matched support can establish a verdict."""
    rows = []
    for name in sorted(result["metrics"], key=metric_priority):
        metric = result["metrics"][name]
        before, after = metric["before"], metric["after"]
        a, b = before["value"], after["value"]
        delta = metric.get("delta")
        comparable = (
            a is not None
            and b is not None
            and delta is not None
            and all(math.isfinite(v) for v in (a, b, delta))
            and before.get("samples", 0) > 0
            and before.get("samples") == after.get("samples")
            and before.get("better") in ("lower", "higher")
            and before.get("better") == after.get("better")
        )
        verdict = "no evidence"
        if comparable:
            signed = delta if before["better"] == "lower" else -delta
            verdict = "unchanged" if abs(delta) <= noise else "worse" if signed > 0 else "better"
        rows.append(
            {
                "tile": tile,
                "metric": name,
                "before": a,
                "after": b,
                "delta": delta if comparable else None,
                "verdict": verdict,
                "informational": name in INFORMATIONAL,
            }
        )
    return rows


def table(rows):
    lines = [
        "tile | metric | before | after | delta | verdict",
        "--- | --- | ---: | ---: | ---: | ---",
    ]
    for row in rows:
        values = [row[k] for k in ("tile", "metric", "before", "after", "delta", "verdict")]
        if row.get("informational"):
            values[-1] = f"informational: {values[-1]}"
        lines.append(
            " | ".join(
                "null" if v is None else f"{v:.5f}" if isinstance(v, float) else str(v)
                for v in values
            )
        )
    return "\n".join(lines)


def build_pair(before, after, directory, tiles=TILES, repo=REPO):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            side: pool.submit(build_version, side, version, directory, tiles, repo)
            for side, version in (("before", before), ("after", after))
        }
        return {side: future.result() for side, future in futures.items()}


def compare(before, after, directory, tiles=TILES, noise=0.00001, repo=REPO, context_tiles=()):
    tool = private_tool()
    if not tool:
        print("Private gmaps tool unavailable; nothing scored (optional local installation).")
        return None
    directory.mkdir(parents=True, exist_ok=False)
    build_tiles = tuple(dict.fromkeys([*tiles, *context_tiles]))
    builds = build_pair(before, after, directory, build_tiles, repo)
    rows, errors = [], []
    for tile in tiles:
        command = [
            tool,
            "diff",
            "--tile",
            tile,
            "--before",
            str(directory / "before/coarse"),
            "--after",
            str(directory / "after/coarse"),
        ]
        # Private tool output is never emitted wholesale: it may contain detailed findings.
        with (directory / f"{tile}.diff.stderr.log").open("w") as log:
            proc = subprocess.run(
                command,
                cwd=repo,
                capture_output=False,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
            )
        (directory / f"{tile}.diff.json").write_text(proc.stdout)
        if proc.returncode:
            errors.append(tile)
            print(f"{tile}: unscored; private tool failed ({proc.returncode}); see local report.")
            continue
        rows.extend(metric_rows(tile, json.loads(proc.stdout), noise))
    report = {"builds": builds, "rows": rows, "unscored": errors, "noise": noise}
    (directory / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    output = []
    for tile in tiles:
        output.extend([f"{tile}:", table([row for row in rows if row["tile"] == tile]), ""])
    output.extend(
        [
            "tile | draft seconds before | after | delta",
            "--- | ---: | ---: | ---:",
        ]
    )
    for tile in build_tiles:
        a, b = (builds[side]["seconds"][tile] for side in ("before", "after"))
        output.append(f"{tile} | {a:.3f} | {b:.3f} | {b - a:+.3f}")
    rendered = "\n".join(output)
    (directory / "table.md").write_text(rendered + "\n")
    print(rendered)
    print(f"Private local report: {directory}")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True, help="git ref, worktree, or worktree directory")
    parser.add_argument("--tiles", nargs="+", default=TILES)
    parser.add_argument(
        "--context-tiles",
        nargs="+",
        default=(),
        help="build neighboring own tiles for streamed-scene support; do not score them",
    )
    parser.add_argument(
        "--noise",
        type=float,
        default=0.00001,
        help="absolute paired metric tolerance (default: score rounding unit)",
    )
    args = parser.parse_args(argv)
    if not private_tool():
        print("Private gmaps tool unavailable; nothing scored (optional local installation).")
        return 0
    work = Path(os.environ["TURKU3D_WORK"]).resolve()
    if work == Path(os.environ["TURKU3D_DATA"]).resolve():
        parser.error("source a non-main sandbox environment first")
    if not math.isfinite(args.noise) or args.noise < 0:
        parser.error("noise must be finite and nonnegative")
    reports = work / "reports/draft-reference-score"
    reports.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="pair-", dir=reports)) / "measurement"
    report = compare(
        args.before, args.after, directory, args.tiles, args.noise, context_tiles=args.context_tiles
    )
    return 2 if report and report["unscored"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
