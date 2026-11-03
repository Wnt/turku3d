"""One immutable source-order decode shared by surveys, tiles and generations."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import laspy
import numpy as np

from turku3d import config as C, profiling

# Columns as readers see them: exact scaled survey coordinates, as a direct laspy read.
DTYPE = np.dtype([(name, '<f8') for name in ('x', 'y', 'z')] + [
    ('classification', 'u1'), ('return_number', 'u1'),
    ('number_of_returns', 'u1'), ('intensity', '<u2'),
])
# Stored: the LAZ integer coordinates. x = X * scale + offset reproduces laspy's
# float64 values bit for bit, at 17 instead of 29 bytes per point.
STORED = np.dtype([(name, '<i4') for name in ('X', 'Y', 'Z')] + DTYPE.descr[3:])


class Decoded:
    """A memory-mapped source-order slice with its LAS scale and offset."""

    def __init__(self, data, scales, offsets):
        self.data, self.scales, self.offsets = data, scales, offsets

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return Decoded(self.data[index], self.scales, self.offsets)

    def scaled(self, axis):
        name = 'XYZ'[axis]
        return self.data[name] * self.scales[axis] + self.offsets[axis]


def source(path):
    """Memory-map exact survey coordinates and columns in original LAZ order.

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
    scale = root / (digest + '.scale.json')
    with target.with_suffix('.lock').open('a') as lock:
        profiling.flock(lock, fcntl.LOCK_EX, 'decode')
        if not target.exists() or not scale.exists():
            fd, temporary = tempfile.mkstemp(prefix='decode-', suffix='.npy', dir=root)
            os.close(fd)
            try:
                selection = (laspy.DecompressionSelection.base().decompress_z()
                             .decompress_classification().decompress_intensity())
                with laspy.open(path, decompression_selection=selection) as reader:
                    header = reader.header
                    data = np.lib.format.open_memmap(temporary, mode='w+', dtype=STORED,
                                                    shape=(header.point_count,))
                    offset = 0
                    for chunk in reader.chunk_iterator(1_000_000):
                        end = offset + len(chunk)
                        for name in STORED.names:
                            data[name][offset:end] = np.asarray(getattr(chunk, name))
                        offset = end
                    if offset != len(data):
                        raise ValueError(f'incomplete LAZ source: {path}')
                    data.flush()
                    del data
                    meta = dict(scales=[float(v) for v in header.scales],
                                offsets=[float(v) for v in header.offsets])
                scale_tmp = scale.with_suffix('.tmp')
                scale_tmp.write_text(json.dumps(meta))
                scale_tmp.replace(scale)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        # Open while holding the lock. A mapped inode stays valid after eviction.
        data = np.load(target, mmap_mode='r', allow_pickle=False)
        meta = json.loads(scale.read_text())
        if data.dtype != STORED:
            raise ValueError(f'invalid decoded source: {target}')
        target.touch()
        root.touch()
    return Decoded(data, np.asarray(meta['scales'], np.float64), np.asarray(meta['offsets'], np.float64))


class Columns(SimpleNamespace):
    def __len__(self):
        return len(self.x)


def columns(decoded):
    data = decoded.data
    return Columns(x=decoded.scaled(0), y=decoded.scaled(1), z=decoded.scaled(2),
                   **{name: data[name] for name in DTYPE.names[3:]})


def chunks(path, size=1_000_000):
    data = source(path)
    for start in range(0, len(data), size):
        yield columns(data[start:start + size])
