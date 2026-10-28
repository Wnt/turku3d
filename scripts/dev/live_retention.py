"""Bound live releases and stage caches after publication, preserving active references."""

import os
import shutil
from pathlib import Path

import live_cache
import sandbox as s


def after_publish(published, release, previous):
    s.write_json(release / ".publication.json", {"previous": previous.name})
    os.utime(release, None)
    prune_releases(published, protected=(previous,))
    cache = published / "cache/tool-stages/live"
    if cache.is_dir():
        for stage in cache.iterdir():
            if stage.is_dir() and not stage.is_symlink():
                live_cache.prune(published, stage.name)


def prune_releases(published, protected=()):
    """Retain live, its previous two publications, and active batch/rollback references.

    Called under the integrator publication lock so prepare/repoint cannot race deletion.
    Reference names may be commit prefixes (a data release adds its data key).
    """
    import integrator

    published = published.resolve()
    root = published / "live-releases"
    live = (published / "live").resolve()
    references = set(str(p) for p in protected)
    for lane in integrator.LANES.values():
        if row := integrator.running(lane):
            references.update(
                str(row[key])
                for key in ("from", "to", "previous_release", "release")
                if row.get(key)
            )
    for link in published.glob("*rollback*"):
        if link.is_symlink():
            references.add(str(link.resolve()))
    entries = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and p != live),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    )
    complete = [p for p in entries if (p / "live-build.json").is_file() or p.name == "legacy"]
    keep = {live}
    cursor = live
    for _ in range(2):
        previous = s.read_json(cursor / ".publication.json", {}).get("previous")
        candidate = root / previous if previous and Path(previous).name == previous else None
        if candidate not in complete or candidate in keep:
            # Bootstrap releases created before publication ancestry was recorded.
            candidate = next((p for p in complete if p not in keep), None)
        if candidate is None:
            break
        keep.add(candidate)
        cursor = candidate
    for path in entries:
        if path in keep or any(
            str(path) == ref or path.name == ref or path.name.startswith(ref + "-")
            for ref in references
        ):
            continue
        shutil.rmtree(path)
