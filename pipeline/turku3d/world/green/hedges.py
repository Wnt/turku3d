"""Hedges, shrub masses and single shrubs of one 62.5 m cell, measured from the lidar.

The shrub layer is every vegetation return between 0.35 m and 3 m above the ground, on the global 0.25 m grid:
a cell is planted where at least two such returns fall in it. Tree trunks and low branches are thin or sparse and
do not survive the area and density tests; returns inside a roof outline are roof noise.

1. **Mapped hedges** (the base map's `kanta:Pensasaita` lines and OSM `barrier=hedge`) are measured along their
   line: at each half metre the planted cells across the line give the width and their returns the height. A
   mapped hedge the scan does not show is kept only when it was mapped after the scan.
2. **Everything else** is grouped into connected patches. A long narrow patch is a hedge nobody mapped (most
   garden hedges): its centre line is the longest path through the ridge of its distance transform. A compact
   patch is a shrub (one instance, measured height and diameter); a large one is a shrub mass (an outline).
3. Each hedge gets a `form`: `clipped` when its top is level and its width even along the line, else `loose`.
   Panorama readings may overrule it in the stored description.

A hedge is cut at the cell edge like every areal kind; its cross-section depends only on the measured line, so the
two halves meet.
"""

from __future__ import annotations

import numpy as np

from . import lidar

RES = 0.25
MARGIN = 6.0
LOW, HIGH = 0.35, 3.0
MIN_HEDGE_LENGTH = 2.5
MIN_HEDGE_WIDTH = 0.7  # metres, for a hedge no map shows


def layer(window: lidar.Window, box) -> dict:
    """Shrub-layer rasters of the window box: planted mask, top height (90th percentile) and greenness."""
    from scipy import ndimage

    returns = window.returns
    cols, rows = int(round((box[2] - box[0]) / RES)), int(round((box[3] - box[1]) / RES))
    use = (returns.h >= LOW) & (returns.h < HIGH)
    col = np.clip(((returns.e[use] - box[0]) / RES).astype(np.int64), 0, cols - 1)
    row = np.clip(((returns.n[use] - box[1]) / RES).astype(np.int64), 0, rows - 1)
    index = row * cols + col
    h = returns.h[use]
    count = np.bincount(index, minlength=rows * cols).reshape(rows, cols)
    order = np.lexsort((h, index))
    cells, starts, counts = np.unique(index[order], return_index=True, return_counts=True)
    top = np.zeros(rows * cols, np.float32)
    top[cells] = h[order][starts + (0.9 * (counts - 1)).astype(int)]
    rgb = returns.rgb[use].astype(np.float64)
    green = np.bincount(index, rgb[:, 1] - 0.5 * (rgb[:, 0] + rgb[:, 2]), rows * cols) / np.maximum(np.bincount(index, rgb.mean(axis=1), rows * cols), 1.0)
    # Returns above the shrub layer: a cell under a tree crown needs denser low returns to count as planted.
    above = np.bincount(np.clip(((returns.n[~use] - box[1]) / RES).astype(np.int64), 0, rows - 1) * cols
                        + np.clip(((returns.e[~use] - box[0]) / RES).astype(np.int64), 0, cols - 1),
                        returns.h[~use] >= HIGH, rows * cols).reshape(rows, cols)
    off_r = int(round((box[1] - window.row0 * lidar.GRID) / RES))
    off_c = int(round((box[0] - window.col0 * lidar.GRID) / RES))
    roof = np.kron(window.roof, np.ones((2, 2), np.float32))[off_r:off_r + rows, off_c:off_c + cols]
    planted = (count >= np.where(above > 0, 4, 2)) & (roof < 1.0)
    planted = ndimage.binary_closing(planted, np.ones((3, 3), bool))
    planted &= ndimage.uniform_filter(planted.astype(np.float32), 5) > 0.3  # no specks
    return {"planted": planted, "top": top.reshape(rows, cols), "green": green.reshape(rows, cols), "rows": rows, "cols": cols}


def along_line(line: np.ndarray, step: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Points every `step` metres along a polyline and the unit normal at each."""
    seg = np.diff(line, axis=0)
    length = np.hypot(seg[:, 0], seg[:, 1])
    total = float(length.sum())
    if total < 1e-6:
        return line[:1], np.array([[0.0, 1.0]])
    s = np.arange(0.0, total + 1e-6, step)
    if total - s[-1] > 0.2:
        s = np.append(s, total)
    cumulative = np.concatenate([[0.0], np.cumsum(length)])
    k = np.clip(np.searchsorted(cumulative, s, side="right") - 1, 0, len(seg) - 1)
    t = (s - cumulative[k]) / np.maximum(length[k], 1e-9)
    points = line[k] + seg[k] * t[:, None]
    tangent = seg[k] / np.maximum(length[k], 1e-9)[:, None]
    return points, np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)


def measure_line(line: np.ndarray, grid: dict, box, reach: float = 1.75) -> dict | None:
    """Width, height and centre offset of the planting along a mapped or traced line."""
    points, normal = along_line(line)
    offsets = np.arange(-reach, reach + 1e-6, RES / 2)
    sample = points[:, None, :] + normal[:, None, :] * offsets[None, :, None]
    col = np.floor((sample[..., 0] - box[0]) / RES).astype(int)
    row = np.floor((sample[..., 1] - box[1]) / RES).astype(int)
    inside = (col >= 0) & (col < grid["cols"]) & (row >= 0) & (row < grid["rows"])
    col, row = np.clip(col, 0, grid["cols"] - 1), np.clip(row, 0, grid["rows"] - 1)
    hit = grid["planted"][row, col] & inside
    width = np.zeros(len(points))
    centre = np.zeros(len(points))
    height = np.zeros(len(points))
    green = np.zeros(len(points))
    middle = len(offsets) // 2
    for k in range(len(points)):
        # The run of planted samples nearest the line.
        on = np.flatnonzero(hit[k])
        if not len(on):
            continue
        start = on[np.argmin(np.abs(on - middle))]
        if abs(offsets[start]) > 0.9:
            continue
        a = b = start
        while a > 0 and hit[k, a - 1]:
            a -= 1
        while b < len(offsets) - 1 and hit[k, b + 1]:
            b += 1
        width[k] = offsets[b] - offsets[a] + RES / 2
        centre[k] = (offsets[a] + offsets[b]) / 2
        tops = grid["top"][row[k, a:b + 1], col[k, a:b + 1]]
        height[k] = float(np.percentile(tops, 80))
        green[k] = float(grid["green"][row[k, a:b + 1], col[k, a:b + 1]].mean())
    found = width > 0
    if found.mean() < 0.5 or found.sum() < 4:
        return None
    return {"points": points, "normal": normal, "width": width, "centre": centre, "height": height, "green": green, "found": found}


def smooth(values: np.ndarray, found: np.ndarray, size: int = 5) -> np.ndarray:
    """Running median over the found samples; gaps take the nearest found value."""
    index = np.flatnonzero(found)
    filled = np.interp(np.arange(len(values)), index, values[index])
    pad = np.pad(filled, size // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, size), axis=1)


def hedge_row(measured: dict, source: dict) -> dict | None:
    found = measured["found"]
    first, last = np.flatnonzero(found)[[0, -1]]
    cut = slice(first, last + 1)
    width = smooth(measured["width"], found)[cut]
    height = smooth(measured["height"], found)[cut]
    centre = smooth(measured["centre"], found)[cut]
    points = measured["points"][cut] + measured["normal"][cut] * centre[:, None]
    if len(points) < 4:
        return None
    raw_h = measured["height"][cut][found[cut]]
    raw_w = measured["width"][cut][found[cut]]
    level = float(np.std(raw_h - np.median(raw_h)))
    even = float(np.std(raw_w))
    form = "clipped" if level < 0.16 and even < 0.3 and float(np.median(width)) < 1.6 else "loose"
    # One vertex per metre is enough for a swept volume; keep the ends.
    keep = np.unique(np.concatenate([np.arange(0, len(points), 2), [len(points) - 1]]))
    return {
        "line": [[round(float(e), 2), round(float(n), 2)] for e, n in points[keep]],
        "width": [round(float(max(w, 0.4)), 2) for w in width[keep]],
        "height": [round(float(max(h, 0.4)), 2) for h in height[keep]],
        "form": form, "evergreen": bool(np.mean(measured["green"][cut][found[cut]]) >= 0.055),
        "level": round(level, 2), "even": round(even, 2), **source,
    }


def rasterise(points: np.ndarray, half: np.ndarray, grid: dict, box) -> np.ndarray:
    """Cells within the half width of a measured line."""
    mask = np.zeros((grid["rows"], grid["cols"]), bool)
    for (e, n), r in zip(points, half):
        c, w = (e - box[0]) / RES, (r + 0.4) / RES
        rr = (n - box[1]) / RES
        r0, r1 = int(max(rr - w, 0)), int(min(rr + w + 1, grid["rows"]))
        c0, c1 = int(max(c - w, 0)), int(min(c + w + 1, grid["cols"]))
        mask[r0:r1, c0:c1] = True
    return mask


def centre_line(patch: np.ndarray) -> np.ndarray | None:
    """The longest path through the ridge of a patch's distance transform, as (row, col) points."""
    from scipy import ndimage
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import breadth_first_order, minimum_spanning_tree

    distance = ndimage.distance_transform_edt(patch)
    ridge = patch & (distance >= ndimage.maximum_filter(distance, 3) - 0.3) & (distance >= 0.5 * distance.max()) | (distance == distance.max())
    rows, cols = np.nonzero(ridge)
    if len(rows) < 4:
        return None
    from scipy.spatial import cKDTree

    points = np.stack([rows, cols], axis=1).astype(float)
    pairs = cKDTree(points).query_pairs(3.0, output_type="ndarray")
    if not len(pairs):
        return None
    weight = np.linalg.norm(points[pairs[:, 0]] - points[pairs[:, 1]], axis=1)
    graph = coo_matrix((weight, (pairs[:, 0], pairs[:, 1])), shape=(len(points), len(points)))
    tree = minimum_spanning_tree(graph)
    tree = tree + tree.T

    def far(start):
        order, parent = breadth_first_order(tree, start, directed=False)
        return order[-1], parent

    a, _ = far(0)
    b, parent = far(a)
    path = [b]
    while path[-1] != a and parent[path[-1]] >= 0:
        path.append(parent[path[-1]])
    return points[path]


def measure(cell_box, window: lidar.Window, mapped: list[dict], crowns: list[dict] | None = None) -> dict:
    """{"hedges": [...], "masses": [...], "shrubs": [...]} of a cell. `mapped` rows are {"line": [[e, n], ...],
    "source": layer, "key": id, "genus"?, "surveyed"?}; hedges are cut to the cell box."""
    import shapely
    from scipy import ndimage

    box = window.box
    grid = layer(window, box)
    cell = shapely.box(*cell_box)
    used = np.zeros_like(grid["planted"])
    hedges = []

    def add(row: dict | None, measured: dict | None) -> None:
        if row is None:
            return
        line = shapely.LineString(row["line"])
        if measured is not None:
            used[:] |= rasterise(measured["points"] + measured["normal"] * measured["centre"][:, None], measured["width"] / 2, grid, box)
        for piece in getattr(line.intersection(cell), "geoms", [line.intersection(cell)]):
            if piece.geom_type != "LineString" or piece.length < 1.0:
                continue
            # Carry the per-vertex measures onto the clipped piece by distance along the whole line.
            along = np.array([line.project(shapely.Point(p)) for p in row["line"]])
            at = np.array([line.project(shapely.Point(p)) for p in piece.coords])
            hedges.append({**row, "line": [[round(x, 2), round(y, 2)] for x, y in piece.coords],
                           "width": [round(float(v), 2) for v in np.interp(at, along, row["width"])],
                           "height": [round(float(v), 2) for v in np.interp(at, along, row["height"])]})

    for item in mapped:
        line = np.array(item["line"], float)
        if len(line) < 2:
            continue
        source = {key: item[key] for key in ("source", "key", "genus") if item.get(key)}
        measured = measure_line(line, grid, box)
        if measured is not None:
            add(hedge_row(measured, source), measured)
        elif item.get("surveyed", "") >= "2021-04":
            points, _ = along_line(line, 1.0)
            add({"line": [[round(float(e), 2), round(float(n), 2)] for e, n in points], "width": [0.7] * len(points),
                 "height": [0.9] * len(points), "form": "clipped", "evergreen": False, "measured": False, **source}, None)

    # The low part of a tree is the tree: its trunk, and the whole footprint of a crown that reaches the ground.
    for tree in crowns or []:
        radius = tree["width"] / 2 if tree["base"] <= 2.0 else 0.8
        centre = tree["centre"] if tree["base"] <= 2.0 else (tree["e"], tree["n"])
        used |= rasterise(np.array([centre], float), np.array([radius]), grid, box)
    rest = grid["planted"] & ~used
    labels, count = ndimage.label(rest, structure=np.ones((3, 3)))
    masses, shrubs = [], []
    if count:
        areas = ndimage.sum_labels(rest, labels, range(1, count + 1)) * RES * RES
        slices = ndimage.find_objects(labels)
        for k in np.flatnonzero(areas >= 0.6):
            window_slice = slices[k]
            patch = labels[window_slice] == k + 1
            tops = grid["top"][window_slice][patch]
            height = float(np.percentile(tops, 85))
            if height < 0.5:
                continue
            distance = ndimage.distance_transform_edt(patch)
            half = float(distance.max()) * RES
            area = float(areas[k])
            length = area / max(2 * half * 0.8, RES)
            green = float(grid["green"][window_slice][patch].mean())
            rows, cols = np.nonzero(patch)
            centre_e = box[0] + (window_slice[1].start + cols.mean() + 0.5) * RES
            centre_n = box[1] + (window_slice[0].start + rows.mean() + 0.5) * RES
            if length >= max(MIN_HEDGE_LENGTH, 3.5 * 2 * half) and half <= 1.4:
                path = centre_line(patch)
                if path is not None and len(path) >= 4:
                    line = np.stack([box[0] + (window_slice[1].start + path[:, 1] + 0.5) * RES,
                                     box[1] + (window_slice[0].start + path[:, 0] + 0.5) * RES], axis=1)
                    line = np.array(shapely.LineString(line).simplify(0.3).coords)
                    measured = measure_line(line, {**grid, "planted": rest}, box)
                    row = hedge_row(measured, {"source": "lidar", "key": f"l-{round(centre_e * 10)}-{round(centre_n * 10)}"}) if measured else None
                    # A fence overgrown with something reads as vegetation too, but is thinner than a hedge.
                    if row is not None and float(np.median(row["width"])) >= MIN_HEDGE_WIDTH:
                        add(row, None)
                        continue
            if not (cell_box[0] <= centre_e < cell_box[2] and cell_box[1] <= centre_n < cell_box[3]):
                continue
            if area > 14.0:
                outline = shapely.simplify(shapely.union_all([shapely.box(box[0] + (window_slice[1].start + c) * RES, box[1] + (window_slice[0].start + r) * RES,
                                                                               box[0] + (window_slice[1].start + c + 1) * RES, box[1] + (window_slice[0].start + r + 1) * RES)
                                                                   for r, c in zip(rows, cols)]).buffer(0.25).buffer(-0.25), 0.35)
                if outline.geom_type == "Polygon" and outline.area > 4:
                    masses.append({"ring": [[round(x, 2), round(y, 2)] for x, y in outline.exterior.coords[:-1]], "height": round(height, 2),
                                   "evergreen": green >= 0.055, "key": f"m-{round(centre_e * 10)}-{round(centre_n * 10)}",
                                   "ground": round(float(window.ground_height(centre_e, centre_n)), 2)})
                    continue
            if area < 1.0:
                continue
            shrubs.append({"id": f"s-{round(centre_e * 10)}-{round(centre_n * 10)}", "e": round(float(centre_e), 2), "n": round(float(centre_n), 2),
                           "height": round(height, 2), "width": round(2 * float(np.sqrt(area / np.pi)), 2), "evergreen": green >= 0.055,
                           "ground": round(float(window.ground_height(centre_e, centre_n)), 2)})
    for hedge in hedges:
        line = np.array(hedge["line"])
        hedge["ground"] = [round(float(v), 2) for v in window.ground_height(line[:, 0], line[:, 1])]
    hedges.sort(key=lambda h: h["line"][0])
    return {"hedges": hedges, "masses": masses, "shrubs": sorted(shrubs, key=lambda s: s["id"])}
