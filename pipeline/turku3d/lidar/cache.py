"""Read-only source LAZ -> immutable, memory-mapped collision tile point shards."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from functools import cached_property, lru_cache
from pathlib import Path

import laspy
import numpy as np
from scipy.spatial import cKDTree
from shapely import intersects_xy

from turku3d import config as C

POINT_DTYPE = np.dtype(
    [
        ("xyz", "<f8", (3,)),
        ("classification", "u1"),
        ("return_number", "u1"),
        ("number_of_returns", "u1"),
        ("intensity", "<u2"),
    ]
)


class PointCloud:
    """Points in engine xyz metres. Polygons and boxes use engine x/z metres."""

    def __init__(self, records):
        if records.dtype != POINT_DTYPE:
            raise ValueError("records must use POINT_DTYPE")
        self.records = records

    @classmethod
    def from_xyz(cls, xyz, classification=2, return_number=1, number_of_returns=1):
        records = np.zeros(len(xyz), POINT_DTYPE)
        records["xyz"] = xyz
        records["classification"] = classification
        records["return_number"] = return_number
        records["number_of_returns"] = number_of_returns
        return cls(records)

    def __len__(self):
        return len(self.records)

    @property
    def points(self):
        return self.records["xyz"]

    @cached_property
    def tree(self):
        """One horizontal KD-tree per cached PointCloud, constructed on first query."""
        return cKDTree(self.points[:, [0, 2]])

    def query(self, bbox=None, polygon=None, classes=None, returns=None, last_return=False):
        keep = np.ones(len(self), dtype=bool)
        p = self.points
        if bbox is not None:
            keep &= (
                (p[:, 0] >= bbox.xmin)
                & (p[:, 0] < bbox.xmax)
                & (p[:, 2] >= bbox.zmin)
                & (p[:, 2] < bbox.zmax)
            )
        if classes is not None:
            keep &= np.isin(self.records["classification"], np.atleast_1d(classes))
        if returns is not None:
            keep &= np.isin(self.records["return_number"], np.atleast_1d(returns))
        if last_return:
            keep &= self.records["return_number"] == self.records["number_of_returns"]
        if polygon is not None:
            ii = np.flatnonzero(keep)
            keep[ii] &= intersects_xy(polygon, p[ii, 0], p[ii, 2])
        return PointCloud(self.records[keep])


def _identity(path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


class TileStore:
    """Decompress each source once; cache exact half-open tile crops and buffered crops.

    Cache identity includes source path/size/mtime, this implementation and grid constants.
    No pickle, no source writes. An advisory process lock and atomic directory rename keep
    concurrent builders from publishing partial shards. SHA manifests remain input authority.
    """

    def __init__(self, source_dir=None, cache_dir=None, *, source_paths=None):
        self.source_dir = Path(source_dir or C.raw_dir("laz-3x3"))
        self.source_paths = source_paths
        self.cache_dir = Path(cache_dir or C.cache_dir("lidar"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cached_tile = lru_cache(maxsize=24)(self._read_tile)
        self.version = hashlib.sha256(
            Path(__file__).read_bytes()
            + repr((C.ORIGIN, C.COLLISION_TILE_SIZE, POINT_DTYPE.descr)).encode()
        ).hexdigest()

    def _source(self, tile):
        path = (
            Path(self.source_paths[tile.name])
            if self.source_paths is not None
            else self.source_dir / f"{tile.name}.laz"
        )
        key = hashlib.sha256(json.dumps([_identity(path), self.version]).encode()).hexdigest()
        return path, self.cache_dir / "sources" / key

    def _ensure_source(self, tile):
        source, dest = self._source(tile)
        if (dest / "MANIFEST.json").exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if (dest / "MANIFEST.json").exists():
                return dest
            tmp = Path(tempfile.mkdtemp(prefix=".points-", dir=dest.parent))
            try:
                counts = {}
                with laspy.open(source) as reader:
                    for chunk in reader.chunk_iterator(1_000_000):
                        records = np.empty(len(chunk), POINT_DTYPE)
                        records["xyz"][:, 0] = np.asarray(chunk.x) - C.ORIGIN_E
                        records["xyz"][:, 1] = np.asarray(chunk.z) - C.ORIGIN_H
                        records["xyz"][:, 2] = C.ORIGIN_N - np.asarray(chunk.y)
                        for name in POINT_DTYPE.names[1:]:
                            records[name] = np.asarray(getattr(chunk, name))
                        indices = np.floor(
                            records["xyz"][:, [0, 2]] / C.COLLISION_TILE_SIZE
                        ).astype(int)
                        # Lexicographic sort makes each tile write contiguous, not 64 scans/chunk.
                        order = np.lexsort((indices[:, 0], indices[:, 1]))
                        sorted_indices = indices[order]
                        starts = np.r_[
                            0, np.flatnonzero(np.any(np.diff(sorted_indices, axis=0), axis=1)) + 1
                        ]
                        for a, b in zip(starts, np.r_[starts[1:], len(order)], strict=True):
                            if a == b:
                                continue
                            name = C.CollisionTile(*sorted_indices[a]).id
                            with (tmp / f"{name}.bin").open("ab") as out:
                                records[order[a:b]].tofile(out)
                            counts[name] = counts.get(name, 0) + int(b - a)
                for name, count in counts.items():
                    raw = tmp / f"{name}.bin"
                    data = np.memmap(raw, mode="r", dtype=POINT_DTYPE, shape=(count,))
                    np.save(tmp / f"{name}.npy", data, allow_pickle=False)
                    del data
                    raw.unlink()
                (tmp / "MANIFEST.json").write_text(
                    json.dumps(
                        {
                            "source": _identity(source),
                            "version": self.version,
                            "counts": counts,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                os.rename(tmp, dest)
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp)
        return dest

    def _key(self, bbox):
        sources = C.laz_tiles_for_bbox(bbox)
        identities = [str(self._source(t)[1]) for t in sources]
        key = hashlib.sha256(json.dumps([identities, bbox.as_tuple()]).encode()).hexdigest()
        return sources, key

    def tile(self, tile, buffer=0.0):
        tile = C.CollisionTile.parse(tile) if isinstance(tile, str) else tile
        if not np.isfinite(buffer) or buffer < 0:
            raise ValueError("buffer must be finite and nonnegative")
        bbox = tile.bbox.buffered(buffer)
        sources, key = self._key(bbox)
        return self._cached_tile(tile.id, float(buffer), tuple(sources), key)

    def _read_tile(self, tile_id, buffer, sources, key):
        bbox = C.CollisionTile.parse(tile_id).bbox.buffered(buffer)
        paths = [self._ensure_source(t) for t in sources]
        shards = [
            p / f"{t.id}.npy"
            for p in paths
            for t in C.collision_tiles_for_bbox(bbox)
            if (p / f"{t.id}.npy").exists()
        ]
        if buffer == 0 and len(shards) == 1:
            return PointCloud(np.load(shards[0], mmap_mode="r", allow_pickle=False))
        dest = self.cache_dir / "crops" / f"{key}.npy"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            parts = [
                PointCloud(np.load(p, mmap_mode="r", allow_pickle=False)).query(bbox=bbox).records
                for p in shards
            ]
            data = np.concatenate(parts) if parts else np.empty(0, POINT_DTYPE)
            fd, name = tempfile.mkstemp(dir=dest.parent, suffix=".npy")
            try:
                with os.fdopen(fd, "wb") as stream:
                    np.save(stream, data, allow_pickle=False)
                os.replace(name, dest)
            finally:
                Path(name).unlink(missing_ok=True)
        return PointCloud(np.load(dest, mmap_mode="r", allow_pickle=False))

    def query(self, bbox, **filters):
        """Assemble disjoint unbuffered tiles; overlapping query buffers never duplicate points."""
        parts = [
            self.tile(t).query(bbox=bbox, **filters).records
            for t in C.collision_tiles_for_bbox(bbox)
        ]
        return PointCloud(np.concatenate(parts) if parts else np.empty(0, POINT_DTYPE))
