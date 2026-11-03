"""Measured ship parts: sheer hull with raked stem, tiers, funnels, lifeboats and livery.

Every size and position comes from the vessel's lidar survey (``harbour.survey_ship``);
colours come from reviewed livery observations and otherwise keep the palette defaults.
"""

import numpy as np
import shapely

from turku3d.structures.geometry import Faces

from .bridge_details import appearance, tube


class Frame:
    """Hull-local along/across coordinates and the measured sheer line."""

    def __init__(self, boat):
        from .harbour import axis

        outline = boat["outline"]
        self.direction = axis(outline)
        self.normal = np.array([-self.direction[1], self.direction[0]])
        self.centre = np.asarray(outline.centroid.coords[0])
        self.deck = boat["deck"]
        sheer = boat.get("sheer") or []
        self.stations = np.array([u for u, _ in sheer]) if sheer else None
        self.heights = np.array([h for _, h in sheer]) if sheer else None
        self.bow = boat.get("bow", 1)
        self.rake = boat.get("rake", 0.0)
        ring = self.local(np.asarray(outline.exterior.coords)[:, :2])
        self.tip = float((ring[:, 0] * self.bow).max())
        self.length = float(np.ptp(ring[:, 0]))
        if not self.rake:
            # Airborne returns rarely resolve a stem; a reviewed side view gives its slope.
            crest = float(self.top(self.world(np.array([self.bow * self.tip, 0.]))))
            freeboard = crest - boat.get("water", 0.2) + 0.7
            self.rake = float(boat.get("appearance", {}).get("stem_rake", 0.0)) * freeboard

    def local(self, xy):
        return (np.asarray(xy, float) - self.centre) @ np.array([self.direction, self.normal]).T

    def world(self, uv):
        uv = np.asarray(uv, float)
        return self.centre + uv[..., :1] * self.direction + uv[..., 1:2] * self.normal

    def top(self, xy):
        """Hull top (sheer) height at world xy."""
        if self.stations is None:
            return np.full(np.shape(xy)[:-1], self.deck) if np.ndim(xy) > 1 else self.deck
        u = self.local(xy)[..., 0]
        return np.interp(u, self.stations, self.heights)


def densify(ring, step):
    out = []
    for a, b in zip(ring[:-1], ring[1:], strict=True):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.extend(a + (b - a) * t for t in np.arange(n) / n)
    return np.asarray(out)


def hull(key, boat, frame, livery):
    """Flared hull skin from keel to the sheer line; the stem rakes aft below the deck."""
    outline = shapely.orient_polygons(boat["outline"])
    ring = densify(np.asarray(outline.exterior.coords)[:, :2], 3.0)
    uv = frame.local(ring)
    water = boat["water"]
    bottom = water - 0.7
    tops = frame.top(ring)
    boot = livery.get("boot_height", 0.14)
    reach = max(2 * frame.rake, 0.12 * frame.length, 1e-3)
    fore = np.clip((uv[:, 0] * frame.bow - (frame.tip - reach)) / reach, 0, 1)

    def at(y):
        f = np.clip((y - bottom) / np.maximum(tops - bottom, 0.1), 0, 1)
        u = uv[:, 0] - frame.bow * frame.rake * (1 - f) * fore
        v = uv[:, 1] * (0.88 + 0.12 * f)
        xy = frame.world(np.c_[u, v])
        return np.c_[xy[:, 0], np.broadcast_to(y, len(xy)), xy[:, 1]]

    levels = [np.full(len(ring), bottom), np.full(len(ring), water + boot)]
    levels += [bottom + (tops - bottom) * f for f in (0.55, 0.8, 1.0)]
    rings = [at(y) for y in levels]
    painted = boot > 0.3
    parts = []
    for k in range(len(rings) - 1):
        surface = livery.get("boot_surface", "waterline") if k == 0 and painted else livery.get("hull_surface", "hull")
        parts.append(appearance(skin(key, rings[k], rings[k + 1], "wall"), surface))
    if not painted:
        # Thin painted waterline on the skin; no horizontal slab.
        lo, hi = at(np.full(len(ring), water + 0.05)), at(np.full(len(ring), water + boot))
        for q in (lo, hi):
            q[:, [0, 2]] += (q[:, [0, 2]] - frame.centre) * 0.001
        parts.append(appearance(skin(key, lo, hi, "wall"), "waterline", collision=False))
    return parts


def skin(key, lo, hi, material):
    faces = Faces()
    for i in range(len(lo)):
        j = (i + 1) % len(lo)
        faces.face([lo[i], hi[i], hi[j], lo[j]])
    return faces.mesh(key, material)


def oriented(points, normal):
    """Wind a planar polygon so it faces ``normal``."""
    q = np.asarray(points, float)
    n = np.cross(q[1] - q[0], q[2] - q[0])
    return q if n @ np.asarray(normal, float) >= 0 else q[::-1]


def deck(key, boat, frame):
    """Walkable deck following the sheer line, triangulated in short stations."""
    import mapbox_earcut

    region = boat["outline"].buffer(-0.08)
    faces = Faces()
    u = frame.local(np.asarray(region.exterior.coords)[:, :2])[:, 0] if region.geom_type == "Polygon" else [0]
    lo, hi = float(np.min(u)) - 1, float(np.max(u)) + 1
    for s in np.arange(lo, hi, 3.0):
        strip = shapely.Polygon(frame.world(np.array([[s, -60], [s + 3, -60], [s + 3, 60], [s, 60]])))
        for polygon in shapely.get_parts(region.intersection(strip)):
            if polygon.geom_type != "Polygon" or polygon.area < 1e-6:
                continue
            rings = [np.asarray(r.coords)[:-1] for r in [polygon.exterior, *polygon.interiors]]
            xz = np.concatenate(rings)
            tris = mapbox_earcut.triangulate_float64(
                xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)).reshape(-1, 3)
            xyz = np.c_[xz[:, 0], frame.top(xz) + 0.015, xz[:, 1]]
            for tri in tris:
                q = xyz[tri]
                if np.cross(q[1] - q[0], q[2] - q[0])[1] < 0:
                    q = q[::-1]
                faces.face(q, q[:, [0, 2]])
    return [appearance(faces.mesh(key, "bridge_deck"), "deck")]


def portholes(key, boat, frame, livery):
    """Rows of round portholes along the straight hull sides below the sheer line."""
    rows = livery.get("porthole_rows") or []
    if not rows:
        return []
    spacing = livery.get("porthole_spacing", 1.6)
    radius = livery.get("porthole_radius", 0.2)
    water = boat["water"]
    ring = shapely.orient_polygons(boat["outline"]).exterior
    faces = Faces()
    for a, b in zip(ring.coords[:-1], ring.coords[1:], strict=True):
        a, b = np.asarray(a), np.asarray(b)
        length = np.linalg.norm(b - a)
        if length < spacing or abs((b - a) @ frame.direction) / length < 0.94:
            continue
        along = (b - a) / length
        out = np.array([along[1], -along[0]])
        for t in np.arange(spacing / 2, length - spacing / 2, spacing):
            xy = a + along * t
            if frame.local(xy)[0] * frame.bow > frame.tip - 6 or frame.local(xy)[0] * frame.bow < -frame.length / 2 + 4:
                continue
            u, v = frame.local(xy)
            sheer = float(frame.top(xy))
            for depth in rows:
                y = sheer - depth
                if y < water + livery.get("boot_height", 0.14) + 0.5:
                    continue
                # The skin flares in below the sheer line; sit on it, just proud.
                f = (y - water + 0.7) / max(sheer - water + 0.7, 1)
                centre = frame.world(np.array([u, v * (0.88 + 0.12 * f)])) + out * 0.03
                angles = np.arange(8) * np.pi / 4
                pts = [[*(centre + along * radius * np.cos(t))[:1], y + radius * np.sin(t),
                        (centre + along * radius * np.cos(t))[1]] for t in angles]
                faces.face(oriented(pts, [out[0], 0, out[1]]))
    if not faces.indices:
        return []
    return [appearance(faces.mesh(key, "building"), "glass", collision=False)]


def tiers(boat, frame):
    """Stack measured roof terraces: each tier rises from the tier below it, not the hull."""
    cabins = sorted(boat["cabins"], key=lambda item: item[1])
    out = []
    for i, (shape, top) in enumerate(cabins):
        for polygon in shapely.get_parts(shape):
            if polygon.geom_type != "Polygon":
                continue
            ring = np.asarray(polygon.exterior.coords)[:, :2]
            floor = float(np.max(frame.top(ring)))
            if top <= floor + 1.2:
                continue  # Inside the hull's forecastle or poop.
            base = floor
            for other, below in cabins[:i]:
                if below < top - 1.0 and below > base and other.intersection(polygon).area >= 0.6 * polygon.area:
                    base = below
            out.append((polygon, base, top))
    return out


def ellipse(centre, direction, normal, length, width, n=16):
    t = np.arange(n) * 2 * np.pi / n
    return centre + np.cos(t)[:, None] * direction * length / 2 + np.sin(t)[:, None] * normal * width / 2


def funnels(key, boat, frame, livery):
    """Oval funnels at the surveyed size with livery bands counted from the top."""
    bands = livery.get("funnel_bands") or [[1.0, livery.get("funnel_surface", "hull")]]
    parts = []
    for f in boat.get("funnels", []):
        centre = np.asarray(f["centre"])
        ring = ellipse(centre, frame.direction, frame.normal, f["length"] * 0.92, f["width"] * 0.92)
        base, top = f["base"] - 0.3, f["top"]
        height = top - f["base"]
        previous = top
        for fraction, surface in bands:
            lo = max(base, top - fraction * height) if fraction < 1 else base
            faces = Faces()
            for i in range(len(ring)):
                j = (i + 1) % len(ring)
                faces.face([[*ring[i][:1], lo, ring[i][1]], [*ring[i][:1], previous, ring[i][1]],
                            [*ring[j][:1], previous, ring[j][1]], [*ring[j][:1], lo, ring[j][1]]])
            parts.append(appearance(faces.mesh(key, "building"), surface))
            previous = lo
        cap = Faces()
        inner = centre + (ring - centre) * 0.85
        cap.face(oriented([[x, top - 0.4, z] for x, z in inner], [0, 1, 0]))
        parts.append(appearance(cap.mesh(key, "building"), "hull", collision=False))
        if cross := livery.get("funnel_cross"):
            # A vertical company stripe across the coloured bands on both broad sides.
            lo_f, hi_f, surface = cross
            for side in (-1, 1):
                p = centre + frame.normal * side * (f["width"] * 0.46 + 0.03)
                half = frame.direction * min(0.45, f["length"] * 0.07)
                y0, y1 = top - hi_f * height, top - lo_f * height
                faces = Faces()
                quad = [[*(p - half)[:1], y0, (p - half)[1]], [*(p - half)[:1], y1, (p - half)[1]],
                        [*(p + half)[:1], y1, (p + half)[1]], [*(p + half)[:1], y0, (p + half)[1]]]
                faces.face(oriented(quad, [*(frame.normal * side)[:1], 0, (frame.normal * side)[1]]))
                parts.append(appearance(faces.mesh(key, "building"), surface, collision=False))
    return parts


def lifeboats(key, boat, frame, livery):
    """Surveyed boats hang on twin davits at the deck edge; hull plus closed canopy."""
    surface = livery.get("lifeboat_surface", "white")
    parts = []
    for b in boat.get("lifeboats", []):
        centre = np.asarray(b["centre"])
        side = np.sign(b["side"]) or 1
        keel = b["base"] + 0.45
        gunwale = keel + 0.8
        canopy = max(gunwale + 0.5, min(b["top"], gunwale + 1.0))
        t = np.linspace(0, 2 * np.pi, 13)[:-1]
        # A pointed double-ended plan: narrower ends than an ellipse.
        shape = np.c_[np.cos(t) * b["length"] / 2, np.sin(t) * b["width"] / 2 * (0.55 + 0.45 * np.abs(np.sin(t)))]
        plan = centre + shape[:, :1] * frame.direction + shape[:, 1:] * frame.normal
        for lo, hi, s_lo, s_hi, paint in ((keel, gunwale, 0.55, 1.0, surface),
                                          (gunwale, canopy, 1.0, 0.7, surface)):
            faces = Faces()
            a = centre + (plan - centre) * s_lo
            c = centre + (plan - centre) * s_hi
            for i in range(len(plan)):
                j = (i + 1) % len(plan)
                faces.face([[a[i][0], lo, a[i][1]], [c[i][0], hi, c[i][1]], [c[j][0], hi, c[j][1]], [a[j][0], lo, a[j][1]]])
            if hi == canopy:
                faces.face(oriented([[x, canopy, z] for x, z in (centre + (plan - centre) * 0.7)], [0, 1, 0]))
            parts.append(appearance(faces.mesh(key, "wall"), paint, collision=False))
        inboard = centre - frame.normal * side * (b["width"] / 2 + 0.6)
        for end in (-0.32, 0.32):
            foot = inboard + frame.direction * b["length"] * end
            head = centre + frame.direction * b["length"] * end
            for a, c in (([foot[0], b["base"], foot[1]], [foot[0], canopy + 0.6, foot[1]]),
                         ([foot[0], canopy + 0.6, foot[1]], [head[0], canopy + 0.5, head[1]]),
                         ([head[0], canopy + 0.5, head[1]], [head[0], canopy, head[1]])):
                part = tube(key, a, c, 0.06, "white")
                part.metadata["collision"] = False
                parts.append(part)
    return parts
