"""World-up direction of an equirect panorama from its vertical lines, to straighten the crops made from it.

Panoramas are not level. Backpack Look Around (2026-06) leans about 13 degrees; cars, Google and photospheres lean
0.3-3 degrees. A level 90-degree crop around the horizon has a vertical vanishing point where facade edges, window
jambs and poles meet. The world-up direction that puts it there is fitted to the crop's near-vertical line segments
(LSD) with a truncated angular loss, jointly over eight crops around the horizon. The result is used only within
LIMITS: tilt, line evidence, stability when one crop is left out, and agreement with the panorama's own pitch and roll.
Where the lines fail (parks, trees, a lone facade), the pitch and roll are used instead: fitted to the line results of
38 panoramas, they give world up within 0.18 deg (median, max 0.57) for Look Around and 0.37 deg (max 1.3) for
Street View (2026-11-12, metadata_up()).

On renders with exact camera poses the per-crop fit reads roll and pitch within 0.1 degrees (2026-11-12). Verticals
say nothing about heading, which stays with the panorama's metadata.

Frame: ENU vectors in the panorama's frame, with azimuth as `perspective()` maps it (pano_heading applied). A level
panorama has up (0, 0, 1).
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

from .camera import basis


def segments(img: np.ndarray, min_len: float = 25.0, max_tilt: float = 25.0) -> np.ndarray:
    """Near-vertical line segments (x1, y1, x2, y2) of an RGB image."""
    gray = cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2GRAY)
    found = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if found is None:
        return np.zeros((0, 4))
    s = found.reshape(-1, 4).astype(np.float64)
    d = s[:, 2:] - s[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    tilt = np.degrees(np.arctan2(np.abs(d[:, 0]), np.abs(d[:, 1])))
    return s[(length >= min_len) & (tilt <= max_tilt)]


def fit_vertical(seg: np.ndarray, w: int, h: int, fov: float, pitch: float = 0.0, span: float = 16.0):
    """Roll and pitch (degrees) whose vertical vanishing point best fits the segments of a pinhole image with
    vertical `fov`, plus the inlier segment length (segments within 1 degree)."""
    f = (h / 2) / math.tan(math.radians(fov) / 2)
    cx, cy = w / 2, h / 2
    mid = (seg[:, :2] + seg[:, 2:]) / 2
    d = seg[:, 2:] - seg[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    d = d / length[:, None]

    def angles(r, p):
        r, p = np.radians(r)[..., None], np.radians(p)[..., None]
        ux, uy, uz = np.sin(r) * np.cos(p), -np.cos(r) * np.cos(p), np.sin(p)
        vx, vy, vz = cx * uz + f * ux, cy * uz + f * uy, uz  # homogeneous vanishing point
        tx, ty = vx - mid[:, 0] * vz, vy - mid[:, 1] * vz
        return np.degrees(np.arctan2(np.abs(tx * d[:, 1] - ty * d[:, 0]), np.abs(tx * d[:, 0] + ty * d[:, 1])))

    best = (0.0, pitch)
    for half, step in [(span, 0.25), (0.6, 0.02)]:
        grid = np.arange(-half, half + 1e-9, step)
        R, P = (g.ravel() for g in np.meshgrid(grid + best[0], grid + best[1]))
        cost = (length * np.minimum(angles(R, P), 1.5) ** 2).sum(-1)
        i = int(np.argmin(cost))
        best = (float(R[i]), float(P[i]))
    inlier = angles(np.array([best[0]]), np.array([best[1]]))[0] < 1.0
    return best[0], best[1], float(length[inlier].sum())


# Sanity limits for straightening. Outside them the crop is left as the panorama is, and the row says why.
LIMITS = {
    "max_tilt_deg": 20.0,  # backpack rigs lean about 13 deg; more is a wrong fit
    "min_evidence_px": 2000,  # inlier vertical segment length over the eight crops
    "max_instability_deg": 1.0,  # leaving out any one crop moves the answer at most this much
    "max_metadata_gap_deg": 1.5,  # agrees with the panorama's own pitch and roll (metadata_up), when it has them
}

# Signs of each provider's pitch and roll in metadata_up(), fitted to the line fits (see the module docstring).
METADATA_SIGNS = {"lookaround": (1.0, 1.0), "google": (-1.0, -1.0)}


def _cost(G, seg, chunk=512):
    """Truncated angular cost of candidate world-up vectors G (C, 3) against all segments, and per-segment angles
    for the best candidate."""
    mid, d, L, rt, up, fw, f, c = seg
    out = np.empty(len(G))
    for i in range(0, len(G), chunk):
        g = G[i : i + chunk]
        ux, uy, uz = g @ rt.T, -(g @ up.T), g @ fw.T
        vx, vy, vz = c * uz + f * ux, c * uz + f * uy, uz
        tx, ty = vx - mid[:, 0] * vz, vy - mid[:, 1] * vz
        a = np.degrees(np.arctan2(np.abs(tx * d[:, 1] - ty * d[:, 0]), np.abs(tx * d[:, 0] + ty * d[:, 1])))
        out[i : i + chunk] = (L * np.minimum(a, 1.5) ** 2).sum(-1)
    return out


def _fit(seg, centre=(0.0, 0.0), stages=((0.37, 0.01), (0.012, 0.0005))):
    """World up (unit, pano frame) minimising the cost, searched as normalise(a, b, 1) on refining grids."""
    a0, b0 = centre
    for half, step in stages:
        grid = np.arange(-half, half + 1e-12, step)
        A, B = (x.ravel() for x in np.meshgrid(grid + a0, grid + b0))
        i = int(np.argmin(_cost(np.stack([A, B, np.ones_like(A)], -1), seg)))
        a0, b0 = float(A[i]), float(B[i])
    g = np.array([a0, b0, 1.0])
    return g / np.linalg.norm(g)


def _angle(u, v):
    return math.degrees(math.acos(min(1.0, abs(float(u @ v)))))


def estimate_up(eq: Image.Image | np.ndarray, pano_heading: float, metadata: list | np.ndarray | None = None,
                size: int = 640) -> dict:
    """World up in the panorama's frame from one joint fit to the vertical segments of eight level crops.

    `up` is what to straighten with: the fit when it passes LIMITS, else `metadata` (metadata_up()), else level.
    `method` says which (lines, metadata, none) and `check` which limit the lines failed.
    """
    meta = None if metadata is None else np.asarray(metadata, dtype=np.float64)

    def result(g, failed, **measured):
        use = "lines" if not failed else "metadata" if meta is not None else "none"
        up = g if use == "lines" else meta if use == "metadata" else np.array([0.0, 0.0, 1.0])
        return {
            "up": [round(float(x), 6) for x in up],
            "straightened": use != "none",
            "method": use,
            "check": "; ".join(failed) or "ok",
            "metadata_tilt_deg": None if meta is None else round(_angle(meta, np.array([0.0, 0.0, 1.0])), 2),
            **measured,
        }

    from .pano import perspective

    src = np.asarray(eq.convert("RGB") if isinstance(eq, Image.Image) else eq)
    f, c = (size / 2) / math.tan(math.radians(45)), size / 2
    parts = []
    for k, yaw in enumerate(range(0, 360, 45)):
        crop = np.asarray(perspective(src, pano_heading, {"yaw": yaw, "pitch": 0.0, "roll": 0.0, "fov": 90.0}, size, size))
        s = segments(crop)
        fw, rt, up = basis(yaw, 0.0)
        parts += [(k, x, rt, up, fw) for x in s]
    if len(parts) < 8:
        return result(None, ["no vertical lines"], tilt_deg=None)
    crop_of = np.array([p[0] for p in parts])
    raw = np.array([p[1] for p in parts])
    d = raw[:, 2:] - raw[:, :2]
    L = np.hypot(d[:, 0], d[:, 1])

    def pack(mask):
        return ((raw[mask, :2] + raw[mask, 2:]) / 2, d[mask] / L[mask, None], L[mask],
                np.array([p[2] for p in parts])[mask], np.array([p[3] for p in parts])[mask],
                np.array([p[4] for p in parts])[mask], f, c)

    every = np.ones(len(parts), bool)
    g = _fit(pack(every))
    mid, dn, Ln, rt, up, fw, *_ = pack(every)
    ux, uy, uz = rt @ g, -(up @ g), fw @ g
    tx, ty = c * uz + f * ux - mid[:, 0] * uz, c * uz + f * uy - mid[:, 1] * uz
    inlier = np.degrees(np.arctan2(np.abs(tx * dn[:, 1] - ty * dn[:, 0]), np.abs(tx * dn[:, 0] + ty * dn[:, 1]))) < 1.0
    evidence = float(Ln[inlier].sum())
    centre = (g[0] / g[2], g[1] / g[2])
    instability = max((_angle(_fit(pack(crop_of != k), centre, ((0.03, 0.001),)), g)
                       for k in set(crop_of.tolist()) if (crop_of != k).sum() >= 8), default=0.0)
    tilt = math.degrees(math.acos(min(1.0, g[2])))
    gap = None if meta is None else _angle(g, meta)
    failed = [name for name, bad in [
        ("tilt over max_tilt_deg", tilt > LIMITS["max_tilt_deg"]),
        ("too little line evidence", evidence < LIMITS["min_evidence_px"]),
        ("unstable without one crop", instability > LIMITS["max_instability_deg"]),
        ("disagrees with metadata", gap is not None and gap > LIMITS["max_metadata_gap_deg"]),
    ] if bad]
    return result(g, failed, tilt_deg=round(tilt, 2), evidence_px=round(evidence), instability_deg=round(instability, 2))


def metadata_up(provider: str, pano_heading: float, pitch_rad: float | None, roll_rad: float | None) -> list | None:
    """World up in the panorama's frame (as estimate_up() returns it) from the panorama's own pitch and roll.

    `provider` is "google" or "lookaround"; `pano_heading` the grid heading perspective() uses. None without them.
    """
    if pitch_rad is None or roll_rad is None:
        return None
    sr, sp = METADATA_SIGNS["google" if "google" in provider else "lookaround"]
    h = math.radians(pano_heading)
    fwd, right = np.array([math.sin(h), math.cos(h), 0.0]), np.array([math.cos(h), -math.sin(h), 0.0])
    g = (sr * math.sin(roll_rad) * right + sp * math.sin(pitch_rad) * math.cos(roll_rad) * fwd
         + math.cos(pitch_rad) * math.cos(roll_rad) * np.array([0.0, 0.0, 1.0]))
    return [round(float(x), 6) for x in g / np.linalg.norm(g)]


def to_pano(up) -> np.ndarray:
    """Rotation taking world directions into the panorama's frame: the one that turns (0, 0, 1) into `up`."""
    g = np.asarray(up, dtype=np.float64)
    g = g / np.linalg.norm(g)
    z = np.array([0.0, 0.0, 1.0])
    axis, c = np.cross(z, g), float(g @ z)
    s = np.linalg.norm(axis)
    if s < 1e-12:
        return np.eye(3)
    k = axis / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + s * K + (1 - c) * K @ K
