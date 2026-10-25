"""Texture QA metrics.

seam ratio (per axis) = mean |pixel difference across the wrap seam| / mean |difference between
adjacent interior columns (rows)|. 1.0 = the seam is as smooth as the interior; < 1.5 passes
(ARCHITECTURE 1.8: "no visible tiling seams in a 2x2 test (seam ratio < 1.5)").
seam rank = percentile of the seam step among the texture's own adjacent-column steps; pass_seam
accepts ratio < 1.5 OR rank < 95 (a seam on a natural tile joint is invisible).
lowfreq_x / lowfreq_y = |mean luminance of the first quarter - last quarter| / 255 along the axis:
large values mean banding when the texture repeats (ai-textures.md Verification 4a).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SEAM_PASS = 1.5
SEAM_RANK_PASS = 95.0  # or: the seam is no sharper than 95 % of the texture's own column steps


def _lum(img: np.ndarray) -> np.ndarray:
    f = img.astype(np.float32)
    return f[..., :3].mean(-1) if f.ndim == 3 else f


def seam_ratio(img: np.ndarray, axis: str = "x") -> float:
    L = _lum(img)
    if axis == "y":
        L = L.T
    seam = np.abs(L[:, 0] - L[:, -1]).mean()
    interior = np.abs(np.diff(L, axis=1)).mean()
    return float(seam / max(interior, 1e-6))


def seam_rank(img: np.ndarray, axis: str = "x") -> float:
    """Percentile (0-100) of the wrap-seam difference among all interior adjacent column (row)
    differences. A seam that falls on a natural joint has a high ratio but an ordinary rank."""
    L = _lum(img)
    if axis == "y":
        L = L.T
    seam = np.abs(L[:, 0] - L[:, -1]).mean()
    cols = np.abs(np.diff(L, axis=1)).mean(0)
    return float((cols < seam).mean() * 100)


def lowfreq(img: np.ndarray, axis: str = "x") -> float:
    L = _lum(img)
    if axis == "y":
        L = L.T
    q = max(1, L.shape[1] // 4)
    return float(abs(L[:, :q].mean() - L[:, -q:].mean()) / 255)


def metrics(img: np.ndarray, axes: str = "xy") -> dict:
    H, W = img.shape[:2]
    m = {
        "width": W,
        "height": H,
        "seam_x": round(seam_ratio(img, "x"), 3),
        "seam_y": round(seam_ratio(img, "y"), 3),
        "seam_rank_x": round(seam_rank(img, "x"), 1),
        "seam_rank_y": round(seam_rank(img, "y"), 1),
        "lowfreq_x": round(lowfreq(img, "x"), 4),
        "lowfreq_y": round(lowfreq(img, "y"), 4),
        "mean_rgb": [round(float(v), 1) for v in img[..., :3].reshape(-1, 3).mean(0)]
        if img.ndim == 3
        else None,
        "std_lum": round(float(_lum(img).std()), 2),
    }
    m["axes"] = axes
    m["pass_seam"] = all(
        m[f"seam_{a}"] < SEAM_PASS or m[f"seam_rank_{a}"] < SEAM_RANK_PASS for a in axes
    )
    m["pass_lowfreq"] = all(m[f"lowfreq_{a}"] < 0.03 for a in axes)
    return m


def tile_preview(img: np.ndarray, axes: str = "xy", n: int = 2) -> np.ndarray:
    return np.tile(
        img, (n if "y" in axes else 1, n if "x" in axes else 1) + ((1,) if img.ndim == 3 else ())
    )


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    axes = "xy"
    if "--axes" in argv:
        axes = argv[argv.index("--axes") + 1]
    for p in [a for a in argv if not a.startswith("--") and a != axes]:
        img = np.asarray(Image.open(p).convert("RGB"))
        print(json.dumps({"file": str(Path(p)), **metrics(img, axes)}))
    return 0
