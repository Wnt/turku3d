"""np.unique(rows, axis=0) for integer rows (grid cells), without comparing rows.

NumPy sorts the rows of a 2-D array as structured records: about 14 s for 8 million
LiDAR cells. Offsetting each column to start at zero and packing the columns most
significant first gives one int64 key per row in the same lexicographic order. When the
occupied box of cells is small enough to index directly (a LAZ file or a tile at half a
metre), first occurrences, counts and ranks come from one pass over a dense array instead
of a sort: 0.3 s for the same cells. The results are exactly np.unique's.
"""

import numpy as np

DENSE_CELLS = 1 << 23  # index this many cells directly (a 1.4 km square at half a metre)...
DENSE_MAX = 1 << 25  # ...or up to two cells per row, to this many


def dense(size, rows):
    """Whether a table of ``size`` cells is worth a pass over ``rows`` rows: bounded memory, no sort."""
    return size <= DENSE_CELLS or size <= min(DENSE_MAX, 2 * rows)


def keys(rows):
    """(key per row, column offsets, spans), or None when the box does not fit an int64."""
    low, high = rows.min(axis=0), rows.max(axis=0)
    spans = [int(h) - int(lo) + 1 for lo, h in zip(low, high, strict=True)]
    if np.prod(np.array(spans, dtype=object)) >= 2**63:
        return None
    return np.ravel_multi_index(tuple((rows - low).astype(np.int64).T), spans), low, spans


def unique(rows, *, return_index=False, return_inverse=False, return_counts=False):
    """``np.unique(rows, axis=0, ...)`` for a 2-D integer array; the inverse is always 1-D."""
    rows = np.asarray(rows)
    flags = dict(return_index=return_index, return_inverse=return_inverse, return_counts=return_counts)
    packed = keys(rows) if rows.ndim == 2 and rows.dtype.kind in "iu" and len(rows) and rows.shape[1] else None
    if packed is None:
        found = np.unique(rows, axis=0, **flags)
        if not return_inverse:
            return found
        found = list(found)
        found[2 if return_index else 1] = found[2 if return_index else 1].reshape(-1)
        return tuple(found)
    key, low, spans = packed
    size = int(np.prod(spans))
    if dense(size, len(key)):
        ordered, extra = _dense(key, size, **flags)
    else:
        found = np.unique(key, **flags)
        found = found if isinstance(found, tuple) else (found,)
        ordered, extra = found[0], [f.reshape(-1) for f in found[1:]]
    values = np.column_stack(np.unravel_index(ordered, spans)).astype(rows.dtype) + low
    return (values, *extra) if extra else values


def _dense(key, size, *, return_index, return_inverse, return_counts):
    """Sorted unique keys and the requested arrays, in np.unique's order, through a dense table."""
    n = len(key)
    index = np.int32 if n < 2**31 - 1 else np.int64
    counts = np.bincount(key, minlength=size)
    occupied = np.flatnonzero(counts)
    extra = []
    if return_index:
        first = np.full(size, n, index)
        np.minimum.at(first, key, np.arange(n, dtype=index))
        extra.append(first[occupied].astype(np.int64))
    if return_inverse:
        rank = np.zeros(size, np.int64)
        rank[occupied] = np.arange(len(occupied))
        extra.append(rank[key])
    if return_counts:
        extra.append(counts[occupied])
    return occupied, extra


def highest(rows, height):
    """Per distinct row (in np.unique's order), the index of its first row among those with the
    greatest height: the row ``np.lexsort((-height, *rows.T[::-1]))`` puts first in its group."""
    rows, height = np.asarray(rows), np.asarray(height)
    packed = keys(rows) if len(rows) else None
    if packed is None or not dense(int(np.prod(packed[2])), len(rows)):
        order = np.lexsort((-height, *rows.T[::-1]))
        ordered = rows[order]
        keep = np.ones(len(order), bool)
        keep[1:] = np.any(ordered[1:] != ordered[:-1], axis=1)
        return order[keep]
    key, _, spans = packed
    size = int(np.prod(spans))
    best = np.full(size, -np.inf)
    np.maximum.at(best, key, height)
    top = np.flatnonzero(height == best[key])
    first = np.full(size, len(rows), np.int64)
    np.minimum.at(first, key[top], top)
    return first[first < len(rows)]
