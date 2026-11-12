"""Audit two migrated city tiles, packed poles, unchanged collision and public payloads."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen

import numpy as np
import shapely
from turku3d import config as C
from turku3d.col.format import read_col
from turku3d.col.materials import table
from turku3d.package.gltf import read_glb

# Match extractPoles' object ranges and lowest-vertex base, using the actual codec
# output. The client creates dynamic bodies from these IDs/bases, not .col poles.
DECODE = r"""
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { MeshoptDecoder } = await import(require.resolve('meshoptimizer'));
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
const file = process.argv[1], raw = fs.readFileSync(file), length = raw.readUInt32LE(12);
const json = JSON.parse(raw.subarray(20, 20 + length).toString());
const resources = { '@glb.bin': raw.subarray(28 + length) };
if (json.buffers?.[0] && !json.buffers[0].uri) json.buffers[0].uri = '@glb.bin';
for (const buffer of json.buffers ?? []) if (buffer.uri && buffer.uri !== '@glb.bin') resources[buffer.uri] = fs.readFileSync(path.resolve(path.dirname(file), buffer.uri));
for (const image of json.images ?? []) if (image.uri) resources[image.uri] = new Uint8Array(4);
const doc = await io.readJSON({ json, resources });
const output = [];
for (const node of doc.getRoot().listNodes()) {
  if (!node.getMesh()) continue;
  const extras = node.getExtras(), matrix = node.getWorldMatrix();
  const objects = Array.isArray(extras.objects) ? extras.objects : [extras];
  const poles = objects.filter(e => e.prop_type === 'sign' || e.prop_type === 'bus_stop');
  if (!poles.length) continue;
  const primitives = node.getMesh().listPrimitives();
  if (primitives.length !== 1 || node.getExtension('EXT_mesh_gpu_instancing')) throw Error('Unexpected pole packing');
  const primitive = primitives[0], position = primitive.getAttribute('POSITION'), index = primitive.getIndices();
  if (!position || !index) throw Error('Pole has no indexed geometry');
  for (const e of poles) {
    const start = e.start ?? 0, count = e.count ?? index.getCount();
    if (!Number.isInteger(start) || !Number.isInteger(count) || start < 0 || count <= 0 || start + count > index.getCount()) throw Error('Invalid pole range');
    const used = [...new Set(Array.from({length:count}, (_,i) => index.getScalar(start+i)))];
    const vertices = used.map(i => position.getElement(i, []));
    const low = Math.min(...vertices.map(p => p[1]));
    const bottom = vertices.filter(p => p[1] <= low + .001);
    const local = [bottom.reduce((s,p) => s+p[0],0)/bottom.length, low, bottom.reduce((s,p) => s+p[2],0)/bottom.length];
    const base = [0,1,2].map(k => matrix[k]*local[0]+matrix[4+k]*local[1]+matrix[8+k]*local[2]+matrix[12+k]);
    output.push({id:e.pole_id, object_id:e.object_id, height:e.pole_height, base, recorded:e.pole_base, count, plates:e.plate_ids ?? null, merged:e.merged ?? null});
  }
}
console.log(JSON.stringify(output));
"""


def packed_poles(out, manifest):
    visual = out / manifest["visual"][manifest["tile"]]["url"]
    # First-pass GLBs need no decoder if their metadata contains no pole candidates.
    if manifest["pass_kind"] == "first":
        document, _ = read_glb(visual.read_bytes())
        for node in document.get("nodes", []):
            extras = node.get("extras", {})
            assert not extras.get("pole_id")
            assert not any(e.get("pole_id") for e in extras.get("objects", []))
        return []
    result = subprocess.run(
        ["node", "--input-type=module", "-e", DECODE, str(visual)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def solid_support(out, manifest):
    allowed = {
        m.id
        for m in table()
        if m.cls == "solid"
        and m.name
        not in {
            "building",
            "roof",
            "wall",
            "railing",
            "fence",
            "tree_trunk",
            "street_furniture",
            "pole",
            "bollard",
            "vegetation_low",
        }
    }
    parts = []
    for entry in manifest["collision"].values():
        col = read_col((out / entry["url"]).read_bytes())
        triangles = (col.positions + np.asarray(col.origin))[col.indices]
        parts.append(triangles[np.isin(col.materials, list(allowed))])
    triangles = np.concatenate(parts)
    return triangles, shapely.STRtree(shapely.polygons(triangles[:, :, [0, 2]]))


def support_error(triangles, tree, x, y, z):
    hits = []
    for index in tree.query(shapely.Point(x, z), predicate="intersects"):
        a, b, c = triangles[index]
        u, v = b - a, c - a
        det = u[0] * v[2] - u[2] * v[0]
        if abs(det) < 1e-10:
            continue
        normal = np.cross(u, v)
        if np.linalg.norm(normal[[0, 2]]) / abs(det) > np.tan(np.deg2rad(40)):
            continue
        dx, dz = x - a[0], z - a[2]
        height = a[1] + (dx * v[2] - dz * v[0]) / det * u[1]
        height += (u[0] * dz - u[2] * dx) / det * v[1]
        hits.append(abs(float(height) - y))
    assert hits, f"No solid collision under pole base {(x, y, z)}"
    return min(hits)


def verify(out, manifest, before_out, before_manifest):
    packed = packed_poles(out, manifest)
    previous = packed_poles(before_out, before_manifest)
    stats = manifest["furniture"]["pole_support"]
    assert stats["source"] == len(previous) > 0, "No source posts or wrong migration baseline"
    assert stats["placed"] == len(packed) > 0
    assert stats["source"] == stats["placed"] + stats["omitted"]
    assert sum(stats["reasons"].values()) == stats["omitted"]
    assert manifest["collision"] == before_manifest["collision"], "Unrelated collision changed"
    for key in ("buildings", "buildingPlacements", "lights", "roads"):
        assert manifest.get(key) == before_manifest.get(key), f"Unrelated {key} changed"
    prior = {row["id"]: row for row in previous}
    assert len(prior) == len(previous)
    assert len({row["id"] for row in packed}) == len(packed)
    triangles, tree = solid_support(out, manifest)
    max_base_error = max_collision_error = max_rim_error = 0.0
    unresolved = []
    shifted = []
    angles = np.arange(8) * np.pi / 4
    base_offsets = 0.05 * np.c_[np.cos(angles), np.sin(angles)]
    for row in packed:
        assert row["id"] in prior and row["object_id"] and row["height"] == 2.7
        recorded = np.asarray(row["recorded"], float)
        assert recorded.shape == (3,) and np.isfinite(recorded).all()
        before = prior[row["id"]]
        adjustment = float(recorded[1] - before["recorded"][1])
        if abs(adjustment) > 0.001:
            shifted.append(
                dict(
                    id=row["id"],
                    before=before["recorded"],
                    after=row["recorded"],
                    adjustment_m=round(adjustment, 4),
                )
            )
        assert np.allclose(
            recorded[[0, 2]], np.asarray(before["recorded"])[[0, 2]], atol=0.001, rtol=0
        )
        assert row["plates"] == before["plates"] and row["merged"] == before["merged"]
        delta = float(np.max(np.abs(np.asarray(row["base"]) - recorded)))
        assert delta <= 0.003, (row, delta)
        x, y, z = recorded
        if any(
            C.CollisionTile.at(x + dx, z + dz).id not in manifest["collision"]
            for dx, dz in np.vstack(([0, 0], base_offsets))
        ):
            assert np.array_equal(recorded, np.asarray(before["recorded"]))
            unresolved.append(row["id"])
            continue
        error = support_error(triangles, tree, *recorded)
        assert error <= 0.01, (row, error)
        for dx, dz in base_offsets:
            rim_error = support_error(triangles, tree, x + dx, y, z + dz)
            assert rim_error <= 0.055, (row, dx, dz, rim_error)
            max_rim_error = max(max_rim_error, rim_error)
        max_base_error = max(max_base_error, delta)
        max_collision_error = max(max_collision_error, error)
    assert len(unresolved) == stats["unresolved"]
    assert len(packed) > len(unresolved), "No resolved posts were verified"
    return dict(
        **stats,
        packed_poles=len(packed),
        max_base_error=max_base_error,
        max_collision_support_error=max_collision_error,
        max_collision_rim_error=max_rim_error,
        unresolved_ids=unresolved,
        shifted_examples=sorted(shifted, key=lambda row: -abs(row["adjustment_m"]))[:3],
        omitted_examples=[
            dict(id=row["id"], before=row["recorded"])
            for row in previous
            if row["id"] not in {p["id"] for p in packed}
        ][:3],
        dynamic_body_metadata="verified; runtime collision behavior not exercised",
    )


def public_payloads(base, tile, manifest, generation, revision):
    url = urljoin(base.rstrip("/") + "/", tile + "/manifest.json")
    with urlopen(url, timeout=20) as response:
        served = json.load(response)
    assert served.get("pass_kind") == "full" and not served.get("stale"), served
    assert served["generation"] == generation, "City is not serving the registered migration"
    assert served["revision"] == revision, "City is serving another revision"
    files, sizes = 0, 0

    def fetch(entry, expected):
        nonlocal files, sizes
        assert entry["sha256"] == expected["sha256"] and entry["bytes"] == expected["bytes"]
        with urlopen(urljoin(url, entry["url"]), timeout=30) as response:
            data = response.read()
        assert len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"]
        files += 1
        sizes += len(data)
        return data

    visual = fetch(served["visual"][tile], manifest["visual"][tile])
    document, _ = read_glb(visual)
    # Migration geometry is often in external content-addressed buffers.
    for buffer in document.get("buffers", []):
        uri = buffer.get("uri")
        if not uri or uri.startswith("data:"):
            continue
        with urlopen(
            urljoin(urljoin(url, served["visual"][tile]["url"]), uri), timeout=30
        ) as response:
            data = response.read()
        assert len(data) == buffer["byteLength"]
        name = Path(uri).stem
        assert len(name) == 64 and hashlib.sha256(data).hexdigest() == name
        files += 1
        sizes += len(data)
    assert set(served["collision"]) == set(manifest["collision"])
    for cell, entry in served["collision"].items():
        fetch(entry, manifest["collision"][cell])
    return dict(files=files, bytes=sizes, generation=served["generation"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--farm-root", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--public-url", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    meta = json.loads((args.farm_root / "revisions" / args.revision / "revision.json").read_text())
    assert meta["code_revision"] == args.commit
    report = []
    for tile in ("t0_0_0", "t0_0_1", "t0_1_0"):
        out = args.farm_root / "products" / meta["generation"] / tile
        receipt = json.loads((out / "ready.json").read_text())
        assert receipt["revision"] == args.revision
        assert receipt["generation"] == meta["generation"]
        assert receipt["applied"] == ["pole-support@1"], (
            "Unexpected regeneration or migration scope"
        )
        manifest = json.loads((out / "manifest.json").read_text())
        before_out = args.farm_root / "products" / receipt["source_generation"] / tile
        before_manifest = json.loads((before_out / "manifest.json").read_text())
        assert manifest["pass_kind"] == before_manifest["pass_kind"] == "full"
        assert len(manifest["collision"]) == 16
        result = dict(
            tile=tile,
            commit=args.commit,
            revision=args.revision,
            migration_seconds=receipt["seconds"],
            support=verify(out, manifest, before_out, before_manifest),
            public=public_payloads(
                args.public_url, tile, manifest, meta["generation"], args.revision
            ),
        )
        report.append(result)
        print(json.dumps(result), flush=True)
    result = dict(
        tiles=report,
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        seconds=round(time.monotonic() - started, 3),
        evidence="existing full products migrated; no generator rebuild",
    )
    (C.work_root() / "prop-support-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            dict(report=str(C.work_root() / "prop-support-result.json"), seconds=result["seconds"])
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
