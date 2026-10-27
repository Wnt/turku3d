"""Reuse verified synthetic browser tiles while their complete producer inputs match."""

import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

from turku3d.package.manifest import verify_tree


def build(root, out):
    layers = root / ".cache/gate-layers"
    inputs = root / ".cache/gate-inputs"
    subprocess.run([sys.executable, "-m", "fixtures.make_layers", "--out", str(layers)], check=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "turku3d.package.assemble",
            "--layers",
            str(layers),
            "--out",
            str(out),
            "--library",
            str(inputs / "library"),
            # Synthetic GLBs embed their own preview texture. Never bind them to
            # the sandbox's production ground splats and collision fingerprints.
            "--ground",
            str(inputs / "ground-preview"),
            "--facades",
            str(inputs / "facades"),
            # Synthetic layers are not the office block; its real-layer
            # acceptance gate does not apply to them.
            "--area",
            "playable-tiles",
            "--tile",
            "t0_0_0",
            "t0_0_1",
            "t0_1_0",
            "t0_1_1",
        ],
        check=True,
    )


def main():
    root = Path(__file__).resolve().parents[2]
    out = Path(sys.argv[1])
    inputs = sorted((root / "pipeline/turku3d").rglob("*.py"))
    inputs += sorted((root / "pipeline/turku3d").rglob("*.json"))
    inputs += sorted((root / "pipeline/turku3d").rglob("*.mjs"))
    inputs += sorted((root / "pipeline/tests/fixtures").rglob("*.py"))
    inputs += [
        Path(__file__).resolve(),
        root / "pipeline/env/package-lock.json",
        root / "tools/validator/package-lock.json",
        root / "pipeline/pyproject.toml",
    ]
    inputs += sorted(p for p in (root / ".cache/gate-inputs").rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for path in inputs:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    for package in ("numpy", "scipy", "pillow"):
        digest.update(f"{package}:{importlib.metadata.version(package)}".encode())
    key = digest.hexdigest()
    stamp = out / "gate-inputs.json"
    if stamp.exists() and json.loads(stamp.read_text()) == key and not verify_tree(out):
        print("Reusing hash-verified synthetic browser tile")
        return
    build(root, out)
    errors = verify_tree(out)
    if errors:
        raise ValueError(errors)
    stamp.write_text(json.dumps(key) + "\n")
    print("Built and verified synthetic browser tile")


if __name__ == "__main__":
    main()
