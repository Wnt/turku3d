"""Read-only source LAZ -> immutable collision-tile point shards over the shared decode.

A shard is a span of one per-source index (4 bytes per point) into the source-order decode,
not a second copy of its 17-byte rows: the same rows in the same order, a quarter of the disk.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from functools import cached_property, lru_cache
from pathlib import Path

from turku3d import profiling

from .decoded import STORED as SHARD_DTYPE, source as decoded_source
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


def _points(raw, scales, offsets):
    """Engine-metre records from stored integer shard rows (decoded.STORED).

    Bit-identical to converting laspy's x/y/z from the 17-byte rows a shard indexes.
    """
    records = np.empty(len(raw), POINT_DTYPE)
    records["xyz"][:, 0] = (raw["X"] * scales[0] + offsets[0]) - C.ORIGIN_E
    records["xyz"][:, 1] = (raw["Z"] * scales[2] + offsets[2]) - C.ORIGIN_H
    records["xyz"][:, 2] = C.ORIGIN_N - (raw["Y"] * scales[1] + offsets[1])
    for name in POINT_DTYPE.names[1:]:
        records[name] = raw[name]
    return records


def _identity(path):
    from turku3d.tile_layer import identities

    digest = identities({"source": path})["source"]
    if digest is None:
        raise FileNotFoundError(path)
    return {"path": str(path.resolve()), "sha256": digest}


class TileStore:
    """Decompress each source once; cache exact half-open tile crops and buffered crops.

    Cache identity includes source content, this implementation and grid constants.
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
            + Path(__file__).with_name("decoded.py").read_bytes()
            + repr((C.ORIGIN, C.COLLISION_TILE_SIZE, POINT_DTYPE.descr, SHARD_DTYPE.descr)).encode()
        ).hexdigest()

    def _source(self, tile):
        path = (
            Path(self.source_paths[tile.name])
            if self.source_paths is not None
            else self.source_dir / f"{tile.name}.laz"
        )
        key = hashlib.sha256(json.dumps([_identity(path)["sha256"], self.version]).encode()).hexdigest()
        return path, self.cache_dir / "sources" / key

    def _ensure_source(self, tile):
        source, dest = self._source(tile)
        if (dest / "MANIFEST.json").exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.with_suffix(".lock").open("a") as lock:
            profiling.flock(lock, fcntl.LOCK_EX, "lidar")
            if (dest / "MANIFEST.json").exists():
                return dest
            tmp = Path(tempfile.mkdtemp(prefix=".points-", dir=dest.parent))
            try:
                decoded = decoded_source(source)
                cells = np.empty((len(decoded), 2), np.int32)
                for start in range(0, len(decoded), 1_000_000):
                    part = decoded[start:start + 1_000_000]
                    xz = np.column_stack(
                        (part.scaled(0) - C.ORIGIN_E, C.ORIGIN_N - part.scaled(1))
                    )
                    cells[start:start + len(part)] = np.floor(xz / C.COLLISION_TILE_SIZE)
                # Stable: each collision tile's rows keep source order, as the copied shards did.
                low = cells.min(axis=0) if len(cells) else np.zeros(2, np.int32)
                width = int(cells[:, 0].max() - low[0] + 1) if len(cells) else 1
                keys = (cells[:, 1] - low[1]) * width + (cells[:, 0] - low[0])
                del cells
                order = np.argsort(keys, kind="stable").astype(np.uint32)
                values, starts, counts = np.unique(keys[order], return_index=True, return_counts=True)
                spans = {
                    C.CollisionTile(int(v % width + low[0]), int(v // width + low[1])).id: [int(a), int(n)]
                    for v, a, n in zip(values, starts, counts, strict=True)
                }
                np.save(tmp / "index.npy", order, allow_pickle=False)
                (tmp / "MANIFEST.json").write_text(
                    json.dumps(
                        {
                            "source": _identity(source),
                            "version": self.version,
                            "spans": spans,
                            "scales": [float(v) for v in decoded.scales],
                            "offsets": [float(v) for v in decoded.offsets],
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

    @staticmethod
    @lru_cache(maxsize=256)
    def _manifest(directory):
        manifest = json.loads((directory / "MANIFEST.json").read_text())
        scale = (np.asarray(manifest["scales"], np.float64), np.asarray(manifest["offsets"], np.float64))
        return manifest["spans"], scale

    @staticmethod
    @lru_cache(maxsize=16)
    def _rows(path, directory):
        """Source-order rows and this source's shard index; an evicted decode is rebuilt."""
        return decoded_source(path).data, np.load(directory / "index.npy", mmap_mode="r", allow_pickle=False)

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
        shards = []
        for source in sources:
            path, directory = self._source(source)
            self._ensure_source(source)
            spans, scale = self._manifest(directory)
            for t in C.collision_tiles_for_bbox(bbox):
                if t.id in spans:
                    shards.append((path, directory, spans[t.id], scale))

        def load(path, directory, span, scale):
            data, index = self._rows(path, directory)
            start, count = span
            return PointCloud(_points(data[index[start:start + count]], *scale))

        if buffer == 0 and len(shards) == 1:
            return load(*shards[0])
        dest = self.cache_dir / "crops" / f"{key}.npy"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            parts = [load(*shard).query(bbox=bbox).records for shard in shards]
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
