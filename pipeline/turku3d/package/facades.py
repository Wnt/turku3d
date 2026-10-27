"""Facade bindings, per-tile facade inputs and density planning for assembly."""

from __future__ import annotations

import json
import math
from pathlib import Path

from turku3d import config as C
from turku3d.package.gltf import read_glb


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


def glb_buildings(data: bytes) -> set[str]:
    """Building IDs whose meshes a layer GLB carries; facade maps bind by building ID."""
    js, _ = read_glb(data)
    return {
        str(node["extras"]["building_id"])
        for node in js.get("nodes", [])
        if node.get("extras", {}).get("building_id") is not None
    }


def facade_inputs(facades: Path, bindings: list[str], buildings: set[str]):
    """The bound facade IDs and exported facade files of some buildings, for one tile's key."""
    bound = sorted(f for f in bindings if f.rsplit("_", 1)[0] in buildings)
    files = [
        p
        for b in sorted(buildings)
        if (facades / b).is_dir()
        for p in sorted((facades / b).iterdir())
        if p.is_file()
    ]
    return bound, files


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


# Packaged facade densities in pixels per metre, highest first. A planned facade steps down
# one level at a time.
DENSITY_LADDER = (16, 8, 4, 2, 1)


def halve_densities(
    plan: dict[str, int],
    ranked: list[str],
    floors: dict[str, int],
    count: int,
    *,
    below_floors: bool = False,
) -> list[tuple[str, int, int]]:
    """Step `count` facades one ladder level down, always the facade with the highest planned
    density, the farthest from spawn among equals (ranked is farthest first). Unplanned
    facades keep their source density, above every ladder level. A facade stops at its
    declared minimum density unless below_floors. Returns (facade_id, old, new) per step;
    empty when no facade can step down."""
    order = {facade_id: index for index, facade_id in enumerate(ranked)}

    def lower(facade_id):
        current = plan.get(facade_id, 64)
        level = next((level for level in DENSITY_LADDER if level < current), None)
        return level if level and (below_floors or level >= floors[facade_id]) else None

    steps = []
    for _ in range(count):
        candidates = [fid for fid in ranked if lower(fid)]
        if not candidates:
            break
        facade_id = min(candidates, key=lambda fid: (-plan.get(fid, 64), order[fid]))
        old, new = plan.get(facade_id, 64), lower(facade_id)
        plan[facade_id] = new
        steps.append((facade_id, old, new))
    return steps


def facade_source_density(facades: Path, facade_id: str) -> float:
    building_id, index = facade_id.rsplit("_", 1)
    meta = json.loads((facades / building_id / f"{index}.json").read_text())
    return float(meta["pixels_per_metre"][0])


def facade_density_floor(facades: Path, facade_id: str) -> int:
    building_id, index = facade_id.rsplit("_", 1)
    meta = json.loads((facades / building_id / f"{index}.json").read_text())
    floor = meta.get("min_pixels_per_metre", 0)
    if not isinstance(floor, int) or isinstance(floor, bool) or floor not in (0, *range(4, 73)):
        raise ValueError(f"{facade_id}: invalid minimum facade density {floor!r}")
    return floor
