"""Reference-based retention for draft products only; never traverses the input data store."""

import argparse
import fcntl
import functools
import json
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

GRACE_SECONDS = 15 * 60
PAYLOAD = re.compile(r"(?:vis\.[0-9a-f]{64}\.glb|roads\.[0-9a-f]{64}\.json|col/c_-?\d+_-?\d+\.[0-9a-f]{64}\.col)")
TILE = re.compile(r"t0_-?\d+_-?\d+")


def write_json(path, value):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value) + "\n")
    tmp.replace(path)


def references(value):
    if isinstance(value, dict):
        return set().union(set(), *(references(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(references(v) for v in value))
    return {value} if isinstance(value, str) and PAYLOAD.fullmatch(value) else set()


def current(out):
    refs = set()
    for name in ("manifest.json", "collision.json", "first.json", "last-full.json"):
        path = out / name
        if path.exists():
            refs.update(references(json.loads(path.read_text())))
    return refs


@contextmanager
def tile_lock(out, blocking=True):
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".retention.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True


def remember(out, now, refs=None):
    """Persist the old public pointers before either publication can replace them."""
    path = out / ".retention.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    rows = [r for r in rows if r["until"] > now]
    refs = sorted(current(out) if refs is None else refs)
    rows = [r for r in rows if r["files"] != refs]
    if refs:
        rows.append(dict(until=now + GRACE_SECONDS, files=refs))
    write_json(path, rows)


def prune_tile(out, now=None):
    """Caller holds the tile lock, shared with publication and cancelled-build recovery."""
    now = time.time() if now is None else now
    keep = current(out)  # Invalid manifests fail closed: nothing is deleted.
    path = out / ".retention.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    if not path.exists() and keep:
        # Legacy manifests have no history: conservatively retain the newest spare
        # of each payload family for one grace interval, even if its bytes are old.
        spare = {}
        candidates = [*out.glob("vis.*.glb"), *out.glob("roads.*.json")]
        if not (out / "col").is_symlink():
            candidates.extend(out.glob("col/*.col"))
        for file in candidates:
            name = file.relative_to(out).as_posix()
            if file.is_symlink() or not PAYLOAD.fullmatch(name) or name in keep:
                continue
            family = name.split(".", 1)[0]
            if family not in spare or file.stat().st_mtime > spare[family].stat().st_mtime:
                spare[family] = file
        rows.append(dict(until=now + GRACE_SECONDS,
                         files=[p.relative_to(out).as_posix() for p in spare.values()]))
    rows = [r for r in rows if r["until"] > now]
    for row in rows:
        keep.update(row["files"])
    deleted = 0
    for pattern in ("vis.*.glb", "roads.*.json", "col/*.col"):
        if pattern.startswith("col/") and (out / "col").is_symlink():
            continue
        for file in out.glob(pattern):
            name = file.relative_to(out).as_posix()
            # On first deployment no historical manifests exist. Protect recent payloads
            # for a full client grace interval; older generations are unreachable.
            if (file.is_symlink() or not PAYLOAD.fullmatch(name) or name in keep
                    or file.stat().st_mtime > now - GRACE_SECONDS):
                continue
            deleted += file.stat().st_size
            file.unlink()
    write_json(path, rows)
    return deleted


def remember_full(out):
    """Retain the last complete textured generation until its full replacement."""
    path = out / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text())
        if manifest.get("pass_kind") == "full":
            write_json(out / "last-full.json", manifest)


def managed_build(operation):
    @functools.wraps(operation)
    def build(tile_id, out, **kwargs):
        out = Path(out)
        with tile_lock(out):
            remember_full(out)
            previous = current(out)
            remember(out, time.time(), previous)
            from turku3d.stage_cache import finish_report

            report = out / "stage-times.json"
            write_json(report, {})
            try:
                result = operation(tile_id, out, **kwargs)
            except BaseException as error:
                finish_report(report, "failed", error)
                raise
            finish_report(report, "complete")
            remember_full(out)
            remember(out, time.time(), previous)
            prune_tile(out)
            return result
    return build


def _prune(root):
    """Online sweep skips active tiles. Only known generated stores are touched."""
    from turku3d.stage_cache import prune_entries

    root = Path(root)
    deleted = 0
    for out in root.glob("t0_*_*"):
        if out.is_symlink() or not out.is_dir() or not TILE.fullmatch(out.name):
            continue
        with tile_lock(out, blocking=False) as acquired:
            if not acquired:
                continue
            deleted += prune_tile(out)
            stages = root / ".stages" / out.name
            directories = stages.glob("*") if stages.is_dir() else []
            for directory in directories:
                if directory.is_dir() and not directory.is_symlink():
                    deleted += prune_entries(directory)
            # No active package references these after the tile lock is acquired.
            for name in (".stage",):
                directory = out / name
                if directory.is_dir() and not directory.is_symlink():
                    deleted += sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
                    shutil.rmtree(directory)
    # Shared area stages are scoped by their source path, then retain two keys.
    # Include legacy unscoped namespaces during the one-time migration.
    area = root.parent / "cache" / "coarse-area"
    if area.is_dir() and not area.is_symlink():
        for directory in area.glob("*"):
            if not directory.is_dir() or directory.is_symlink():
                continue
            if any(directory.glob("*.pkl")):
                deleted += prune_entries(directory)
            else:
                for stage in directory.glob("*"):
                    if stage.is_dir() and not stage.is_symlink():
                        deleted += prune_entries(stage)
    # New packages use tile-local intermediates. The old shared encoding cache has
    # no remaining readers once the old producer stops and the new slot activates.
    legacy = root / ".texture-cache"
    if legacy.is_dir() and not legacy.is_symlink():
        deleted += sum(p.stat().st_size for p in legacy.rglob("*") if p.is_file())
        shutil.rmtree(legacy)
    return dict(deleted_bytes=deleted)


def prune(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".retention.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(deleted_bytes=0, busy=True)
        return _prune(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prune(args.root)))


if __name__ == "__main__":
    main()
