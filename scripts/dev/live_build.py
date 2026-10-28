"""Copy helpers for explicit legacy artifact inspection, outside game delivery."""

import shutil
import subprocess

import sandbox as s
from landing_class import C


def _clone(source, target):
    if target.exists() or target.is_symlink():
        return
    if not source.exists():
        return
    source = source.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # APFS clones keep a private, writable snapshot without copying asset bytes.
    if subprocess.call(
        ["cp", "-cRL", str(source), str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ):
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)


def _replace(source, target):
    """Make target a private clone of source (kept as it is when source is missing)."""
    if not source.exists():
        return
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    _clone(source, target)


def seed(work, published):
    """Seed a reused build sandbox from the live release: layers and textures only when
    missing (refresh updates them in place), the tiles and the game bundle every time (a
    build starts from the live tiles and ships the live game unless it builds its own), and
    the facade approvals."""
    _replace(
        published / "live" / ("check-game" if (published / "live/check-game").is_dir() else "game"),
        work / "build/check-game",
    )
    for relative in ("build/layers", "textures"):
        _clone(published / relative, work / relative)
    for relative in ("dist/tiles", "build/game"):
        _replace(published / relative, work / relative)
    # The data root's tile list is authoritative; a sandbox list would shadow it.
    (work / C.PLAYABLE_TILES_FILE).unlink(missing_ok=True)
    approve(work, published)


def approve(work, published):
    """Replace the work's facade assembly with the approval root's."""
    approvals = published / "facade-approvals/assembly"
    if approvals.is_dir():
        # The approvals are authoritative: a retired facade must leave the release too,
        # so the assembly is replaced rather than merged over the published copy.
        target = work / "textures/assembly"
        _detach(target, work)
        if target.exists():
            shutil.rmtree(target)
        with s.lock(approvals / ".export.lock"):
            shutil.copytree(approvals, target)


def _detach(path, work):
    """Replace linked components below work with private clones before writing through them."""
    current = work
    for part in path.relative_to(work).parts:
        current = current / part
        if not current.is_symlink():
            continue
        private = current.with_name(current.name + ".seed-local")
        if private.exists() or private.is_symlink():
            shutil.rmtree(private)
        _clone(current, private)
        current.unlink()
        private.rename(current)
