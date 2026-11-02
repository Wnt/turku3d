"""Crown competitors see obstacles beyond tile ownership, including separate neighbour features."""
import shapely

from turku3d.config import VisualTile
from turku3d.coarse.canopy_context import prepare
from turku3d.coarse.forest_paths import read_paths
from turku3d.coarse.forests import read_areas


def test_exclusion_context_includes_neighbour_paving_water_and_lanes(tmp_path, monkeypatch):
    import producer_world as world
    monkeypatch.setenv('TURKU3D_DATA', str(tmp_path))
    streets = world.write_wfs('GIS_Katualueet', [{'Katuosalaji': 'Ajorata'}], [shapely.box(255, 80, 260, 120)])
    water = world.write_wfs('GIS_Vesialueet', [{}], [shapely.box(263, 80, 268, 90)])
    osm = world.write_osm([
        ({'highway': 'residential'}, shapely.LineString([(265, 100), (265, 120)])),
        ({'highway': 'residential', 'bridge': 'yes'}, shapely.LineString([(265, 130), (265, 150)])),
    ])
    context = prepare(VisualTile(0, 0, 0), streets, water, osm)
    assert shapely.union_all(context['water']).covers(shapely.Point(265, 85))
    lanes = shapely.union_all(context['lanes'])
    assert lanes.covers(shapely.Point(257, 100))
    assert lanes.covers(shapely.Point(265, 110))
    assert not lanes.covers(shapely.Point(265, 140))


def test_paths_and_open_exclusions_share_the_twenty_metre_canopy_halo(tmp_path, monkeypatch):
    import producer_world as world
    monkeypatch.setenv('TURKU3D_DATA', str(tmp_path))
    osm = world.write_osm([
        ({'highway': 'footway', 'width': '2'}, shapely.LineString([(265, 80), (265, 120)])),
        ({'leisure': 'pitch'}, shapely.box(260, 140, 280, 160)),
    ])
    tile = VisualTile(0, 0, 0)
    assert shapely.union_all(read_paths(tile, osm)).covers(shapely.Point(265, 100))
    open_areas = shapely.union_all([geom for _, geom, kind, _ in read_areas(tile, osm) if kind == 'open'])
    assert open_areas.covers(shapely.Point(268, 150))
    assert not open_areas.covers(shapely.Point(275, 150))
