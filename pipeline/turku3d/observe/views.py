"""Which panoramas see which wall, and which to use.

For every exposed wall of a building, panorama spots in front of it are tested with rays against the LoD2 scene
(other buildings and the building's own wings), column by column along the wall, so each panorama gets the stretch
of wall it really sees. Views are then chosen to cover the wall: newest imagery first, frontal before oblique, from
a distance where the eaves are not foreshortened, both providers when they add a second opinion. Trees, vehicles and
scaffolding are not in LoD2: the reader reports those per crop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .lod2 import Building, Scene, Wall
from .panoramas import Pano, Panoramas

MAX_OBLIQUE_DEG = 62.0  # a wall column seen at a flatter angle than this is not read from that panorama
MAX_TOP_ELEVATION_DEG = 68.0  # nor a wall whose eaves are seen more steeply than this
MIN_DISTANCE_M = 3.0
MIN_WALL_M = 2.5  # shorter walls get no crop of their own; they show in their neighbours' margins
COLUMN_M = 1.0
TARGET_PX_PER_M = 40.0  # enough to read glazing bars; beyond it more resolution earns nothing
VIEWS_PER_WALL = 3
SECOND_OPINION_APART_M = 6.0
CROPS_PER_BUILDING = 10  # wall crops in one reading


@dataclass
class WallView:
    wall: Wall
    pano: Pano
    x0: float  # stretch of the wall this panorama sees, metres from the wall's left end
    x1: float
    seen_fraction: float  # of the wall's exposed length
    distance: float  # perpendicular distance camera - wall plane
    oblique_deg: float  # at the middle of the stretch
    px_per_m: float  # panorama pixels per wall metre at the middle of the stretch (horizontal)
    quality: float
    free: np.ndarray = field(repr=False, default=None)  # per column: fraction of rays that reach the wall

    def to_dict(self) -> dict:
        return {"wall": self.wall.id, "panorama": self.pano.to_dict(), "x0_m": round(self.x0, 2),
                "x1_m": round(self.x1, 2), "seen_fraction": round(self.seen_fraction, 3),
                "distance_m": round(self.distance, 1), "oblique_deg": round(self.oblique_deg, 1),
                "px_per_m": round(self.px_per_m, 1), "quality": round(self.quality, 3)}


def reach(wall: Wall) -> float:
    """How far a panorama may be and still read the wall."""
    return float(np.clip(3.0 * (wall.top - wall.base), 35.0, 90.0))


def recency(date: str) -> float:
    """1 for this year's imagery, falling by a tenth per year of age (floor 0.4)."""
    try:
        year = int(str(date)[:4])
    except ValueError:
        return 0.4
    newest = 2026
    return max(0.4, 1.0 - 0.1 * max(0, newest - year))


def panorama_width(pano: Pano) -> int:
    return 6416 if pano.source == "lookaround" else 8192  # Look Around zoom 2, Street View zoom 4


def measure(scene: Scene, building: Building, wall: Wall, pano: Pano) -> WallView | None:
    """The stretch of the wall a panorama sees, or None when it sees less than MIN_WALL_M of it."""
    cam = pano.position
    rel = cam[:2] - wall.a
    d = float(rel @ wall.normal)
    if d < MIN_DISTANCE_M:
        return None
    s = float(rel @ wall.tangent)
    exposed = wall.exposed()
    if not exposed:
        return None
    columns, lows = [], []
    for x0, x1, z0 in exposed:
        n = max(1, round((x1 - x0) / COLUMN_M))
        xs = x0 + (np.arange(n) + 0.5) * (x1 - x0) / n
        columns.append(xs)
        lows.append(np.full(n, z0))
    xs, lows = np.concatenate(columns), np.concatenate(lows)
    oblique = np.degrees(np.arctan2(np.abs(xs - s), d))
    tops = np.interp(xs, [p[0] for p in wall.profile], [p[1] for p in wall.profile])
    top_elev = np.degrees(np.arctan2(tops - cam[2], np.hypot(xs - s, d)))
    ok = (oblique <= MAX_OBLIQUE_DEG) & (top_elev <= MAX_TOP_ELEVATION_DEG)
    if ok.sum() * COLUMN_M < MIN_WALL_M:
        return None
    # Three heights per column: above parked cars, the middle, just under the eaves.
    levels = np.stack([np.minimum(lows + 2.2, tops - 0.3), (lows + tops) / 2, tops - 0.4], 1)
    pts = np.stack([np.repeat(wall.a[0] + xs * wall.tangent[0], 3), np.repeat(wall.a[1] + xs * wall.tangent[1], 3),
                    levels.ravel()], 1)
    pts[:, :2] += wall.normal * 0.05
    sel = np.repeat(ok, 3)
    hit, _ = scene.cast(cam, pts[sel])
    free = np.zeros(len(xs))
    free[ok] = (~np.isfinite(hit)).reshape(-1, 3).mean(1)
    seen = free >= 0.67
    if seen.sum() * COLUMN_M < MIN_WALL_M:
        return None
    # The longest run of seen columns (a gap of one column is bridged: a lamp post or a survey sliver).
    best, start, gap = (0, 0), None, 0
    for i, v in enumerate(list(seen) + [False, False]):
        if v:
            start = i if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 1:
                end = i - gap + 1
                if end - start > best[1] - best[0]:
                    best = (start, end)
                start, gap = None, 0
    i0, i1 = best
    if (i1 - i0) * COLUMN_M < MIN_WALL_M:
        return None
    step = np.diff(xs).mean() if len(xs) > 1 else COLUMN_M
    x0, x1 = float(xs[i0] - step / 2), float(xs[i1 - 1] + step / 2)
    mid = (x0 + x1) / 2
    dist = math.hypot(mid - s, d)
    obl = math.degrees(math.atan2(abs(mid - s), d))
    px_per_m = panorama_width(pano) / (2 * math.pi) / dist * math.cos(math.radians(obl))
    total = sum(b - a for a, b, _ in exposed)
    fraction = min(1.0, (x1 - x0) / total)
    top_mid = float(np.interp(mid, [p[0] for p in wall.profile], [p[1] for p in wall.profile]))
    steep = math.degrees(math.atan2(top_mid - cam[2], dist))
    quality = (min(1.0, px_per_m / TARGET_PX_PER_M) ** 0.5 * math.cos(math.radians(obl)) ** 0.5
               * math.cos(math.radians(max(0.0, steep))) ** 0.5 * recency(pano.date)
               * float(free[i0:i1].mean()))
    return WallView(wall, pano, x0, x1, fraction, d, obl, px_per_m, quality, free)


def choose(candidates: list[WallView], count: int = VIEWS_PER_WALL) -> list[WallView]:
    """Views that together cover the wall, best first; then one second opinion when a different one exists:
    another provider or year, or a camera far enough along the street to see past what stands in front."""
    if not candidates:
        return []
    wall = candidates[0].wall
    chosen: list[WallView] = []
    covered = np.zeros(max(1, math.ceil(wall.length / COLUMN_M)), bool)

    def span(v):
        return slice(max(0, int(v.x0 / COLUMN_M)), max(1, math.ceil(v.x1 / COLUMN_M)))

    def new_share(v):
        return float((~covered[span(v)]).sum()) / len(covered)

    pool = sorted(candidates, key=lambda v: -v.quality)
    while pool and len(chosen) < count:
        best = max(pool, key=lambda v: v.quality * (new_share(v) + 0.05))
        if chosen and new_share(best) < 0.15:
            break
        chosen.append(best)
        covered[span(best)] = True
        pool.remove(best)
    if len(chosen) < count:
        def opinion(v):
            apart = min(float(np.linalg.norm(c.pano.position[:2] - v.pano.position[:2])) for c in chosen)
            different = (all(c.pano.source != v.pano.source for c in chosen)
                         or all(c.pano.date[:4] != v.pano.date[:4] for c in chosen))
            if apart < SECOND_OPINION_APART_M and not different:
                return 0.0
            # A sharper look (a close panorama after a distant one) is worth more than a similar one.
            sharper = float(np.clip((v.px_per_m / max(c.px_per_m for c in chosen)) ** 0.5, 0.7, 1.6))
            return (v.quality * v.seen_fraction * (1.25 if different else 1.0) * sharper
                    * min(1.0, apart / SECOND_OPINION_APART_M))

        second = max(pool, key=opinion, default=None)
        if second is not None and opinion(second) > 0.25:
            chosen.append(second)
    return chosen


def budget(views: dict, limit: int = CROPS_PER_BUILDING) -> dict:
    """At most `limit` views for the building: every seen wall keeps its best view while there is room, larger
    walls first; further views go to the larger walls."""
    area = {wid: max(v.x1 - v.x0 for v in vs) * (vs[0].wall.top - vs[0].wall.base) for wid, vs in views.items()}
    order = sorted(views, key=lambda wid: -area[wid])
    kept: dict = {}
    left = limit
    for rank in range(max((len(v) for v in views.values()), default=0)):
        for wid in order:
            if left and rank < len(views[wid]):
                kept.setdefault(wid, []).append(views[wid][rank])
                left -= 1
    return kept


def building_views(scene: Scene, building: Building, panos: Panoramas, *, per_wall: int = VIEWS_PER_WALL,
                   resolve_limit: int = 14) -> dict:
    """{'views': {wall id: [WallView]}, 'unseen': [{wall, reason}], 'candidates': {wall id: n}} for one building."""
    walls = [w for w in building.walls]
    far = max((reach(w) for w in walls), default=40.0)
    b = building.footprint.bounds
    spots = panos.spots((b[0], b[1], b[2], b[3]), margin=far)
    resolved: dict = {}
    views, unseen, counts, every = {}, [], {}, []
    for wall in walls:
        if wall.ring != 0:
            unseen.append({"wall": wall.id, "reason": "courtyard wall"})
            continue
        if not wall.exposed():
            unseen.append({"wall": wall.id, "reason": "built against a neighbour"})
            continue
        if wall.length < MIN_WALL_M:
            unseen.append({"wall": wall.id, "reason": f"shorter than {MIN_WALL_M} m: read from the adjoining walls"})
            continue
        # Cheap pass on the coverage positions (camera height assumed), then resolve only promising spots.
        rough = []
        for spot in spots:
            rel = np.array([spot["E"], spot["N"]]) - wall.a
            d, s = float(rel @ wall.normal), float(rel @ wall.tangent)
            if d < MIN_DISTANCE_M or d > reach(wall) or s < -reach(wall) or s > wall.length + reach(wall):
                continue
            key = (spot["provider"], spot["id"])
            stand_in = resolved.get(key) or Pano(_Stub(spot), np.array([spot["E"], spot["N"], wall.base + 2.4]), 0.0,
                                                "assumed")
            v = measure(scene, building, wall, stand_in)
            if v is not None:
                rough.append((v.quality * (v.x1 - v.x0), key, spot))
        rough.sort(key=lambda r: -r[0])
        found = []
        for _, key, spot in rough[:resolve_limit]:
            if key not in resolved:
                resolved[key] = panos.resolve(spot, wall.base)
            pano = resolved[key]
            if pano is None:
                continue
            v = measure(scene, building, wall, pano)
            if v is not None:
                found.append(v)
        counts[wall.id] = len(found)
        every += found
        picked = choose(found, per_wall)
        if picked:
            views[wall.id] = picked
        else:
            unseen.append({"wall": wall.id, "reason": "no panorama has a free line of sight" if not rough
                           else "panoramas nearby, none resolved to a usable view"})
    kept = budget(views)
    for wid in views:
        if wid not in kept:
            unseen.append({"wall": wid, "reason": "seen, but left out of the reading: larger walls used the budget"})
    return {"views": kept, "unseen": unseen, "candidates": counts, "contexts": contexts(building, every)}


def contexts(building: Building, found: list[WallView], count: int = 2) -> list[Pano]:
    """Panoramas for whole-building views: those that see most wall area, from clearly different directions."""
    score, pano_of = {}, {}
    centre = building.centre
    for v in found:
        key = (v.pano.source, v.pano.id)
        pano_of[key] = v.pano
        score[key] = score.get(key, 0.0) + (v.x1 - v.x0) * (v.wall.top - v.wall.base) * v.quality
    width = math.hypot(*np.subtract(building.footprint.bounds[2:], building.footprint.bounds[:2]))
    chosen: list[Pano] = []
    for key in sorted(score, key=lambda k: -score[k]):
        pano = pano_of[key]
        offset = pano.position[:2] - centre
        if np.linalg.norm(offset) < 0.45 * width:  # too close to hold the building in one frame
            continue
        bearing = math.degrees(math.atan2(offset[0], offset[1]))
        if all(abs((bearing - math.degrees(math.atan2(*(c.position[:2] - centre))) + 180) % 360 - 180) >= 50
               for c in chosen):
            chosen.append(pano)
        if len(chosen) == count:
            break
    if not chosen and score:
        chosen.append(pano_of[max(score, key=score.get)])
    return chosen


class _Stub:
    """A coverage spot standing in for panorama metadata during the cheap pass."""

    def __init__(self, spot):
        self.id, self.source, self.date = spot["id"], spot["provider"], spot.get("date")
