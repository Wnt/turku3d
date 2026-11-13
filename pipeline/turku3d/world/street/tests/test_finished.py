"""The finished-ground rule in the world pipeline (street.finished over turku3d.ground.finished)."""
import numpy as np
import shapely

from turku3d import config as C


def plane(x, z):
    return 10.0 + 0.01 * x - 0.004 * z


def test_a_trench_in_a_renovated_street_is_filled_and_tiles_agree_on_their_edge(tmp_path, monkeypatch):
    from turku3d.ground import finished as rule
    from turku3d.world.street import finished, lidar

    monkeypatch.setenv('TURKU3D_WORLD_DATA', str(tmp_path / 'world'))
    monkeypatch.setenv('TURKU3D_SHARED_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setattr(lidar, 'laz_path', lambda name: tmp_path / f'{name}.laz')
    rule._WINDOWS.clear()
    finished.correction.cache_clear()
    # Scanned key points: a plane with a 2 m deep, 8 m wide trench crossing the edge between two lidar tiles
    # (engine x = 500), inside a street part renovated after the scan.
    gx, gz = np.meshgrid(np.arange(-200.0, 1201.0, 2.0), np.arange(-900.0, 401.0, 2.0))
    x, z = gx.ravel(), gz.ravel()
    trench = (np.abs(z + 250.0) < 4.0) & (np.abs(x - 500.0) < 60.0)
    points = np.column_stack([x, plane(x, z) - 2.0 * trench, z])
    street = shapely.box(440.0, -270.0, 560.0, -230.0)
    evidence = rule.Evidence(zones=[(street, {'key': 'k', 'kind': 'Ajorata', 'year': 2022, 'material': 'Luonnonkivi'})],
                             streets=[street], points=points)
    monkeypatch.setattr(finished, 'evidence', lambda name, sources: evidence)
    try:
        west, east = '23459500_6705000', '23460000_6705000'
        for name in (west, east):
            report = finished.make(name, sources=object())
            assert report['changed_m2'] > 0, report
        (sw, ww), (se, we) = finished.correction(west), finished.correction(east)
        # The shared edge: column 500 of the western tile is column 0 of the eastern one.
        assert np.allclose(sw[:, -1], se[:, 0], atol=1e-4) and np.array_equal(ww[:, -1], we[:, 0])
        e, n = C.local_to_en(np.array([480.0, 500.0, 520.0, 500.0]), 0.0, np.array([-250.0, -250.0, -250.0, -150.0]))[:2]
        scanned = plane(np.array([480.0, 500.0, 520.0, 500.0]), -250.0) - np.array([2.0, 2.0, 2.0, 0.0])
        scanned[3] = plane(500.0, -150.0)
        out = finished.finish(e, n, scanned)
        assert np.allclose(out[:3], plane(np.array([480.0, 500.0, 520.0]), -250.0), atol=0.1), out
        assert out[3] == scanned[3]  # 100 m from the street: the scan as it is
    finally:
        rule._WINDOWS.clear()
        finished.correction.cache_clear()
