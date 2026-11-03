import numpy as np
import shapely
from turku3d.ground.mesh import Mesh
from turku3d.structures.geometry import Mesh as Structure
from turku3d.coarse.openings import Openings
from turku3d.coarse.surface_closure import resolve, footprint, clean
from turku3d.coarse.stairs import cut_ground


def square():
    return Mesh(np.array([[0,0,0],[0,0,4],[4,0,4],[4,0,0]],float),
                np.array([[0,1,2],[0,2,3]],np.uint32),np.ones(2,np.uint8))


def floor(x0,x1,y=0):
    return Structure('stairs:test','paving_slab',[[x0,y,0],[x0,y,4],[x1,y,4],[x1,y,0]],
                     [[0,1,2],[0,2,3]],np.zeros((4,2)))


def test_overlarge_cut_fills_only_uncovered_remainder():
    source = square()
    cut = cut_ground(source,[shapely.box(1,-1,3,5)])
    dry, solid, report = resolve(source,cut,cut,[floor(1,2)],[],Openings(),shapely.Polygon())
    assert abs(report['filled_area_m2'] - 4) < 1e-6
    assert abs(footprint(dry).area - 12) < 1e-6
    assert abs(footprint(solid).area - 12) < 1e-6
    assert report['uncovered_area_m2'] == 0
    assert footprint(dry).intersection(shapely.box(1,0,2,4)).area < 1e-8


def test_bridge_cannot_mask_missing_lower_ground():
    source = square()
    cut = cut_ground(source,[shapely.box(1,-1,3,5)])
    dry, _, report = resolve(source,cut,cut,[],[floor(1,3,5)],Openings(),shapely.Polygon())
    assert report['filled_area_m2'] == 8
    assert footprint(dry).area == 16


def test_fill_respects_water_buildings_and_portal_clearance():
    source = square()
    source.positions[:,1] = 1
    cut = cut_ground(source,[shapely.box(0,-1,4,5)])
    opening = Openings()
    opening.add(floor(1,2),2.5)
    dry, solid, _ = resolve(source,cut,cut,[floor(1,2)],[],opening,shapely.box(3,-1,4,5))
    assert footprint(dry).area == 8
    assert footprint(solid).area == 8
    assert not footprint(dry).contains(shapely.Point(1.5,2))


def test_clean_duplicate_and_zero_area_faces():
    m = square()
    m.indices = np.r_[m.indices, m.indices[:1], [[0,0,1]]]
    m.material = np.ones(4,np.uint8)
    assert len(clean(m).indices) == 2


def test_rising_deck_face_keeps_ground_below_its_raised_part():
    # One deck triangle starts at grade and rises 0.8 m: its centroid is on the
    # ground sheet, but the walkway under its raised corner must stay.
    source = square()
    deck = Structure('draft-bridge:test:deck', 'road', [[0, 0, 0], [0, 0, 4], [4, .8, 4]],
                     [[0, 1, 2]], np.zeros((3, 2)))
    dry, solid, report = resolve(source, source, source, [], [deck], Openings(), shapely.Polygon())
    assert abs(footprint(dry).area - 16) < 1e-6
    assert abs(footprint(solid).area - 16) < 1e-6
    assert report['uncovered_area_m2'] == 0


def test_closure_never_removes_emitted_ground_under_structures():
    source = square()
    dry, _, report = resolve(source, source, source, [floor(1, 3)], [], Openings(), shapely.Polygon())
    assert abs(footprint(dry).area - 16) < 1e-6
    assert report['filled_area_m2'] == 0
