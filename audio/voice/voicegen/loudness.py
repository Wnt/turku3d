"""ITU-R BS.1770-4 integrated loudness for mono clips, plus trimming and normalization."""

import math

import numpy as np
from scipy.signal import lfilter


def _k_weighting(rate):
    """K-weighting biquads for any sample rate (libebur128's analogue-prototype derivation)."""
    k = math.tan(math.pi * 1681.974450955533 / rate)
    q = 0.7071752369554196
    vh = 10 ** (3.999843853973347 / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf_b = [
        (vh + vb * k / q + k * k) / a0,
        2 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
    ]
    shelf_a = [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    k = math.tan(math.pi * 38.13547087602444 / rate)
    q = 0.5003270373238773
    a0 = 1 + k / q + k * k
    high_a = [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return (shelf_b, shelf_a), ([1.0, -2.0, 1.0], high_a)


def integrated_loudness(samples, rate):
    """Gated integrated loudness in LUFS (400 ms blocks, 75 % overlap, -70 LUFS and -10 LU gates)."""
    x = np.asarray(samples, dtype=np.float64)
    for b, a in _k_weighting(rate):
        x = lfilter(b, a, x)
    block, step = round(0.4 * rate), round(0.1 * rate)
    if len(x) < block:
        powers = np.array([np.mean(x * x)])
    else:
        squares = np.concatenate([[0.0], np.cumsum(x * x)])
        starts = np.arange(0, len(x) - block + 1, step)
        powers = (squares[starts + block] - squares[starts]) / block
    with np.errstate(divide="ignore"):
        levels = -0.691 + 10 * np.log10(powers)
    gated = powers[levels > -70]
    if not len(gated):
        return -math.inf
    relative = -0.691 + 10 * math.log10(gated.mean()) - 10
    gated = powers[(levels > -70) & (levels > relative)]
    return -0.691 + 10 * math.log10(gated.mean())


def trim(samples, rate, threshold_db=-50.0, margin_s=0.05, fade_s=0.005):
    """Cut leading/trailing silence below threshold (relative to peak) and fade the new edges."""
    x = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak == 0:
        return x
    loud = np.flatnonzero(np.abs(x) > peak * 10 ** (threshold_db / 20))
    margin = round(margin_s * rate)
    x = x[max(0, loud[0] - margin) : loud[-1] + margin + 1].copy()
    fade = min(round(fade_s * rate), len(x) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
    return x


def normalize(samples, rate, target_lufs, peak_dbfs):
    """Scale to target integrated loudness; scale further down only if the peak would exceed peak_dbfs."""
    x = np.asarray(samples, dtype=np.float64)
    loudness = integrated_loudness(x, rate)
    if not math.isfinite(loudness):
        raise ValueError("cannot normalize a silent clip")
    x = x * 10 ** ((target_lufs - loudness) / 20)
    ceiling = 10 ** (peak_dbfs / 20)
    peak = float(np.max(np.abs(x)))
    if peak > ceiling:
        x *= ceiling / peak
    return x.astype(np.float32)
