"""City bulk runs keep no persistent per-tile or per-unit caches on the 300 GB staging disk.

A fresh city run computes each tile once. When a tile finishes, its stage and prepared-input
entries go. When no remaining tile needs a classified 500 m lidar unit (tile + MARGIN), its
decode, shard index and reductions go too. Per-model archetype packages, which no other tile of
the generation reuses, go once they are older than every build still in flight. Shared
neighbour context (building attachments, area caches) stays: it is small and bounded.
Deleting is only ever a cache miss: a later reader rebuilds what it needs.
"""
from collections import Counter
import hashlib
import os
from pathlib import Path
import queue
import shutil
import threading
import time

MARGIN = 150.0  # metres; every lidar reader's window lies within tile + 20 m (POINT_MARGIN) plus overhanging buildings


def size(path):
    try:
        if path.is_dir():
            return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def remove(path):
    n = size(path)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    return n


class UnitCaches:
    """The pinned producers' cache paths of one classified lidar unit (laz file)."""

    def __init__(self, repo, shared):
        from turku3d import config as C
        from turku3d.lidar.cache import TileStore
        from turku3d.vegetation.trees import CELL
        self.C, self.CELL, self.shared = C, CELL, Path(shared)
        self.recipe = hashlib.sha256((Path(repo) / 'pipeline/turku3d/lidar/decoded.py').read_bytes()).hexdigest()
        self.store = TileStore(source_dir=C.raw_dir('laz') / 'pistepilvi', cache_dir=C.cache_dir('lidar'))

    def source(self, name):
        return self.C.raw_dir('laz') / 'pistepilvi' / f'{name}.laz'

    def paths(self, name):
        from turku3d.tile_layer import identities
        path = self.source(name)
        identity = identities({'laz': path})['laz']
        if identity is None:
            return []
        decoded = self.shared / 'raw-derived' / 'decoded' / self.recipe / identity
        _, shards = self.store._source(self.C.LazTile.parse(name))
        vegetation = self.C.cache_dir('vegetation') / (hashlib.sha256(
            repr((identity, self.C.ORIGIN, self.CELL, 'reduced-v1')).encode()).hexdigest() + '.npz')
        roofs = self.C.cache_dir('coarse-roofs')
        return [decoded.with_suffix('.npy'), decoded.with_suffix('.scale.json'), shards,
                vegetation, roofs / f'{identity}.class6.npy']

    def units(self, tile):
        C = self.C
        bbox = C.VisualTile.parse(tile).bbox.buffered(MARGIN)
        return [u.name for u in C.laz_tiles_for_bbox(bbox) if self.source(u.name).is_file()]


class Transient:
    """Deletes finished work on a background thread; `done` is safe to call from farm events."""

    def __init__(self, cache, remaining_tiles, units_of, unit_paths, grace=60.0):
        self.cache, self.units_of, self.unit_paths, self.grace = Path(cache), units_of, unit_paths, grace
        self.need = {tile: units_of(tile) for tile in remaining_tiles}
        self.remaining = Counter(u for units in self.need.values() for u in units)
        self.metrics = dict(tile_bytes=0, unit_bytes=0, units_removed=0, archetype_bytes=0)
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def done(self, tile):
        self.jobs.put(('tile', tile))

    def sweep(self, oldest_start):
        """Archetype packages written before every in-flight build started are dead."""
        self.jobs.put(('archetypes', oldest_start))

    def close(self):
        self.jobs.put(None)
        self.thread.join()

    def _work(self):
        while (job := self.jobs.get()) is not None:
            kind, value = job
            try:
                if kind == 'tile':
                    self._tile(value)
                else:
                    self._archetypes(value)
            except OSError:
                pass  # A cache miss later is the only consequence.

    def _tile(self, tile):
        for kind in ('stages', 'inputs'):
            self.metrics['tile_bytes'] += remove(self.cache / kind / tile)
        for unit in self.need.pop(tile, ()):
            self.remaining[unit] -= 1
            if self.remaining[unit] == 0:
                for path in self.unit_paths(unit):
                    self.metrics['unit_bytes'] += remove(path)
                self.metrics['units_removed'] += 1

    def _archetypes(self, oldest_start):
        root = self.cache / 'stages' / 'archetypes'
        if not root.is_dir():
            return
        for entry in root.iterdir():
            try:
                newest = max((p.stat().st_mtime for p in entry.rglob('*') if p.is_file()), default=entry.stat().st_mtime)
            except FileNotFoundError:
                continue
            if newest < oldest_start - self.grace:
                self.metrics['archetype_bytes'] += remove(entry)


def finished_units(transient):
    """Units no remaining tile needs (a resumed run): their leftovers can go at once."""
    return [u for u, n in transient.remaining.items() if n == 0]
