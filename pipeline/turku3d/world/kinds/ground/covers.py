"""What covers the ground: the vocabulary of the ground kind and how the registers map onto it.

A cover is a material family with flat, metric parameters, so the client synthesises it at one texel density
(game/src/world/location/families/groundSynth.ts). `color` and `roughness` are the means of the synthesised maps for
those parameters (linear albedo): what the far level of detail and any coarse lighting use.

Families: `asphalt`, `paving` (laid slabs and blocks: cast concrete, sawn granite, slate), `setts` (cut granite
cubes), `cobbles` (rounded field stone), `gravel`, `grass`, `soil` (planting beds, forest floor), `sand`,
`bedrock`, `kerbstone`, `road_paint`.
"""
from __future__ import annotations

# Levels: a carriageway lies a kerb's height below what borders it.
LOW_USES = ('ajorata', 'pyöräkaista')

# Register surface text -> cover. The first word found wins; sizes are metres (across, along).
REGISTER = (
    ('punainen', {'family': 'asphalt', 'params': {'tone': 0.35, 'tint': '#b97f72', 'grain': 0.6}, 'color': (0.197, 0.086, 0.068), 'roughness': 0.87}),
    ('asfaltti', {'family': 'asphalt', 'params': {'tone': 0.5, 'grain': 0.6}, 'color': (0.136, 0.134, 0.13), 'roughness': 0.87}),
    # From the old generator's finish table (coarse/street_materials.py), which was set against street panoramas of
    # sampled register parts: "Betonikivi" is mostly red-brown block paving and "Betonilaatta" footways are small grey
    # pavers in running bond, not half-metre slabs; "Maatiili" is clay brick.
    ('maatiili', {'family': 'paving', 'params': {'size_x': 0.1, 'size_y': 0.2, 'bond': 'running', 'tone': 0.7, 'joint_m': 0.004, 'stone': 'concrete', 'tint': '#ffc4b0'},
                  'color': (0.3, 0.17, 0.14), 'roughness': 0.86}),
    ('betonilaatta', {'family': 'paving', 'params': {'size_x': 0.125, 'size_y': 0.2, 'bond': 'running', 'tone': 0.62, 'joint_m': 0.005, 'stone': 'concrete'},
                      'color': (0.28, 0.27, 0.26), 'roughness': 0.86}),
    ('betonikivi', {'family': 'paving', 'params': {'size_x': 0.1, 'size_y': 0.2, 'bond': 'running', 'tone': 0.72, 'joint_m': 0.004, 'stone': 'concrete', 'tint': '#ffccba'},
                    'color': (0.3, 0.19, 0.16), 'roughness': 0.86}),
    ('noppakivi', {'family': 'setts', 'params': {'size_x': 0.09, 'size_y': 0.09, 'tone': 0.5}, 'color': (0.171, 0.149, 0.135), 'roughness': 0.81}),
    ('nupukivi', {'family': 'setts', 'params': {'size_x': 0.14, 'size_y': 0.2, 'tone': 0.45}, 'color': (0.181, 0.158, 0.144), 'roughness': 0.78}),
    ('kenttäkivi', {'family': 'cobbles', 'params': {'size_m': 0.14}, 'color': (0.153, 0.134, 0.116), 'roughness': 0.8}),
    ('liuskekivi', {'family': 'paving', 'params': {'size_x': 0.4, 'size_y': 0.6, 'bond': 'running', 'tone': 0.4, 'joint_m': 0.008, 'stone': 'slate'}, 'color': (0.104, 0.109, 0.117), 'roughness': 0.71}),
    ('graniitti', {'family': 'paving', 'params': {'size_x': 0.4, 'size_y': 1.0, 'bond': 'running', 'tone': 0.55, 'joint_m': 0.008, 'stone': 'granite'}, 'color': (0.232, 0.18, 0.162), 'roughness': 0.71}),
    ('luonnonkivi', {'family': 'paving', 'params': {'size_x': 0.3, 'size_y': 0.5, 'bond': 'running', 'tone': 0.5, 'joint_m': 0.008, 'stone': 'granite'}, 'color': (0.218, 0.178, 0.163), 'roughness': 0.71}),
    ('kivituhka', {'family': 'gravel', 'params': {'grade': 'fine'}, 'color': (0.231, 0.205, 0.165), 'roughness': 0.95}),
    ('kivi', {'family': 'paving', 'params': {'size_x': 0.3, 'size_y': 0.5, 'bond': 'running', 'tone': 0.5, 'joint_m': 0.008, 'stone': 'granite'}, 'color': (0.218, 0.178, 0.163), 'roughness': 0.71}),
    ('sora', {'family': 'gravel', 'params': {'grade': 'coarse'}, 'color': (0.258, 0.229, 0.188), 'roughness': 0.95}),
    ('murske', {'family': 'gravel', 'params': {'grade': 'coarse'}, 'color': (0.258, 0.229, 0.188), 'roughness': 0.95}),
    ('hiekka', {'family': 'sand', 'params': {}, 'color': (0.35, 0.292, 0.204), 'roughness': 0.96}),
    ('betoni', {'family': 'paving', 'params': {'size_x': 1.2, 'size_y': 1.2, 'bond': 'stack', 'tone': 0.5, 'joint_m': 0.004, 'stone': 'concrete'}, 'color': (0.279, 0.272, 0.26), 'roughness': 0.85}),
    ('nurmi', {'family': 'grass', 'params': {'mown': 1.0}, 'color': (0.065, 0.12, 0.026), 'roughness': 0.92}),
    ('niitty', {'family': 'grass', 'params': {'mown': 0.2}, 'color': (0.065, 0.12, 0.027), 'roughness': 0.92}),
    ('metsä', {'family': 'soil', 'params': {'kind': 'forest'}, 'color': (0.054, 0.045, 0.024), 'roughness': 0.95}),
    ('kasvillisuus', {'family': 'soil', 'params': {'kind': 'bed'}, 'color': (0.045, 0.035, 0.02), 'roughness': 0.95}),
    ('istutus', {'family': 'soil', 'params': {'kind': 'bed'}, 'color': (0.045, 0.035, 0.02), 'roughness': 0.95}),
)
# By use, when the surface text says nothing.
BY_USE = {
    'street-low': {'family': 'asphalt', 'params': {'tone': 0.45, 'grain': 0.8}, 'color': (0.129, 0.126, 0.122), 'roughness': 0.87},
    'street-high': {'family': 'asphalt', 'params': {'tone': 0.55, 'grain': 0.4}, 'color': (0.143, 0.14, 0.136), 'roughness': 0.88},
    'green': {'family': 'grass', 'params': {'mown': 0.7}, 'color': (0.065, 0.12, 0.027), 'roughness': 0.92},
}
# OpenStreetMap tags -> cover, for land the city's registers do not describe (yards, lots). First match wins.
OSM = (
    (('landuse', ('grass', 'village_green', 'meadow', 'recreation_ground', 'cemetery')), 'grass'),
    (('leisure', ('park', 'garden', 'pitch')), 'grass'),
    (('natural', ('grassland', 'heath')), 'grass'),
    (('landuse', ('flowerbed', 'forest', 'allotments')), 'planting'),
    (('natural', ('scrub', 'wood')), 'planting'),
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
    'grass': {'family': 'grass', 'params': {'mown': 0.8}, 'color': (0.065, 0.12, 0.026), 'roughness': 0.92},
    'planting': {'family': 'soil', 'params': {'kind': 'bed'}, 'color': (0.045, 0.035, 0.02), 'roughness': 0.95},
    'rock': {'family': 'bedrock', 'params': {}, 'color': (0.183, 0.174, 0.165), 'roughness': 0.85},
    'sand': {'family': 'sand', 'params': {}, 'color': (0.35, 0.292, 0.204), 'roughness': 0.96},
    # A cycle lane on the carriageway: Turku paints them red.
    'cycle-lane': {'family': 'asphalt', 'params': {'tone': 0.35, 'tint': '#b97f72', 'grain': 0.6}, 'color': (0.197, 0.086, 0.068), 'roughness': 0.87},
    'gravel': {'family': 'gravel', 'params': {'grade': 'fine'}, 'color': (0.231, 0.205, 0.165), 'roughness': 0.95},
    'asphalt-yard': {'family': 'asphalt', 'params': {'tone': 0.55, 'grain': 0.5}, 'color': (0.146, 0.143, 0.138), 'roughness': 0.88},
    'pavers-yard': {'family': 'paving', 'params': {'size_x': 0.14, 'size_y': 0.21, 'bond': 'running', 'tone': 0.5, 'joint_m': 0.005, 'stone': 'concrete'}, 'color': (0.237, 0.231, 0.219), 'roughness': 0.86},
    'setts-yard': {'family': 'setts', 'params': {'size_x': 0.09, 'size_y': 0.09, 'tone': 0.5}, 'color': (0.171, 0.149, 0.135), 'roughness': 0.81},
    # Land nobody mapped and the orthophoto shows neither green nor gravel: a yard's asphalt (the old generator's choice).
    'unmapped': {'family': 'asphalt', 'params': {'tone': 0.55, 'grain': 0.5}, 'color': (0.146, 0.143, 0.138), 'roughness': 0.88},
    'kerb': {'family': 'kerbstone', 'params': {}, 'color': (0.273, 0.252, 0.241), 'roughness': 0.82},
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
