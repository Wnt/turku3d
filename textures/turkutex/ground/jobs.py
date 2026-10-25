"""Create quota-aware image_gen jobs for the eight ground library materials."""

import argparse
import json
from pathlib import Path

from ..paths import TEXTURES_DIR


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    materials = json.loads((TEXTURES_DIR / "library/ground.json").read_text())["materials"]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w") as f:
        for material in materials:
            prompt = (
                "Write everything in English. Use the built-in image_gen tool to generate exactly one "
                "photorealistic square seamless game ground ALBEDO texture: "
                + material["description"]
                + ". Orthographic straight down, covers exactly 2 by 2 metres, fills the entire image. "
                "Flat diffuse neutral illumination; no baked shadows, ambient occlusion, highlights, "
                "perspective, text, borders, objects, vehicles, people or leaves. Fine sharp natural "
                "surface detail. Opposite edges must tile seamlessly. Output 1024 by 1024 or higher. "
                "Do not generate normal or roughness maps. Do not write code. Return the generated image."
            )
            f.write(
                json.dumps(
                    {
                        "id": material["key"],
                        "prompt": prompt,
                        "expect_images": 1,
                        "prompt_version": "ground-v1",
                        "meta": material,
                    }
                )
                + "\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
