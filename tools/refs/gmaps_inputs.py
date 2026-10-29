"""Bind the private scorer to the current prepared-input dependency API."""

import hashlib
import json

from turku3d.coarse import inputs


def identity(paths_by_dataset):
    """Validation reads these datasets only; decorative producers are unrelated."""
    records = []
    for dataset, paths in sorted(paths_by_dataset.items()):
        present = [p for p in paths or () if p is not None]
        if dataset == "ortho":
            for path in list(present):
                chunks = path.with_name(path.name.removesuffix(".vrt") + ".chunks.json")
                if chunks.exists():
                    present.append(chunks)
        records.append(
            (
                dataset,
                None if paths is None else [p is not None for p in paths],
                inputs._key(present, dataset),
            )
        )
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def install():
    """Adapt the installed reader in this process without editing private source."""
    import own_inputs

    own_inputs.identity = lambda tile: identity(own_inputs.paths_for(tile))
