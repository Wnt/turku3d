"""Shared verified live-stage artifacts, keyed by declared source and manifest inputs."""

import hashlib
import json
import os
import shutil

import sandbox as s

OUTPUTS = {
    "refresh": ("build/layers", "textures"),
    "tile-build": ("dist/tiles",),
    "game-build": ("build/game",),
}


def digest(repo, work, published, stage, previous):
    tree = s.git("rev-parse", "HEAD^{tree}", cwd=repo)
    h = hashlib.sha256(
        json.dumps(
            {"v": 1, "tree": tree, "stage": stage, "previous": previous}, sort_keys=True
        ).encode()
    )
    # Source manifests contain hashes for the large immutable data products. A
    # changed inventory also invalidates the cache, including missing files.
    for root in (
        published / "data-manifests",
        published / "facade-approvals",
        work / "build/layers",
        work / "textures",
    ):
        if not root.exists():
            h.update(f"missing:{root.name}".encode())
            continue
        for file in sorted(root.rglob("*")):
            if file.is_file() and file.suffix.lower() in (".json", ".toml", ".txt"):
                h.update(str(file.relative_to(root)).encode() + b"\0")
                h.update(file.read_bytes())
    return h.hexdigest()


def directory(published, stage, key):
    return published / "cache/tool-stages/live" / stage / key


def restore(published, work, stage, key):
    source = directory(published, stage, key)
    result = source / "result.json"
    if not result.is_file():
        return None
    report = json.loads(result.read_text())
    for relative in OUTPUTS.get(stage, ()):
        cached = source / relative
        if not cached.is_dir():
            return None
    for relative in OUTPUTS.get(stage, ()):
        target = work / relative
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        from live_build import _clone

        _clone(source / relative, target)
    return report


def store(published, work, stage, key, report):
    target = directory(published, stage, key)
    if target.exists():
        return
    temp = target.with_name(key + f".{os.getpid()}.tmp")
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=False)
    try:
        from live_build import _clone

        for relative in OUTPUTS.get(stage, ()):
            source = work / relative
            if not source.is_dir():
                return
            _clone(source, temp / relative)
        (temp / "result.json").write_text(json.dumps(report))
        try:
            temp.rename(target)
        except OSError:
            if not target.exists():
                raise
    finally:
        if temp.exists():
            shutil.rmtree(temp)
