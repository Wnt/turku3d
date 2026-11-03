"""One immutable source-order decode shared by surveys, tiles and generations."""

import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import laspy
import numpy as np

from turku3d import config as C, profiling

DTYPE = np.dtype([(name, '<f8') for name in ('x', 'y', 'z')] + [
    ('classification', 'u1'), ('return_number', 'u1'),
    ('number_of_returns', 'u1'), ('intensity', '<u2'),
])


def source(path):
    """Memory-map exact scaled coordinates and survey columns in original LAZ order.

    Source order is significant: first-return reducers and triangulation tie breaks
    must see the same points as a direct laspy read. No coordinate conversion,
    sorting, quantisation or class filtering happens here.
    """
    from turku3d.tile_layer import identities

    digest = identities({'source': Path(path)})['source']
    if digest is None:
        raise FileNotFoundError(path)
    recipe = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    shared = os.environ.get('TURKU3D_SHARED_CACHE')
    root = (Path(shared) / 'raw-derived' / 'decoded' / recipe
            if shared else C.cache_dir('decoded') / recipe)
    root.mkdir(parents=True, exist_ok=True)
    target = root / (digest + '.npy')
    with target.with_suffix('.lock').open('a') as lock:
        profiling.flock(lock, fcntl.LOCK_EX, 'decode')
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix='decode-', suffix='.npy', dir=root)
            os.close(fd)
            try:
                selection = (laspy.DecompressionSelection.base().decompress_z()
                             .decompress_classification().decompress_intensity())
                with laspy.open(path, decompression_selection=selection) as reader:
                    data = np.lib.format.open_memmap(temporary, mode='w+', dtype=DTYPE,
                                                    shape=(reader.header.point_count,))
                    offset = 0
                    for chunk in reader.chunk_iterator(1_000_000):
                        end = offset + len(chunk)
                        for name in DTYPE.names:
                            data[name][offset:end] = np.asarray(getattr(chunk, name))
                        offset = end
                    if offset != len(data):
                        raise ValueError(f'incomplete LAZ source: {path}')
                    data.flush()
                    del data
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        # Open while holding the lock. A mapped inode stays valid after eviction.
        data = np.load(target, mmap_mode='r', allow_pickle=False)
        if data.dtype != DTYPE:
            raise ValueError(f'invalid decoded source: {target}')
        target.touch()
        root.touch()
    return data


class Columns(SimpleNamespace):
    def __len__(self):
        return len(self.x)


def columns(data):
    return Columns(**{name: data[name] for name in DTYPE.names})


def chunks(path, size=1_000_000):
    data = source(path)
    for start in range(0, len(data), size):
        yield columns(data[start:start + size])
