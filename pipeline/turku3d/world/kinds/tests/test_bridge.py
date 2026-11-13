"""Bridges: a deck's facts become abutments, piers, a girder over the water and parapets by type and profile."""
import numpy as np

from turku3d.world.api import Stub


def _open_edges(positions: np.ndarray, triangles: np.ndarray) -> int:
    _, weld = np.unique(np.round(positions, 4), axis=0, return_inverse=True)
    triangles = weld.ravel()[triangles]
    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum())


def _facts(**over) -> dict:
    """A straight 60 m road bridge running east, 12 m wide: banks to 6 m and from 54 m, water between 12 and 48 m."""
    e0, n0, length, half = 23459000.0, 6704000.0, 60.0, 6.0
    stations = np.arange(0.0, length + 0.01, 1.0)
    ground = np.where((stations < 12) | (stations > 48), 2.0, 0.0)
    ground = np.where(stations < 4, 4.5, np.where(stations > 56, 4.5, ground))
    facts = {
        'key': 'osm:way/1', 'name': 'Testisilta', 'wikidata': '', 'year': 1907, 'structure': 'beam', 'material': '',
        'use': 'road',
        'rings': [[[e0, n0 - half], [e0 + length, n0 - half], [e0 + length, n0 + half], [e0, n0 + half]]],
        'axis': [[e0, n0], [e0 + length, n0]], 'ends': [5.0, 5.0],
        'sides': [[[e0, n0 + half], [e0 + length, n0 + half]], [[e0, n0 - half], [e0 + length, n0 - half]]],
        'water': [[12.0, 48.0]], 'faces': [6.0, 54.0], 'banks': [1.0, 1.0],
        'ground': {'step': 1.0, 'heights': ground.tolist()}, 'piers': {'measured': [30.0]}, 'gates': [],
    }
    return {**facts, **over}


def _compile(facts: dict, profile: dict | None = None):
    from turku3d.world.kinds.bridge import Bridge, profiles

    bridge = Bridge()
    profiles.cache_clear()
    description = bridge.describe(Stub('bridge/osm-way-1', (0, 0, 1, 1), [], facts), {'profile': profile} if profile else None)
    return bridge.compile(description)


def test_a_bridge_stands_on_abutments_and_its_measured_pier_with_one_girder_over_the_water():
    from turku3d.world.kinds.ground.mesh import WATER_LEVEL

    output = _compile(_facts())
    parts = {p.name: p for p in output.parts}
    assert {'abutment', 'pier', 'pier-cap', 'span', 'edge-beam'} <= set(parts)
    base = output.origin[1]
    pier = parts['pier'].positions    # entity frame: x east of the axis's start
    # One pier, at the measured station 30 m along, from under the bed to the girder.
    assert abs((pier[:, 0].min() + pier[:, 0].max()) / 2 - 30.0) < 0.01 and pier[:, 0].max() - pier[:, 0].min() < 3.0
    assert pier[:, 1].min() + base < WATER_LEVEL - 1.0
    for name in ('pier', 'span'):
        assert _open_edges(parts[name].positions, np.asarray(parts[name].triangles)) == 0, name
    span = parts['span'].positions
    assert span[:, 1].min() + base >= WATER_LEVEL + 0.8 - 1e-6        # the girder keeps clear of the water
    assert span[:, 1].max() + base <= 5.0 - 0.9 + 0.05 + 1e-6         # and stays under the deck's top
    # An old road bridge: solid stone parapets over the abutments, railings over the spans.
    assert 'parapet' in parts and any('panel' in (item.ids or [''])[0] for item in output.instances)


def test_the_type_adds_piers_where_the_scan_saw_none_and_a_profile_sets_parapets_pylons_and_an_arch():
    output = _compile(_facts(piers={'measured': []}, use='foot', year=1990), profile={
        'parapets': {'approach': 'balustrade', 'span': 'lattice'},
        'pylons': {'height': 5.0, 'width': 1.0, 'lantern': True},
        'arches': [{'end': 0, 'from_face': 3.0, 'width': 2.0, 'height': 2.6, 'grille': True}]})
    parts = {p.name: p for p in output.parts}
    pier = parts['pier'].positions[:, 0]
    # A 48 m foot bridge between its faces is longer than the type's 35 m span: one pier half way.
    assert abs((pier.min() + pier.max()) / 2 - 30.0) < 0.01
    assert {'balustrade-plinth', 'balustrade-coping', 'pylon', 'pylon-bronze', 'abutment-void', 'grille'} <= set(parts)
    names = {(item.ids or [''])[0].split('/')[-1].rsplit('-', 1)[0] for item in output.instances}
    assert {'baluster', 'panel', 'post'} <= names
    # Four pylons: two at each face.
    pylon = parts['pylon'].positions
    assert len(np.unique(np.round(pylon[:, [0, 2]] / 3.0), axis=0)) >= 4
