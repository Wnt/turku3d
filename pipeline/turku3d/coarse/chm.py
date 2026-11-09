"""City-wide lidar canopy height model (CHM) and ground model (DTM) on a 4 m grid, for the far canopy shells.

Each pistepilvi LAZ block (500 m, named by its north-west corner) is streamed once, decoding only z and classification:
per 4 m cell, the highest class-5 (high vegetation) return and the lowest class-2 (ground) return. The canopy height is
their difference (0 without vegetation). ``merge`` puts the blocks on one grid in game coordinates (x east, z south of
config.ORIGIN), fills ground under buildings from the nearest ground cell, and saves it as an immutable npz:

    chm   int16 decimetres above the ground, rows by z (north first), columns by x
    dtm   int16 decimetres (N2000), every cell filled
    seen  bool, the cell lies in a scanned block
    xmin, zmin, step   the grid's north-west corner (game coordinates) and cell size

    python -m turku3d.coarse.chm blocks SRC_DIR OUT_DIR    # one npz per block, nearest the origin first; resumes
    python -m turku3d.coarse.chm merge OUT_DIR CHM.npz
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np

from turku3d import config as C

CELL = 4.0
BLOCK = 500.0
MISSING = -32768


def _corner(path: Path) -> tuple[float, float]:
    east, north = (float(v) for v in path.stem.split("_"))
    return east, north


def block(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """(chm, dtm) in decimetres for one LAZ block, rows south to north, and the point count."""
    import laspy

    e0, north = _corner(path)
    n0, n = north - BLOCK, int(BLOCK / CELL)
    top, ground, points = np.full(n * n, -1e9), np.full(n * n, 1e9), 0
    selection = laspy.DecompressionSelection.base().decompress_z().decompress_classification()
    with laspy.open(path, decompression_selection=selection) as reader:
        header = reader.header
        for chunk in reader.chunk_iterator(2_000_000):
            klass = np.asarray(chunk.classification)
            points += len(klass)
            x = np.floor((np.asarray(chunk.X) * header.scales[0] + header.offsets[0] - e0) / CELL).astype(np.int64)
            y = np.floor((np.asarray(chunk.Y) * header.scales[1] + header.offsets[1] - n0) / CELL).astype(np.int64)
            z = np.asarray(chunk.Z) * header.scales[2] + header.offsets[2]
            inside = (x >= 0) & (x < n) & (y >= 0) & (y < n)
            cell = y * n + x
            for keep, grid, best in ((inside & (klass == 5), top, np.maximum), (inside & (klass == 2), ground, np.minimum)):
                ii, zz = cell[keep], z[keep]
                if not len(ii):
                    continue
                order = np.lexsort((zz if best is np.maximum else -zz, ii))
                ii, zz = ii[order], zz[order]
                last = np.r_[ii[1:] != ii[:-1], True]  # each cell's extreme z is its last after the sort
                grid[ii[last]] = best(grid[ii[last]], zz[last])
    have = (top > -1e8) & (ground < 1e8)
    chm = np.round(np.where(have, np.clip(top - ground, 0, 3276), 0) * 10).astype(np.int16)
    dtm = np.where(ground < 1e8, np.clip(np.round(ground * 10), -32767, 32767), MISSING).astype(np.int16)
    return chm.reshape(n, n), dtm.reshape(n, n), points


def blocks(src: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    origin = (C.ORIGIN_E, C.ORIGIN_N)
    paths = sorted(src.glob("*.laz"), key=lambda p: (_corner(p)[0] + BLOCK / 2 - origin[0]) ** 2
                   + (_corner(p)[1] - BLOCK / 2 - origin[1]) ** 2)
    for path in paths:
        dest = out / f"{path.stem}.npz"
        if dest.exists():
            continue
        start = time.time()
        chm, dtm, points = block(path)
        temporary = dest.with_suffix(".tmp.npz")
        e0, north = _corner(path)
        np.savez_compressed(temporary, chm=chm, dtm=dtm, x0=e0, y0=north - BLOCK, cell=CELL)
        temporary.replace(dest)
        print(json.dumps(dict(block=path.stem, s=round(time.time() - start, 1), points=points)), flush=True)


def _block_files(src: Path) -> list[Path]:
    return sorted(p for p in src.glob("*.npz") if re.fullmatch(r"\d+_\d+", p.stem))


def merge(src: Path, out: Path) -> dict:
    """One grid in game coordinates from the per-block files; the ground under buildings and other gaps is filled
    from the nearest ground cell."""
    from scipy.ndimage import distance_transform_edt

    files = _block_files(src)
    corners = [_corner(p) for p in files]
    if not corners:
        raise FileNotFoundError(f"no block files in {src}")
    east0, east1 = min(e for e, _ in corners), max(e for e, _ in corners) + BLOCK
    north0, north1 = min(n for _, n in corners) - BLOCK, max(n for _, n in corners)
    n = int(BLOCK / CELL)
    cols, rows = int(round((east1 - east0) / CELL)), int(round((north1 - north0) / CELL))
    chm = np.zeros((rows, cols), np.int16)
    dtm = np.full((rows, cols), MISSING, np.int16)
    seen = np.zeros((rows, cols), bool)
    for path in files:
        east, north = _corner(path)
        with np.load(path) as data:
            # Block rows run south to north; grid rows run north to south (game z grows southwards).
            r0, c0 = int(round((north1 - north) / CELL)), int(round((east - east0) / CELL))
            chm[r0:r0 + n, c0:c0 + n] = data["chm"][::-1]
            dtm[r0:r0 + n, c0:c0 + n] = data["dtm"][::-1]
            seen[r0:r0 + n, c0:c0 + n] = True
    hole = dtm == MISSING
    if hole.any() and (~hole).any():
        index = distance_transform_edt(hole, return_distances=False, return_indices=True)
        dtm = dtm[tuple(index)]
    xmin, zmin = east0 - C.ORIGIN_E, C.ORIGIN_N - north1
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, chm=chm, dtm=dtm, seen=seen, xmin=xmin, zmin=zmin, step=CELL)
    temporary.replace(out)
    return dict(blocks=len(corners), rows=rows, cols=cols, xmin=xmin, zmin=zmin,
                canopy=float((chm > 20)[seen].mean()), holes=int(hole.sum()))


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "blocks":
        blocks(Path(argv[1]), Path(argv[2]))
    elif len(argv) == 3 and argv[0] == "merge":
        print(json.dumps(merge(Path(argv[1]), Path(argv[2]))))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
