"""Evidence-selected architectural emitters fitted to mapped geometry, never coordinates."""
import numpy as np
import shapely

from turku3d.night_lighting import rules
from turku3d.bridges.build import _box
from .bridge_details import appearance, combine, wall


def bridge_profile(way):
    if way.get('landing') or way['tags'].get('lit') == 'no':
        return {}
    return rules()['bridges'].get(way['tags'].get('wikidata'), {})


def bridge_strips(way, rail_lines, height_at, rail_height):
    """One merged thin diffuser band below the mapped handrails; no collision."""
    if not bridge_profile(way).get('rail'):
        return []
    from .bridges import samples
    parts = []
    key = f"draft-bridge:{way['id']}:night"
    for line in shapely.get_parts(rail_lines):
        if line.geom_type != 'LineString' or line.length < .2:
            continue
        _, points = samples(line, 2)
        y = height_at(points) + rail_height - .04
        m = wall(key, points, y - .025, y, .035, 'night_rail', material='railing')
        m.metadata['collision'] = False
        parts.append(m)
    return combine(parts, key)


def dress_lights(key, boat, frame):
    """Dressed ships: stays from the stem over the mast heads to the stern carry bulbs.

    Only a reviewed livery observation (``dress_lights``) enables them; the stays are the
    wires the survey sees between mast tops and the hull ends.
    """
    if not boat.get('appearance', {}).get('dress_lights') or not boat['masts']:
        return []
    from .bridge_details import tube
    settings = rules()["boats"]
    direction = frame.direction
    masts = sorted(boat['masts'], key=lambda m: np.asarray(m[0].coords[0]) @ direction)
    ends = []
    for u in (frame.bow * (frame.tip - 1.0), -frame.bow * (frame.length - frame.tip - 1.5)):
        xy = frame.world(np.array([u, 0.]))
        ends.append(np.array([xy[0], float(frame.top(xy)) + 1.2, xy[1]]))
    ends.sort(key=lambda e: e[[0, 2]] @ direction)
    chain = [ends[0], *[np.array([m.x, h - .3, m.y]) for m, h in masts], ends[1]]
    total = sum(np.linalg.norm(b - a) for a, b in zip(chain[:-1], chain[1:], strict=True))
    spacing = max(settings['bulbSpacing'], total / settings['maxBulbs'])
    parts = []
    for a, b in zip(chain[:-1], chain[1:], strict=True):
        length = np.linalg.norm(b - a)
        sag = min(length * .04, 1.2)
        points = [a * (1 - t) + b * t - np.array([0, sag * 4 * t * (1 - t), 0])
                  for t in np.linspace(0, 1, max(2, int(length / spacing) + 1))]
        for p, q in zip(points[:-1], points[1:], strict=True):
            wire = tube(key, p, q, .012, 'hull', sides=4)
            wire.metadata['collision'] = False
            parts.append(wire)
        for p in points[1:-1]:
            parts.append(appearance(_box(key, p - np.array([0, .05, 0]), (.045, .06, .045), 'wall'),
                                    'night_bulb', collision=False))
    return parts


def restaurant_bulbs(key, boat):
    """Sparse dotted strings between existing masts, with bounded sag and count."""
    if boat['tags'].get('amenity') not in ('restaurant', 'bar', 'cafe', 'pub', 'nightclub'):
        return []
    from .harbour import axis
    settings = rules()["boats"]
    direction = axis(boat['outline'])
    masts = sorted(boat['masts'], key=lambda m: np.asarray(m[0].coords[0]) @ direction)
    parts = []
    for (a, ah), (b, bh) in zip(masts[:-1], masts[1:]):
        start, end = np.array([a.x, ah-.3, a.y]), np.array([b.x, bh-.3, b.y])
        length = np.linalg.norm(end-start)
        count = min(40, int(length / settings['bulbSpacing']))
        for t in np.linspace(0, 1, max(2, count+1)):
            if len(parts) >= settings["maxBulbs"]:
                return parts
            point = start * (1-t) + end * t
            point[1] -= min(length*.08, 1.5) * 4*t*(1-t)
            part = appearance(_box(key, point, (.045,.06,.045), 'wall'), 'night_bulb', collision=False)
            parts.append(part)
    return parts
