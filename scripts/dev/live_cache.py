"""Shared verified live-stage artifacts, keyed by declared source and manifest inputs."""

import filecmp
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess

import sandbox as s

OUTPUTS = {
    "refresh": ("build/layers", "textures"),
    "tile-build": ("dist/tiles",),
    "game-build": ("build/game", "build/check-game"),
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
    "game-build": (
        "game/*",
        "mk/game.mk",
        "tools/dev/environment.mjs",
        "Makefile",
        "scripts/dev/live*",
    ),
}


def game_key(repo, env):
    """Bundle inputs only: tiles, layers and facade approvals are fetched at runtime."""
    h = hashlib.sha256(b"game-build-v1\0")
    patterns = (
        *INPUTS["game-build"],
        "tools/dev/localhost/*",
        "tools/dev/http.mjs",
        "scripts/dev/game_build.py",
    )
    names = set(
        s.git("ls-files", "--cached", "--others", "--exclude-standard", cwd=repo).splitlines()
    )
    names.update(str(p.relative_to(repo)) for p in (repo / "game").glob(".env*"))
    for name in sorted(names):
        path = repo / name
        if path.is_file() and (
            any(fnmatch.fnmatchcase(name, p) for p in patterns) or name.startswith("game/.env")
        ):
            h.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    settings = {
        k: v for k, v in env.items() if k.startswith("VITE_") or k in ("NODE_ENV", "NODE_OPTIONS")
    }
    h.update(json.dumps(settings, sort_keys=True).encode())
    h.update(subprocess.check_output(["node", "--version"]))
    installed = repo / "game/node_modules/.package-lock.json"
    h.update(installed.read_bytes() if installed.is_file() else b"missing:node_modules")
    return h.hexdigest()


def digest(repo, work, published, stage, previous, scope=None):
    if stage == "game-build":
        return game_key(repo, {**os.environ, "VITE_PUBLIC_RELEASE": "1"})
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


def same_tree(old, new):
    if not old.is_dir() or not new.is_dir():
        return False
    if any(path.is_symlink() for root in (old, new) for path in root.rglob("*")):
        return False
    before = {p.relative_to(old) for p in old.rglob("*") if p.is_file()}
    after = {p.relative_to(new) for p in new.rglob("*") if p.is_file()}
    return before == after and all(
        filecmp.cmp(old / name, new / name, shallow=False) for name in before
    )
