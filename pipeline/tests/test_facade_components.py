"""Facade solids replace wall paint and agree with collision, including rotated walls."""

import copy
from types import SimpleNamespace

import numpy as np
import pytest
from turku3d import config as C
from turku3d.facade_component_geometry import TRI, geometry, plan, surface_parts
from turku3d.facade_components import components
from turku3d.package.gltf import GlbMesh
from turku3d.wall_surfaces import validate_meshes


def fixture(kind, **kwargs):
    vertices = np.array([[0, 0, 0], [12, 0, 0], [12, 12, 0], [0, 12, 0]], float)
    desc = dict(
        kind=kind,
        bounds=[0.15, 0.05, 0.7, 0.85],
        depth_m=1.1,
        rows=3,
        count=2,
        head="flat",
        rise_m=2.4,
        run_m=5,
        direction=1,
        **kwargs,
    )
    wall = dict(
        width_m=12,
        height_m=12,
        geometry_components=[desc],
        geometry=dict(p0=[C.ORIGIN_E, C.ORIGIN_N], p1=[C.ORIGIN_E + 12, C.ORIGIN_N], base_h=0),
    )
    return vertices, wall


@pytest.mark.parametrize(
    "kind", ["bay", "recess", "balcony", "glazed_balcony", "loggia", "veranda", "arcade", "stairs"]
)
def test_components_pass_wall_invariant_and_have_outward_box_normals(kind, tmp_path):
    vertices, wall = fixture(kind)
    plans = plan(vertices, wall)
    meshes, boxes = geometry(vertices, plans, "test", np.zeros(3))
    from turku3d.layers import write_visual

    write_visual(tmp_path / f"{kind}.vis.glb", meshes)
    ids = {m.extras["object_id"] for m in meshes}
    assert len(ids) == len(meshes)
    other, _ = geometry(vertices + [0, 0, 20], plans, "test", np.zeros(3))
    assert ids.isdisjoint(m.extras["object_id"] for m in other)
    parts = list(surface_parts(vertices, vertices[:, :2], plans))
    meshes += [GlbMesh("wall", p, TRI, uvs=uv) for p, uv in parts]
    assert meshes
    validate_meshes(meshes, "test")
    for mesh in meshes:
        box = mesh.extras.get("component_box")
        if box:
            tris = mesh.positions[mesh.indices]
            normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
            assert np.all(np.sum(normals * (tris.mean(axis=1) - box["center"]), axis=1) > 0)
    if kind in {"bay", "balcony", "glazed_balcony", "loggia", "veranda", "arcade", "stairs"}:
        assert boxes
    before = copy.deepcopy(wall)
    plan(vertices, wall)
    assert wall == before


def test_arcade_opens_collision_but_upper_recess_does_not():
    for kind in ["arcade", "loggia", "recess", "bay"]:
        v, w = fixture(kind)
        plans = plan(v, w)
        geometry(v, plans, "test", np.zeros(3))
        parts = list(surface_parts(v, v[:, :2], plans, collision=True))
        moved = [p for p, _ in parts if abs(p[:, 2]).max() > 0.1]
        assert bool(moved) == (kind == "arcade")
        if moved:
            assert moved[0][:, 2].min() == pytest.approx(1.1)


def test_uvs_survive_rotated_wall_cut_and_displacement():
    v, w = fixture("bay")
    # Survey is along +north (engine -z), exterior is -x.
    v[:, [0, 2]] = np.c_[np.zeros(4), -v[:, 0]]
    w["geometry"]["p1"] = [C.ORIGIN_E, C.ORIGIN_N + 12]
    plans = plan(v, w)
    geometry(v, plans, "test", np.zeros(3))
    uv = np.c_[-v[:, 2] / 12, v[:, 1] / 12]
    parts = list(surface_parts(v, uv, plans))
    for p, t in parts:
        np.testing.assert_allclose(t, np.c_[-p[:, 2] / 12, p[:, 1] / 12], atol=1e-7)
    assert any(p[:, 0].max() < -0.5 for p, _ in parts)


def test_passage_fragments_are_not_bridged():
    v, w = fixture("loggia")
    v[:2, 1] = 5  # Tunnel split wall starts above the mapped passage.
    assert plan(v, w) == []


def test_adapter_abstains_on_lower_bounds_and_missing_artwork_location():
    form = dict(
        facade_observations=dict(
            sections=[
                dict(position="left", kind="glazed balconies", rows=4),
                dict(position="right", kind="glazed balconies", rows_lower_bound=3),
                dict(position="centre high", kind="clock"),
                dict(position="whole facade", kind="external stairs"),
            ]
        )
    )
    original = copy.deepcopy(form)
    result, pending = components(form, 5)
    assert [p["kind"] for p in result] == ["glazed_balcony"]
    assert len(pending) == 3
    assert form == original


def test_geometry_only_reading_keeps_paint_unreviewed():
    from turku3d.facade_sections import prepare
    from turkutex.facade_archetypes import sample

    p = sample("test", dict(levels=4), [dict(wall_id="test", width_m=12, height_m=12)])
    p["walls"][0]["structure"] = dict(
        render_deferred="depth",
        facade_observations=dict(sections=[dict(position="left", kind="recessed loggias", rows=4)]),
    )
    out = prepare(p)
    assert out["needs_review"] is False
    assert out["walls"][0]["geometry_components"][0]["kind"] == "loggia"
    assert out["walls"][0]["render_sections"] == []


def test_production_wall_meshes_and_collision_use_the_same_arcade(monkeypatch):
    import shapely
    from turku3d import facade_programs
    from turku3d.coarse.stored_facades import wall_meshes
    from turku3d.facade_component_geometry import collision_walls
    from turku3d.facade_sections import prepare
    from turkutex.facade_archetypes import sample

    v, w = fixture("arcade")
    p = sample("test", dict(levels=4), [dict(wall_id="test", width_m=12, height_m=12)])
    p["walls"][0].update(
        geometry=w["geometry"],
        structure=dict(
            render_deferred="depth",
            facade_observations=dict(
                sections=[dict(position="ground", kind="recessed arcade", column_count=3)]
            ),
        ),
    )
    observation = prepare(p)
    monkeypatch.setattr(facade_programs, "measured", lambda key: observation)
    building = SimpleNamespace(
        key="test",
        facade_key="test",
        base=0,
        top=12,
        attributes=dict(levels=4),
        footprint=shapely.box(0, 0, 12, 10),
    )
    meshes = wall_meshes(building, v, np.zeros(3))
    assert any(m.extras.get("component_box") for m in meshes)
    validate_meshes(meshes, "test")
    pos, idx = collision_walls(building, v)

    # In the centre of the arcade, both visible and collision rear walls move inward.
    def rear(points, indices):
        tris = points[indices]
        center = tris.mean(axis=1)
        return tris[
            (center[:, 1] < 3) & (center[:, 1] > 0.1) & (center[:, 0] > 3) & (center[:, 0] < 9)
        ]

    assert np.any(rear(pos, idx)[:, :, 2] > 0.5)
    assert any(np.any(rear(m.positions, m.indices)[:, :, 2] > 0.5) for m in meshes)


def test_passage_slots_require_tunnel_provenance_and_artwork_requires_position():
    from turku3d.facade_component_geometry import slots

    vertices, wall = fixture("passage")
    wall.update(wall_id="survey", observation=dict(needs_review=False))
    wall["geometry_components"][0]["bounds"] = [0, 0, 1, 0.25]
    observation = dict(needs_review=False, walls=[wall])
    vertices[:2, 1] = 3
    assert slots(vertices, observation) == []
    mapped = slots(vertices, observation, dict(floor=0, ceiling=3))
    assert len(mapped) == 1 and mapped[0]["owner"] == "mapped-passage"
    assert np.array(mapped[0]["corners"])[:, 1].max() == 3
    features, _ = components(
        dict(
            facade_observations=dict(
                sections=[dict(kind="clock", bounds=[0.4, 0.6, 0.2, 0.2], depth_m=0.04)]
            )
        ),
        4,
    )
    assert features[0]["kind"] == "artwork"
    vertices[:2, 1] = 0
    wall["geometry_components"] = features
    art = slots(vertices, observation)
    assert art[0]["owner"] == "artwork-asset-slot"
