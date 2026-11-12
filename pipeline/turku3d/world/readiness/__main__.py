"""check-readiness: how a compiled location, or any GLB, obeys docs/design/render-readiness.md.

  python -m turku3d.world check-readiness --location NAME [--store DIR] [--all] [--entity ID ...] [--json FILE]
  python -m turku3d.world check-readiness --glb FILE... [--kind building]

It reports per entity, per instanced asset and per location, and names the owning track of every finding. It never
fails a build: the exit code is 0 unless the input cannot be read.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import check, clusters


def store_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    if os.environ.get("TURKU3D_WORLD_STORE"):
        return Path(os.environ["TURKU3D_WORLD_STORE"])
    return Path(os.environ.get("TURKU3D_WORK", ".")) / "world"


def run_location(
    store: Path,
    name: str,
    everything: bool,
    only: list[str],
    rays: int,
    jobs: int,
    with_clusters: bool,
    log=print,
) -> dict:
    started = time.perf_counter()
    manifest = json.loads((store / "locations" / f"{name}.json").read_text())
    rows = [
        row
        for row in manifest["entities"]
        if (everything or row["role"] == "scene") and (not only or row["id"] in only)
    ]
    work, assets = [], {}
    for row in rows:
        if row.get("url"):
            work.append(
                (row["id"], row["kind"], str(check.resolve(store, row["url"])), row["origin"], rays)
            )
        for item in row.get("instances", []):
            entry = assets.setdefault(item["asset"], {"kinds": set(), "copies": 0, "entities": 0})
            entry["kinds"].add(row["kind"])
            entry["copies"] += len(item["transforms"])
            entry["entities"] += 1
    for url, entry in assets.items():
        kind = "asset:foliage" if entry["kinds"] & set(check.FOLIAGE) else "asset"
        work.append((f"asset:{url}", kind, str(check.resolve(store, url)), [0, 0, 0], rays))
    reports, meshes, cells = {}, {}, {}
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for identity, report, mesh, absolute in pool.map(check.entity_job, work, chunksize=4):
            reports[identity] = report
            if mesh is not None:
                meshes[identity] = mesh
            if absolute is not None:
                cells[identity] = (absolute, (absolute.min(axis=0), absolute.max(axis=0)))
    measured = time.perf_counter()
    if with_clusters:
        for identity, result in clusters.hierarchies(meshes).items():
            reports[identity]["hierarchy"] = result
            check.hierarchy_flags(reports[identity])
    del meshes
    entities = []
    for row in rows:
        report = reports.get(
            row["id"], {"triangles": 0, "flags": [], "materials": {"keys": [], "families": []}}
        )
        placed = [
            {
                "asset": item["asset"],
                "copies": len(item["transforms"]),
                "triangles": reports[f"asset:{item['asset']}"]["triangles"],
                "ids": "ids" in item,
                "behaviour": item.get("behaviour"),
            }
            for item in row.get("instances", [])
        ]
        entities.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "role": row["role"],
                "tier": row.get("tier"),
                "owner": check.OWNERS.get(row["kind"], "?"),
                "instances": placed,
                **report,
            }
        )
    asset_rows = [
        {
            "asset": url,
            "kinds": sorted(entry["kinds"]),
            "copies": entry["copies"],
            "entities": entry["entities"],
            "owner": check.OWNERS.get(sorted(entry["kinds"])[0], "?"),
            **reports[f"asset:{url}"],
        }
        for url, entry in assets.items()
    ]
    borders = check.border_mismatch(cells)
    result = {
        "schema": "turku-readiness/1",
        "location": name,
        "revision": manifest.get("revision"),
        "store": str(store),
        "scope": "all" if everything else "scene",
        "summary": summarise(entities, asset_rows, borders),
        "entities": entities,
        "assets": asset_rows,
        "borders": borders,
        "timings": {
            "measureS": round(measured - started, 2),
            "clustersS": round(time.perf_counter() - measured, 2),
        },
    }
    return result


def summarise(entities: list[dict], assets: list[dict], borders: list[dict]) -> dict:
    """Location totals: triangles, instance reuse, materials, and how many entities miss each rule, per kind."""
    own = sum(e["triangles"] for e in entities)
    placed = sum(i["copies"] * i["triangles"] for e in entities for i in e["instances"])
    unique = own + sum(a["triangles"] for a in assets)
    rows, families = set(), {}
    surface: dict[str, float] = {}
    for item in [*entities, *assets]:
        for key in item["materials"].get("keys", []):
            rows.add(key)
            families.setdefault(json.loads(key)[0], set()).add(key)
        weight = item.get("area_m2", 0.0) * (item.get("copies", 1))
        for name, share in item["materials"].get("surface_share", {}).items():
            surface[name] = surface.get(name, 0.0) + share * weight
    total_area = sum(surface.values()) or 1.0
    by_kind: dict[str, dict] = {}
    for item in [
        *entities,
        *[{**a, "kind": "asset (" + ", ".join(a["kinds"]) + ")"} for a in assets],
    ]:
        kind = by_kind.setdefault(
            item["kind"],
            {
                "entities": 0,
                "triangles": 0,
                "flagged": 0,
                "rules": {},
                "floor": [],
                "sealed": 0,
                "fields": 0,
            },
        )
        kind["entities"] += 1
        kind["triangles"] += item["triangles"]
        kind["flagged"] += bool(item["flags"])
        for rule in sorted({flag.split(" ", 1)[0] for flag in item["flags"]}):
            kind["rules"][rule] = kind["rules"].get(rule, 0) + 1
        if "needed" in (item.get("hierarchy") or {}) and item["triangles"] >= 1000:
            kind["floor"].append((item["hierarchy"]["needed"]["300m"], item["triangles"]))
        if "field" in item:
            kind["fields"] += 1
            kind["sealed"] += item["field"]["state"] == "sealed"
    for kind in by_kind.values():
        weights = sum(t for _, t in kind["floor"]) or 1
        kind["needed_at_300m"] = (
            round(sum(f * t for f, t in kind["floor"]) / weights, 4) if kind["floor"] else None
        )
        del kind["floor"]
    return {
        "entities": len(entities),
        "assets": len(assets),
        "own_triangles": own,
        "instanced_triangles": placed,
        "drawn_triangles": own + placed,
        "unique_triangles": unique,
        "instanced_share": round(placed / max(own + placed, 1), 4),
        "reuse": round((own + placed) / max(unique, 1), 2),
        "material_rows": len(rows),
        "families": {name: len(keys) for name, keys in sorted(families.items())},
        "surface_share": {
            name: round(area / total_area, 4) for name, area in sorted(surface.items())
        },
        "border_pairs_unmatched": len(borders),
        "by_kind": by_kind,
    }


def print_report(result: dict, top: int, log=print) -> None:
    s = result["summary"]
    log(
        f"readiness of {result['location']} (revision {result['revision']}, {result['scope']} entities) in {result['store']}"
    )
    log(
        f"  {s['entities']} entities, {s['assets']} instanced assets; drawn triangles {s['drawn_triangles']:,} "
        f"(own {s['own_triangles']:,}, instanced {s['instanced_triangles']:,} = {s['instanced_share']:.0%}); "
        f"unique {s['unique_triangles']:,}, reuse x{s['reuse']}"
    )
    log(
        f"  materials: {s['material_rows']} rows in {len(s['families'])} families ("
        + ", ".join(f"{name} {count}" for name, count in s["families"].items())
        + ")"
    )
    log(
        "  surface: "
        + ", ".join(f"{name} {share:.1%}" for name, share in s["surface_share"].items())
        + f"; ground borders unmatched: {s['border_pairs_unmatched']}"
    )
    log(
        f"  {'kind':<28}{'n':>5}{'triangles':>11}{'flagged':>9}{'sealed':>9}{'@300m':>7}  rules missed (entities)"
    )
    for name, kind in sorted(s["by_kind"].items()):
        rules = ", ".join(f"{rule} {count}" for rule, count in sorted(kind["rules"].items()))
        sealed = f"{kind['sealed']}/{kind['fields']}" if kind["fields"] else "-"
        floor = f"{kind['needed_at_300m']:.0%}" if kind["needed_at_300m"] is not None else "-"
        log(
            f"  {name:<28}{kind['entities']:>5}{kind['triangles']:>11,}{kind['flagged']:>9}{sealed:>9}{floor:>7}  {rules}"
        )
    worst = sorted(
        (item for item in [*result["entities"], *result["assets"]] if item["flags"]),
        key=lambda item: (-len(item["flags"]), -item["triangles"]),
    )
    for item in worst[:top]:
        name = (
            item.get("id")
            or f"asset {item['asset']} (x{item['copies']}, {', '.join(item['kinds'])})"
        )
        log(f"  {name} [{item['owner']}] {item['triangles']:,} triangles")
        for flag in item["flags"]:
            log(f"      {flag}")
    for pair in result["borders"][:top]:
        log(
            f"  border {pair['cells'][0]} | {pair['cells'][1]}: {pair['vertices'][0]} against {pair['vertices'][1]} vertices, {pair['unmatched']} unmatched (F3)"
        )


def run_files(paths: list[str], kind: str, rays: int, with_clusters: bool, log=print) -> dict:
    reports, meshes = {}, {}
    for path in paths:
        _, report, mesh, _ = check.entity_job((path, kind, path, [0, 0, 0], rays))
        reports[path] = report
        if mesh is not None:
            meshes[path] = mesh
    if with_clusters:
        for path, result in clusters.hierarchies(meshes).items():
            reports[path]["hierarchy"] = result
            check.hierarchy_flags(reports[path])
    for path, report in reports.items():
        log(path)
        for key in (
            "triangles",
            "area_m2",
            "density_per_m2",
            "small_share",
            "opaque",
            "solids",
            "thickness",
            "field",
            "ground",
            "repeats",
            "uv",
            "hierarchy",
        ):
            if key in report:
                value = report[key]
                log(f"  {key}: {json.dumps(value)}")
        log(
            f"  materials: {json.dumps({k: v for k, v in report['materials'].items() if k != 'keys'})}"
        )
        for flag in report["flags"]:
            log(f"  ! {flag}")
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m turku3d.world check-readiness",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--location", help="a built location in the store")
    parser.add_argument(
        "--store", help="output store root (default $TURKU3D_WORLD_STORE or $TURKU3D_WORK/world)"
    )
    parser.add_argument(
        "--all", action="store_true", help="include the background ring (default: scene entities)"
    )
    parser.add_argument(
        "--entity", action="append", default=[], help="only this entity id (repeatable)"
    )
    parser.add_argument("--glb", nargs="+", help="check GLB files instead of a location")
    parser.add_argument("--kind", default="building", help="with --glb: which kind's rules apply")
    parser.add_argument(
        "--json",
        help="write the full report here (default $TURKU3D_WORK/readiness/<location>.json)",
    )
    parser.add_argument("--top", type=int, default=12, help="entities listed with their findings")
    parser.add_argument("--rays", type=int, default=256, help="thickness samples per mesh")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("TURKU3D_JOBS", "3")))
    parser.add_argument(
        "--no-clusters", action="store_true", help="skip the cluster hierarchy build"
    )
    args = parser.parse_args(argv)
    if args.glb:
        reports = run_files(args.glb, args.kind, args.rays, not args.no_clusters)
        if args.json:
            Path(args.json).write_text(json.dumps(reports, indent=1, default=float))
        return 0
    if not args.location:
        parser.error("give --location NAME or --glb FILE")
    store = store_root(args.store)
    result = run_location(
        store, args.location, args.all, args.entity, args.rays, args.jobs, not args.no_clusters
    )
    print_report(result, args.top)
    target = (
        Path(args.json)
        if args.json
        else Path(os.environ.get("TURKU3D_WORK", ".")) / "readiness" / f"{args.location}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=1, default=float))
    print(
        f"  full report: {target} ({result['timings']['measureS']} s measuring, {result['timings']['clustersS']} s clusters)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
