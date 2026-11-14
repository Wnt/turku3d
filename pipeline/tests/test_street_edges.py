"""Street edges outside the centre's milieu map: concrete kerb stones and grass verges (coarse.street_edges)."""
import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.coarse import kerbs, street_edges, street_materials, surfaces
from turku3d.structures.geometry import Faces

TILE = C.VisualTile(0, 0, 0)
# One zone over the tile's west half (x < 100): the milieu map.
ZONES = [(shapely.box(-50, -50, 100, 300), "Liikekeskusta")]


def strip(x0, x1, z=50.0):
    p = np.array([[x0, 0, z], [x1, 0, z], [x1, 0, z + 1], [x0, 0, z + 1]], float)
    return p, np.array([[0, 2, 1], [0, 3, 2]], np.uint32)


def test_kerbs_outside_the_milieu_map_are_concrete_inside_granite():
    concrete = street_edges.concrete_area(TILE, ZONES)
    pos = np.concatenate([strip(10, 12)[0], strip(200, 202)[0], strip(220, 222)[0]])
    idx = np.concatenate([strip(0, 1)[1] + 4 * k for k in range(3)])
    ids = np.array([mat_id("curb")] * 4 + [mat_id("road")] * 2, np.uint8)
    out = street_edges.kerb_stone(pos, idx, ids, concrete)
    assert list(out) == [mat_id("curb")] * 2 + [street_materials.CONCRETE_CURB] * 2 + [mat_id("road")] * 2
    # The map's edge streets keep granite on both sides; no table, no change.
    assert street_edges.kerb_stone(*strip(100 + street_edges.GRANITE_REACH - 2, 100 + street_edges.GRANITE_REACH - 1),
                                   np.full(2, mat_id("curb"), np.uint8), concrete).tolist() == [mat_id("curb")] * 2
    assert street_edges.concrete_area(TILE, ()) is None
    assert street_edges.kerb_stone(pos, idx, ids, None) is ids
    # The finish draws the procedural concrete kerb, collision keeps the curb class.
    mesh = surfaces.visuals(TILE, pos, idx[2:3], np.array([street_materials.CONCRETE_CURB]), np.array([True]))[0]
    assert mesh.extras["material_key"] == "draft_concrete_curb"


def test_mapped_kerb_strips_split_at_the_milieu_map():
    faces = Faces()
    for x in (40.0, 180.0):
        p, i = strip(x, x + 2)
        faces.triangles(p, i, p[:, [0, 2]])
    meshes = [faces.mesh(f"kerbs_{TILE.id}", "curb")]
    concrete = street_edges.concrete_area(TILE, ZONES)
    glbs = kerbs.visuals(TILE, meshes, concrete)
    keys = sorted(g.extras["material_key"] for g in glbs)
    assert keys == ["draft_concrete_curb", "draft_granite_curb"]
    assert sum(len(g.indices) for g in glbs) == len(meshes[0].indices)
    assert [g.extras["material_key"] for g in kerbs.visuals(TILE, meshes)] == ["draft_granite_curb"]


def test_verges_fill_only_the_base_beside_streets_outside_the_map():
    concrete = street_edges.concrete_area(TILE, ZONES)
    window = shapely.box(*TILE.bbox.as_tuple())
    lawn = shapely.box(200, 0, 250, 250)  # the ortho saw grass there: it stays the ortho's
    base = window.difference(lawn)
    line = shapely.LineString([(0, 125), (250, 125)])
    half = street_edges._half_width({"highway": "residential"})
    assert half == 3.0  # the proven 6 m corridor is wider than the read residential carriageway
    (area, material), = street_edges.verges(TILE, [(line, half)], base, concrete)
    assert material == mat_id("grass")
    assert area.difference(shapely.box(0, 125 - half - street_edges.VERGE_M, 250, 125 + half + street_edges.VERGE_M)).area < 1e-6
    assert area.intersection(line.buffer(half, cap_style="flat")).area < 1e-6
    assert area.intersection(lawn).area < 1e-6
    assert area.intersection(shapely.box(-50, -50, 100 + street_edges.GRANITE_REACH, 300)).area < 1e-6
    expected = (200 - 100 - street_edges.GRANITE_REACH) * street_edges.VERGE_M * 2
    assert abs(area.area - expected) < 1.0
    assert street_edges.verges(TILE, [(line, half)], base, None) == []
    # Wide streets: the class's read width or the lanes, whichever is wider, keep their asphalt.
    assert street_edges._half_width({"highway": "primary", "lanes": "4"}) == 4 * street_edges.LANE_M / 2 + 0.5
    assert street_edges._half_width({"highway": "secondary"}) == 3.75
