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
from turku3d.package.gltf import GlbMesh, build_glb
from turku3d.package.manifest import DistTree, _atomic_write, git_sha, verify_tree
from turku3d.package.seams import edge_equal, verify_tile_seams
from turku3d.package.spawn import derive_spawn, osm_sources

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


def collision_tile(tile, layers, manifests, available, *, dynamic_traffic=False):
    positions, indices, material, primitives, ranges = [], [], [], [], []
    count = 0
    for name in sorted(layers):
        if dynamic_traffic and name == "traffic_static":
            continue
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
    """Authorize exported maps for facades whose identity and dimensions still match."""
    manifest = facades.parent / "MANIFEST.json"
    if not manifest.exists():
        if facades.exists():
            raise ValueError(f"{facades}: use the filtered texture assembly export, not staging")
        return []
    export = json.loads(manifest.read_text())
    if not isinstance(export.get("bound"), list) or "inventory_sha256" not in export:
        raise ValueError(f"{manifest}: not a filtered texture assembly export")
    inventory = layers / "buildings/facades.geojson"
    if not inventory.exists():
        return []
    features = json.loads(inventory.read_text())["features"]
    current = {
        f"{p['building_id']}_{p['facade_index']}": p
        for feature in features
        if (p := feature["properties"]).get("building_id") is not None
        and p.get("facade_index") is not None
    }
    bound = []
    for facade_id in export["bound"]:
        props = current.get(facade_id)
        if props is None:
            continue
        building_id, index = facade_id.rsplit("_", 1)
        sidecar = facades / building_id / f"{index}.json"
        if not sidecar.exists():
            continue
        meta = json.loads(sidecar.read_text())
        if (
            meta.get("building_id") == props["building_id"]
            and meta.get("facade_index") == props["facade_index"]
            and abs(meta["width_m"] - props["width_m"]) <= 0.01
            and abs(meta["height_m"] - (props["top_height"] - props["base_height"])) <= 0.01
        ):
            bound.append(facade_id)
    return bound


def visual_tiles(
    tiles,
    stage,
    library,
    facades,
    texture_cache,
    coarse_ids,
    bindings,
    ground_bindings,
    facade_density=None,
):
    requests = []
    for tile, inputs in tiles.items():
        work = stage / tile.id
        work.mkdir(exist_ok=True)
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
                facadeDensity=(facade_density or {}).get(tile.id, {}),
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


def facade_priorities(inventory: Path, bindings: list[str], spawn: dict) -> dict[str, list[str]]:
    """Rank each tile's exported facades from farthest to nearest spawn."""
    if not inventory.exists() or not spawn:
        return {}
    bound = set(bindings)
    sx, _, sz = spawn["position"]
    ranked: dict[str, list[tuple[float, str]]] = {}
    for feature in json.loads(inventory.read_text())["features"]:
        props = feature["properties"]
        facade_id = f"{props.get('building_id')}_{props.get('facade_index')}"
        if facade_id not in bound:
            continue
        coords = feature["geometry"]["coordinates"]
        e = sum(point[0] for point in coords) / len(coords)
        n = sum(point[1] for point in coords) / len(coords)
        x, z = e - C.ORIGIN_E, C.ORIGIN_N - n
        tile_id = C.VisualTile(0, math.floor(x / 250), math.floor(z / 250)).id
        ranked.setdefault(tile_id, []).append((math.hypot(x - sx, z - sz), facade_id))
    return {tile: [fid for _, fid in sorted(rows, reverse=True)] for tile, rows in ranked.items()}


def facade_source_bytes(facades: Path, facade_id: str) -> int:
    building_id, index = facade_id.rsplit("_", 1)
    directory = facades / building_id
    meta = json.loads((directory / f"{index}.json").read_text())
    return sum((directory / filename).stat().st_size for filename in meta["files"].values())


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
        if name == "traffic_static":
            continue
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
    parked_path = root / "traffic_static/placements.json"
    tree.vehicles = [
        car for car in tree.vehicles if C.VisualTile.at(car["x"], car["z"]) not in requested
    ]
    tree.vehicle_models = {}
    if parked_path.exists() and "traffic_static" in manifests:
        from turku3d.parked.models import model, palette

        parked = json.loads(parked_path.read_text())["cars"]
        envelope = {(t.i, t.j) for t in requested}
        tree.vehicles += [
            {
                k: car[k]
                for k in (
                    "object_id",
                    "model",
                    "color",
                    "x",
                    "y",
                    "z",
                    "yaw",
                    "width",
                    "height",
                    "length",
                )
            }
            for car in parked
            if (C.VisualTile.at(car["x"], car["z"]).i, C.VisualTile.at(car["x"], car["z"]).j)
            in envelope
        ]
        for name, color in sorted({(car["model"], car["color"]) for car in tree.vehicles}):
            pos, idx, normals, uv = model(name)
            data = build_glb(
                [
                    GlbMesh(
                        name,
                        pos,
                        idx,
                        normals=normals,
                        uvs=uv,
                        image=palette(name, color),
                        roughness=0.42,
                    )
                ]
            )
            tree.vehicle_models[f"{name}/{color}"] = tree._put(f"vehicles/{name}-{color}.glb", data)
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
    area_visuals = set(C.AREAS[area].visual_tiles)
    available = set(tree.collision)
    for t in requested:
        for c in C.collision_tiles_for_bbox(t.bbox.buffered(C.MESH_MARGIN)):
            if c.visual_parent() in area_visuals and c.visual_parent() not in requested:
                continue
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
            files = inputs + [root / name / "MANIFEST.json" for name in manifests] + osm_sources()
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
                        pending_collision[c] = collision_tile(
                            c, collision, manifests, available, dynamic_traffic=True
                        )
        # The ground layer covers the meshing margin too. Publish its adjacent
        # collision tiles so bridges crossing an L0 edge have a supported shore.
        for tile in requested:
            for c in C.collision_tiles_for_bbox(tile.bbox.buffered(C.MESH_MARGIN)):
                if c.visual_parent() in area_visuals and c.visual_parent() not in requested:
                    continue
                if c.id in available and c not in pending_collision:
                    pending_collision[c] = collision_tile(
                        c, collision, manifests, available, dynamic_traffic=True
                    )
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
        budget_before = {
            tile.id: stats["brotli_bytes"]
            + ground_bindings.get(tile.id, {}).get("texture_bytes", 0)
            for tile, (data, stats) in pending_visual.items()
            if data is not None and tile.level == 0
        }
        priorities = facade_priorities(root / "buildings/facades.geojson", bindings, tree.spawn)
        facade_density: dict[str, dict[str, int]] = {}
        for tile, (data, stats) in pending_visual.items():
            if data is None or tile.level != 0:
                continue
            wire_bytes = budget_before[tile.id]
            ranked = priorities.get(tile.id, [])
            plan = facade_density.setdefault(tile.id, {})
            if wire_bytes > 8_000_000 and ranked:
                # KTX headers and tiny mips limit savings from resampling. The
                # estimate selects a first batch; measured wire bytes decide
                # whether to extend it. Near-spawn facades retain source detail.
                needed = wire_bytes - 7_750_000
                predicted = 0
                for facade_id in ranked:
                    plan[facade_id] = 4
                    predicted += facade_source_bytes(facades, facade_id) * 0.94
                    if predicted >= needed:
                        break
            while wire_bytes > 8_000_000 and ranked and plan:
                data, stats = visual_tiles(
                    {tile: pending_inputs[tile]},
                    stage,
                    library,
                    facades,
                    out.parent / ".texture-cache",
                    coarse_ids,
                    bindings,
                    ground_bindings,
                    facade_density,
                )[tile]
                pending_visual[tile] = (data, stats)
                wire_bytes = stats["brotli_bytes"] + ground_bindings.get(tile.id, {}).get(
                    "texture_bytes", 0
                )
                if wire_bytes <= 8_000_000:
                    break
                remaining = [facade_id for facade_id in ranked if facade_id not in plan]
                if remaining:
                    for facade_id in remaining[: max(1, math.ceil(len(ranked) * 0.1))]:
                        plan[facade_id] = 4
                elif all(density == 4 for density in plan.values()):
                    plan.update({facade_id: 2 for facade_id in ranked})
                elif all(density == 2 for density in plan.values()):
                    plan.update({facade_id: 1 for facade_id in ranked})
                else:
                    break
            if wire_bytes > 8_000_000:
                raise ValueError(
                    f"{tile.id}: visual plus ground textures exceeds 8 MB after facade scaling: {wire_bytes}"
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
        shared_dir = stage / "shared"
        if shared_dir.exists():
            for asset in shared_dir.iterdir():
                _atomic_write(out / "visual/shared" / asset.name, asset.read_bytes())
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
                budget_before_bytes=budget_before.get(tile.id, wire_bytes),
                max_error_m=stats["max_error_m"],
                bounds=stats["bounds"],
                shared_images=[
                    {"url": f"visual/shared/{name}", "sha256": name.removesuffix(".ktx2")}
                    for name in stats.get("shared_images", [])
                ],
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
        "facades_bound": len(bindings),
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
