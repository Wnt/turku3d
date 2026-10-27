"""Landing classes: what a landing or an integration batch can change in the live release.

`classify` sorts the paths of `git diff base..head` and the data-root delta into one of
three classes. The rule is conservative: any path outside the inert and data allowlists
makes the landing `code`.

- `inert`: prose and docs. No live stage reads them, so the release content is unchanged.
- `data`: content the live build packs without code changes (voice clips, the address pool)
  plus a changed data root (facade approvals, the playable tile list, published layers).
- `code`: everything else (game, pipeline, tools, textures, scripts, configs, data-manifests).

Stdlib only (plus the stdlib-only pipeline config): the lander imports it on every landing.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from turku3d import config as C  # noqa: E402  (stdlib-only module; no pipeline env needed)

CLASSES = ("inert", "data", "code")
INERT = ("docs/*", "*.md", "LICENSE")
DATA = ("game/public/assets/voice/*", "game/public/assets/content/*")
# The data-root parts whose change makes a batch `data`; see data_parts().
PARTS = ("facade-approvals", "playable-tiles", "layers")
# Ledger rows keep at most this many paths; `changed_paths` holds the full count.
PATH_CAP = 40


def published_root():
    """The data root the lander and the integrator compare releases against."""
    return Path(os.environ.get("TURKU3D_DATA", Path.home() / "turku3d/data")).resolve()


def path_class(path):
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in INERT):
        return "inert"
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in DATA):
        return "data"
    return "code"


def _json_tree(root):
    """Hash of every JSON file below root (relative path and bytes); 'missing' when absent."""
    if not root.is_dir():
        return "missing"
    h = hashlib.sha256()
    for file in sorted(root.rglob("*.json")):
        if file.is_file():
            h.update(str(file.relative_to(root)).encode() + b"\0" + file.read_bytes() + b"\0")
    return h.hexdigest()


def _tiles_hash(tiles):
    return hashlib.sha256(json.dumps(sorted(tiles)).encode()).hexdigest()


def released_tiles(release):
    """The tile list a release was built for; releases before the list had the core."""
    record = Path(release) / "live-build.json" if release else None
    if record and record.is_file():
        return tuple(json.loads(record.read_text()).get("tiles", C.CORE_TILES))
    return C.CORE_TILES


def listed_tiles(published):
    """The data root's playable tiles without migrating a missing list (read-only)."""
    path = Path(published) / C.PLAYABLE_TILES_FILE
    return C.read_playable_tiles(path) if path.is_file() else list(C.CORE_TILES)


def data_parts(published):
    """Per-part identity of the data root a live build reads.

    The approval assembly's JSON (MANIFEST and every facade sidecar, which carries its
    texture key), the playable tile list and the published layers' JSON manifests.
    """
    published = Path(published)
    return {
        "facade-approvals": _json_tree(published / "facade-approvals/assembly"),
        "playable-tiles": _tiles_hash(listed_tiles(published)),
        "layers": _json_tree(published / "build/layers"),
    }


def release_parts(release):
    """The same parts for a published release directory, comparable with data_parts()."""
    release = Path(release)
    return {
        "facade-approvals": _json_tree(release / "textures/assembly"),
        "playable-tiles": _tiles_hash(released_tiles(release)),
        "layers": _json_tree(release / "layers"),
    }


def data_delta(current, baseline):
    """The parts that differ; a part missing from the baseline counts as changed."""
    return [part for part in PARTS if current.get(part) != (baseline or {}).get(part)]


def changed_paths(repo, base, head):
    if base == head:
        return []
    return subprocess.run(
        ["git", "diff", "--name-only", base, head],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()


def classify(repo, base, head, data_delta, published=None):
    """{"class", "paths", "changed_paths", "data_delta", "tiles"} for the landing base..head.

    `tiles` names the playable tiles a landing affects: none for `inert`, every listed tile
    for `code` and for a data delta that can touch any tile (facade approvals pack into
    every tile, a layer change is only resolved by the live build), else the tiles newly
    listed since the live release.
    """
    paths = changed_paths(repo, base, head)
    classes = {path_class(path) for path in paths}
    if data_delta:
        classes.add("data")
    landing = max(classes, key=CLASSES.index, default="inert")
    root = Path(published) if published else published_root()
    listed = listed_tiles(root) if landing != "inert" else []
    if landing == "data" and not {"facade-approvals", "layers"} & set(data_delta):
        released = set(released_tiles(root / "live"))
        listed = [tile for tile in listed if tile not in released]
    return {
        "class": landing,
        "paths": paths[:PATH_CAP],
        "changed_paths": len(paths),
        "data_delta": list(data_delta),
        "tiles": listed,
    }
