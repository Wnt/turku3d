"""Mapped harbour structures and classified-lidar vessel measurements, city-wide."""

import json
from functools import lru_cache

import numpy as np
import shapely
from scipy.ndimage import grey_opening, label, maximum_filter1d, median_filter

from turku3d import config as C


def source_paths(tile):
    paths = []
    for block in C.laz_tiles_for_bbox(tile.bbox.buffered(150)):
        for directory in (C.work_root() / "lidar-supplement", C.raw_dir("laz") / "pistepilvi"):
            path = directory / (block.name + ".laz")
            if path.is_file():
                paths.append(path)
                break
    return paths


@lru_cache(maxsize=2)
def point_store(paths):
    from turku3d.lidar import TileStore

    return TileStore(source_paths={p.stem: p for p in paths})


def cloud_for(shape, paths, margin=0):
    bbox = C.BBoxLocal(*shape.buffer(margin).bounds)
    sources = {p.stem: p for p in paths}
    if not sources or any(b.name not in sources for b in C.laz_tiles_for_bbox(bbox)):
        return None
    return point_store(tuple(paths)).query(bbox)


def axis(shape):
    corners = np.asarray(shape.minimum_rotated_rectangle.exterior.coords)[:-1]
    sides = np.roll(corners, -1, axis=0) - corners
    direction = sides[np.argmax(np.linalg.norm(sides, axis=1))]
    return direction / np.linalg.norm(direction)


def survey_ship(boat, points, classes):
    """Preserve the mapped hull; measure upper structures rather than extruding a house."""
    shape = boat["outline"]
    keep = np.isin(classes, (1, 6, 12, 17)) & shapely.intersects_xy(shape, points[:, 0], points[:, 2])
    p = points[keep]
    p = p[(p[:, 1] > 0.6) & (p[:, 1] < 65)]
    if len(p) < 30:
        return
    direction = axis(shape)
    normal = np.array([-direction[1], direction[0]])
    centre = np.asarray(shape.centroid.coords[0])
    local = (p[:, [0, 2]] - centre) @ np.array([direction, normal]).T
    # Dense low returns describe the working deck, not high roofs or rigging.
    low = p[(p[:, 1] <= 5), 1]
    low_xy = p[(p[:, 1] <= 5)][:, [0, 2]]
    low_area = len(np.unique(np.floor(low_xy), axis=0))
    if len(low) >= 20 and low_area >= shape.area * .08:
        boat["deck"] = float(np.clip(np.percentile(low, 35), 1.0, 4.5))
    deck = boat["deck"]
    # A longitudinal height envelope resolves separate masts despite sparse rigging.
    start = np.floor(local[:, 0].min())
    bins = np.floor(local[:, 0] - start).astype(int)
    envelope = np.full(bins.max() + 1, deck)
    np.maximum.at(envelope, bins, p[:, 1])
    peaks = np.flatnonzero((envelope == maximum_filter1d(envelope, size=9)) & (envelope > deck + 10))
    masts = []
    for index in sorted(peaks, key=lambda k: -envelope[k]):
        top = envelope[index]
        # A mast needs a vertical column near the centreline, not just one high wire.
        near = (np.abs(local[:, 0] - (start + index + .5)) < 1.8) & (np.abs(local[:, 1]) < 2)
        column = p[near]
        levels = np.unique(np.floor((column[:, 1] - deck) / 2))
        if len(column) < 12 or len(levels[levels >= 1]) < 5:
            continue
        tip = column[column[:, 1] > top - 4]
        if len(tip) < 2:
            continue
        xy = np.median(tip[:, [0, 2]], axis=0)
        point = shapely.Point(xy)
        if not shape.covers(point) or any(point.distance(q) < 7 for q, _ in masts):
            continue
        masts.append((point, float(top)))
    if masts:
        boat["masts"] = sorted(masts, key=lambda item: item[0].x)
    hull_profile(boat, p, local)
    deck = boat["deck"]
    parts = deck_parts(boat, p, local, direction, normal, centre)
    # Funnels and boats are their own parts, never cabin boxes.
    owned = [shapely.Point(f["centre"]).buffer(max(f["length"], f["width"]) / 2 + .6) for f in parts]
    taken = (shapely.intersects_xy(shapely.union_all(owned), p[:, 0], p[:, 2])
             if owned else np.zeros(len(p), bool))
    # Cabin occupancy must have dense roof evidence; sparse yards/wires don't become walls.
    high = (p[:, 1] > deck + 1.8) & (p[:, 1] < deck + 12) & ~taken
    xy = p[high][:, [0, 2]]
    if len(xy):
        origin = np.floor(xy.min(axis=0))
        cells = np.floor(xy - origin).astype(int)
        counts = np.zeros(tuple(cells.max(axis=0) + 1), dtype=int)
        np.add.at(counts, tuple(cells.T), 1)
        # Separate roof terraces by their local height. Combining all elevated returns
        # into one component turned passenger ships into one full-footprint cuboid.
        heights = np.zeros(counts.shape)
        for cell in np.argwhere(counts >= 5):
            inside = np.all(cells == cell, axis=1)
            heights[tuple(cell)] = np.percentile(p[high, 1][inside], 65)
        cabins = []
        bands = np.round(heights).astype(int)
        for band in np.unique(bands[counts >= 5]):
            regions, n = label((counts >= 5) & (bands == band))
            for i in range(1, n + 1):
                ids = np.argwhere(regions == i)
                if len(ids) < 5:
                    continue
                roof = shapely.union_all([shapely.box(*(q + origin), *(q + origin + 1)) for q in ids])
                roof = roof.intersection(shape.buffer(-.25)).simplify(.35, preserve_topology=True)
                top = float(np.median(heights[regions == i]))
                for part in shapely.get_parts(roof):
                    if part.geom_type == "Polygon" and part.area >= 5:
                        cabins.append((part, top))
        if cabins:
            boat["cabins"] = cabins
    boat["survey"] = dict(source="classified city lidar", points=len(p), top=float(p[:, 1].max()))


def hull_profile(boat, p, local):
    """Sheer line and bow from the lowest dense deck level per station.

    Superstructure tiers shorter than a third of the hull are opened away, so forecastles,
    main decks and low sterns keep their measured heights under the white upper works.
    """
    water = boat.get("water", .2)
    u = local[:, 0]
    start, stop = float(u.min()), float(u.max())
    length = stop - start
    if length < 12:
        return
    step = 3.0
    stations = np.arange(start, stop, step)
    floors = np.full(len(stations), np.nan)
    # Side decks run along the hull edges; deckhouses are usually set in from them.
    edge = shapely.distance(boat["outline"].exterior, shapely.points(p[:, [0, 2]])) < 1.5
    for i, s in enumerate(stations):
        h = p[(u >= s) & (u < s + step) & edge, 1]
        h = h[h > water + 1.5]
        hist, edges = np.histogram(h, bins=np.arange(water + 1.5, water + 30, .3))
        dense = np.flatnonzero(hist >= max(8, .04 * len(h)))
        if len(dense):
            floors[i] = np.median(h[(h >= edges[dense[0]]) & (h < edges[dense[0]] + .3)])
    if np.isfinite(floors).mean() < .5:
        return
    floors = np.interp(stations, stations[np.isfinite(floors)], floors[np.isfinite(floors)])
    window = max(3, int(round(length * .5 / step)) | 1)
    base = grey_opening(floors, size=window, mode="nearest")
    hull = floors - base < 1.6
    # Raised decks reaching an end are forecastles and poops of the hull itself;
    # raised plateaus amidships are white superstructure tiers.
    reach = int(.3 * len(floors))
    for order in (range(reach), range(len(floors) - 1, len(floors) - 1 - reach, -1)):
        for i in order:
            if hull[i] or floors[i] - base[i] > 4.5:
                break
            hull[i] = True
    top = np.interp(stations, stations[hull], floors[hull])
    top = np.convolve(np.pad(top, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    top = np.clip(top, water + .9, water + 14)
    top = np.round(top, 3)
    boat["deck"] = float(np.median(top))
    boat["sheer"] = [(float(s + step / 2), float(h)) for s, h in zip(stations, top, strict=True)]
    # The finer end is the bow; equal ends fall back to the higher (forecastle) end.
    ring = (np.asarray(boat["outline"].exterior.coords)[:, :2]
            - np.asarray(boat["outline"].centroid.coords[0])) @ np.array([axis(boat["outline"]),
                                                                        [-axis(boat["outline"])[1], axis(boat["outline"])[0]]]).T
    lo, hi = ring[:, 0].min(), ring[:, 0].max()
    def width(at):
        line = shapely.LineString([(at, -100), (at, 100)]).intersection(shapely.Polygon(ring))
        return line.length
    aft, fore = width(lo + .06 * (hi - lo)), width(hi - .06 * (hi - lo))
    if abs(aft - fore) > .1 * max(aft, fore):
        boat["bow"] = 1 if fore < aft else -1
    else:
        boat["bow"] = 1 if top[-1] >= top[0] else -1
    # A raked stem: the deck-level bow reaches further forward than the lower stem.
    tip = (lo, hi)[boat["bow"] > 0]
    fore = boat["bow"] * local[:, 0]
    near = (np.abs(local[:, 1]) < 1.5) & (fore > abs(tip) - 10)
    crest = top[(0, -1)[boat["bow"] > 0]]
    def reach(y0, y1):
        m = near & (p[:, 1] >= y0) & (p[:, 1] < y1)
        return (np.max(fore[m]), y0) if m.sum() >= 10 else None
    upper = reach(crest - 1, crest + .5)
    lower = next((r for y in np.arange(water + 1.5, crest - 2.5, .5) if (r := reach(y, y + .5))), None)
    rake = 0.
    if upper and lower:
        rake = float(np.clip(upper[0] - lower[0], 0, .9 * (crest - lower[1])))
    boat["rake"] = rake if rake > .5 else 0.
    boat["bow_tip"] = float(tip)


def deck_parts(boat, p, local, direction, normal, centre):
    """Funnels (tall solid ovals on deck) and lifeboats (small boats along the deck edges)."""
    res = .5
    origin = local.min(axis=0)
    cells = np.floor((local - origin) / res).astype(int)
    shape = tuple(cells.max(axis=0) + 1)
    if min(shape) < 6:
        return []
    order = np.lexsort((p[:, 1], cells[:, 1], cells[:, 0]))
    key = cells[order, 0] * shape[1] + cells[order, 1]
    heights = p[order, 1]
    starts = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    ends = np.r_[starts[1:], len(key)]
    top = np.full(shape, np.nan)
    solid = (ends - starts) >= 3
    top.flat[key[starts[solid]]] = heights[(starts + ((ends - starts - 1) * .8).astype(int))[solid]]
    hull = np.isfinite(top)
    filled = np.where(hull, top, boat["deck"])
    filled = np.where(hull, filled, median_filter(filled, 3))
    base = grey_opening(filled, size=(int(7 / res), int(7 / res)), mode="nearest")
    rise = np.where(hull, filled - base, 0)
    beam = float(np.ptp(local[:, 1]))
    masts = [np.asarray(q.coords[0]) for q, _ in boat["masts"]]
    sailing = len(masts) >= 3 or boat.get("tags", {}).get("building:material") == "wood"
    found = []
    claimed = np.zeros(shape, bool)
    for kind, lo, hi in (("funnel", 4., 40.), ("lifeboat", .8, 3.)):
        if kind == "funnel" and sailing:
            continue
        regions, count = label((rise > lo) & (rise < hi) & ~claimed)
        for i in range(1, count + 1):
            ids = np.argwhere(regions == i)
            uv = ids * res + origin + res / 2
            area = len(ids) * res * res
            if area < 2.5:
                continue
            sigma = uv.std(axis=0)
            length, width = 4 * sigma[0] + res, 4 * sigma[1] + res
            mid = uv.mean(axis=0)
            world = centre + direction * mid[0] + normal * mid[1]
            cell_top = top[regions == i]
            cell_base = base[regions == i]
            item = dict(kind=kind, centre=tuple(map(float, world)), along=float(mid[0]),
                        side=float(mid[1]), length=float(length), width=float(width),
                        base=float(np.median(cell_base)), top=float(np.percentile(cell_top, 90)))
            if kind == "funnel":
                fill = area / (np.pi / 4 * length * width)
                if not (2.5 <= length <= 12 and 2 <= width <= 8 and length / width <= 2.6
                        and fill >= .6 and abs(mid[1]) < beam / 4
                        and item["top"] - item["base"] >= 4
                        and all(np.linalg.norm(world - m) > 3 for m in masts)):
                    continue
                claimed |= median_filter(regions == i, 3) | (regions == i)
            else:
                length = float(np.clip(length, 4, 9))
                if not (3 <= np.ptp(uv[:, 0]) + res <= 11 and np.ptp(uv[:, 1]) + res <= 3.5
                        and abs(mid[1]) >= beam / 2 - 3.2):
                    continue
                item.update(length=length, width=float(np.clip(width, 1.8, 2.8)))
            found.append(item)
    funnels = [f for f in found if f["kind"] == "funnel"]
    # Lifeboats hang on one deck level, in rows along both sides.
    boats = [b for b in found if b["kind"] == "lifeboat"]
    # Davit boats are carried by sea-going ships, in rows on both sides.
    if np.ptp(local[:, 0]) < 45 or {np.sign(b["side"]) for b in boats} != {-1., 1.}:
        boats = []
    boat["funnels"], boat["lifeboats"] = funnels, boats
    return funnels + boats


def prepare(tile, osm, paths, water=None):
    from turku3d.spatial import rows

    records, seen = [], set()
    for key, row in rows(osm, tile.bbox.buffered(100)):
        tags = json.loads(row["tags"] or "{}")
        kind = tags.get("man_made")
        if kind not in ("crane", "mast", "tower") or tags.get("building:part"):
            continue
        if kind == "tower" and tags.get("tower:type") not in ("lighting", "communication"):
            continue  # Church/building towers already belong to their surveyed shells.
        identity = str(row.get("osm_id", key))
        if identity in seen:
            continue
        seen.add(identity)
        shape = shapely.force_2d(row["geometry_local"])
        centre = shape.centroid
        cloud = cloud_for(centre, paths, 38 if kind == "crane" else 8)
        try:
            height = float(tags.get("height", "25" if kind == "crane" else "15").split()[0])
        except ValueError:
            height = 25 if kind == "crane" else 15
        base, direction, reach = 3.0, np.array([1., 0.]), height * .65
        measured = False
        if cloud is not None:
            p = cloud.points
            cls = cloud.records["classification"]
            distance = np.linalg.norm(p[:, [0, 2]] - centre.coords[0], axis=1)
            dry = np.ones(len(p), dtype=bool) if water is None or water.is_empty else ~shapely.intersects_xy(water, p[:, 0], p[:, 2])
            ground = p[(distance < 10) & (cls == 2) & dry, 1]
            if len(ground) < 5:
                ground = p[(distance < 5) & (cls == 12) & dry, 1]
            if len(ground):
                base = float(np.percentile(ground, 10))
            p = p[np.isin(cls, (1, 6, 12, 17)) & (p[:, 1] > base + 8)]
            if len(p) >= 20:
                # Connected 2 m voxels separate the mapped crane from nearby buildings.
                origin = np.floor(p.min(axis=0) / 2) * 2
                cells = np.floor((p - origin) / 2).astype(int)
                occupied = np.zeros(tuple(cells.max(axis=0) + 1), dtype=bool)
                occupied[tuple(cells.T)] = True
                regions, _ = label(occupied, structure=np.ones((3, 3, 3)))
                ids = regions[tuple(cells.T)]
                near = np.linalg.norm(p[:, [0, 2]] - centre.coords[0], axis=1) < 5
                candidates = np.unique(ids[near])
                if len(candidates):
                    best = max(candidates, key=lambda i: p[ids == i, 1].max())
                    p = p[ids == best]
                    height = float(np.percentile(p[:, 1], 99.5) - base)
                    upper = p[p[:, 1] > base + height * .4]
                    delta = upper[:, [0, 2]] - centre.coords[0]
                    _, _, vt = np.linalg.svd(delta, full_matrices=False)
                    direction = vt[0]
                    along = delta @ direction
                    if abs(np.percentile(along, 2)) > abs(np.percentile(along, 98)):
                        direction = -direction
                        along = -along
                    reach = float(np.clip(np.percentile(along, 98), height * .25, height * 1.5))
                    tip_height = float(np.median(upper[along >= np.percentile(along, 95), 1]))
                    measured = True
        records.append(dict(id="osm:" + identity, kind=kind, outline=centre.buffer(.5),
                            centre=centre, base=base, height=float(np.clip(height, 5, 90)),
                            direction=direction, reach=reach, tags=tags, measured=measured,
                            tip_height=tip_height if measured else base+height,
                            building_keys=[], owner=C.VisualTile.at(centre.x, centre.y).id))
    return records


def produce(tile, record):
    from .boats import extrusion
    from .bridge_details import tube

    key = "harbour:" + record["id"]
    centre = np.asarray(record["centre"].coords[0])
    base, height = record["base"], record["height"]
    direction = record["direction"]
    normal = np.array([-direction[1], direction[0]])
    crane = record["kind"] == "crane"
    width = min(7, height * .2) if crane else max(.35, height * .025)
    top = base + height * (.62 if crane else 1)
    parts = []
    def point(xy, y):
        return [xy[0], y, xy[1]]
    def bar(a, b, radius=.09):
        parts.append(tube(key, a, b, radius, "blue" if crane else "white"))
    corners = [centre + direction * a * width / 2 + normal * b * width / 2
               for a, b in ((-1,-1), (1,-1), (1,1), (-1,1))]
    for q in corners:
        bar(point(q, base), point(centre + (q-centre)*.65, top), .15 if crane else .045)
    for y in np.arange(base, top, max(2, width)):
        hi = min(top, y + max(2, width))
        lower = [centre + (q-centre)*(1-.35*(y-base)/(top-base)) for q in corners]
        upper = [centre + (q-centre)*(1-.35*(hi-base)/(top-base)) for q in corners]
        for i in range(4):
            j = (i+1)%4
            bar(point(lower[i], y), point(lower[j], y), .055)
            bar(point(lower[i], y), point(upper[j], hi), .045)
    if crane:
        tipxy = centre + direction * record["reach"]
        tip_y = record["tip_height"]
        tip = point(tipxy, tip_y)
        for sign in (-1, 1):
            bar(point(centre + normal * sign * .65, top),
                point(tipxy + normal * sign * .25, tip_y), .12)
        for t in np.linspace(0, 1, max(4, int(record["reach"]/2))):
            xy = centre + direction * record["reach"] * t
            y = top + (tip_y-top)*t
            bar(point(xy-normal*.6, y), point(xy+normal*.6, y), .07)
            if t > 0:
                prev = centre + direction * record["reach"] * (t-.08)
                bar(point(prev-normal*.6, y-(tip_y-top)*.08), point(xy+normal*.6, y), .05)
        # The high A-frame and its suspension cable retain the measured skyline
        # even when the working boom lies below the crane's highest return.
        apex = point(centre-direction*width*.4, base+height)
        bar(point(centre-direction*width*.65, top), apex, .12)
        bar(apex, tip, .035)
        cabin = shapely.Point(centre).buffer(width*.27, quad_segs=1)
        parts.append(extrusion(key, cabin, top-2, top+.5, "blue"))
        bar(tip, point(tipxy, base+3), .025)
        parts.append(extrusion(key, shapely.Point(tipxy).buffer(.22, quad_segs=2), base+2.5, base+3, "hull"))
    for part in parts:
        part.metadata["collision"] = False
    return parts
