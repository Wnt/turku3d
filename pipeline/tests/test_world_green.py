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


def test_planting_compiles_hedges_to_parts_and_shrubs_to_instances():
    hedge = {"id": "hedge-x", "source": "lidar", "line": [[23459510.0, 6704490.0], [23459518.0, 6704490.0]], "ground": [10.0, 10.2],
             "width": [1.0, 1.2], "height": [1.4, 1.4], "form": "loose", "evergreen": False, "seed": 3, "measured": {"level": 0.2, "even": 0.2}}
    shrub = {"id": "s-1", "e": 23459520.0, "n": 6704480.0, "ground": 10.0, "family": "lilac", "height": 1.8, "width": 2.0, "seed": 9}
    output = planting.Planting().compile({"cell": [0, 0], "hedges": [hedge], "masses": [], "shrubs": [shrub]})
    names = {(part.name, part.lod) for part in output.parts}
    assert {("body-leafy", 0), ("leaves-leafy", 0), ("body-leafy", 1)} <= names
    body = next(part for part in output.parts if part.name == "body-leafy" and part.lod == 0)
    assert body.material.family == "hedge" and open_edges(body.triangles) == 0
    assert {("shrub-foliage", 0), ("shrub-bark", 0), ("shrub-foliage", 1)} <= names and not output.instances
