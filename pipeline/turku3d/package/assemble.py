"""Compose published layers into collision, visual L0 and coarse parent tiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col.format import ColTile, Primitive, read_col, write_col
from turku3d.layers import layer_root, read_collision, read_manifest, read_visual
from turku3d.package import coarse
from turku3d.package.manifest import DistTree, _atomic_write, git_sha, verify_tree
from turku3d.package.seams import edge_equal, verify_tile_seams
from turku3d.package.spawn import derive_spawn, parking_sources

BRIDGE = Path(__file__).with_name("visual.mjs")


def digest(files: list[Path], extra=None) -> str:
    h = hashlib.sha256(json.dumps(extra, sort_keys=True).encode())
    for p in sorted(set(files)):
        h.update(str(p).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _continuous(name, manifest):
    return (
        manifest["params"].get("seams", "continuous" if name == "ground" else "objects")
        == "continuous"
    )


def load_layers(root: Path):
    manifests, collision = {}, {}
    for p in sorted(root.glob("*/MANIFEST.json")):
        manifests[p.parent.name] = read_manifest(p.parent)
        collision[p.parent.name] = {
            name.removesuffix(".col.npz"): read_collision(p.parent / name)
            for name in manifests[p.parent.name]["tiles"]
            if name.endswith(".col.npz")
        }
    # Check each continuous layer separately, before and after tile-local float32 conversion.
    for name, tiles in collision.items():
        if not _continuous(name, manifests[name]):
            continue
        for tid, layer in tiles.items():
            tile = C.CollisionTile.parse(tid)
            for dx, dz, axis in ((1, 0, 0), (0, 1, 2)):
                other = C.CollisionTile(tile.i + dx, tile.j + dz)
                if other.id not in tiles:
                    continue
                a, b = layer.positions, tiles[other.id].positions
                for decoded in (False, True):
                    if decoded:
                        a = (a - tile.origin).astype(np.float32).astype(np.float64) + tile.origin
                        b = (b - other.origin).astype(np.float32).astype(np.float64) + other.origin
                    if not edge_equal(a, b, axis, other.origin[axis]):
                        raise ValueError(
                            f"{name}: seam mismatch {tid}/{other.id} (decoded={decoded})"
                        )
    return manifests, collision


def collision_tile(tile, layers, manifests, available):
    positions, indices, material, primitives, ranges = [], [], [], [], []
    count = 0
    for name in sorted(layers):
        if tile.id not in layers[name]:
            continue
        layer = layers[name][tile.id]
        positions.append(layer.positions)
        indices.append(layer.indices + count)
        material.append(layer.material)
        if _continuous(name, manifests[name]):
            ranges.append([count, len(layer.positions)])
        count += len(layer.positions)
        for p in layer.primitives:
            center = tuple(p["center"] - tile.origin)
            rotation = (0, math.sin(p["yaw"] / 2), 0, math.cos(p["yaw"] / 2))
            if p["kind"] == "box":
                primitive = Primitive.box(int(p["material"]), center, p["half_extents"], rotation)
            else:
                factory = Primitive.capsule if p["kind"] == "capsule" else Primitive.cylinder
                primitive = factory(
                    int(p["material"]), center, p["height"] / 2, p["radius"], rotation
                )
            primitives.append(primitive)
    world = np.concatenate(positions)
    border = any(
        C.CollisionTile(tile.i + dx, tile.j + dz).id not in available
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
    )
    col = ColTile.for_tile(
        tile.i,
        tile.j,
        world,
        np.concatenate(indices),
        np.concatenate(material),
        primitives=primitives,
        aoi_border=border,
        meta={"continuous_vertices": ranges},
    )
    error = float(np.max(np.abs(col.world_positions() - world))) if world.size else 0.0
    if error > 0.002:
        raise ValueError(f"{tile.id}: collision precision exceeds 2 mm ({error})")
    # Preserve continuous-layer vertex ranges for post-shipping seam validation.
    data = write_col(col, encoding="meshopt", optimize=False)
    decoded = read_col(data)
    if decoded.positions.tobytes() != col.positions.tobytes() or not np.array_equal(
        decoded.materials, col.materials
    ):
        raise ValueError(f"{tile.id}: collision codec round-trip differs")

    def canonical(indices):
        return np.take_along_axis(
            indices, (indices.argmin(axis=1)[:, None] + np.arange(3)) % 3, axis=1
        )

    if not np.array_equal(canonical(col.indices), canonical(decoded.indices)):
        raise ValueError(f"{tile.id}: collision triangle topology changed")
    for before, after in zip(col.primitives, decoded.primitives, strict=True):
        if (
            np.max(
                np.abs(np.array((*before.center, *before.params)) - (*after.center, *after.params))
            )
            > 0.002
        ):
            raise ValueError(f"{tile.id}: primitive precision exceeds 2 mm")
    lo, hi = decoded.compute_bbox()
    return data, dict(
        tris=len(col.indices), prims=len(primitives), h=(lo[1], hi[1]), flags=decoded.flags
    )


def facade_bindings(facades, layers):
    """Only a filtered export tied to this geometry inventory can authorize unique maps."""
    manifest = facades.parent / "MANIFEST.json"
    if not manifest.exists():
        if facades.exists():
            raise ValueError(f"{facades}: use the filtered texture assembly export, not staging")
        return []
    export = json.loads(manifest.read_text())
    if not isinstance(export.get("bound"), list) or "inventory_sha256" not in export:
        raise ValueError(f"{manifest}: not a filtered texture assembly export")
    inventory = layers / "buildings/facades.geojson"
    if (
        not inventory.exists()
        or hashlib.sha256(inventory.read_bytes()).hexdigest() != export["inventory_sha256"]
    ):
        return []  # Geometry changed since export: preserve library fallbacks.
    return export["bound"]


def visual_tiles(
    tiles, stage, library, facades, texture_cache, coarse_ids, bindings, ground_bindings
):
    requests = []
    for tile, inputs in tiles.items():
        work = stage / tile.id
        work.mkdir()
        requests.append(
            dict(
                tile=tile.id,
                level=tile.level,
                inputs=[str(p) for p in inputs],
                output=str(work / "tile.glb"),
                stats=str(work / "stats.json"),
                library=str(library),
                facades=str(facades),
                facadeBindings=bindings,
                groundBinding=ground_bindings.get(tile.id),
                textureCache=str(texture_cache),
                coarseTerrain=tile.id in coarse_ids,
            )
        )
    req = stage / "requests.json"
    req.write_text(json.dumps(requests))
    proc = subprocess.run(["node", str(BRIDGE), str(req)], capture_output=True, text=True)
    if proc.returncode:
        raise ValueError(f"visual packaging failed\n{proc.stdout}\n{proc.stderr}")
    return {
        tile: (
            (stage / tile.id / "tile.glb").read_bytes()
            if (stage / tile.id / "tile.glb").exists()
            else None,
            json.loads((stage / tile.id / "stats.json").read_text()),
        )
        for tile in tiles
    }


def assemble(
    tile_ids: list[str],
    *,
    layers: Path | None = None,
    out: Path | None = None,
    library: Path | None = None,
    facades: Path | None = None,
    ground: Path | None = None,
    area="office-block",
) -> dict:
    root = (layers or layer_root()).resolve()
    out = (out or C.dist_dir() / "tiles").resolve()
    export = C.work_root() / "textures/assembly"
    library = (library or export / "library").resolve()
    facades = (facades or export / "facades").resolve()
    from .ground_textures import publish_ground

    ground = (ground or C.work_root() / "textures/ground").resolve()
    ground_bindings = publish_ground(ground, out, tile_ids, root / "ground")
    bindings = facade_bindings(facades, root)
    requested = {C.VisualTile.parse(tid) for tid in tile_ids}
    if any(t.level != 0 for t in requested):
        raise ValueError("request L0 tiles; 500 m and 1 km parents are built automatically")
    manifests, collision = load_layers(root)
    visuals = {}
    for name, m in manifests.items():
        for filename in m["tiles"]:
            if filename.endswith(".vis.glb"):
                tile = C.VisualTile.parse(filename.removesuffix(".vis.glb"))
                if tile.level == 0:
                    path = root / name / filename
                    read_visual(path)
                    visuals.setdefault(tile, []).append(path)
    for t in requested:
        if t not in visuals:
            raise ValueError(f"{t.id}: no published visual layers in {root}")
    parents = {t.parent() for t in requested}
    parents |= {t.parent() for t in list(parents)}
    # REPLACE removes the entire parent, so its uncovered quadrants need coarse leaf siblings.
    parents |= {
        child for parent in list(parents) if parent.level == 2 for child in parent.children()
    }
    build = requested | parents
    tree = DistTree(out, areas=[area])
    tree.spawn = derive_spawn(root, collision)
    cache_file = out / ".assemble-cache.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    # Include implementation and tool lock, not git HEAD: an unrelated commit is not an input change.
    code_files = (
        list(Path(__file__).parent.glob("*.py"))
        + [
            *Path(__file__).parent.glob("*.mjs"),
            Path(__file__).parents[1] / "layers.py",
            C.REPO_ROOT / "pipeline/env/package-lock.json",
            Path(C.__file__),
        ]
        + [
            p
            for p in (Path(__file__).parents[1] / "col").iterdir()
            if p.suffix in (".py", ".mjs", ".json")
        ]
    )
    code = digest(code_files)
    textures = [
        p
        for d in (library, facades, ground, ground.parent / "library")
        if d.exists()
        for p in d.rglob("*")
        if p.is_file() and p.suffix in (".ktx2", ".json", ".png", ".jpg", ".jpeg")
    ]
    texture_hash = digest(textures)
    available = set(tree.collision)
    for t in requested:
        for c in t.collision_tiles():
            available.discard(c.id)
            if any(c.id in v for v in collision.values()):
                available.add(c.id)
    changed, pending_inputs, pending_collision, keys = [], {}, {}, {}
    coarse_ids, points_cache = set(), {}
    tree_valid = (out / "manifest.json").exists() and not verify_tree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assemble-", dir=out.parent) as temp:
        stage = Path(temp)
        for tile in sorted(build, key=lambda t: t.id):
            inputs = []
            for child, paths in visuals.items():
                if (tile.level == 0 and tile == child) or (
                    tile.level > 0
                    and child.i // 2**tile.level == tile.i
                    and child.j // 2**tile.level == tile.j
                ):
                    inputs.extend(paths)
            terrain = coarse.discover(tile) if tile.level > 0 else None
            files = (
                inputs + [root / name / "MANIFEST.json" for name in manifests] + parking_sources()
            )
            if terrain:
                files += terrain["files"]
            key = digest(files, [code, texture_hash, bindings, tile.id, sorted(available), area])
            keys[tile.id] = key
            if (
                cache.get(tile.id) == key
                and (tile.id in tree.visual or tile.level > 0)
                and tree_valid
            ):
                continue
            if terrain:
                inputs = inputs + [
                    coarse.build(tile, terrain, stage / (tile.id + ".dtm.glb"), points_cache)
                ]
                coarse_ids.add(tile.id)
            pending_inputs[tile] = inputs
            changed.append(tile.id)
            if tile.level == 0:
                for c in tile.collision_tiles():
                    if c.id in available:
                        pending_collision[c] = collision_tile(c, collision, manifests, available)
        if not changed:
            return {
                "changed": [],
                "cached": sorted(t.id for t in build),
                "manifest": str(out / "manifest.json"),
            }
        pending_visual = visual_tiles(
            pending_inputs,
            stage,
            library,
            facades,
            out.parent / ".texture-cache",
            coarse_ids,
            bindings,
            ground_bindings,
        )
        for tile, (data, stats) in pending_visual.items():
            if data is not None and tile.level == 0:
                wire_bytes = stats["brotli_bytes"] + ground_bindings.get(tile.id, {}).get(
                    "texture_bytes", 0
                )
                if wire_bytes > 8_000_000:
                    raise ValueError(
                        f"{tile.id}: visual plus ground textures exceeds 8 MB: {wire_bytes}"
                    )
        shipped = {
            cid: read_col(out / entry["url"])
            for cid, entry in tree.collision.items()
            if cid in available and C.CollisionTile.parse(cid) not in pending_collision
        }
        shipped.update({tile.id: read_col(data) for tile, (data, _) in pending_collision.items()})
        errors = verify_tile_seams(shipped)
        if errors:
            raise ValueError("; ".join(errors))
        # All geometry, budgets and source/decoded seams have passed before publishing anything.
        for tile, (data, stats) in pending_visual.items():
            if data is None:
                previous = tree.visual.pop(tile.id, None)
                if previous:
                    (out / previous["url"]).unlink(missing_ok=True)
                continue
            ground_bytes = ground_bindings.get(tile.id, {}).get("texture_bytes", 0)
            wire_bytes = stats["brotli_bytes"] + ground_bytes
            if wire_bytes > 8_000_000 and tile.level == 0:
                raise ValueError(
                    f"{tile.id}: visual plus ground textures exceeds 8 MB: {wire_bytes}"
                )
            entry = tree.add_visual(tile, data, tris=stats["tris"], h=stats["h"])
            entry.update(
                brotli_bytes=stats["brotli_bytes"],
                ground_texture_bytes=ground_bytes,
                wire_bytes=wire_bytes,
                max_error_m=stats["max_error_m"],
                bounds=stats["bounds"],
            )
            if tile.level > 0:
                entry["coarse_dtm"] = tile.id in coarse_ids
        for tile, (data, stats) in pending_collision.items():
            tree.add_collision(tile, data, **stats)
        for cid in set(tree.collision) - available:
            stale = out / tree.collision.pop(cid)["url"]
            stale.unlink(missing_ok=True)
            if stale.parent.exists() and not any(stale.parent.iterdir()):
                stale.parent.rmdir()
        # Neighbour availability can change AOI flags on an existing, otherwise unchanged tile.
        for cid, entry in list(tree.collision.items()):
            c = C.CollisionTile.parse(cid)
            if c in pending_collision:
                continue
            col = read_col(out / entry["url"])
            border = any(
                C.CollisionTile(c.i + dx, c.j + dz).id not in available
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
            if col.aoi_border != border:
                col.aoi_border = border
                data = write_col(col, encoding="meshopt", optimize=False)
                tree.add_collision(
                    c, data, tris=entry["tris"], prims=entry["prims"], h=entry["h"], flags=col.flags
                )
        tree.commit(code=git_sha())
        cache.update(keys)
        _atomic_write(cache_file, (json.dumps(cache, sort_keys=True) + "\n").encode())
    return {
        "changed": [tid for tid in changed if tid in tree.visual],
        "visual": len(tree.visual),
        "collision": len(tree.collision),
        "manifest": str(out / "manifest.json"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tile", nargs="+", default=["t0_0_0"])
    ap.add_argument("--layers", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--library", type=Path)
    ap.add_argument("--ground", type=Path)
    ap.add_argument("--facades", type=Path)
    ap.add_argument("--area", default="office-block")
    args = vars(ap.parse_args())
    args["tile_ids"] = args.pop("tile")
    print(json.dumps(assemble(**args)))


if __name__ == "__main__":
    main()
