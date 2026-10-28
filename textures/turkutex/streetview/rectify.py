"""Direct wall-plane sampling, shared by refs and offline CV experiments.

Coordinates are ENH, corners bottom-left, bottom-right, top-right, top-left.
This removes perspective for the supplied pose; it does not correct pose error,
non-planar architecture, missing panorama coverage or foreground occlusion.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .camera import sample_bilinear


def rectified_points(
    corners: np.ndarray,
    long_side: int | None = None,
    *,
    pixels_per_metre: float | None = None,
    max_side: int = 4096,
) -> np.ndarray:
    """Wall-plane pixel centres. Choose long-side sizing or fixed metric sampling.

    Legacy long-side sizing keeps the refs minimum of 16 pixels per dimension.
    Metric sizing preserves narrow walls down to one pixel. The size cap reduces
    both dimensions together; effective density is output size / wall extent.
    """
    corners = np.asarray(corners, dtype=np.float64)
    if corners.shape != (4, 3) or not np.isfinite(corners).all():
        raise ValueError("corners must be four finite ENH points")
    across, up = corners[1] - corners[0], corners[3] - corners[0]
    width, height = float(np.linalg.norm(across)), float(np.linalg.norm(up))
    if min(width, height) <= 0 or np.linalg.norm(np.cross(across, up)) < width * height * 0.999:
        raise ValueError("wall must have perpendicular, nonzero edges")
    if not np.allclose(corners[2], corners[0] + across + up, rtol=0, atol=0.005):
        raise ValueError("wall corners must form a rectangle")
    if (long_side is None) == (pixels_per_metre is None):
        raise ValueError("choose exactly one of long_side and pixels_per_metre")
    scale = pixels_per_metre if pixels_per_metre is not None else long_side / max(width, height)
    if not np.isfinite(scale) or scale <= 0 or max_side < 16:
        raise ValueError("sampling density must be positive and max_side at least 16")
    scale = min(scale, max_side / max(width, height))
    minimum = 16 if long_side is not None else 1
    w, h = max(minimum, round(width * scale)), max(minimum, round(height * scale))
    u, v = np.meshgrid((np.arange(w) + 0.5) / w, 1 - (np.arange(h) + 0.5) / h)
    return corners[0] + u[..., None] * across + v[..., None] * up


def sample_wall(image: np.ndarray, model, points: np.ndarray, camera_position: np.ndarray):
    """Return sampled RGB and source-coverage mask; no network or image synthesis."""
    directions = points - np.asarray(camera_position)
    height, width = image.shape[:2]
    u, v = model.uv_from_dir(directions, width, height)
    rgb = sample_bilinear(image, u, v)
    valid = np.ones(points.shape[:2], bool)
    if hasattr(model, "project"):
        _, _, valid = model.project(directions, width, height)
        rgb[~valid] = 0
    return rgb, valid


def rectify_record(view: dict, panorama: Path, *, pixels_per_metre=32.0, max_side=2048):
    """Re-render a saved refs view using an explicitly supplied local panorama.

    The panorama must have the same camera/layout as the recorded source; changing
    its resolution is supported. Provider pose conventions remain owned by refs.
    """
    from .camera import EquirectModel
    from .panos import PanoMeta
    from .refs import equirect_model

    meta = PanoMeta.from_dict(
        dict(
            view["pano"],
            source=view["source"],
            id=view["pano_id"],
            date=view.get("date"),
            **view.get("source_camera_model", {}),
        )
    )
    points = rectified_points(
        view["facade_corners_EN_H"], pixels_per_metre=pixels_per_metre, max_side=max_side
    )
    model = equirect_model(meta)
    if view.get("image_to_grid") is not None:
        model = EquirectModel(0, rotation=np.asarray(view["image_to_grid"], float))
    elif (
        "reference_version" not in view
        and "upright panorama pose" not in view.get("notes", "")
        and isinstance(model, EquirectModel)
    ):
        # Historical experiment records explicitly omitted pitch/roll/convergence.
        # Reproduce their recorded crop rather than silently changing the baseline.
        heading = -meta.heading_rad + np.pi if meta.source == "lookaround" else meta.heading_rad
        model = EquirectModel(heading)
    with Image.open(panorama) as source:
        rgb, valid = sample_wall(
            np.asarray(source.convert("RGB")),
            model,
            points,
            view["camera"]["pos_EN_H"],
        )
    return np.clip(rgb, 0, 255).astype(np.uint8), valid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", type=Path, required=True)
    parser.add_argument("--panorama", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="local-only PNG")
    parser.add_argument("--pixels-per-metre", type=float, default=32)
    parser.add_argument("--max-side", type=int, default=2048)
    args = parser.parse_args()
    rgb, valid = rectify_record(
        json.loads(args.view.read_text()),
        args.panorama,
        pixels_per_metre=args.pixels_per_metre,
        max_side=args.max_side,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb).save(args.output)
    Image.fromarray(valid.astype(np.uint8) * 255).save(args.output.with_suffix(".mask.png"))


if __name__ == "__main__":
    main()
