"""The location driver: sources -> entities -> compiled records -> a manifest the client loads.

`build` discovers the entities of a query and compiles those whose inputs changed. `refresh` recompiles from the
source facts of the last discovery, without reading sources: the live update path of one entity.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .api import BEHAVIOURS, SHAPES, Area, Instances, Output, Stub
from .frame import ORIGIN, to_engine
from .glb import pack
from .kinds import KINDS, code_hash
from .sources import Sources
from .store import Store, canonical, read_description, sha256

FORMAT = 1  # the record and GLB layout; a change rebuilds everything
VIEWS = Path(__file__).resolve().parents[3] / "qa/views/views.jsonl"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def area_of_views(name: str, views: Path = VIEWS) -> Area:
    """The query that covers an area's reference views: their cameras and what they look at."""
    points = []
    for line in views.read_text().splitlines():
        row = json.loads(line) if line.strip() else {}
        if row.get("area") == name:
            pose = row["pose"]
            yaw = np.radians(pose["yaw"])
            points.append((pose["E"], pose["N"]))
            points.append((pose["E"] + 40 * np.sin(yaw), pose["N"] + 40 * np.cos(yaw)))
    if not points:
        raise SystemExit(f"no reference views with area {name!r} in {views}")
    points = np.array(points)
    centre = (points.min(axis=0) + points.max(axis=0)) / 2
    reach = float(np.linalg.norm(points - centre, axis=1).max())
    return Area(round(float(centre[0]), 1), round(float(centre[1]), 1), float(np.ceil((reach + 90) / 10) * 10))


def instance_rows(store: Store, output: Output, entity: str) -> tuple[list[dict], list[np.ndarray], int]:
    rows, reach, triangles = [], [], 0
    for item in output.instances:
        if not len(item.transforms):
            continue
        transforms = np.asarray(item.transforms, dtype=float).reshape(-1, 7)
        if isinstance(item.asset, str):
            asset, count, radius = item.asset, 0, 1.0
            row = {"asset": asset}
        else:
            data, stats = pack(item.asset, "asset")
            asset, count = store.put(data, "glb")["url"], stats["triangles"]
            points = np.concatenate([np.asarray(part.positions, dtype=float).reshape(-1, 3) for part in item.asset])
            radius = float(np.linalg.norm(points, axis=1).max())
            # The asset's own box: with a transform it bounds one object, so a hierarchy can cull single instances.
            row = {"asset": asset, "bounds": [round(float(v), 3) for v in (*points.min(axis=0), *points.max(axis=0))]}
        row.update(lod=item.lod, transforms=np.round(transforms, 4).tolist())
        if item.ids is not None:
            if len(item.ids) != len(transforms):
                raise ValueError(f"{entity}: {len(item.ids)} instance ids for {len(transforms)} transforms")
            row["ids"] = list(item.ids)
        if item.behaviour is not None:
            if item.behaviour not in BEHAVIOURS:
                raise ValueError(f"{entity}: unknown behaviour {item.behaviour!r}")
            row["behaviour"] = item.behaviour
        if item.collision is not None:
            if item.collision.get("shape") not in SHAPES:
                raise ValueError(f"{entity}: unknown collision shape {item.collision!r}")
            row["collision"] = item.collision
        if item.mass is not None:
            row["mass"] = float(item.mass)
        rows.append(row)
        extent = np.abs(transforms[:, 4:7]).max(axis=1, keepdims=True) * radius
        reach += [transforms[:, :3] - extent, transforms[:, :3] + extent]
        triangles += count * len(transforms)
    return rows, reach, triangles


def compile_entity(store: Store, kind_name: str, description: dict) -> dict:
    """Compiles one description and stores its outputs; returns the record's variable part."""
    kind = KINDS[kind_name]
    started = time.perf_counter()
    output = kind.compile(description)
    compiled = time.perf_counter()
    lods, reach, families = [], [], set()
    for lod in sorted({part.lod for part in output.parts}):
        data, stats = pack([part for part in output.parts if part.lod == lod], description["id"])
        if stats["triangles"]:
            lods.append({"lod": lod, **store.put(data, "glb"), "triangles": stats["triangles"]})
            families.update(stats["families"])
    reach += [np.asarray(part.positions, dtype=float).reshape(-1, 3) for part in output.parts if len(part.positions)]
    instances, instance_reach, instance_triangles = instance_rows(store, output, description["id"])
    reach = np.concatenate(reach + instance_reach) if reach or instance_reach else np.zeros((1, 3))
    body = {
        "origin": [round(float(v), 4) for v in output.origin],
        "bounds": [round(float(v), 3) for v in (*reach.min(axis=0), *reach.max(axis=0))],
        "output": {"lods": lods, "instances": instances, "families": sorted(families), "instanceTriangles": instance_triangles},
    }
    body["hash"] = sha256(canonical(body))
    body["timings"] = {"compileMs": round((compiled - started) * 1000, 1), "packMs": round((time.perf_counter() - compiled) * 1000, 1)}
    return body


def entity_record(store: Store, stub: Stub, timings: dict | None = None) -> dict:
    """The record of an entity for its current description and generator code, compiled when it is new."""
    kind = KINDS[stub.kind]
    started = time.perf_counter()
    description = kind.describe(stub, read_description(stub.id))
    stored = store.put_json(description)
    key = sha256(canonical({"format": FORMAT, "kind": stub.kind, "code": code_hash(stub.kind), "description": stored["sha256"]}))
    described = time.perf_counter()
    record = store.record(key)
    fresh = record is None
    if fresh:
        record = {
            "schema": "turku-entity/1", "id": stub.id, "kind": stub.kind, "sources": stub.sources,
            "description": {"sha256": stored["sha256"], "url": stored["url"], "tier": description["tier"]},
            "generator": {"kind": stub.kind, "code": code_hash(stub.kind)}, "key": key,
            **compile_entity(store, stub.kind, description),
        }
        record["timings"]["describeMs"] = round((described - started) * 1000, 1)
        store.put_record(key, record)
    if timings is not None:
        timings["describe"] = timings.get("describe", 0.0) + described - started
        timings["compile"] = timings.get("compile", 0.0) + time.perf_counter() - described
        timings["compiled"] = timings.get("compiled", 0) + fresh
    return record


def manifest_row(record: dict, role: str) -> dict:
    """What the client needs of one record: the level of detail chosen for its role, and its instances."""
    lods = record["output"]["lods"]
    want = 1 if role == "background" else 0
    mesh = next((entry for entry in lods if entry["lod"] == want), lods[0] if lods else None)
    instances = record["output"]["instances"]
    levels = {row["lod"] for row in instances}
    level = want if want in levels else min(levels, default=0)
    row = {"id": record["id"], "kind": record["kind"], "tier": record["description"]["tier"], "role": role,
           "hash": record["hash"], "origin": record["origin"], "bounds": record["bounds"]}
    if mesh:
        row.update(lod=mesh["lod"], url=mesh["url"], bytes=mesh["bytes"], triangles=mesh["triangles"])
    row["instances"] = [{k: v for k, v in item.items() if k != "lod"} for item in instances if item["lod"] == level]
    return row


def stats_of(rows: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    return {"entities": len(rows), "triangles": sum(r.get("triangles", 0) for r in rows),
            "bytes": sum(r.get("bytes", 0) for r in rows), "instances": sum(len(i["transforms"]) for r in rows for i in r["instances"]),
            "byKind": by_kind}


def build(name: str, area: Area, background: float, store: Store, log=print) -> dict:
    """Discovers and compiles a location; writes and returns its manifest."""
    started = time.perf_counter()
    sources = Sources(log=log)
    wide = area.grown(max(background, area.radius))
    stubs: list[Stub] = []
    timings: dict[str, float] = {}
    for kind in KINDS.values():
        t0 = time.perf_counter()
        found = kind.discover(wide, sources)
        timings[f"discover:{kind.name}"] = time.perf_counter() - t0
        stubs += found
    discovered = time.perf_counter()
    rows, work = [], {}
    for stub in sorted(stubs, key=lambda s: s.id):
        store.put_facts(stub.id, {"id": stub.id, "box": list(stub.box), "sources": stub.sources, "facts": stub.facts})
        per_kind = work.setdefault(stub.kind, {})
        record = entity_record(store, stub, per_kind)
        rows.append(manifest_row(record, "scene" if area.box_distance(stub.box) <= area.radius else "background"))
    compiled = time.perf_counter()
    ground = sources.terrain.heights(area.e, area.n)[0]
    previous = store.location(name)
    manifest = {
        "schema": "turku-location/1", "name": name, "revision": (previous or {}).get("revision", 0) + 1, "built": now(),
        "query": {"crs": "EPSG:3877", "at": [area.e, area.n], "radius": area.radius, "background": wide.radius},
        "origin": list(ORIGIN),
        "focus": [round(float(v), 3) for v in to_engine(area.e, area.n, float(ground) if np.isfinite(ground) else 0.0)],
        "entities": rows, "stats": stats_of(rows),
        "timings": {
            "sourcesMs": round(sum(sources.timings.values()) * 1000),
            "discoverMs": round((discovered - started) * 1000),
            "describeMs": round(sum(w.get("describe", 0) for w in work.values()) * 1000),
            "compileMs": round(sum(w.get("compile", 0) for w in work.values()) * 1000),
            "totalMs": 0,
        },
    }
    manifest["timings"]["totalMs"] = round((time.perf_counter() - started) * 1000)
    store.put_location(manifest)
    log(f"location {name}: E {area.e} N {area.n} radius {area.radius:g} m, background {wide.radius:g} m, revision {manifest['revision']}")
    log(f"  sources   {sum(sources.timings.values()):7.2f} s  " + ", ".join(f"{k} {v:.2f}" for k, v in sorted(sources.timings.items())))
    if sources.wfs.fetched:
        log(f"            fetched {len(sources.wfs.fetched)} WFS blocks")
    for kind in KINDS.values():
        w = work.get(kind.name, {})
        count = sum(1 for r in rows if r["kind"] == kind.name)
        log(f"  {kind.name:<9} discover {timings[f'discover:{kind.name}']:6.2f} s, describe {w.get('describe', 0):6.2f} s, "
            f"compile {w.get('compile', 0):6.2f} s: {count} entities, {w.get('compiled', 0)} compiled")
    stats = manifest["stats"]
    log(f"  total     {time.perf_counter() - started:7.2f} s: {stats['entities']} entities, {stats['triangles']} triangles, "
        f"{stats['instances']} instances, {stats['bytes'] / 1e6:.1f} MB -> {store.location_path(name)}")
    return manifest


def refresh(name: str, store: Store, ids: list[str] | None = None, log=print) -> dict:
    """Recompiles entities of a built location from their cached source facts (all, or `ids`), and rewrites the
    manifest when anything a client draws changed. Returns {revision, changed, ms}."""
    started = time.perf_counter()
    manifest = store.location(name)
    if manifest is None:
        raise SystemExit(f"location {name!r} is not built in {store.root}")
    known = {row["id"] for row in manifest["entities"]}
    missing = [i for i in ids or [] if i not in known]
    if missing:
        raise SystemExit(f"not in location {name}: {', '.join(missing)}")
    changed = []
    for index, row in enumerate(manifest["entities"]):
        if ids is not None and row["id"] not in ids:
            continue
        saved = store.facts(row["id"])
        stub = Stub(saved["id"], tuple(saved["box"]), saved["sources"], saved["facts"])
        fresh = manifest_row(entity_record(store, stub), row["role"])
        if fresh != row:
            manifest["entities"][index] = fresh
            changed.append(row["id"])
    if changed:
        manifest.update(revision=manifest["revision"] + 1, built=now(), stats=stats_of(manifest["entities"]))
        store.put_location(manifest)
    ms = round((time.perf_counter() - started) * 1000)
    log(f"location {name}: revision {manifest['revision']}, {len(changed)} changed in {ms} ms" + (f": {', '.join(changed[:6])}" if changed else ""))
    return {"revision": manifest["revision"], "changed": changed, "ms": ms}
