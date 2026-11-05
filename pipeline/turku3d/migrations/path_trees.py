"""path-trees@1: no drawn trunk on walked or driven ground (``vegetation.clearance``, the generator's
``coarse.forests.trunk_clearance``).

Decodes the tile's vegetation instances, runs the shared rule against the tile's walked ground (the same
prepared ``trunk_ground`` input as the generator) and rewrites what it moves or drops:
- the instance TRANSLATION/SCALE/ROTATION accessors and the per-instance extras of each variant's nodes;
  a moved trunk keeps its object id (so its rotation and identity) and takes the ground height change at
  its new place from the tile's collision terrain;
- ``instance_crowns`` re-measured from the lidar for the whole tile (``trees-lidar``'s own ``crowns``), as a
  generator build measures them after this placement;
- the trunk cylinders in the collision cells.
A tile with no trunk on walked ground is unchanged.
"""

from __future__ import annotations

import posixpath
from collections import Counter

import numpy as np

from turku3d import config as C

from .core import Migration, Product, glb_chunks, glb_pack
from .furniture import splice_primitives
from .trees_lidar import accessor, crowns, tile_canopy, vegetation_nodes

MATCH = 0.05  # m: a collision trunk this close to an instance's old place is that instance's trunk


def tile_inputs(tile):
    """The rule's inputs through the generator's own loaders and source paths (``coarse.inputs._sources``):
    walked ground, water, owned building footprints."""
    from turku3d.coarse.inputs import _prepare_one, buildings
    from turku3d.coarse.sources import Sources

    sources = Sources()
    osm = sources.osm_source(tile)
    if osm is None:
        return [], []
    _, streets = sources.wfs_source("GIS:Katualueet", tile)
    walked = _prepare_one(tile, "trunk_ground", [osm, streets])
    found, lakes = sources.wfs_source("kanta:Vesikohde", tile)
    water = _prepare_one(tile, "water", [lakes, osm]) if found else []
    found, bldg = sources.wfs_source("bldg:Building_LOD2", tile)
    footprints = ([b.footprint for b in buildings(bldg, tile, context=True) if b.visual_owner == tile.id]
                  if found and bldg else [])
    return walked, [*water, *footprints]


def instances(document, binary, product, url):
    """Per variant: (nodes, translations, scales, rotations, extras lists) in accessor order."""
    out = {}
    for variant, nodes in vegetation_nodes(document).items():
        node = document["nodes"][nodes[0]]
        attributes = node["extensions"]["EXT_mesh_gpu_instancing"]["attributes"]
        arrays = {name: accessor(document, binary, product, url, attributes[name])
                  for name in ("TRANSLATION", "SCALE", "ROTATION") if name in attributes}
        out[variant] = (nodes, arrays)
    return out


class Terrain:
    """Ground heights from the tile's collision triangles (heights near a reference level)."""

    def __init__(self, product, manifest):
        from turku3d.col.format import read_col

        tris = []
        for entry in (manifest.get("collision") or {}).values():
            col = read_col(product.read(entry["url"]))
            if len(col.indices):
                tris.append((col.positions.astype(np.float64) + np.asarray(col.origin, np.float64))[col.indices])
        self.tris = np.concatenate(tris).astype(np.float64) if tris else np.empty((0, 3, 3))
        lo, hi = self.tris[:, :, [0, 2]].min(1), self.tris[:, :, [0, 2]].max(1)
        self.lo, self.hi = lo, hi

    def at(self, x, z, near):
        """Height of the triangle under (x, z) closest to ``near``, or None."""
        hit = np.flatnonzero((self.lo[:, 0] <= x) & (x <= self.hi[:, 0]) & (self.lo[:, 1] <= z) & (z <= self.hi[:, 1]))
        best = None
        for t in self.tris[hit]:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
            det = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
            if abs(det) < 1e-9:
                continue
            u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / det
            v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / det
            w = 1 - u - v
            if min(u, v, w) < -1e-6:
                continue
            y = u * ay + v * by + w * cy
            if best is None or abs(y - near) < abs(best - near):
                best = y
        return best


def _append(document, chunks, end, values):
    data = np.ascontiguousarray(values, dtype="<f4")
    pad = -end % 4
    chunks.append(b"\0" * pad)
    end += pad
    document["bufferViews"].append({"buffer": 0, "byteOffset": end, "byteLength": data.nbytes})
    chunks.append(data.tobytes())
    document["accessors"].append({"bufferView": len(document["bufferViews"]) - 1, "componentType": 5126,
                                  "count": len(data), "type": {3: "VEC3", 4: "VEC4"}[data.shape[1]]})
    return len(document["accessors"]) - 1, end + data.nbytes


def transform(product: Product) -> None:
    from turku3d.coarse.forests import trunk_base_radius
    from turku3d.vegetation import clearance

    m = product.manifest
    entry = (m.get("visual") or {}).get(product.tile)
    if not entry:
        return
    document, binary = glb_chunks(product.read(entry["url"]))
    groups = instances(document, binary, product, entry["url"])
    if not groups:
        return
    tile = C.VisualTile.parse(product.tile)
    origin = np.array([tile.origin[0], 0.0, tile.origin[2]])
    trunks, rows = [], []
    for variant, (nodes, arrays) in groups.items():
        ids = (document["nodes"][nodes[0]].get("extras") or {}).get("instance_object_ids") or []
        for k, (t, s) in enumerate(zip(arrays["TRANSLATION"], arrays["SCALE"], strict=True)):
            object_id = str(ids[k]) if k < len(ids) else ""
            bush = object_id.startswith("bush:")
            trunk = 0.0 if bush else float(np.clip(s[1] * 0.018, 0.09, 0.55))
            trunks.append(clearance.Trunk((variant, k), object_id, float(t[0] + origin[0]), float(t[2] + origin[2]),
                                          max(trunk_base_radius(variant) * float(s[0]), trunk), trunk,
                                          float(s[0])))
            rows.append((variant, k, t.astype(float), s.astype(float)))
    walked, blocked = tile_inputs(tile)
    stats = Counter()
    decisions = clearance.place(trunks, walked, blocked, tile.bbox.as_tuple(), stats)
    if not decisions:
        return

    terrain = Terrain(product, m)
    edits = []  # (old world xyz, height, radius, new world xyz | None)
    inline = "uri" not in document["buffers"][0]
    chunks, end = ([binary], len(binary)) if inline else ([], 0)
    if not inline:
        raise ValueError(f"{product.tile}: vegetation GLB without an inline BIN chunk")
    for variant, (nodes, arrays) in groups.items():
        keep, translations = [], arrays["TRANSLATION"].astype(float).copy()
        ids = (document["nodes"][nodes[0]].get("extras") or {}).get("instance_object_ids") or []
        for k in range(len(translations)):
            bush = k < len(ids) and str(ids[k]).startswith("bush:")
            decision = decisions.get((variant, k), ())
            if decision is None:
                if not bush:  # bushes have no trunk collision
                    edits.append((translations[k] + origin, arrays["SCALE"][k], None))
                continue
            keep.append(k)
            if decision == ():
                continue
            old = translations[k] + origin
            x, z = decision
            g0, g1 = terrain.at(old[0], old[2], old[1]), terrain.at(x, z, old[1])
            y = old[1] + (g1 - g0 if g0 is not None and g1 is not None else 0.0)
            translations[k] = [x - origin[0], y, z - origin[2]]
            if not bush:
                edits.append((old, arrays["SCALE"][k], translations[k] + origin))
        if len(keep) == len(translations) and all(decisions.get((variant, k), ()) == () for k in keep):
            continue
        attributes = {}
        if keep:
            for name, values in (("TRANSLATION", translations), ("SCALE", arrays["SCALE"]),
                                 ("ROTATION", arrays.get("ROTATION"))):
                if values is not None:
                    attributes[name], end = _append(document, chunks, end, np.asarray(values)[keep])
        for i in nodes:
            node = document["nodes"][i]
            extras = node.setdefault("extras", {})
            for key in ("instance_object_ids", "instance_species", "instance_crowns"):
                if isinstance(extras.get(key), list) and len(extras[key]) == len(translations):
                    extras[key] = [extras[key][k] for k in keep]
            if keep:
                node["extensions"]["EXT_mesh_gpu_instancing"]["attributes"] = attributes
            else:  # nothing left of this variant: an empty node draws nothing
                node.pop("mesh", None)
                node["extensions"].pop("EXT_mesh_gpu_instancing", None)
                if not node["extensions"]:
                    node.pop("extensions")
                extras["layer"] = "vegetation-empty"
    binary = b"".join(chunks)
    document["buffers"][0]["byteLength"] = len(binary)

    # Crowns as a generator build measures them: trees-lidar over the final placement.
    if any("instance_crowns" in (document["nodes"][i].get("extras") or {})
           for nodes in vegetation_nodes(document).values() for i in nodes):
        canopy = tile_canopy(tile)
        if canopy is not None and len(canopy):
            per_variant = crowns(document, binary, product, entry["url"], (0.0, 0.0, 0.0),
                                 canopy - [tile.origin[0], 0.0, tile.origin[2]])
            for variant, nodes in vegetation_nodes(document).items():
                for i in nodes:
                    document["nodes"][i].setdefault("extras", {})["instance_crowns"] = per_variant[variant]
    data = glb_pack(document, binary)
    product.replace(entry, data)
    if "brotli_bytes" in entry:
        import brotli

        entry["brotli_bytes"] = len(brotli.compress(data, quality=9))
    _collision(product, m, edits, stats)
    m.setdefault("vegetation", {})["trunk_clearance"] = dict(sorted(stats.items()))


def _trunk(position, scale):
    """(centre, half height, radius) of the generator's trunk cylinder (``coarse.forests.trunks``)."""
    height = min(float(scale[1]) * 0.55, 8.0)
    radius = float(np.clip(scale[1] * 0.018, 0.09, 0.55))
    return np.array([position[0], position[1] + height / 2, position[2]]), height / 2, radius


def _collision(product, manifest, edits, stats):
    from turku3d.col.format import PRIM_CYLINDER, Primitive
    from turku3d.col.materials import mat_id

    material = mat_id("tree_trunk")
    cells = manifest.get("collision") or {}
    removals, additions = {}, {}
    for old, scale, new in edits:
        cell = C.CollisionTile.at(old[0], old[2])
        removals.setdefault(cell.id, []).append(_trunk(old, scale))
        if new is not None:
            target = C.CollisionTile.at(new[0], new[2])
            if target.id not in cells:
                target = cell
            centre, half, radius = _trunk(new, scale)
            additions.setdefault(target.id, []).append(
                Primitive.cylinder(material, tuple(centre - np.asarray(target.origin, np.float64)), half, radius))
    for cell_id in sorted(set(removals) | set(additions)):
        entry = cells.get(cell_id)
        if entry is None:
            continue
        origin = np.asarray(C.CollisionTile.parse(cell_id).origin, np.float64)
        gone = removals.get(cell_id, [])
        found = [False] * len(gone)

        def keep(prim):
            if prim.kind != PRIM_CYLINDER or prim.material != material:
                return True
            centre = np.asarray(prim.center, np.float64) + origin
            for n, (c, _, radius) in enumerate(gone):
                if (not found[n] and np.hypot(*(centre[[0, 2]] - c[[0, 2]])) < MATCH
                        and abs(prim.params[1] - radius) < 1e-3):
                    found[n] = True
                    return False
            return True

        data = splice_primitives(product.read(entry["url"]), keep, additions.get(cell_id, []))
        stats["collision_trunks_unmatched"] += found.count(False)
        product.replace(entry, data)
        entry["prims"] = entry.get("prims", 0) - found.count(True) + len(additions.get(cell_id, []))


MIGRATION = Migration("path-trees", 1, transform)
