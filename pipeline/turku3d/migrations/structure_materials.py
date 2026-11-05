"""structure-materials@1: spot rebuild after the structure-material rule (``coarse.bridge_materials``).

Tunnel walls, roofs and floors had no image (one flat grey factor). Stairs, retaining walls and
cliffs, and every bridge outside the weathered heritage set, carried a 128 px albedo so uniform
(luminance std ~4/255) that it read as untextured, and the cliff's periodic fissure as a zig-zag.
They now take the metric PBR maps (albedo, normal, ORM) through ``surface_material``; geometry,
collision and UV layout are unchanged, only the materials and the UV scale.

The affected class is the tiles that build such a structure, which the manifest counts in
``tunnels``, ``bridges`` and ``stairs`` (flights and retaining walls). A manifest with
``material_audit`` was built by a generator that already has the rule. Fence prototypes take
the same maps in a fresh build but do not select a tile here: nearly every tile has barriers."""

from .core import Rebuild


def affected(manifest: dict) -> bool:
    if "material_audit" in manifest:
        return False
    stairs = manifest.get("stairs") or {}
    return bool(
        manifest.get("tunnels")
        or manifest.get("bridges")
        or stairs.get("flights")
        or stairs.get("retaining_walls")
    )


MIGRATION = Rebuild("structure-materials", 1, affected=affected)
