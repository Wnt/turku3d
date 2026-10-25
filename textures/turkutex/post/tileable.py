"""Deterministic make-tileable and low-frequency flatten (Codex tiles are never trusted, D7).

make_tileable: for each requested axis, cross the wrap seam with a copy rolled by half the size and
join the two along a minimum-error boundary cut (image quilting, Efros & Freeman 2001) inside a band
around the seam, with a short feather. The new image wraps exactly along that axis, and the
artificial seam of the rolled copy ends up in the image centre where the original content is used.

flatten_lowfreq: divide out large-scale illumination/colour gradients (reflect-padded blur: it runs before tiling).
  mode "xy": ratio = global mean / blur(img, sigma)        (materials that tile in both directions)
  mode "x":  ratio = row mean / blur_along_x(img, sigma)    (facade strips: keep vertical structure such
                                                              as plinth/wall/cornice, remove left-right
                                                              lighting falloff)
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def _min_cut_path(err: np.ndarray) -> np.ndarray:
    """Vertical min-cost path through err (H, B): column index per row (dynamic programming)."""
    H, B = err.shape
    cost = err.copy()
    back = np.zeros((H, B), np.int64)
    for y in range(1, H):
        prev = cost[y - 1]
        left = np.r_[np.inf, prev[:-1]]
        right = np.r_[prev[1:], np.inf]
        stack = np.stack([left, prev, right])
        k = np.argmin(stack, 0)
        cost[y] += stack[k, np.arange(B)]
        back[y] = np.arange(B) + k - 1
    path = np.zeros(H, np.int64)
    path[-1] = int(np.argmin(cost[-1]))
    for y in range(H - 1, 0, -1):
        path[y - 1] = back[y, path[y]]
    return path


def _tile_x(img: np.ndarray, band_frac: float, feather: int) -> np.ndarray:
    H, W = img.shape[:2]
    f = img.astype(np.float32)
    B = max(8, int(W * band_frac))
    # The new wrap seam is the original column pair (s-1, s): pick s near the centre where adjacent
    # columns differ least, so the seam does not land on a strong vertical feature (tile joint).
    lum = f.mean(-1) if f.ndim == 3 else f
    cd = np.abs(np.diff(lum, axis=1)).mean(0)          # cd[k] = |col k+1 - col k|
    lo, hi = int(W * 0.4), int(W * 0.6)
    s = lo + 1 + int(np.argmin(cd[lo:hi]))
    r = np.roll(f, s, axis=1)  # r wraps perfectly at x = 0 / W (its own seam sits at x = s)
    c0 = s - B // 2            # band around r's internal seam, where we switch to the original
    # left of the cut: r (continuous with the right edge via wrap); right of the cut: f ... and the
    # mirror at the other side would need a second cut, so instead: inside the band choose per row
    # between r (outside) and f (inside); the original f is continuous inside, r is continuous at edges.
    a = r[:, c0:c0 + B]
    b = f[:, c0:c0 + B]
    err = np.sum((a - b) ** 2, axis=-1) if f.ndim == 3 else (a - b) ** 2
    # two cuts: left cut in the left half of the band, right cut in the right half
    half = B // 2
    pl = _min_cut_path(err[:, :half])
    pr = _min_cut_path(err[:, half:]) + half
    cols = np.arange(B)[None, :]
    inside = (cols >= pl[:, None]) & (cols <= pr[:, None])
    m = inside.astype(np.float32)
    if feather > 0:
        m = ndimage.uniform_filter1d(m, size=2 * feather + 1, axis=1, mode="nearest")
    out = r.copy()
    mm = m[..., None] if f.ndim == 3 else m
    # outside the band: r everywhere; but r has its seam at W//2, which lies inside the band and is
    # replaced by f there (inside the cut). Outside the band r is just the original content shifted.
    out[:, c0:c0 + B] = a * (1 - mm) + b * mm
    # roll back so the content sits where it was originally (seam now at the borders is continuous)
    return np.roll(out, -s, axis=1)


def make_tileable(img: np.ndarray, axes: str = "xy", band_frac: float = 0.2, feather: int = 3) -> np.ndarray:
    out = img.astype(np.float32)
    if "x" in axes:
        out = _tile_x(out, band_frac, feather)
    if "y" in axes:
        t = np.swapaxes(out, 0, 1)
        out = np.swapaxes(_tile_x(t, band_frac, feather), 0, 1)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8) if img.dtype == np.uint8 else out


def flatten_lowfreq(img: np.ndarray, mode: str = "xy", sigma_frac: float = 0.125, strength: float = 1.0) -> np.ndarray:
    f = img.astype(np.float32) + 1.0
    H, W = f.shape[:2]
    sig = sigma_frac * max(H, W)
    lum = f.mean(-1) if f.ndim == 3 else f
    if mode == "xy":
        low = ndimage.gaussian_filter(lum, sig, mode="reflect")
        target = lum.mean()
    elif mode == "x":
        low = ndimage.gaussian_filter1d(lum, sig, axis=1, mode="reflect")
        target = lum.mean(1, keepdims=True)
    else:
        raise ValueError(mode)
    ratio = (target / np.maximum(low, 1e-3)) ** strength
    out = f * (ratio[..., None] if f.ndim == 3 else ratio) - 1.0
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def detect_period_x(img: np.ndarray, min_frac: float = 1 / 12, max_frac: float = 0.5) -> int | None:
    """Dominant horizontal repeat (pixels) from the autocorrelation of the column profile, or None."""
    f = img.astype(np.float32)
    lum = f.mean(-1) if f.ndim == 3 else f
    # column profile of vertical edges: robust to colour, picks window/pilaster rhythm
    prof = np.abs(np.diff(lum, axis=1)).mean(0)
    prof = prof - prof.mean()
    n = len(prof)
    ac = np.correlate(prof, prof, mode="full")[n - 1:]
    ac /= np.arange(n, 0, -1)  # unbiased
    ac /= max(ac[0], 1e-9)
    lo, hi = max(2, int(n * min_frac)), int(n * max_frac)
    if hi <= lo:
        return None
    lag = lo + int(np.argmax(ac[lo:hi]))
    return lag if ac[lag] > 0.3 else None


def crop_to_period(img: np.ndarray, period: int | None = None) -> tuple[np.ndarray, dict]:
    """Crop to a whole number of horizontal repeats so the wrap keeps the bay rhythm.
    The crop start is chosen where the column at `start` best matches the column at `start + width`."""
    W = img.shape[1]
    p = period or detect_period_x(img)
    if not p:
        return img, {"period_px": None}
    k = W // p
    w = k * p
    if w == W:
        return img, {"period_px": p, "repeats": k, "crop": [0, W]}
    f = img.astype(np.float32)
    lum = f.mean(-1) if f.ndim == 3 else f
    # "quiet" columns: little vertical-edge energy (plain wall between bays, not a window or pilaster)
    energy = np.abs(np.diff(lum, axis=1, append=lum[:, :1])).mean(0)
    energy = energy / max(float(np.median(energy)), 1e-6)

    def cost(s0):
        e = s0 + w
        if e >= W:
            return float("inf")
        mismatch = float(np.abs(f[:, s0] - f[:, e]).mean()) / 255 * 10
        return mismatch + energy[max(0, s0 - 2):s0 + 3].max() + energy[max(0, e - 2):e + 3].max()

    best = min(range(0, W - w), key=cost)
    return img[:, best:best + w], {"period_px": p, "repeats": k, "crop": [best, best + w]}


def feather_wrap_x(img: np.ndarray, k: int = 12) -> np.ndarray:
    """Close a small residual wrap step without moving content: spread half of the column difference
    d = left - right over k columns on each side (left side -d/2 -> 0, right side +d/2 -> 0)."""
    f = img.astype(np.float32)
    d = f[:, :1] - f[:, -1:]
    w = np.linspace(0.5, 0.0, k, endpoint=False)
    shape = (1, k) + ((1,) if f.ndim == 3 else ())
    f[:, :k] -= d * w.reshape(shape)
    f[:, -k:] += d * w[::-1].reshape(shape)
    return np.clip(f + 0.5, 0, 255).astype(np.uint8) if img.dtype == np.uint8 else f
