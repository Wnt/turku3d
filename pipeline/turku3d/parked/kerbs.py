"""Kerbside parking bays along the city's carriageway edges, with evidence per street side.

All geometry is engine x/z metres (x = E - E0, z = -(N - N0)), so "left" and "right" of an OSM way
are mirrored relative to shapely's y-up convention: the real-world left normal of a direction
(dx, dz) is (dz, -dx).

A kerb is a stretch of the city carriageway outline (GIS:Katualueet "Ajorata") running along a car
street within ``KERB_REACH`` of its centreline. Where the city has no carriageway polygon, an OSM
street with explicit kerb parking gets a kerb at half its tagged width. Evidence for one side:

* OSM ``parking:<side>`` / ``parking:both`` (and the older ``parking:lane:*``): lane, street_side,
  on_kerb and half_on_kerb allow kerb bays with their ``:orientation``; no, separate (bays come from
  the separately mapped polygons), fire_lane and no_* forbid them.
* Traffic-sign posts (GIS:Liikennemerkit): E2 or H20 open a zone that runs from the post, in the
  direction of traffic on that side, to the next junction or sign post (at most ``ZONE_M``);
  E4.1 makes its bays perpendicular and E4.3 diagonal.
* Permit-parking kerb lines (GIS:Lupapysakointipaikat) allow parallel bays where they run.
* Any other side of a residential or unclassified street is inferred (low priority) when the
  carriageway leaves a driving width after parking.

Statutory gaps are masks: within 5 m of a crossing carriageway and of E1 pedestrian crossings,
12 m either side of E6 bus stops, driveway mouths, kerb build-outs, and no-stopping or no-parking
posts (C37, C38, C39, C41, C43, E8).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import shapely

# Sign codes (Varustelaji_koodi). H20 (paid parking) is a panel under E2 or on its own post.
ALLOW = frozenset({"E2", "H20"})
PERPENDICULAR, DIAGONAL = "E4.1", "E4.3"
FORBID = frozenset({"C37", "C38", "C39", "C41", "C43", "E8"})
CROSSING, BUS_STOP = "E1", "E6"
SIGN_CODES = ALLOW | FORBID | {PERPENDICULAR, DIAGONAL, "E4.2", CROSSING, BUS_STOP, "C40"}
POST_RADIUS = 1.0  # signs this close share one post
SIGN_REACH = 9.0  # a post stands on the kerb side within this of the kerb line
ZONE_M = 150.0

CAR_STREETS = frozenset(
    {
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
    }
)
INFERRED_STREETS = frozenset({"residential", "unclassified"})
KERB_STREETS = CAR_STREETS - {"service"}
YES = frozenset({"lane", "street_side", "on_kerb", "half_on_kerb", "shoulder", "yes"})
NO = frozenset({"no", "separate", "fire_lane", "no_parking", "no_stopping", "no_standing"})
ORIENTATIONS = {"parallel": "parallel", "perpendicular": "perpendicular", "diagonal": "diagonal"}

KERB_REACH = 14.0  # m from the centreline to a kerb of that street
KERB_GAP = 0.25  # wheels stand this far off the kerb face
MIN_LATERAL = 2.0  # a kerb closer to the centreline is a median or island
MAX_ANGLE = math.radians(25)
BUILD_OUT = 0.8  # the kerb steps this much towards the centreline at crossings and corners
JUNCTION_GAP = 5.0  # no parking within 5 m of a crossing carriageway's edge
CROSSING_RADIUS = 7.5  # an E1 post marks a 4 m crossing; plus 5 m before it on its kerb
BUS_STOP_RADIUS = 13.0  # 12 m either side of the stop sign
FORBID_RADIUS = 10.0
DRIVEWAY_HALF = 2.75  # a driveway mouth: half its width plus a metre
# Bay pitch along the kerb and depth from it, per orientation; diagonal bays lean 45 degrees.
PITCH = {"parallel": 6.0, "perpendicular": 2.6, "diagonal": 3.6}
NOMINAL = (2.0, 5.0)  # width, length of the largest regular body: a bay must hold it
DRIVE_TWO_WAY, DRIVE_ONE_WAY = 5.0, 3.0  # clear carriageway an inferred side must leave


@dataclass(frozen=True)
class Post:
    x: float
    z: float
    codes: frozenset

    @property
    def allows(self):
        return bool(self.codes & (ALLOW | {PERPENDICULAR, DIAGONAL}))

    @property
    def forbids(self):
        return bool(self.codes & FORBID)

    @property
    def orientation(self):
        if PERPENDICULAR in self.codes:
            return "perpendicular"
        return "diagonal" if DIAGONAL in self.codes else "parallel"


@dataclass(frozen=True)
class Bay:
    key: str
    x: float
    z: float
    yaw: float
    orientation: str
    street: str  # the street whose own road mask a kerb bay may enter
    priority: int  # 1 explicit evidence, 0 inferred
    source: str


def read_posts(path, bbox):
    """Parking-relevant sign posts: signs within POST_RADIUS of each other share a post."""
    if path is None:
        return []
    from turku3d.spatial import rows

    signs = []
    for _, row in rows(path, bbox, columns=["Varustelaji_koodi", "geometry_local"]):
        code, g = str(row.get("Varustelaji_koodi") or ""), row["geometry_local"]
        if code in SIGN_CODES and g is not None and g.geom_type == "Point":
            signs.append((round(g.x, 2), round(g.y, 2), code))
    posts: list[list] = []
    for x, z, code in sorted(signs):
        for post in posts:
            if math.hypot(post[0] - x, post[1] - z) <= POST_RADIUS:
                post[2].add(code)
                break
        else:
            posts.append([x, z, {code}])
    return [Post(x, z, frozenset(codes)) for x, z, codes in posts]


def read_points(path, bbox):
    if path is None:
        return []
    from turku3d.spatial import rows

    return [
        (round(g.x, 2), round(g.y, 2))
        for _, row in rows(path, bbox, columns=["geometry_local"])
        if (g := row["geometry_local"]) is not None and g.geom_type == "Point"
    ]


def read_lines(paths, bbox):
    from turku3d.spatial import rows

    out = []
    for path in paths:
        for _, row in rows(path, bbox, columns=["geometry_local"]):
            g = row["geometry_local"]
            if g is not None and not g.is_empty:
                for line in getattr(g, "geoms", [g]):
                    if line.geom_type == "LineString":
                        out.append(shapely.force_2d(line))
    # Overlapping areas fetch the same line twice.
    return sorted({line.wkb: line for line in out}.values(), key=lambda line: line.wkb)


def left_normal(dx, dz):
    """Real-world left of an engine direction (engine z points south)."""
    return dz, -dx


def side_value(tags, side):
    for key in (f"parking:{side}", "parking:both", f"parking:lane:{side}", "parking:lane:both"):
        if key in tags:
            return tags[key]
    return None


def side_restriction(tags, side):
    for key in (f"parking:{side}:restriction", "parking:both:restriction"):
        if key in tags:
            return tags[key]
    return None


def side_orientation(tags, side, value):
    for key in (
        f"parking:{side}:orientation",
        "parking:both:orientation",
        f"parking:lane:{side}",
        "parking:lane:both",
    ):
        if tags.get(key) in ORIENTATIONS:
            return tags[key]
    return ORIENTATIONS.get(value, "parallel")


def osm_evidence(tags, side):
    """('yes', orientation) | ('no', None) | (None, None) for one side of an OSM street."""
    value = side_value(tags, side)
    if side_restriction(tags, side) in ("no_parking", "no_stopping", "no_standing"):
        return "no", None
    if value in NO:
        return "no", None
    if value in YES or value in ORIENTATIONS:
        return "yes", side_orientation(tags, side, value)
    return None, None


class Street:
    """One OSM car way: its line, stations, kerbs and per-side evidence intervals."""

    def __init__(self, index, feature):
        self.index, self.id, self.tags = index, feature.id, feature.tags
        self.line = feature.geometry
        self.highway = self.tags.get("highway")
        self.name = self.tags.get("name") or self.id
        oneway = self.tags.get("oneway")
        self.oneway = 1 if oneway in ("yes", "1", "true") else -1 if oneway == "-1" else 0
        self.kerbs = {1: [], -1: []}  # side -> runs: (stations, laterals) arrays
        self.zones = {1: [], -1: []}  # side -> (t0, t1, orientation, source)
        self.forbid = {1: [], -1: []}  # side -> (t0, t1)

    def frame(self, t):
        """Point, unit direction and real-world left normal at station t."""
        length = self.line.length
        a = self.line.interpolate(max(0.0, t - 0.5))
        b = self.line.interpolate(min(length, t + 0.5))
        dx, dz = b.x - a.x, b.y - a.y
        n = math.hypot(dx, dz) or 1.0
        dx, dz = dx / n, dz / n
        p = self.line.interpolate(min(max(t, 0.0), length))
        return (p.x, p.y), (dx, dz), left_normal(dx, dz)

    def travel(self, side):
        """+1 when traffic next to this side runs along the way, -1 against it."""
        if self.oneway:
            return self.oneway
        return -1 if side == 1 else 1

    def half_width(self):
        laterals = [float(np.median(lats)) for side in (1, -1) for _, lats in self.kerbs[side]]
        if laterals:
            return float(np.median(laterals))
        try:
            return float(self.tags["width"]) / 2
        except (KeyError, ValueError):
            pass
        try:
            return float(self.tags.get("lanes", 2)) * 1.6
        except ValueError:
            return 3.2


def car_streets(features):
    streets = []
    for f in sorted(features, key=lambda f: f.id):
        t, g = f.tags, f.geometry
        if t.get("highway") not in CAR_STREETS or g.geom_type != "LineString" or g.length < 1:
            continue
        if t.get("area") == "yes" or t.get("bridge", "no") != "no" or t.get("layer", "0") not in ("0", ""):
            continue
        if t.get("tunnel", "no") not in ("no", "building_passage"):
            continue
        streets.append(Street(len(streets), f))
    return streets


def carriageway(city):
    polys = [
        f.geometry
        for f in city
        if f.tags.get("Katuosalaji_koodi") == "1" and f.geometry.geom_type in ("Polygon", "MultiPolygon")
    ]
    if not polys:
        return shapely.Polygon()
    # Close the slivers between neighbouring street parts.
    return shapely.union_all(polys).buffer(0.05, join_style="mitre").buffer(-0.05, join_style="mitre")


def not_kerb_areas(city, features):
    """Areas beyond an outline that make it a lane edge rather than a kerb: other traffic areas,
    cycle lanes and waiting areas, and separately mapped parking (which brings its own bays)."""
    parts = [
        f.geometry.buffer(0.1)
        for f in city
        if f.tags.get("Katuosalaji_koodi") in ("12", "15", "22")
        and f.geometry.geom_type in ("Polygon", "MultiPolygon")
    ]
    parts += [
        f.geometry
        for f in features
        if f.tags.get("amenity") == "parking" and f.geometry.geom_type in ("Polygon", "MultiPolygon")
    ]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def find_kerbs(streets, carriage, beyond, window):
    """Assign each stretch of the carriageway outline near ``window`` to its nearest street side."""
    if carriage.is_empty or not streets:
        return
    outline = shapely.segmentize(carriage.boundary.intersection(window), 2.0)
    coords = []
    for line in getattr(outline, "geoms", [outline]):
        c = np.asarray(line.coords)[:, :2]
        if len(c) > 1:
            coords.append(np.stack([c[:-1], c[1:]], axis=1))
    if not coords:
        return
    segs = np.concatenate(coords)
    mids = shapely.points((segs[:, 0] + segs[:, 1]) / 2)
    lines = [s.line for s in streets]
    tree = shapely.STRtree(lines)
    (seg_i, street_i), _ = tree.query_nearest(mids, max_distance=KERB_REACH, return_distance=True)
    # Ties (equidistant streets) keep the first, so a segment belongs to one street.
    _, first = np.unique(seg_i, return_index=True)
    seg_i, street_i = seg_i[first], street_i[first]
    if not len(seg_i):
        return
    if not beyond.is_empty:
        shapely.prepare(beyond)
    groups = defaultdict(list)
    for s, k in zip(seg_i.tolist(), street_i.tolist(), strict=True):
        street = streets[k]
        (a, b) = segs[s]
        ta = street.line.project(shapely.Point(a))
        tb = street.line.project(shapely.Point(b))
        if abs(tb - ta) < 0.2:
            continue  # across the street: a junction mouth or an end
        tm = (ta + tb) / 2
        (px, pz), (dx, dz), (nx, nz) = street.frame(tm)
        mx, mz = (a + b) / 2
        lateral = (mx - px) * nx + (mz - pz) * nz
        side = 1 if lateral > 0 else -1
        sx, sz = b - a
        seg_len = math.hypot(sx, sz)
        if abs(lateral) < MIN_LATERAL or seg_len < 1e-6:
            continue
        if abs(sx * dz - sz * dx) / seg_len > math.sin(MAX_ANGLE):
            continue
        # The far side of a kerb is pavement or verge, not more traffic area.
        if not beyond.is_empty and beyond.contains(
            shapely.Point(mx + side * nx * 0.6, mz + side * nz * 0.6)
        ):
            continue
        la = abs((a[0] - px) * nx + (a[1] - pz) * nz)
        lb = abs((b[0] - px) * nx + (b[1] - pz) * nz)
        groups[(k, side)].append((min(ta, tb), max(ta, tb), la if ta < tb else lb, lb if ta < tb else la))
    for (k, side), segments in groups.items():
        segments.sort()
        run_t, run_l = [], []
        for t0, t1, l0, l1 in segments:
            if run_t and t0 - run_t[-1] > 1.0:
                streets[k].kerbs[side].append((np.asarray(run_t), np.asarray(run_l)))
                run_t, run_l = [], []
            if run_t and t0 < run_t[-1]:
                t0 = run_t[-1]  # overlapping outline pieces keep station order
            if t1 <= t0:
                continue
            run_t += [t0, t1]
            run_l += [l0, l1]
        if len(run_t) >= 2:
            streets[k].kerbs[side].append((np.asarray(run_t), np.asarray(run_l)))


def fallback_kerbs(streets, carriage):
    """A street with explicit kerb parking and no city carriageway gets kerbs at half its width."""
    for street in streets:
        if street.kerbs[1] or street.kerbs[-1] or street.highway not in KERB_STREETS:
            continue
        if not carriage.is_empty and carriage.intersection(street.line.buffer(3)).area > 1.5 * street.line.length:
            continue  # the city outline is there; it simply has no kerb near this street
        for side, name in ((1, "left"), (-1, "right")):
            if osm_evidence(street.tags, name)[0] != "yes":
                continue
            try:
                half = float(street.tags["width"]) / 2
            except (KeyError, ValueError):
                try:
                    half = float(street.tags.get("lanes", 2)) * 1.6 + 2.2
                except ValueError:
                    half = 5.4
            ts = np.array([0.0, street.line.length])
            street.kerbs[side].append((ts, np.full(2, half)))


def junctions(streets):
    """Junction nodes (three or more street ends meet) with the street indices that meet there,
    and driveway mouths (a service way joins a through street)."""
    ends = defaultdict(list)
    for s in streets:
        coords = list(s.line.coords)
        for i, c in enumerate(coords):
            ends[node_key(c)].append((s.index, 1 if i in (0, len(coords) - 1) else 2))
    nodes, mouths = [], []
    for key, members in ends.items():
        degree = sum(d for _, d in members)
        if degree < 3:
            continue
        main = [i for i, _ in members if streets[i].highway != "service"]
        service = [i for i, _ in members if streets[i].highway == "service"]
        main_degree = sum(d for i, d in members if streets[i].highway != "service")
        if main_degree >= 3:
            nodes.append((key, sorted(set(main + service))))
        else:
            mouths.extend((key, i) for i in sorted(set(service)))
    return nodes, mouths


def kerb_masks(streets, posts, stops, features, city):
    """Statutory no-parking geometry for kerb bays."""
    nodes, mouths = junctions(streets)
    masks = []
    for (x, z), members in nodes:
        radius = max(streets[i].half_width() for i in members) + JUNCTION_GAP + 1.0
        masks.append(shapely.Point(x, z).buffer(radius))
    for (x, z), i in mouths:
        near = streets[i].line.intersection(shapely.Point(x, z).buffer(15))
        if not near.is_empty:
            masks.append(near.buffer(DRIVEWAY_HALF))
    for post in posts:
        if CROSSING in post.codes:
            masks.append(shapely.Point(post.x, post.z).buffer(CROSSING_RADIUS))
        if BUS_STOP in post.codes:
            masks.append(shapely.Point(post.x, post.z).buffer(BUS_STOP_RADIUS))
        if post.forbids:
            masks.append(shapely.Point(post.x, post.z).buffer(FORBID_RADIUS))
    masks += [shapely.Point(x, z).buffer(BUS_STOP_RADIUS) for x, z in stops]
    for f in features:
        t, g = f.tags, f.geometry
        if t.get("highway") == "bus_stop" or t.get("public_transport") == "platform":
            masks.append(g.buffer(BUS_STOP_RADIUS if g.geom_type == "Point" else 6.0))
        elif t.get("highway") == "crossing" or t.get("footway") == "crossing" or t.get("crossing"):
            # Mapped on the centreline (nodes) or across the street (ways).
            masks.append(g.buffer(CROSSING_RADIUS if g.geom_type == "Point" else 6.5))
    masks += [
        f.geometry.buffer(1.0)
        for f in city
        if f.tags.get("Katuosalaji_koodi") == "7" and f.geometry.geom_type in ("Polygon", "MultiPolygon")
    ]
    return shapely.union_all(masks) if masks else shapely.Polygon()


def locate(streets, tree, x, z, reach):
    """(street, station, side, lateral) of the nearest street side within reach, or None."""
    p = shapely.Point(x, z)
    found = tree.query_nearest(p, max_distance=reach, all_matches=False)
    if not len(found):
        return None
    street = streets[int(found[0])]
    t = street.line.project(p)
    (px, pz), _, (nx, nz) = street.frame(t)
    lateral = (x - px) * nx + (z - pz) * nz
    return street, t, 1 if lateral > 0 else -1, abs(lateral)


def kerb_lateral(street, side, t):
    for ts, lats in street.kerbs[side]:
        if ts[0] - 1 <= t <= ts[-1] + 1:
            return float(np.interp(t, ts, lats))
    return None


def node_key(c):
    return round(c[0], 2), round(c[1], 2)


def walk(ends, junction_keys, street, side, t, direction, stops_at, budget):
    """Intervals (street, side, t0, t1) from station t in the direction of travel, through plain
    way splits of the same street, up to a junction, another post on that side, or the budget."""
    out, seen = [], set()
    while budget > 0 and street.index not in seen:
        seen.add(street.index)
        ahead = [u for u in stops_at.get((street.index, side), ()) if (u - t) * direction > 0.5]
        ahead += [u for u in street.junction_stations if (u - t) * direction > 0.5]
        limit = street.line.length if direction > 0 else 0.0
        end = min([limit, *ahead], key=lambda u: abs(u - t))
        reach = min(abs(end - t), budget)
        out.append((street, side, min(t, t + direction * reach), max(t, t + direction * reach)))
        budget -= reach
        if end != limit or reach < abs(end - t):
            break
        key = node_key(street.line.coords[-1 if direction > 0 else 0])
        joined = ends.get(key, ())
        if key in junction_keys or len(joined) != 2:
            break
        nxt = next(s for s in joined if s.index != street.index)
        if nxt.name != street.name:
            break
        # Sides are relative to each way's own direction; the physical side keeps its place.
        enters_at_start = node_key(nxt.line.coords[0]) == key
        new_direction = 1 if enters_at_start else -1
        side = side * direction * new_direction
        direction, street = new_direction, nxt
        t = 0.0 if enters_at_start else street.line.length
    return out


def evidence(streets, posts, permits):
    """Sign zones, forbidding posts and permit lines as station intervals per street side."""
    if not streets:
        return
    tree = shapely.STRtree([s.line for s in streets])
    ends = defaultdict(list)
    for s in streets:
        for c in (s.line.coords[0], s.line.coords[-1]):
            ends[node_key(c)].append(s)
    nodes, _ = junctions(streets)
    junction_keys = {key for key, _ in nodes}
    for s in streets:
        s.junction_stations = [
            s.line.project(shapely.Point(c)) for c in s.line.coords if node_key(c) in junction_keys
        ]
    placed = []
    for post in posts:
        if not (post.allows or post.forbids):
            continue
        hit = locate(streets, tree, post.x, post.z, KERB_REACH + SIGN_REACH)
        if hit is None:
            continue
        street, t, side, lateral = hit
        kerb = kerb_lateral(street, side, t)
        if kerb is not None and abs(lateral - kerb) > SIGN_REACH:
            continue
        placed.append((post, street, t, side))
    stops_at = defaultdict(list)
    for _post, street, t, side in placed:
        stops_at[(street.index, side)].append(t)
    for post, street, t, side in placed:
        if post.forbids:
            street.forbid[side].append((t - FORBID_RADIUS, t + FORBID_RADIUS))
            continue
        if street.highway not in KERB_STREETS:
            continue
        for s, sd, t0, t1 in walk(
            ends, junction_keys, street, side, t, street.travel(side), stops_at, ZONE_M
        ):
            s.zones[sd].append((t0, t1, post.orientation, f"sign:{post.x:.1f},{post.z:.1f}"))
    for i, line in enumerate(permits):
        for frac in np.linspace(0.0, 1.0, max(2, int(line.length // 2) + 1)):
            p = line.interpolate(frac, normalized=True)
            hit = locate(streets, tree, p.x, p.y, KERB_REACH)
            if hit is None:
                continue
            street, t, side, _ = hit
            if street.highway in KERB_STREETS:
                street.zones[side].append((t - 1.5, t + 1.5, "parallel", f"permit:{i}"))


def side_rule(street, side, t):
    """(orientation, priority, source) of kerb parking at station t, or None."""
    name = "left" if side == 1 else "right"
    verdict, orientation = osm_evidence(street.tags, name)
    if verdict == "no":
        return None
    if any(t0 <= t <= t1 for t0, t1 in street.forbid[side]):
        return None
    if verdict == "yes":
        return orientation, 1, "osm"
    for t0, t1, zone_orientation, source in street.zones[side]:
        if t0 <= t <= t1:
            return zone_orientation, 1, source.split(":")[0]
    if street.highway in INFERRED_STREETS and not any(
        k.startswith("parking") for k in street.tags
    ):
        return "parallel", 0, "inferred"
    return None


def clear_width_ok(street, side, t, depth, rule_other):
    """An inferred side keeps a driving width beside it and the other side's parked cars."""
    own = kerb_lateral(street, side, t)
    other = kerb_lateral(street, -side, t)
    if own is None or other is None:
        return False
    other_depth = 0.0
    if rule_other is not None:
        other_depth = NOMINAL[0] + KERB_GAP if rule_other[0] == "parallel" else NOMINAL[1]
    need = DRIVE_ONE_WAY if street.oneway else DRIVE_TWO_WAY
    return own + other - depth - KERB_GAP - other_depth >= need


def bay_pose(street, side, t, orientation, lateral, width, length):
    """Centre and yaw of a bay at station t with its outer face KERB_GAP inside the kerb."""
    (px, pz), (dx, dz), (nx, nz) = street.frame(t)
    # Follow the kerb line itself, which may run at an angle to the centreline.
    la = kerb_lateral(street, side, t - 2.0)
    lb = kerb_lateral(street, side, t + 2.0)
    if la is not None and lb is not None:
        (ax, az), _, (anx, anz) = street.frame(t - 2.0)
        (bx, bz), _, (bnx, bnz) = street.frame(t + 2.0)
        kx = bx + side * bnx * lb - (ax + side * anx * la)
        kz = bz + side * bnz * lb - (az + side * anz * la)
        n = math.hypot(kx, kz)
        if n > 1e-6:
            dx, dz = kx / n, kz / n
            nx, nz = left_normal(dx, dz)
    travel = street.travel(side)
    fx, fz = dx * travel, dz * travel  # along the traffic beside this kerb
    ox, oz = nx * side, nz * side  # towards the kerb
    if orientation == "parallel":
        depth, (hx, hz) = width, (fx, fz)
    elif orientation == "perpendicular":
        depth, (hx, hz) = length, (ox, oz)  # nose in
    else:
        s = math.sqrt(0.5)
        depth = (width + length) * s
        hx, hz = (fx + ox) * s, (fz + oz) * s
    inset = lateral - KERB_GAP - depth / 2
    return px + side * nx * inset, pz + side * nz * inset, math.atan2(hx, hz), depth


def bays(streets, carriage, blocked):
    """Kerb bays of every street side, sliding past ``blocked`` in half-metre steps."""
    from .placement import footprint

    nominal_w, nominal_l = NOMINAL
    out = []
    for street in streets:
        if street.highway not in KERB_STREETS:
            continue
        for side in (1, -1):
            name = "left" if side == 1 else "right"
            for ts, lats in street.kerbs[side]:
                typical = float(np.median(lats))
                t, end = float(ts[0]), float(ts[-1])
                while t < end:
                    rule = side_rule(street, side, t)
                    if rule is None:
                        t += 0.5
                        continue
                    orientation, priority, source = rule
                    pitch = PITCH[orientation]
                    centre = t + pitch / 2
                    if centre + pitch / 2 > end:
                        break
                    lateral = float(np.interp(centre, ts, lats))
                    x, z, yaw, depth = bay_pose(
                        street, side, centre, orientation, lateral, nominal_w, nominal_l
                    )
                    if orientation == "parallel":
                        probe = footprint(x, z, yaw, nominal_w, pitch - 0.6)
                    else:
                        probe = footprint(x, z, yaw, pitch - 0.3, nominal_l)
                    if (
                        typical - lateral > BUILD_OUT
                        or (
                            priority == 0
                            and not clear_width_ok(
                                street, side, centre, depth, side_rule(street, -side, centre)
                            )
                        )
                        or blocked.intersects(probe)
                        or not carriage.covers(probe)
                    ):
                        t += 0.5
                        continue
                    # The station along the whole way names the bay in every tile that sees it.
                    out.append(
                        Bay(
                            f"{street.id}:{name}:{round(centre * 2)}",
                            x,
                            z,
                            yaw,
                            orientation,
                            street.name,
                            priority,
                            source,
                        )
                    )
                    t += pitch
    return out


def plan(features, city, posts, stops, permits, obstacles, window):
    """Kerb bays of the street sides near ``window`` and the statutory gaps between them.

    ``obstacles`` (prepared) are every static exclusion except street corridors; bays slide past them
    and past the statutory gaps, which are returned for the final per-car check."""
    streets = car_streets(features)
    carriage = carriageway(city)
    find_kerbs(streets, carriage, not_kerb_areas(city, features), window)
    fallback_kerbs(streets, carriage)
    evidence(streets, posts, permits)
    gaps = kerb_masks(streets, posts, stops, features, city)
    if carriage.is_empty:
        carriage = shapely.union_all([s.line.buffer(s.half_width() + 0.5) for s in streets])
    cover = carriage.buffer(0.3)
    shapely.prepare(cover)
    blocked = shapely.union_all([gaps, obstacles])
    shapely.prepare(blocked)
    return bays(streets, cover, blocked), gaps
