"""Build two real tiles and check packed sign bases against source and solid collision."""

import argparse
import importlib.util
import json
import pickle
import subprocess
from pathlib import Path

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.inputs import gather
from turku3d.coarse.packaging import current_code
from turku3d.coarse.sources import Sources
from turku3d.col.format import read_col
from turku3d.col.materials import table
from turku3d.package.gltf import read_glb
from turku3d.stage_cache import decompress_payload

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
    output.push({id:e.pole_id, object_id:e.object_id, height:e.pole_height, base, count});
  }
}
console.log(JSON.stringify(output));
"""


def packed_poles(out, manifest):
    visual = out / manifest["visual"][manifest["tile"]]["url"]
    # First-pass GLBs need no decoder if their metadata contains no pole candidates.
    document, _ = read_glb(visual.read_bytes())
    if manifest["pass_kind"] == "first":
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


def verify(root, out, manifest):
    assert manifest["code"] == current_code(), "Build did not use frozen deployed generator"
    packed = packed_poles(out, manifest)
    if manifest["pass_kind"] == "first":
        assert not packed
        return dict(poles=0, expected="structural first pass")
    tile = C.VisualTile.parse(manifest["tile"])
    inputs = gather(tile, root, Sources())
    stats = manifest["pole_support"]
    assert stats["source"] == len(inputs.poles) > 0, "No real pole source coverage"
    assert manifest["poles"] == stats["placed"] == len(packed) > 0
    assert stats["source"] == stats["placed"] + stats["omitted"]
    assert sum(stats["reasons"].values()) == stats["omitted"]
    assert sum(stats["support"].values()) == stats["placed"]
    metrics = json.loads((out / "stage-times.json").read_text())
    path = root / ".stages" / tile.id / "pole_support" / (metrics["pole_support"]["key"] + ".pkl")
    poles, cached_stats = pickle.loads(decompress_payload(path.read_bytes()[32:]))
    assert cached_stats == stats
    expected = {f"{tile.id}:{p.object_id}": p for p in poles}
    assert len(expected) == len(poles)
    assert {p["id"] for p in packed} == set(expected)
    source = {p.object_id: p for p in inputs.poles}
    triangles, tree = solid_support(out, manifest)
    max_base_error = max_collision_error = 0.0
    angles = np.arange(8) * np.pi / 4
    base_offsets = 0.05 * np.c_[np.cos(angles), np.sin(angles)]
    max_rim_error = 0.0
    for row in packed:
        pole = expected[row["id"]]
        original = source[pole.object_id]
        assert (pole.x, pole.z) == (original.x, original.z), "Mapped pole moved horizontally"
        assert row["object_id"] == pole.object_id and row["height"] == 2.7
        delta = float(np.max(np.abs(np.asarray(row["base"]) - [pole.x, pole.y, pole.z])))
        assert delta <= 0.003, (row, delta)
        error = support_error(triangles, tree, *row["base"])
        assert error <= 0.01, (row, error)
        x, y, z = row["base"]
        for dx, dz in base_offsets:
            rim_error = support_error(triangles, tree, x + dx, y, z + dz)
            assert rim_error <= 0.055, (row, dx, dz, rim_error)
            max_rim_error = max(max_rim_error, rim_error)
        max_base_error, max_collision_error = (
            max(max_base_error, delta),
            max(max_collision_error, error),
        )
    return dict(
        **stats,
        packed_poles=len(packed),
        max_base_error=max_base_error,
        max_collision_support_error=max_collision_error,
        max_collision_rim_error=max_rim_error,
        dynamic_body_metadata="complete; runtime collision behavior not exercised by this probe",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    root = Path("/srv/turku3d/work/coarse")
    spec = importlib.util.spec_from_file_location(
        "build_probe", Path(__file__).with_name("probe-builds.py")
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    for tile in ("t0_0_0", "t0_1_0"):
        result = probe.run(
            root,
            tile_id=tile,
            first_budget=45,
            full_budget=150,
            verify=lambda out, manifest: verify(root, out, manifest),
        )
        print(json.dumps(dict(commit=args.commit, generator=current_code(), **result)), flush=True)


if __name__ == "__main__":
    main()
