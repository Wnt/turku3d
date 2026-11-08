"""Lidar shrubs: bushes where class-4 returns measured shrubs in the open, none under crowns, in grass or on roads."""
import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import canopy_trees as T

TILE = C.VisualTile.parse("t0_0_0")


def patch(x0, z0, x1, z1, top):
    """One class-4 top per half-metre cell over a box."""
    x, z = np.meshgrid(np.arange(x0, x1, .5) + .25, np.arange(z0, z1, .5) + .25)
    return np.c_[x.ravel(), np.full(x.size, top), z.ravel()]


class Flat:
    """Flat ground at 0 m, with the vectorised sampler the generator passes."""

    def __call__(self, x, z):
        return 0.0

    @staticmethod
    def many(x, z):
        return np.zeros(len(np.asarray(x)))


def bushes(points, canopy=(), blocked=None, occupied=()):
    return T.shrubs(TILE, points, np.asarray(canopy, float).reshape(-1, 3), Flat(), blocked, occupied)


def test_open_thickets_become_bushes_spaced_by_their_size():
    thicket = patch(20, 20, 26, 23, 1.6)  # 6 x 3 m mantle, 1.6 m tall
    result = bushes(thicket)
    assert 3 <= len(result) <= 8
    assert all(20 <= b.x <= 26 and 20 <= b.z <= 23 for b in result)
    assert all(b.object_id.startswith("bush:lidar:") and b.measured and b.trunk_radius == 0 for b in result)
    assert all(abs(b.height - 1.6) < .01 and .5 <= b.crown_radius <= 1.5 for b in result)
    xy = np.array([(b.x, b.z) for b in result])
    gaps = np.linalg.norm(xy[:, None] - xy[None], axis=2) + np.eye(len(xy)) * 99
    assert gaps.min() >= T.SHRUB_SPACING - 1e-6


def test_no_bush_from_grass_trees_lone_returns_roads_or_standing_trunks():
    assert bushes(patch(20, 20, 26, 23, 0.4)) == []  # tall grass
    assert bushes(patch(20, 20, 26, 23, 4.5)) == []  # a tree's crown layer
    assert bushes(patch(20, 20, 21, 21, 1.5)) == []  # 4 cells: no thicket
    under = patch(60, 60, 66, 63, 1.5)
    assert bushes(under, canopy=patch(59, 59, 67, 64, 12.0)) == []  # lower branches under a crown
    road = shapely.box(100, 0, 110, 250)
    assert bushes(patch(100, 40, 110, 43, 1.5), blocked=road) == []
    near_trunk = bushes(patch(140, 40, 142, 43, 1.5), occupied=[(141, 41.5)])
    assert all(np.hypot(b.x - 141, b.z - 41.5) >= 1.5 for b in near_trunk)
