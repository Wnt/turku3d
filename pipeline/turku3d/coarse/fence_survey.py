"""Above-ground linear lidar support for mapped walls and short enclosure gaps."""

import json

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.ops import substring

from turku3d import config as C
from turku3d.lidar import PointCloud, TileStore
from turku3d.roads.access import polygon, private_areas
from turku3d.spatial import rows


def profile(line, cloud):
    """A thin, sustained top above the higher adjacent ground, never a retaining drop."""
    ground = cloud.points[cloud.records["classification"] == 2]
    candidates = cloud.points[np.isin(cloud.records["classification"], [1, 2, 6, 12, 17])]
    if len(ground) < 8 or len(candidates) < 6 or line.length < 1:
        return None
    ground_index, index = cKDTree(ground[:, [0, 2]]), cKDTree(candidates[:, [0, 2]])
    steps = np.unique(np.r_[np.arange(0, line.length, 2.5), line.length])
    tops, bases = [], []
    for station in steps:
        centre = np.asarray(line.interpolate(station).coords[0])
        a = np.asarray(line.interpolate(max(0, station-0.1)).coords[0])
        b = np.asarray(line.interpolate(min(line.length, station+0.1)).coords[0])
        direction = (b-a)/max(np.linalg.norm(b-a), 1e-12)
        g = ground[ground_index.query_ball_point(centre, 1.8)]
        if len(g) < 8:
            continue
        lateral = (g[:, [0, 2]]-centre) @ np.array([-direction[1], direction[0]])
        sides = [g[(lateral*sign > 0.6) & (lateral*sign < 1.8),1] for sign in (-1,1)]
        levels = [float(np.median(v)) for v in sides if len(v)>=4]
        base = max(levels) if levels else float(np.quantile(g[:, 1],0.65))
        p = candidates[index.query_ball_point(centre, 1.5)]
        delta = p[:, [0, 2]]-centre
        across = abs(delta @ np.array([-direction[1], direction[0]]))
        along = abs(delta @ direction)
        h = p[:, 1]-base
        use = (across < 0.65) & (along <= 1.25) & (h >= 0.35) & (h <= 3.5)
        h = h[use]
        if len(h) < 3:
            continue
        bins, counts = np.unique(np.floor(h/0.15).astype(int), return_counts=True)
        strong = bins[counts >= max(3, counts.max()*0.4)]
        if not len(strong):
            continue
        mode = (strong.max()+0.5)*0.15
        support = h[abs(h-mode) < 0.2]
        if len(support) >= 3:
            tops.append(float(np.median(support)))
            bases.append(base)
    if len(tops) < max(2, int(np.ceil(len(steps)*0.6))):
        return None
    centre = float(np.median(tops))
    if np.median(abs(np.asarray(tops)-centre)) > 0.25:
        return None
    return dict(height=round(centre,2),base=float(np.median(bases)))


def low_profile(line, cloud):
    """A low cheek or coping wall: returns on the line stay within 0.8 m of the higher side.

    Stair cheek walls and plaza edges on a slope are mapped as plain walls. Their
    granite top is a low coping just above the higher side (often classed as low or
    medium vegetation), so the free-standing test above finds no top and the wall got
    the 1.8 m default. Without returns 0.8-3.5 m above the higher side, the lidar
    says the wall is low: it spans from the lower side to its measured top. A pure
    retaining drop (no coping above the higher side) stays with the retaining stage.
    """
    classes = cloud.records["classification"]
    ground = cloud.points[classes == 2]
    above = cloud.points[np.isin(classes, [1, 3, 4, 6, 12, 17])]
    if len(ground) < 8 or len(above) < 3 or line.length < 1:
        return None
    ground_index, index = cKDTree(ground[:, [0, 2]]), cKDTree(above[:, [0, 2]])
    steps = np.unique(np.r_[np.arange(0, line.length, 2.5), line.length])
    lows, highs, tops, tall = [], [], [], 0
    for station in steps:
        centre = np.asarray(line.interpolate(station).coords[0])
        a = np.asarray(line.interpolate(max(0, station-0.1)).coords[0])
        b = np.asarray(line.interpolate(min(line.length, station+0.1)).coords[0])
        direction = (b-a)/max(np.linalg.norm(b-a), 1e-12)
        normal = np.array([-direction[1], direction[0]])
        g = ground[ground_index.query_ball_point(centre, 1.8)]
        lateral = (g[:, [0, 2]]-centre) @ normal
        sides = [g[(lateral*sign > 0.45) & (lateral*sign < 1.8), 1] for sign in (-1, 1)]
        if min(len(v) for v in sides) < 4:
            continue
        low, high = sorted(float(np.median(v)) for v in sides)
        p = above[index.query_ball_point(centre, 1.5)]
        delta = p[:, [0, 2]]-centre
        y = p[(abs(delta @ normal) < 0.35) & (abs(delta @ direction) <= 1.25), 1]
        if ((y >= high+0.8) & (y <= high+3.5)).sum() >= 3:
            tall += 1
            continue
        top = y[(y >= low+0.1) & (y < high+0.8)]
        # An open-sky wall top 2.5 m long has dozens of returns; a few last returns
        # under canopy or a bush are no measured top.
        if len(top) >= 8:
            lows.append(low)
            highs.append(high)
            tops.append(float(np.quantile(top, 0.75)))
    if tall > len(steps)/3 or len(tops) < max(2, int(np.ceil(len(steps)*0.6))):
        return None
    base, high, top = (float(np.median(v)) for v in (lows, highs, tops))
    if top-base < 0.15 or (high-base >= 0.6 and top-high < 0.35):
        return None
    return dict(height=round(top-base, 2), base=base, low=True)


# A retaining step is measured from planes within 1.3 m of the mapped line
# (retaining_survey.measure); lidar beyond that reach never decides it.
STEP_REACH = 1.5


def step_cloud(store, line, available, buildings):
    """Raw returns around a wall line from the tiles on hand, if they cover its reach."""
    reach = C.BBoxLocal(*line.buffer(STEP_REACH).bounds)
    if not all(b.name in available for b in C.laz_tiles_for_bbox(reach)):
        return None
    bounds = C.BBoxLocal(*line.buffer(2).bounds)
    parts = [store.tile(t).query(bbox=bounds).records for t in C.collision_tiles_for_bbox(bounds)
             if all(b.name in available for b in C.laz_tiles_for_bbox(t.bbox))]
    if not parts:
        return None
    cloud = PointCloud(np.concatenate(parts))
    if not buildings.is_empty:
        cloud = PointCloud(cloud.records[~shapely.intersects_xy(
            buildings.buffer(0.2), cloud.points[:, 0], cloud.points[:, 2])])
    return cloud


def retaining_steps(geometry, tags, on_road, store, available, buildings):
    """Mapped retaining walls take their two levels from the raw step, like WFS walls.

    Without it the wall top came from the smoothed ground 2.5 m away: at a hill foot
    that is far up the slope, a 4 m street wall where the real one is 1.5 m with a
    hedge slope above. Unmeasured parts keep the mapped line and the fallback.
    """
    from .retaining_survey import ground_points, measure

    out = []
    for line in shapely.get_parts(geometry):
        if line.geom_type != "LineString" or line.length < 1.5:
            out.append((line, tags, on_road))
            continue
        cloud = step_cloud(store, line, available, buildings)
        runs = (measure(line, ground_points(cloud.query(classes=[2, 12], last_return=True)))
                if cloud is not None else [])
        out.extend((edge, {**tags, "_survey": profile}, on_road) for edge, profile in runs)
        covered = shapely.union_all([edge.buffer(0.5) for edge, _ in runs])
        rest = line.difference(covered) if runs else line
        for part in shapely.get_parts(rest):
            if part.geom_type != "LineString" or part.length < 1.0:
                continue
            carried = carry_levels(part, runs)
            out.append((part, {**tags, "_survey": carried} if carried else tags, on_road))
    return out


CARRY = 4.0  # a shorter unmeasured end of a measured wall keeps the measured step


def carry_levels(part, runs):
    """Levels for a short unmeasured end beside a measured run, from that run's end.

    Lidar often has no ground within a metre of the wall where it meets a building
    or a hedge; its smoothed grid there is the roof or slope above, metres too high.
    """
    from .retaining import sections

    if part.length > CARRY:
        return None
    ends = shapely.points(np.asarray(part.coords)[[0, -1]])
    best = None
    for edge, profile in runs:
        centres = np.asarray(profile["centres"])
        gap = shapely.distance(ends[:, None], shapely.points(centres[[0, -1]])[None]).min()
        if gap < 1.0 and (best is None or gap < best[0]):
            nearest = int(np.argmin(np.linalg.norm(
                centres - np.asarray(part.interpolate(0.5, normalized=True).coords[0]), axis=1)))
            best = gap, np.asarray(profile["heights"])[nearest], edge
    section = sections(part)
    if best is None or section is None:
        return None
    _, level, edge = best
    centres, left, right = section
    # Keep the measured sides: the run's left side is the part's left side when
    # both follow the mapped line's direction.
    run = np.diff(np.asarray(edge.coords)[[0, -1]], axis=0)[0]
    own = np.diff(np.asarray(part.coords)[[0, -1]], axis=0)[0]
    level = level if run @ own >= 0 else level[::-1]
    return dict(centres=centres, left=left, right=right, heights=np.tile(level, (len(centres), 1)))


def enrich(tile, records, osm, paths):
    """Complete mapped enclosures only where raw lidar confirms an above-ground barrier."""
    source = [(r["geometry_local"], json.loads(r["tags"] or "{}"))
              for _, r in rows(osm, tile.bbox.buffered(100))]
    private = private_areas(source)
    known = shapely.union_all([g for g, t, _ in records if t["barrier"] != "gate"])
    buildings = shapely.union_all([polygon(g) for g,t in source if t.get("building") not in (None,"no")])
    gates = shapely.union_all([g.buffer(2) for g,t in source if t.get("barrier") == "gate"])
    # Without private areas there is no enclosure to complete (an empty collection has no boundary).
    gaps = shapely.GeometryCollection() if private.is_empty else (
        private.boundary.difference(known.buffer(0.5)).difference(buildings.buffer(0.4)).difference(gates))
    pending = list(records)
    donors = [(g,t) for g,t,_ in records if t["barrier"] in ("fence","wall","city_wall","railing")]
    for gap in shapely.get_parts(gaps):
        if (gap.geom_type != "LineString" or gap.is_ring or not 1.5 <= gap.length <= 12
                or not gap.intersects(shapely.box(*tile.bbox.as_tuple()))):
            continue
        if donors:
            donor, tags = min(donors, key=lambda row: row[0].distance(gap))
            if donor.distance(gap) < 1:
                pending.append((gap, {**tags, "_inferred_gap": True}, False))
    store = TileStore(source_paths={p.stem:p for p in paths})
    available = {p.stem for p in paths}
    result = []
    for geometry, tags, on_road in pending:
        if tags["barrier"] == "retaining_wall":
            result.extend(retaining_steps(geometry, tags, on_road, store, available, buildings))
            continue
        if tags["barrier"] not in ("fence","wall","city_wall","railing") or "height" in tags:
            result.append((geometry,tags,on_road))
            continue
        for line in shapely.get_parts(geometry):
            if line.geom_type != "LineString":
                result.append((line,tags,on_road))
                continue
            bounds = C.BBoxLocal(*line.buffer(2).bounds)
            cloud = None
            if all(b.name in available for b in C.laz_tiles_for_bbox(bounds)):
                cloud = store.query(bounds).query(last_return=True)
                if not buildings.is_empty:
                    cloud = PointCloud(cloud.records[~shapely.intersects_xy(
                        buildings.buffer(0.2), cloud.points[:,0], cloud.points[:,2])])
            if cloud is None:
                if not tags.get("_inferred_gap"):
                    result.append((line,tags,on_road))
                continue
            from .retaining_survey import ground_points, measure

            # Equal spans of about 5 m: a short remainder would be too short to
            # measure and keep the 1.8 m default as a stub beside a measured wall.
            edges = np.linspace(0, line.length, max(1, int(round(line.length/5)))+1)
            spans = [line] if tags.get("_inferred_gap") else [
                substring(line, start, end) for start, end in zip(edges[:-1], edges[1:])
            ]
            for span in spans:
                local = cloud.query(C.BBoxLocal(*span.buffer(2).bounds))
                measured = profile(span,local)
                if measured is None and tags["barrier"] in ("wall","city_wall"):
                    measured = low_profile(span,local)
                fitted = ({**tags,"height":str(measured["height"]),"_lidar_base":measured["base"],
                           "_lidar_height":True,**({"_lidar_low":True} if measured.get("low") else {})}
                          if measured is not None else tags)
                profiles = (measure(span,ground_points(local))
                            if tags["barrier"] in ("wall","city_wall") and span.length>=1.5 else [])
                if profiles:
                    result.extend((edge,{**fitted,"_survey":profile},on_road) for edge,profile in profiles)
                elif measured is not None or not tags.get("_inferred_gap"):
                    result.append((span,fitted,on_road))
    return result
