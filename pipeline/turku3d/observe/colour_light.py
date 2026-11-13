"""The light a panorama was taken in: overcast or sun, measured from the panorama itself.

Diffuse light under cloud shows a paint nearly as it is; sun gives a warm lit side, a blue shaded side and flare.
One figure a panorama, `overcast` in 0..1, from three things an equirect shows without any model:

- the sky (well above the horizon, not foliage, not the black a Look Around equirect has at its poles): the share
  of it that is blue;
- a burnt-out sun: saturated pixels in a sky that also has blue (bright cloud saturates too, but has no blue);
- the ground below the horizon (grey pixels: asphalt, gravel, paving): how far its lightness spreads. Cast
  shadows on a sunlit street make it wide; under cloud it is narrow.

A single panorama's figure (`own`) is noisy; `overcast` is the median over the panoramas of the same visit nearby.

    python -m turku3d.observe colour --light [--out ROOT]    every panorama the packets under ROOT use;
                                                             writes ROOT/panorama-light.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import colour

WIDTH = 1024  # the equirect is measured at this width
RUN_M = 120.0  # panoramas of one provider and date this near each other share their weather


def measure(equirect: np.ndarray) -> dict:
    """{'overcast', 'blue', 'sun', 'ground_range', 'sky'} of one equirect (RGB, any size, horizon at mid-height)."""
    step = max(1, equirect.shape[1] // WIDTH)
    small = equirect[::step, ::step]
    h = small.shape[0]
    lab = colour.to_lab(small.reshape(-1, 3)).reshape(h, -1, 3)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
    row = lambda elevation: int(np.clip((0.5 - elevation / 180.0) * h, 0, h))  # noqa: E731
    up = slice(row(80), row(18))
    leaf = (hue > 85) & (hue < 175) & (chroma > 12)
    sky = (lab[..., 0] > 52) & ~leaf & (small.max(-1) > 12)
    sky[: up.start] = False
    sky[up.stop:] = False
    count = int(sky.sum())
    blue = sky & (lab[..., 2] < -12) & (chroma > 16)  # clear sky; blue-grey cloud is duller than this
    burnt = sky & (small.min(-1) >= 250)
    blue_share = float(blue.sum() / count) if count else 0.0
    sun = float(burnt.sum() / count) if count else 0.0
    down = slice(row(-14), row(-55))
    ground = np.zeros_like(sky)
    ground[down] = (chroma[down] < 9) & (small[down].max(-1) > 25)
    values = lab[..., 0][ground]
    ground_range = float(np.quantile(values, 0.92) - np.quantile(values, 0.08)) if len(values) > 500 else 20.0
    by_sky = float(np.clip(1.0 - blue_share / 0.30, 0.0, 1.0)) if count > 2000 else 0.5
    by_ground = float(np.clip(1.0 - (ground_range - 16.0) / 28.0, 0.2, 1.0))
    flare = 0.6 if sun > 0.004 and blue_share > 0.08 else 1.0
    # The sky decides; the ground can halve the figure (its spread also has lorries, patches and road paint in it).
    return {"own": round(by_sky * (0.5 + 0.5 * by_ground) * flare, 2), "blue": round(blue_share, 3), "sun": round(sun, 4),
            "ground_range": round(ground_range, 1), "sky": round(count / max(1, sky[up].size), 2)}


def of_root(root: Path) -> dict:
    """{'provider:id': measure} for every panorama the packets under `root` took crops from; kept in
    root/panorama-light.json and added to on later calls."""
    from turkutex.streetview.panos import PanoCache

    from .panoramas import GOOGLE_ZOOM, LOOKAROUND_ZOOM

    Image.MAX_IMAGE_PIXELS = None
    cache = PanoCache()
    path = root / "panorama-light.json"
    out = json.loads(path.read_text()) if path.exists() else {}
    for views in sorted((root / "buildings").glob("*/views.json")):
        for crop in json.loads(views.read_text())["crops"]:
            pano = crop["panorama"]
            key = f"{pano['provider']}:{pano['id']}"
            if key in out:
                continue
            relative = (Path("google") / pano["id"] / f"pano_z{GOOGLE_ZOOM}.jpg" if pano["provider"] == "google"
                        else Path("lookaround") / pano["id"] / f"equi_z{LOOKAROUND_ZOOM}.jpg")
            image = cache.read_path(relative)
            if not image.exists():
                continue
            with Image.open(image) as im:
                im.draft("RGB", (WIDTH * 2, WIDTH))
                out[key] = {**measure(np.asarray(im.convert("RGB"))), "date": pano["date"],
                            "position": pano["position"][:2]}
    # One visit of a camera car has one weather: a panorama's figure is the median of its own and its
    # neighbours' of the same provider and date within RUN_M (single figures are noisy: a lorry, a wet patch).
    for key, own in out.items():
        near = [v["own"] for k, v in out.items() if k.split(":")[0] == key.split(":")[0] and v["date"] == own["date"]
                and np.hypot(v["position"][0] - own["position"][0], v["position"][1] - own["position"][1]) <= RUN_M]
        own["overcast"] = round(float(np.median(near)), 2)
        own["run"] = len(near)
    path.write_text(json.dumps(out, indent=0))
    return out
