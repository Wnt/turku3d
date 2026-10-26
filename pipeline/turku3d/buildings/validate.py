"""Validate actual layer NPZ triangles in Rapier, without creating shipped artifacts."""

import json
import subprocess

import numpy as np

from turku3d import config as C
from turku3d.layers import read_manifest, write_manifest

from .build import sha256, write_json


def main():
    layer = C.work_root() / "build/layers/buildings"
    manifest = read_manifest(layer)
    manifest_hash = sha256(layer / "MANIFEST.json")
    report = json.loads((layer / "metrics.json").read_text())
    footprints = json.loads((layer / "footprints.geojson").read_text())
    facets = json.loads((layer / "facades.geojson").read_text())
    bases = {}
    facade_segments = {}
    for f in facets["features"]:
        p = f["properties"]
        bases[p["building_id"]] = min(bases.get(p["building_id"], float("inf")), p["base_height"])
        a, b = f["geometry"]["coordinates"]
        facade_segments.setdefault(p["building_id"], []).append(
            [
                a[0] - C.ORIGIN_E,
                C.ORIGIN_N - a[1],
                b[0] - C.ORIGIN_E,
                C.ORIGIN_N - b[1],
                p["base_height"],
            ]
        )
    for record in report["buildings"]:
        if not record.get("shell_valid") or record["building_id"] in bases:
            continue
        evidence = record.get("fallback", {}).get("height_evidence", {})
        if "base_m" in evidence:
            bases[record["building_id"]] = evidence["base_m"]
    data = {
        "tiles": [],
        "footprints": footprints,
        "base_heights": bases,
        "facade_segments": facade_segments,
        "building_ids": report["collision_building_ids"],
    }
    for name in sorted(manifest["tiles"]):
        if not name.endswith(".col.npz"):
            continue
        with np.load(layer / name) as tile:
            data["tiles"].append(
                {
                    "positions": tile["positions"].ravel().tolist(),
                    "indices": tile["indices"].ravel().tolist(),
                    "building_index": tile["building_index"].tolist(),
                }
            )
    cache = C.work_root() / "cache/buildings"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "walkers-input.json"
    write_json(path, data)
    result_path = cache / "walkers-result.json"
    result_path.unlink(missing_ok=True)
    result = subprocess.run(
        ["node", str(C.REPO_ROOT / "tools/validator/src/buildings.ts"), str(path), str(result_path)]
    )
    if sha256(layer / "MANIFEST.json") != manifest_hash:
        print(
            "Layer changed during validation; rerun buildings-validate against the completed build"
        )
        return 2
    if result_path.exists():
        walkers = json.loads(result_path.read_text())
        write_json(layer / "walkers.json", walkers)
        report["walker_validation"] = walkers
        write_json(layer / "metrics.json", report)
        with (layer / "metrics.md").open("a") as f:
            f.write(
                f"\n## Rapier walker validation\n\n{walkers['walkers']} walkers, "
                f"{walkers['entered']} entered, {walkers['endedInside']} ended inside. "
                f"Elapsed: {walkers['elapsed_ms']} ms.\n\n{walkers['terrain']}.\n"
            )
        write_manifest(
            layer,
            inputs=manifest["inputs"],
            params={
                **manifest["params"],
                "walker_validation": "pass" if walkers["pass"] else "fail",
            },
            code=manifest["code"],
            tiles=list(manifest["tiles"]),
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
