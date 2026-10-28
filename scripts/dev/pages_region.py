"""The region a GitHub Pages release ships: playable tiles near a centre that meet quality thresholds.

`publish/pages.json` may carry a `region` block (every field optional, defaults below):

    {"repo": "Wnt/turku-game", "branch": "gh-pages",
     "region": {"centre_en": [23459668, 6704254], "radius_m": 1000, "min_facade_coverage": 0.2,
                "ground": ["ortho-splats"]}}

A listed (playable) tile of the release ships when its centre lies within `radius_m` of `centre_en`
(Läntinen Rantakatu 15), its street-facing wall area covered by generated, derived or fast facade maps is at least
`min_facade_coverage` (a tile without street-facing walls passes), its ground texture kind is one of `ground`
(never `draft`), its validator verdict is not `fail` (a live release exists only after its tile-validate stage
passed) and it connects at an edge or corner with the centre's tile through other shipped tiles. Per-tile figures come
from the release's quality ledger (`tiles/quality.json`, `turku3d.region.quality`).

The Pages tile tree keeps the selected tiles playable: their collision (and the meshing-margin cells beside them
outside the listed tiles), parked cars and delivery addresses. An excluded listed tile under the same 500 m parent
as a selected one stays as visual-only scenery, so the parent's REPLACE refinement has no hole; the L0 children of
a parent without any selected tile are dropped and the parent stands in for them. The game's playable area is the
union of the manifest's collision tiles, so the edge warning and the safe reset bound the smaller area.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from turku3d import config as C  # noqa: E402  (stdlib-only module)

REPO = Path(__file__).resolve().parents[2]
DEFAULT = {
    "centre_en": [C.OFFICE_E, C.OFFICE_N],
    "radius_m": 1000.0,
    "min_facade_coverage": 0.2,
    "ground": ["ortho-splats"],
}


class Refused(ValueError):
    """The region selection leaves nothing publishable."""


def region(config):
    """The region settings: the `region` block of publish/pages.json over the defaults."""
    settings = {**DEFAULT, **((config or {}).get("region") or {})}
    unknown = set(settings) - set(DEFAULT)
    if unknown:
        raise ValueError(f"publish/pages.json region: unknown keys {sorted(unknown)}")
    if "draft" in settings["ground"]:
        raise ValueError("publish/pages.json region: draft ground never ships")
    return settings


def ledger(release):
    """The release's per-tile quality ledger; measured in the pipeline env when the release predates it."""
    path = Path(release) / "tiles/quality.json"
    if path.is_file():
        return json.loads(path.read_text())["tiles"]
    command = ["bash", str(REPO / "pipeline/env/run.sh"), "python", "-m", "turku3d.region"]
    result = subprocess.run(
        [*command, "quality", "--release", str(release), "--json"],
        cwd=REPO,
        check=True,
        text=True,
        capture_output=True,
        timeout=300,
    )
    return json.loads(result.stdout)


def playable(manifest):
    return sorted(
        (t for t, e in manifest["visual"].items() if e.get("level") == 0 and "coarse_dtm" not in e),
        key=C.VisualTile.parse,
    )


def distance(tile, settings):
    """Metres from the region centre to the tile's centre."""
    x, _, z = C.en_to_local(*settings["centre_en"])
    box = C.VisualTile.parse(tile).bbox
    return math.hypot((box.xmin + box.xmax) / 2 - x, (box.zmin + box.zmax) / 2 - z)


def coverage(record):
    """Shippable street-wall coverage; older ledgers predate the separate fast-map metric."""
    return record.get("rough_coverage", record.get("facade_coverage"))


def reasons(tile, record, settings):
    """Why a tile does not ship (empty: it meets every threshold)."""
    found = []
    far = distance(tile, settings)
    if far > settings["radius_m"]:
        found.append(f"outside the radius ({far:.0f} m > {settings['radius_m']:.0f} m)")
    if record is None:
        return [*found, "no quality record"]
    covered = coverage(record)
    if covered is not None and covered < settings["min_facade_coverage"]:
        found.append(f"facade coverage {covered:.0%} < {settings['min_facade_coverage']:.0%}")
    if record.get("ground") not in settings["ground"]:
        found.append(f"ground {record.get('ground')}")
    if record.get("validator", {}).get("status") == "fail":
        found.append("validator failed")
    if record.get("facades", {}).get("other", 0):
        found.append(f"{record['facades']['other']} facade maps are not generated-style")
    return found


def select(manifest, tiles, settings):
    """{"selected": [...], "excluded": {tile: [reasons]}, "centre": tile}; raises Refused when empty."""
    x, _, z = C.en_to_local(*settings["centre_en"])
    centre = C.VisualTile.at(x, z).id
    excluded = {t: reasons(t, tiles.get(t), settings) for t in playable(manifest)}
    passing = {t for t, why in excluded.items() if not why}
    if centre not in passing:
        why = excluded.get(centre) or ["not a playable tile of this release"]
        raise Refused(f"the centre tile {centre} does not ship: {'; '.join(why)}")
    connected, frontier = {centre}, [C.VisualTile.parse(centre)]
    while frontier:
        tile = frontier.pop()
        for di, dj in ((i, j) for i in (-1, 0, 1) for j in (-1, 0, 1) if i or j):
            near = C.VisualTile(0, tile.i + di, tile.j + dj)
            if near.id in passing and near.id not in connected:
                connected.add(near.id)
                frontier.append(near)
    for tile in passing - connected:
        excluded[tile] = ["not connected to the centre tile through shipped tiles"]
    spawn = manifest.get("spawn", {}).get("position")
    if spawn and C.VisualTile.at(spawn[0], spawn[2]).id not in connected:
        raise Refused(
            f"the spawn lies in {C.VisualTile.at(spawn[0], spawn[2]).id}, which does not ship"
        )
    selected = sorted(connected, key=C.VisualTile.parse)
    return {
        "centre": centre,
        "selected": selected,
        "excluded": {t: why for t, why in excluded.items() if t not in connected},
    }


def kept_visual(manifest, selected):
    """Visual tiles of the Pages tree: every parent, the selected tiles and their 500 m siblings."""
    parents = {C.VisualTile.parse(t).parent() for t in selected}
    return {
        tid
        for tid in manifest["visual"]
        if (tile := C.VisualTile.parse(tid)).level > 0 or tile.parent() in parents
    }


def kept_collision(manifest, selected):
    """Collision tiles of the selected tiles plus the margin cells beside them outside the listed tiles."""
    chosen, listed = set(selected), set(playable(manifest))
    cells = {cid: C.CollisionTile.parse(cid) for cid in manifest["collision"]}
    inside = {(c.i, c.j) for c in cells.values() if c.visual_parent().id in chosen}
    keep = set()
    for cid, cell in cells.items():
        parent = cell.visual_parent().id
        if parent in chosen:
            keep.add(cid)
        elif parent not in listed and any(
            (cell.i + di, cell.j + dj) in inside for di in (-1, 0, 1) for dj in (-1, 0, 1)
        ):
            keep.add(cid)
    return keep


def prune_tileset(node, visual):
    """Drop tileset nodes whose content is not in `visual`; a node without children left becomes a leaf."""
    children = [prune_tileset(child, visual) for child in node.get("children", [])]
    children = [child for child in children if child is not None]
    node_id = node.get("extras", {}).get("id")
    if node_id is not None and node_id not in visual:
        return None
    node = {key: value for key, value in node.items() if key != "children"}
    if children:
        node["children"] = children
    return node


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_tiles(source, target, selection):
    """Write the Pages tile tree for `selection` from the release tile tree `source`; returns the manifest."""
    source, target = Path(source), Path(target)
    manifest = json.loads((source / "manifest.json").read_text())
    selected = set(selection["selected"])
    visual = kept_visual(manifest, selected)
    collision = kept_collision(manifest, selected)
    files = set()

    def keep(url):
        files.add(url)

    manifest["visual"] = {t: e for t, e in manifest["visual"].items() if t in visual}
    manifest["collision"] = {c: e for c, e in manifest["collision"].items() if c in collision}
    manifest["vehicles"] = [
        car
        for car in manifest.get("vehicles", [])
        if C.VisualTile.at(car["x"], car["z"]).id in selected
    ]
    used = {f"{car['model']}/{car['color']}" for car in manifest["vehicles"]}
    manifest["vehicle_models"] = {
        k: v for k, v in manifest.get("vehicle_models", {}).items() if k in used
    }
    for entry in [
        *manifest["visual"].values(),
        *manifest["collision"].values(),
        *manifest["vehicle_models"].values(),
        *([manifest["roads"]] if manifest.get("roads") else []),
    ]:
        keep(entry["url"])
    for entry in manifest["visual"].values():
        for image in entry.get("shared_images", []):
            keep(image["url"])
    for tid in manifest["visual"]:
        ground = source / "ground" / tid
        if ground.is_dir():
            files.update(str(p.relative_to(source)) for p in ground.rglob("*") if p.is_file())
    library = source / "ground/library"
    if library.is_dir():
        files.update(str(p.relative_to(source)) for p in library.rglob("*") if p.is_file())
    for name in sorted(files):
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target / name)
    if manifest.get("addresses"):
        pool = json.loads((source / manifest["addresses"]["url"]).read_text())
        pool["addresses"] = [a for a in pool["addresses"] if a["tile"] in selected]
        pool["tiles"] = {t: n for t, n in pool.get("tiles", {}).items() if t in selected}
        data = (json.dumps(pool) + "\n").encode()
        url = f"addresses/{digest(data)[:16]}.json"
        (target / url).parent.mkdir(parents=True, exist_ok=True)
        (target / url).write_bytes(data)
        manifest["addresses"] = {
            "url": url,
            "sha256": digest(data),
            "bytes": len(data),
            "count": len(pool["addresses"]),
        }
    tileset = json.loads((source / manifest["tileset"]["url"]).read_text())
    tileset["root"] = prune_tileset(tileset["root"], set(manifest["visual"])) or {
        **tileset["root"],
        "children": [],
    }
    data = (json.dumps(tileset, indent=1) + "\n").encode()
    (target / manifest["tileset"]["url"]).write_bytes(data)
    manifest["tileset"] = {**manifest["tileset"], "sha256": digest(data), "bytes": len(data)}
    manifest["pages_region"] = {
        "selected": selection["selected"],
        "excluded": sorted(selection["excluded"]),
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def plan(config, release):
    """The selection for the release under the configured region, plus the settings it used."""
    settings = region(config)
    manifest = json.loads((Path(release) / "tiles/manifest.json").read_text())
    tiles = ledger(release)
    selection = select(manifest, tiles, settings)
    return {**selection, "region": settings, "quality": tiles}


def notes(result):
    """The region part of the release notes (the gh-pages commit message, pages.notes): the
    thresholds, every shipped tile with its figures and every excluded tile with the reason."""
    settings, tiles = result["region"], result["quality"]
    e, n = settings["centre_en"]

    def figures(tile):
        record = tiles.get(tile, {})
        covered = coverage(record)
        return (
            f"facades {'-' if covered is None else f'{covered:.0%}'}, "
            f"ground {record.get('ground', '-')}, {record.get('buildings', '-')} buildings"
        )

    lines = [
        f"Region: listed tiles within {settings['radius_m']:.0f} m of E {e:.0f} N {n:.0f} with at least "
        f"{settings['min_facade_coverage']:.0%} generated, derived or fast street facades, "
        f"{' or '.join(settings['ground'])} ground and a passing validator, connected to {result['centre']}",
        f"Shipped tiles ({len(result['selected'])}):",
        *(f"  {tile}: {figures(tile)}" for tile in result["selected"]),
        f"Excluded tiles ({len(result['excluded'])}):",
        *(
            f"  {tile}: {'; '.join(why)} ({figures(tile)})"
            for tile, why in sorted(
                result["excluded"].items(), key=lambda kv: C.VisualTile.parse(kv[0])
            )
        ),
    ]
    return "\n".join(lines) + "\n"
