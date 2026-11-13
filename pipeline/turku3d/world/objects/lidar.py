"""What the city's lidar (about 70 returns per m2, April 2021) knows about street objects.

Per 500 m LAZ tile the returns that stand above the ground and are not roofs are kept once, sorted on a 1 m grid
(`sources/object-lidar/<version>/<tile>.npz`), so that the measurements below read a few cells instead of a tile:

- `column`     the returns around a point: is there a pole, how tall, how thick
- `pole_top`   the height of a pole at a register position, and where its arm points
- `profile`    the height of what stands along a line (a fence, a railing)
- `poles`      thin vertical objects the registers do not list (flag poles, sign and signal posts, masts)
- `spans`      wires across a street with a luminaire hung on them

The cloud is leaf-off and from above: thin posts get tens of returns, boards and mesh fences a row of them.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

from ... import config
from ..sources import lidar as shared

VERSION = "4"
TILE = 500.0
CELL = 1.0
HEAD_REACH, HEAD_TALLEST, HEAD_RETURNS, HEAD_DEEP = 0.6, 13.5, 5, 1.6
TWIN_RETURNS, TWIN_SPREAD = 10, 35.0  # returns in the layer of two arms; degrees a return may lie off their line  # a lamp head over a shaft the scan lost
SIDE = int(TILE / CELL)
GROUND, BUILDING, NOISE = 2, 6, 7
MIN_HAG, MAX_HAG = 0.25, 40.0


def data_root() -> Path:
    root = os.environ.get("TURKU3D_WORLD_DATA")
    return Path(root).expanduser() if root else config.data_root() / "world"


def laz_path(name: str, fetch: bool = True) -> Path | None:
    """The full point cloud tile named by its north-west corner (`sources.lidar.laz_path`)."""
    return shared.laz_path(name, fetch)


def build_tile(name: str) -> dict | None:
    """The object returns of one tile: x, y (m from the tile's south-west corner), hag (m above ground), z, class,
    sorted by 1 m cell, with the cell offsets and the 1 m ground grid."""
    from scipy.ndimage import distance_transform_edt, maximum_filter

    path = laz_path(name)
    if path is None:
        return None
    east, north = (float(v) for v in name.split("_"))
    e0, n0 = east, north - TILE
    las = shared.read(path)
    x, y, z = np.asarray(las.x) - e0, np.asarray(las.y) - n0, np.asarray(las.z)
    cls = np.asarray(las.classification)
    col, row = np.clip(np.floor(x / CELL).astype(np.int64), 0, SIDE - 1), np.clip(np.floor(y / CELL).astype(np.int64), 0, SIDE - 1)
    cell = row * SIDE + col
    # Ground: the lowest ground return per cell, gaps filled from the nearest measured cell.
    ground = np.full(SIDE * SIDE, np.inf)
    is_ground = cls == GROUND
    np.minimum.at(ground, cell[is_ground], z[is_ground])
    ground = ground.reshape(SIDE, SIDE)
    missing = ~np.isfinite(ground)
    if missing.all():
        return None
    if missing.any():
        nearest = distance_transform_edt(missing, return_distances=False, return_indices=True)
        ground = ground[nearest[0], nearest[1]]
    hag = z - ground[row, col]
    # Roof cells: where classified building returns lie; unclassified overlap returns there are roof too.
    roof = np.zeros(SIDE * SIDE, dtype=bool)
    roof[cell[cls == BUILDING]] = True
    roof = roof.reshape(SIDE, SIDE)
    keep = (cls != GROUND) & (cls != BUILDING) & (cls != NOISE) & (hag >= MIN_HAG) & (hag <= MAX_HAG) & ~roof[row, col]
    order = np.argsort(cell[keep], kind="stable")
    cells = cell[keep][order]
    starts = np.searchsorted(cells, np.arange(SIDE * SIDE + 1)).astype(np.uint32)
    centimetres = lambda values: np.clip(np.round(values[keep][order] * 100.0), 0, 65535).astype(np.uint16)  # noqa: E731
    return {
        "x": centimetres(x), "y": centimetres(y), "hag": centimetres(hag), "cls": cls[keep][order].astype(np.uint8),
        "rgb": (np.column_stack([np.asarray(las.red), np.asarray(las.green), np.asarray(las.blue)])[keep][order] >> 8).astype(np.uint8),
        "starts": starts, "ground": ground.astype(np.float32), "roof": maximum_filter(roof, 3),
    }


def tile_path(name: str) -> Path:
    return shared.products_root() / "object-lidar" / VERSION / f"{name}.npz"


def tile_done(name: str) -> bool:
    path = tile_path(name)
    return path.exists() or path.with_suffix(".missing").exists()


def _cut(tile: dict, clip: tuple[float, float, float, float] | None, e0: float, n0: float) -> dict:
    """A stored tile with only the returns of the cells a survey box touches (all of them without a box); the
    cell rows and columns kept are remembered so that a read beyond them can tell."""
    if clip is None:
        return {**tile, "kept": (0, SIDE - 1, 0, SIDE - 1)}
    c0, c1 = int(np.clip(np.floor((clip[0] - e0) / CELL), 0, SIDE - 1)), int(np.clip(np.floor((clip[2] - e0) / CELL), 0, SIDE - 1))
    r0, r1 = int(np.clip(np.floor((clip[1] - n0) / CELL), 0, SIDE - 1)), int(np.clip(np.floor((clip[3] - n0) / CELL), 0, SIDE - 1))
    starts = tile["starts"].astype(np.int64)
    a, b = int(starts[r0 * SIDE]), int(starts[(r1 + 1) * SIDE])
    cell = np.repeat(np.arange(r0 * SIDE, (r1 + 1) * SIDE, dtype=np.int64), np.diff(starts[r0 * SIDE:(r1 + 1) * SIDE + 1]))
    keep = (cell % SIDE >= c0) & (cell % SIDE <= c1)
    cut = {key: np.ascontiguousarray(tile[key][a:b][keep]) for key in ("x", "y", "hag", "cls", "rgb")}
    cut["starts"] = np.searchsorted(cell[keep], np.arange(SIDE * SIDE + 1)).astype(np.uint32)
    return {**cut, "ground": tile["ground"], "roof": tile["roof"], "kept": (r0, r1, c0, c1)}


class ObjectLidar:
    """Object returns by area, tile by tile, built on first use.

    A tile holds up to 16 million returns. With `clip` (a survey box: a build's whole area with a margin) only
    the returns inside it stay in memory, as the centimetres they are stored in; a read that reaches beyond the
    box loads that tile whole, so the answer never depends on the box."""

    def __init__(self, log=print, fetch: bool = True, clip: tuple[float, float, float, float] | None = None):
        self.tiles: dict[str, dict | None] = {}
        self.log = log
        self.fetch = fetch
        self.clip = clip
        self.cache = shared.products_root() / "object-lidar" / VERSION

    def tile(self, e0: float, n0: float, whole: bool = False) -> dict | None:
        name = f"{int(e0)}_{int(n0 + TILE)}"
        held = self.tiles.get(name)
        if name not in self.tiles or (whole and held is not None and held["kept"] != (0, SIDE - 1, 0, SIDE - 1)):
            clip = None if whole else self.clip
            path = self.cache / f"{name}.npz"
            if path.exists():
                with np.load(path) as data:
                    self.tiles[name] = _cut({key: data[key] for key in data.files}, clip, e0, n0)
            elif path.with_suffix(".missing").exists() or (not self.fetch and laz_path(name, fetch=False) is None):
                self.tiles[name] = None
            else:
                built = build_tile(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                if built is None:
                    path.with_suffix(".missing").write_text("no lidar\n")
                else:
                    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.npz")
                    np.savez_compressed(tmp, **built)
                    tmp.replace(path)
                    self.log(f"  object lidar tile {name}: {len(built['x'])} returns above ground")
                self.tiles[name] = built and _cut(built, clip, e0, n0)
        return self.tiles[name]

    def box(self, e0: float, n0: float, e1: float, n1: float, rgb: bool = False) -> np.ndarray:
        """Returns in a survey box as rows (E, N, hag, class), with the scan's own colour (r, g, b: 0..255) on demand."""
        out = []
        metres = lambda values: values.astype(np.float32) * 0.01  # noqa: E731 - stored as centimetres
        for te in np.arange(np.floor(e0 / TILE) * TILE, e1, TILE):
            for tn in np.arange(np.floor(n0 / TILE) * TILE, n1, TILE):
                tile = self.tile(te, tn)
                if tile is None:
                    continue
                c0, c1 = int(max(np.floor((e0 - te) / CELL), 0)), int(min(np.floor((e1 - te) / CELL), SIDE - 1))
                r0, r1 = int(max(np.floor((n0 - tn) / CELL), 0)), int(min(np.floor((n1 - tn) / CELL), SIDE - 1))
                kept = tile["kept"]
                if r0 <= r1 and c0 <= c1 and (r0 < kept[0] or r1 > kept[1] or c0 < kept[2] or c1 > kept[3]):
                    tile = self.tile(te, tn, whole=True)
                starts = tile["starts"]
                for row in range(r0, r1 + 1):
                    a, b = int(starts[row * SIDE + c0]), int(starts[row * SIDE + c1 + 1])
                    if b > a:
                        columns = [metres(tile["x"][a:b]) + te, metres(tile["y"][a:b]) + tn, metres(tile["hag"][a:b]), tile["cls"][a:b]]
                        out.append(np.column_stack(columns + ([tile["rgb"][a:b]] if rgb else [])))
        if not out:
            return np.zeros((0, 7 if rgb else 4))
        points = np.concatenate(out)
        inside = (points[:, 0] >= e0) & (points[:, 0] < e1) & (points[:, 1] >= n0) & (points[:, 1] < n1)
        return points[inside]

    def around(self, e: float, n: float, radius: float) -> np.ndarray:
        """Returns within a horizontal radius of a point: rows (dE, dN, hag, class) relative to it."""
        points = self.box(e - radius, n - radius, e + radius, n + radius)
        points[:, 0] -= e
        points[:, 1] -= n
        return points[np.hypot(points[:, 0], points[:, 1]) <= radius]

    def ground(self, e: float, n: float) -> float:
        tile = self.tile(np.floor(e / TILE) * TILE, np.floor(n / TILE) * TILE)
        if tile is None:
            return float("nan")
        te, tn = np.floor(e / TILE) * TILE, np.floor(n / TILE) * TILE
        return float(tile["ground"][int(min((n - tn) / CELL, SIDE - 1)), int(min((e - te) / CELL, SIDE - 1))])

    def roofed(self, e: float, n: float) -> bool:
        te, tn = np.floor(e / TILE) * TILE, np.floor(n / TILE) * TILE
        tile = self.tile(te, tn)
        return bool(tile is not None and tile["roof"][int(min((n - tn) / CELL, SIDE - 1)), int(min((e - te) / CELL, SIDE - 1))])

    # -- measurements --------------------------------------------------------------------------------------------

    def pole_top(self, e: float, n: float, search: float = 1.2, thick: float = 0.35) -> dict | None:
        """The pole nearest a register position: where its axis really is, how tall it stands, and the arm or
        head at its top. None when no thin vertical run of returns stands within `search` metres."""
        points = self.around(e, n, search + 4.0)
        if not len(points):
            return None
        near = points[np.hypot(points[:, 0], points[:, 1]) <= search]
        near = near[near[:, 2] > 1.0]
        if len(near) < 4:
            return None
        # The axis: the 0.25 m cell whose returns cover the tallest run near the register point.
        key = np.round(near[:, :2] / 0.25).astype(int)
        cells, inverse, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        inverse = inverse.reshape(-1)
        low, high = np.full(len(cells), np.inf), np.full(len(cells), -np.inf)
        np.minimum.at(low, inverse, near[:, 2])
        np.maximum.at(high, inverse, near[:, 2])
        offset = np.hypot(cells[:, 0], cells[:, 1]) * 0.25
        score = (high - low) * np.sqrt(counts) / (1.0 + offset)
        best = int(np.argmax(score))
        if high[best] - low[best] < 1.5:
            return None
        axis = near[inverse == best, :2].mean(axis=0)
        d = np.hypot(points[:, 0] - axis[0], points[:, 1] - axis[1])
        shaft = points[d <= thick]
        heights = np.sort(shaft[:, 2])
        # The shaft is continuous from low down: stop at the first gap of more than 1.5 m above 2 m.
        top = heights[0]
        for k, h in enumerate(heights):
            # A thin pole returns sparsely: only a gap of 3 m, or of 1.5 m with almost nothing above, ends it.
            if top > 2.0 and (h - top > 3.0 or (h - top > 1.5 and len(heights) - k < 3)):
                break
            top = h
        if top < 2.0 or heights[0] > 2.5:
            return None
        result = {"offset": [round(float(axis[0]), 2), round(float(axis[1]), 2)], "top": round(float(top), 2),
                  "returns": int((shaft[:, 2] <= top).sum())}

        def arm_at(top: float) -> dict | None:
            """The arm: returns within a metre below or above the top, 0.4 to 3.5 m from the axis, along one bearing."""
            crown = points[(d > 0.4) & (d <= 3.5) & (points[:, 2] > top - 1.6) & (points[:, 2] < top + 1.2) & (points[:, 3] != 5)]
            if len(crown) < 3:
                return None
            bearing = np.degrees(np.arctan2(crown[:, 0] - axis[0], crown[:, 1] - axis[1])) % 360
            bins = np.bincount((bearing // 15).astype(int) % 24, minlength=24)
            smooth = bins + np.roll(bins, 1) + np.roll(bins, -1)
            k = int(np.argmax(smooth))
            if smooth[k] < 3 or smooth[k] < 0.5 * len(crown):
                return None
            pick = np.abs((bearing - (k * 15 + 7.5) + 180) % 360 - 180) <= 22.5
            reach = np.hypot(crown[pick, 0] - axis[0], crown[pick, 1] - axis[1])
            mean = np.degrees(np.arctan2(np.sum(reach * np.sin(np.radians(bearing[pick]))), np.sum(reach * np.cos(np.radians(bearing[pick]))))) % 360
            return {"bearing": round(float(mean), 1), "reach": round(float(np.percentile(reach, 95)), 2),
                    "height": round(float(np.median(crown[pick, 2])), 2), "returns": int(pick.sum())}

        def twin_at(top: float) -> dict | None:
            """Two arms on one pole: a thin layer of returns at the top in two opposite lobes and nothing else round
            the pole there or under it (a crown fills the layers below; the scan classes arms as vegetation too, so
            the shape decides and not the class)."""
            ring = points[(d > 0.4) & (d <= 3.5)]
            layer = ring[np.abs(ring[:, 2] - top) < 0.8]
            under = ring[(ring[:, 2] > top - 3.0) & (ring[:, 2] <= top - 0.8)]
            if len(layer) < TWIN_RETURNS or len(under) > 0.2 * len(layer):
                return None
            bearing = np.degrees(np.arctan2(layer[:, 0] - axis[0], layer[:, 1] - axis[1])) % 360
            reach = np.hypot(layer[:, 0] - axis[0], layer[:, 1] - axis[1])
            # The line of the two arms: the doubled-angle mean direction of the returns, weighted by their reach.
            doubled = np.radians(bearing * 2)
            line = math.degrees(math.atan2(float(np.sum(reach * np.sin(doubled))), float(np.sum(reach * np.cos(doubled))))) / 2 % 180
            off = (bearing - line + 90) % 180 - 90  # degrees off that line, either way along it
            one = (np.abs(off) <= TWIN_SPREAD) & (np.abs((bearing - line + 180) % 360 - 180) <= 90)
            other = (np.abs(off) <= TWIN_SPREAD) & ~one
            if min(one.sum(), other.sum()) < 4 or min(one.sum(), other.sum()) < 0.4 * max(one.sum(), other.sum()) \
                    or one.sum() + other.sum() < 0.85 * len(layer):
                return None
            reaches = [float(np.percentile(reach[one], 95)), float(np.percentile(reach[other], 95))]
            if abs(reaches[0] - reaches[1]) > 0.6:
                return None
            return {"bearing": round(line, 1), "reach": round(sum(reaches) / 2, 2), "returns": int(len(layer))}

        arm = arm_at(top)
        if arm:
            result["arm"] = arm
        twin = twin_at(top)
        if twin:
            result["twin"] = twin
        # A thin pole can return nothing for metres while its head returns well: a shallow cluster of returns over
        # the axis, above an empty gap, is the top of the same pole (flat for a head on a straight pole, up to a
        # metre and a half deep where the pole bends over into its arm; a crown over a low post is deeper).
        over = points[(d <= HEAD_REACH) & (points[:, 2] > top + 1.5) & (points[:, 2] < HEAD_TALLEST), 2]
        if len(over) >= HEAD_RETURNS and float(np.percentile(over, 95) - np.percentile(over, 5)) <= HEAD_DEEP:
            result["head"] = {"top": round(float(over.max()), 2), "returns": int(len(over))}
            arm = arm_at(float(over.max()))
            if arm:
                result["head"]["arm"] = arm
        return result

    def profile(self, line: np.ndarray, step: float = 1.0, reach: float = 0.35, top: float = 3.5) -> np.ndarray:
        """Height of the tallest return below `top` within `reach` of a polyline, per `step` along it (NaN where no
        return): what stands on a fence or railing line."""
        line = np.asarray(line, dtype=np.float64)[:, :2]
        lengths = np.hypot(*np.diff(line, axis=0).T)
        total = float(lengths.sum())
        count = max(int(np.ceil(total / step)), 1)
        out = np.full(count, np.nan)
        e0, n0, e1, n1 = line[:, 0].min() - 1, line[:, 1].min() - 1, line[:, 0].max() + 1, line[:, 1].max() + 1
        points = self.box(e0, n0, e1, n1)
        points = points[(points[:, 2] < top) & (points[:, 3] != 5)]
        if not len(points):
            return out
        start = 0.0
        for a, b, length in zip(line[:-1], line[1:], lengths):
            if length < 1e-6:
                continue
            u = (b - a) / length
            rel = points[:, :2] - a
            along = rel @ u
            across = np.abs(rel[:, 0] * u[1] - rel[:, 1] * u[0])
            hit = (along >= 0) & (along <= length) & (across <= reach)
            index = np.clip(((start + along[hit]) / step).astype(int), 0, count - 1)
            np.fmax.at(out, index, points[hit, 2])
            start += length
        return out

    def poles(self, e0: float, n0: float, e1: float, n1: float, min_height: float = 2.2) -> list[dict]:
        """Thin vertical objects in a box: columns of returns at most 0.5 m across that rise continuously from below
        1.5 m to their top, with little beside them below the top."""
        from scipy.spatial import cKDTree

        points = self.box(e0 - 1.5, n0 - 1.5, e1 + 1.5, n1 + 1.5)
        if len(points) < 4:
            return []
        key = np.floor(points[:, :2] / 0.25).astype(np.int64)
        order = np.lexsort((points[:, 2], key[:, 1], key[:, 0]))
        points, key = points[order], key[order]
        cells, starts, counts = np.unique(key, axis=0, return_index=True, return_counts=True)
        low, high = points[starts, 2], points[starts + counts - 1, 2]
        candidates = np.nonzero((counts >= 4) & (low <= 1.5) & (high >= min_height))[0]
        candidates = candidates[np.argsort(-high[candidates])]
        tree = cKDTree(points[:, :2])
        taken: set[tuple[int, int]] = set()
        poles = []
        for k in candidates:
            cell = (int(cells[k, 0]), int(cells[k, 1]))
            if cell in taken:
                continue
            column = points[starts[k]:starts[k] + counts[k]]
            gaps = np.diff(column[:, 2])
            top = float(column[-1, 2] if gaps.max(initial=0) <= 1.2 else column[int(np.argmax(gaps > 1.2)), 2])
            if top < min_height:
                continue
            e, n = float(column[:, 0].mean()), float(column[:, 1].mean())
            around = points[tree.query_ball_point([e, n], 1.2)]
            d = np.hypot(around[:, 0] - e, around[:, 1] - n)
            shaft = around[(d <= 0.3) & (around[:, 2] <= top + 0.3)]
            # Slender: between 0.6 m and 80 % of the top, few returns stand 0.35 to 1.2 m away.
            band = (around[:, 2] > 0.6) & (around[:, 2] < max(top * 0.8, 1.6))
            beside, inside = int(((d > 0.35) & band).sum()), int(((d <= 0.35) & band).sum())
            if inside < 4 or beside > 0.6 * inside:
                continue
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    taken.add((cell[0] + di, cell[1] + dj))
            e, n = float(shaft[:, 0].mean()), float(shaft[:, 1].mean())
            if not (e0 <= e < e1 and n0 <= n < n1):
                continue
            cover = np.unique(np.floor(shaft[:, 2] / 0.5)).size / max(np.ceil(top / 0.5), 1)
            if cover < 0.55:
                continue
            poles.append({"e": round(e, 2), "n": round(n, 2), "top": round(top, 2), "returns": int(len(shaft)),
                          "cover": round(float(cover), 2), "vegetation": round(float((shaft[:, 3] == 5).mean()), 2)})
        return poles
