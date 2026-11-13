"""Outdoor stair flights, one entity per 62.5 m cell whose ground holds the middle of any.

A flight is an OpenStreetMap way `highway=steps` (or a footway with a `step_count`) on the ground: not indoors, not
on a bridge (the bridges draw their own), and not a building's own entrance steps (a short flight ending at a
footprint: the building kind draws door steps). The proven generator's flights (old `coarse/stairs.py`,
`stair_survey.py`), with the lidar raster for the profile instead of the raw returns:

- profile: the ground along the line every 0.5 m, made monotonic; flat stretches of a metre or more are landings;
- treads and risers: the mapped count (else a rise of ~0.16 m a step, never over 0.30 m), spread over the rising
  stretches, each tread mitred at the mapped bends; solid cheeks from the treads down into the ground beside them;
- width: the city's street register (a `Portaat` part round the flight: its area over the flight's length), else the
  map's `width`, else 2.5 m (the old default);
- material: the register's surface, else the map's `surface`, else by area: granite in the town (the milieu types of
  building/town-milieus.json, where 42 of the ring's 48 register stairs are stone), concrete elsewhere;
- handrails where the map says `handrail=yes` (sides `handrail:left|right=no` left out), over the rising stretches.

Ground keeps its own slope under a flight; the treads stand on it (every tread at or above the slope) and the
player walks the ground's collision.
"""

from __future__ import annotations

import json

import numpy as np
import shapely
from scipy.ndimage import median_filter
from scipy.optimize import isotonic_regression

from ..api import Area, Material, Output, Part, Stub, envelope
from ..frame import CELL, ORIGIN_E, ORIGIN_N, cell_at, cell_box_en, cell_key
from ..street import lidar

VERSION = 1
STATION = 0.5          # m between profile samples along a flight
DEFAULT_WIDTH = 2.5    # m, the old default
RISE = 0.16            # m a step when the map gives no count
MAX_RISE = 0.30        # m: never a higher step
LANDING = 1.0          # m of level profile that is a landing, not a shallow ramp
MIN_RISE = 0.06        # m: a flight that rises less is not drawn
ENTRANCE = 4.0         # m: a flight this short ending at a building footprint is the building's door steps
ENTRANCE_REACH = 0.5   # m from a footprint
CHEEK_ROOT = 0.05      # m the cheeks reach under the ground
RAIL_HEIGHT = 0.9      # m over the slope
RAIL_INSET = 0.06      # m inside the flight's edge
RAIL_POST_SPACING = 1.5
RAIL_SIZE = 0.045      # m: the tube, drawn square

STONE = {"family": "granite", "params": {"colour": "#9d968c", "colour2": "#7d776f", "finish": "slab", "scale_m": 2.0},
         "color": (0.32, 0.30, 0.27), "roughness": 0.8}
MATERIALS = {
    "granite": STONE,  # the building kind's door-step granite
    "concrete": {"family": "concrete", "params": {"colour": "#9a978f", "finish": "cast"}, "color": (0.31, 0.3, 0.28),
                 "roughness": 0.9},
    "wood": {"family": "painted_wood", "params": {"colour": "#6e5843", "finish": "weathered"}, "color": (0.15, 0.1, 0.06),
             "roughness": 0.85},
    "metal": {"family": "metal", "params": {"colour": "#55585a", "finish": "matt"}, "color": (0.09, 0.09, 0.1),
              "roughness": 0.6, "metalness": 0.6},
}
RAIL = {"family": "metal", "params": {"colour": "#2f3133", "finish": "matt"}, "color": (0.03, 0.03, 0.035),
        "roughness": 0.5, "metalness": 0.7}
# Surfaces of the city's street register (Pintamateriaali) and of the map (surface=*), by stair material.
REGISTER = {"Graniitti": "granite", "Noppakivi": "granite", "Luonnonkivi": "granite", "Liuskekivi": "granite",
            "Betoni": "concrete", "Betonilaatta": "concrete", "Betonikivi": "concrete", "Metalli": "metal",
            "Puu": "wood"}
SURFACE = {"paving_stones": "granite", "sett": "granite", "stone": "granite", "granite": "granite", "rock": "granite",
           "unhewn_cobblestone": "granite", "cobblestone": "granite", "concrete": "concrete",
           "concrete:plates": "concrete", "asphalt": "concrete", "wood": "wood", "metal": "metal",
           "metal_grid": "metal"}


def number(value, default):
    try:
        n = float(str(value).removesuffix(" m"))
        return n if np.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def indoor(tags: dict) -> bool:
    """Steps mapped indoors, wholly above the ground floor (every `level` >= 1) or wholly below it (old
    stairs.indoor): no flight stands on the ground."""
    if tags.get("indoor") not in (None, "", "no") or tags.get("location") == "underground":
        return True
    levels = []
    for value in str(tags.get("level", "")).split(";"):
        try:
            levels.append(float(value))
        except ValueError:
            continue
    return bool(levels) and (min(levels) >= 1 or max(levels) <= -1)


def profile(line, window) -> tuple[list, list]:
    """Stations and heights along a flight (old stair_survey.measure, on the lidar raster): monotonic, level
    landings, simplified to 6 cm."""
    stations = np.unique(np.r_[np.arange(0.0, line.length, STATION), line.length])
    points = shapely.get_coordinates(shapely.line_interpolate_point(line, stations))
    heights = window.sample("ground", points[:, 0], points[:, 1]).astype(float)
    heights = median_filter(heights, size=3, mode="nearest")
    heights = isotonic_regression(heights, increasing=bool(heights[-1] >= heights[0])).x
    flat = np.abs(np.diff(heights)) < 0.045
    edges = np.flatnonzero(np.diff(np.r_[False, flat, False]))
    for start, end in edges.reshape(-1, 2):
        if stations[end] - stations[start] >= LANDING:
            heights[start: end + 1] = np.median(heights[start: end + 1])
    simple = np.asarray(shapely.LineString(np.c_[stations, heights]).simplify(0.06).coords)
    return np.round(simple[:, 0], 3).tolist(), np.round(simple[:, 1], 3).tolist()


def register_stairs(box) -> list[tuple]:
    """(polygon, material word) of the street register's stair parts over the box."""
    from ..street import registers

    try:
        frame = registers.layer("GIS:Katualueet", box)
    except Exception:  # noqa: BLE001 - another city or offline: the map alone
        return []
    if "Katuosalaji" not in frame.columns:
        return []
    rows = frame[frame["Katuosalaji"] == "Portaat"]
    out = []
    for geom, word in zip(rows.geometry, rows.get("Pintamateriaali", [None] * len(rows)), strict=True):
        if geom is not None and not geom.is_empty:
            out.append((shapely.make_valid(shapely.force_2d(geom)), str(word) if word is not None else ""))
    return out


class Steps:
    name = "steps"

    def discover(self, area: Area, sources) -> list[Stub]:
        from .building.archetypes import TOWN_MILIEUS
        from .building_shell import milieu_of, milieus

        box = area.bbox(CELL)
        frame = sources.osm.features(box)
        if frame is None or not len(frame):
            return []
        steps = frame["highway"] == "steps"
        if "step_count" in frame.columns:
            counted = frame["highway"].isin(["footway", "path", "pedestrian"]) & ~frame["step_count"].isin([None, "", "0"])
            counted &= frame["step_count"].notna()
            steps |= counted
        rows = frame[steps & (frame.geom_type == "LineString")]
        if not len(rows):
            return []
        buildings = frame[frame["building"].notna() & (frame["building"] != "no")
                          & frame.geom_type.isin(["Polygon", "MultiPolygon"])] if "building" in frame.columns else frame.iloc[:0]
        footprints = shapely.union_all(list(buildings.geometry)) if len(buildings) else None
        stairs, zones = register_stairs(box), milieus(sources, box)
        window = lidar.Window(*area.bbox(CELL + 20.0))
        cells: dict[tuple[int, int], list] = {}
        for osm_id, line, raw in zip(rows["osm_id"], rows.geometry, rows["tags"], strict=True):
            tags = json.loads(raw or "{}")
            if indoor(tags) or tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no":
                continue
            line = shapely.force_2d(line)
            if line.length < 0.2 or tags.get("step_count") == "0":
                continue
            middle = line.interpolate(0.5, normalized=True)
            i, j = cell_at(middle.x, middle.y)
            if not area.touches(cell_box_en(i, j)):
                continue
            ends = [shapely.Point(c) for c in np.asarray(line.coords)[[0, -1], :2]]
            if (footprints is not None and line.length < ENTRANCE
                    and min(footprints.distance(p) for p in ends) < ENTRANCE_REACH):
                continue  # the building's door steps
            flight = self._measure(str(osm_id), line, tags, window, stairs, zones, TOWN_MILIEUS, milieu_of)
            if flight:
                cells.setdefault((i, j), []).append(flight)
        stubs = []
        for (i, j), flights in sorted(cells.items()):
            boxes = np.array([shapely.LineString(f["line"]).buffer(f["width"]).bounds for f in flights])
            bound = (float(boxes[:, 0].min()), float(boxes[:, 1].min()), float(boxes[:, 2].max()), float(boxes[:, 3].max()))
            refs = [{"source": "osm", "key": f["key"]} for f in flights]
            refs.append({"source": "turku-lidar:pistepilvi", "key": cell_key(i, j)})
            stubs.append(Stub(f"steps/{cell_key(i, j)}", bound, refs,
                              {"cell": [i, j], "flights": sorted(flights, key=lambda f: f["key"])}))
        return stubs

    @staticmethod
    def _measure(key, line, tags, window, stairs, zones, town, milieu_of) -> dict | None:
        """A flight's facts, its line running up. `ground` and `rails` name the sides as the engine sees them going
        up the flight: the survey frame's right (east of a flight going north) is the engine's left (z = -N)."""
        middle = line.interpolate(0.5, normalized=True)
        part = next(((poly, word) for poly, word in stairs if poly.covers(middle)), None)
        width = number(tags.get("width"), 0.0)
        if part is not None and not width:
            inside = line.intersection(part[0]).length
            if inside > 0.5 * line.length:
                width = part[0].area / inside
        width = float(np.clip(width or DEFAULT_WIDTH, 0.6, 12.0))
        stations, heights = profile(line, window)
        reversed_ = heights[-1] < heights[0]
        if reversed_:
            line = shapely.LineString(list(line.coords)[::-1])
            stations = (np.round(line.length - np.asarray(stations[::-1]), 3)).tolist()
            heights = heights[::-1]
        stations[0], stations[-1] = 0.0, round(line.length, 3)
        rise = heights[-1] - heights[0]
        if rise < MIN_RISE:
            # A flight the scan sees level (a gap, a roof over it) rises by its mapped incline.
            incline = str(tags.get("incline", ""))
            rise = line.length * abs(number(incline[:-1], 0.0)) / 100.0 if incline.endswith("%") else 0.0
            if rise < MIN_RISE:
                return None
            stations, heights = [0.0, round(line.length, 3)], [heights[0], round(heights[0] + rise, 3)]
        count = round(number(tags.get("step_count"), max(1, round(rise / RISE))))
        count = int(min(max(count, int(np.ceil(rise / MAX_RISE)), 1), 2000))
        # The ground under both cheeks, every STATION m.
        along = np.unique(np.r_[np.arange(0.0, line.length, STATION), line.length])
        sides = []
        for sign in (-1.0, 1.0):  # survey right, left: engine left, right
            edge = line.offset_curve(sign * width / 2, join_style="mitre", mitre_limit=2.0)
            pts = shapely.get_coordinates(shapely.line_interpolate_point(edge, along / line.length, normalized=True))
            sides.append(np.round(window.sample("ground", pts[:, 0], pts[:, 1]), 2).tolist())
        word = part[1] if part is not None else ""
        material = REGISTER.get(word) or SURFACE.get(str(tags.get("surface", "")))
        if material is None:
            material = "granite" if milieu_of(line, zones) in town else "concrete"
        rail = tags.get("handrail") == "yes"
        # The map's left and right follow the way as drawn; the engine's left is the survey's right going up.
        osm_right, osm_left = rail and tags.get("handrail:right") != "no", rail and tags.get("handrail:left") != "no"
        rails = [osm_left, osm_right] if reversed_ else [osm_right, osm_left]
        return {
            "key": key,
            "line": np.round(np.asarray(line.coords)[:, :2], 3).tolist(),
            "width": round(width, 2),
            "count": count,
            "stations": stations,
            "heights": heights,
            "ground": {"along": np.round(along, 3).tolist(), "left": sides[0], "right": sides[1]},
            "material": material,
            "rails": rails,
        }

    def describe(self, stub: Stub, stored: dict | None) -> dict:
        """Source facts; a stored description may set a flight's `material` (by key) or the `materials`."""
        stored = stored or {}
        flights = [{**f, **(stored.get("flights", {}).get(f["key"], {}))} for f in stub.facts["flights"]]
        return {**envelope(self.name, VERSION, stub, stored, "w2-landmarks"), "cell": stub.facts["cell"],
                "flights": flights, "materials": {**MATERIALS, **stored.get("materials", {}), "rail": RAIL}}

    def compile(self, description: dict) -> Output:
        i, j = description["cell"]
        e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
        meshes: dict[str, _Mesh] = {}
        for flight in description["flights"]:
            body = meshes.setdefault(flight["material"], _Mesh())
            rails = meshes.setdefault("rail", _Mesh())
            _flight(flight, e0, n1, body, rails)
        meshes = {k: m for k, m in meshes.items() if m.positions}
        if not meshes:
            return Output((i * CELL, 0.0, j * CELL), [])
        base = float(np.floor(min(np.concatenate(m.positions)[:, 1].min() for m in meshes.values())))
        parts = []
        for name, mesh in sorted(meshes.items()):
            spec = description["materials"][name]
            positions = np.concatenate(mesh.positions)
            positions[:, 1] -= base
            parts.append(Part("steps-rail" if name == "rail" else "steps", positions, np.concatenate(mesh.triangles),
                              Material(spec["family"], spec.get("params", {}), tuple(spec["color"]),
                                       spec.get("roughness", 0.85), spec.get("metalness", 0.0)),
                              uvs=np.concatenate(mesh.uvs)))
        return Output((i * CELL, base, j * CELL), parts, error_m={0: 0.03})


class _Mesh:
    """Quads with metre UVs, each wound to face a given direction."""

    def __init__(self):
        self.positions, self.triangles, self.uvs = [], [], []
        self.count = 0

    def quads(self, corners: np.ndarray, uvs: np.ndarray, facing: np.ndarray):
        """corners (q, 4, 3) round each quad, uvs (q, 4, 2), facing (q, 3) or (3,): the side the faces show."""
        if not len(corners):
            return
        q = len(corners)
        normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        normal += np.cross(corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 0])
        flip = np.einsum("ij,ij->i", normal, np.broadcast_to(facing, (q, 3))) < 0
        k = self.count + np.arange(q)[:, None] * 4
        tris = np.stack([np.c_[k, k + 1, k + 2], np.c_[k, k + 2, k + 3]], axis=1)
        tris[flip] = tris[flip][:, :, ::-1]
        keep = np.linalg.norm(normal, axis=1) > 1e-9
        self.positions.append(corners.reshape(-1, 3))
        self.uvs.append(uvs.reshape(-1, 2))
        self.triangles.append(tris[keep].reshape(-1, 3))
        self.count += 4 * q

    def box(self, a: np.ndarray, b: np.ndarray, size: float):
        """A square bar from a to b (engine points)."""
        run = b - a
        length = float(np.linalg.norm(run))
        if length < 1e-6:
            return
        axis = run / length
        side = np.cross(axis, [0.0, 1.0, 0.0])
        side = side / np.linalg.norm(side) if np.linalg.norm(side) > 1e-6 else np.array([1.0, 0.0, 0.0])
        up = np.cross(side, axis)
        h = size / 2
        ring = [side * h + up * h, -side * h + up * h, -side * h - up * h, side * h - up * h]
        corners, uvs, facing = [], [], []
        for k in range(4):
            p, q = ring[k], ring[(k + 1) % 4]
            corners.append([a + p, b + p, b + q, a + q])
            uvs.append([[0, 0], [length, 0], [length, size], [0, size]])
            facing.append((p + q) / 2)
        for point, sign in ((a, -1.0), (b, 1.0)):
            corners.append([point + ring[0], point + ring[1], point + ring[2], point + ring[3]])
            uvs.append([[0, 0], [size, 0], [size, size], [0, size]])
            facing.append(axis * sign)
        self.quads(np.asarray(corners), np.asarray(uvs, float), np.asarray(facing))


def _flight(flight: dict, e0: float, n1: float, body: _Mesh, rails: _Mesh):
    """Treads, risers and cheeks of one flight into `body`, its handrails into `rails` (old stairs.produce)."""
    coords = np.asarray(flight["line"], float)
    xz = np.c_[coords[:, 0] - e0, n1 - coords[:, 1]]
    line = shapely.LineString(xz)
    width = float(flight["width"])
    knots = np.asarray(flight["stations"], float)
    levels = np.asarray(flight["heights"], float)
    knots[[0, -1]] = [0.0, line.length]
    bottom, top = levels[0], levels[-1]
    rise = top - bottom
    count = int(flight["count"])
    # Measured landings stay level; each rising stretch owns its share of the steps.
    levels = np.maximum.accumulate(np.clip(levels, bottom, top))
    counts = np.maximum(1, np.rint(np.diff(levels) / rise * count).astype(int))
    counts = np.maximum(counts, np.ceil(np.diff(levels) / MAX_RISE).astype(int))
    counts[np.diff(levels) <= 1e-6] = 1
    bends = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(xz, axis=0), axis=1))]
    bends[-1] = line.length
    distances = np.unique(np.r_[bends, *[np.linspace(lo, hi, n + 1) for lo, hi, n in
                                         zip(knots[:-1], knots[1:], counts, strict=True)]])
    # A tread edge and a mapped bend at the same station: keep the bend, never a zero-length tread.
    near = np.clip(np.searchsorted(bends, distances), 1, len(bends) - 1)
    nearest = np.where(np.abs(distances - bends[near - 1]) < np.abs(distances - bends[near]), near - 1, near)
    snap = np.abs(distances - bends[nearest]) < 1e-6
    distances[snap] = bends[nearest[snap]]
    distances = np.unique(distances)
    distances = distances[np.r_[True, np.diff(distances) > 1e-4]]
    if len(distances) < 2:
        return
    centres = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
    directions = np.diff(centres, axis=0)
    directions /= np.maximum(np.linalg.norm(directions, axis=1)[:, None], 1e-9)
    normals = np.c_[-directions[:, 1], directions[:, 0]]
    offsets = np.vstack([normals[0],
                         (normals[:-1] + normals[1:]) / np.maximum(1 + (normals[:-1] * normals[1:]).sum(axis=1), 0.1)[:, None],
                         normals[-1]]) * width / 2
    mid = (distances[:-1] + distances[1:]) / 2
    interval = np.clip(np.searchsorted(knots, mid, side="right") - 1, 0, len(counts) - 1)
    span = np.maximum(knots[interval + 1] - knots[interval], 1e-9)
    step = np.minimum(counts[interval], ((mid - knots[interval]) / span * counts[interval]).astype(int) + 1)
    high = levels[interval] + np.diff(levels)[interval] * step / counts[interval]
    low = np.r_[bottom, high[:-1]]
    left, right = centres + offsets, centres - offsets

    def at(points, heights):
        return np.c_[points[:, 0], heights, points[:, 1]]

    n = len(high)
    up = np.array([0.0, 1.0, 0.0])
    # Treads.
    tread = np.stack([at(left[:-1], high), at(left[1:], high), at(right[1:], high), at(right[:-1], high)], axis=1)
    body.quads(tread, tread[:, :, [0, 2]], up)
    # Risers, facing down the flight.
    riser = np.stack([at(right[:-1], low), at(left[:-1], low), at(left[:-1], high), at(right[:-1], high)], axis=1)
    face = np.c_[-directions[:, 0], np.zeros(n), -directions[:, 1]]
    width_u = np.linalg.norm(left[:-1] - right[:-1], axis=1)
    riser_uv = np.stack([np.c_[np.zeros(n), low], np.c_[width_u, low], np.c_[width_u, high], np.c_[np.zeros(n), high]], axis=1)
    body.quads(riser, riser_uv, face)
    # Cheeks: from the treads down into the ground beside them.
    ground = flight["ground"]
    along = np.asarray(ground["along"], float)
    for side, heights, sign in ((left, ground["left"], 1.0), (right, ground["right"], -1.0)):
        g = np.interp(distances, along, np.asarray(heights, float))
        g0, g1 = np.minimum(g[:-1], low) - CHEEK_ROOT, np.minimum(g[1:], low) - CHEEK_ROOT
        cheek = np.stack([at(side[:-1], g0), at(side[1:], g1), at(side[1:], high), at(side[:-1], high)], axis=1)
        outward = np.c_[normals[:, 0] * sign, np.zeros(n), normals[:, 1] * sign]
        cheek_uv = np.stack([np.c_[distances[:-1], g0], np.c_[distances[1:], g1], np.c_[distances[1:], high],
                             np.c_[distances[:-1], high]], axis=1)
        body.quads(cheek, cheek_uv, outward)
    # The two ends close the solid down to the ground.
    for end, outward in ((0, -1.0), (-1, 1.0)):
        k = 0 if end == 0 else n - 1
        d = directions[k] * outward
        g = min(np.interp(distances[end], along, ground["left"]), np.interp(distances[end], along, ground["right"]))
        h = high[k] if end == -1 else low[0]
        if h - g > 0.02:
            corners = np.array([[at(left[[end]], [g - CHEEK_ROOT])[0], at(right[[end]], [g - CHEEK_ROOT])[0],
                                 at(right[[end]], [h])[0], at(left[[end]], [h])[0]]])
            body.quads(corners, np.array([[[0, g], [width, g], [width, h], [0, h]]]), np.array([d[0], 0.0, d[1]]))
    # Handrails over the rising stretches: posts and a rail parallel to the slope.
    rising = np.flatnonzero(np.diff(levels) > 0.045)
    if not len(rising):
        return
    start, stop = knots[rising[0]], knots[rising[-1] + 1]
    for sign, wanted in ((1.0, flight["rails"][0]), (-1.0, flight["rails"][1])):
        if not wanted:
            continue
        # The rail follows the profile's slope: it bends only at its knots and at the mapped bends.
        inner = np.r_[knots, bends]
        stations = np.unique(np.r_[start, stop, inner[(inner > start) & (inner < stop)]])
        def rail_at(where, sign=sign):
            centre = shapely.get_coordinates(shapely.line_interpolate_point(line, where))
            piece = np.clip(np.searchsorted(distances, where, side="right") - 1, 0, n - 1)
            inset = offsets[piece] / (width / 2) * (width / 2 - RAIL_INSET) * sign
            return at(centre + inset, np.interp(where, knots, levels) + RAIL_HEIGHT), piece

        rail, _ = rail_at(stations)
        for a, b in zip(rail[:-1], rail[1:], strict=True):
            rails.box(a, b, RAIL_SIZE)
        count = max(1, int(np.ceil((stop - start) / RAIL_POST_SPACING)))
        tops, pieces = rail_at(np.linspace(start, stop, count + 1))
        for top, piece in zip(tops, pieces, strict=True):
            rails.box(np.array([top[0], high[piece], top[2]]), top, RAIL_SIZE)
