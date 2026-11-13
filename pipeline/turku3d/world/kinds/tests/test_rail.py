"""A railway track on a made-up line whose answer is known."""

import numpy as np

from turku3d.world.frame import CELL, cell_box_en
from turku3d.world.kinds import rail


def track_cell(i=2, j=-3, length=30.0, crossing=(12.0, 18.0)):
    """A straight track running east at 5 m, climbing 1 cm a metre, crossed by a street between two stations."""
    e0, n0, e1, n1 = cell_box_en(i, j)
    stations = np.arange(0.0, length + 0.5, rail.STEP)
    points = np.column_stack([e0 + 10 + stations, np.full(len(stations), n0 + CELL / 2), 5 + 0.01 * stations])
    return {'cell': [i, j], 'tracks': [{'key': 'osm:way/1', 'gauge': rail.GAUGE, 'electrified': True, 'start': 0.0,
                                        'stations': stations.tolist(), 'points': points.tolist(),
                                        'flush': [bool(crossing[0] <= s <= crossing[1]) for s in stations]}],
            'steel': rail.STEEL, 'running': rail.RUNNING, 'sleeper': rail.CONCRETE, 'sleeper_pitch': rail.SLEEPER_PITCH}


def test_rails_stand_on_sleepers_and_lie_flush_in_a_crossing():
    description = track_cell()
    out = rail.Rail().compile(description)
    by_name = {part.name: part for part in out.parts}
    assert set(by_name) == {'rails', 'running'}
    top = by_name['running'].positions.copy()
    top[:, 0] += out.origin[0]
    top[:, 1] += out.origin[1]
    i, j = description['cell']
    along = top[:, 0] - (i * CELL + 10)          # station of each vertex: the track runs east from 10 m into the cell
    ground = 5 + 0.01 * along
    open_track = (along < 11.5) | (along > 18.5)
    assert np.allclose(top[open_track, 1] - ground[open_track], rail.RAIL_TOP, atol=0.005)
    inside = (along > 12.5) & (along < 17.5)
    assert np.allclose(top[inside, 1] - ground[inside], rail.FLUSH_TOP, atol=0.005)
    # Two rails, the running edges a gauge apart.
    across = np.unique(np.round(top[:, 2] - top[:, 2].mean(), 3))
    assert np.allclose(across, [-(rail.GAUGE / 2 + rail.HEAD), -rail.GAUGE / 2, rail.GAUGE / 2, rail.GAUGE / 2 + rail.HEAD], atol=0.002)
    # A closed solid: every edge of the welded rails is shared by two triangles.
    positions = np.concatenate([part.positions for part in out.parts])
    triangles = np.concatenate([part.triangles + offset for part, offset in zip(out.parts, np.cumsum([0] + [len(p.positions) for p in out.parts[:-1]]))])
    _, welded = np.unique(np.round(positions, 4), axis=0, return_inverse=True)
    edges = np.sort(welded[triangles][:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    assert (counts == 2).all()
    # Sleepers every pitch along the open track, none in the crossing, each across the track.
    sleepers = out.instances[0].transforms
    stations = sleepers[:, 0] + out.origin[0] - (i * CELL + 10)
    # (the last one before a crossing and the first after it stay a section clear of the street's edge)
    assert len(sleepers) == sum(1 for k in range(int(np.ceil(30.0 / rail.SLEEPER_PITCH))) if not 11.0 < k * rail.SLEEPER_PITCH < 19.0)
    assert not ((stations > 12.0) & (stations < 18.0)).any()
    assert np.allclose(np.cos(sleepers[:, 3]), 0.0, atol=1e-4)   # the asset's length (+X) turned to lie north-south
