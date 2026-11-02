"""Low moored hulls, open decks, measured cabins and thin nautical furniture."""

from functools import cache
from io import BytesIO

import numpy as np
import shapely
from PIL import Image

from turku3d.package.gltf import GlbMesh
from turku3d.structures.geometry import Faces, profile_wall

from .bridge_details import appearance, combine, tube
from .gardens import horizontal
from .geometry import clip_mesh

COLOURS = {
    "hull": (31, 38, 42),
    "waterline": (76, 62, 48),
    "deck": (115, 110, 99),
    "wood": (126, 84, 48),
    "blue": (30, 64, 108),
    "white": (216, 217, 205),
    "glass": (33, 51, 58),
    "canvas": (225, 225, 210),
}


@cache
def texture(surface):
    """Small metric steel grain, timber planks and canvas; no house facade atlas."""
    size = 128
    rng = np.random.default_rng(590 + list(COLOURS).index(surface))
    noise = rng.normal(0, 2 if surface in ("glass", "blue", "white") else 5, (size, size))
    if surface == "wood":
        noise += np.sin(np.arange(size)[None, :] * 0.7) * 5
        noise[:, ::32] -= 18
    if surface == "deck":
        noise[::32] -= 15
    pixels = np.clip(np.array(COLOURS[surface]) + noise[:, :, None], 0, 255).astype(np.uint8)
    out = BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def extrusion(key, polygon, low, high, surface, material="wall", *, bottom_scale=1):
    faces = Faces()
    polygon = shapely.orient_polygons(polygon)
    centre = np.asarray(polygon.centroid.coords[0])
    ring = np.asarray(polygon.exterior.coords)[:-1]
    lower = centre + (ring - centre) * bottom_scale
    for i in range(len(ring)):
        j = (i + 1) % len(ring)
        # Exterior faces look out; lower ring gives the hull its slight flare.
        faces.face(
            [
                [lower[i, 0], low, lower[i, 1]],
                [ring[i, 0], high, ring[i, 1]],
                [ring[j, 0], high, ring[j, 1]],
                [lower[j, 0], low, lower[j, 1]],
            ]
        )
    horizontal(faces, polygon, high)
    return appearance(faces.mesh(key, material), surface)


def rails(key, polygon, height, *, surface="white"):
    parts = []
    for ring in [polygon.exterior, *polygon.interiors]:
        line = shapely.LineString(ring.coords).simplify(0.12)
        coords = np.asarray(line.coords)
        for a, b in zip(coords[:-1], coords[1:], strict=True):
            if np.linalg.norm(b - a) < 0.1:
                continue
            for lift in (0.35, 0.75, 1.05):
                part = tube(
                    key, [a[0], height + lift, a[1]], [b[0], height + lift, b[1]], 0.018, surface
                )
                part.metadata["collision"] = False
                parts.append(part)
        for distance in np.arange(0, line.length, 1.8):
            p = line.interpolate(float(distance))
            part = tube(key, [p.x, height, p.y], [p.x, height + 1.05, p.y], 0.025, surface)
            part.metadata["collision"] = False
            parts.append(part)
        # A thin continuous collision rail avoids thousands of tiny tube faces.
        # Full visuals retain open bars; first-pass collision remains walk-safe.
        for a, b in zip(coords[:-1], coords[1:], strict=True):
            if np.linalg.norm(b - a) < 0.1:
                continue
            proxy = profile_wall(
                key,
                [a, b],
                [height, height],
                [height + 1.05] * 2,
                thickness=0.06,
                material="railing",
            )
            proxy.visual = False
            parts.append(appearance(proxy, surface))
    return parts


def cabin(key, polygon, deck, top, surface="wood", fascia="blue"):
    """Measured roof outline; compact boat windows and blue horizontal fascia."""
    polygon = shapely.orient_polygons(polygon)
    parts = [extrusion(key, polygon, deck, top, surface, "building")]
    levels = max(1, min(2, round((top - deck) / 2.4)))
    story = (top - deck) / levels
    coords = np.asarray(polygon.exterior.coords)
    for a, b in zip(coords[:-1], coords[1:], strict=True):
        delta = b - a
        length = np.linalg.norm(delta)
        if length < 0.2:
            continue
        direction = delta / length
        outward = np.array([direction[1], -direction[0]]) * 0.012
        for floor in range(levels):
            y = deck + floor * story
            for lo, hi, band_surface in (
                (y + 0.04, y + 0.25, fascia),
                (y + story - 0.18, y + story, "white"),
            ):
                faces = Faces()
                p, q = a + outward, b + outward
                faces.face([[p[0], lo, p[1]], [p[0], hi, p[1]], [q[0], hi, q[1]], [q[0], lo, q[1]]])
                parts.append(appearance(faces.mesh(key, "building"), band_surface, collision=False))
            count = max(1, int(length / 1.8))
            margin = min(0.3, length * 0.1)
            for bay in range(count):
                lo = margin + (length - 2 * margin) * bay / count + 0.08
                hi = margin + (length - 2 * margin) * (bay + 1) / count - 0.08
                if hi <= lo:
                    continue
                p, q = a + direction * lo + outward, a + direction * hi + outward
                low = y + min(0.7, story * 0.3)
                high = min(y + story - 0.3, low + 1.0)
                if high <= low:
                    continue
                frame = Faces()
                glass = Faces()
                frame.face(
                    [
                        [p[0], low - 0.06, p[1]],
                        [p[0], high + 0.06, p[1]],
                        [q[0], high + 0.06, q[1]],
                        [q[0], low - 0.06, q[1]],
                    ]
                )
                p, q = p + outward, q + outward
                glass.face(
                    [[p[0], low, p[1]], [p[0], high, p[1]], [q[0], high, q[1]], [q[0], low, q[1]]]
                )
                parts.extend(
                    [
                        appearance(frame.mesh(key, "building"), "white", collision=False),
                        appearance(glass.mesh(key, "building"), "glass", collision=False),
                    ]
                )
    roof = Faces()
    horizontal(roof, polygon, top + 0.01)
    parts.append(appearance(roof.mesh(key, "building"), "white" if surface == "white" else "blue", collision=False))
    parts.extend(rails(key, polygon, top))
    return parts


def furniture(key, boat, deck_area):
    """Restaurant tables and small open white parasols remain clear of measured cabins."""
    parts = []
    outline = boat["outline"]
    deck = boat["deck"]
    rectangle = np.asarray(outline.minimum_rotated_rectangle.exterior.coords)[:-1]
    sides = np.roll(rectangle, -1, axis=0) - rectangle
    direction = sides[np.argmax(np.linalg.norm(sides, axis=1))]
    direction /= np.linalg.norm(direction)
    centre = np.asarray(outline.centroid.coords[0])
    length = np.ptp((rectangle - centre) @ direction)
    for distance in np.arange(-length / 2 + 4, length / 2 - 3, 4.5):
        p = centre + direction * distance
        if not deck_area.buffer(-1.3).covers(shapely.Point(p)):
            continue
        top = Faces()
        horizontal(top, shapely.Point(p).buffer(0.55, quad_segs=4), deck + 0.75)
        parts.append(appearance(top.mesh(key, "wall"), "wood", collision=False))
        normal = np.array([-direction[1], direction[0]])
        chairs = Faces()
        for sign in (-1, 1):
            chair_centre = p + normal * sign * 0.95
            corners = np.array(
                [
                    chair_centre + direction * a + normal * b
                    for a, b in ((-0.25, -0.25), (0.25, -0.25), (0.25, 0.25), (-0.25, 0.25))
                ]
            )
            horizontal(chairs, shapely.Polygon(corners), deck + 0.45)
            a, b = (
                chair_centre + normal * sign * 0.25 - direction * 0.25,
                chair_centre + normal * sign * 0.25 + direction * 0.25,
            )
            chairs.face(
                [
                    [a[0], deck + 0.45, a[1]],
                    [a[0], deck + 1.0, a[1]],
                    [b[0], deck + 1.0, b[1]],
                    [b[0], deck + 0.45, b[1]],
                ]
            )
        parts.append(appearance(chairs.mesh(key, "wall"), "white", collision=False))
        parts.append(tube(key, [p[0], deck, p[1]], [p[0], deck + 2.8, p[1]], 0.025, "white"))
        canopy = Faces()
        ring = np.asarray(
            shapely.orient_polygons(shapely.Point(p).buffer(1.5, quad_segs=2)).exterior.coords
        )
        for a, b in zip(ring[:-1], ring[1:], strict=True):
            canopy.face(
                [[a[0], deck + 2.5, a[1]], [p[0], deck + 2.9, p[1]], [b[0], deck + 2.5, b[1]]]
            )
        parts.append(appearance(canopy.mesh(key, "wall"), "canvas", collision=False))
    return parts


def produce(tile, records):
    meshes = []
    for boat in records:
        outline = boat["outline"]
        if not outline.intersects(shapely.box(*tile.bbox.as_tuple())):
            continue
        if boat["kind"] == "pier":
            # Mapped floating docks are low pontoons, not ship cabins and restaurant decks.
            key = "pier:" + boat["id"]
            water, deck = boat["water"], boat["deck"]
            parts = [extrusion(key, outline, water - 0.3, deck - 0.16, "hull"),
                     extrusion(key, outline, deck - 0.16, deck, "wood", "bridge_deck")]
            for part in combine(parts, key):
                clipped = clip_mesh(part, tile.bbox)
                if clipped is not None:
                    meshes.append(clipped)
            continue
        key = "boat:" + boat["id"]
        deck = boat["deck"]
        water = boat["water"]
        parts = [extrusion(key, outline, water - 0.7, deck, "hull", bottom_scale=0.88)]
        deck_faces = Faces()
        horizontal(deck_faces, outline.buffer(-0.08), deck + 0.015)
        parts.append(appearance(deck_faces.mesh(key, "bridge_deck"), "deck"))
        # Paint follows the flared hull skin; no horizontal waterline slab.
        band = Faces()
        ring = np.asarray(shapely.orient_polygons(outline).exterior.coords)
        centre = np.asarray(outline.centroid.coords[0])
        low, high = water + 0.05, water + 0.14

        scale = 0.88 + 0.12 * (np.array([low, high]) - (water - 0.7)) / (deck - (water - 0.7))
        lo, hi = centre + (ring - centre) * (scale[:, None, None] + 0.001)
        for i in range(len(ring) - 1):
            band.face(
                [
                    [lo[i, 0], low, lo[i, 1]],
                    [hi[i, 0], high, hi[i, 1]],
                    [hi[i + 1, 0], high, hi[i + 1, 1]],
                    [lo[i + 1, 0], low, lo[i + 1, 1]],
                ]
            )
        parts.append(appearance(band.mesh(key, "wall"), "waterline", collision=False))
        cabins = shapely.union_all([g for g, _ in boat["cabins"]])
        for shape, top in boat["cabins"]:
            for polygon in shapely.get_parts(shape):
                if polygon.geom_type == "Polygon":
                    parts.extend(cabin(key, polygon, deck, top, boat["appearance"]["cabin_surface"],
                                       boat["appearance"].get("fascia_surface", "blue")))
        # Separate blue bulwark and slim white bars around the open working deck.
        edge = outline.buffer(-0.12)
        parts.extend(rails(key, edge, deck, surface=boat["appearance"].get("rail_surface", "blue")))
        for point, top in boat["masts"]:
            parts.append(
                tube(key, [point.x, deck, point.y], [point.x, top, point.y], 0.055, "white")
            )
        parts.extend(furniture(key, boat, outline.difference(cabins.buffer(0.6))))
        for part in combine(parts, key):
            clipped = clip_mesh(part, tile.bbox)
            if clipped is not None:
                meshes.append(clipped)
    return meshes


def visuals(tile, meshes):
    return [
        GlbMesh(
            m.object_id,
            m.positions - tile.origin,
            m.indices,
            uvs=m.uvs,
            image=texture(m.metadata["surface"]),
            repeat=True,
            color=(1, 1, 1, 1),
            roughness=0.25 if m.metadata["surface"] == "glass" else 0.8,
            extras=dict(
                layer="structures" if m.object_id.startswith("pier:") else "boats",
                object_id=m.object_id,
                material_key=("pier_" if m.object_id.startswith("pier:") else "boat_") + m.metadata["surface"]
            ),
        )
        for m in meshes
        if m.visual
    ]


def stats(tile, records, meshes):
    return dict(
        boats=[
            dict(
                id=b["id"],
                name=b["tags"].get("name"),
                water_m=b["water"],
                deck_m=b["deck"],
                cabin_parts=len(b["cabins"]),
                masts=len(b["masts"]),
                source_buildings=b["building_keys"],
                source_facades=b["source_facades"],
                appearance=b["appearance"],
            )
            for b in records
            if b["owner"] == tile.id and b["kind"] == "ship"
        ],
        piers=[dict(id=b["id"], deck_m=b["deck"], water_m=b["water"])
               for b in records if b["owner"] == tile.id and b["kind"] == "pier"],
        triangles=sum(len(m.indices) for m in meshes),
    )


def stage(cache, tile, records):
    from pathlib import Path

    from turku3d import config as C
    from turku3d.stage_cache import source_key

    return cache.run(
        "boats",
        source_key(
            Path(__file__),
            Path(__file__).with_name("geometry.py"),
            Path(__file__).with_name("bridge_details.py"),
            Path(__file__).with_name("gardens.py"),
            Path(C.__file__),
            Path(__file__).parents[1] / "structures/geometry.py",
        ),
        [tile, records],
        lambda: produce(tile, records),
    )
