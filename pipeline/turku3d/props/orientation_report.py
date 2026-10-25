"""Draw published furniture headings over the cached true ortho for street QA."""

import json
import math
from collections import Counter

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.merge import merge

from turku3d import config as C
from turku3d.layers import layer_root

COLORS = {
    "sign": "#ed2626",
    "bus_stop": "#cc40e0",
    "bench": "#22c55e",
    "lamp": "#f6bd16",
    "bin": "#33b5e5",
    "bollard": "#ffffff",
}


def main():
    root = layer_root() / "props"
    records = json.loads((root / "instances.json").read_text())
    extent = (0, 0, 500, 500)
    left, bottom, right, top = (
        C.ORIGIN_E + extent[0],
        C.ORIGIN_N - extent[3],
        C.ORIGIN_E + extent[2],
        C.ORIGIN_N - extent[1],
    )
    paths = sorted((C.raw_dir("ortho") / "ortho2025/work-3x3").glob("*.tif"))
    sources = [rasterio.open(p) for p in paths]
    chosen = [
        src
        for src in sources
        if src.bounds.right > left
        and src.bounds.left < right
        and src.bounds.top > bottom
        and src.bounds.bottom < top
    ]
    try:
        rgb, _ = merge(chosen, bounds=(left, bottom, right, top), res=0.5)
    finally:
        for src in sources:
            src.close()
    image = Image.fromarray(np.moveaxis(rgb[:3], 0, 2).astype("uint8"))
    draw = ImageDraw.Draw(image)
    counts = Counter()
    for p in records:
        x, z = p["x"], p["z"]
        if not (0 <= x < 500 and 0 <= z < 500):
            continue
        counts[p["kind"]] += 1
        color = COLORS.get(p["kind"], "#ffffff")
        px, py = x * 2, z * 2
        ex = px + math.sin(p["yaw"]) * 16
        ey = py + math.cos(p["yaw"]) * 16
        draw.line((px, py, ex, ey), fill="#111111", width=5)
        draw.line((px, py, ex, ey), fill=color, width=3)
        draw.ellipse((ex - 3, ey - 3, ex + 3, ey + 3), fill=color, outline="#111111")
    out = C.work_root() / "reports/props"
    out.mkdir(parents=True, exist_ok=True)
    image.save(out / "orientation-ortho.png")
    (out / "orientation.json").write_text(
        json.dumps({"area_engine_xz": extent, "counts": counts, "legend": COLORS}, indent=2) + "\n"
    )
    print(out / "orientation-ortho.png")


if __name__ == "__main__":
    main()
