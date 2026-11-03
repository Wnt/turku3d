"""Sports venues of a coarse tile: grandstands, running tracks, pitches and terraces.

A class rule for every mapped venue, from OSM and the building register (no per-venue data):

* **Grandstands.** A register/LoD2 building is a grandstand when an OSM ``building=grandstand``,
  ``man_made=grandstand`` or ``leisure=bleachers`` area covers at least ``COVER`` of its
  footprint, or when the register calls it a sports building (``SPORTS_USE``), it is long and
  shallow (``ASPECT``, ``MAX_DEPTH``) and its long side faces a mapped track, pitch or stadium
  within ``FIELD_REACH``. Its shell leaves the building program (no facade program, window grid,
  facade candidate or box collider). The stand is generated in the frame of the footprint's
  minimum rotated rectangle, front = the long side nearest the field, seat depth = the median
  front-to-back depth of the footprint: a concrete podium with a board band, one or two tiers
  of seat rows split by white aisle stairs every ``AISLE_SPACING``, a back wall with a dark
  glazed band behind the top row, stepped end walls, a roof canopy on back columns and diagonal
  brackets, and floodlight heads along its front edge. The canopy is one gently sloped plane
  fitted to the LoD2 roof only where its parts cover the stand (a part's plane never extends
  past its own polygon), at most ``ROOF_PITCH`` across and ``ROOF_LEVEL`` along the stand. A low
  stand (under ``ROOF_MIN`` tall) gets open bleacher rows without a roof. The rest of an
  irregular footprint (service wings) is plain concrete massing at its own LoD2 roof heights.
* **Running tracks** (``leisure=track``): the rubber track finish on the ground, with white lane
  lines (``lanes`` tag, else the ring width over ``LANE``). An oval track's lines are offsets of
  its infield ring; a straight track's run along its long axis.
* **Pitches** (``leisure=pitch``): grass pitches get alternating mown stripes across their long
  axis. Line markings follow ``sport``: football (touch/goal lines, halfway line, centre circle,
  penalty and goal areas, spots), tennis (doubles/singles lines, service boxes, net), basketball
  (boundary, centre line and circle, keys); other sports their boundary. Football goals stand on
  both goal lines, sized by the pitch width. A pitch already carrying a surveyed playground ball
  court (the playground stage places its boards and goals) keeps only its lines.
* **Terraces** (``leisure=bleachers`` areas not covering a building): stepped concrete rows rising
  away from the nearest field outline, ``TERRACE_TREAD`` deep, the rise following the lidar bank
  where it is steeper than ``TERRACE_RISE``, with a railing along the top edge.

Every venue part is low-poly (rows are single quads, aisles split them) and reuses a handful of
procedural materials, so a full stadium adds a few thousand triangles and under ten draws. The
ground finishes are ``street_materials`` visual ids (collision keeps the physical class), the
lines one draped paint mesh, the structures ``layer=structures`` meshes with matching collision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely

from . import street_materials as SM

SPORTS_USE = ("2550",)  # register (LoD2 ``function``/``usage``) code of sports buildings
COVER = 0.4  # share of a building footprint an OSM grandstand/bleachers area must cover
ASPECT = 3.0  # min length / depth of a register sports building read as a grandstand
MAX_DEPTH = 30.0  # m
FIELD_REACH = 25.0  # m from a grandstand's front edge to its field
ROW = 0.8  # m seat-row depth
ROW_RISE = (0.25, 0.55)  # m per row
PODIUM = (1.2, 2.5)  # m front wall below the first row
CONCOURSE = 1.6  # m walkway between two tiers
UPPER_STEP = 1.0  # m front wall of the upper tier above the concourse
BACK_AISLE = 1.5  # m walkway behind the top row
ROOF_CLEAR = 3.2  # m above the top row
ROOF_MIN = 4.5  # m: a lower stand is open bleachers
ROOF_THICK = 0.7  # m fascia
ROOF_PITCH = 0.12  # canopy rise per metre of seat depth: nearly flat, never a wedge
ROOF_LEVEL = 0.01  # canopy rise per metre along the stand
ROOF_OUTLIER = 2.0  # m: covered roof samples further than this from the fit (towers, wings)
AISLE_SPACING = 11.0  # m between aisle stairs
AISLE = 1.2  # m aisle width
BAY = 9.5  # m between roof columns
LANE = 1.22  # m athletics lane
LINE = 0.05  # m track line width
PITCH_LINE = 0.12  # m pitch line width
STRIP = 2.0  # m longest line paint quad
PAINT_LIFT = 0.008  # m over the ground surface, as road paint
STRIPE = 5.5  # m mown stripe width
TERRACE_TREAD = 0.8  # m
TERRACE_RISE = 0.38  # m minimum rise per terrace row
RAIL = 1.0  # m railing height
STRUCTURE_SINK = 1.0  # m vertical faces reach below the sampled ground, closing slopes

GRASS_SURFACES = (None, "grass", "artificial_turf", "grass_paver")
FOOTBALL = ("soccer", "football", "american_football", "rugby", "rugby_union", "rugby_league",
            "gaelic_games", "australian_football", "field_hockey", "lacrosse")

# Procedural albedo (sRGB) of venue structure surfaces: concrete, timber seats, white aisle
# paint, a light metal roof, dark steel, glazing and board panels.
PALETTE = {
    "concrete": (166, 164, 156),
    "seat": (172, 128, 64),
    "paint": (226, 224, 214),
    "roof": (196, 199, 199),
    "steel": (88, 94, 98),
    "glass": (52, 61, 68),
    "board": (34, 46, 84),
}


@dataclass
class Venues:
    """The OSM venue features near a tile and the grandstand buildings among its shells."""

    tracks: list = field(default_factory=list)  # (polygon, tags)
    pitches: list = field(default_factory=list)  # (polygon, tags)
    fields: list = field(default_factory=list)  # track/pitch polygons (stand fronts, terrace rows)
    stands: list = field(default_factory=list)  # OSM grandstand / bleachers polygons
    terraces: list = field(default_factory=list)  # bleachers areas covering no building
    open_stands: list = field(default_factory=list)  # OSM grandstands covering no building
    grandstands: dict = field(default_factory=dict)  # building key -> reason

    @property
    def empty(self):
        return not (self.tracks or self.pitches or self.terraces or self.grandstands
                    or self.open_stands)


def source_files():
    base = Path(__file__).parents[1]
    return [Path(__file__), Path(SM.__file__), Path(__file__).with_name("roofs.py"),
            base / "structures/geometry.py", base / "package/gltf.py", base / "spatial.py"]


def prepare(cache, tile, sources, buildings):
    """Cached venue features of the tile and the grandstands among its (and context) buildings."""
    from turku3d.stage_cache import source_key
    from turku3d.tile_layer import identities

    osm = sources.osm_source(tile)
    return cache.run(
        "venue_features",
        source_key(*source_files()),
        [tile, identities({"osm": osm}) if osm else None,
         [(b.key, b.footprint.wkb, str(b.attributes.get("use", ""))) for b in buildings]],
        lambda: features(tile, osm, buildings),
    )


def courts(playgrounds):
    """Surveyed playground ball court outlines (their stage places boards and goals)."""
    return [shapely.make_valid(shapely.Polygon(o["polygon"])) for site in playgrounds
            for o in site.get("objects", ()) if o.get("category") == "ballcourt" and len(o["polygon"]) >= 3]


def _polygons(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "LineString" and geometry.is_ring:
        geometry = shapely.Polygon(geometry)
    geometry = shapely.make_valid(geometry)
    return [g for g in getattr(geometry, "geoms", [geometry]) if g.geom_type == "Polygon" and g.area > 1.0]


def features(tile, osm, buildings):
    """Venue features within reach of the tile, and which of the buildings are grandstands."""
    from turku3d.spatial import rows

    venues = Venues()
    if osm is None:
        return venues
    seen = set()
    for _, row in rows(osm, tile.bbox.buffered(120)):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        leisure, building = tags.get("leisure"), tags.get("building")
        stand = building == "grandstand" or tags.get("man_made") == "grandstand"
        if not (stand or leisure in ("stadium", "track", "pitch", "bleachers")):
            continue
        if tags.get("indoor") == "yes" or tags.get("layer", "0") not in ("0", ""):
            continue
        for polygon in _polygons(shapely.force_2d(row["geometry_local"])):
            identity = (leisure, building, shapely.normalize(polygon).wkb)
            if identity in seen:
                continue
            seen.add(identity)
            if stand or leisure == "bleachers":
                venues.stands.append((polygon, tags))
            elif leisure == "track":
                venues.tracks.append((polygon, tags))
                venues.fields.append(polygon)
            elif leisure == "pitch":
                if not building:
                    venues.pitches.append((polygon, tags))
                venues.fields.append(polygon)
    venues.grandstands = grandstands(buildings, venues)
    covered = [b.footprint for b in buildings]
    for polygon, tags in venues.stands:
        overlap = sum(polygon.intersection(f).area for f in covered if f.intersects(polygon))
        if overlap >= 0.2 * polygon.area:
            continue
        if tags.get("leisure") == "bleachers" and not tags.get("building"):
            venues.terraces.append((polygon, tags))
        else:
            venues.open_stands.append((polygon, tags))
    return venues


def frame(polygon):
    """(origin, u, v, length, depth) of a polygon's minimum rotated rectangle, u along its long side."""
    with np.errstate(divide="ignore", invalid="ignore"):
        rect = shapely.minimum_rotated_rectangle(polygon)
    ring = np.asarray(rect.exterior.coords)[:4]
    edges = np.roll(ring, -1, axis=0) - ring
    lengths = np.linalg.norm(edges, axis=1)
    k = int(np.argmax(lengths))
    u = edges[k] / max(lengths[k], 1e-9)
    v = np.array([-u[1], u[0]])
    if (ring - ring[k]) @ v @ np.ones(4) < 0:
        v = -v
    return ring[k], u, v, float(lengths[k]), float(lengths[(k + 1) % 4])


def _front(polygon, fields):
    """The rectangle frame with its origin on the long side nearest the venue's field."""
    origin, u, v, length, depth = frame(polygon)
    fields = [f for f in fields if f.intersection(polygon).area < 0.1 * polygon.area]
    if fields:
        field_area = shapely.union_all(fields)
        near = shapely.Point(*(origin + u * length / 2))
        far = shapely.Point(*(origin + u * length / 2 + v * depth))
        if far.distance(field_area) < near.distance(field_area):
            origin, v = origin + v * depth, -v
            origin, u = origin + u * length, -u
    return origin, u, v, length, depth


def grandstands(buildings, venues):
    stands = [p for p, _ in venues.stands]
    result = {}
    for b in buildings:
        footprint = b.footprint
        covered = sum(footprint.intersection(p).area for p in stands if p.intersects(footprint))
        if covered >= COVER * footprint.area:
            result[b.key] = "osm"
            continue
        use = str(b.attributes.get("use", ""))
        if use not in SPORTS_USE or not venues.fields:
            continue
        origin, u, v, length, depth = _front(footprint, venues.fields)
        seat_depth = _depth(footprint, origin, u, v, length)
        if seat_depth <= 0 or length < ASPECT * seat_depth or seat_depth > MAX_DEPTH:
            continue
        front = shapely.LineString([origin, origin + u * length])
        if min(front.distance(f) for f in venues.fields) <= FIELD_REACH:
            result[b.key] = "register"
    return result


def _depth(footprint, origin, u, v, length, stations=None):
    """Front-to-back depth of the footprint's first part at stations along the front edge."""
    ts = np.linspace(0.05, 0.95, 19) if stations is None else stations
    depths = []
    for t in ts:
        p = origin + u * length * t
        line = shapely.LineString([p - v * 0.5, p + v * (MAX_DEPTH * 3)])
        piece = footprint.buffer(0.05).intersection(line)
        parts = [g for g in getattr(piece, "geoms", [piece]) if g.geom_type == "LineString" and g.length > 0]
        if not parts:
            depths.append(0.0)
            continue
        first = min(parts, key=lambda g: float(min((np.asarray(g.coords) - p) @ v)))
        depths.append(float(max((np.asarray(first.coords) - p) @ v)))
    depths = np.asarray(depths)
    return depths if stations is not None else float(np.median(depths[depths > 0])) if (depths > 0).any() else 0.0


# -- structures ------------------------------------------------------------------------------------


class Builder:
    """Per-surface face sets in world-local coordinates; one mesh per surface and collider class.

    Materials are single-sided, so every face is oriented by an explicit outward direction."""

    def __init__(self, prefix):
        from turku3d.structures.geometry import Faces

        self.prefix = prefix
        self.sets = {}
        self._faces = Faces

    def faces(self, surface, material):
        key = (surface, material)
        if key not in self.sets:
            self.sets[key] = self._faces()
        return self.sets[key]

    def quad(self, surface, material, points, out):
        """A planar polygon facing ``out`` (a 3-vector), or away from ``out`` given as ('from', point)."""
        p = np.asarray(points, float)
        normal = np.cross(p[1] - p[0], p[2] - p[0])
        if np.linalg.norm(normal) < 1e-12 and len(p) > 3:
            normal = np.cross(p[2] - p[0], p[3] - p[0])
        if isinstance(out, tuple) and out and out[0] == "from":
            direction = p.mean(axis=0) - np.asarray(out[1], float)
        else:
            direction = np.asarray(out, float)
        if normal @ direction < 0:
            p = p[::-1]
        self.faces(surface, material).face(p)

    def beam(self, surface, material, a, b, size, caps=True):
        """A square beam between two 3D points; ``caps=False`` for ends buried in other parts."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        axis = b - a
        length = np.linalg.norm(axis)
        if length < 1e-6:
            return
        axis /= length
        side = np.cross(axis, [0.0, 1.0, 0.0])
        if np.linalg.norm(side) < 1e-6:
            side = np.array([1.0, 0.0, 0.0])
        side /= np.linalg.norm(side)
        up = np.cross(side, axis)
        h = size / 2
        ring = [side * h + up * h, -side * h + up * h, -side * h - up * h, side * h - up * h]
        centre = ("from", (a + b) / 2)
        for k in range(4):
            j = (k + 1) % 4
            self.quad(surface, material, [a + ring[k], b + ring[k], b + ring[j], a + ring[j]], centre)
        if caps:
            self.quad(surface, material, [a + r for r in ring], -axis)
            self.quad(surface, material, [b + r for r in ring], axis)

    def meshes(self, collision=True):
        out = []
        for (surface, material), faces in sorted(self.sets.items()):
            if not faces.indices:
                continue
            solid = collision and material != "none"
            out.append(faces.mesh(f"{self.prefix}:{surface}:{material}",
                                  "street_furniture" if material == "none" else material,
                                  metadata=dict(surface=surface, kind="venue", collision=solid)))
        return out


UP = np.array([0.0, 1.0, 0.0])


def _stand_profile(seat_depth, height):
    """(podium, rows, roofed): every row as (v_front, v_back, tread height above base, tier)."""
    podium = min(max(0.12 * height, PODIUM[0]), PODIUM[1])
    roofed = height >= ROOF_MIN
    avail = seat_depth - (BACK_AISLE if roofed else 0.3) - 0.5
    tiers = 2 if roofed and avail >= 10.0 else 1
    per_tier = max(1, int((avail - CONCOURSE * (tiers - 1)) / tiers / ROW))
    top = height - ROOF_CLEAR if roofed else height
    climb = top - podium - (UPPER_STEP if tiers == 2 else 0.0)
    rise = min(max(climb / (per_tier * tiers), ROW_RISE[0]), ROW_RISE[1])
    rows, v, h = [], 0.5, podium
    for tier in range(tiers):
        if tier:
            v += CONCOURSE
            h += UPPER_STEP
        for _ in range(per_tier):
            rows.append((v, v + ROW, h + rise, tier))
            v += ROW
            h += rise
    return podium, rows, roofed


def grandstand(b, fields, height_at):
    """Structure meshes of one grandstand building, in world-local coordinates."""
    from .roofs import plane_height

    footprint = b.footprint
    origin, u, v, length, _ = _front(footprint, fields)
    stations = np.linspace(0.0, 1.0, max(3, int(length / 2.0)) + 1)
    profile = _depth(footprint, origin, u, v, length, stations)
    seat_depth = float(np.median(profile[profile > 0])) if (profile > 0).any() else 0.0
    if seat_depth < 3.0:
        return []
    inside = np.flatnonzero(profile >= 0.6 * seat_depth)
    u0, u1 = stations[inside[0]] * length, stations[inside[-1]] * length
    if u1 - u0 < 6.0:
        return []
    front = np.array([-v[0], 0.0, -v[1]])  # towards the field
    back = -front
    side = np.array([u[0], 0.0, u[1]])

    def world(a, bb):
        p = origin + u * a + v * bb
        return float(p[0]), float(p[1])

    def p3(a, bb, h):
        x, z = world(a, bb)
        return (x, h, z)

    box = shapely.Polygon([world(u0, 0), world(u1, 0), world(u1, seat_depth), world(u0, seat_depth)])
    base = float(b.base)
    roof_at = _canopy(b, box, world, u0, u1, seat_depth, plane_height)

    roof_mid = roof_at((u0 + u1) / 2, seat_depth / 2)
    podium, rows, roofed = _stand_profile(seat_depth, roof_mid - base)
    sink = base - STRUCTURE_SINK
    s = Builder(f"venue:{b.key}")
    count = max(1, int(round((u1 - u0) / AISLE_SPACING)))
    centres = np.linspace(u0, u1, count + 1)
    aisles = [(max(u0, c - AISLE / 2), min(u1, c + AISLE / 2)) for c in centres]
    blocks = [(aisles[k][1], aisles[k + 1][0]) for k in range(count)]
    # Podium: front wall with a board band and the front walkway.
    s.quad("concrete", "wall", [p3(u0, 0, sink), p3(u1, 0, sink), p3(u1, 0, base + podium),
                                p3(u0, 0, base + podium)], front)
    board_top = base + min(1.15, podium - 0.1)
    s.quad("board", "none", [p3(u0, -0.03, base + 0.25), p3(u1, -0.03, base + 0.25),
                             p3(u1, -0.03, board_top), p3(u0, -0.03, board_top)], front)
    s.quad("concrete", "paving_slab", [p3(u0, 0, base + podium), p3(u0, rows[0][0], base + podium),
                                       p3(u1, rows[0][0], base + podium), p3(u1, 0, base + podium)], UP)
    previous_h, previous_v, previous_tier = base + podium, rows[0][0], 0
    for v_front, v_back, h, tier in rows:
        h = base + h
        if tier != previous_tier:
            # Concourse floor and the upper tier's front wall.
            s.quad("concrete", "paving_slab", [p3(u0, previous_v, previous_h), p3(u0, v_front, previous_h),
                                               p3(u1, v_front, previous_h), p3(u1, previous_v, previous_h)], UP)
            s.quad("concrete", "wall", [p3(u0, v_front, previous_h), p3(u1, v_front, previous_h),
                                        p3(u1, v_front, h), p3(u0, v_front, h)], front)
            previous_tier = tier
        else:
            for a0, a1 in blocks:
                s.quad("seat", "stair_slope", [p3(a0, v_front, previous_h), p3(a1, v_front, previous_h),
                                               p3(a1, v_front, h), p3(a0, v_front, h)], front)
            mid, half = (v_front + v_back) / 2, previous_h + (h - previous_h) / 2
            for a0, a1 in aisles:
                # Two white aisle steps per seat row.
                s.quad("paint", "stair_slope", [p3(a0, v_front, previous_h), p3(a1, v_front, previous_h),
                                                p3(a1, v_front, half), p3(a0, v_front, half)], front)
                s.quad("paint", "stair_slope", [p3(a0, v_front, half), p3(a0, mid, half),
                                                p3(a1, mid, half), p3(a1, v_front, half)], UP)
                s.quad("paint", "stair_slope", [p3(a0, mid, half), p3(a1, mid, half),
                                                p3(a1, mid, h), p3(a0, mid, h)], front)
                s.quad("paint", "stair_slope", [p3(a0, mid, h), p3(a0, v_back, h),
                                                p3(a1, v_back, h), p3(a1, mid, h)], UP)
            for a0, a1 in blocks:
                s.quad("seat", "stair_slope", [p3(a0, v_front, h), p3(a0, v_back, h),
                                               p3(a1, v_back, h), p3(a1, v_front, h)], UP)
            previous_h, previous_v = h, v_back
            continue
        for a0, a1 in [(u0, u1)]:
            s.quad("seat", "stair_slope", [p3(a0, v_front, h), p3(a0, v_back, h),
                                           p3(a1, v_back, h), p3(a1, v_front, h)], UP)
        previous_h, previous_v = h, v_back
    top_h, top_v = previous_h, previous_v
    # Back walkway, back wall (a dark glazed band behind the top row) and stepped end walls.
    s.quad("concrete", "paving_slab", [p3(u0, top_v, top_h), p3(u0, seat_depth, top_h),
                                       p3(u1, seat_depth, top_h), p3(u1, top_v, top_h)], UP)
    low, high = (roof_at(u0, seat_depth), roof_at(u1, seat_depth)) if roofed else (top_h + RAIL,) * 2
    s.quad("concrete", "wall", [p3(u0, seat_depth, sink), p3(u1, seat_depth, sink),
                                p3(u1, seat_depth, high), p3(u0, seat_depth, low)], back)
    s.quad("concrete", "wall", [p3(u0, seat_depth - 0.3, top_h), p3(u1, seat_depth - 0.3, top_h),
                                p3(u1, seat_depth - 0.3, high), p3(u0, seat_depth - 0.3, low)], front)
    if roofed:
        band = min(2.4, (roof_mid - top_h) * 0.5)
        s.quad("glass", "none", [p3(u0, seat_depth - 0.33, top_h + 0.4), p3(u1, seat_depth - 0.33, top_h + 0.4),
                                 p3(u1, seat_depth - 0.33, top_h + 0.4 + band),
                                 p3(u0, seat_depth - 0.33, top_h + 0.4 + band)], front)
    for a, outward in ((u0, -side), (u1, side)):
        outline = [(0.0, sink), (0.0, base + podium)]
        h_prev = base + podium
        for v_front, v_back, h, _ in rows:
            outline += [(v_front, h_prev), (v_front, base + h), (v_back, base + h)]
            h_prev = base + h
        outline += [(seat_depth, h_prev), (seat_depth, sink)]
        _end_wall(s, outline, a, p3, outward)
        if roofed:
            # End wind screens from the top tier up to the roof.
            r = roof_at(a, seat_depth) - ROOF_THICK
            _end_wall(s, [(top_v, top_h), (seat_depth, top_h), (seat_depth, r),
                          (top_v + 0.6 * (seat_depth - top_v), r)], a, p3, outward)
    if roofed:
        _roof(s, u0, u1, seat_depth, roof_at, p3, top_h, front)
    else:
        rail = top_h + RAIL
        s.beam("steel", "railing", p3(u0, seat_depth - 0.1, rail), p3(u1, seat_depth - 0.1, rail), 0.06)
    meshes = s.meshes()
    wings = footprint.difference(box.buffer(0.3, join_style="mitre"))
    meshes.extend(_massing(b, wings, base, f"venue:{b.key}:wing"))
    return meshes


def _canopy(b, box, world, u0, u1, depth, plane_height):
    """The stand roof height at (u, v): a plane fitted to the roof parts that cover the stand.

    Each sample takes the topmost part covering it, so a small steep LoD2 facet cannot be
    extrapolated across the stand. A stand without covering parts keeps a flat roof at its top."""
    parts = [(g, plane) for g, plane in b.roof_parts if g.intersects(box)]
    samples = []
    for a in np.linspace(u0, u1, max(3, int((u1 - u0) / 4.0)) + 1):
        for bb in np.linspace(0.0, depth, max(3, int(depth / 3.0)) + 1):
            xz = world(a, bb)
            point = shapely.Point(xz)
            heights = [float(plane_height(plane, xz)) for g, plane in parts if g.covers(point)]
            if heights:
                samples.append((a, bb, max(heights)))
    if len(samples) < 3:
        top = float(b.top)
        return lambda a, bb: top
    a, bb, h = np.asarray(samples, float).T
    am, bm = float(a.mean()), float(bb.mean())
    keep = np.ones(len(h), bool)
    for _ in range(2):
        design = np.c_[a[keep] - am, bb[keep] - bm, np.ones(keep.sum())]
        if np.linalg.matrix_rank(design) < 3:
            su, sv = 0.0, 0.0
        else:
            su, sv, _ = np.linalg.lstsq(design, h[keep], rcond=None)[0]
        su = float(np.clip(su, -ROOF_LEVEL, ROOF_LEVEL))
        sv = float(np.clip(sv, -ROOF_PITCH, ROOF_PITCH))
        level = float(np.median(h[keep] - su * (a[keep] - am) - sv * (bb[keep] - bm)))
        residual = np.abs(h - (level + su * (a - am) + sv * (bb - bm)))
        if (residual <= ROOF_OUTLIER).sum() < 3:
            break
        keep = residual <= ROOF_OUTLIER
    return lambda u, v: level + su * (u - am) + sv * (v - bm)


def _end_wall(s, outline, a, p3, outward):
    """A stepped wall polygon in the plane u = a, given as (v, height) points."""
    import mapbox_earcut

    vh = np.asarray(outline, float)
    keep = np.r_[True, np.linalg.norm(np.diff(vh, axis=0), axis=1) > 1e-6]
    vh = vh[keep]
    if len(vh) < 3:
        return
    triangles = mapbox_earcut.triangulate_float64(vh, np.array([len(vh)], np.uint32)).reshape(-1, 3)
    points = np.array([p3(a, bb, h) for bb, h in vh])
    for tri in triangles:
        s.quad("concrete", "wall", points[tri], outward)


def _roof(s, u0, u1, depth, roof_at, p3, top_h, front):
    """A flat slab cantilevered over the stand, back columns, brackets and floodlight heads."""
    corners = [(u0, -0.3), (u1, -0.3), (u1, depth), (u0, depth)]
    top = [np.asarray(p3(a, b, roof_at(a, b))) for a, b in corners]
    low = [np.asarray(p3(a, b, roof_at(a, b) - ROOF_THICK)) for a, b in corners]
    s.quad("roof", "roof", top, UP)
    s.quad("roof", "roof", low, -UP)
    centre = ("from", np.mean(top + low, axis=0))
    for k in range(4):
        j = (k + 1) % 4
        s.quad("roof", "roof", [low[k], low[j], top[j], top[k]], centre)
    bays = max(1, int(round((u1 - u0) / BAY)))
    for a in np.linspace(u0 + 0.4, u1 - 0.4, bays + 1):
        under = roof_at(a, depth) - ROOF_THICK
        s.beam("steel", "pole", p3(a, depth - 0.65, top_h - 0.2), p3(a, depth - 0.65, under), 0.45, caps=False)
        knee = roof_at(a, 0.4 * depth) - ROOF_THICK
        s.beam("steel", "none", p3(a, depth - 0.65, under - 3.5), p3(a, 0.4 * depth, knee), 0.3, caps=False)
        s.beam("steel", "none", p3(a, 0.4 * depth, knee - 0.15), p3(a, 0.0, roof_at(a, 0.0) - ROOF_THICK - 0.15), 0.25,
               caps=False)
    lights = max(2, int((u1 - u0) / 14.0))
    for a in np.linspace(u0 + 3.0, u1 - 3.0, lights):
        h = roof_at(a, 0.2)
        s.beam("steel", "none", p3(a - 1.2, 0.3, h + 0.55), p3(a + 1.2, 0.3, h + 0.55), 0.5)


def _massing(b, remainder, base, prefix):
    """Plain concrete prisms for the rest of a grandstand footprint, at their LoD2 roof heights."""
    import mapbox_earcut

    from .roofs import plane_height

    s = Builder(prefix)
    sink = base - STRUCTURE_SINK
    for part in _polygons(remainder):
        if part.area < 15.0 or part.buffer(-1.0).is_empty:
            continue
        part = shapely.geometry.polygon.orient(part.simplify(0.2), 1.0)
        if part.geom_type != "Polygon" or part.is_empty:
            continue
        c = part.representative_point()
        heights = [float(plane_height(plane, (c.x, c.y))) for g, plane in b.roof_parts if g.covers(c)]
        top = max(heights) if heights else min(float(b.top), base + 6.0)
        if top < base + 2.0:
            continue
        ring = np.asarray(part.exterior.coords)[:-1]
        for i in range(len(ring)):
            a, bb = ring[i], ring[(i + 1) % len(ring)]
            normal = np.array([bb[1] - a[1], 0.0, -(bb[0] - a[0])])  # right of a->b on a CCW ring
            s.quad("concrete", "wall", [(a[0], sink, a[1]), (bb[0], sink, bb[1]), (bb[0], top, bb[1]),
                                        (a[0], top, a[1])], normal)
        tris = mapbox_earcut.triangulate_float64(ring, np.array([len(ring)], np.uint32)).reshape(-1, 3)
        for tri in tris:
            s.quad("roof", "roof", np.c_[ring[tri][:, 0], np.full(3, top), ring[tri][:, 1]], UP)
    return s.meshes()


def goals(tile, venues, height_at, courts=()):
    """Football goals on both goal lines of every football pitch whose centre is in the tile."""
    s = Builder(f"venue:{tile.id}:goals")
    count = 0
    blocked = shapely.union_all(list(courts)) if courts else shapely.Polygon()
    for polygon, tags in venues.pitches:
        sport = str(tags.get("sport", "")).split(";")[0]
        if sport not in FOOTBALL:
            continue
        c = polygon.centroid
        if not tile.bbox.contains(c.x, c.y) or (not blocked.is_empty and blocked.intersects(polygon)):
            continue
        origin, u, v, length, width = frame(polygon)
        if length < 30.0:
            continue
        span, high = (7.32, 2.44) if width >= 45 else (5.0, 2.0) if width >= 25 else (3.0, 1.2)
        for end, inward in ((0.0, 1.0), (length, -1.0)):
            centre = origin + u * end + v * width / 2
            y = height_at(*centre)
            left, right = centre - v * span / 2, centre + v * span / 2
            behind = u * (-inward) * 1.6
            for post in (left, right):
                s.beam("paint", "pole", (post[0], y - 0.2, post[1]), (post[0], y + high, post[1]), 0.12, caps=False)
                s.beam("steel", "none", (post[0], y + high, post[1]), (post[0] + behind[0], y, post[1] + behind[1]), 0.05,
                       caps=False)
            s.beam("paint", "pole", (left[0], y + high, left[1]), (right[0], y + high, right[1]), 0.12)
            count += 1
    return s.meshes(), count


def terraces(tile, venues, height_at):
    """Stepped concrete rows of bleachers areas, rising away from the nearest field outline."""
    s = Builder(f"venue:{tile.id}:terraces")
    window = shapely.box(*tile.bbox.as_tuple())
    rows_built = 0
    for polygon, _ in venues.terraces:
        clipped = polygon.intersection(window)
        if clipped.is_empty:
            continue
        near = [f for f in venues.fields if f.distance(polygon) < 30.0 and not f.covers(polygon)]
        if not near:
            continue
        field_area = shapely.union_all(near)
        edge = field_area.boundary
        start = float(field_area.distance(polygon))
        depth = max(field_area.distance(shapely.Point(p)) for p in np.asarray(polygon.exterior.coords))
        count = int(min(30, max(1, (depth - start) / TERRACE_TREAD)))
        # Measured bank rise: lidar ground across the terrace over its front ground line.
        bank = []
        for t in np.linspace(0, 1, 32, endpoint=False):
            p = np.asarray(polygon.exterior.interpolate(t, normalized=True).coords[0])
            d = field_area.distance(shapely.Point(p))
            if d - start > 0.5 * (depth - start):
                q = edge.interpolate(edge.project(shapely.Point(p)))
                bank.append((height_at(*p) - height_at(q.x, q.y)) / max(d - start, 1.0))
        slope = float(np.median(bank)) if bank else 0.0
        rise = max(TERRACE_RISE, min(0.55, slope * TERRACE_TREAD))
        for row in range(count):
            band = clipped.intersection(field_area.buffer(start + (row + 1) * TERRACE_TREAD)).difference(
                field_area.buffer(start + row * TERRACE_TREAD))
            for part in _polygons(band) if band.area > 0.05 else []:
                _terrace_band(s, part, edge, height_at, row, rise,
                              field_area, start + (row + 1) * TERRACE_TREAD, row == count - 1)
                rows_built += 1
        outer = clipped.boundary.difference(field_area.buffer(start + (count - 0.5) * TERRACE_TREAD))
        for line in getattr(outer, "geoms", [outer]):
            if line.geom_type != "LineString" or line.length < 1.0:
                continue
            pts = np.asarray(line.coords)
            for a, bb in zip(pts[:-1], pts[1:], strict=True):
                ha = _tread_height(a, edge, height_at, count - 1, rise) + RAIL
                hb = _tread_height(bb, edge, height_at, count - 1, rise) + RAIL
                s.beam("steel", "railing", (a[0], ha, a[1]), (bb[0], hb, bb[1]), 0.05, caps=False)
    return s.meshes(), rows_built


def _tread_height(p, edge, height_at, row, rise):
    q = edge.interpolate(edge.project(shapely.Point(p)))
    return height_at(q.x, q.y) + (row + 1) * rise


def _terrace_band(s, part, edge, height_at, row, rise, field_area, back, last):
    """One terrace row: a tread following its front ground line, its riser and its end faces.

    The back edge (at ``back`` from the field) is covered by the next row's riser, so only the
    top row closes its back."""
    import mapbox_earcut

    part = shapely.geometry.polygon.orient(part.simplify(0.1), 1.0)
    if part.is_empty or part.geom_type != "Polygon":
        return
    rings = [np.asarray(r.coords)[:-1] for r in [part.exterior, *part.interiors]]
    xz = np.concatenate(rings)
    if len(xz) < 3:
        return
    tris = mapbox_earcut.triangulate_float64(xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)).reshape(-1, 3)
    h = np.array([_tread_height(p, edge, height_at, row, rise) for p in xz])
    for tri in tris:
        s.quad("concrete", "stair_slope", np.c_[xz[tri][:, 0], h[tri], xz[tri][:, 1]], UP)
    ring, hr = rings[0], h[: len(rings[0])]
    mids = (ring + np.roll(ring, -1, axis=0)) / 2
    hidden = np.abs(shapely.distance(field_area, shapely.points(mids)) - back) < 0.3 * TERRACE_TREAD
    for i in range(len(ring)):
        if hidden[i] and not last:
            continue
        j = (i + 1) % len(ring)
        a, bb = ring[i], ring[j]
        low_a = min(height_at(*a), hr[i] - rise) - 0.3
        low_b = min(height_at(*bb), hr[j] - rise) - 0.3
        normal = np.array([bb[1] - a[1], 0.0, -(bb[0] - a[0])])  # outward of a CCW ring
        s.quad("concrete", "wall", [(a[0], low_a, a[1]), (bb[0], low_b, bb[1]), (bb[0], hr[j], bb[1]),
                                    (a[0], hr[i], a[1])], normal)


def structures(tile, buildings, venues, height_at, playground_courts=()):
    """Grandstand, goal, net and terrace structure meshes owned by the tile, and their counts."""
    meshes, stands = [], 0
    for b in buildings:
        if b.key in venues.grandstands:
            built = grandstand(b, venues.fields, height_at)
            meshes.extend(built)
            stands += bool(built)
    for k, (polygon, tags) in enumerate(venues.open_stands):
        c = polygon.centroid
        if not tile.bbox.contains(c.x, c.y):
            continue
        base = min(height_at(x, z) for x, z in np.asarray(polygon.exterior.coords))
        stand = SimpleNamespace(key=f"osm-stand-{k}", footprint=shapely.geometry.polygon.orient(polygon, 1.0),
                                base=base, top=base + _tag_height(tags), roof_parts=[])
        built = grandstand(stand, venues.fields, height_at)
        meshes.extend(built)
        stands += bool(built)
    goal_meshes, goal_count = goals(tile, venues, height_at, playground_courts)
    terrace_meshes, terrace_rows = terraces(tile, venues, height_at)
    meshes += goal_meshes + terrace_meshes + tennis_nets(tile, venues, height_at)
    stats = dict(grandstands=stands, goals=goal_count, terrace_rows=terrace_rows,
                 triangles=sum(len(m.indices) for m in meshes))
    return meshes, stats


def _tag_height(tags):
    """A mapped stand's height: ``height``, else storeys, else an open single-tier stand."""
    try:
        return min(max(float(str(tags.get("height", "")).removesuffix(" m")), 2.0), 40.0)
    except ValueError:
        pass
    try:
        return min(max(3.2 * float(tags.get("building:levels", "")), 3.0), 40.0)
    except ValueError:
        return 6.0


def texture(surface):
    """Small seamless procedural albedos for venue structures."""
    from io import BytesIO

    from PIL import Image

    rng = np.random.default_rng(97)
    y, x = np.mgrid[:128, :128]
    noise = rng.normal(0, 3, (128, 128))
    if surface == "seat":
        noise += 10 * (y % 16 < 2) - 6 * (y % 16 > 13)  # plank joints along the row (v = height)
        noise += 4 * np.sin(x * np.pi / 9)
    elif surface == "concrete":
        noise += np.repeat(np.repeat(rng.normal(0, 3, (16, 16)), 8, 0), 8, 1)
        noise -= 18 * ((x % 64) < 1)
    elif surface == "roof":
        noise -= 14 * ((x % 32) < 2)  # standing seams
    elif surface == "board":
        noise += 40 * ((y // 32 % 2) * ((x // 48) % 2))
    pixels = np.clip(np.array(PALETTE[surface])[None, None, :] + noise[:, :, None], 0, 255).astype(np.uint8)
    out = BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def visuals(tile, meshes):
    from turku3d.package.gltf import GlbMesh

    return [
        GlbMesh(
            m.object_id,
            m.positions - tile.origin,
            m.indices,
            uvs=m.uvs * (0.5 if m.metadata.get("surface") in ("seat", "paint") else 0.25),
            image=texture(m.metadata.get("surface", "concrete")),
            repeat=True,
            color=(1, 1, 1, 1),
            roughness=0.6 if m.metadata.get("surface") in ("glass", "roof", "steel") else 0.9,
            extras=dict(layer="structures", object_id=m.object_id,
                        material_key="venue_" + m.metadata.get("surface", "concrete"), kind="venue"),
        )
        for m in meshes
        if m.visual
    ]


# -- ground ------------------------------------------------------------------------------------------


def _frame_point(origin, u, v):
    return lambda a, b: tuple(origin + u * a + v * b)


def ground_regions(tile, venues):
    """(polygon, visual finish id) of track rubber and mown pitch stripes over the tile."""
    window = shapely.box(*tile.bbox.buffered(1.0).as_tuple())
    regions = []
    for polygon, tags in venues.pitches:
        if not polygon.intersects(window) or tags.get("surface") not in GRASS_SURFACES:
            continue
        sport = str(tags.get("sport", "")).split(";")[0]
        if sport and sport not in FOOTBALL and sport not in ("athletics", "multi", "baseball", "pesapallo", "cricket"):
            continue
        origin, u, v, length, width = frame(polygon)
        bands = max(2, int(round(length / STRIPE)))
        step = length / bands
        at = _frame_point(origin, u, v)
        for k in range(bands):
            strip = shapely.Polygon([at(k * step, -1), at((k + 1) * step, -1), at((k + 1) * step, width + 1), at(k * step, width + 1)])
            piece = polygon.intersection(strip).intersection(window)
            if not piece.is_empty:
                regions.append((piece, SM.PITCH_LIGHT if k % 2 == 0 else SM.PITCH_DARK))
    for polygon, tags in venues.tracks:
        piece = polygon.intersection(window)
        if not piece.is_empty and tags.get("surface") not in ("grass", "dirt", "gravel", "fine_gravel", "sand", "compacted"):
            regions.append((piece, SM.TRACK))
    return [(g, m) for g, m in regions if g.area > 0.01]


def _lines_track(polygon, tags):
    """(centre line, width) of every lane line, clipped to the track."""
    lines = []
    interiors = list(polygon.interiors)
    if interiors:
        inner = shapely.Polygon(max(interiors, key=lambda r: shapely.Polygon(r).area))
        width = polygon.area / max((polygon.exterior.length + inner.exterior.length) / 2, 1.0)
        lanes = _lanes(tags, width)
        lane = min(LANE, width / lanes)
        for k in range(lanes + 1):
            ring = inner.buffer(k * lane, quad_segs=16).exterior if k else inner.exterior
            lines.append(shapely.LineString(ring.coords))
    else:
        origin, u, v, length, width = frame(polygon)
        lanes = _lanes(tags, width)
        lane = min(LANE, width / lanes)
        offset = (width - lanes * lane) / 2
        at = _frame_point(origin, u, v)
        for k in range(lanes + 1):
            lines.append(shapely.LineString([at(0, offset + k * lane), at(length, offset + k * lane)]))
        lines.append(shapely.LineString([at(length - 1.0, offset), at(length - 1.0, offset + lanes * lane)]))
    area = polygon.buffer(0.05)
    return [(line.intersection(area), LINE) for line in lines]


def _lanes(tags, width):
    try:
        lanes = int(str(tags.get("lanes", "")).split(";")[0])
    except ValueError:
        lanes = 0
    if not 1 <= lanes <= 12:
        lanes = int(round(width / LANE))
    return min(max(lanes, 1), 12)


def _lines_pitch(polygon, tags):
    """(centre line or spot polygon, width) of the pitch markings of its sport."""
    sport = str(tags.get("sport", "")).split(";")[0]
    origin, u, v, length, width = frame(polygon)
    at = _frame_point(origin, u, v)
    lines = [shapely.LineString(polygon.exterior.coords)]

    def rect(a0, b0, a1, b1):
        return shapely.LineString([at(a0, b0), at(a1, b0), at(a1, b1), at(a0, b1), at(a0, b0)])

    def circle(a, b, r):
        return shapely.LineString(shapely.Point(at(a, b)).buffer(r, quad_segs=12).exterior.coords)

    if sport in FOOTBALL and length >= 30:
        lines.append(shapely.LineString([at(length / 2, 0), at(length / 2, width)]))
        lines.append(circle(length / 2, width / 2, min(9.15, width / 7)))
        box_d, box_w = min(16.5, 0.157 * length), min(40.32, 0.6 * width)
        goal_d, goal_w = min(5.5, 0.052 * length), min(18.32, 0.27 * width)
        for end, sign in ((0.0, 1), (length, -1)):
            lines.append(rect(end, (width - box_w) / 2, end + sign * box_d, (width + box_w) / 2))
            lines.append(rect(end, (width - goal_w) / 2, end + sign * goal_d, (width + goal_w) / 2))
            spot = at(end + sign * min(11.0, 0.105 * length), width / 2)
            lines.append(shapely.Point(spot).buffer(0.21, quad_segs=3))
    elif sport == "tennis":
        cl, cw = min(23.77, length - 1.0), min(10.97, width - 1.0)
        a0, b0 = (length - cl) / 2, (width - cw) / 2
        singles = cw * 8.23 / 10.97
        s0 = (width - singles) / 2
        lines = [rect(a0, b0, a0 + cl, b0 + cw),
                 shapely.LineString([at(a0, s0), at(a0 + cl, s0)]),
                 shapely.LineString([at(a0, s0 + singles), at(a0 + cl, s0 + singles)])]
        service = cl * 6.40 / 23.77
        for a in (length / 2 - service, length / 2 + service):
            lines.append(shapely.LineString([at(a, s0), at(a, s0 + singles)]))
        lines.append(shapely.LineString([at(length / 2 - service, width / 2), at(length / 2 + service, width / 2)]))
    elif sport == "basketball":
        lines.append(shapely.LineString([at(length / 2, 0), at(length / 2, width)]))
        lines.append(circle(length / 2, width / 2, min(1.8, width / 8)))
        key_d, key_w = min(5.8, 0.2 * length), min(4.9, 0.33 * width)
        for end, sign in ((0.0, 1), (length, -1)):
            lines.append(rect(end, (width - key_w) / 2, end + sign * key_d, (width + key_w) / 2))
    return [(line, PITCH_LINE) for line in lines]


def line_marks(tile, venues):
    """(centre line or spot polygon, width) of the track and pitch markings over the tile."""
    window = shapely.box(*tile.bbox.as_tuple())
    result = []
    for polygon, tags in venues.tracks:
        if polygon.intersects(window):
            result.extend(_lines_track(polygon, tags))
    for polygon, tags in venues.pitches:
        if polygon.intersects(window):
            result.extend(_lines_pitch(polygon, tags))
    out = []
    for geometry, width in result:
        clipped = geometry.intersection(window)
        for part in getattr(clipped, "geoms", [clipped]):
            if (part.geom_type == "LineString" and part.length > 0.05) or (
                    part.geom_type == "Polygon" and part.area > 0.01):
                out.append((part, width))
    return out


def paint(tile, positions, indices, marks):
    """One merged line paint mesh: every line a strip of quads at most ``STRIP`` long, draped
    ``PAINT_LIFT`` over the ground surface and raised where the surface bulges between its
    vertices; spots are small flat fans. Same material and colour as the road paint."""
    import mapbox_earcut

    from turku3d.ground.mesh import HeightQuery
    from turku3d.package.gltf import GlbMesh

    ground = HeightQuery(SimpleNamespace(positions=positions, indices=indices))

    def height(xz):
        h = ground(np.asarray(xz, float).reshape(-1, 2))
        if np.isnan(h).any():  # a strip end just past the surface edge keeps its neighbour height
            good = ~np.isnan(h)
            h[~good] = np.nanmax(h) if good.any() else 0.0
        return h

    points, faces = [], []
    count = 0
    for geometry, width in marks:
        if geometry.geom_type == "Polygon":
            ring = np.asarray(geometry.exterior.coords)[:-1]
            tris = mapbox_earcut.triangulate_float64(ring, np.array([len(ring)], np.uint32)).reshape(-1, 3)
            h = height(ring).max() + PAINT_LIFT
            points.append(np.c_[ring[:, 0], np.full(len(ring), h), ring[:, 1]])
            faces.append(tris + count)
            count += len(ring)
            continue
        xz = np.asarray(shapely.segmentize(geometry.simplify(0.01), STRIP).coords)[:, :2]
        closed = len(xz) > 3 and np.allclose(xz[0], xz[-1])
        if closed:
            xz = xz[:-1]
        if len(xz) < 2:
            continue
        seg = (np.roll(xz, -1, axis=0) - xz) if closed else np.diff(xz, axis=0)
        seg /= np.maximum(np.linalg.norm(seg, axis=1, keepdims=True), 1e-9)
        normal = np.c_[-seg[:, 1], seg[:, 0]]
        before = np.roll(normal, 1, axis=0) if closed else np.vstack([normal[:1], normal])
        after = normal if closed else np.vstack([normal, normal[-1:]])
        miter = before + after
        miter /= np.maximum(np.linalg.norm(miter, axis=1, keepdims=True), 1e-9)
        scale = 1.0 / np.maximum((miter * after).sum(axis=1), 0.5)
        offset = miter * (scale * width / 2)[:, None]
        left, right = xz + offset, xz - offset
        hl, hr = height(left), height(right)
        n = len(xz)
        a = np.arange(n if closed else n - 1)
        b = (a + 1) % n
        # Raise each quad where the ground surface rises above its plane between the vertices.
        mids = np.r_[(left[a] + left[b]) / 2, (right[a] + right[b]) / 2, (xz[a] + xz[b]) / 2]
        lift = height(mids) - np.r_[(hl[a] + hl[b]) / 2, (hr[a] + hr[b]) / 2, (hl[a] + hl[b] + hr[a] + hr[b]) / 4]
        bulge = np.maximum(lift.reshape(3, -1).max(axis=0), 0.0)
        raise_ = np.zeros(n)
        np.maximum.at(raise_, a, bulge)
        np.maximum.at(raise_, b, bulge)
        hl, hr = hl + PAINT_LIFT + raise_, hr + PAINT_LIFT + raise_
        points.append(np.r_[np.c_[left[:, 0], hl, left[:, 1]], np.c_[right[:, 0], hr, right[:, 1]]])
        # Counter-clockwise seen from above: left a, right a, right b / left a, right b, left b.
        quads = np.c_[a, n + a, n + b, a, n + b, b].reshape(-1, 3) + count
        faces.append(quads)
        count += 2 * n
    if not points:
        return None
    positions = np.concatenate(points)
    faces = np.concatenate(faces).astype(np.uint32)
    tri = positions[faces]
    down = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 1] < 0
    faces[down] = faces[down][:, [0, 2, 1]]
    return GlbMesh(
        "ground_venue_paint",
        positions - tile.origin,
        faces,
        color=(0.72, 0.71, 0.66, 1),
        roughness=0.9,
        extras=dict(layer="markings", object_id=f"venue_paint_{tile.id}", material_key="road_paint"),
        colors=np.ones((len(positions), 3), np.float32),
    )


def tennis_nets(tile, venues, height_at):
    s = Builder(f"venue:{tile.id}:nets")
    for polygon, tags in venues.pitches:
        if str(tags.get("sport", "")).split(";")[0] != "tennis":
            continue
        c = polygon.centroid
        if not tile.bbox.contains(c.x, c.y):
            continue
        origin, u, v, length, width = frame(polygon)
        at = _frame_point(origin, u, v)
        cw = min(10.97, width - 1.0) + 1.8
        a, b0, b1 = length / 2, (width - cw) / 2, (width + cw) / 2
        p0, p1 = np.asarray(at(a, b0)), np.asarray(at(a, b1))
        y0, y1 = height_at(*p0), height_at(*p1)
        for p, y in ((p0, y0), (p1, y1)):
            s.beam("steel", "pole", (p[0], y - 0.1, p[1]), (p[0], y + 1.07, p[1]), 0.08, caps=False)
        net = [(p0[0], y0 + 0.05, p0[1]), (p1[0], y1 + 0.05, p1[1]), (p1[0], y1 + 0.914, p1[1]),
               (p0[0], y0 + 0.914, p0[1])]
        across = np.array([u[0], 0.0, u[1]])
        s.quad("glass", "none", net, across)
        s.quad("glass", "none", net, -across)
        s.beam("paint", "none", (p0[0], y0 + 0.93, p0[1]), (p1[0], y1 + 0.93, p1[1]), 0.05)
    return s.meshes(collision=False)


def stats(venues):
    return dict(tracks=len(venues.tracks), pitches=len(venues.pitches), terraces=len(venues.terraces),
                open_stands=len(venues.open_stands),
                grandstand_buildings=dict(sorted(venues.grandstands.items())))
