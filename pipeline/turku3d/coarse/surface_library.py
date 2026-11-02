"""Content-addressed ground maps shared across areas and immutable source checkouts."""
import errno
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path

from turku3d import profiling
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image

from turku3d import config as C
from turku3d.tile_layer import identities
from . import ground_finishes, street_materials
from .surfaces import KEYS, fallback, neutral_asphalt


def produce(stage, channel, source):
    finish_maps = (
        ground_finishes.maps(KEYS[channel])
        if KEYS[channel] in street_materials.FINISH_KEYS.values() else None
    )
    for slot in ("albedo", "normal", "orm"):
        output = stage / f"{slot}.png"
        if finish_maps is not None:
            Image.fromarray(finish_maps[slot]).save(output)
        elif source:
            subprocess.run(
                [
                    "ktx",
                    "extract",
                    "--transcode",
                    "rgba8",
                    "--layer",
                    str(channel),
                    "--level",
                    "0" if slot == "albedo" else "1" if slot == "normal" else "2",
                    str(source / f"{slot}.array.ktx2"),
                    str(output),
                ],
                check=True,
                capture_output=True,
            )
        else:
            pixels = (
                fallback(channel)
                if slot == "albedo"
                else np.full(
                    (256, 256, 3),
                    (128, 128, 255) if slot == "normal" else (255, 230, 0),
                    np.uint8,
                )
            )
            Image.fromarray(pixels).save(output)
        if slot == "albedo" and channel in (0, 1):
            with Image.open(output) as image:
                pixels = neutral_asphalt(np.asarray(image.convert("RGB")))
            Image.fromarray(pixels).save(output)
    (stage / "material.json").write_text(
        json.dumps(
            dict(
                uv_scale=[0.25, 0.25] if KEYS[channel] == "fieldstone" else [0.5, 0.5],
                source="procedural" if finish_maps or not source else "own ground library",
            )
        )
    )


def key(channel, source):
    procedural = KEYS[channel] in street_materials.FINISH_KEYS.values()
    code = [inspect.getsource(produce)]
    if procedural:
        code.append(inspect.getsource(ground_finishes.maps))
    elif source is None:
        code.append(inspect.getsource(fallback))
    if channel in (0, 1):
        code.append(inspect.getsource(neutral_asphalt))
    raw = identities({slot: source / f'{slot}.array.ktx2' for slot in ('albedo', 'normal', 'orm')}) if source and not procedural else None
    return hashlib.sha256(repr((channel, KEYS[channel], procedural, code, raw)).encode()).hexdigest()


def link_file(source, destination):
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        shutil.copy2(source, destination)


def library(root: Path, base: Path, channels):
    sources = [C.work_root() / 'textures/library', C.data_root() / 'textures/library']
    source = next((p for p in sources if all((p / f'{s}.array.ktx2').exists()
                   for s in ('albedo', 'normal', 'orm'))), None)
    keys = {channel: key(channel, source) for channel in sorted(channels)}
    shared = os.environ.get('TURKU3D_SHARED_CACHE')
    cache = Path(shared) / 'surface-maps' if shared else root / '.surface-maps'
    cache.mkdir(parents=True, exist_ok=True)
    # Only this cheap overlay depends on the branch's base library path.
    identity = hashlib.sha256(repr((str(base.resolve()), keys)).encode()).hexdigest()[:16]
    target = root / f'.surface-library-{identity}'
    target.mkdir(parents=True, exist_ok=True)
    if base.exists():
        for path in base.iterdir():
            try:
                (target / path.name).symlink_to(path.resolve(), target_is_directory=path.is_dir())
            except FileExistsError:
                pass
    for channel, digest in keys.items():
        destination = target / ('draft_' + KEYS[channel])
        with (cache / f'{digest}.lock').open('a') as lock:
            profiling.flock(lock, fcntl.LOCK_EX, "surface")
            if destination.is_dir():
                continue
            maps = cache / digest
            if not maps.is_dir():
                with tempfile.TemporaryDirectory(prefix='.maps-', dir=cache) as temporary:
                    stage = Path(temporary) / 'maps'
                    stage.mkdir()
                    produce(stage, channel, source)
                    stage.rename(maps)
            os.utime(maps, None)
            # Private hard links keep complete products valid after shared-cache eviction.
            with tempfile.TemporaryDirectory(prefix='.link-', dir=target) as temporary:
                stage = Path(temporary) / 'maps'
                shutil.copytree(maps, stage, copy_function=link_file)
                stage.rename(destination)
    return target
