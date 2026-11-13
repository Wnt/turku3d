"""What covers the ground: the vocabulary of the ground kind and how the registers map onto it.

A cover is a material family with flat, metric parameters, which the client draws at one texel density from the
photographic ground library or synthesises (game/src/world/location/families/ground.ts, groundSynth.ts). `color` and
`roughness` are the means of those maps for the parameters (linear albedo): what the far level of detail and any
coarse lighting use.

Families: `asphalt`, `paving` (laid slabs and blocks: cast concrete, sawn granite, slate), `setts` (cut granite
cubes), `cobbles` (rounded field stone), `gravel`, `grass`, `soil` (planting beds, forest floor), `sand`,
`bedrock`, `kerbstone`, `road_paint`.
"""
from __future__ import annotations

# Levels: a carriageway lies a kerb's height below what borders it.
LOW_USES = ('ajorata', 'pyöräkaista')

# Register surface text -> cover. The first word found wins; sizes are metres (across, along).
REGISTER = (
    ('punainen', {'family': 'asphalt', 'params': {'tone': 0.35, 'tint': '#b97f72'}, 'color': (0.211, 0.092, 0.073), 'roughness': 0.93}),
    ('asfaltti', {'family': 'asphalt', 'params': {'tone': 0.72}, 'color': (0.176, 0.176, 0.176), 'roughness': 0.93}),
    # From the old generator's finish table (coarse/street_materials.py), which was set against street panoramas of
    # sampled register parts: "Betonikivi" is mostly red-brown block paving and "Betonilaatta" footways are small grey
    # pavers in running bond, not half-metre slabs; "Maatiili" is clay brick.
    ('maatiili', {'family': 'paving', 'params': {'size_x': 0.1, 'size_y': 0.2, 'bond': 'running', 'tone': 0.45, 'joint_m': 0.004, 'stone': 'concrete', 'tint': '#ffc8b0'},
                  'color': (0.229, 0.137, 0.103), 'roughness': 0.86}),
    # Cast concrete on footways, measured against the asphalt beside it in the street photos of the random dev
    # points (suburbs, house and work areas; 9 footways): weathered concrete is warm grey, about as bright as old
    # asphalt up to 1.4 times it, a little redder than green and clearly less blue (red 1.02-1.15, blue 0.74-0.90 of
    # green). "Betonilaatta" is 30 cm slabs in half bond, "Betonikivi" small pavers with a muted red-brown cast.
    # Pale blue-grey slabs and a vivid red band, as drawn before, are what the judge named at five points.
    # The light readings of the same photos (descriptions/location/dev_*.json, 14 paving-stone footways) agree on
    # the hue and put the slabs lighter: median #a39a90 beside asphalt #77777a, the reddish ones #8e7872.
    ('betonilaatta', {'family': 'paving', 'params': {'size_x': 0.3, 'size_y': 0.3, 'bond': 'running', 'tone': 0.68, 'joint_m': 0.006, 'stone': 'concrete', 'tint': '#fff5e1'},
                      'color': (0.297, 0.271, 0.223), 'roughness': 0.86}),
    ('betonikivi', {'family': 'paving', 'params': {'size_x': 0.1, 'size_y': 0.2, 'bond': 'running', 'tone': 0.42, 'joint_m': 0.004, 'stone': 'concrete', 'tint': '#ffe2da'},
                    'color': (0.223, 0.172, 0.156), 'roughness': 0.86}),
    ('noppakivi', {'family': 'setts', 'params': {'size_x': 0.09, 'size_y': 0.09, 'tone': 0.5}, 'color': (0.171, 0.149, 0.135), 'roughness': 0.81}),
    ('nupukivi', {'family': 'setts', 'params': {'size_x': 0.14, 'size_y': 0.2, 'tone': 0.45}, 'color': (0.238, 0.229, 0.218), 'roughness': 0.82}),
    ('kenttäkivi', {'family': 'cobbles', 'params': {'size_m': 0.14}, 'color': (0.153, 0.134, 0.116), 'roughness': 0.8}),
    ('liuskekivi', {'family': 'paving', 'params': {'size_x': 0.4, 'size_y': 0.6, 'bond': 'running', 'tone': 0.4, 'joint_m': 0.008, 'stone': 'slate'}, 'color': (0.104, 0.109, 0.117), 'roughness': 0.71}),
    ('graniitti', {'family': 'paving', 'params': {'size_x': 0.4, 'size_y': 1.0, 'bond': 'running', 'tone': 0.55, 'joint_m': 0.008, 'stone': 'granite'}, 'color': (0.232, 0.18, 0.162), 'roughness': 0.71}),
    ('luonnonkivi', {'family': 'paving', 'params': {'size_x': 0.3, 'size_y': 0.5, 'bond': 'running', 'tone': 0.5, 'joint_m': 0.008, 'stone': 'granite'}, 'color': (0.218, 0.178, 0.163), 'roughness': 0.71}),
    ('kivituhka', {'family': 'gravel', 'params': {'grade': 'fine'}, 'color': (0.273, 0.248, 0.21), 'roughness': 0.95}),
    ('kivi', {'family': 'paving', 'params': {'size_x': 0.3, 'size_y': 0.5, 'bond': 'running', 'tone': 0.5, 'joint_m': 0.008, 'stone': 'granite'}, 'color': (0.218, 0.178, 0.163), 'roughness': 0.71}),
    ('sora', {'family': 'gravel', 'params': {'grade': 'coarse'}, 'color': (0.304, 0.275, 0.233), 'roughness': 0.95}),
    ('murske', {'family': 'gravel', 'params': {'grade': 'coarse'}, 'color': (0.304, 0.275, 0.233), 'roughness': 0.95}),
    ('hiekka', {'family': 'sand', 'params': {}, 'color': (0.35, 0.292, 0.204), 'roughness': 0.96}),
    ('betoni', {'family': 'paving', 'params': {'size_x': 1.2, 'size_y': 1.2, 'bond': 'stack', 'tone': 0.5, 'joint_m': 0.004, 'stone': 'concrete'}, 'color': (0.279, 0.272, 0.26), 'roughness': 0.85}),
    ('nurmi', {'family': 'grass', 'params': {'mown': 1.0}, 'color': (0.072, 0.129, 0.016), 'roughness': 0.96}),
    ('niitty', {'family': 'grass', 'params': {'mown': 0.2}, 'color': (0.076, 0.122, 0.014), 'roughness': 0.96}),
    # The green register's "Metsä" and "Kasvillisuus" are woodland floor and unmown verges: green in every street
    # photo (bare brown strips beside the road were drawn at 5 of the 12 dev points).
    ('metsä', {'family': 'grass', 'params': {'mown': 0.0}, 'color': (0.076, 0.122, 0.014), 'roughness': 0.96}),
    ('kasvillisuus', {'family': 'grass', 'params': {'mown': 0.2}, 'color': (0.076, 0.122, 0.014), 'roughness': 0.96}),
    ('istutus', {'family': 'soil', 'params': {'kind': 'bed'}, 'color': (0.045, 0.035, 0.02), 'roughness': 0.95}),
)
# By use, when the surface text says nothing.
BY_USE = {
    'street-low': {'family': 'asphalt', 'params': {'tone': 0.7}, 'color': (0.172, 0.172, 0.172), 'roughness': 0.93},
    'street-high': {'family': 'asphalt', 'params': {'tone': 0.8}, 'color': (0.19, 0.19, 0.19), 'roughness': 0.93},
    'green': {'family': 'grass', 'params': {'mown': 0.7}, 'color': (0.073, 0.127, 0.016), 'roughness': 0.96},
}
# OpenStreetMap tags -> cover, for land the city's registers do not describe (yards, lots). First match wins.
OSM = (
    (('landuse', ('grass', 'village_green', 'meadow', 'recreation_ground', 'cemetery')), 'grass'),
    (('leisure', ('park', 'garden', 'pitch')), 'grass'),
    (('natural', ('grassland', 'heath')), 'grass'),
    (('landuse', ('flowerbed', 'allotments')), 'planting'),
    (('landuse', ('forest',)), 'rough'),
    (('natural', ('scrub', 'wood')), 'rough'),
    (('natural', ('water',)), 'water'),
    (('natural', ('bare_rock',)), 'rock'),
    (('natural', ('sand', 'beach')), 'sand'),
    (('leisure', ('playground',)), 'sand'),
    (('surface', ('asphalt', 'paved')), 'asphalt-yard'),
    (('surface', ('paving_stones', 'concrete')), 'pavers-yard'),
    (('surface', ('sett', 'cobblestone', 'unhewn_cobblestone')), 'setts-yard'),
    (('surface', ('gravel', 'fine_gravel', 'unpaved', 'compacted', 'pebblestone', 'dirt', 'ground')), 'gravel'),
    (('amenity', ('parking',)), 'asphalt-yard'),
)
NAMED = {
    'grass': {'family': 'grass', 'params': {'mown': 0.8}, 'color': (0.073, 0.128, 0.016), 'roughness': 0.96},
    # A street's verge on land nobody mapped (ground's `_verges`): grass mown now and then.
    'verge': {'family': 'grass', 'params': {'mown': 0.4}, 'color': (0.074, 0.125, 0.015), 'roughness': 0.96},
    # Woodland floor and scrub: what grows under the trees and shrubs the planting track sets there.
    'rough': {'family': 'grass', 'params': {'mown': 0.0}, 'color': (0.076, 0.122, 0.014), 'roughness': 0.96},
    # A railway's track bed (the `rail` kind lays the sleepers and rails on it): crushed grey granite.
    'railway': {'family': 'gravel', 'params': {'grade': 'ballast'}, 'color': (0.15, 0.143, 0.136), 'roughness': 0.97},
    'planting': {'family': 'soil', 'params': {'kind': 'bed'}, 'color': (0.045, 0.035, 0.02), 'roughness': 0.95},
    # Under open water: the bed, which the `water` kind's surface lies over.
    'water': {'family': 'soil', 'params': {'kind': 'bed'}, 'color': (0.03, 0.028, 0.02), 'roughness': 0.95},
    'rock': {'family': 'bedrock', 'params': {}, 'color': (0.183, 0.174, 0.165), 'roughness': 0.85},
    'sand': {'family': 'sand', 'params': {}, 'color': (0.35, 0.292, 0.204), 'roughness': 0.96},
    # A cycle lane on the carriageway: Turku paints them red.
    # Where cars stand along the kerb: asphalt the traffic does not polish, paler and dustier than the lanes.
    'parking-lane': {'family': 'asphalt', 'params': {'tone': 0.95}, 'color': (0.218, 0.218, 0.218), 'roughness': 0.93},
    'cycle-lane': {'family': 'asphalt', 'params': {'tone': 0.7, 'tint': '#92807a'}, 'color': (0.198, 0.149, 0.134), 'roughness': 0.93},
    'gravel': {'family': 'gravel', 'params': {'grade': 'fine'}, 'color': (0.273, 0.248, 0.21), 'roughness': 0.95},
    'asphalt-yard': {'family': 'asphalt', 'params': {'tone': 0.8}, 'color': (0.19, 0.19, 0.19), 'roughness': 0.93},
    'pavers-yard': {'family': 'paving', 'params': {'size_x': 0.14, 'size_y': 0.21, 'bond': 'running', 'tone': 0.5, 'joint_m': 0.005, 'stone': 'concrete', 'tint': '#fff5e1'}, 'color': (0.242, 0.222, 0.182), 'roughness': 0.86},
    'setts-yard': {'family': 'setts', 'params': {'size_x': 0.09, 'size_y': 0.09, 'tone': 0.5}, 'color': (0.171, 0.149, 0.135), 'roughness': 0.81},
    # Land nobody mapped and the orthophoto shows neither green nor gravel: a yard's asphalt (the old generator's choice).
    'unmapped': {'family': 'asphalt', 'params': {'tone': 0.8}, 'color': (0.19, 0.19, 0.19), 'roughness': 0.93},
    # Land nobody mapped where the orthophoto has no imagery (the city's rural outskirts): meadow and forest floor.
    'unseen': {'family': 'grass', 'params': {'mown': 0.2}, 'color': (0.076, 0.122, 0.014), 'roughness': 0.96},
    'kerb': {'family': 'kerbstone', 'params': {}, 'color': (0.381, 0.336, 0.312), 'roughness': 0.81},
    # Cast concrete kerb stones: pale, even grey, where the streets were built outside the old centre (ground's
    # `kerb_stone`).
    'kerb-concrete': {'family': 'kerbstone', 'params': {'stone': 'concrete'}, 'color': (0.37, 0.365, 0.345), 'roughness': 0.86},
    'paint': {'family': 'road_paint', 'params': {'colour': '#eeeeea', 'wear': 0.35}, 'color': (0.729, 0.729, 0.681), 'roughness': 0.63},
}


def of_register(surface: str, level: str, green: bool) -> dict:
    """The cover of a register polygon from its surface text, else from its use."""
    text = (surface or '').lower()
    for word, cover in REGISTER:
        if word in text:
            return cover
    return BY_USE['green' if green else f'street-{level}']


def of_osm(tags: dict) -> str | None:
    """The named cover of an OpenStreetMap area, or None when its tags say nothing about the ground."""
    for (key, values), name in OSM:
        if tags.get(key) in values:
            return name
    return None
