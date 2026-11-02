import numpy as np
import shapely
from turku3d.landmarks.faces import architectural_faces
from turku3d.landmarks.layout import accepted_openings


def wall(left, right, z=0):
    return np.array([[left, 0, z], [right, 0, z], [right, 10, z], [left, 10, z]], float)


def spec():
    return dict(
        windows=dict(type="gothic-lancet", width_m=2, height_m=4, spacing_m=8, sill_m=2, mullions=1)
    )


def plan(face):
    return accepted_openings(
        face.width, face.high - face.low, face.low, spec(), False, face.polygon, 0
    )


def test_subdivision_through_an_opening_does_not_change_face_or_plan():
    whole = architectural_faces([wall(0, 24)])
    split = architectural_faces([wall(18, 24), wall(6, 12), wall(0, 6), wall(12, 18)])
    assert len(whole) == len(split) == 1
    assert whole[0].identity == split[0].identity
    assert plan(whole[0]) == plan(split[0])
    assert [opening[0] for opening in plan(split[0])] == [4, 12, 20]


def test_real_setbacks_and_disconnected_faces_remain_separate():
    assert len(architectural_faces([wall(0, 12), wall(12, 24, z=0.1)])) == 2
    assert len(architectural_faces([wall(0, 6), wall(12, 18)])) == 2


def test_sloping_face_rejects_opening_for_both_texture_and_geometry():
    q = wall(0, 8)
    q[2, 1] = 3
    q[3, 1] = 6
    face = architectural_faces([q])[0]
    assert plan(face) == []
    assert face.polygon.area == 36
    assert not face.polygon.covers(shapely.Point(4, 6))


def test_short_architectural_bay_gets_one_fitting_window():
    face = architectural_faces([wall(0, 6)])[0]
    assert len(plan(face)) == 1
    assert plan(face)[0][0] == 3


def test_narrow_tower_does_not_overlap_the_wide_tower_opening_row():
    from turku3d.landmarks.layout import openings

    d = dict(
        style="gothic",
        tower=dict(opening_rows=[[2, 1.2, 3, 3]], copper_start_m=20),
        roof=dict(material="slate"),
    )
    assert len(openings(3.2, 10, 0, d, True)) == 1
    assert len(openings(15, 10, 0, d, True)) == 3
