"""Exact source order and columns, with one decode across concurrent consumers."""

from concurrent.futures import ThreadPoolExecutor
import shutil

import laspy
import numpy as np
import pytest

from turku3d.lidar import decoded


@pytest.mark.parametrize('point_format,version', [(3, '1.2'), (6, '1.4')])
def test_shared_decode_matches_direct_reader(tmp_path, monkeypatch, point_format, version):
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'shared'))
    path = tmp_path / 'source.laz'
    header = laspy.LasHeader(point_format=point_format, version=version)
    header.scales = [.001, .001, .001]
    header.offsets = [23459000., 6704000., 0.]
    las = laspy.LasData(header)
    rng = np.random.default_rng(71)
    las.x, las.y, las.z = (rng.uniform(0, 250, 513) + offset for offset in header.offsets)
    las.classification = rng.integers(0, 18, 513, dtype=np.uint8)
    las.return_number = rng.integers(1, 4, 513, dtype=np.uint8)
    las.number_of_returns = np.full(513, 4, np.uint8)
    las.intensity = rng.integers(0, 65535, 513, dtype=np.uint16)
    las.write(path)
    direct = laspy.read(path)
    opened = []
    original = laspy.open
    def record(*args, **kwargs):
        opened.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(laspy, 'open', record)
    with ThreadPoolExecutor(max_workers=4) as pool:
        arrays = list(pool.map(decoded.source, [path] * 4))
    assert len(opened) == 1
    for name in decoded.DTYPE.names:
        for data in arrays:
            np.testing.assert_array_equal(data[name], np.asarray(getattr(direct, name)))
        pieces = [getattr(chunk, name) for chunk in decoded.chunks(path, 31)]
        np.testing.assert_array_equal(np.concatenate(pieces), arrays[0][name])
    moved = tmp_path / 'another-generation.laz'
    shutil.copyfile(path, moved)
    np.testing.assert_array_equal(decoded.source(moved), arrays[0])
    assert len(opened) == 1
    las.z = np.asarray(las.z) + 1
    las.write(path)
    assert not np.array_equal(decoded.source(path), arrays[0])
    assert len(opened) == 2


def test_failed_decode_never_publishes(tmp_path, monkeypatch):
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'shared'))
    path = tmp_path / 'broken.laz'
    path.write_bytes(b'broken')
    with pytest.raises(laspy.errors.LaspyException):
        decoded.source(path)
    assert not list((tmp_path / 'shared/raw-derived/decoded').rglob('*.npy'))
