"""Shared-grid ortho fusion. A single calibration is reused by every tile in a build."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter


def colour_calibration(primary, recent, valid) -> dict:
    """Robust per-channel percentile fit on the entire AOI, never on individual tiles."""
    mask = np.asarray(valid, bool) & (primary.min(-1) > 12) & (recent.min(-1) > 12)
    if mask.sum() < 16:
        raise ValueError("not enough overlapping ortho pixels for colour calibration")
    p = np.percentile(primary[mask], [25, 50, 75], axis=0)
    r = np.percentile(recent[mask], [25, 50, 75], axis=0)
    gain = np.clip((p[2] - p[0]) / np.maximum(r[2] - r[0], 8), 0.7, 1.4)
    offset = np.clip(p[1] - gain * r[1], -35, 35)
    return {"gain": gain.tolist(), "offset": offset.tolist(), "samples": int(mask.sum())}


def fuse(primary, recent, primary_valid, recent_valid, calibration, *, pixel_m=0.04):
    """Prefer true ortho; replace temporal outliers and lift broad shadows conservatively.

    Two epochs cannot prove that a car/person was removed. Return an uncertainty mask for QA.
    A shared halo of at least ceil(4*.24/pixel_m) is required for identical filter support.
    """
    p = np.asarray(primary, np.float32)
    r = np.clip(
        np.asarray(recent, np.float32) * np.asarray(calibration["gain"], np.float32)
        + np.asarray(calibration["offset"], np.float32),
        0,
        255,
    )
    pv, rv = np.asarray(primary_valid, bool), np.asarray(recent_valid, bool)
    if p.shape != r.shape or p.shape != (*pv.shape, 3) or rv.shape != pv.shape:
        raise ValueError("ortho shapes do not match")
    if not (pv | rv).all():
        raise ValueError("ortho has uncovered pixels; fetch coverage before building")
    p = np.where(pv[..., None], p, r)
    r = np.where(rv[..., None], r, p)
    lp, lr = p.mean(-1), r.mean(-1)
    sigma = 0.24 / pixel_m
    # Gaussian support is finite (4 sigma); tile halos make the operation spatially invariant.
    bp = gaussian_filter(lp, sigma, mode="nearest")
    br = gaussian_filter(lr, sigma, mode="nearest")
    difference = np.abs(lp - lr)
    p_chroma = p / np.maximum(lp[..., None], 12)
    r_chroma = r / np.maximum(lr[..., None], 12)
    same_surface = np.max(np.abs(p_chroma - r_chroma), -1) < 0.22
    shadow = (lr > lp * 1.30) & same_surface & (lp < 105)
    transient = (np.abs(lp - bp) > np.abs(lr - br) + 18) & (difference > 25)
    use_recent = rv & (shadow | transient | ~pv)
    # Soft selection avoids hard temporal cut lines; all filtering is in world metres.
    blend = gaussian_filter(use_recent.astype(np.float32), 0.06 / pixel_m, mode="nearest")
    blend = np.where(~pv, 1, np.where(~rv, 0, blend))
    combined = p * (1 - blend[..., None]) + r * blend[..., None]
    # Remaining same-colour shadows: bounded luminance lift, preserve texture contrast.
    reference = gaussian_filter(np.maximum(lp, lr), sigma, mode="nearest")
    lum = combined.mean(-1)
    lift = np.where(same_surface & (lum < 95), np.clip(reference / np.maximum(lum, 16), 1, 1.35), 1)
    combined = np.clip(combined * lift[..., None], 0, 255).round().astype(np.uint8)
    uncertainty = ((difference > 45) & ~same_surface) | ~pv | ~rv
    return combined, r.round().astype(np.uint8), uncertainty.astype(np.uint8) * 255


def gpu_bytes(width: int, height: int, bytes_per_block: int = 16) -> int:
    """Full mip-chain allocation for a 4×4 GPU block (not KTX2 file size)."""
    if min(width, height, bytes_per_block) <= 0:
        raise ValueError("positive texture dimensions/block size required")
    total = 0
    while True:
        total += ((width + 3) // 4) * ((height + 3) // 4) * bytes_per_block
        if width == height == 1:
            return total
        width, height = max(1, width // 2), max(1, height // 2)
