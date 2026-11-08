"""Forest floors: closed canopy in mapped woodland becomes litter, needle duff or moss; open ground keeps its lawn."""

from types import SimpleNamespace

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import forest_floor as F
from turku3d.coarse import ground_finishes, surfaces
from turku3d.coarse.street_materials import FOREST_BROADLEAF, FOREST_CONIFER, FOREST_MOSS

TILE = C.VisualTile.parse("t0_0_0")
FLAT = SimpleNamespace(many=lambda x, z: np.zeros(len(np.asarray(x))))


def canopy(x0, z0, x1, z1, step=1.0, top=15.0):
    """Class-5 returns over a box (engine frame around TILE), one per ``step`` metres."""
    x, z = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step))
    return np.c_[x.ravel(), np.full(x.size, top), z.ravel()]


def tree(x, z, variant="tree_detailed_fall"):
    return SimpleNamespace(x=x, z=z, variant=variant)


def floors(forests, points, trees=(), water=(), excluded=None):
    return dict((m, g) for g, m in F.regions(TILE, forests, points, FLAT, list(trees), list(water), excluded))


def test_closed_canopy_in_woodland_is_litter_and_open_woodland_keeps_its_lawn():
    wood = shapely.box(0, 0, 250, 250)
    # Closed canopy over the north half; a lone crown in the south is a park tree over lawn.
    points = np.r_[canopy(0, 0, 250, 125), canopy(180, 200, 186, 206)]
    result = floors([("area:0", wood, "wood", "")], points)
    assert set(result) == {FOREST_BROADLEAF}
    litter = result[FOREST_BROADLEAF]
    assert litter.contains(shapely.Point(125, 60)) and not litter.contains(shapely.Point(183, 203))
    # The edge follows the canopy share within RADIUS, not the crowns' outline.
    assert 125 - F.RADIUS - 2 <= litter.bounds[3] <= 125 + F.RADIUS / 2
    # Not woodland (a park): no floor even under closed canopy.
    assert floors([("area:1", wood, "park", "")], points) == {}
    # Canopy lower than CANOPY_MIN (scrub) is not a closed forest roof.
    assert floors([("area:0", wood, "wood", "")], canopy(0, 0, 250, 125, top=3.0)) == {}


def test_needles_follow_leaf_type_or_the_trees_and_wet_ground_is_moss():
    wood = shapely.box(0, 0, 250, 250)
    points = canopy(0, 0, 250, 250)
    pines = [tree(x, z, "tree_pineDefaultA") for x in range(150, 250, 6) for z in range(0, 250, 6)]
    oaks = [tree(x, z) for x in range(0, 120, 6) for z in range(0, 250, 6)]
    lake = shapely.box(-40, 200, 0, 250)
    result = floors([("area:0", wood, "forest", "")], points, pines + oaks, [lake])
    assert result[FOREST_CONIFER].contains(shapely.Point(200, 100))
    assert result[FOREST_BROADLEAF].contains(shapely.Point(60, 100))
    assert result[FOREST_MOSS].contains(shapely.Point(5, 230)) and not result[FOREST_MOSS].contains(shapely.Point(60, 100))
    # The tag decides only where no tree stands within RADIUS: the trees' leaf type is measured.
    tagged = floors([("area:0", wood, "forest", "needleleaved")], points, oaks)
    assert tagged[FOREST_BROADLEAF].contains(shapely.Point(60, 100))
    assert tagged[FOREST_CONIFER].contains(shapely.Point(200, 100))
    assert set(floors([("area:0", wood, "forest", "needleleaved")], points)) == {FOREST_CONIFER}
    # A hollow 1.5 m below its surroundings collects water: moss.
    bowl = SimpleNamespace(many=lambda x, z: -1.5 * np.exp(-((np.asarray(x) - 125) ** 2 + (np.asarray(z) - 125) ** 2) / 200))
    wet = dict((m, g) for g, m in F.regions(TILE, [("area:0", wood, "forest", "")], points, bowl, oaks, []))
    assert wet[FOREST_MOSS].contains(shapely.Point(125, 125))


def test_mapped_surfaces_and_the_tile_edge_bound_the_floor():
    wood = shapely.box(-50, -50, 300, 300)
    path = shapely.LineString([(0, 125), (250, 125)]).buffer(1.5)
    result = floors([("area:0", wood, "wood", "")], canopy(-30, -30, 280, 280), excluded=path)
    litter = result[FOREST_BROADLEAF]
    assert not litter.intersects(shapely.Point(100, 125))
    assert litter.within(shapely.box(*TILE.bbox.as_tuple()).buffer(1e-6))
    assert abs(litter.area - (250 * 250 - path.intersection(shapely.box(0, 0, 250, 250)).area)) < 300
    assert floors([("area:0", wood, "wood", "")], np.empty((0, 3))) == {}


def test_forest_floors_are_procedural_visual_finishes_bound_by_key():
    for key in ground_finishes.FOREST_FLOORS:
        assert key in ground_finishes.PROCEDURAL and ground_finishes.PERIODS[key] == 4
        albedo = ground_finishes.maps(key, 96)["albedo"].astype(float) / 255
        linear = ((albedo + .055) / 1.055) ** 2.4
        # Dark, low forest-floor albedo; litter and duff brown (red over blue), moss green.
        mean = linear.reshape(-1, 3).mean(axis=0)
        assert .02 < mean.mean() < .12 and mean[0] > mean[2] and (key != "forest_floor_moss" or mean[1] > mean[0])
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float)
    for finish, key in ((FOREST_BROADLEAF, "forest_floor_broadleaf"), (FOREST_CONIFER, "forest_floor_conifer"),
                        (FOREST_MOSS, "forest_floor_moss")):
        mesh = surfaces.visuals(TILE, p, np.array([[0, 2, 1]]), np.array([finish]), np.array([True]))[0]
        assert mesh.extras["material_key"] == "draft_" + key
