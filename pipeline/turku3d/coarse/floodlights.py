"""Night floodlights of lit sports venues, from OSM venues and lighting masts (no per-venue data).

A class rule for every mapped venue:

* **Playing surfaces** are OSM ``leisure=pitch|track|ice_rink`` areas (an open or ``area=no``
  ``track`` line, such as a BMX course, is its ``TRACK_BUFFER`` corridor) and ``leisure=stadium``
  areas holding no surface. Indoor, layered and building-tagged features are skipped, and so are
  areas under ``MIN_AREA`` or over ``MAX_AREA`` and areas a mapped building covers (``INDOOR``:
  a hall mapped as a stadium).
* **Lit** when ``lit`` says so (``LIT``); never when ``lit=no``. A track line is floodlit only by
  mapped masts (a lit line is a lamp-lit path, such as a skating loop). An untagged surface is lit when it
  lies in a ``leisure=stadium`` area (``STADIUM_COVER``), when a mapped lighting mast stands within
  ``MAST_REACH`` of it, or when it is a full-size artificial-turf football pitch (``TURF_AREA``),
  which Finnish clubs light for winter evenings.
* **Venues** are lit surfaces grouped where one holds another (the pitch inside a running track)
  or where small courts touch (a block of tennis courts, ``COURT_AREA``). The lit field is the
  group's minimum rotated rectangle.
* **Masts** are the mapped ``man_made=mast|tower`` with ``tower:type=lighting``, each joined to the
  nearest venue within ``MAST_REACH``. The harbour stage already draws them as lattice masts, so a
  mapped mast keeps that mesh and its lamp heads sit on its drawn top (the stage's lidar-measured
  height). A lit venue without mapped masts gets the usual layout: corner masts round a stadium,
  masts along both long sides of a field, two per side for a court (``LAYOUT``). These carry
  ``mesh: 0`` and the client draws their poles.
* **Aim.** Every mast's heads face the venue centre.

Each tile lists the venues whose field centre it owns and the masts standing in it, so neighbour
tiles agree whatever the load order. Heights come from the tile's own ground (collision) through
``ground_at``; a mast or venue whose ground is unknown is left out.
"""

from __future__ import annotations

import json
import math

import numpy as np
import shapely

from turku3d import config as C

FORMAT_VERSION = 1
SURFACES = ("pitch", "track", "ice_rink")
LIT = {"yes", "24/7", "automatic", "limited", "interval", "sunset-sunrise", "dusk-dawn"}
MIN_AREA = 150.0  # m2: smaller pitches are table-tennis spots and play corners
MAX_AREA = 40000.0  # m2: larger areas are parks and sports grounds, not one field
TRACK_BUFFER = 3.0  # m half width of an open track line
STADIUM_COVER = 0.5  # share of a surface inside a stadium area that makes it the stadium's field
MAST_REACH = 30.0  # m from a venue's surfaces to a mapped lighting mast that lights it
TURF_AREA = 4000.0  # m2: an artificial-turf football pitch this large is a lit club pitch
COURT_AREA = 1500.0  # m2: courts this small join neighbours they touch into one lit block
SEARCH = 300.0  # m around the tile: a venue reaching the tile is read whole
INDOOR = 0.5  # share of a surface under mapped buildings that makes it an indoor court
MAST_DEFAULT = 15.0  # m, as the harbour stage draws an unmeasured mast
# Collision materials of the open ground (col/materials_v1.json): road to rock, quay and ramps.
# Fences, walls, buildings, roofs and furniture are not where a mast or a field stands.
GROUND = frozenset((*range(0, 14), 15, 18))
# (min field length m, masts per long side or "corners", offset beyond the field m, height m)
LAYOUT = {
    "court": (2, 1.5, 10.0),
    "field": (2, 4.0, 16.0),
    "large": (3, 4.0, 20.0),
    "track": (3, 3.0, 24.0),
    "stadium": ("corners", 8.0, 30.0),
}
LARGE = 90.0  # m field length from which a field has three masts a side
LAMP_SPACING = 4.0  # m of mast height per lamp head
LAMPS = (2, 10)
LEVEL = {"stadium": 1.0, "track": 0.85, "large": 0.85, "field": 0.75, "court": 0.6}


def _surfaces(osm, bbox):
    """(polygon, tags, id, leisure) of every surface and stadium area near the box."""
    from turku3d.spatial import rows

    out, seen, buildings = [], set(), []
    for key, row in rows(osm, bbox):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        leisure = tags.get("leisure")
        if tags.get("building") and tags.get("building") not in ("roof", "grandstand") and leisure is None:
            buildings.extend(_polygons(shapely.force_2d(row["geometry_local"])))
        if leisure not in (*SURFACES, "stadium") or tags.get("building"):
            continue
        if tags.get("indoor") == "yes" or tags.get("layer", "0") not in ("0", ""):
            continue
        identity = f"osm:{row.get('osm_type', 'way')}:{row.get('osm_id', key)}"
        if identity in seen:
            continue
        seen.add(identity)
        geometry = shapely.force_2d(row["geometry_local"])
        line = leisure == "track" and geometry.geom_type in ("LineString", "MultiLineString") \
            and (tags.get("area") == "no" or not geometry.is_closed)
        polygons = _polygons(geometry.buffer(TRACK_BUFFER, quad_segs=2) if line else geometry)
        polygon = shapely.union_all(polygons) if polygons else None
        if polygon is None or polygon.is_empty:
            continue
        out.append((polygon, tags, identity, "line" if line else leisure))
    if buildings:
        tree = shapely.STRtree(buildings)
        out = [s for s in out if sum(buildings[i].intersection(s[0]).area for i in tree.query(s[0]))
               < INDOOR * s[0].area]
    return sorted(out, key=lambda s: s[2])


def _masts(osm, bbox):
    """(point, tags, id) of mapped lighting masts near the box (harbour ids: ``osm:<id>``)."""
    from turku3d.spatial import rows

    out, seen = [], set()
    for key, row in rows(osm, bbox):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if tags.get("man_made") not in ("mast", "tower") or tags.get("tower:type") != "lighting":
            continue
        if tags.get("building:part"):
            continue
        identity = "osm:" + str(row.get("osm_id", key))
        if identity in seen:
            continue
        seen.add(identity)
        out.append((shapely.force_2d(row["geometry_local"]).centroid, tags, identity))
    return sorted(out, key=lambda m: m[2])


def _lit(polygon, tags, stadiums, masts, line=False):
    value = str(tags.get("lit", "")).strip().lower()
    if value == "no":
        return False
    if line:
        return any(polygon.distance(m) <= MAST_REACH for m, _, _ in masts)
    if value in LIT:
        return True
    if any(s.intersection(polygon).area >= STADIUM_COVER * polygon.area for s in stadiums):
        return True
    if any(polygon.distance(m) <= MAST_REACH for m, _, _ in masts):
        return True
    sport = tags.get("sport", "")
    return ("soccer" in sport or "football" in sport) and tags.get("surface") == "artificial_turf" \
        and polygon.area >= TURF_AREA


def _groups(fields):
    """Union-find over lit fields: one holds the other, or small courts touch."""
    parent = list(range(len(fields)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    hulls = [f["polygon"].convex_hull for f in fields]
    for i in range(len(fields)):
        for j in range(i + 1, len(fields)):
            a, b = fields[i]["polygon"], fields[j]["polygon"]
            small = min(a.area, b.area)
            held = hulls[i].intersection(hulls[j]).area >= 0.6 * min(hulls[i].area, hulls[j].area)
            courts = max(a.area, b.area) <= COURT_AREA and a.distance(b) <= 2.0
            if held or (courts and small > 0):
                parent[root(i)] = root(j)
    groups = {}
    for i in range(len(fields)):
        groups.setdefault(root(i), []).append(fields[i])
    return [sorted(g, key=lambda f: f["id"]) for _, g in sorted(groups.items(), key=lambda kv: min(f["id"] for f in kv[1]))]


def _polygons(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "LineString" and geometry.is_ring:
        geometry = shapely.Polygon(geometry)
    geometry = shapely.make_valid(geometry)
    return [g for g in getattr(geometry, "geoms", [geometry]) if g.geom_type == "Polygon" and g.area > 1.0]


def _frame(polygon):
    """(centre, unit long axis, length, width) of the polygon's minimum rotated rectangle."""
    with np.errstate(divide="ignore", invalid="ignore"):
        rect = shapely.minimum_rotated_rectangle(polygon)
    ring = np.asarray(rect.exterior.coords)[:4]
    edges = np.roll(ring, -1, axis=0) - ring
    lengths = np.linalg.norm(edges, axis=1)
    k = int(np.argmax(lengths))
    u = edges[k] / max(lengths[k], 1e-9)
    if u[0] < 0 or (u[0] == 0 and u[1] < 0):
        u = -u  # canonical direction: the record does not depend on the rectangle's start corner
    return ring.mean(axis=0), u, float(lengths[k]), float(lengths[(k + 1) % 4])


def venues(osm, bbox):
    """Lit venues reaching ``bbox`` (BBoxLocal), each with its masts (mapped or by the layout)."""
    if osm is None:
        return []
    area = bbox.buffered(SEARCH)
    surfaces = _surfaces(osm, area)
    masts = _masts(osm, area)
    stadiums = [s[0] for s in surfaces if s[3] == "stadium"]
    fields = []
    for polygon, tags, identity, leisure in surfaces:
        if leisure == "stadium":
            # A stadium area is a field itself only when it holds no mapped surface.
            if any(s[3] != "stadium" and s[0].intersection(polygon).area >= STADIUM_COVER * s[0].area
                   for s in surfaces):
                continue
        if not MIN_AREA <= polygon.area <= MAX_AREA:
            continue
        if _lit(polygon, tags, stadiums, masts, leisure == "line") or leisure == "stadium":
            fields.append(dict(polygon=polygon, tags=tags, id=identity,
                               leisure="track" if leisure == "line" else leisure))
    out = []
    for group in _groups(fields):
        union = shapely.union_all([f["polygon"] for f in group])
        centre, u, length, width = _frame(union)
        in_stadium = any(s.intersection(union).area >= STADIUM_COVER * union.area for s in stadiums)
        leisures = {f["leisure"] for f in group}
        if in_stadium or "stadium" in leisures:
            kind = "stadium"
        elif "track" in leisures and length >= LARGE:
            kind = "track"
        elif length < 45:
            kind = "court"
        else:
            kind = "large" if length >= LARGE else "field"
        sports = sorted({s.strip() for f in group for s in f["tags"].get("sport", "").split(";") if s.strip()})
        out.append(dict(id=group[0]["id"], members=[f["id"] for f in group], kind=kind, sport=sports,
                        union=union, centre=centre, u=u, length=length, width=width, masts=[]))
    # Mapped masts join the nearest venue within reach.
    for point, tags, identity in masts:
        near = [(v["union"].distance(point), v["id"], v) for v in out]
        near = [n for n in near if n[0] <= MAST_REACH]
        if not near:
            continue
        _, _, venue = min(near, key=lambda n: (n[0], n[1]))
        venue["masts"].append(dict(id=identity, x=point.x, z=point.y, height=_tag_height(tags), mesh=1))
    for venue in out:
        if not venue["masts"]:
            venue["masts"] = _layout(venue)
        venue["masts"].sort(key=lambda m: m["id"])
    return out


def _tag_height(tags):
    try:
        return float(np.clip(float(str(tags.get("height", "")).split()[0]), 5, 90))
    except (ValueError, IndexError):
        return None


def _layout(venue):
    """The usual mast layout of a lit venue without mapped masts."""
    count, offset, height = LAYOUT[venue["kind"]]
    c, u = venue["centre"], venue["u"]
    v = np.array([-u[1], u[0]])
    half_l, half_w = venue["length"] / 2, venue["width"] / 2
    points = []
    if count == "corners":
        for a in (-1, 1):
            for b in (-1, 1):
                corner = u * half_l * a + v * half_w * b
                points.append(c + corner + corner / np.linalg.norm(corner) * offset)
    else:
        stations = np.linspace(-0.4, 0.4, count) * venue["length"] if count > 1 else [0.0]
        for b in (-1, 1):
            for s in stations:
                points.append(c + u * s + v * (half_w + offset) * b)
    return [dict(id=f"{venue['id']}:mast:{i}", x=float(p[0]), z=float(p[1]), height=height, mesh=0)
            for i, p in enumerate(points)]


def lamps(height):
    return int(np.clip(round(height / LAMP_SPACING), *LAMPS))


def _r(value):
    return round(float(value), 2)


def tile_floodlights(tile, osm, ground_at, harbour=None):
    """The tile's ``lights.floodlights`` record: the venues whose field centre it owns and the masts
    standing in it. ``ground_at(x, z)`` is the tile's ground height (None when unknown); ``harbour``
    maps a mapped mast id to the harbour stage's drawn height."""
    harbour = harbour or {}
    bbox = tile.bbox
    owns = lambda x, z: bbox.xmin <= x < bbox.xmax and bbox.zmin <= z < bbox.zmax  # noqa: E731
    out_venues, out_masts = [], []
    for venue in venues(osm, bbox):
        c = venue["centre"]
        if owns(*c):
            samples = [ground_at(*(c + venue["u"] * s * venue["length"])) for s in (-0.25, 0.0, 0.25)]
            samples = [y for y in samples if y is not None]
            if samples:
                out_venues.append(dict(
                    id=venue["id"], kind=venue["kind"], sport=venue["sport"], members=venue["members"],
                    x=_r(c[0]), y=_r(np.median(samples)), z=_r(c[1]), ux=round(float(venue["u"][0]), 4),
                    uz=round(float(venue["u"][1]), 4), length=_r(venue["length"]), width=_r(venue["width"]),
                    level=LEVEL[venue["kind"]]))
        for mast in venue["masts"]:
            if not owns(mast["x"], mast["z"]):
                continue
            base = ground_at(mast["x"], mast["z"])
            if base is None:
                continue
            height = harbour.get(mast["id"]) if mast["mesh"] else None
            height = height or mast["height"] or MAST_DEFAULT
            out_masts.append(dict(
                id=mast["id"], venue=venue["id"], x=_r(mast["x"]), y=_r(base), z=_r(mast["z"]),
                top=_r(base + height), aim=[_r(c[0]), _r(c[1])], lamps=lamps(height), mesh=mast["mesh"]))
    if not out_venues and not out_masts:
        return None
    return dict(version=FORMAT_VERSION, venues=sorted(out_venues, key=lambda v: v["id"]),
                masts=sorted(out_masts, key=lambda m: m["id"]))


def collision_ground(cells, read):
    """``ground_at(x, z)``: the highest open-ground collision triangle over the point (``GROUND``).

    ``cells`` is the manifest ``collision`` field, ``read(url)`` the payload bytes. Unreadable cells
    give None."""
    from turku3d.col.format import read_col

    loaded = {}

    def triangles(cid):
        if cid not in loaded:
            entry = cells.get(cid)
            try:
                col = read_col(read(entry["url"])) if entry else None
            except Exception:  # a malformed or fixture cell has no ground  # noqa: BLE001
                col = None
            if col is None or not len(col.indices):
                loaded[cid] = None
            else:
                origin = np.asarray(col.origin if col.origin is not None
                                    else (col.tile_i * C.COLLISION_TILE_SIZE, 0.0, col.tile_j * C.COLLISION_TILE_SIZE))
                keep = np.isin(col.materials, list(GROUND))
                loaded[cid] = (col.positions.astype(np.float64) + origin)[col.indices[keep].astype(np.int64)]
        return loaded[cid]

    def ground_at(x, z):
        tri = triangles(f"c_{math.floor(x / C.COLLISION_TILE_SIZE)}_{math.floor(z / C.COLLISION_TILE_SIZE)}")
        if tri is None:
            return None
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
        ok = np.abs(d) > 1e-9
        d = np.where(ok, d, 1.0)
        w0 = ((b[:, 2] - c[:, 2]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (z - c[:, 2])) / d
        w1 = ((c[:, 2] - a[:, 2]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (z - c[:, 2])) / d
        w2 = 1 - w0 - w1
        hit = ok & (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not hit.any():
            return None
        return float((w0 * a[:, 1] + w1 * b[:, 1] + w2 * c[:, 1])[hit].max())

    return ground_at
