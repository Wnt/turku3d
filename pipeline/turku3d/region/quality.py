"""Per-tile quality ledger: what each playable tile ships and how good it is.

    python -m turku3d.region quality                      # the table (work root ledger, else work dist, else data root)
    python -m turku3d.region quality --json               # the same as JSON
    python -m turku3d.region quality --release DIR        # measure a release directory (tiles/, layers/, textures/)
    python -m turku3d.region quality --validated REPORT   # record tile-validate verdicts in a dist ledger

The assembler CLI writes `<dist>/tiles/quality.json` after every tile build (`record`), so the ledger travels
with the tile tree into the stage cache, the tile store and the live release. `make tile-validate` adds the
validator's verdict per tile. When the integrator publishes a release, `publish` merges the release's ledger
into `$TURKU3D_DATA/tile-quality.json`, marks its tiles validator-green (every stage of the release passed) and
keeps `full_quality_since` and tiles that later leave the list (`listed: false`), so nothing good is forgotten.

Per playable L0 tile: when it was built and how long the assembly took, wire bytes, buildings, facade walls by
map source (generate, derived, derived-neighbour, other = not generated-style, none), the street-facing wall area
covered by a generated-style map, the ground texture kind (ortho-splats, untextured, draft), trees, parked cars
and the validator verdict.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

from turku3d import config as C

FORMAT = "turku3d-tile-quality"
VERSION = 1
LEDGER = "tile-quality.json"  # in the data root
DIST_LEDGER = "quality.json"  # in a tile dist tree
SOURCES = ("generate", "derived", "derived-neighbour", "other", "none")
GENERATED = ("generate", "derived", "derived-neighbour")


def now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_of(meta: dict) -> str:
    """The map source of a bound facade sidecar; `other` is anything not generated-style (pages.generated)."""
    if meta.get("method"):
        return "other"
    mode = meta.get("source_mode")
    if mode == "generate":
        return "generate" if meta.get("generated_qa") else "other"
    if mode in ("derived", "derived-neighbour") and meta.get("derived_from"):
        return mode
    return "other"


def playable(manifest: dict) -> list[str]:
    """The manifest's playable L0 tiles: L0 visual entries that are not coarse visual-only leaves."""
    return sorted(
        (
            tid
            for tid, e in manifest["visual"].items()
            if e.get("level") == 0 and "coarse_dtm" not in e
        ),
        key=C.VisualTile.parse,
    )


def tile_buildings(layers: Path, tile: str) -> set[str]:
    """Building IDs the buildings layer packs into the tile."""
    from turku3d.package.facades import glb_buildings

    path = layers / "buildings" / f"{tile}.vis.glb"
    return glb_buildings(path.read_bytes()) if path.is_file() else set()


def collision_digest(manifest: dict, tile: str) -> str:
    entries = sorted(
        (cid, e["sha256"])
        for cid, e in manifest["collision"].items()
        if C.CollisionTile.parse(cid).visual_parent().id == tile
    )
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def ground_kind(entry: dict) -> str:
    if entry.get("coarse_dtm"):
        return "draft"
    return "ortho-splats" if entry.get("ground_texture_bytes", 0) > 0 else "untextured"


def measure(tiles: Path, layers: Path, assembly: Path) -> dict[str, dict]:
    """Quality of every playable L0 tile in the dist tree `tiles`, built from `layers` and the facade `assembly`."""
    from turku3d.package.facades import facade_bindings

    tiles, layers, assembly = Path(tiles), Path(layers), Path(assembly)
    manifest = json.loads((tiles / "manifest.json").read_text())
    facades = assembly / "facades"
    bound = set(facade_bindings(facades, layers))
    inventory = layers / "buildings/facades.geojson"
    walls = (
        [f["properties"] for f in json.loads(inventory.read_text())["features"]]
        if inventory.is_file()
        else []
    )
    vegetation = layers / "vegetation/MANIFEST.json"
    trees = (
        json.loads(vegetation.read_text())["params"].get("budgets", {})
        if vegetation.is_file()
        else {}
    )
    cars: dict[str, int] = {}
    for car in manifest.get("vehicles", []):
        tid = C.VisualTile.at(car["x"], car["z"]).id
        cars[tid] = cars.get(tid, 0) + 1
    result = {}
    for tid in playable(manifest):
        entry = manifest["visual"][tid]
        buildings = tile_buildings(layers, tid)
        counts = dict.fromkeys(SOURCES, 0)
        street = dict.fromkeys(SOURCES, 0)
        area = dict.fromkeys(SOURCES, 0.0)
        for wall in walls:
            if str(wall.get("building_id")) not in buildings:
                continue
            facade_id = f"{wall['building_id']}_{wall['facade_index']}"
            kind = "none"
            if facade_id in bound:
                sidecar = facades / str(wall["building_id"]) / f"{wall['facade_index']}.json"
                kind = source_of(json.loads(sidecar.read_text()))
            counts[kind] += 1
            if wall.get("street_facing"):
                street[kind] += 1
                area[kind] += wall["width_m"] * (wall["top_height"] - wall["base_height"])
        total = sum(area.values())
        result[tid] = {
            "visual_sha256": entry["sha256"],
            "wire_bytes": entry.get("wire_bytes", entry["bytes"]),
            "buildings": len(buildings),
            "facades": counts,
            "street_facades": street,
            # None: no street-facing wall to cover (a park or a quay).
            "facade_coverage": round(sum(area[k] for k in GENERATED) / total, 4) if total else None,
            "ground": ground_kind(entry),
            "trees": int(trees.get(tid, {}).get("trees", 0)),
            "parked_cars": cars.get(tid, 0),
            "collision_sha256": collision_digest(manifest, tid),
        }
    return result


def merge(previous: dict, measured: dict, *, built: str, changed=(), seconds=None) -> dict:
    """The ledger tiles for `measured`, carrying build times and verdicts over from `previous`.

    A tile whose visual bytes are unchanged keeps its build time; a tile whose collision is unchanged keeps
    its validator verdict, otherwise the verdict is `unvalidated` until the validator sees it.
    """
    tiles = {}
    for tid, record in measured.items():
        old = previous.get(tid, {})
        same_visual = old.get("visual_sha256") == record["visual_sha256"] and tid not in changed
        verdict = old.get("validator")
        if not verdict or old.get("collision_sha256") != record["collision_sha256"]:
            verdict = {"status": "unvalidated"}
        tiles[tid] = {
            **record,
            "built": old.get("built", built) if same_visual else built,
            "build_seconds": old.get("build_seconds") if same_visual else seconds,
            "validator": verdict,
            "listed": True,
            "full_quality_since": old.get("full_quality_since"),
        }
        stamp(tiles[tid], built)
    return tiles


def full_quality(record: dict) -> bool:
    """Every layer at production quality: textured ground, generated-style maps only, validator green."""
    return (
        record.get("ground") == "ortho-splats"
        and record.get("facades", {}).get("other", 0) == 0
        and record.get("validator", {}).get("status") == "pass"
    )


def stamp(record: dict, when: str) -> None:
    if full_quality(record) and not record.get("full_quality_since"):
        record["full_quality_since"] = when


def read(path: Path) -> dict:
    try:
        ledger = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return ledger.get("tiles", {}) if ledger.get("format") == FORMAT else {}


def write(path: Path, tiles: dict, **fields) -> dict:
    ledger = {"format": FORMAT, "version": VERSION, "updated": now(), **fields, "tiles": tiles}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    pending.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    pending.replace(path)
    return ledger


def after_build(layers, facades, changed, seconds) -> dict:
    """The assembler CLI's hook for its default dist: record the build in `<dist>/tiles/quality.json`."""
    from turku3d.layers import layer_root

    assembly = Path(facades).parent if facades else C.work_root() / "textures/assembly"
    return record(C.dist_dir() / "tiles", Path(layers or layer_root()), assembly, changed, seconds)


def record(tiles: Path, layers: Path, assembly: Path, changed=(), seconds=None) -> dict:
    """After a tile build: measure the dist tree and update its ledger (the assembler CLI's hook)."""
    path = Path(tiles) / DIST_LEDGER
    built = json.loads((Path(tiles) / "manifest.json").read_text()).get("built") or now()
    measured = measure(tiles, layers, assembly)
    merged = merge(
        read(path),
        measured,
        built=built,
        changed=set(changed),
        seconds=round(seconds, 3) if seconds is not None else None,
    )
    return write(path, merged)


def verdicts(report: dict) -> dict[str, dict]:
    """Validator verdict per L0 tile from a tile-validate report (tools/validator, reportVersion 2).

    A tile passes when every checked collision tile under it loaded without errors and no check failed on it.
    A failing report that names no tile fails every tile it checked."""
    checked: dict[str, set[str]] = {}
    failed: set[str] = set()

    def parent(cell):
        return C.CollisionTile.parse(cell).visual_parent().id

    for item in report.get("tiles", []):
        tid = parent(item["id"])
        checked.setdefault(tid, set()).add(item["id"])
        if item.get("errors"):
            failed.add(tid)
    for check in report.get("checks", {}).values():
        for item in check.get("perTile", []) if isinstance(check, dict) else []:
            if item.get("status") not in (None, "pass", "skip"):
                failed.add(parent(item["tile"]))
    if report.get("pass") is False and not failed:
        failed = set(checked)
    when = report.get("started") or now()
    return {
        tid: {"status": "fail" if tid in failed else "pass", "cells": len(cells), "checked": when}
        for tid, cells in checked.items()
    }


def validated(tiles: Path, report_path: Path) -> dict:
    """Record a tile-validate report's verdicts in the dist ledger (`make tile-validate`)."""
    path = Path(tiles) / DIST_LEDGER
    ledger = read(path)
    if not ledger:
        return {}
    for tid, verdict in verdicts(json.loads(Path(report_path).read_text())).items():
        if tid in ledger:
            ledger[tid]["validator"] = verdict
            stamp(ledger[tid], verdict["checked"])
    return write(path, ledger)


def release_layout(release: Path) -> tuple[Path, Path, Path]:
    release = Path(release)
    return release / "tiles", release / "layers", release / "textures/assembly"


def release_tiles(release: Path) -> dict:
    """The ledger of a release: its own `tiles/quality.json`, measured when the release predates the ledger."""
    tiles, layers, assembly = release_layout(release)
    ledger = read(tiles / DIST_LEDGER)
    if ledger:
        return ledger
    built = json.loads((tiles / "manifest.json").read_text()).get("built") or now()
    return merge({}, measure(tiles, layers, assembly), built=built)


def publish(published: Path, release: Path, commit: str) -> dict:
    """Merge a published (verified) release's ledger into the data root's `tile-quality.json`."""
    path = Path(published) / LEDGER
    previous = read(path)
    tiles = {}
    when = now()
    for tid, record in release_tiles(release).items():
        old = previous.get(tid, {})
        record = {**record, "validator": {"status": "pass", "checked": when, "release": commit}}
        record["full_quality_since"] = old.get("full_quality_since") or record.get(
            "full_quality_since"
        )
        stamp(record, when)
        tiles[tid] = record
    for tid, old in previous.items():
        if tid not in tiles:
            tiles[tid] = {**old, "listed": False}
    return write(path, tiles, release=commit)


def table(tiles: dict) -> str:
    head = (
        "tile",
        "built",
        "build s",
        "wire MB",
        "bldgs",
        "facades gen/der/nbr/other/none",
        "street cov",
        "ground",
        "trees",
        "cars",
        "validator",
        "full since",
    )
    rows = [head]
    for tid in sorted(tiles, key=C.VisualTile.parse):
        r = tiles[tid]
        f = r.get("facades", {})
        cov = r.get("facade_coverage")
        rows.append(
            (
                tid + ("" if r.get("listed", True) else " (unlisted)"),
                (r.get("built") or "-")[:16],
                "-" if r.get("build_seconds") is None else f"{r['build_seconds']:.0f}",
                f"{r.get('wire_bytes', 0) / 1e6:.2f}",
                str(r.get("buildings", 0)),
                "/".join(str(f.get(k, 0)) for k in SOURCES),
                "-" if cov is None else f"{cov:.0%}",
                r.get("ground", "-"),
                str(r.get("trees", 0)),
                str(r.get("parked_cars", 0)),
                r.get("validator", {}).get("status", "-"),
                (r.get("full_quality_since") or "-")[:16],
            )
        )
    widths = [max(len(row[k]) for row in rows) for k in range(len(head))]
    return "\n".join(
        "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in rows
    )


def default_ledger() -> Path:
    """The work root's ledger (main: the data root's), else the work dist's (a sandbox), else the data root's."""
    candidates = (
        C.work_root() / LEDGER,
        C.dist_dir() / "tiles" / DIST_LEDGER,
        C.data_root() / LEDGER,
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.region quality", description=__doc__)
    ap.add_argument("--ledger", type=Path, help="ledger to print (default: see above)")
    ap.add_argument("--release", type=Path, help="measure a release directory instead")
    ap.add_argument("--publish", type=Path, help="with --release: merge it into this data root")
    ap.add_argument("--commit", default="", help="with --publish: the release commit")
    ap.add_argument(
        "--validated", type=Path, help="tile-validate report to record in --dist's ledger"
    )
    ap.add_argument("--dist", type=Path, help="tile dist tree (default: $TURKU3D_WORK/dist/tiles)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.validated:
        validated(a.dist or C.dist_dir() / "tiles", a.validated)
        return 0
    if a.publish:
        if not a.release:
            ap.error("--publish needs --release")
        tiles = publish(a.publish, a.release, a.commit)["tiles"]
    elif a.release:
        tiles = release_tiles(a.release)
    else:
        tiles = read(a.ledger or default_ledger())
        if not tiles:
            print(f"no tile quality ledger at {a.ledger or default_ledger()}", file=sys.stderr)
            return 1
    print(json.dumps(tiles, indent=2, sort_keys=True) if a.json else table(tiles))
    return 0
