"""Measured ground corrections preserve exclusions and do not touch other surface classes."""

import numpy as np
import shapely
from types import SimpleNamespace

from turku3d.coarse import ground_colour, ortho_cleanup
from turku3d.col.materials import mat_id


def test_lab_roundtrip_and_existing_calibration_space():
    from turku3d.facade_kit import renderer
    renderer()
    from turkutex.wall_palette import lab

    values = np.array([[0, 0, 0], [255, 255, 255], [87, 130, 63], [80, 100, 140]])
    assert np.allclose(ground_colour.lab(values), lab(values))
    assert np.max(np.abs(ground_colour.rgb(ground_colour.lab(values)) - values)) < 1e-9


def test_only_supported_unblocked_ground_changes(monkeypatch):
    ids = np.array([[mat_id('road'), mat_id('road'), mat_id('grass')],
                    [mat_id('road'), mat_id('road'), mat_id('gravel')]])
    z, x = np.indices(ids.shape)
    monkeypatch.setattr(ortho_cleanup, 'raster', lambda *args: (ids, x, z))
    source = np.full((*ids.shape, 3), 120, np.uint8)
    source[0, 1] = 230
    model = dict(groups={'asphalt': dict(delta_lab=[-10, 0, 0])}, mosaics={})
    output, stats = ground_colour.apply(SimpleNamespace(), source, source, (), (),
                                        buildings=[shapely.box(-.1, .9, .1, 1.1)], model=model)
    assert output[0, 0].max() < 120
    assert np.array_equal(output[0, 1], source[0, 1])  # paint
    assert np.array_equal(output[1, 0], source[1, 0])  # building
    assert np.array_equal(output[:, 2], source[:, 2])  # unsupported classes
    assert stats['asphalt']['pixels'] == 2


def test_calibration_table_invalidates_visuals_without_collision(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import collision_key, packaging

    visual_before = packaging.code_hash()
    collision_before = collision_key.code_hash()
    read = Path.read_bytes
    table = ground_colour.TABLE
    monkeypatch.setattr(Path, 'read_bytes', lambda path: read(path) + b' ' if path == table else read(path))
    assert packaging.code_hash() != visual_before
    assert collision_key.code_hash() == collision_before
