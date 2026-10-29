"""Re-score retained quarters after deployment, with paired wall-patch comparisons.

Run under host_slots.py build. This never captures a browser, substitutes a different
quarter, or counts an abstention as an improvement. Outputs remain sandbox-private.
"""

import argparse
import hashlib
import json
import os
import shlex
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "textures"))
from turkutex.wall_palette import lab  # noqa: E402


def rescore(row, root):
    from gmaps_round3 import score_one

    return score_one(row, product_root=root)


def paired(before, after):
    rows = []
    for key in before.keys() & after.keys():
        patches = before[key].keys() & after[key].keys()
        if len(patches) < 10:
            continue
        # Shared immutable reference IDs protect against support changing after a build.
        if any(before[key][p]["reference"] != after[key][p]["reference"] for p in patches):
            raise ValueError(f"Reference changed in {key}")
        reference = lab(np.median([before[key][p]["reference"] for p in patches], axis=0))
        error = {}
        for label, groups in (("before", before), ("after", after)):
            own = lab(np.median([groups[key][p]["own"] for p in patches], axis=0))
            error[label] = float(np.linalg.norm(own - reference))
        rows.append(dict(group=key, shared_reference_patches=len(patches), **error))
    return rows


def verified_pair(before_state, result):
    before = json.loads(Path(before_state["report"]).read_text())
    path = Path(result["report"])
    after = json.loads(path.read_text())
    if before["reference_sha256"] != after["reference_sha256"]:
        raise ValueError("Decoded reference manifest changed")
    expected = result["own_sha256"]
    source = path.parent.parent / f"own-score-{expected[:16]}" / "source.glb"
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if after["build_sha256"] != expected or digest != expected:
        raise ValueError("Retained own source changed")
    return paired(before["facades"]["colour_groups"], after["facades"]["colour_groups"])


def resume_checkpoint(job, coverage, output, selected, expected_quarters):
    """Reuse immutable measurements only for the same model and explicit original inputs."""
    request = json.loads((job / "request.json").read_text())
    if os.environ.get("TURKU3D_SANDBOX") and request["name"] != os.environ["TURKU3D_SANDBOX"]:
        raise ValueError("Checkpoint belongs to another task")
    command = shlex.split(request["commands"][0])
    for option, expected in (
        ("--coverage", coverage),
        ("--out", output),
        ("--expected-quarters", expected_quarters),
    ):
        if str(expected) != command[command.index(option) + 1]:
            raise ValueError(f"Checkpoint {option} differs")
    repo = Path(__file__).resolve().parents[2]
    for name in ("wall_palette.json", "wall_palette.py", "facade_fast_render.py"):
        relative = Path("textures/turkutex") / name
        if (repo / relative).read_bytes() != (job / "repo" / relative).read_bytes():
            raise ValueError(f"Checkpoint colour source changed: {name}")
    old = json.loads((output / "coverage.json").read_text())["quarters"]
    results, pairs = {}, {}
    for key, result in old.items():
        if key in selected and result["status"] == "scored":
            pairs[key] = verified_pair(selected[key], result)
            results[key] = result
    return results, pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", required=True, type=Path, help="Frozen before coverage.json")
    parser.add_argument("--product-root", required=True, type=Path, help="Retained captures/ root")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--expected-quarters", type=int, default=57)
    parser.add_argument(
        "--resume-job", type=Path, help="Prior owned check directory with frozen colour source"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    states = json.loads(args.coverage.read_text())["quarters"]
    selected = {key: row for key, row in states.items() if row["status"] == "scored"}
    if len(selected) != args.expected_quarters:
        raise ValueError(
            f"Expected {args.expected_quarters} measured baseline quarters, got {len(selected)}"
        )
    results, pairs = {}, {}
    if args.resume_job:
        results, pairs = resume_checkpoint(
            args.resume_job, args.coverage, args.out, selected, args.expected_quarters
        )
        print(json.dumps(dict(reused_quarters=len(results))), flush=True)
    workers = max(1, min(2, int(os.environ.get("TURKU3D_JOBS", "2"))))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = {
            pool.submit(rescore, dict(row, coverage_id=key), args.product_root): key
            for key, row in selected.items()
            if key not in results
        }
        for job in as_completed(jobs):
            key = jobs[job]
            try:
                result = job.result()
                results[key] = result
                if result["status"] == "scored":
                    pairs[key] = verified_pair(selected[key], result)
            except Exception as error:
                results[key] = dict(status="error", error=str(error))
            (args.out / "coverage.json").write_text(
                json.dumps(dict(quarters=results), indent=2) + "\n"
            )
            (args.out / "paired-walls.json").write_text(json.dumps(pairs, indent=2) + "\n")
            print(json.dumps(dict(quarter=key, **results[key])), flush=True)
    walls = [r for rows in pairs.values() for r in rows]
    summary = dict(
        expected_quarters=args.expected_quarters,
        quarters=len(results),
        scored=sum(r["status"] == "scored" for r in results.values()),
        paired_walls=len(walls),
        **{
            side: dict(
                mean=float(np.mean([r[side] for r in walls])),
                median=float(np.median([r[side] for r in walls])),
            )
            for side in ("before", "after")
        },
    )
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    if summary["scored"] != args.expected_quarters or not walls:
        raise SystemExit("Paired comparison incomplete; inspect coverage.json")


if __name__ == "__main__":
    main()
