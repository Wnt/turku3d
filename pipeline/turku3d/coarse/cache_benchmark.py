"""Manual real-data cold/warm/clean cache comparison; run through the host build scheduler."""

import argparse
import hashlib
import json
import shutil
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from turku3d import config as C
from turku3d.stage_cache import StageCache

from .build import build


def payloads(manifest, directory):
    entries = [*manifest["visual"].values(), *manifest["collision"].values()]
    if manifest.get("roads"):
        entries.append(manifest["roads"])
    result = {}
    for entry in entries:
        data = (directory / entry["url"]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        assert digest == entry["sha256"], entry["url"]
        result[entry["url"]] = digest
    for path in sorted((directory.parent / "shared").glob("*.ktx2")):
        result["shared/" + path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile", default="t0_0_0")
    parser.add_argument("--out", type=Path, default=C.work_root() / "reports/incremental-tiles")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report, hashes = {}, []
    original_run = StageCache.run

    def changed_stage(cache, name, code, inputs, operation):
        # Simulate a source-only edit in this one stage. Equal output must stop propagation.
        return original_run(
            cache, name, code + "-benchmark-edit" if name == "markings" else code, inputs, operation
        )

    for label, root in (
        ("cold", "cached"),
        ("warm", "cached"),
        ("one_stage_changed", "cached"),
        ("clean", "clean"),
    ):
        directory = args.out / root / args.tile
        if label in ("cold", "clean") and directory.parent.exists():
            shutil.rmtree(directory.parent)
        started = time.perf_counter()
        with (
            patch.object(StageCache, "run", changed_stage)
            if label == "one_stage_changed"
            else nullcontext()
        ):
            manifest = build(args.tile, directory)
        report[label] = dict(
            wall_seconds=round(time.perf_counter() - started, 3),
            seconds=manifest["seconds"],
            stages=json.loads((directory / "stage-times.json").read_text()),
        )
        hashes.append(payloads(manifest, directory))
        (args.out / "timings.json").write_text(json.dumps(report, indent=2) + "\n")
        print(label, report[label], flush=True)
    assert hashes[0] == hashes[1] == hashes[2] == hashes[3], "cached and clean payloads differ"
    misses = [
        name for name, stage in report["one_stage_changed"]["stages"].items() if not stage["cached"]
    ]
    assert misses == ["markings"], f"unexpected stage invalidation: {misses}"
    report["changed_stage"] = "markings source identity (injected no-op edit)"
    report["byte_identical"] = True
    report["payloads"] = len(hashes[0])
    (args.out / "timings.json").write_text(json.dumps(report, indent=2) + "\n")
    print("All shipped payloads byte-identical", flush=True)


if __name__ == "__main__":
    main()
