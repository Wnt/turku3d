"""Shared map cache preserves native pixels, content identity and private delivery."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import threading

import numpy as np
from PIL import Image

from turku3d.coarse import surface_library as maps, surfaces


def test_shared_channels_singleflight_and_private_outputs_survive_eviction(tmp_path, monkeypatch):
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'shared'))
    monkeypatch.setattr(maps.C, 'work_root', lambda: tmp_path / 'work')
    monkeypatch.setattr(maps.C, 'data_root', lambda: tmp_path / 'data')
    original = maps.produce
    calls = []
    lock = threading.Lock()
    def count(*args):
        with lock:
            calls.append(args[1])
        return original(*args)
    # Identity remains the real producer's source while the wrapper counts executions.
    getsource = maps.inspect.getsource
    monkeypatch.setattr(maps.inspect, 'getsource', lambda f: getsource(original if f is count else f))
    monkeypatch.setattr(maps, 'produce', count)
    roots = [tmp_path / name for name in ('loop', 'integration')]
    with ThreadPoolExecutor(2) as pool:
        outputs = list(pool.map(lambda root: maps.library(root, root / 'base', [0, 3]), roots))
    assert sorted(calls) == [0, 3]
    for channel in (0, 3):
        name = 'draft_' + surfaces.KEYS[channel]
        for slot in ('albedo', 'normal', 'orm'):
            first, second = (o / name / f'{slot}.png' for o in outputs)
            assert first.read_bytes() == second.read_bytes()
            assert first.stat().st_ino == second.stat().st_ino
        expected = surfaces.fallback(channel)
        if channel == 0:
            expected = surfaces.neutral_asphalt(expected)
        np.testing.assert_array_equal(np.asarray(Image.open(outputs[0] / name / 'albedo.png')), expected)
    shutil.rmtree(tmp_path / 'shared/surface-maps')
    assert (outputs[1] / 'draft_cobblestone/albedo.png').is_file()
    assert maps.library(roots[1], roots[1] / 'base', [0, 3]) == outputs[1]
    assert len(calls) == 2


def test_map_identity_uses_input_bytes_and_only_relevant_producers(tmp_path, monkeypatch):
    source_a, source_b = tmp_path / 'a', tmp_path / 'b'
    for root in (source_a, source_b):
        root.mkdir()
        for slot in ('albedo', 'normal', 'orm'):
            (root / f'{slot}.array.ktx2').write_bytes(slot.encode())
    before = maps.key(3, source_a)
    finish = surfaces.KEYS.index('fieldstone')
    procedural = maps.key(finish, source_a)
    assert maps.key(3, source_b) == before
    (source_b / 'albedo.array.ktx2').write_bytes(b'changed bytes')
    assert maps.key(3, source_b) != before
    assert maps.key(finish, source_b) == procedural
    getsource = maps.inspect.getsource
    # Any change in the procedural module (a helper such as a forest floor's palette too) replaces its maps.
    monkeypatch.setattr(maps.inspect, 'getsource', lambda f: getsource(f) + ('\n# new finish' if f is maps.ground_finishes else ''))
    assert maps.key(3, source_a) == before
    assert maps.key(finish, source_a) != procedural


def test_existing_base_material_keeps_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(maps.C, 'work_root', lambda: tmp_path / 'work')
    monkeypatch.setattr(maps.C, 'data_root', lambda: tmp_path / 'data')
    base = tmp_path / 'base'
    material = base / 'draft_asphalt_fresh'
    material.mkdir(parents=True)
    (material / 'albedo.png').write_bytes(b'accepted base pixels')
    result = maps.library(tmp_path / 'out', base, [0])
    assert (result / 'draft_asphalt_fresh').resolve() == material
