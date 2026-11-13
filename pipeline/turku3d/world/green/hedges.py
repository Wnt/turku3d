"""Hedges, shrub masses and single shrubs of one 62.5 m cell, measured from the lidar.

The shrub layer is every vegetation return between 0.35 m and 3 m above the ground, on the global 0.25 m grid:
a cell is planted where at least two such returns fall in it, or where sparser returns form a long narrow run. Tree trunks and low branches are thin or sparse and
do not survive the area and density tests; returns inside a roof outline are roof noise.

1. **Mapped hedges** (the base map's `kanta:Pensasaita` lines and OSM `barrier=hedge`) are built along their whole
   line, as the old generator built them (coarse/hedges.py): somebody saw a hedge there, and a leafless hedge in an
   April scan is a few returns a metre that no density test finds. The returns within reach of the line give, at
   each half metre, the width (both edges of the returns across the line), the centre and the height; stretches
   without returns take their neighbours' measures, a line without any the old generator's default. A line with
   next to no returns along it is kept only when it was mapped after the scan. A hedge both maps draw is built once.
2. **Everything else** is grouped into connected patches. A long narrow patch is a hedge nobody mapped (most
   garden hedges): its centre line is the longest path through the ridge of its distance transform. A compact
   patch is a shrub (one instance, measured height and diameter); a large one is a shrub mass (an outline).
3. **Lines of sparse returns** that are left over: a young or leafless hedge on a plot boundary is a row of single
   returns, a metre or less apart, for ten metres and more. Such a row is a hedge (`sparse`) unless the base map
   draws a fence or a railing along it.
4. Each hedge gets a `form`: `clipped` when its top is level and its width even along the line, else `loose`.
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
MIN_HEDGE_WIDTH = 0.5  # metres, for a hedge no map shows
SPARSE_RUN, SPARSE_WIDTH = 4.0, 2.0  # metres: the least length and the most mean width of a run of sparse returns
MAPPED_REACH = 1.75  # metres across a mapped line within which returns are its hedge
MAPPED_STATION = 6  # returns within a metre along a station of a mapped line that measure the hedge there
MAPPED_SEEN = 0.25  # share of a mapped line's stations the scan must measure, or the scan shows no hedge there
MAPPED_DEFAULT = (0.9, 1.4)  # width and height where the scan measures nothing (the old generator's hedge height)
MAPPED_LEAST = (0.7, 0.8)  # a hedge somebody mapped is at least this wide and tall
SAME_HEDGE = 1.5  # metres: a mapped line this near one already built is the same hedge drawn by the other map
PAVED_EDGE = 0.3  # metres a plant may hang over the edge of paving
BED_SEEN = 0.4  # share of a bed's cells the scan must show planted for the bed to be one mass
BED_HEIGHT = (0.5, 2.0)
ROW_LINK = 1.25  # metres between the single returns of one row
ROW_LENGTH = 8.0  # metres: the shortest row of single returns that is a hedge
ROW_HALF = 0.6  # metres to either side of its centre line in which a row's returns lie
ROW_COVER = 0.7  # share of a row's metres that hold a return
ROW_ON = 2.5  # metres from the end of a hedge within which a row begins that carries the hedge on:
ROW_ON_LENGTH, ROW_ON_COVER = 4.0, 0.45  # such a row may be shorter and thinner (the hedge's young or bare end)
ROW_TOP = 2.2  # metres: a taller row of single returns is a mesh fence or a net, not a young hedge
FENCE_NEAR = 0.9  # metres: a row this near a register fence along most of its length is that fence


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
    # A leafless hedge returns little in an April scan (2 to 9 returns a square metre along a line): sparse returns
    # count where they form a long narrow run, which a trunk, a low bough or a tuft does not.
    near = ndimage.uniform_filter(count.astype(np.float32), 3) * 9  # returns within a 0.75 m square
    sparse = (near >= np.where(above > 0, 6, 3)) & (roof < 1.0)
    runs, found = ndimage.label(sparse, structure=np.ones((3, 3)))
    for k, run in enumerate(ndimage.find_objects(runs)):
        reach = float(np.hypot(run[0].stop - run[0].start, run[1].stop - run[1].start)) * RES
        area = float((runs[run] == k + 1).sum()) * RES * RES
        if reach >= SPARSE_RUN and area / reach <= SPARSE_WIDTH:
            planted[run] |= runs[run] == k + 1
    planted = ndimage.binary_closing(planted, np.ones((3, 3), bool))
    planted &= ndimage.uniform_filter(planted.astype(np.float32), 5) > 0.3  # no specks
    return {"planted": planted, "top": top.reshape(rows, cols), "green": green.reshape(rows, cols), "rows": rows, "cols": cols,
            "count": count, "open": (above == 0) & (roof < 1.0)}


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


class Low:
    """The shrub-layer returns of a window, indexed for queries round a point."""

    def __init__(self, window: lidar.Window):
        from scipy.spatial import cKDTree

        returns = window.returns
        use = (returns.h >= LOW) & (returns.h < HIGH)
        self.e, self.n, self.h = returns.e[use], returns.n[use], returns.h[use]
        rgb = returns.rgb[use].astype(np.float64)
        self.green = (rgb[:, 1] - 0.5 * (rgb[:, 0] + rgb[:, 2])) / np.maximum(rgb.mean(axis=1), 1.0)
        self.tree = cKDTree(np.stack([self.e, self.n], axis=1)) if len(self.e) else None


def measure_mapped(line: np.ndarray, low: Low) -> dict:
    """Width, height and centre offset of the returns along a mapped hedge line, per half metre: every station
    within a metre along and MAPPED_REACH across that holds MAPPED_STATION returns is measured."""
    points, normal = along_line(line)
    tangent = np.stack([normal[:, 1], -normal[:, 0]], axis=1)
    count = len(points)
    width, centre, height, green = np.zeros(count), np.zeros(count), np.zeros(count), np.zeros(count)
    near = low.tree.query_ball_point(points, float(np.hypot(1.0, MAPPED_REACH))) if low.tree is not None else [[]] * count
    for k, index in enumerate(near):
        if len(index) < MAPPED_STATION:
            continue
        index = np.asarray(index)
        delta = np.stack([low.e[index] - points[k, 0], low.n[index] - points[k, 1]], axis=1)
        across = delta @ normal[k]
        keep = (np.abs(delta @ tangent[k]) <= 1.0) & (np.abs(across) <= MAPPED_REACH)
        if keep.sum() < MAPPED_STATION:
            continue
        # Both edges of the returns across the line, not the distance from a line that may be drawn off the hedge;
        # the outer returns are a neighbouring bush or a bough (the old generator's fit).
        left, right = np.percentile(across[keep], [15, 85])
        width[k] = float(np.clip(right - left + 0.3, 0.5, 2.6))
        centre[k] = float(np.clip((left + right) / 2, -1.2, 1.2))
        height[k] = float(np.percentile(low.h[index][keep], 90))
        green[k] = float(low.green[index][keep].mean())
    return {"points": points, "normal": normal, "width": width, "centre": centre, "height": height, "green": green, "found": width > 0}


def smooth(values: np.ndarray, found: np.ndarray, size: int = 5) -> np.ndarray:
    """Running median over the found samples; gaps take the nearest found value."""
    index = np.flatnonzero(found)
    filled = np.interp(np.arange(len(values)), index, values[index])
    pad = np.pad(filled, size // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, size), axis=1)


def hedge_row(measured: dict, source: dict, whole: bool = False) -> dict | None:
    """A census row of a measured line. `whole` keeps a mapped line from end to end: stretches the scan did not
    measure take the measures beside them, a line it measured nowhere the default."""
    found = measured["found"]
    least = MAPPED_LEAST if whole else (0.4, 0.4)
    if found.sum() >= 4:
        first, last = (0, len(found) - 1) if whole else np.flatnonzero(found)[[0, -1]]
        cut = slice(first, last + 1)
        width = smooth(measured["width"], found)[cut]
        height = smooth(measured["height"], found)[cut]
        centre = smooth(measured["centre"], found)[cut]
        raw_h = measured["height"][cut][found[cut]]
        raw_w = measured["width"][cut][found[cut]]
        level = float(np.std(raw_h - np.median(raw_h)))
        even = float(np.std(raw_w))
        evergreen = bool(np.mean(measured["green"][cut][found[cut]]) >= 0.055)
    elif whole:
        cut = slice(0, len(found))
        width, height = np.full(len(found), MAPPED_DEFAULT[0]), np.full(len(found), MAPPED_DEFAULT[1])
        centre, level, even, evergreen = np.zeros(len(found)), 0.5, 0.5, False
    else:
        return None
    points = measured["points"][cut] + measured["normal"][cut] * centre[:, None]
    if len(points) < 4:
        return None
    form = "clipped" if level < 0.16 and even < 0.3 and float(np.median(width)) < 1.6 else "loose"
    # One vertex per metre is enough for a swept volume; keep the ends.
    keep = np.unique(np.concatenate([np.arange(0, len(points), 2), [len(points) - 1]]))
    return {
        "line": [[round(float(e), 2), round(float(n), 2)] for e, n in points[keep]],
        "width": [round(float(max(w, least[0])), 2) for w in width[keep]],
        "height": [round(float(max(h, least[1])), 2) for h in height[keep]],
        "form": form, "evergreen": evergreen, "seen": round(float(found[cut].mean()), 2),
        "level": round(level, 2), "even": round(even, 2), **source,
    }


def rows_of_returns(grid: dict, taken: np.ndarray, fences: list, ends: list, box) -> list[dict]:
    """Rows of single returns left over by everything else: [{"line", "height", "green"}], see the module text.
    `ends` are the end points of the hedges already found."""
    import shapely
    from scipy import ndimage

    loose = (grid["count"] >= 1) & grid["open"] & ~taken
    reach = int(round(ROW_LINK / RES))
    yy, xx = np.mgrid[-reach:reach + 1, -reach:reach + 1]
    blobs, count = ndimage.label(ndimage.binary_dilation(loose, yy * yy + xx * xx <= reach * reach))
    barrier = shapely.union_all([shapely.LineString(line) for line in fences if len(line) >= 2]) if fences else None
    found = []
    for k, where in enumerate(ndimage.find_objects(blobs)):
        if np.hypot(where[0].stop - where[0].start, where[1].stop - where[1].start) * RES < ROW_ON_LENGTH + 2 * ROW_LINK:
            continue
        blob = blobs[where] == k + 1
        path = centre_line(blob)
        if path is None or len(path) < 4:
            continue
        line = shapely.LineString(np.stack([box[0] + (where[1].start + path[:, 1] + 0.5) * RES,
                                            box[1] + (where[0].start + path[:, 0] + 0.5) * RES], axis=1)).simplify(0.6)
        carries_on = bool(ends) and min(shapely.distance(shapely.Point(tip), shapely.MultiPoint(ends)) for tip in (line.coords[0], line.coords[-1])) <= ROW_ON + ROW_LINK
        if line.length < (ROW_ON_LENGTH if carries_on else ROW_LENGTH):
            continue
        rows, cols = np.nonzero(blob & loose[where])
        cells = shapely.points(box[0] + (where[1].start + cols + 0.5) * RES, box[1] + (where[0].start + rows + 0.5) * RES)
        on = shapely.distance(line, cells) <= ROW_HALF
        if on.mean() < 0.8:
            continue  # scattered growth, not one row
        along = shapely.line_locate_point(line, cells[on])
        covered = len(np.unique(np.floor(along).astype(int))) / max(np.ceil(line.length), 1.0)
        if covered < (ROW_ON_COVER if carries_on else ROW_COVER):
            continue
        if barrier is not None and line.intersection(barrier.buffer(FENCE_NEAR)).length > 0.5 * line.length:
            continue
        # The row's own returns: what stands beside it (a tree's bough, a bush) belongs to something else.
        tops = grid["top"][where][rows[on], cols[on]]
        if len(tops) < 5:
            continue
        top = float(np.percentile(tops, 90))
        if top > ROW_TOP:
            continue
        found.append({"line": np.array(line.coords), "height": top, "green": float(grid["green"][where][rows[on], cols[on]].mean())})
    return found


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
    """The line through the middle of a connected patch from one end to the other, as (row, col) points: between the
    two cells farthest apart inside the patch, the path that keeps farthest from its edge."""
    from scipy import ndimage
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    rows, cols = np.nonzero(patch)
    if len(rows) < 4:
        return None
    distance = ndimage.distance_transform_edt(patch)
    index = np.full(patch.shape, -1, np.int64)
    index[rows, cols] = np.arange(len(rows))
    first, second, steps, costs = [], [], [], []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        r2, c2 = rows + dr, cols + dc
        ok = (r2 < patch.shape[0]) & (c2 >= 0) & (c2 < patch.shape[1])
        ok[ok] = patch[r2[ok], c2[ok]]
        step = float(np.hypot(dr, dc))
        first.append(index[rows[ok], cols[ok]])
        second.append(index[r2[ok], c2[ok]])
        steps.append(np.full(int(ok.sum()), step))
        costs.append(step / (0.5 * (distance[rows[ok], cols[ok]] + distance[r2[ok], c2[ok]])) ** 2)
    first, second = np.concatenate(first), np.concatenate(second)
    if not len(first):
        return None
    shape = (len(rows), len(rows))
    hops = coo_matrix((np.concatenate(steps), (first, second)), shape=shape).tocsr()
    reach = dijkstra(hops, directed=False, indices=int(np.argmax(distance[rows, cols])))
    start = int(np.argmax(np.where(np.isfinite(reach), reach, -1.0)))
    reach = dijkstra(hops, directed=False, indices=start)
    end = int(np.argmax(np.where(np.isfinite(reach), reach, -1.0)))
    _, before = dijkstra(coo_matrix((np.concatenate(costs), (first, second)), shape=shape).tocsr(), directed=False, indices=start,
                         return_predecessors=True)
    path = [end]
    while path[-1] != start and before[path[-1]] >= 0:
        path.append(int(before[path[-1]]))
    return np.stack([rows[path], cols[path]], axis=1).astype(float)


def mask_of(shape, grid: dict, box) -> tuple[slice, slice, np.ndarray]:
    """The grid cells whose centre lies in a polygon: (rows, columns, mask of that window)."""
    import shapely

    rows, cols = grid["planted"].shape
    c0, r0 = max(int((shape.bounds[0] - box[0]) / RES), 0), max(int((shape.bounds[1] - box[1]) / RES), 0)
    c1, r1 = min(int((shape.bounds[2] - box[0]) / RES) + 1, cols), min(int((shape.bounds[3] - box[1]) / RES) + 1, rows)
    if c1 <= c0 or r1 <= r0:
        return slice(0, 0), slice(0, 0), np.zeros((0, 0), bool)
    ee, nn = np.meshgrid(box[0] + (np.arange(c0, c1) + 0.5) * RES, box[1] + (np.arange(r0, r1) + 0.5) * RES)
    return slice(r0, r1), slice(c0, c1), shapely.contains_xy(shape, ee, nn)


def simple(polygon, least: float = 4.0):
    """The polygon without holes: small holes are filled, a larger one is opened to the outside by a slit."""
    import shapely

    for hole in list(polygon.interiors):
        if shapely.Polygon(hole).area >= least and polygon.geom_type == "Polygon":
            a, b = shapely.ops.nearest_points(shapely.LineString(hole.coords), polygon.exterior)
            cut = polygon.difference(shapely.LineString([a, b]).buffer(0.06, cap_style="square"))
            polygon = max(getattr(cut, "geoms", [cut]), key=lambda g: g.area)
    return shapely.Polygon(polygon.exterior) if polygon.geom_type == "Polygon" and all(shapely.Polygon(h).area < least for h in polygon.interiors) else polygon


def bed_masses(beds: list[dict], cell, grid: dict, box, window, hedged, used: np.ndarray) -> list[dict]:
    """The register's planting beds the scan shows planted over BED_SEEN of their ground, as shrub masses of the
    bed's outline at the scan's height, cut to the cell (`open` marks the vertices on the cut). A bed the scan shows
    less of keeps what the scan finds on it (the dev points' photos show lawn or weeds on such beds as often as
    shrubs); its ground is unmown. What a hedge already built covers is left out."""
    import shapely

    masses = []
    border = cell.exterior
    for bed in beds:
        shape = bed["shape"] if hedged is None else bed["shape"].difference(hedged)
        if shape.is_empty or shape.area < 0.5 * bed["shape"].area:
            continue  # a hedge's own strip
        rs, cs, inside = mask_of(shape, grid, box)
        if not inside.any():
            continue
        planted = grid["planted"][rs, cs] & inside
        seen = float(planted.sum()) / max(int(inside.sum()), 1)
        if seen < BED_SEEN:
            continue
        used[rs, cs] |= inside
        height = float(np.clip(np.median(grid["top"][rs, cs][planted]), *BED_HEIGHT))
        green = float(grid["green"][rs, cs][planted].mean())
        for k, piece in enumerate(getattr(shape.intersection(cell), "geoms", [shape.intersection(cell)])):
            if piece.geom_type != "Polygon" or piece.area < 1.5:
                continue
            piece = simple(shapely.segmentize(shapely.simplify(piece, 0.15), 2.0))
            if piece.geom_type != "Polygon" or len(piece.interiors):
                continue
            ring = np.array(piece.exterior.coords[:-1])
            centre = piece.representative_point()
            masses.append({"ring": [[round(float(e), 2), round(float(n), 2)] for e, n in ring], "height": round(height, 2),
                           "evergreen": green >= 0.055, "key": f"b-{bed['key']}-{round(centre.x * 10)}-{round(centre.y * 10)}-{k}",
                           "ground": [round(float(v), 2) for v in window.ground_height(ring[:, 0], ring[:, 1])],
                           "open": [bool(border.distance(shapely.Point(e, n)) < 0.02) for e, n in ring], "source": bed["source"], "bed": bed["key"],
                           "seen": round(seen, 2)})
    return masses


def measure(cell_box, window: lidar.Window, mapped: list[dict], crowns: list[dict] | None = None, fences: list | None = None,
            public=None, beds: list[dict] | None = None, paved=None) -> dict:
    """{"hedges": [...], "masses": [...], "shrubs": [...]} of a cell. `mapped` rows are {"line": [[e, n], ...],
    "source": layer, "key": id, "genus"?, "surveyed"?}, the better map first; `fences` are the base map's fence and
    railing lines [[e, n], ...]; `public` is the ground the city keeps (streets and green areas): a hedge off it is
    a `garden` hedge; `beds` are the register's planting beds [{"shape": polygon, "key", "source"}]; `paved` is
    the ground the registers give a hard surface: what stands in the shrub layer there is a parked car, a market
    stall or a terrace, and only a mapped hedge is built on it. Hedges and beds are cut to the cell box."""
    import shapely
    from scipy import ndimage

    box = window.box
    grid = layer(window, box)
    low = Low(window)
    cell = shapely.box(*cell_box)
    used = np.zeros_like(grid["planted"])
    hard = np.zeros_like(grid["planted"])
    for part in shapely.get_parts(paved.buffer(-PAVED_EDGE)) if paved is not None and not paved.is_empty else []:
        if part.is_empty or part.geom_type != "Polygon":
            continue
        rs, cs, inside = mask_of(part, grid, box)
        hard[rs, cs] |= inside
    grid["planted"] = grid["planted"] & ~hard
    hedges = []
    built = []  # the mapped lines already built, as drawn

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
        if len(item["line"]) < 2:
            continue
        drawn = shapely.LineString(item["line"])
        # The same hedge in both maps: the part of this line that runs along one already built is not built again.
        new = drawn.difference(shapely.union_all(built).buffer(SAME_HEDGE)) if built else drawn
        if new.length < 0.4 * drawn.length:
            continue
        pieces = [drawn] if new.length > drawn.length - 1.0 else [piece for piece in shapely.get_parts(new) if piece.length >= MIN_HEDGE_LENGTH]
        source = {key: item[key] for key in ("source", "key", "genus") if item.get(key)}
        for piece in pieces:
            measured = measure_mapped(np.array(piece.coords, float)[:, :2], low)
            if measured["found"].mean() >= MAPPED_SEEN:
                row = hedge_row(measured, source, whole=True)
            elif item.get("surveyed", "") >= "2021-04":
                row = hedge_row({**measured, "found": np.zeros(len(measured["found"]), bool)}, source, whole=True)
                row = row and {**row, "form": "clipped", "measured": False}
            else:
                continue
            if row is not None:
                # Its whole measured line is taken, also where the scan found nothing to measure.
                line = np.array(row["line"])
                add(row, {"points": line, "normal": np.zeros_like(line), "centre": np.zeros(len(line)), "width": np.array(row["width"])})
                built.append(piece)

    hedged = shapely.union_all([shapely.LineString(h["line"]).buffer(max(float(np.median(h["width"])) / 2, 0.4) + 0.3) for h in hedges]) if hedges else None
    masses = bed_masses(beds or [], cell, grid, box, window, hedged, used)
    # The low part of a tree is the tree: its trunk, and the whole footprint of a crown that reaches the ground.
    for tree in crowns or []:
        radius = tree["width"] / 2 if tree["base"] <= 2.0 else 0.8
        centre = tree["centre"] if tree["base"] <= 2.0 else (tree["e"], tree["n"])
        used |= rasterise(np.array([centre], float), np.array([radius]), grid, box)
    rest = grid["planted"] & ~used
    labels, count = ndimage.label(rest, structure=np.ones((3, 3)))
    shrubs = []
    made = used | hard  # cells of what was built: a patch that made nothing leaves its returns to the rows
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
                        made[window_slice] |= patch
                        continue
            if area >= 1.0:
                made[window_slice] |= patch  # a mass or a shrub, of this cell or of the one its centre lies in
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
    ends = [tuple(hedge["line"][k]) for hedge in hedges for k in (0, -1)]
    for row in rows_of_returns(grid, ndimage.binary_dilation(made, np.ones((5, 5), bool)), fences or [], ends, box):
        points, _ = along_line(row["line"], 1.0)
        mid = points[len(points) // 2]
        add({"line": [[round(float(e), 2), round(float(n), 2)] for e, n in points], "width": [0.7] * len(points),
             "height": [round(max(row["height"], 0.5), 2)] * len(points), "form": "loose", "evergreen": row["green"] >= 0.055, "sparse": True,
             "level": 0.5, "even": 0.5, "source": "lidar", "key": f"r-{round(mid[0] * 10)}-{round(mid[1] * 10)}"}, None)
    for hedge in hedges:
        line = np.array(hedge["line"])
        hedge["garden"] = public is None or not public.contains(shapely.Point(line[len(line) // 2]))
        hedge["ground"] = [round(float(v), 2) for v in window.ground_height(line[:, 0], line[:, 1])]
    hedges.sort(key=lambda h: h["line"][0])
    return {"hedges": hedges, "masses": masses, "shrubs": sorted(shrubs, key=lambda s: s["id"])}
