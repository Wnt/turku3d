"""Small content-cached structural inputs shared by every tile in a source area."""

import hashlib
from pathlib import Path

from turku3d import config as C
from turku3d.spatial import rows
from turku3d.stage_cache import StageCache, source_key
from turku3d.tile_layer import identities


def cached(path, name, producers, operation):
    namespace = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    return StageCache(C.cache_dir("coarse-area") / namespace, retain=True).run(
        name,
        source_key(*producers, Path(__file__), Path(__file__).parents[1] / "spatial.py"),
        identities({"source": path}),
        operation,
    )


def passages(path):
    from . import underpasses

    return cached(
        path,
        "quay_passages",
        [
            Path(underpasses.__file__),
            Path(__file__).with_name("bridge_details.py"),
            Path(__file__).with_name("tunnels.py"),
        ],
        lambda: underpasses.quay_passages(
            row for _, row in rows(path, columns=("osm_id", "tags", "geometry_local"))
        ),
    )
