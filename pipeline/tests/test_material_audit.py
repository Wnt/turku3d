"""Untextured/flat surface audit and the structure-material rule (tunnels, stairs, cliffs)."""
from io import BytesIO
from types import SimpleNamespace

import numpy as np
from PIL import Image
from turku3d import config as C
from turku3d.coarse import bridge_materials, material_audit
from turku3d.migrations import structure_materials
from turku3d.package.gltf import GlbMesh, build_glb, read_glb

QUAD = (np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], float), np.array([[0, 1, 2]]))
UV = np.array([[0, 0], [1, 0], [1, 1]], float)


def png(pixels):
    out = BytesIO()
    Image.fromarray(np.asarray(pixels, np.uint8)).save(out, format="PNG")
    return out.getvalue()


def mesh(key, layer="structures", **kw):
    return GlbMesh(key, *QUAD, extras=dict(layer=layer, object_id=key, material_key=key), **kw)


def test_audit_counts_untextured_and_flat_but_not_exempt_surfaces():
    rng = np.random.default_rng(1)
    flat = png(np.full((16, 16, 3), 150))
    varied = png(rng.integers(0, 255, (16, 16, 3)))
    glb = build_glb([
        mesh("bare"),
        mesh("flat_wall", uvs=UV, image=flat),
        mesh("rich_wall", uvs=UV, image=varied),
        mesh("lamp", emissive=(1, 1, 1)),
        mesh("water", layer="ground"),
        mesh("night_quay_bar"),
        mesh("road_paint", layer="markings"),
    ])
    # The packaged copy moves the flat layer image to ../shared/, as the assembler does.
    report = material_audit.audit(glb, [glb])
    assert report["untextured"]["keys"] == {"structures/bare": 1}
    assert report["flat"]["triangles"] == 0  # embedded, not a shared copy
    js, _ = read_glb(glb)
    assert report["surfaces"] == len(js["meshes"]) and report["exempt_triangles"] == 4
    assert material_audit.flat_keys([glb]) == {("structures", "flat_wall")}


def test_structure_maps_are_not_flat_and_take_metric_uvs():
    for surface in bridge_materials.PALETTE:
        assert material_audit.image_std(bridge_materials.maps(surface)["image"]) >= material_audit.FLAT_STD
    kwargs = bridge_materials.surface_material("concrete", UV * 4)
    assert np.allclose(kwargs["uvs"], UV * 4 / bridge_materials.METRES)
    assert {"image", "normal_image", "orm_image"} <= kwargs.keys()
    assert np.allclose(bridge_materials.surface_material("asphalt", UV)["uvs"], UV)
    assert bridge_materials.surface_material("unobtainium", UV)["image"] == bridge_materials.maps("concrete")["image"]


def test_tunnel_surfaces_all_carry_textures_except_lamps():
    from turku3d.coarse.tunnels import Tunnels
    from turku3d.structures.geometry import Mesh

    def part(name, material, **metadata):
        m = Mesh(name, material, QUAD[0] + [0, 0, 1], QUAD[1], UV)
        m.metadata.update(metadata)
        return m

    meshes = [part("tunnel:1:left", "wall"), part("tunnel:1:roof", "wall"), part("tunnel:1:floor", "road"),
              part("tunnel:2:floor", "paving_slab"), part("tunnel:3:roof", "wall", vault=True),
              part("tunnel:1:lights", "wall", lamp=True)]
    tunnels = SimpleNamespace(meshes=meshes, ceilings=[])
    visuals = Tunnels.visuals(tunnels, C.VisualTile(0, 0, 0))
    keys = {v.name: (v.extras["material_key"], v.image is not None) for v in visuals}
    assert keys == {
        "tunnel:1:left": ("tunnel_concrete", True), "tunnel:1:roof": ("tunnel_concrete", True),
        "tunnel:1:floor": ("ground_asphalt", True), "tunnel:2:floor": ("draft_bridge_stone", True),
        "tunnel:3:roof": ("draft_bridge_stone", True), "tunnel:1:lights": ("tunnel_concrete", False),
    }
    report = material_audit.audit(build_glb(visuals), [build_glb(visuals)])
    assert report["untextured"]["triangles"] == 0


def test_structure_materials_migration_selects_structure_tiles_once():
    affected = structure_materials.affected
    assert affected({"tunnels": 2})
    assert affected({"bridges": 1})
    assert affected({"stairs": {"flights": 0, "retaining_walls": 3}})
    assert not affected({"tunnels": 0, "bridges": 0, "stairs": {"flights": 0, "retaining_walls": 0}})
    assert not affected({"tunnels": 2, "material_audit": {}})
