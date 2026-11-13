"""elevation: one described wall drawn square-on from its compiled triangles, without a browser.

    python -m turku3d.world.kinds.building.elevation FILE.json [--wall ID] [--photo RECTIFIED.jpg] [--out FILE.jpg]

The check for positions and proportions when no browser slot is free, in under a second: the wall at `--scale`
pixels per metre in flat material colours, under the rectified photo of the same wall (`building.rectify`, same
scale) when one is given. It shows where things are, not how the materials look; the workbench shot does that.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from . import compile as compile_module
from . import describe
from .workbench import shell_for, work_root

LIGHT = np.array([-0.35, 0.6, 0.72]) / np.linalg.norm([-0.35, 0.6, 0.72])
SKY = (150, 170, 190)


def draw(output, frame: dict, scale: float, rows: int | None = None):
    """The compiled parts seen square-on from outside the wall of `frame`, nearest surfaces last."""
    from PIL import Image, ImageDraw

    x, h, z = output.origin
    origin = np.array([compile_module.ORIGIN_E + x, compile_module.ORIGIN_N - z, h])
    p0 = np.asarray(frame["p0"], float) - origin[:2]
    p1 = np.asarray(frame["p1"], float) - origin[:2]
    width = float(np.linalg.norm(p1 - p0))
    along = (p1 - p0) / width
    outward = np.array([along[1], -along[0]])
    triangles = []
    top = 0.0
    for part in output.parts:
        engine = np.asarray(part.positions, float)
        plan = np.column_stack([engine[:, 0], -engine[:, 2]]) - p0
        local = np.column_stack([plan @ along, engine[:, 1], plan @ outward])
        corners = local[np.asarray(part.triangles).reshape(-1, 3)]
        normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        length = np.linalg.norm(normal, axis=1)
        # Faces turned towards the viewer, from 1.2 m behind the wall plane to 3 m in front of it.
        keep = (
            (length > 1e-9)
            & (normal[:, 2] > 1e-6)
            & (corners[:, :, 2].max(axis=1) > -1.2)
            & (corners[:, :, 2].min(axis=1) < 3.0)
        )
        if not keep.any():
            continue
        corners, normal = corners[keep], normal[keep] / length[keep, None]
        top = max(top, float(corners[:, :, 1].max()))
        linear = np.clip(np.asarray(part.material.color[:3], float), 0, 1)
        srgb = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
        shade = 0.5 + 0.5 * np.clip(normal @ LIGHT, 0, 1)
        for triangle, s in zip(corners, shade, strict=True):
            triangles.append(
                (triangle[:, 2].mean(), triangle, tuple(int(255 * c * s) for c in srgb))
            )
    base = frame.get("base_h", origin[2]) - origin[2]
    rows = rows or int((top - base + 0.5) * scale)
    image = Image.new("RGB", (int(width * scale), rows), SKY)
    pen = ImageDraw.Draw(image)
    triangles.sort(key=lambda item: item[0])
    for _depth, triangle, colour in triangles:
        pen.polygon(
            [(float(u * scale), float(rows - (v - base) * scale)) for u, v, _d in triangle],
            fill=colour,
        )
    return image, len(triangles)


def main(argv=None) -> int:
    from PIL import Image

    parser = argparse.ArgumentParser(prog="building.elevation")
    parser.add_argument("description", type=Path)
    parser.add_argument(
        "--wall", help="id of the described wall to draw; the first with a frame otherwise"
    )
    parser.add_argument(
        "--photo", type=Path, help="rectified photo of the same wall at the same scale"
    )
    parser.add_argument("--scale", type=float, default=40.0, help="pixels per metre")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    stored = json.loads(args.description.read_text())
    key = stored["id"].split("/", 1)[1]
    walls = [
        w
        for w in stored.get("walls", [])
        if "frame" in w and (args.wall is None or w.get("id") == args.wall)
    ]
    if not walls:
        raise SystemExit("the description has no such wall with a frame")
    frame = walls[0]["frame"]
    shell, register = shell_for(key, frame["p0"])
    output = compile_module.compile(describe.effective(key, shell, register, stored))
    photo = Image.open(args.photo).convert("RGB") if args.photo else None
    image, drawn = draw(output, frame, args.scale, photo.height if photo else None)
    if photo:
        sheet = Image.new(
            "RGB", (max(image.width, photo.width), photo.height * 2 + 6), (20, 20, 20)
        )
        sheet.paste(photo, (0, 0))
        sheet.paste(image, (0, photo.height + 6))
        image = sheet
    target = args.out or work_root() / "elevation" / f"{args.description.stem}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, quality=88)
    print(
        json.dumps(
            {
                "out": str(target),
                "size": image.size,
                "triangles": drawn,
                "ms": round((time.perf_counter() - started) * 1000),
                "report": output.report[:8],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
