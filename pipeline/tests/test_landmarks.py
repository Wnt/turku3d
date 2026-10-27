"""Landmarks: allowlist validation, closed budgeted lidar shells, and the assembler replacement."""

from collections import Counter

import numpy as np
import pytest
import turku3d.landmarks as L
from shapely.geometry import Polygon
from turku3d.col.materials import mat_id
from turku3d.landmarks import allowlist, mesh, textures
from turku3d.layers import CollisionLayer
from turku3d.package.gltf import GlbMesh, build_glb, read_glb

ENTRY = """
version = 1
[[landmark]]
id = "church"
name = "Test church"
building = "B1"
method = "lidar-heightfield"
cell_m = 0.5
visual_triangles = 3000
collision_triangles = 400
wall = "brick"
wall_rgb = [146, 82, 62]
"""

# A 20 m x 10 m gabled nave, a 4 m x 4 m tower bay with a 30 m spire, a 4 m x 4 m courtyard.
NAVE = Polygon(
    [(0, 0), (20, 0), (20, 10), (12, 10), (12, 14), (8, 14), (8, 10), (0, 10)],
    [[(14, 3), (17, 3), (17, 6), (14, 6)]],
)


def church_points(n=60_000, seed=1):
    rng = np.random.default_rng(seed)
    x, z = rng.uniform(-1, 21, n), rng.uniform(-1, 15, n)
    height = 10 + 3 * (1 - np.abs(z - 5) / 5).clip(0)
    radius = np.hypot(x - 10, z - 12)
    height = np.where(radius < 2, 30 - 10 * radius, height)
    return np.column_stack((x, height, z))


def closed_and_outward(shell):
    """Every directed edge once with its reverse once; positive enclosed volume."""
    edges = Counter()
    for t in shell.indices.astype(int):
        for k in range(3):
            edges[(t[k], t[(k + 1) % 3])] += 1
    manifold = all(v == 1 and edges.get((b, a)) == 1 for (a, b), v in edges.items())
    p = shell.positions[shell.indices.astype(int)]
    volume = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6
    return manifold, volume


def test_allowlist_parses_and_the_shipped_list_is_valid():
    (church,) = allowlist.parse(ENTRY)
    assert church.building == "B1" and church.wall_rgb == (146, 82, 62)
    shipped = allowlist.load()
    assert "tuomiokirkko" in {lm.id for lm in shipped}


@pytest.mark.parametrize(
    "old, new, error",
    [
        ("visual_triangles = 3000", "visual_triangles = 90000", "visual_triangles"),
        ("collision_triangles = 400", "collision_triangles = 2001", "collision_triangles"),
        ('wall = "brick"', 'wall = "glass"', "wall must be"),
        ("wall_rgb = [146, 82, 62]", "wall_rgb = [300, 0, 0]", "wall_rgb"),
        (
            'building = "B1"',
            'building = "B1"\npolygon_en = [[0, 0], [1, 0], [0, 1]]',
            "exactly one",
        ),
        ("cell_m = 0.5", "cell_m = 0.05", "cell_m"),
        ('method = "lidar-heightfield"', 'method = "poisson"', "method"),
        ('name = "Test church"', 'name = "Test church"\nheight = 3', "unknown keys"),
    ],
    ids=["visual-cap", "collision-cap", "wall", "rgb", "two-sources", "cell", "method", "keys"],
)
def test_allowlist_rejects(old, new, error):
    with pytest.raises(ValueError, match=error):
        allowlist.parse(ENTRY.replace(old, new))


def test_allowlist_rejects_duplicates():
    twice = ENTRY + ENTRY.split("version = 1", 1)[1]
    with pytest.raises(ValueError, match="duplicate id"):
        allowlist.parse(twice)
    other = twice.replace('id = "church"', 'id = "other"', 1)
    with pytest.raises(ValueError, match="claimed twice"):
        allowlist.parse(other)


def test_heightfield_fills_gaps_and_clips_isolated_spikes():
    pts = church_points()
    keep = ~((pts[:, 0] > 2) & (pts[:, 0] < 4) & (pts[:, 2] > 2) & (pts[:, 2] < 4))
    spike = np.array([[5.1, 60.0, 5.1]])
    field = mesh.heightfield(np.vstack((pts[keep], spike)), NAVE, 0.5)
    assert not field.measured.all() and np.isfinite(field.heights).all()
    assert 11.0 < field.sample(3.0, 3.0) < 12.9  # a neighbour of the gap, not 0 or the max
    assert field.sample(5.1, 5.1) < 14
    assert field.heights.max() > 28  # the spire is not a spike: its neighbours rise with it
    with pytest.raises(ValueError, match="no lidar"):
        mesh.heightfield(pts[:0], NAVE, 0.5)


@pytest.mark.parametrize("budget", [120, 400, 2000, 6000])
def test_shell_is_closed_and_never_exceeds_its_budget(budget):
    field = mesh.heightfield(church_points(), NAVE, 0.5)
    shell = mesh.shell(field, NAVE, 0.0, budget)
    assert len(shell.indices) <= budget
    manifold, volume = closed_and_outward(shell)
    assert manifold and volume > 0
    assert set(np.unique(shell.part)) == {mesh.TOP, mesh.WALL, mesh.BASE}
    assert shell.positions[:, 1].min() == 0.0


def test_more_budget_means_less_error_and_the_spire_survives():
    field = mesh.heightfield(church_points(), NAVE, 0.5)
    coarse = mesh.shell(field, NAVE, 0.0, 400)
    fine = mesh.shell(field, NAVE, 0.0, 6000)
    assert fine.max_error_m < coarse.max_error_m
    assert fine.max_error_m <= 0.5
    assert fine.positions[:, 1].max() > 28


def test_shell_refuses_a_budget_below_the_footprint_box():
    field = mesh.heightfield(church_points(), NAVE, 0.5)
    with pytest.raises(ValueError, match="below the"):
        mesh.shell(field, NAVE, 0.0, 30)


def test_build_landmark_measures_and_splits_surfaces(monkeypatch):
    (church,) = allowlist.parse(ENTRY)
    ground = np.array([[x, 0.2, z] for x in (-2.0, 22.0) for z in (-2.0, 16.0)])
    monkeypatch.setattr(
        L, "read_points", lambda paths, poly, classes, base: (church_points(), ground)
    )
    image = np.full((40, 80, 3), 120, np.uint8)
    monkeypatch.setattr(textures, "ortho_crop", lambda vrt, b, r: (image, b))
    tile = L.owner(NAVE)
    built = L.build_landmark(church, NAVE, tile)
    record = built["record"]
    assert record["visual"]["triangles"] <= 3000 and record["collision"]["triangles"] <= 400
    assert record["base_m"] == pytest.approx(0.2 - L.BASE_BELOW_GROUND_M)
    assert record["top_m"] > 28
    roof, walls = built["meshes"]
    assert roof.extras["material_key"] == "landmark_ortho" and walls.repeat
    assert walls.extras["material_key"] == "landmark_brick"
    assert roof.extras["building_id"] == "B1" and roof.extras["layer"] == "landmarks"
    collision = built["collision"]
    collision.validate()
    assert set(np.unique(collision.material)) == {mat_id("roof"), mat_id("building")}
    js, _ = read_glb(build_glb(built["meshes"]))
    assert {s["wrapS"] for s in js["samplers"]} == {33071, 10497}


def test_a_boat_sits_a_draft_below_the_water_around_it(monkeypatch):
    (boat,) = allowlist.parse(
        ENTRY.replace(
            'building = "B1"',
            "polygon_en = [[23459500, 6704500], [23459510, 6704500], "
            "[23459510, 6704496], [23459500, 6704496]]",
        ).replace('wall = "brick"', 'wall = "render"\nclasses = [1]\nbase = "water"')
    )
    polygon = L.footprint(boat, None)
    assert polygon.bounds == pytest.approx((0, 0, 10, 4))
    deck = np.array([[x, 2.5, z] for x in np.arange(0, 10, 0.2) for z in np.arange(0, 4, 0.2)])
    water = np.array([[-3.0, 0.2, 2.0], [13.0, 0.1, 2.0], [5.0, 0.15, -3.0]])
    seen = {}

    def points(paths, poly, classes, base):
        seen.update(classes=classes, base=base)
        return deck, water

    monkeypatch.setattr(L, "read_points", points)
    image = np.full((8, 8, 3), 90, np.uint8)
    monkeypatch.setattr(textures, "ortho_crop", lambda vrt, b, r: (image, b))
    record = L.build_landmark(boat, polygon, L.owner(polygon))["record"]
    assert seen == {"classes": (1,), "base": "water"}
    assert record["base_m"] == pytest.approx(0.15 - L.HULL_DRAFT_M)
    assert record["building"] is None


def test_wall_image_is_seamless_and_tinted():
    brick = textures.wall_image("brick", (146, 82, 62), seed=3).astype(float)
    assert brick.shape == (textures.WALL_PX, textures.WALL_PX, 3)
    # The wrap seam is no stronger than the strongest step inside (a mortar joint).
    for image in (brick, brick.transpose(1, 0, 2)):
        steps = np.abs(np.diff(image, axis=0)).mean(axis=(1, 2))
        assert np.abs(image[0] - image[-1]).mean() <= steps.max()
    assert brick[..., 0].mean() > brick[..., 2].mean()
    render = textures.wall_image("render", (200, 200, 190), seed=3)
    assert abs(render.mean() - 197) < 8


def test_assembler_drops_replaced_building_shells(tmp_path):
    from turku3d.package.assemble import drop_replaced, replaced_buildings

    root = tmp_path / "layers"
    (root / "buildings").mkdir(parents=True)
    box = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    tris = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], np.uint32)
    positions = np.vstack((box, box + 5))
    indices = np.vstack((tris, tris + 4)).astype(np.uint32)
    material = np.full(8, mat_id("building"), np.uint8)
    np.savez_compressed(
        root / "buildings/c_0_0.col.npz",
        positions=positions,
        indices=indices,
        material=material,
        building_ids=np.array(["B1", "B2"]),
        building_index=np.repeat(np.arange(2, dtype=np.uint32), 4),
    )
    manifests = {"buildings": {"params": {}}, "landmarks": {"params": {"replaces": ["B1"]}}}
    collision = {"buildings": {"c_0_0": CollisionLayer(positions, indices, material)}}
    assert replaced_buildings(manifests) == ["B1"]
    drop_replaced(root, manifests, collision)
    kept = collision["buildings"]["c_0_0"]
    kept.validate()
    assert len(kept.indices) == 4 and np.allclose(kept.positions, box + 5)
    untouched = {"buildings": {"c_0_0": CollisionLayer(positions, indices, material)}}
    drop_replaced(root, {"buildings": {"params": {}}}, untouched)
    assert len(untouched["buildings"]["c_0_0"].indices) == 8


def test_glb_mesh_repeat_only_changes_the_sampler():
    image = textures.jpeg(np.zeros((4, 4, 3)))
    a = GlbMesh("a", np.eye(3), np.array([[0, 1, 2]]), uvs=np.zeros((3, 2)), image=image)
    js, _ = read_glb(build_glb([a]))
    assert js["samplers"] == [
        {"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}
    ]
