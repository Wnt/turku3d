"""Dependency-ordered subprocess runner. Successful records survive partial failures."""

import concurrent.futures
import fcntl
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from turku3d import config as C
from turku3d.fetch.common import atomic_write_text

from .estimate import guard_quota

ORDER = (
    "fetch",
    "ground",
    "structures",
    "buildings",
    "facades",
    "ground-textures",
    "assemble",
    "validate",
)


@dataclass
class Step:
    name: str
    commands: list[list[str]] = field(default_factory=list)
    inputs: list[Path] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)
    reason: str = ""
    jobs: list[dict] = field(default_factory=list)


def fingerprint(paths):
    h = hashlib.sha256()
    for path in sorted(set(paths)):
        h.update(str(path).encode())
        files = (
            sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
            if path.is_dir()
            else [path]
        )
        for p in files:
            h.update(str(p).encode())
            if not p.exists():
                h.update(b"missing")
            elif p.suffix in (".json", ".jsonl", ".py", ".yaml", ".ts", ".toml"):
                h.update(p.read_bytes())
            else:
                st = p.stat()
                h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()


def execute(steps, root, workers=4, quota_cap=100, usage=None, env=None):
    root.mkdir(parents=True, exist_ok=True)
    results = []
    # All region areas share layer/dist outputs: serialize whole runs within this sandbox.
    lockpath = root.parent / ".region.lock"
    with lockpath.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for step in steps:
            start = time.monotonic()
            record = root / f"{step.name}.json"
            key = hashlib.sha256(
                (fingerprint(step.inputs) + json.dumps(step.commands)).encode()
            ).hexdigest()
            previous = json.loads(record.read_text()) if record.exists() else {}
            row = {"step": step.name, "status": "skipped", "reason": step.reason}
            if step.commands:
                complete = bool(step.outputs) and all(p.exists() for p in step.outputs)
                if (
                    complete
                    and previous.get("key") == key
                    and previous.get("outputs") == fingerprint(step.outputs)
                ):
                    row.update(
                        status="cached", reason="inputs, code and output manifests unchanged"
                    )
                else:
                    try:
                        if step.jobs:
                            guard_quota(step.jobs, usage() if usage else None, quota_cap)

                        def run(item, step_name=step.name):
                            index, command = item
                            log = root / f"{step_name}-{index}.log"
                            with log.open("w") as out:
                                out.write(json.dumps(command) + "\n")
                                out.flush()
                                result = subprocess.run(
                                    command,
                                    cwd=C.REPO_ROOT,
                                    env=env,
                                    stdout=out,
                                    stderr=subprocess.STDOUT,
                                )
                            return result.returncode, str(log)

                        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                            runs = list(pool.map(run, enumerate(step.commands)))
                        row["logs"] = [log for _, log in runs]
                        if any(code for code, _ in runs):
                            raise RuntimeError("command failed; see per-step logs")
                        if not step.outputs or not all(p.exists() for p in step.outputs):
                            raise RuntimeError("step returned success without declared outputs")
                        atomic_write_text(
                            record, json.dumps({"key": key, "outputs": fingerprint(step.outputs)})
                        )
                        row.update(status="done", reason="")
                    except (RuntimeError, OSError) as error:
                        row.update(status="failed", reason=str(error))
            row["seconds"] = round(time.monotonic() - start, 3)
            results.append(row)
            print(f"{step.name}: {row['status']} {row['reason']}", flush=True)
            atomic_write_text(root / "summary.json", json.dumps(results, indent=2))
            if row["status"] == "failed":
                break
    return results


def plan(area, source, workers, report):
    tiles = [t.id for t in area.visual_tiles]
    py = [sys.executable, "-m"]
    layers = C.work_root() / "build/layers"
    dist = C.dist_dir() / "tiles"
    code = C.REPO_ROOT / "pipeline/turku3d"
    raw = C.raw_dir()
    sources = [raw / k for k in ("laz", "wfs", "ortho", "osm")]
    common = [code / "config.py", code / "region", C.DATA_MANIFESTS / "areas" / f"{area.name}.yaml"]
    steps = [
        Step(
            "fetch",
            [py + ["turku3d.region.dispatch", "fetch", source]],
            sources + common + [code / "fetch"],
            [report / "fetch-ready.json"],
        )
    ]
    for name, module, args in (
        ("ground", "ground.build", ["--tiles", *tiles, "--source-area", source]),
        ("structures", "structures", []),
        ("buildings", "buildings.build", ["--source-area", source, "--jobs", str(workers)]),
    ):
        entry = code / (module.replace(".", "/") + ".py")
        available = entry.exists() or (code / name / "__main__.py").exists()
        # These producers currently hard-code the office bounds, not an arbitrary region.
        supported = name == "ground" or area.name == "office-block"
        steps.append(
            Step(
                name,
                [py + ["turku3d.region.dispatch", "build", source, module, *args]]
                if available and supported
                else [],
                sources + common + [code / name, layers / "ground/MANIFEST.json"]
                if name != "ground"
                else sources + common + [code / name],
                [layers / name / "MANIFEST.json", layers / name],
                "producer unavailable" if not available else "producer supports office-block only",
            )
        )
    for name in ("facades", "ground-textures"):
        steps.append(Step(name, reason="no area-aware producer adapter published"))
    assembler = "assemble" if (code / "package/assemble.py").exists() else "stub_tile"
    steps.append(
        Step(
            "assemble",
            [
                py
                + [
                    "turku3d.region.dispatch",
                    "assemble",
                    source if assembler == "stub_tile" else area.name,
                    assembler,
                    *tiles,
                ]
            ],
            sources
            + common
            + [code / "package", code / "ground", layers, C.work_root() / "textures"],
            [dist / "manifest.json", dist],
        )
    )
    reports = [report / f"validate-{t}.json" for t in tiles]
    steps.append(
        Step(
            "validate",
            [
                [
                    "node",
                    "tools/validator/src/cli.ts",
                    "--walkers",
                    "2000",
                    "--out",
                    str(out),
                    str(dist / "collision" / tile),
                ]
                for tile, out in zip(tiles, reports, strict=True)
            ],
            [dist, C.REPO_ROOT / "tools/validator/src", C.REPO_ROOT / "game/src/shared/col"],
            reports,
        )
    )
    return steps
