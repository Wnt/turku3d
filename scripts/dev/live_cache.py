"""Shared verified live-stage artifacts, keyed by declared source and manifest inputs."""

import fnmatch
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

INPUTS = {
    "refresh": ("pipeline/*", "textures/*", "Makefile", "scripts/dev/live*"),
    "tile-build": ("pipeline/*", "textures/*", "Makefile", "scripts/dev/live*"),
    "game-build": ("game/*", "Makefile", "scripts/dev/live*"),
    "collision-sweep": ("pipeline/*", "game/*", "Makefile", "scripts/dev/live*"),
    "collision-audit": ("pipeline/*", "game/*", "Makefile", "scripts/dev/live*"),
    "chrome": ("game/*", "qa/*", "Makefile", "scripts/dev/live*"),
}


def digest(repo, work, published, stage, previous):
    h = hashlib.sha256(
        json.dumps({"v": 2, "stage": stage, "previous": previous}, sort_keys=True).encode()
    )
    patterns = INPUTS.get(stage, ("pipeline/*", "game/*", "qa/*", "Makefile", "scripts/dev/live*"))
    for name in sorted(s.git("ls-files", cwd=repo).splitlines()):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            h.update(name.encode() + b"\0" + (repo / name).read_bytes() + b"\0")
    # Source manifests contain hashes for the large immutable data products. A
    # changed inventory also invalidates the cache, including missing files.
    data_roots = [published / "data-manifests", published / "facade-approvals"]
    if stage != "refresh":
        data_roots.extend((work / "build/layers", work / "textures"))
    for root in data_roots:
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
