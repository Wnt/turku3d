"""Low-sided tapered spars and open standing rigging on measured sailing vessels."""

import numpy as np
import shapely

from turku3d.structures.geometry import Faces

from .bridge_details import appearance, tube
from .harbour import axis


def stock(key, a, b, sections, surface):
    """Continuous tapered round stock; section fractions preserve measured endpoints."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    delta = b - a
    length = np.linalg.norm(delta)
    direction = delta / length
    normal = np.cross(direction, [0, 1, 0] if abs(direction[1]) < .9 else [1, 0, 0])
    normal /= np.linalg.norm(normal)
    other = np.cross(direction, normal)
    angles = np.arange(8) * np.pi / 4
    radial = np.cos(angles)[:, None] * normal + np.sin(angles)[:, None] * other
    rings = [a + fraction * delta + radius * radial for fraction, radius in sections]
    faces = Faces()
    circumference = 2 * np.pi * max(radius for _, radius in sections)
    for (lo, _), (hi, _), lower, upper in zip(sections[:-1], sections[1:], rings[:-1], rings[1:], strict=True):
        for i in range(8):
            j = (i + 1) % 8
            faces.face([lower[i], lower[j], upper[j], upper[i]],
                       [[i*circumference/8, lo*length], [(i+1)*circumference/8, lo*length],
                        [(i+1)*circumference/8, hi*length], [i*circumference/8, hi*length]])
    faces.face(rings[0][::-1])
    faces.face(rings[-1])
    return appearance(faces.mesh(key, "railing"), surface)


def anchor(outline, centre, direction, normal, distance, side, beam):
    """Land stays on the actual deck edge, including narrowing bows and sterns."""
    station = centre + direction * distance
    cross = shapely.LineString([station-normal*beam, station+normal*beam]).intersection(outline)
    lines = [g for g in shapely.get_parts(cross) if g.geom_type == "LineString" and g.length > .4]
    if not lines:
        return None
    line = min(lines, key=lambda g: g.distance(shapely.Point(station)))
    offsets = (np.asarray(line.coords) - station) @ normal
    extent = offsets.max() if side > 0 else offsets.min()
    return station + normal * (extent - side*.2)


def produce(key, boat):
    outline, deck = boat["outline"], boat["deck"]
    tall = [(p, h) for p, h in boat["masts"] if h > deck + 10]
    sailing = (len(tall) >= 3 or boat["tags"].get("building:material") == "wood"
               or boat["tags"].get("ship:type") in ("sailing", "sailboat", "tall_ship"))
    if len(tall) < 2 or not sailing:
        return [tube(key, [p.x, deck, p.y], [p.x, top, p.y], .055, "white")
                for p, top in boat["masts"]]

    direction = axis(outline)
    normal = np.array([-direction[1], direction[0]])
    corners = np.asarray(outline.minimum_rotated_rectangle.exterior.coords)[:-1]
    beam = float(np.ptp(corners @ normal))
    parts = []
    for point, top in boat["masts"]:
        height = top-deck
        radius = float(np.clip(height*.0045, .10, .24))
        parts.append(stock(key, [point.x, deck, point.y], [point.x, top, point.y],
                           [(0, radius), (.48, radius*.67), (.78, radius*.35), (1, .035)],
                           "wood" if boat["tags"].get("building:material") == "wood" else "white"))
    # Only mast bodies collide. Spars, stays and ratlines remain open visual detail.
    detail_start = len(parts)
    for point, top in tall:
        xy = np.asarray(point.coords[0])
        height = top-deck
        for fraction in (.48, .65, .8, .92):
            y = deck + height*fraction
            half = beam * (1.15-fraction*.65)
            a, b = xy-normal*half, xy+normal*half
            centre_radius = float(np.clip(half*.012, .065, .16))
            parts.append(stock(key, [a[0], y, a[1]], [b[0], y, b[1]],
                               [(0, .025), (.5, centre_radius), (1, .025)], "wood"))
        for sign in (-1, 1):
            for side in (-1, 1):
                foot = anchor(outline, xy, direction, normal, sign*min(12, height*.45), side, beam)
                if foot is not None:
                    parts.append(tube(key, [point.x, top, point.y], [foot[0], deck+.5, foot[1]], .018, "hull"))
        # A lower-mast fan and its climbing ropes give standing rigging its broad
        # base instead of the former isolated full-height triangular hairlines.
        shoulder = np.array([point.x, deck+height*.48, point.y])
        for side in (-1, 1):
            feet = [anchor(outline, xy, direction, normal, distance, side, beam)
                    for distance in (-2.4, 0, 2.4)]
            feet = [np.array([p[0], deck+.4, p[1]]) for p in feet if p is not None]
            for foot in feet:
                parts.append(tube(key, foot, shoulder, .018, "hull"))
            if len(feet) >= 2:
                for y in np.arange(deck+1, shoulder[1]-.7, .8):
                    t = (y-deck-.4)/(shoulder[1]-deck-.4)
                    a, b = feet[0]*(1-t)+shoulder*t, feet[-1]*(1-t)+shoulder*t
                    parts.append(tube(key, a, b, .01, "hull"))
    for part in parts[detail_start:]:
        part.metadata["collision"] = False
    return parts
