"""Seed missing sandbox products from one immutable release, preserving local work."""

import json
import os
from pathlib import Path

import live_build
from landing_class import C


def reuse(work, published):
    work, published = work.resolve(), published.resolve()
    if work == published or not work.is_relative_to(Path(os.environ["TURKU3D_WORK"]).resolve()):
        raise ValueError("tile reuse writes only inside the current sandbox")
    release = (published / "live").resolve(strict=True)
    copied = []
    for source, relative in (
        ("layers", "build/layers"),
        ("textures", "textures"),
        ("tiles", "dist/tiles"),
    ):
        target = work / relative
        if target.exists() or target.is_symlink():
            continue
        if not target.parent.resolve().is_relative_to(work):
            raise ValueError(f"refusing to clone through an external sandbox link: {target}")
        live_build._clone(release / source, target)
        copied.append(relative)
    # A snapshot must not silently follow a newer shared tile list than its built inputs.
    listed = work / C.PLAYABLE_TILES_FILE
    if "dist/tiles" in copied and not listed.exists():
        record = json.loads((release / "live-build.json").read_text())
        C.write_playable_tiles(listed, record["tiles"])
    return {"release": release.name, "copied": copied}


if __name__ == "__main__":
    print(json.dumps(reuse(Path(os.environ["TURKU3D_WORK"]), Path(os.environ["TURKU3D_DATA"]))))
