"""Trees and planting of the world pipeline: the generated meshes and the compiled rows."""

import numpy as np

from turku3d.world.green import hedgegen, treegen
from turku3d.world.kinds import planting, trees


def open_edges(triangles: np.ndarray, positions: np.ndarray | None = None) -> int:
    """Edges not shared by exactly two triangles; with positions, vertices at one place count as one (UV seams)."""
    if positions is not None:
        _, weld = np.unique(np.round(positions, 3), axis=0, return_inverse=True)
        triangles = weld.reshape(-1)[triangles]
        triangles = triangles[(triangles[:, 0] != triangles[:, 1]) & (triangles[:, 1] != triangles[:, 2]) & (triangles[:, 0] != triangles[:, 2])]
    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts != 2).sum())


def volume(positions: np.ndarray, triangles: np.ndarray) -> float:
    p = positions - positions.mean(axis=0)
    return float(np.einsum("ij,ij->i", p[triangles[:, 0]], np.cross(p[triangles[:, 1]], p[triangles[:, 2]])).sum() / 6)


def test_a_library_tree_is_closed_bark_and_leaves_at_the_measured_size():
    shape = treegen.Shape("lime", 16.0, 5.0, 4.0)
    bark, leaves = treegen.build(shape, 0)
    assert bark.material.family == "bark" and leaves.material.family == "foliage"
    assert open_edges(bark.triangles, bark.positions) == 0
    assert leaves.material.surface == "masked" and leaves.material.params["uv"] == "atlas"
    top = float(np.percentile(leaves.positions[:, 1], 99.5))
    reach = float(np.percentile(np.hypot(leaves.positions[:, 0], leaves.positions[:, 2]), 90))
    assert abs(top - 16.0) < 1.0 and abs(reach - 5.0) < 0.6
    far = treegen.build(shape, 1)[1]
    assert 0.02 < len(far.triangles) / len(leaves.triangles) < 0.06  # the far level: a prefix of the cards, unfolded


def test_a_hedge_is_a_closed_solid_of_its_measured_section():
    points = np.stack([np.linspace(0, 10, 21), np.zeros(21), np.zeros(21)], axis=1)
    body = hedgegen.sweep(points, np.full(21, 1.0), np.full(21, 1.5), "clipped", np.random.default_rng(1))
    assert open_edges(body["triangles"]) == 0
    assert 10 * 1.0 * 1.5 * 0.8 < volume(body["positions"], body["triangles"]) < 10 * 1.0 * (1.5 + hedgegen.SINK) * 1.05
    ring = np.array([[0, 0], [4, 0], [4, 3], [0, 3]], float)
    for outline in (ring, ring[::-1]):
        dome = hedgegen.mass(outline, 0.0, 1.2, np.random.default_rng(2))
        assert open_edges(dome["triangles"]) == 0 and volume(dome["positions"], dome["triangles"]) > 6.0


def tree(tree_id, e, n, height, width, family="lime"):
    return {"id": tree_id, "e": e, "n": n, "ground": 10.0, "source": "lidar", "family": family, "age": "mature", "seed": 5,
            "height": height, "crown": {"major": width, "minor": width * 0.9, "angle": 30.0, "base": 0.3 * height}}


def test_trees_compile_to_one_fixed_instance_row_each_of_shared_models():
    description = {"cell": [0, 0], "trees": [tree("a", 23459510.0, 6704490.0, 15.0, 9.0), tree("b", 23459520.0, 6704480.0, 17.5, 10.5),
                                             tree("c", 23459530.0, 6704470.0, 6.0, 3.0, "rowan")]}
    output = trees.Trees().compile(description)
    near = [item for item in output.instances if item.lod == 0]
    assert sorted(tree_id for item in near for tree_id in item.ids) == ["a", "b", "c"]
    assert len(near) == 2  # the two limes share a model
    assert {(part.name, part.lod) for part in output.parts} == {("bark", 1), ("foliage", 1)}  # the far level: two meshes
    for item in output.instances:
        assert item.lod == 0 and item.behaviour == "fixed" and item.collision["shape"] == "cylinder"
        assert np.all(item.transforms[:, 4:] > 0.7) and np.all(item.transforms[:, 4:] < 1.45)


def census_row(tree_id, e, n, height, **more):
    return {"id": tree_id, "e": e, "n": n, "ground": 10.0, "source": "lidar", "height": height, "width": 0.5 * height, "major": 0.55 * height,
            "minor": 0.45 * height, "angle": 0.0, "base": 0.3 * height, "widestAt": 0.5, "returns": 900, "through": 0.9, "green": 0.01,
            "top": [e, n], **more}


def test_a_row_of_unknown_trees_is_one_species_and_a_street_crown_the_register_dropped_is_not_built():
    row = [census_row(f"lidar-{k}", 23459500.0 + 9.0 * k, 6704500.0, 12.0 + 0.3 * k) for k in range(6)]
    named = census_row("puut-1", 23459500.0 + 54.0, 6704500.0, 13.0, source="register+lidar", register={"species": "Acer platanoides"})
    chosen = trees.families([*row, named])
    assert {chosen[item["id"]][0] for item in row} == {"maple"}
    assert {family for family, _ in trees.families(row).values()} == {"lime"}  # a planted row of unknown species
    assert len({family for family, _ in trees.families(row[:3]).values()}) == 1
    stub = trees.Stub("trees/c_0_0", (0, 0, 1, 1), [], {"cell": [0, 0], "trees": [*row, named], "absent": [], "cameras": [],
                                                       "onStreet": {"lidar-0": 4.0, "lidar-1": 1.0}})
    description = trees.Trees().describe(stub, None)
    assert description["felled"] == ["lidar-0"]  # its crown (3 m) does not reach off the street; lidar-1 leans over from a garden
    assert "lidar-0" not in {tree["id"] for tree in description["trees"]} and len(description["trees"]) == 6


def test_planting_is_instance_rows_near_and_one_merged_mesh_far_and_stores_no_sprays():
    hedge = {"id": "hedge-x", "source": "lidar", "line": [[23459510.0, 6704490.0], [23459518.0, 6704490.0]], "ground": [10.0, 10.2],
             "width": [1.0, 1.2], "height": [1.4, 1.4], "form": "loose", "evergreen": False, "seed": 3, "measured": {"level": 0.2, "even": 0.2}}
    shrubs = [{"id": f"s-{k}", "e": 23459520.0 + 3 * k, "n": 6704480.0, "ground": 10.0, "family": "lilac", "height": 1.8, "width": 2.0, "seed": 9}
              for k in range(2)]
    output = planting.Planting().compile({"cell": [0, 0], "hedges": [hedge], "masses": [], "shrubs": shrubs})
    assert all(item.lod == 0 for item in output.instances) and all(part.lod == 1 for part in output.parts)
    own, library = output.instances[0], output.instances[1:]
    assert own.transforms.tolist() == [[0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]] and [part.name for part in own.asset] == ["body-leafy"]
    assert len(library) == 1 and len(library[0].transforms) == 2  # two shrubs of one size share a library model
    body = own.asset[0]
    assert body.material.family == "hedge" and open_edges(body.triangles) == 0
    sprays = body.material.params["sprays"]
    assert sprays["share"] == 1.0 and sprays["dense"] == 1.0 and len(sprays["cell"]) == 4
    density, loose, place = body.attributes["spray"].T
    assert set(loose) == {1.0} and density.max() == hedgegen.DENSITY["loose"] and density.min() == 0.0 and set(place) == {0.0}
    names = {part.name for part in output.parts}
    assert names == {"body-leafy", "foliage"}  # far: the coarse body and one leaf mesh (the shrubs' far level); no stored hedge cards
    far_body = next(part for part in output.parts if part.name == "body-leafy")
    assert far_body.material.params["sprays"]["share"] == hedgegen.FAR[0]


def test_a_garden_conifer_is_thuja_columns_and_no_lidar_crown_stands_in_a_carriageway():
    conifer = census_row("lidar-t", 23459500.0, 6704500.0, 5.0, green=0.2, major=6.0, minor=2.0, centre=[23459500.0, 6704500.0])
    in_road = census_row("lidar-r", 23459520.0, 6704500.0, 8.0)
    at_kerb = census_row("lidar-k", 23459540.0, 6704500.0, 8.0)
    street_tree = census_row("puut-2", 23459560.0, 6704500.0, 12.0, source="register+lidar", register={"species": "Tilia"})
    garden = census_row("lidar-g", 23459580.0, 6704500.0, 4.2)
    stub = trees.Stub("trees/c_0_0", (0, 0, 1, 1), [], {
        "cell": [0, 0], "trees": [conifer, in_road, at_kerb, street_tree, garden], "absent": [], "cameras": [], "onStreet": {},
        "carriageway": {"lidar-r": {"d": -3.5, "to": [23459520.0, 6704504.1]}, "lidar-k": {"d": -0.5, "to": [23459540.0, 6704501.1]},
                        "puut-2": {"d": 1.0}}})
    description = trees.Trees().describe(stub, None)
    found = {tree["id"]: tree for tree in description["trees"]}
    columns = [tree for key, tree in found.items() if key.startswith("lidar-t-")]
    assert len(columns) == 3 and all(tree["family"] == "thuja" and tree["crown"]["major"] <= 0.55 * 6.5 * 1.1 for tree in columns)
    assert max(tree["e"] for tree in columns) - min(tree["e"] for tree in columns) < 0.1  # along the crown's long axis: north
    assert description["felled"] == ["lidar-r"]  # 3.5 m inside the carriageway: out of its crown's reach of the edge
    assert found["lidar-k"]["n"] == 6704501.1 and found["lidar-k"]["moved"]  # its top leant over the kerb
    assert found["puut-2"]["crown"]["base"] >= trees.ROAD_CLEAR  # a street tree's boughs clear the lorries
    assert found["lidar-g"]["height"] == 4.7  # a garden tree gained a decimetre a year, not a street sapling's 0.35


class FakeWindow:
    """A lidar window of bare flat ground with the given shrub-layer returns."""

    def __init__(self, box, e, n, h):
        from turku3d.world.green import lidar

        self.box, self.col0, self.row0 = box, int(box[0] / lidar.GRID), int(box[1] / lidar.GRID)
        shape = (int((box[3] - box[1]) / lidar.GRID), int((box[2] - box[0]) / lidar.GRID))
        self.roof = np.zeros(shape, np.float32)
        count = len(e)
        self.returns = lidar.Returns(np.asarray(e, float), np.asarray(n, float), np.asarray(h, np.float32), np.full(count, 4, np.uint8),
                                     np.tile(np.array([[90, 100, 80]], np.uint8), (count, 1)), np.ones(count, bool))

    def ground_height(self, e, n):
        return np.zeros(np.shape(e)) + 10.0


def test_a_hedge_both_maps_draw_is_built_once_and_whole_though_the_leafless_scan_shows_it_thinly():
    from turku3d.world.green import hedges

    rng = np.random.default_rng(4)
    along = rng.uniform(2.0, 9.0, 60)  # returns on the first metres only, eight a metre: no density test finds a hedge
    window = FakeWindow((0.0, 0.0, 40.0, 40.0), 10.0 + along, 20.0 + rng.normal(0.0, 0.3, 60), rng.uniform(0.5, 1.5, 60))
    mapped = [{"line": [[10.0, 20.0], [30.0, 20.0]], "source": "kanta:Pensasaita", "key": "a"},
              {"line": [[10.5, 20.4], [30.0, 20.5]], "source": "osm", "key": "1"},
              {"line": [[10.0, 30.0], [30.0, 30.0]], "source": "osm", "key": "2"}]
    found = hedges.measure((0.0, 0.0, 40.0, 40.0), window, mapped)["hedges"]
    assert [hedge["source"] for hedge in found] == ["kanta:Pensasaita"]  # not the same hedge again, nor a line without returns
    line = np.array(found[0]["line"])
    assert line[0, 0] < 10.6 and line[-1, 0] > 29.4 and 1.0 < np.median(found[0]["height"]) < 1.6
    young = planting.hedge_row({**found[0], "height": [0.5] * len(line), "garden": True})
    assert min(young["height"]) > 1.2  # a garden hedge low in the scan has grown since


def test_a_register_bed_is_a_shrub_mass_and_nothing_grows_on_paving():
    import shapely

    from turku3d.world.green import hedgegen, hedges

    rng = np.random.default_rng(5)
    shrub = (rng.uniform(4.0, 9.0, 900), rng.uniform(4.0, 9.0, 900))  # a dense shrub layer on the lawn
    car = (rng.uniform(22.0, 26.0, 600), rng.uniform(4.0, 6.0, 600))  # and the same on the asphalt: a parked car
    grown = (rng.uniform(30.0, 50.0, 4000), rng.uniform(20.0, 26.0, 4000))  # a bed the scan shows planted all over
    window = FakeWindow((0.0, 0.0, 60.0, 40.0), np.concatenate([shrub[0], car[0], grown[0]]), np.concatenate([shrub[1], car[1], grown[1]]),
                        np.concatenate([rng.uniform(0.5, 1.4, 1500), rng.uniform(0.8, 1.0, 4000)]))
    beds = [{"shape": shapely.box(30.0, 20.0, 50.0, 26.0), "key": "bed", "source": "GIS:Viheralueet"},
            {"shape": shapely.box(30.0, 30.0, 39.0, 36.0), "key": "lawn", "source": "GIS:Viheralueet"}]  # nothing of this one in the scan
    found = hedges.measure((0.0, 0.0, 40.0, 40.0), window, [], beds=beds, paved=shapely.box(20.0, 0.0, 28.0, 40.0))
    lidar_only = [m for m in found["masses"] if "open" not in m]
    assert len(lidar_only) == 1 and 4.0 < np.mean(lidar_only[0]["ring"], axis=0)[0] < 9.0 and not found["shrubs"] and not found["hedges"]
    bed, unseen = sorted([m for m in found["masses"] if "open" in m], key=lambda m: m["bed"])
    assert unseen["bed"] == "lawn" and unseen["height"] == hedges.BED_DEFAULT  # planted all the same: leafless twigs return little
    ring = np.array(bed["ring"])
    assert bed["bed"] == "bed" and 0.8 < bed["height"] < 1.0 and ring[:, 0].max() == 40.0  # cut at the cell's edge
    assert [bool(flag) for flag in bed["open"]] == [bool(e == 40.0) for e in ring[:, 0]]
    body = hedgegen.mass(ring, np.array(bed["ground"]), bed["height"], np.random.default_rng(1), bed["open"])
    top = body["positions"][body["positions"][:, 1] > 10.0 + bed["height"] - 0.01]
    assert abs(top[:, 0].max() - 40.0) < 0.01 and top[:, 0].min() > 30.1  # upright on the cut, leaning in elsewhere
    density, _, place = body["spray"].T
    on_top = place == 1.0
    assert on_top.any() and (density[on_top] > density[~on_top].max()).all()  # and sprays on its flat top too, more of them
    assert (place == -1.0).sum() == on_top.sum()  # none on its underside


def test_a_meadow_grows_grass_and_flowers_on_its_ground_but_not_on_the_path_through_it():
    import shapely

    from turku3d.world.green import tufts

    class Slope(FakeWindow):
        def ground_height(self, e, n):
            return 10.0 + 0.1 * np.asarray(e, float)

    box = (0.0, 0.0, 62.5, 62.5)
    assert tufts.kind_of("Niitty", "A4", "Niitty") == "m" and tufts.kind_of("Viheralue", "M2", "Metsä") == "f" and tufts.kind_of("Viheralue", "R4", "Nurmikko") is None
    cover = tufts.cover(box, Slope((-16.0, -16.0, 78.5, 78.5), [], [], []), [("m", shapely.box(0, 0, 30, 62.5)), ("f", shapely.box(0, 0, 62.5, 62.5))],
                        shapely.box(10, 0, 14, 62.5))
    assert cover["rows"][5][:8] == "mmmmmmmm" and cover["rows"][5][9:11] == ".." and cover["rows"][5][-1] == "f"
    ground = tufts.ground(cover)  # the covered squares, over which the client grows the cards
    square = ground["positions"].reshape(-1, 4, 3)
    middle, kind = square.mean(axis=1), ground["tuft"][0::4]
    assert not ((middle[:, 0] > 10.7) & (middle[:, 0] < 13.3)).any()  # the path is bare
    assert np.abs(ground["positions"][:, 1] - (10.0 + 0.1 * ground["positions"][:, 0])).max() < 0.05  # on the slope
    assert set(kind[middle[:, 0] < 30]) == {0.0} and set(kind[middle[:, 0] > 31]) == {1.0}  # meadow, then forest floor
    plants = tufts.material().params["tufts"]["plants"]
    assert plants[0][0]["density"] > 1.8 * plants[1][0]["density"] and tufts.material(far=True).params["tufts"]["share"] < 0.4
    assert tufts.cover(box, FakeWindow((-16.0, -16.0, 78.5, 78.5), [], [], []), [("m", shapely.box(100, 100, 120, 120))], None) is None


def test_nothing_of_a_tree_reaches_into_a_carriageway_under_lorry_height():
    from turku3d.world.green import treegen

    # A model fills its measured crown and no more: every spray and bough inside the envelope, none under the base.
    for shape in (treegen.Shape("spruce", 16.0, 4.8, 2.6), treegen.Shape("maple", 10.0, 4.6, 2.8), treegen.Shape("birch", 16.0, 4.8, 4.5)):
        floor, reach = treegen.envelope(shape)
        for part in treegen.build(shape, 0):
            crown = part.positions[np.hypot(part.positions[:, 0], part.positions[:, 2]) > 1.0]  # off the stem
            assert crown[:, 1].min() >= floor - 0.01 and np.hypot(crown[:, 0], crown[:, 2]).max() <= reach + 0.01, (shape, part.name)
    spruce = census_row("lidar-s", 23459500.0, 6704500.0, 14.0, green=0.2, major=8.0, minor=7.0)
    apple = census_row("lidar-a", 23459520.0, 6704500.0, 4.0, major=5.0, minor=4.0, base=0.6)
    lime = census_row("puut-3", 23459540.0, 6704500.0, 9.0, source="register+lidar", register={"species": "Tilia"})
    hedge = census_row("lidar-t", 23459560.0, 6704500.0, 5.0, green=0.2, major=2.0, minor=6.0, angle=90.0, centre=[23459560.0, 6704500.0])
    stub = trees.Stub("trees/c_0_0", (0, 0, 1, 1), [], {
        "cell": [0, 0], "trees": [spruce, apple, lime, hedge], "absent": [], "cameras": [], "onStreet": {},
        "carriageway": {"lidar-s": {"d": 2.5, "b": 0.0}, "lidar-a": {"d": 1.0, "b": 0.0}, "puut-3": {"d": 0.8, "b": 0.0}, "lidar-t": {"d": 1.6, "b": 90.0}}})
    found = {tree["id"]: tree for tree in trees.Trees().describe(stub, None)["trees"]}
    for key, room in (("lidar-s", 2.5), ("lidar-a", 1.0)):
        crown = found[key]["crown"]  # the carriageway lies north: the short side faces it, the long side lies east to west
        assert crown["angle"] == 90.0 and crown["major"] > crown["minor"]
        assert treegen.ENVELOPE[0] * crown["minor"] / 2 <= room + trees.OVERHANG + 0.01
    lifted = found["puut-3"]
    assert lifted["crown"]["clear"] and trees.model_of(lifted).base / trees.model_of(lifted).height >= 0.45  # the class above, not the nearest
    columns = [tree for key, tree in found.items() if key.startswith("lidar-t")]
    assert columns and len(columns) < 3  # the column that would stand in the carriageway east of the row is left out
    assert all(tree["e"] + treegen.ENVELOPE[0] * tree["crown"]["major"] / 2 <= 23459561.6 + trees.OVERHANG + 0.01 for tree in columns)
