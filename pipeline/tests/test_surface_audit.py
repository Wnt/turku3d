import numpy as np
import shapely
from turku3d.coarse.surface_audit import audit, Rays
from turku3d.coarse.openings import Openings
from turku3d.ground.mesh import Mesh


def square(y=0):
    return np.array([[[0,y,0],[0,y,2],[2,y,2]], [[0,y,0],[2,y,2],[2,y,0]]], float)


def test_source_walk_cannot_be_satisfied_by_riverbed_or_bridge_roof():
    q = np.array([[.5,.5], [1.5,1.5]])
    surface = np.concatenate([square(-3), square(5)])
    result = audit(surface, surface, q, np.zeros(2))
    assert result['counts']['visual_holes'] == 2
    assert result['counts']['collision_holes'] == 2
    assert result['defects']['visual_holes'][0]['visual_y'] == -3


def test_parity_and_missing_surface_are_separate():
    q = np.array([[.5,.5]])
    result = audit(square(), square(.2), q, np.zeros(1))
    assert result['counts']['mismatch'] == 1
    assert result['counts']['visual_holes'] == 0
    result = audit(square(), np.empty((0,3,3)), q, np.zeros(1))
    assert result['counts']['mismatch'] == 1
    assert result['counts']['collision_holes'] == 1


def test_slivers_and_wall_support():
    thin = np.array([[[0,0,0],[2,0,0],[2,0,.01]]])
    wall = np.array([[[1,0,0],[1,2,0],[1,2,2]]])
    visual = np.concatenate([square(), thin, wall])
    q = np.array([[.5,.5]])
    r = audit(visual, square(), q, np.zeros(1), support=shapely.Polygon())
    assert r['counts']['slivers'] == 1
    assert r['counts']['unsupported_walls'] == 1
    assert r['counts']['thin_walls'] == 1
    supported = audit(visual, square(), q, np.zeros(1), support=shapely.box(.9,-1,1.1,3))
    assert supported['counts']['unsupported_walls'] == 0


def test_opening_detects_wall_but_preserves_upper_deck():
    t = square()
    opening = Openings()
    opening.add(Mesh(t.reshape(-1,3), np.arange(6).reshape(-1,3), np.ones(2)), 2.5)
    wall = np.array([[[1,0,0],[1,2,0],[1,2,2]]])
    collision = np.concatenate([t, wall, square(3)])
    r = audit(collision, collision, np.array([[.5,.5]]), np.zeros(1), openings=opening)
    assert r['counts']['blocked_openings'] == 1
    assert Rays(collision).cast(np.array([[.5,.5]]), np.zeros(1))[0] == 0
