"""Night accents keep surveyed placement, opaque batches and daytime geometry."""
import json
import struct
from types import SimpleNamespace

import numpy as np
import shapely

from turku3d.coarse.night_accents import bridge_strips, bridge_profile, restaurant_bulbs
from turku3d.night_lighting import use_class, lamp_height
from turku3d.landmarks.appearance import night_wash
from turku3d.package.gltf import GlbMesh, build_glb


def test_only_reviewed_lit_bridge_receives_rail_strip_at_surveyed_height():
    way = {'id': 'mapped', 'tags': {'wikidata': 'Q11871663', 'lit': 'yes'}}
    rails = shapely.MultiLineString([[(0, 0), (40, 0)], [(0, 5), (40, 5)]])
    parts = bridge_strips(way, rails, lambda p: p[:, 0] * .01 + 7, 1.2)
    assert len(parts) == 1 and not parts[0].metadata['collision']
    p = parts[0].positions
    assert np.allclose(p[:, 1] - p[:, 0] * .01, 8.16, atol=.026)
    assert len(parts[0].indices) < 500
    way['tags']['lit'] = 'no'
    assert bridge_strips(way, rails, lambda p: p[:, 0]*0, 1.2) == []
    assert bridge_profile({'tags': {'wikidata': 'unknown'}}) == {}


def test_restaurant_strings_follow_existing_masts_and_never_ferry_defaults():
    boat = {'tags': {'amenity': 'restaurant'}, 'outline': shapely.box(0, 0, 40, 8),
            'masts': [(shapely.Point(5, 4), 12), (shapely.Point(30, 4), 15)]}
    parts = restaurant_bulbs('mapped-ship', boat)
    assert 10 < len(parts) <= 64
    assert all(not p.metadata['collision'] for p in parts)
    boat['tags'] = {'route': 'ferry'}
    assert restaurant_bulbs('ferry', boat) == []


def test_building_use_is_conservative_and_osm_wins_over_register_code():
    assert use_class({'use': '1120'}) == 1
    assert use_class({'osm_use': 'apartments', 'use': '1120'}) == 0
    assert use_class({'osm_use': 'school'}) == 1
    assert use_class({'osm_use': 'restaurant'}) == 2
    assert use_class({'osm_use': 'barn'}) == 0
    assert lamp_height({'height': 'nan'}, 8) == 8


def test_baked_landmark_mask_survives_glb_and_stops_before_copper():
    mesh = GlbMesh('wall', np.array([[0, 0, 0], [10, 0, 0], [10, 50, 0], [0, 50, 0.]]),
                   np.array([[0, 3, 2], [0, 2, 1]], dtype=np.uint32))
    d = {'tower': {'copper_start_m': 45}, 'night_lighting': {'directions_xz': [[0, 1]],
         'color_linear': [1, .74, .46], 'body_intensity': .18, 'tower_intensity': .65}}
    night_wash([mesh], SimpleNamespace(base=0), np.zeros(3), d)
    assert mesh.night_irradiance[np.isclose(mesh.positions[:,1], 0)].max() > 0
    assert mesh.night_irradiance[mesh.positions[:,1] >= 45].max() == 0
    assert mesh.night_irradiance[np.isclose(mesh.positions[:,1], 28)].max() > mesh.night_irradiance[np.isclose(mesh.positions[:,1], 0)].max()
    assert len(mesh.indices) < 40
    blob = build_glb([mesh]); n = struct.unpack_from('<I', blob, 12)[0]
    doc = json.loads(blob[20:20+n])
    assert '_NIGHT_IRRADIANCE' in doc['meshes'][0]['primitives'][0]['attributes']


def test_generic_osm_use_does_not_hide_register_evidence():
    assert use_class({'osm_use': 'yes', 'use': '1120'}) == 1


def test_rule_edit_refreshes_reader_and_recycles_import_time_worker_constants(tmp_path, monkeypatch):
    import time
    import os
    from turku3d import config as C, night_lighting
    from turku3d.coarse.worker_pool import Workers
    path = tmp_path / 'pipeline/turku3d/night_lighting.json'; path.parent.mkdir(parents=True)
    path.write_text('{"lamps": {"pathHeight": 4.8}}')
    monkeypatch.setattr(night_lighting, 'RULES_PATH', path)
    monkeypatch.setattr(C, 'REPO_ROOT', tmp_path)
    assert night_lighting.rules()['lamps']['pathHeight'] == 4.8
    started = time.time()
    path.write_text('{"lamps": {"pathHeight": 4.2}}'); os.utime(path, (started+1, started+1))
    assert night_lighting.rules()['lamps']['pathHeight'] == 4.2
    assert Workers()._changed_since(started)
