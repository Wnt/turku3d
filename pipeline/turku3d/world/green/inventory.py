"""Every tree of one 62.5 m cell, measured from the lidar and joined with the city's registers and OSM.

A cell owns the trees whose trunk stands in it. Its window is the cell grown by `MARGIN`, on a global 0.25 m grid,
so a tree is measured with its whole crown and its neighbours, and two cells never disagree about a tree.

1. A canopy height model from the high vegetation returns (class 5, at least 2 m above ground). Overhead wires
   and poles carry that class too: a morphological opening removes what is thinner than a crown.
2. Tree tops are the local maxima of the smoothed model within a radius that grows with height. A register tree
   takes the nearest top within `JOIN` metres and keeps its surveyed trunk position; a register tree under a
   larger crown becomes a seed of its own; a register tree with no returns at all is reported, not built.
3. A watershed from the seeds down the canopy splits touching crowns.
4. Each crown is measured from its own returns: height, the ellipse of its footprint, the crown base (the lowest
   level where the crown reaches 40 % of its widest radius), where it is widest, the trunk (the stack of low
   returns under the crown), and three cues of an evergreen crown in a leaf-off scan (how little of the pulse
   reached the ground, how green the returns are, how conical the crown is).

The scan is from April 2021. Growth since then is a rule of the tree kind, not a measurement, and is applied there.
"""

from __future__ import annotations

import heapq
import json

import numpy as np

from . import lidar

RES = 0.25
MARGIN = 16.0
MIN_TREE = 3.0  # metres: lower crowns are shrubs (the planting kind)
JOIN = 2.5
REGISTER_LAYERS = ("GIS:Puut", "kanta:Lehtipuu", "kanta:Havupuu")


def disk(radius: float) -> np.ndarray:
    r = int(np.ceil(radius / RES))
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= (radius / RES) ** 2


def register_trees(features, box) -> list[dict]:
    """Register trees of a survey box, the tree register first; the base map repeats many of its trees."""
    trees: list[dict] = []
    for layer in REGISTER_LAYERS:
        added = []
        for feature in features(layer, box):
            attrs = feature.attrs
            for geom in feature.geoms:
                if geom.type != "Point":
                    continue
                e, n = float(geom.shape.x), float(geom.shape.y)
                if not (box[0] <= e < box[2] and box[1] <= n < box[3]):
                    continue
                if any(abs(e - t["e"]) < 2.0 and abs(n - t["n"]) < 2.0 for t in trees):
                    continue
                row = {"e": round(e, 2), "n": round(n, 2), "layer": layer, "key": attrs.get("Id") or feature.key[:12]}
                if layer == "GIS:Puut":
                    row["species"] = attrs.get("Kasvilaji", "")
                    row["conifer"] = attrs.get("Kasviryhma") == "Havupuu"
                    if attrs.get("Istutusvuosi", "0").isdigit() and int(attrs.get("Istutusvuosi", "0")) > 1800:
                        row["planted"] = int(attrs["Istutusvuosi"])
                else:
                    row["genus"] = attrs.get("suku", "")
                    row["conifer"] = layer == "kanta:Havupuu"
                    row["size"] = attrs.get("pituus", "")
                    row["surveyed"] = attrs.get("alkuPvm", "")[:10]
                added.append(row)
        trees += added
    return trees


def flood(height: np.ndarray, mask: np.ndarray, seeds: list[tuple[int, int]], reach: list[float]) -> np.ndarray:
    """Marker watershed from the tops down: labels 1..n, 0 where no seed reaches within its reach (cells)."""
    labels = np.zeros(height.shape, np.int32)
    heap: list[tuple[float, int, int, int]] = []
    for k, (r, c) in enumerate(seeds, 1):
        if labels[r, c] == 0:
            labels[r, c] = k
            heapq.heappush(heap, (-float(height[r, c]), r, c, k))
    rows, cols = height.shape
    while heap:
        _, r, c, k = heapq.heappop(heap)
        sr, sc = seeds[k - 1]
        limit = reach[k - 1] ** 2
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols and mask[rr, cc] and labels[rr, cc] == 0:
                if (rr - sr) ** 2 + (cc - sc) ** 2 <= limit:
                    labels[rr, cc] = k
                    heapq.heappush(heap, (-float(height[rr, cc]), rr, cc, k))
    return labels


def canopy(window: lidar.Window, box) -> dict:
    """Rasters of the window on the global 0.25 m grid: row 0 at its south edge."""
    from scipy import ndimage

    returns = window.returns
    cols = int(round((box[2] - box[0]) / RES))
    rows = int(round((box[3] - box[1]) / RES))
    col = np.clip(((returns.e - box[0]) / RES).astype(np.int64), 0, cols - 1)
    row = np.clip(((returns.n - box[1]) / RES).astype(np.int64), 0, rows - 1)
    high = returns.cls == lidar.HIGH
    top = np.zeros((rows, cols), np.float32)
    np.maximum.at(top, (row[high], col[high]), returns.h[high])
    raw = top > 0
    closed = ndimage.binary_closing(raw, disk(0.5), border_value=0)
    solid = ndimage.binary_opening(closed, disk(0.5))
    mask = ndimage.binary_dilation(solid, disk(0.5)) & closed
    filled = ndimage.grey_closing(top, footprint=disk(0.5))
    smooth = ndimage.gaussian_filter(np.where(mask, filled, 0.0), 2.0)
    weight = ndimage.gaussian_filter(mask.astype(np.float32), 2.0)
    smooth = np.where(mask, smooth / np.maximum(weight, 0.2), 0.0)
    return {"top": top, "mask": mask, "smooth": smooth, "row": row, "col": col, "rows": rows, "cols": cols}


def tree_tops(smooth: np.ndarray, mask: np.ndarray) -> list[tuple[int, int]]:
    """Local maxima of the canopy within a radius that grows with the height of the tree."""
    from scipy import ndimage

    tops = np.zeros(smooth.shape, bool)
    bands = ((MIN_TREE, 6.0, 1.25), (6.0, 10.0, 1.75), (10.0, 16.0, 2.5), (16.0, 99.0, 3.25))
    for low, high, radius in bands:
        peak = ndimage.maximum_filter(smooth, footprint=disk(radius))
        tops |= mask & (smooth >= low) & (smooth < high) & (smooth >= peak - 1e-6)
    labels, count = ndimage.label(tops, structure=np.ones((3, 3)))
    if not count:
        return []
    centres = ndimage.maximum_position(smooth, labels, range(1, count + 1))
    return [(int(r), int(c)) for r, c in centres]


def measure_crown(e, n, h, cls, rgb, first, cells_e, cells_n, hits: float) -> dict | None:
    """One crown from its returns (survey coordinates, height above ground) and its footprint cells."""
    if len(h) < 12 or len(cells_e) < 6:
        return None
    height = float(np.percentile(h, 99.5))
    area = len(cells_e) * RES * RES
    ce, cn = float(cells_e.mean()), float(cells_n.mean())
    cov = np.cov(np.vstack([cells_e - ce, cells_n - cn])) + np.eye(2) * RES * RES / 12
    values, vectors = np.linalg.eigh(cov)
    minor, major = 4.0 * np.sqrt(np.maximum(values, 1e-6))
    scale = np.sqrt(area / (np.pi * major * minor / 4.0))  # keep the ellipse at the crown's area
    major, minor = float(major * scale), float(minor * scale)
    angle = float(np.degrees(np.arctan2(vectors[0, 1], vectors[1, 1])) % 180.0)  # major axis, degrees east of north

    # Vertical profile: the 80th percentile horizontal distance from the crown axis per half-metre level.
    distance = np.hypot(e - ce, n - cn)
    levels = np.arange(0.5, max(height, 1.0) + 0.5, 0.5)
    level = np.clip(((h - 0.5) / 0.5).astype(int), 0, len(levels) - 1)
    radius = np.zeros(len(levels))
    count = np.bincount(level, minlength=len(levels))
    order = np.lexsort((distance, level))
    starts = np.concatenate([[0], np.cumsum(count)])
    for k in np.flatnonzero(count >= 4):
        radius[k] = distance[order][starts[k] + int(0.8 * (count[k] - 1))]
    radius = np.convolve(radius, [0.25, 0.5, 0.25], mode="same")
    widest = int(np.argmax(radius))
    wide = float(radius[widest])
    if wide <= 0:
        return None
    # The crown base: down from the widest level, the lowest level that is still wide and holds a fair share of the
    # returns a level of this crown has. A fence, a hedge or parked cars under the tree are a separate, thinner
    # layer (a gap or a few returns between them and the boughs), and the one or two lowest boughs of a leafless
    # tree return little: neither is where the foliage ends in summer.
    wide_enough = radius >= 0.4 * wide
    typical = float(np.median(count[wide_enough & (count > 0)])) if (wide_enough & (count > 0)).any() else 0.0
    crown = wide_enough & (count >= max(3, 0.01 * len(h), 0.35 * typical))
    lowest, gap = widest, 0
    for k in range(widest, -1, -1):
        if crown[k]:
            lowest, gap = k, 0
        else:
            gap += 1
            if gap >= 2:
                break
    base = float(levels[lowest]) if crown.any() else 0.4 * height
    base = float(np.clip(base, 0.5, 0.75 * height))
    widest_at = float(np.clip((levels[widest] - base) / max(height - base, 0.5), 0.0, 1.0))
    upper = float(np.interp(base + 0.8 * (height - base), levels, radius)) / wide
    # How much of the tree is its top: the share of the returns above the undergrowth that lie in the upper two
    # fifths of its height (a pine carries its crown there, a spruce is green to the ground).
    above = h > 2.0
    high = float((h[above] > 0.6 * height).mean()) if above.sum() >= 8 else 0.0

    # The trunk: the stack of low returns nearest the crown axis.
    trunk = None
    low = (h >= 0.5) & (h < min(3.0, max(base, 1.5))) & (distance < 0.45 * max(major, 1.0))
    if low.sum() >= 5:
        te, tn = np.floor(e[low] / 0.5), np.floor(n[low] / 0.5)
        keys = te.astype(np.int64) * 1_000_003 + tn.astype(np.int64)
        bins = np.floor(h[low] / 0.5).astype(np.int64)
        best, spread = None, 0
        for key in np.unique(keys):
            at = keys == key
            levels_hit = len(np.unique(bins[at]))
            if levels_hit > spread or (levels_hit == spread and at.sum() > best[1]):
                best, spread = (key, at.sum(), at), levels_hit
        if spread >= 3:
            trunk = [float(e[low][best[2]].mean()), float(n[low][best[2]].mean())]

    upper_half = h > base + 0.5 * (height - base)
    colour = rgb[upper_half].astype(np.float64) if upper_half.sum() >= 8 else rgb.astype(np.float64)
    green = float((colour[:, 1] - 0.5 * (colour[:, 0] + colour[:, 2])).mean() / max(colour.mean(), 1.0))
    firsts = int(first.sum())
    return {
        "height": round(height, 2), "width": round(2.0 * np.sqrt(area / np.pi), 2), "major": round(major, 2),
        "minor": round(minor, 2), "angle": round(angle, 1), "base": round(base, 2), "widestAt": round(widest_at, 2),
        "upper": round(upper, 2), "high": round(high, 2), "centre": [round(ce, 2), round(cn, 2)], "trunk": trunk and [round(v, 2) for v in trunk],
        "returns": int(len(h)), "through": round(float(hits) / max(firsts, 1), 3), "green": round(green, 3),
        "density": round(len(h) / area, 1),
    }


def measure(cell_box, window: lidar.Window, register: list[dict], osm: list[dict] | None = None, keep_all: bool = False) -> dict:
    """The trees whose trunk stands in the cell box, and the register trees the lidar does not show. With
    `keep_all`, also every tree of the window (`window_trees`: the hedge reading leaves their low parts alone)."""
    from scipy import ndimage

    box = window.box
    grid = canopy(window, box)
    smooth, mask = grid["smooth"], grid["mask"]
    tops = tree_tops(smooth, mask)
    top_en = np.array([[box[0] + (c + 0.5) * RES, box[1] + (r + 0.5) * RES] for r, c in tops]).reshape(-1, 2)

    # Join register trees and tops one to one, nearest pairs first. The top of a broad crown is seldom above its
    # trunk, so the reach grows with the height of the top.
    pairs = []
    for t, tree in enumerate(register):
        for k in range(len(tops)):
            distance = float(np.hypot(top_en[k, 0] - tree["e"], top_en[k, 1] - tree["n"]))
            if distance <= float(np.clip(0.22 * smooth[tops[k]], JOIN, 5.0)):
                pairs.append((distance, t, k))
    top_of: dict[int, int] = {}
    taken = np.zeros(len(tops), bool)
    for distance, t, k in sorted(pairs):
        if t not in top_of and not taken[k]:
            top_of[t] = k
            taken[k] = True
    for distance, t, k in pairs:
        if distance <= JOIN and t in top_of:
            taken[k] = True  # a forked crown has several tops: they are one tree
    seeds: list[tuple[int, int]] = []
    owner: list[dict | None] = []
    missing = []
    for t, tree in enumerate(register):
        if t in top_of:
            seeds.append(tops[top_of[t]])
            owner.append(tree)
            continue
        r, c = int((tree["n"] - box[1]) / RES), int((tree["e"] - box[0]) / RES)
        r0, c0 = max(r - 6, 0), max(c - 6, 0)
        patch = np.where(mask[r0:r + 7, c0:c + 7], smooth[r0:r + 7, c0:c + 7], 0.0)
        if patch.size and patch.max() >= MIN_TREE - 0.5:
            pr, pc = np.unravel_index(int(np.argmax(patch)), patch.shape)
            seeds.append((r0 + int(pr), c0 + int(pc)))
            owner.append(tree)
        else:
            missing.append(tree)
    for k, top in enumerate(tops):
        if not taken[k]:
            seeds.append(top)
            owner.append(None)

    reach = [min(12.0, 2.0 + 0.6 * float(smooth[s])) / RES for s in seeds]
    labels = flood(smooth, mask, seeds, reach)
    returns = window.returns
    use = (returns.h >= 1.0) & (returns.cls >= lidar.MEDIUM)
    label_of = labels[grid["row"], grid["col"]]
    use &= label_of > 0
    order = np.argsort(label_of[use], kind="stable")
    index = np.flatnonzero(use)[order]
    bounds = np.searchsorted(label_of[index], np.arange(1, len(seeds) + 2))
    cell_rows, cell_cols = np.nonzero(labels)
    cell_label = labels[cell_rows, cell_cols]
    cell_order = np.argsort(cell_label, kind="stable")
    cell_bounds = np.searchsorted(cell_label[cell_order], np.arange(1, len(seeds) + 2))
    # Ground returns under each crown, from the 0.5 m raster.
    hit_grid = np.kron(window.hits, np.ones((2, 2), np.float32)) / 4.0
    off_r = int(round((box[1] - window.row0 * lidar.GRID) / RES))
    off_c = int(round((box[0] - window.col0 * lidar.GRID) / RES))
    hit_grid = hit_grid[off_r:off_r + grid["rows"], off_c:off_c + grid["cols"]]
    roof = np.kron(window.roof, np.ones((2, 2), np.float32))[off_r:off_r + grid["rows"], off_c:off_c + grid["cols"]]
    hit_sum = ndimage.sum_labels(hit_grid, labels, range(1, len(seeds) + 1)) if seeds else []
    roofed = ndimage.mean(roof > 2.5, labels, range(1, len(seeds) + 1)) if seeds else []

    trees, everywhere = [], []
    for k in range(len(seeds)):
        at = index[bounds[k]:bounds[k + 1]]
        cells = cell_order[cell_bounds[k]:cell_bounds[k + 1]]
        cells_e = box[0] + (cell_cols[cells] + 0.5) * RES
        cells_n = box[1] + (cell_rows[cells] + 0.5) * RES
        crown = measure_crown(returns.e[at], returns.n[at], returns.h[at], returns.cls[at], returns.rgb[at],
                              returns.first[at], cells_e, cells_n, float(hit_sum[k]))
        if crown is None or crown["height"] < MIN_TREE:
            if owner[k] is not None:
                missing.append(owner[k])
            continue
        tree = owner[k]
        apex = (box[0] + (seeds[k][1] + 0.5) * RES, box[1] + (seeds[k][0] + 0.5) * RES)
        if tree is None:
            # Not a tree: a pole or mast (tall and thin), or a crown that is mostly roof planting or roof noise.
            if crown["width"] < 1.2 or (crown["height"] > 6.0 and crown["width"] < 0.18 * crown["height"]):
                continue
            if roofed[k] > 0.6:
                continue
            e, n = crown["trunk"] or [(crown["centre"][0] + apex[0]) / 2, (crown["centre"][1] + apex[1]) / 2]
            source = "lidar"
        else:
            e, n = tree["e"], tree["n"]
            source = "register+lidar"
        everywhere.append({"e": e, "n": n, "centre": crown["centre"], "width": crown["width"], "base": crown["base"]})
        if not (cell_box[0] <= e < cell_box[2] and cell_box[1] <= n < cell_box[3]):
            continue
        row = {"id": f"{tree['layer'].split(':')[1].lower()}-{tree['key']}" if tree else f"lidar-{round(e * 10)}-{round(n * 10)}",
               "e": round(e, 2), "n": round(n, 2), "ground": round(float(window.ground_height(e, n)), 2), "source": source,
               "top": [round(apex[0], 2), round(apex[1], 2)], **crown}
        if tree:
            row["register"] = {key: value for key, value in tree.items() if key not in ("e", "n") and value not in ("", None)}
        trees.append(row)

    for row in osm or []:
        if not trees:
            break
        distance = [np.hypot(t["e"] - row["e"], t["n"] - row["n"]) for t in trees]
        k = int(np.argmin(distance))
        if distance[k] <= 3.0 and "osm" not in trees[k]:
            trees[k]["osm"] = {key: value for key, value in row.items() if key not in ("e", "n")}
    absent = [{**t} for t in missing if cell_box[0] <= t["e"] < cell_box[2] and cell_box[1] <= t["n"] < cell_box[3]]
    for tree in absent:
        tree["ground"] = round(float(window.ground_height(tree["e"], tree["n"])), 2)
    trees.sort(key=lambda t: (t["e"], t["n"]))
    return {"trees": trees, "absent": absent, **({"window_trees": everywhere} if keep_all else {})}


def dumps(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
