"""Mapped harbour structures and classified-lidar vessel measurements, city-wide."""

import json
from functools import lru_cache
import numpy as np
import shapely
from scipy.ndimage import label, maximum_filter1d

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
    # Cabin occupancy must have dense roof evidence; sparse yards/wires don't become walls.
    high = (p[:, 1] > deck + 1.8) & (p[:, 1] < deck + 12)
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
