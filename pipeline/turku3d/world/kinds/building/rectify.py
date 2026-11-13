"""Measuring aid for writing and reviewing descriptions: a reference photo rectified onto one facade of a shell,
with a metric grid in the description's wall coordinates (u from the facade's left end, v from the wall base).

  python -m turku3d.world.kinds.building.rectify KEY --view VIEW_ID [--facade N] [--out FILE.jpg] [--scale 40]

Without --facade it lists the building's facades as the view sees them. The photo stays outside Git; so does the
output, which goes under $TURKU3D_WORK/workbench/rectified/.
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np

from . import shell as shell_module
from .facade import eaves_height
from .workbench import shell_for, view_pose, work_root


def camera(pose: dict, width: int, height: int):
    """(position, right, up, forward, focal length in pixels) of a view pose in survey axes."""
    yaw, pitch = np.radians(pose["yaw"]), np.radians(pose["pitch"])
    forward = np.array([np.sin(yaw) * np.cos(pitch), np.cos(yaw) * np.cos(pitch), np.sin(pitch)])
    right = np.array([np.cos(yaw), -np.sin(yaw), 0.0])
    up = np.cross(right, forward)
    focal = (height / 2) / np.tan(np.radians(pose["fov"]) / 2)
    return np.array([pose["E"], pose["N"], pose["H"]]), right, up, forward, focal


def project(points, pose, width, height):
    """Pixel coordinates (n, 2) and depth (n,) of survey points."""
    position, right, up, forward, focal = camera(pose, width, height)
    relative = np.asarray(points, float) - position
    depth = relative @ forward
    safe = np.where(depth > 1e-6, depth, np.nan)
    return np.column_stack(
        [width / 2 + focal * (relative @ right) / safe, height / 2 - focal * (relative @ up) / safe]
    ), depth


def main(argv=None) -> int:
    from PIL import Image, ImageDraw

    parser = argparse.ArgumentParser(prog="building.rectify")
    parser.add_argument("key")
    parser.add_argument("--view", required=True)
    parser.add_argument("--facade", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scale", type=float, default=40.0, help="pixels per metre")
    args = parser.parse_args(argv)
    view = view_pose(args.view)
    pose = view["pose"]
    shell_doc, _register = shell_for(args.key, (pose["E"], pose["N"]))
    shell = shell_module.read(shell_doc)
    position = np.array([pose["E"], pose["N"], pose["H"]])
    if args.facade is None:
        for facade in shell.facades:
            datum = float(facade.ground[:, 1].min()) if len(facade.ground) else 0.0
            eaves = eaves_height(facade.outline, facade.width)
            u = np.linspace(0.0, facade.width, 13)
            points = facade.point(u, np.full(13, (datum + eaves) / 2))
            pixels, depth = project(points, pose, view["width"], view["height"])
            facing = (position - points) @ facade.normal > 0.5
            seen = facing & (depth > 0) & (pixels[:, 0] > 0) & (pixels[:, 0] < view["width"])
            note = ""
            if seen.any():
                distance = np.linalg.norm(position - points[seen], axis=1).min()
                note = f"  <- in view, u {u[seen].min():.0f}..{u[seen].max():.0f} m, {distance:.0f} m away"
            print(
                f"#{facade.index}: {facade.width:5.1f} m wide, eaves {eaves - datum:4.1f} m above its base "
                f"(base_h {facade.origin[2] + datum:.2f}), p0 [{facade.p0[0]:.2f}, {facade.p0[1]:.2f}] "
                f"p1 [{facade.p1[0]:.2f}, {facade.p1[1]:.2f}]{note}"
            )
        return 0
    facade = shell.facades[args.facade]
    datum = float(facade.ground[:, 1].min()) if len(facade.ground) else 0.0
    scale = args.scale
    columns, rows = int(facade.width * scale), int((facade.height - datum + 0.5) * scale)
    u, v = np.meshgrid(
        (np.arange(columns) + 0.5) / scale, datum + (rows - np.arange(rows) - 0.5) / scale
    )
    pixels, depth = project(facade.point(u.ravel(), v.ravel()), pose, view["width"], view["height"])
    photo = np.asarray(
        Image.open(
            Path(os.environ.get("TURKU3D_DATA", Path.home() / "turku3d/data"))
            / "qa/refs"
            / Path(view["image"]).name
        ).convert("RGB")
    )
    x, y = np.round(pixels[:, 0]), np.round(pixels[:, 1])
    inside = (depth > 0) & (x >= 0) & (x < view["width"]) & (y >= 0) & (y < view["height"])
    out = np.full((rows * columns, 3), 40, np.uint8)
    out[inside] = photo[y[inside].astype(int), x[inside].astype(int)]
    image = Image.fromarray(out.reshape(rows, columns, 3))
    draw = ImageDraw.Draw(image)
    for metre in range(0, int(facade.width) + 1):
        strong = metre % 5 == 0
        draw.line(
            [(metre * scale, 0), (metre * scale, rows)],
            fill=(255, 255, 0) if strong else (120, 120, 60),
            width=1,
        )
        if strong:
            draw.text((metre * scale + 2, rows - 12), f"u{metre}", fill=(255, 255, 0))
    for metre in range(0, int(facade.height - datum) + 1):
        strong = metre % 5 == 0
        line = rows - metre * scale
        draw.line(
            [(0, line), (columns, line)], fill=(0, 255, 255) if strong else (60, 120, 120), width=1
        )
        draw.text((2, line - 11), f"v{metre}", fill=(0, 255, 255))
    target = args.out or work_root() / "rectified" / f"{args.key}-{args.view}-f{args.facade}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, quality=90)
    print(
        json.dumps(
            {
                "out": str(target),
                "facade": args.facade,
                "width_m": round(facade.width, 2),
                "datum_m": round(datum, 2),
                "base_h": round(float(facade.origin[2]) + datum, 3),
                "eaves_m": round(eaves_height(facade.outline, facade.width) - datum, 2),
                "p0": [round(float(c), 3) for c in facade.p0],
                "p1": [round(float(c), 3) for c in facade.p1],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
