"""Post-process accepted runner images, record provenance, encode shared KTX2 arrays."""

import argparse
import json
from pathlib import Path

from ..paths import TEXTURES_DIR
from ..post.pipeline import process, sha
from .encode import encode
from .ortho import gpu_bytes


def build(run: Path, out: Path) -> dict:
    materials = json.loads((TEXTURES_DIR / "library/ground.json").read_text())["materials"]
    records = {
        r["id"]: r
        for line in (run / "out.jsonl").read_text().splitlines()
        if (r := json.loads(line))["ok"]
    }
    manifest = {
        "version": 1,
        "materials": [],
        "arrays": {},
        "gpu_bytes_8bpp": 8 * 3 * gpu_bytes(1024, 1024),
    }
    for material in materials:
        key = material["key"]
        rec = records[key]
        primary = rec.get("primary")
        # Runner's stable copied image paths, not generated_images ephemeral locations.
        images = sorted((run / "images" / key).glob("*.png"))
        if not images:
            raise ValueError(f"no generated image for {key}")
        source = (
            Path(primary["path"])
            if isinstance(primary, dict) and Path(primary["path"]).exists()
            else images[0]
        )
        target = out / key
        result = process(
            source,
            target,
            name=key,
            tile="xy",
            size="1024x1024",
            material=material["preset"],
            ktx2=False,
            sobel=True,
        )
        if not result["qa_after"]["pass_seam"]:
            raise ValueError(f"{key}: albedo seam QA failed: {result['qa_after']}")
        for name in ["albedo", "normal", "orm"]:
            encode(
                [target / f"{name}.png"],
                target / f"{name}.ktx2",
                linear=name != "albedo",
                uastc=name == "normal",
                normal=name == "normal",
            )
        manifest["materials"].append(
            {
                **material,
                "source": str(source),
                "source_sha256": sha(source),
                "provenance": {
                    "kind": "OpenAI image_gen",
                    "thread_id": rec["thread_id"],
                    "job_key": rec["key"],
                    "prompt_version": rec.get("prompt_version", "ground-v1"),
                    "license": "Generated output under OpenAI terms; not a CC0 source asset",
                },
                "qa": result["qa_after"],
                "maps": result["maps"],
                "files": {
                    n: {"sha256": sha(target / f"{n}.ktx2"), "uri": f"{key}/{n}.ktx2"}
                    for n in ["albedo", "normal", "orm"]
                },
            }
        )
        print(f"library {key}: seam QA passed", flush=True)
    for name in ["albedo", "normal", "orm"]:
        target = out / f"{name}.array.ktx2"
        manifest["arrays"][name] = {
            "uri": target.name,
            **encode(
                [out / m["key"] / f"{name}.png" for m in materials],
                target,
                linear=name != "albedo",
                uastc=name == "normal",
                normal=name == "normal",
            ),
        }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    build(a.run, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
