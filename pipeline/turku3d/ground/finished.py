"""Finished ground: construction-era lidar ground replaced by the finished surface.

The city lidar is from 2021-04-17 (``data-manifests/sources.yaml`` ``laz.epoch``). Streets and squares
rebuilt around then (Kauppatori 2019-2022) were caught mid-construction: trenches, excavations and
spoil heaps in what the newer city records describe as a finished paved surface. One city-wide rule,
shared by the generator's ground input (``coarse.inputs``) and the ``finished-ground`` tile step:

1. **Finished-state zones.** ``GIS:Katualueet`` street parts whose completion or renovation year
   (``Valmistumisvuosi``, ``Peruskorjausvuosi``) is the lidar's year or later, except stairs, bridges,
   bridge ramps, tunnels and underpasses (their relief is real). The register calls all of them paved
   (``Olotila``).
2. **Construction-era ground.** Inside a zone, ground more than ``DEPTH`` below its morphological
   closing (a pit or trench narrower than the scale; steps and planes are unchanged by a closing) at
   1, 2 and 4 m rasters up to 40 m radius, or more than ``DEPTH`` above its opening at ``BUMP_RADIUS``
   (a lump; wider raised areas stay), or more than ``DEPTH`` off a base-map spot height
   (``kanta:Korkeuspiste``) surveyed after the lidar. Building footprints act as walls at the
   highest ground next to them, so an excavation against a facade is still a pit. A site is a
   connected region with at least ``MIN_CORE`` m2 of such ground, grown by ``GROW`` m through ground
   at least ``EDGE`` off its closing or opening, at most ``REACH`` m outside the zone.
3. **Finished surface.** The site, the excavation next to it and the ground under nearby footprints
   are filled by a biharmonic (thin-plate) surface that meets the surrounding lidar with its slope
   and passes through the post-lidar spot heights inside it. Everything else keeps its lidar.

Seams: the rule runs in canonical windows (one level-0 tile plus ``BAND`` + ``PAD`` m, from the
complete source rows and the key points in that region) and neighbouring windows are blended with a
partition of unity over a ``2 * BAND`` m band. Every tile therefore computes identical heights where
tiles overlap. ``apply`` replaces the tile's key points in changed ground with a 2 m lattice of the
finished surface; every consumer of the ground key points and grid sees the finished ground.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely
import shapely.geometry

from turku3d import config as C

EPOCH = "2021-04-17"  # data-manifests/sources.yaml laz.epoch
EPOCH_YEAR = 2021
STRUCTURES = frozenset({"Portaat", "Silta", "Siltaluiska", "Tunneli", "Alikulkukäytävä"})
DEPTH = 0.4  # m off the finished surface: construction, not paving relief (kerbs are 0.10-0.16 m)
EDGE = 0.1  # m: a site grows through ground at least this far off
GROW = 2  # m a site grows from its core
PIT_SCALES = ((1, (4, 8)), (2, (8, 13)), (4, (10,)))  # (raster step m, closing radii in its cells)
BUMP_RADIUS = 3  # m: an opening at this radius removes spoil heaps, not raised platforms or quays
REACH = 6  # m a site may extend past its zone (the excavation reaches the facades)
FREE_REACH = 15  # m: excavation and footprint ground this near a site is filled with it
GROW_ROUNDS = 5  # rounds of FREE_REACH in which lower ground next to a site joins it
WALL_RING = 6  # m: a footprint's ring of ground
WALL_QUANTILE = 0.5  # of that ring: the building's wall height for the closing
MIN_CORE = 8  # m2 of construction ground per site
SPOT_WEIGHT = 3.0
TENSION = 0.3  # weight of the membrane rows against the thin-plate rows of the fill
SPOT_SEED = 12  # m around a disagreeing spot height
BAND = 25.0  # m half width of the blend band between windows
PAD = 60.0  # m of data around a window's blend band (covers the 40 m closing and the fill)
POINT_PAD = 10.0  # m of key points beyond a window's raster (the TIN's hull)
LATTICE = 2  # m spacing of the replacement points
CHANGED = 0.01  # m: ground that moved less keeps its lidar key points
MARGIN = 20.0  # m: the tile's key-point window (coarse.sources.POINT_MARGIN)
REGION = C.visual_tile_size(0) + BAND + PAD + POINT_PAD  # m around a tile: its windows' data


@dataclass
class Evidence:
    """Source rows for one tile's windows, in engine-local metres."""

    zones: list = field(default_factory=list)  # (polygon, attrs) renovated paved street parts
    structures: list = field(default_factory=list)  # stairs, bridges, ramps, tunnels: never filled
    streets: list = field(default_factory=list)  # every other street part, of any year: where sites grow
    spots: np.ndarray = field(default_factory=lambda: np.empty((0, 3)))  # x, z, height after EPOCH
    footprints: list = field(default_factory=list)  # building footprint polygons (not afloat)
    water: list = field(default_factory=list)  # water polygons: never construction ground
    points: np.ndarray = field(default_factory=lambda: np.empty((0, 3)))  # key points x, y, z

    def near(self, bbox: C.BBoxLocal) -> bool:
        box = shapely.box(*bbox.as_tuple())
        return any(g.intersects(box) for g, _ in self.zones)


# -- sources ----------------------------------------------------------------------------------------


def zone_rows(frame) -> tuple[list, list, list]:
    """(zones, structures, streets): (polygon, attrs) of the finished-state zones in a GIS:Katualueet
    frame, the polygons of every stair, bridge, ramp, tunnel and underpass part, and of every other
    street part whatever its year."""
    out, structures, streets = [], [], []
    for geom, kind, built, renovated, material, key in zip(
        frame.geometry_local, frame.Katuosalaji, frame.Valmistumisvuosi, frame.Peruskorjausvuosi,
        frame.Pintamateriaali, frame.feature_key, strict=True
    ):
        if geom is None:
            continue
        year = max(_year(built), _year(renovated))
        geom = shapely.make_valid(shapely.force_2d(geom))
        polys = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon" and g.area > 0]
        if str(kind) in STRUCTURES:
            structures.extend(polys)
            continue
        streets.extend(polys)
        if year < EPOCH_YEAR:
            continue
        for poly in polys:
            out.append((poly, {"key": str(key), "kind": str(kind), "year": year, "material": str(material)}))
    return out, structures, streets


def _year(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def spot_rows(frame) -> np.ndarray:
    rows = [
        (float(p.x), float(p.y), float(z))
        for p, z, date in zip(frame.geometry_local, frame.korkeus, frame.alkuPvm, strict=True)
        if p is not None and p.geom_type == "Point" and str(date) > EPOCH and _finite(z)
    ]
    return np.asarray(sorted(set(rows)), np.float64).reshape(-1, 3)


def _finite(value) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def water_rows(frame) -> list:
    out = []
    for geom in frame.geometry_local:
        if geom is None:
            continue
        geom = shapely.make_valid(shapely.force_2d(geom))
        out.extend(g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon" and g.area > 0)
    return out


def on_land(footprints, water) -> list:
    """Footprints standing on land. A moored ship (an LoD2 restaurant boat) is no wall: the water
    between it and the quay is no pit."""
    if not water:
        return list(footprints)
    afloat = shapely.union_all(water)
    return [g for g in footprints if g.intersection(afloat).area < 0.5 * g.area]


def footprint_rows(frame) -> list:
    from turku3d.buildings.source_surfaces import classify_surfaces

    frame = classify_surfaces(frame)
    frame = frame[frame.surface_type == "GroundSurface"]
    out = []
    for _, group in frame.groupby("feature_key", sort=True):
        footprint = shapely.make_valid(shapely.union_all([shapely.force_2d(g) for g in group.geometry_local]))
        out.extend(g for g in getattr(footprint, "geoms", [footprint]) if g.geom_type == "Polygon")
    return out


def load(tile: C.VisualTile, streets, spots, buildings, laz, water=()) -> Evidence:
    """Evidence for ``tile``'s windows from complete source rows (each feature once)."""
    import pandas as pd

    from turku3d.spatial import frame as spatial_frame

    bbox = tile.bbox.buffered(REGION)

    def rows(paths, whole=False):
        frames = [spatial_frame(p, bbox, whole_features=whole) for p in paths if p]
        frames = [f for f in frames if len(f)]
        if not frames:
            return None
        merged = pd.concat(frames, ignore_index=True)
        subset = ["feature_key", "geom_index"] if "geom_index" in merged else ["feature_key"]
        # A feature fetched in several areas keeps one canonical copy, whichever files a tile reads.
        merged["_wkb"] = [g.wkb if g is not None else b"" for g in merged.geometry_local]
        merged = merged.sort_values([*subset, "_wkb"], kind="stable")
        return merged.drop_duplicates(subset=subset).reset_index(drop=True)

    street_frame = rows(streets)
    zones, structures, streets = zone_rows(street_frame) if street_frame is not None else ([], [], [])
    evidence = Evidence(zones=zones, structures=structures, streets=streets)
    if not evidence.near(tile.bbox.buffered(BAND + REACH + 1)):
        return evidence  # no zone reaches the tile: nothing else is read
    spot_frame = rows(spots)
    evidence.spots = spot_rows(spot_frame) if spot_frame is not None else evidence.spots
    water_frame = rows(water)
    evidence.water = water_rows(water_frame) if water_frame is not None else []
    building_frame = rows(buildings, whole=True)
    footprints = footprint_rows(building_frame) if building_frame is not None else []
    evidence.footprints = on_land(footprints, evidence.water)
    evidence.points = key_points(laz, bbox)
    return evidence


def key_points(paths, bbox: C.BBoxLocal) -> np.ndarray:
    """Ground key points (engine x, y, z) in ``bbox``, deduplicated in canonical order."""
    from turku3d.lidar.decoded import columns, source

    e0, n0, e1, n1 = bbox.to_en().as_tuple()
    chunks = []
    for path in sorted(paths):
        las = columns(source(path))
        e, n, h = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        keep = (e >= e0) & (e <= e1) & (n >= n0) & (n <= n1)
        classes = np.asarray(las.classification)[keep]
        x, y, z = C.en_to_local(e[keep], n[keep], h[keep])
        chunks.append(np.column_stack([x, y, z])[~np.isin(classes, [3, 4, 5])])
    points = np.concatenate(chunks) if chunks else np.empty((0, 3))
    return np.unique(points, axis=0)


# -- one window -------------------------------------------------------------------------------------


@dataclass
class Window:
    x0: float
    z0: float
    base: np.ndarray  # lidar TIN on the window's 1 m lattice (row = z)
    solved: np.ndarray  # finished surface
    sites: list  # per site: lattice mask and statistics


def _disk(r):
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    return xx * xx + yy * yy <= r * r


def _grow(mask, r):
    """``mask`` dilated by a disk of radius ``r`` cells (the same cells as ``_disk``), in O(n)."""
    from scipy import ndimage as ndi

    if not mask.any():
        return mask.copy()
    return ndi.distance_transform_edt(~mask) <= r


def _inside(geoms, xs, zs):
    gx, gz = np.meshgrid(xs, zs)
    out = np.zeros(gx.shape, bool)
    if geoms:
        union = shapely.union_all(list(geoms))
        shapely.prepare(union)
        out = shapely.contains_xy(union, gx, gz) | shapely.intersects_xy(union, gx, gz)
    return out


def _tin(points, xs, zs):
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import cKDTree

    gx, gz = np.meshgrid(xs, zs)
    query = np.column_stack([gx.ravel(), gz.ravel()])
    h = LinearNDInterpolator(points[:, [0, 2]], points[:, 1])(query)
    missing = ~np.isfinite(h)
    if missing.any():
        _, nearest = cKDTree(points[:, [0, 2]]).query(query[missing])
        h[missing] = points[nearest, 1]
    return h.reshape(gz.shape)


def _scaled(h, step, op, radius):
    from scipy import ndimage as ndi

    coarse = op(h[::step, ::step], footprint=_disk(radius), mode="nearest")
    return np.repeat(np.repeat(coarse, step, 0), step, 1)[: h.shape[0], : h.shape[1]]


def detect(h, zone, footprints, spots_rc):
    """(pit, bump, core, walls) rasters: construction ground per the rule's step 2, and the ground with
    every building at its wall height."""
    from scipy import ndimage as ndi

    walls = h.copy()
    if footprints.any():
        # A building stands at the high side of the ground round it (WALL_QUANTILE of its ring), so an
        # excavation between a square and a facade is a pit even where the lidar under the building
        # only interpolates the excavation itself.
        buildings, count = ndi.label(footprints)
        ring = _grow(footprints, WALL_RING) & ~footprints
        owner = ndi.distance_transform_edt(~footprints, return_distances=False, return_indices=True)
        nearest = buildings[owner[0], owner[1]]
        ids, heights = nearest[ring], h[ring]
        level = np.full(count + 1, np.nan)
        if len(ids):
            order = np.argsort(ids, kind="stable")
            ids, heights = ids[order], heights[order]
            starts = np.r_[0, np.flatnonzero(np.diff(ids)) + 1]
            for start, end in zip(starts, np.r_[starts[1:], len(ids)], strict=True):
                level[ids[start]] = np.quantile(heights[start:end], WALL_QUANTILE)
        inside = level[buildings[footprints]]
        walls[footprints] = np.where(np.isfinite(inside), inside, h[footprints])
    pit = np.zeros_like(h)
    for step, radii in PIT_SCALES:
        for r in radii:
            pit = np.maximum(pit, _scaled(walls, step, ndi.grey_closing, r) - h)
    bump = np.where(footprints, 0.0, h - ndi.grey_opening(walls, footprint=_disk(BUMP_RADIUS), mode="nearest"))
    core = zone & ~footprints & ((pit > DEPTH) | (bump > DEPTH))
    rows, cols = np.ogrid[: h.shape[0], : h.shape[1]]
    for r, c, level in spots_rc:
        seed = (rows - r) ** 2 + (cols - c) ** 2 <= SPOT_SEED * SPOT_SEED
        core |= seed & zone & ~footprints & (np.abs(h - level) > DEPTH)
    return pit, bump, core, walls


def _biharmonic(h, free, spots_rc):
    """``h`` with the ``free`` cells replaced by the least-squares thin-plate fill (spots as soft rows)."""
    import scipy.sparse as sp
    from scipy import ndimage as ndi
    from scipy.sparse.linalg import spsolve

    rows_n, cols_n = h.shape
    unknown = np.flatnonzero(free.ravel())
    index = np.full(h.size, -1)
    index[unknown] = np.arange(len(unknown))
    support = np.flatnonzero(ndi.binary_dilation(free, iterations=1).ravel())
    r, c = np.divmod(support, cols_n)
    entries_r, entries_c, entries_v = [], [], []
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = np.clip(r + dr, 0, rows_n - 1), np.clip(c + dc, 0, cols_n - 1)
        entries_r += [np.arange(len(support)), np.arange(len(support))]
        entries_c += [rr * cols_n + cc, support]
        entries_v += [np.ones(len(support)), -np.ones(len(support))]
    lap = sp.csr_matrix(
        (np.concatenate(entries_v), (np.concatenate(entries_r), np.concatenate(entries_c))),
        shape=(len(support), h.size),
    )
    if TENSION:
        # Membrane rows (first differences): a thin plate alone carries a trench lip's slope on into
        # a hump; tension keeps the fill within its surroundings.
        n = len(support)
        grad_r, grad_c, grad_v = [], [], []
        for k, (dr, dc) in enumerate(((1, 0), (0, 1))):
            rr, cc = np.clip(r + dr, 0, rows_n - 1), np.clip(c + dc, 0, cols_n - 1)
            grad_r += [k * n + np.arange(n)] * 2
            grad_c += [rr * cols_n + cc, support]
            grad_v += [np.full(n, TENSION), np.full(n, -TENSION)]
        grad = sp.csr_matrix(
            (np.concatenate(grad_v), (np.concatenate(grad_r), np.concatenate(grad_c))), shape=(2 * n, h.size)
        )
        lap = sp.vstack([lap, grad]).tocsr()
    flat = h.ravel()
    is_unknown = index >= 0
    a = lap[:, is_unknown]
    b = -(lap[:, ~is_unknown] @ flat[~is_unknown])
    spot_rows = [(index[rr * cols_n + cc], level) for rr, cc, level in spots_rc if free[rr, cc]]
    if spot_rows:
        s = sp.csr_matrix(
            (np.full(len(spot_rows), SPOT_WEIGHT), (np.arange(len(spot_rows)), [k for k, _ in spot_rows])),
            shape=(len(spot_rows), len(unknown)),
        )
        a = sp.vstack([a, s])
        b = np.r_[b, [SPOT_WEIGHT * level for _, level in spot_rows]]
    a = a.tocsr()
    x = spsolve((a.T @ a).tocsc(), a.T @ b)
    out = flat.copy()
    out[unknown] = x
    return out.reshape(h.shape)


def solve_window(window: C.VisualTile, evidence: Evidence) -> Window | None:
    """The rule over one canonical window; None when no zone reaches its blend band."""
    from scipy import ndimage as ndi

    if not evidence.near(window.bbox.buffered(BAND + REACH + 1)):
        return None
    b = window.bbox.buffered(BAND + PAD)
    xs = np.arange(b.xmin, b.xmax + 0.5, 1.0)
    zs = np.arange(b.zmin, b.zmax + 0.5, 1.0)
    p = evidence.points
    near = (
        (p[:, 0] >= b.xmin - POINT_PAD) & (p[:, 0] <= b.xmax + POINT_PAD)
        & (p[:, 2] >= b.zmin - POINT_PAD) & (p[:, 2] <= b.zmax + POINT_PAD)
    )
    if near.sum() < 16:
        return None
    h = _tin(p[near], xs, zs)
    zone = _inside([g for g, _ in evidence.zones if g.intersects(shapely.box(*b.as_tuple()))], xs, zs)
    footprints = _inside(
        [g for g in evidence.footprints if g.intersects(shapely.box(*b.as_tuple()))], xs, zs
    )
    s = evidence.spots
    inside = (s[:, 0] >= xs[0]) & (s[:, 0] <= xs[-1]) & (s[:, 1] >= zs[0]) & (s[:, 1] <= zs[-1])
    spots_rc = [
        (int(round(z - zs[0])), int(round(x - xs[0])), float(y)) for x, z, y in s[inside]
    ]
    water = _inside([g for g in evidence.water if g.intersects(shapely.box(*b.as_tuple()))], xs, zs)
    zone &= ~water
    spots_rc = [(r, c, y) for r, c, y in spots_rc if zone[r, c] and not footprints[r, c]]
    pit, bump, core, walls = detect(h, zone, footprints, spots_rc)
    reach = _grow(zone, REACH) & ~footprints & ~water
    grown = _grow(core, GROW) & ((pit > EDGE) | (bump > EDGE)) & reach
    mask = core | grown
    labels, count = ndi.label(mask)
    if not count:
        return Window(xs[0], zs[0], h, h, [])
    core_area = ndi.sum(core, labels, np.arange(1, count + 1))
    keep = np.r_[False, core_area >= MIN_CORE]
    mask = keep[labels]
    if not mask.any():
        return Window(xs[0], zs[0], h, h, [])
    window_box = shapely.box(*b.as_tuple())
    blocked = _inside([g for g in evidence.structures if g.intersects(window_box)], xs, zs) | water
    # Sites grow only in street space: any street part (whatever its year) and the strip to the facades.
    street = _inside([g for g in evidence.streets if g.intersects(window_box)], xs, zs) | zone
    space = _grow(street, REACH) & ~blocked
    buildings, _ = ndi.label(footprints)

    def touching(region):
        """Whole footprints that reach ``region``: no ground is observed under a building, so its
        lidar grid is only the interpolation of whatever surrounds it (here, the excavation)."""
        ids = np.unique(buildings[region & footprints])
        return np.isin(buildings, ids[ids > 0])

    for _ in range(GROW_ROUNDS):
        band = _grow(mask, FREE_REACH) & ~mask & space
        # Buildings stand at their wall height in the trial: cheap, and the continuation never bends
        # down to the interpolated excavation under a facade.
        trial = _biharmonic(walls, mask | band, spots_rc)
        join = band & ~footprints & (trial - h > DEPTH) & (pit > EDGE)
        join = ndi.binary_propagation(mask, mask=mask | join) & join
        if not join.any():
            break
        mask |= join
    near_site = _grow(mask, FREE_REACH)
    free = mask | touching(near_site)
    solved = _biharmonic(h, free, spots_rc)
    labels, count = ndi.label(free)
    sites = []
    for k in range(1, count + 1):
        cells = labels == k
        if not (cells & mask).any():
            continue
        sites.append({"cells": cells, "site": cells & mask})
    return Window(xs[0], zs[0], h, solved, sites)


# -- a tile -----------------------------------------------------------------------------------------

_WINDOWS: dict = {}  # (window, evidence digest) -> Window: neighbouring tiles share 4-6 of their 9 windows
WINDOW_CACHE = 24


def _digest(window: C.VisualTile, evidence: Evidence) -> str:
    """The evidence a window reads (its data region), so a cached window is exactly a recomputed one."""
    import hashlib

    b = window.bbox.buffered(BAND + PAD + POINT_PAD)
    box = shapely.box(*b.as_tuple())
    h = hashlib.sha256(window.id.encode())
    for group in (evidence.zones, evidence.structures, evidence.streets, evidence.footprints, evidence.water):
        for item in group:
            g = item[0] if isinstance(item, tuple) else item
            if g.intersects(box):
                h.update(g.wkb)
            h.update(b"|")
    for array in (evidence.spots, evidence.points):
        if len(array):
            x = array[:, 0]
            z = array[:, 1] if array is evidence.spots else array[:, 2]
            sel = (x >= b.xmin) & (x <= b.xmax) & (z >= b.zmin) & (z <= b.zmax)
            h.update(np.ascontiguousarray(array[sel]).tobytes())
        h.update(b"#")
    return h.hexdigest()


def cached_window(window: C.VisualTile, evidence: Evidence) -> Window | None:
    if not evidence.near(window.bbox.buffered(BAND + REACH + 1)):
        return None
    key = (window.id, _digest(window, evidence))
    if key not in _WINDOWS:
        if len(_WINDOWS) >= WINDOW_CACHE:
            _WINDOWS.pop(next(iter(_WINDOWS)))
        _WINDOWS[key] = _stored(key, lambda: solve_window(window, evidence))
    return _WINDOWS[key]


def _store():
    """Shared on-disk windows beside the prepared inputs: the nine tiles a window serves solve it once."""
    import os
    from pathlib import Path

    shared = os.environ.get("TURKU3D_SHARED_CACHE")
    return (Path(shared) if shared else C.work_root() / "cache") / "finished-ground"


def _stored(key, solve):
    """The window from the store (keyed by its evidence digest and this module's code), else solved and kept."""
    import hashlib
    import os
    import pickle
    from pathlib import Path

    code = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    path = _store() / code / f"{key[0]}.{key[1][:32]}.pkl"
    try:
        return pickle.loads(path.read_bytes())
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
        pass
    result = solve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_bytes(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
        temp.replace(path)
    except OSError:
        pass  # a read-only or full cache only costs the solve
    return result


def _ramp(v, low, high):
    """Partition-of-unity weight of the window [low, high] along one axis."""
    return np.clip((v - (low - BAND)) / (2 * BAND), 0, 1) * np.clip(((high + BAND) - v) / (2 * BAND), 0, 1)


def windows(tile: C.VisualTile):
    """The windows blended over a tile, in one city-wide order (identical sums in every tile)."""
    return [C.VisualTile(0, tile.i + di, tile.j + dj) for dj in (-1, 0, 1) for di in (-1, 0, 1)]


def surface(tile: C.VisualTile, evidence: Evidence, xs, zs, solved_windows=None):
    """(finished heights, lidar heights, changed mask, per-window results) on the nodes xs x zs."""
    solved_windows = solved_windows if solved_windows is not None else {
        w.id: cached_window(w, evidence) for w in windows(tile)
    }
    gx, gz = np.meshgrid(xs, zs)
    finished = np.zeros(gx.shape)
    lidar = np.zeros(gx.shape)
    weight = np.zeros(gx.shape)
    delta = np.zeros(gx.shape)
    for w in windows(tile):
        b = w.bbox
        phi = _ramp(gx, b.xmin, b.xmax) * _ramp(gz, b.zmin, b.zmax)
        result = solved_windows.get(w.id)
        weight += phi
        if result is None:
            continue
        c = np.clip(np.round(gx - result.x0).astype(int), 0, result.base.shape[1] - 1)
        r = np.clip(np.round(gz - result.z0).astype(int), 0, result.base.shape[0] - 1)
        delta += phi * (result.solved[r, c] - result.base[r, c])
        finished += phi * result.solved[r, c]
        lidar += phi * result.base[r, c]
    covered = np.abs(weight - 1) < 1e-9
    changed = covered & (np.abs(delta) > CHANGED)
    return finished, lidar, changed, solved_windows


def apply(tile: C.VisualTile, points: np.ndarray, evidence: Evidence | None):
    """(key points, report, region): ``points`` with the construction-era ground of the tile's
    key-point window replaced by the finished surface, and that ground as a polygon; the same array,
    None and None where nothing changes."""
    from scipy import ndimage as ndi

    if evidence is None or not evidence.zones or not evidence.near(tile.bbox.buffered(MARGIN + BAND + REACH)):
        return points, None, None
    b = tile.bbox.buffered(MARGIN)
    xs = np.arange(b.xmin, b.xmax + 0.5, 1.0)
    zs = np.arange(b.zmin, b.zmax + 0.5, 1.0)
    finished, lidar, changed, results = surface(tile, evidence, xs, zs)
    if not changed.any():
        return points, None, None
    replaced = _grow(changed, LATTICE)
    c = np.clip(np.round(points[:, 0] - xs[0]).astype(int), 0, len(xs) - 1)
    r = np.clip(np.round(points[:, 2] - zs[0]).astype(int), 0, len(zs) - 1)
    inside = (points[:, 0] >= b.xmin) & (points[:, 0] <= b.xmax) & (points[:, 2] >= b.zmin) & (points[:, 2] <= b.zmax)
    drop = inside & replaced[r, c]
    lattice = replaced & ((np.round(xs) % LATTICE == 0)[None, :]) & ((np.round(zs) % LATTICE == 0)[:, None])
    lr, lc = np.nonzero(lattice)
    added = np.column_stack([xs[lc], np.round(finished[lr, lc], 3), zs[lr]])
    out = np.concatenate([points[~drop], added])
    return (out, report(tile, xs, zs, finished, lidar, changed, results, int(drop.sum()), len(added)),
            region(changed, xs, zs))


def region(mask, xs, zs):
    """The 1 m nodes of ``mask`` as a polygon (each node's square cell)."""
    from affine import Affine
    from rasterio import features

    transform = Affine(1.0, 0.0, xs[0] - 0.5, 0.0, 1.0, zs[0] - 0.5)  # row = +z
    polys = [shapely.geometry.shape(g) for g, v in features.shapes(mask.astype(np.uint8), mask=mask,
                                                                   transform=transform) if v]
    return shapely.union_all(polys) if polys else None


def report(tile, xs, zs, finished, lidar, changed, results, dropped, added):
    """Per-tile statistics: area changed inside the tile and the sites it touches."""
    t = tile.bbox
    own = ((xs >= t.xmin) & (xs < t.xmax))[None, :] & ((zs >= t.zmin) & (zs < t.zmax))[:, None]
    diff = (finished - lidar)[changed & own]
    own_window = results.get(tile.id)
    box = shapely.box(*t.as_tuple())
    sites = []
    for site in own_window.sites if own_window is not None else ():
        rr, cc = np.nonzero(site["site"])
        x, z = cc + own_window.x0, rr + own_window.z0
        if not shapely.intersects_xy(box, x, z).any():
            continue
        cells = site["cells"]
        rise = own_window.solved[cells] - own_window.base[cells]
        e, n, _ = C.local_to_en(float(np.mean(x)), 0.0, float(np.mean(z)))
        sites.append({
            "centre_en": [round(float(e)), round(float(n))],
            "bbox_en": [round(float(v)) for v in (*C.local_to_en(x.min(), 0, z.max())[:2],
                                                  *C.local_to_en(x.max(), 0, z.min())[:2])],
            "area_m2": int(site["site"].sum()),
            "filled_m2": int(cells.sum()),
            "max_raise_m": round(float(rise.max()), 2),
            "max_lower_m": round(float(-rise.min()), 2),
        })
    return {
        "changed_m2": int((changed & own).sum()),
        "raised_m2": int((diff > CHANGED).sum()),
        "lowered_m2": int((diff < -CHANGED).sum()),
        "max_raise_m": round(float(diff.max()), 2) if len(diff) else 0.0,
        "max_lower_m": round(float(-diff.min()), 2) if len(diff) else 0.0,
        "key_points_replaced": dropped,
        "lattice_points": added,
        "sites": sites,
    }


# -- city survey ------------------------------------------------------------------------------------


def _survey_one(tile_id: str) -> dict:
    import time

    from turku3d.coarse.inputs import finished_ground, finished_sources
    from turku3d.coarse.sources import NoCoverage, Sources

    tile = C.VisualTile.parse(tile_id)
    sources = Sources()
    started = time.process_time()
    try:
        laz, missing = sources.lidar_paths(tile)
    except NoCoverage:
        return {"tile": tile_id, "state": "no-lidar"}
    if missing:
        return {"tile": tile_id, "state": "missing-lidar"}
    value = finished_ground(tile, finished_sources(tile, sources, laz))
    return {"tile": tile_id, "state": "changed" if value["report"] else "unchanged",
            "cpu_s": round(time.process_time() - started, 1), "report": value["report"]}


def _survey_group(tiles) -> list:
    return [_survey_one(t) for t in tiles]


def merge_sites(rows) -> list:
    """City-wide sites: per-tile site rows whose extents overlap are one site."""
    sites = [dict(s, tiles=[r["tile"]]) for r in rows if r.get("report") for s in r["report"]["sites"]]
    merged = []
    for site in sorted(sites, key=lambda s: -s["area_m2"]):
        b = site["bbox_en"]
        for other in merged:
            o = other["bbox_en"]
            if b[0] <= o[2] and o[0] <= b[2] and b[1] <= o[3] and o[1] <= b[3]:
                other["tiles"] = sorted(set(other["tiles"]) | set(site["tiles"]))
                other["bbox_en"] = [min(b[0], o[0]), min(b[1], o[1]), max(b[2], o[2]), max(b[3], o[3])]
                break
        else:
            merged.append(site)
    return merged


def main(argv=None):
    """``survey OUT.jsonl [--jobs N] [tile ...]``: the rule over the given (default: every lidar-covered
    level-0) tiles, one JSON line per tile, then the merged city-wide sites in OUT.sites.json."""
    import argparse
    import json
    from concurrent.futures import ProcessPoolExecutor
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="python -m turku3d.ground.finished")
    parser.add_argument("command", choices=["survey"])
    parser.add_argument("out", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--tiles-file", type=Path, help="one tile id per line (e.g. the zone-reachable tiles)")
    parser.add_argument("tiles", nargs="*")
    args = parser.parse_args(argv)
    tiles = args.tiles or (args.tiles_file.read_text().split() if args.tiles_file else [])
    if not tiles:
        from turku3d.fetch.common import Registry

        blocks = {Path(k).stem for k in Registry(C.raw_dir("laz")).records() if k.startswith("maanpinta/")}
        found = set()
        for name in blocks:  # a block is named by its west and north edge
            e, n = (float(v) for v in name.split("_"))
            for de in (125.0, 375.0):
                for dn in (125.0, 375.0):
                    x, _, z = C.en_to_local(e + de, n - dn, 0.0)
                    found.add(C.VisualTile.at(x, z).id)
        tiles = sorted(found, key=lambda t: (abs(C.VisualTile.parse(t).i) + abs(C.VisualTile.parse(t).j), t))
    done = set()
    if args.out.exists():
        done = {json.loads(line)["tile"] for line in args.out.read_text().splitlines() if line.strip()}
    todo = [t for t in tiles if t not in done]
    # Neighbouring tiles go to one worker in 2x2 groups, so they share its window cache.
    groups = {}
    for t in todo:
        v = C.VisualTile.parse(t)
        groups.setdefault((v.i // 2, v.j // 2), []).append(t)
    with args.out.open("a") as out, ProcessPoolExecutor(args.jobs) as pool:
        for rows in pool.map(_survey_group, list(groups.values()), chunksize=1):
            for row in rows:
                out.write(json.dumps(row) + "\n")
            out.flush()
    rows = [json.loads(line) for line in args.out.read_text().splitlines() if line.strip()]
    sites = merge_sites(rows)
    summary = {
        "tiles": len(rows),
        "changed_tiles": sorted(r["tile"] for r in rows if r.get("state") == "changed"),
        "changed_m2": sum(r["report"]["changed_m2"] for r in rows if r.get("report")),
        "raised_m2": sum(r["report"]["raised_m2"] for r in rows if r.get("report")),
        "lowered_m2": sum(r["report"]["lowered_m2"] for r in rows if r.get("report")),
        "cpu_s": round(sum(r.get("cpu_s", 0) for r in rows), 1),
        "sites": sites,
    }
    args.out.with_suffix(".sites.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "sites"} | {"site_count": len(sites)}))


if __name__ == "__main__":
    main()
