import numpy as np
import shapely
from turku3d.ground.mesh import Mesh
from turku3d.structures.geometry import Mesh as Structure
from turku3d.coarse.openings import Openings
from turku3d.coarse.surface_closure import resolve, clean, boundary
from turku3d.coarse.surface_audit import audit

BOUNDS = (0, 0, 4, 4)
EMPTY = Mesh(np.zeros((0, 3)), np.zeros((0, 3), np.uint32), np.zeros(0, np.uint8))


def sheet(x0, x1, y):
    return Mesh(np.array([[x0, y, 0], [x0, y, 4], [x1, y, 4], [x1, y, 0]], float),
                np.array([[0, 1, 2], [0, 2, 3]], np.uint32), np.ones(2, np.uint8))


def join(*meshes):
    p, ix, off = [], [], 0
    for m in meshes:
        p.append(m.positions); ix.append(m.indices + off); off += len(m.positions)
    return Mesh(np.concatenate(p), np.concatenate(ix).astype(np.uint32), np.ones(sum(len(m.indices) for m in meshes), np.uint8))


def floor(x0, x1, y):
    return Structure('stairs:test', 'paving_slab', [[x0, y, 0], [x0, y, 4], [x1, y, 4], [x1, y, 0]],
                     [[0, 1, 2], [0, 2, 3]], np.zeros((4, 2)))


def wall(x, y0, y1):
    return Structure('retaining:test', 'wall', [[x, y0, 0], [x, y1, 0], [x, y1, 4], [x, y0, 4]],
                     [[0, 1, 2], [0, 2, 3]], np.zeros((4, 2)))


def run(dry, structures=(), excluded=shapely.Polygon(), openings=None):
    _, _, meshes, report = resolve(dry, dry, list(structures), [], EMPTY, openings or Openings(), excluded, BOUNDS)
    return meshes, report


def test_raised_sheet_edge_gets_one_closing_face():
    meshes, report = run(join(sheet(0, 2, 0), sheet(2, 4, 1)))
    assert report['sealed_seams'] == 1 and abs(report['sealed_length_m'] - 4) < 1e-6
    assert abs(report['max_step_m'] - 1) < 1e-6
    face = meshes[0]
    assert face.material == 'wall' and face.metadata['collision'] is True
    assert np.allclose(face.positions[:, 0], 2) and face.positions[:, 1].min() < 0 and abs(face.positions[:, 1].max() - 1) < 1e-9


def test_existing_wall_or_shared_edge_needs_no_face():
    assert run(join(sheet(0, 2, 0), sheet(2, 4, 1)), [wall(2, 0, 1)])[1]['sealed_seams'] == 0
    continuous = Mesh(np.array([[0, 0, 0], [0, 0, 4], [2, 0, 4], [2, 0, 0], [4, 1, 4], [4, 1, 0]], float),
                      np.array([[0, 1, 2], [0, 2, 3], [3, 2, 4], [3, 4, 5]], np.uint32), np.ones(4, np.uint8))
    assert run(continuous)[1]['sealed_seams'] == 0


def test_lower_ground_faces_a_higher_structure_floor():
    meshes, report = run(sheet(0, 2, 0), [floor(2, 4, 1.2)])
    assert report['sealed_seams'] == 1 and abs(report['max_step_m'] - 1.2) < 1e-6


def test_water_edges_and_portal_clearance_stay_open():
    assert run(join(sheet(0, 2, 0), sheet(2, 4, 1)), excluded=shapely.box(1.7, -1, 2.5, 5))[1]['sealed_seams'] == 0
    opening = Openings()
    opening.add(floor(1, 3, 0), 2.5)
    # A slightly skewed seam (real seams are never exactly axis-aligned).
    low = Mesh(np.array([[0, 0, 0], [0, 0, 4], [2.01, 0, 4], [2, 0, 0]], float),
               np.array([[0, 1, 2], [0, 2, 3]], np.uint32), np.ones(2, np.uint8))
    high = Mesh(np.array([[2, 1, 0], [2.01, 1, 4], [4, 1, 4], [4, 1, 0]], float),
                np.array([[0, 1, 2], [0, 2, 3]], np.uint32), np.ones(2, np.uint8))
    meshes, report = run(join(low, high), openings=opening)
    # A seam on a passage footprint is left to the portal: no face may close it.
    assert report['sealed_seams'] == 0 and meshes == []
    away = Openings()
    away.add(floor(3.5, 4, 1), 2.5)
    assert run(join(low, high), openings=away)[1]['sealed_seams'] == 1


def test_audit_counts_open_seams_between_samples():
    visual = np.concatenate([join(sheet(0, 2, 0), sheet(2, 4, 1)).positions[join(sheet(0, 2, 0), sheet(2, 4, 1)).indices]])
    g = np.arange(.25, 4, .5)
    q = np.array([[x, z] for x in g for z in g])
    expected = np.where(q[:, 0] < 2, 0., 1.)
    assert len(audit(visual, visual, q, expected)['defects']['open_seams']) == 8
    walled = np.concatenate([visual, wall(2, 0, 1).positions[wall(2, 0, 1).indices]])
    assert audit(walled, walled, q, expected)['counts']['open_seams'] == 0


def test_clean_duplicate_and_zero_area_faces():
    m = sheet(0, 4, 0)
    m.indices = np.r_[m.indices, m.indices[:1], [[0, 0, 1]]]
    m.material = np.ones(4, np.uint8)
    assert len(clean(m).indices) == 2
    assert len(boundary(clean(sheet(0, 4, 0)))[0]) == 4


def test_audit_ignores_a_deck_edge_over_continuous_ground():
    ground, deck = sheet(0, 4, 0), sheet(2, 4, 3)
    visual = np.concatenate([ground.positions[ground.indices], deck.positions[deck.indices]])
    g = np.arange(.25, 4, .5)
    q = np.array([[x, z] for x in g for z in g])
    expected = np.where(q[:, 0] < 2, 0., 3.)
    assert audit(visual, visual, q, expected)['counts']['open_seams'] == 0
