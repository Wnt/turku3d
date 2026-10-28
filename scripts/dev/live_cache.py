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
    "tile-validate": (
        "pipeline/*",
        "tools/validator/*",
        "game/src/shared/col/*",
        "game/src/vehicle/*",
        "mk/tile.mk",
        "Makefile",
        "scripts/dev/live*",
    ),
    "car-report": (
        "pipeline/*",
        "tools/validator/*",
        "game/src/shared/col/*",
        "game/src/vehicle/*",
        "mk/tile.mk",
        "scripts/dev/live*",
    ),
    "game-build": ("game/*", "Makefile", "scripts/dev/live*"),
}


def digest(repo, work, published, stage, previous, scope=None):
    h = hashlib.sha256(
        json.dumps(
            {"v": 2, "stage": stage, "previous": previous, "scope": scope}, sort_keys=True
        ).encode()
    )
    patterns = INPUTS.get(stage, ("pipeline/*", "game/*", "qa/*", "Makefile", "scripts/dev/live*"))
    for name in sorted(s.git("ls-files", cwd=repo).splitlines()):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            h.update(name.encode() + b"\0" + (repo / name).read_bytes() + b"\0")
    # The playable tile list decides which tiles every stage builds and checks.
    listed = published / "playable-tiles.json"
    h.update(listed.read_bytes() if listed.is_file() else b"missing:playable-tiles")
    # Source manifests contain hashes for the large immutable data products. A
    # changed inventory also invalidates the cache, including missing files.
    data_roots = [published / "data-manifests", work / "textures/assembly"]
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


def prune(published, stage, keep=3):
    """Keep the three most recently stored/used keys; serialize with clones and restores."""
    root = directory(published, stage, "")
    with s.lock(root / ".retention.lock"):
        _prune(root, keep)


def _prune(root, keep):
    entries = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
    complete = sorted(
        (p for p in entries if (p / "result.json").is_file() and not p.name.endswith(".tmp")),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    )
    for path in entries:
        if path not in complete[:keep]:
            shutil.rmtree(path)


def restore(published, work, stage, key):
    with s.lock(directory(published, stage, "") / ".retention.lock"):
        return _restore(published, work, stage, key)


def _restore(published, work, stage, key):
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
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        from live_build import _clone

        _clone(source / relative, target)
    os.utime(source, None)
    return report


def store(published, work, stage, key, report):
    with s.lock(directory(published, stage, "") / ".retention.lock"):
        _store(published, work, stage, key, report)
        _prune(directory(published, stage, ""), 3)


def _store(published, work, stage, key, report):
    target = directory(published, stage, key)
    if target.exists():
        os.utime(target, None)
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
