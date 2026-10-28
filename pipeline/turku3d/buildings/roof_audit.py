"""Manual office roof colour audit and before/after full-layer visual packaging.

python -m turku3d.buildings.roof_audit
Uses the existing full building shells without rerunning roofer or changing facades.
"""

import json
import struct
import subprocess

import numpy as np
import shapely
from PIL import Image

from turku3d import config as C
from turku3d.buildings.facades import ortho_roof_key, roof_samples
from turku3d.buildings.inputs import Building
from turku3d.coarse.build import default_library, package_visual
from turku3d.package.gltf import read_glb


def main():
    root = C.work_root() / "reports/roofs"
    root.mkdir(parents=True, exist_ok=True)
    layer = C.data_root() / "build/layers/buildings"
    tile = C.VisualTile.parse("t0_0_0")
    source = layer / f"{tile.id}.vis.glb"
    document, blob = read_glb(source.read_bytes())
    footprints = {
        f["id"]: shapely.geometry.shape(f["geometry"])
        for f in json.loads((layer / "footprints.geojson").read_text())["features"]
    }
    ortho = sorted((C.raw_dir("ortho") / "ortho2022to/work-3x3").glob("*.tif"))
    library = default_library()
    decoded = root / "old-roof-albedo.png"
    if not decoded.exists():
        subprocess.run(
            [
                "ktx",
                "extract",
                "--transcode",
                "rgb8",
                str(library / "roof_standing_seam_metal/albedo.ktx2"),
                str(decoded),
            ],
            check=True,
        )
    old = np.median(np.asarray(Image.open(decoded).convert("RGB")).reshape(-1, 3), axis=0)
    ids = sorted(
        {
            n["extras"]["building_id"]
            for n in document["nodes"]
            if n.get("extras", {}).get("surface_type") == "RoofSurface"
        }
    )
    colours, records = {}, []
    for bid in ids:
        b = Building(bid, footprints[bid])
        key, evidence = ortho_roof_key(b, ortho)
        rgb = np.asarray(evidence["rgb_median"])
        pixels = roof_samples(b, ortho)
        colours[bid] = rgb.tolist()
        records.append(
            dict(
                building=bid,
                material=key,
                **evidence,
                old_rgb=old.tolist(),
                before_mae_rgb=float(np.abs(pixels - old).mean()),
                after_mae_rgb=float(np.abs(pixels - rgb).mean()),
            )
        )
    for phase in ["before", "after"]:
        # Repackage the same real full-pipeline geometry in both phases. Only the
        # roof-colour metadata differs; node IDs, facades and triangles are untouched.
        doc = json.loads(json.dumps(document))
        for n in doc["nodes"]:
            e = n.get("extras", {})
            if e.get("surface_type") == "RoofSurface":
                e.pop("roof_rgb", None)
                if phase == "after":
                    e["roof_rgb"] = colours[e["building_id"]]
        raw = json.dumps(doc, separators=(",", ":")).encode()
        raw += b" " * (-len(raw) % 4)
        data = (
            struct.pack("<III", 0x46546C67, 2, 28 + len(raw) + len(blob))
            + struct.pack("<II", len(raw), 0x4E4F534A)
            + raw
            + struct.pack("<II", len(blob), 0x004E4942)
            + blob
        )
        directory = root / phase / tile.id
        stage = directory / ".stage"
        stage.mkdir(parents=True, exist_ok=True)
        inputs = stage / "buildings.vis.glb"
        inputs.write_bytes(data)
        packaged, stats = package_visual(tile, [inputs], stage, directory.parent, library)
        (directory / "vis.glb").write_bytes(packaged)
        (directory / "manifest.json").write_text(
            json.dumps(dict(tile=tile.id, buildings=len(ids), stats=stats), indent=2) + "\n"
        )
    report = dict(
        tile=tile.id,
        roofs=len(ids),
        old_texture_rgb=old.tolist(),
        before_mean_roof_mae=float(np.mean([r["before_mae_rgb"] for r in records])),
        after_mean_roof_mae=float(np.mean([r["after_mae_rgb"] for r in records])),
        records=records,
    )
    (root / "colour-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "records"}), flush=True)


if __name__ == "__main__":
    main()
