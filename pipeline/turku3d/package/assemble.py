"""Compose published layers into collision, visual L0 and coarse parent tiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col.format import ColTile, Primitive, read_col, write_col
from turku3d.content.addresses import publish as publish_addresses
from turku3d.facade_kit import kit_request, store_path
from turku3d.layers import (
    CollisionLayer,
    layer_root,
    read_collision,
    read_manifest,
    read_visual,
)
from turku3d.package import coarse
from turku3d.package.facades import (
    facade_bindings,
    facade_density_floor,
    facade_inputs,
    facade_priorities,
    facade_source_bytes,
    facade_source_density,
    glb_buildings,
    halve_densities,
)
from turku3d.package.gltf import GlbMesh, build_glb
from turku3d.package.manifest import DistTree, _atomic_write, git_sha, verify_tree
from turku3d.package.seams import edge_equal, verify_tile_seams
from turku3d.package.spawn import derive_spawn, osm_sources
from turku3d.roads import publish as publish_roads

BRIDGE = Path(__file__).with_name("visual.mjs")
HEADROOM_BYTES = 7_600_000


def _portable(path: Path) -> str:
    """A path relative to the sandbox, data root or repo, so seeded copies share cache keys."""
    resolved = path.resolve()
    for base in (C.work_root(), C.data_root(), C.REPO_ROOT):
        if resolved.is_relative_to(base.resolve()):
            return str(resolved.relative_to(base.resolve()))
    return str(resolved)


def digest(files: list[Path], extra=None) -> str:
    h = hashlib.sha256(json.dumps(extra, sort_keys=True).encode())
    for p in sorted(set(files), key=_portable):
        h.update(_portable(p).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def collision_cells(published, available, layers):
    """Cells requiring cache validation, including unrequested retained neighbours."""
    cells = published | {C.CollisionTile.parse(cid) for cid in available}
    return sorted(
        (c for c in cells if any(c.id in layer for layer in layers.values())),
        key=lambda c: c.id,
    )


def collision_cache_key(tile_id, manifests, code, layer_ids):
    """Hash the cell's current source inputs, including retained neighbour cells."""
    files = {
        name: m["tiles"][f"{tile_id}.col.npz"]["sha256"]
        for name, m in manifests.items()
        if f"{tile_id}.col.npz" in m["tiles"]
    }
    return digest([], [code, tile_id, files, layer_ids])


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
    drop_replaced(root, manifests, collision)
    return manifests, collision


def replaced_buildings(manifests) -> list[str]:
    """Buildings-layer footprints another layer ships instead (landmarks: `params.replaces`)."""
    return sorted({b for m in manifests.values() for b in m["params"].get("replaces", [])})


def drop_replaced(root: Path, manifests, collision) -> None:
    """Remove the replaced buildings' collision shells, by the layer's per-triangle owners."""
    replaced = replaced_buildings(manifests)
    for tid, layer in collision.get("buildings", {}).items() if replaced else ():
        with np.load(root / "buildings" / f"{tid}.col.npz", allow_pickle=False) as arrays:
            ids, owner = arrays["building_ids"], arrays["building_index"]
        keep = ~np.isin(ids[owner], replaced)
        if keep.all():
            continue
        used, inverse = np.unique(layer.indices[keep], return_inverse=True)
        collision["buildings"][tid] = CollisionLayer(
            layer.positions[used],
            inverse.reshape(-1, 3).astype(np.uint32),
            layer.material[keep],
            layer.primitives,
        )


# Every layer make refresh rebuilds, with the tile kinds it must publish. Assembly of a
# core tile fails on any gap instead of shipping a release without that layer.
OFFICE_LAYERS = {
    "ground": (".vis.glb", ".col.npz"),
    "buildings": (".vis.glb", ".col.npz"),
    "structures": (".vis.glb", ".col.npz"),
    "bridges": (".vis.glb", ".col.npz"),
    "buildings_outer": (".vis.glb",),
    "props": (".vis.glb", ".col.npz"),
    "vegetation": (".vis.glb", ".col.npz"),
    "traffic_static": (".vis.glb", ".col.npz"),
    "roads": (),
    "landmarks": (),  # Allowlisted objects only; most tiles have none.
}
# Expansion tiles outside the measured core ship with the layers that exist for them;
# ground, buildings and roads are the minimum (structures need measured office data).
EXPANSION_LAYERS = ("ground", "buildings", "roads")


def require_layers(manifests, tile_ids):
    """Per-area required layers: core tiles need every layer, expansion tiles the minimum."""
    core = [t for t in tile_ids if t in C.CORE_TILES]
    expansion = [t for t in tile_ids if t not in C.CORE_TILES]
    names = OFFICE_LAYERS.keys() if core else EXPANSION_LAYERS
    missing = sorted(set(names) - manifests.keys())
    if missing:
        raise ValueError(f"Missing required layers: {', '.join(missing)}; run make refresh")
    if core:
        if not manifests["structures"]["params"].get("acceptance_passed"):
            raise ValueError(
                "Office-block structures failed real-data acceptance; run make refresh"
            )
        for name, kinds in OFFICE_LAYERS.items():
            tiles = manifests[name]["tiles"]
            absent = [kind for kind in kinds if not any(tile.endswith(kind) for tile in tiles)]
            if absent:
                raise ValueError(
                    f"Office-block {name} layer has no {' or '.join(absent)} tiles; run make refresh"
                )
    ground = manifests["ground"]["tiles"]
    for tid in expansion:
        tile = C.VisualTile.parse(tid)
        needed = [f"{tid}.vis.glb"] + [f"{c.id}.col.npz" for c in tile.collision_tiles()]
        absent = [name for name in needed if name not in ground]
        if absent:
            raise ValueError(f"{tid}: ground layer lacks {absent[0]}; run make tile-add T={tid}")


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
    replaced=(),
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
                replacedBuildings=list(replaced),
                facadeKit=kit_request(
                    C.work_root() / "cache/facade-kit",
                    set().union(*(glb_buildings(p.read_bytes()) for p in inputs)),
                ),
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
    manifests, collision = load_layers(root)
    checked = area == "office-block" or (
        area == "playable-tiles" and root == layer_root().resolve()
    )
    if checked:
        require_layers(manifests, tile_ids)
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
    if checked:
        require_layers(manifests, tile_ids)
    visuals, owners = {}, {}
    for name, m in manifests.items():
        if name == "traffic_static":
            continue
        for filename in m["tiles"]:
            if filename.endswith(".vis.glb"):
                tile = C.VisualTile.parse(filename.removesuffix(".vis.glb"))
                if tile.level == 0:
                    path = root / name / filename
                    owners[path] = glb_buildings(read_visual(path))
                    visuals.setdefault(tile, []).append(path)
    for t in requested:
        if t not in visuals:
            raise ValueError(f"{t.id}: no published visual layers in {root}")
    parents = {t.parent() for t in requested}
    parents |= {t.parent() for t in list(parents)}
    # Surrounding lidar massing is visual only. Publish its coarse parents even
    # though their L0 children are outside the playable collision region.
    outer = {
        tile.parent()
        for tile, paths in visuals.items()
        if any(path.parent.name == "buildings_outer" for path in paths)
    }
    parents |= outer
    parents |= {tile.parent() for tile in outer}
    # REPLACE removes the entire parent, so its uncovered quadrants need coarse leaf siblings.
    parents |= {
        child for parent in list(parents) if parent.level == 2 for child in parent.children()
    }
    # The same holds for a 500 m parent only partly covered by playable L0 tiles: its other
    # quadrants become visual-only coarse L0 leaves (massing and coarse DTM, no collision).
    area_visuals = set(C.area(area).visual_tiles)
    coarse_leaves = {
        sibling
        for tile in requested
        for sibling in tile.parent().children()
        if sibling not in requested and sibling not in area_visuals
    }
    build = requested | parents | coarse_leaves
    tree = DistTree(out, areas=[area])
    tree.spawn = derive_spawn(root, collision)
    roads_changed = publish_roads(root / "roads", tree)
    # Delivery addresses: every addressed building with a street-side door in the area's L0 tiles.
    area_l0 = {t.id for t in C.area(area).visual_tiles if t.level == 0} | {t.id for t in requested}
    roads_changed = publish_addresses(root, tree, area_l0) or roads_changed
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
                    "clear_front_m",
                    "clear_rear_m",
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
            Path(__file__).parents[1] / "facade_kit.py",
            C.REPO_ROOT / "textures/turkutex/facade_fast_render.py",
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
    # Shared maps only. A tile's own ground maps enter its key through its binding, and the
    # facade maps of its own buildings through facade_inputs.
    textures = [
        p
        for d in (library, ground.parent / "library")
        if d.exists()
        for p in d.rglob("*")
        if p.is_file() and p.suffix in (".ktx2", ".json", ".png", ".jpg", ".jpeg")
    ]
    texture_hash = digest(textures)
    replaced = replaced_buildings(manifests)
    layer_ids = {
        name: [m.get("code"), m["params"].get("seams")]
        for name, m in manifests.items()
        if name != "roads"
    }
    layer_ids["replaced_buildings"] = replaced
    current_cells = {cid for layer in collision.values() for cid in layer}
    available = set(tree.collision) & current_cells
    published_cells = set()
    for t in requested:
        for c in C.collision_tiles_for_bbox(t.bbox.buffered(C.MESH_MARGIN)):
            if c.visual_parent() in area_visuals and c.visual_parent() not in requested:
                continue
            available.discard(c.id)
            if any(c.id in v for v in collision.values()):
                available.add(c.id)
                published_cells.add(c)
    # Facade densities are planned per tile from the facades centred in it, which may belong
    # to a seam neighbour's buildings; their maps enter that tile's key as well.
    priorities = facade_priorities(root / "buildings/facades.geojson", bindings, tree.spawn)
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
            terrain = coarse.discover(tile) if tile.level > 0 or tile in coarse_leaves else None
            if tile in coarse_leaves and not inputs and not terrain:
                continue  # Nothing to show there: no massing and no terrain coverage.
            # A tile's key covers its own layer files and its own buildings' facades, never
            # another tile's: adding a tile or rebuilding buildings elsewhere leaves its packed
            # visual cached. Collision is keyed per tile below.
            ranked = priorities.get(tile.id, []) if tile.level == 0 else []
            buildings = set().union(*(owners[p] for p in inputs))
            buildings |= {facade_id.rsplit("_", 1)[0] for facade_id in ranked}
            bound, facade_files = facade_inputs(facades, bindings, buildings)
            files = inputs + osm_sources() + facade_files
            files += [p for key in buildings if (p := store_path(key)).exists()]
            if terrain:
                files += terrain["files"]
            role = (
                "coarse" if tile in coarse_leaves else "playable" if tile in requested else "parent"
            )
            key = digest(
                files,
                [
                    code,
                    texture_hash,
                    bound,
                    ranked,
                    tile.id,
                    role,
                    layer_ids,
                    ground_bindings.get(tile.id),
                ],
            )
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
        # Every listed tile's collision tiles, and the meshing margin the ground layer covers
        # too, so bridges crossing an L0 edge have a supported shore. Each collision tile is
        # rebuilt only when one of its layer files changed; border flags are refreshed below.
        # Retained cells must be checked too: a refresh can change a neighbour's
        # layer bytes even when its visual tile was not requested in this build.
        for c in collision_cells(published_cells, available, collision):
            key = collision_cache_key(c.id, manifests, code, layer_ids)
            keys[f"collision:{c.id}"] = key
            if cache.get(f"collision:{c.id}") == key and c.id in tree.collision and tree_valid:
                continue
            pending_collision[c] = collision_tile(
                c, collision, manifests, available, dynamic_traffic=True
            )
        if not changed and not pending_collision and not set(tree.collision) - available:
            if roads_changed:
                tree.commit(code=git_sha())
            return {
                "changed": [],
                "cached": sorted(t.id for t in build),
                "facades_bound": len(bindings),
                "manifest": str(out / "manifest.json"),
            }
        pending_visual = (
            visual_tiles(
                pending_inputs,
                stage,
                library,
                facades,
                out.parent / ".texture-cache",
                coarse_ids,
                bindings,
                ground_bindings,
                replaced=replaced,
            )
            if pending_inputs
            else {}
        )
        budget_before = {
            tile.id: stats["brotli_bytes"]
            + ground_bindings.get(tile.id, {}).get("texture_bytes", 0)
            for tile, (data, stats) in pending_visual.items()
            if data is not None and tile.level == 0
        }
        floors = {
            facade_id: facade_density_floor(facades, facade_id)
            for ranked in priorities.values()
            for facade_id in ranked
        }
        facade_density: dict[str, dict[str, int]] = {}
        for tile, (data, stats) in pending_visual.items():
            if data is None or tile.level != 0:
                continue
            wire_bytes = budget_before[tile.id]
            ranked = priorities.get(tile.id, [])
            plan = facade_density.setdefault(tile.id, {})
            if wire_bytes >= HEADROOM_BYTES and ranked:
                # KTX headers and tiny mips limit savings from resampling. The
                # estimate selects a first batch; measured wire bytes decide
                # whether to extend it. Near-spawn facades retain source detail.
                needed = wire_bytes - (HEADROOM_BYTES - 100_000)
                predicted = 0.0
                while predicted < needed:
                    step = halve_densities(plan, ranked, floors, 1)
                    if not step:
                        break
                    facade_id, old, new = step[0]
                    source = facade_source_density(facades, facade_id)
                    predicted += (
                        facade_source_bytes(facades, facade_id)
                        * 0.94
                        * (min(old, source) ** 2 - new**2)
                        / source**2
                    )
            while wire_bytes >= HEADROOM_BYTES and ranked:
                if plan:
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
                        replaced=replaced,
                    )[tile]
                    pending_visual[tile] = (data, stats)
                    wire_bytes = stats["brotli_bytes"] + ground_bindings.get(tile.id, {}).get(
                        "texture_bytes", 0
                    )
                    if wire_bytes < HEADROOM_BYTES:
                        break
                batch = max(1, math.ceil(len(ranked) * 0.1))
                if not halve_densities(plan, ranked, floors, batch):
                    if wire_bytes <= 8_000_000 or not halve_densities(
                        plan, ranked, floors, batch, below_floors=True
                    ):
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
            if tile.level > 0 or tile in coarse_leaves:
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
    started = time.monotonic()
    result = assemble(**args)
    if args["out"] is None:  # the per-tile quality ledger travels with the dist tree
        from turku3d.region.quality import after_build

        after_build(args["layers"], args["facades"], result["changed"], time.monotonic() - started)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
