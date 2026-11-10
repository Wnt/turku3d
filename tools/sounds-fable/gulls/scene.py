"""Scene building for gull candidates: distance over water, synthetic water, and seamless ambience loops.
Original Turku3D code, CC0-1.0."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sfx  # noqa: E402

SR = sfx.SR


def harbour_ir(seed: int = 3) -> np.ndarray:
    """Open water between stone quays: a short diffuse tail and a few discrete echoes (near quay 35 ms, far bank
    0.19 s, a warehouse wall 0.41 s)."""
    return sfx.synthetic_ir(1.3, seed=seed, bright=3500, early=[(0.035, 0.5), (0.19, 0.42), (0.41, 0.22), (0.63, 0.1)])


_IR = None


def at_distance(call: np.ndarray, metres: float, position: float = 0.0, ir: np.ndarray | None = None) -> np.ndarray:
    """A mono call heard `metres` away over water: 1/r level (re 10 m), air absorption, a water-surface reflection
    (a comb that closes up with distance) and a reverberant share that grows with distance. Returns stereo."""
    global _IR
    if ir is None:
        if _IR is None:
            _IR = harbour_ir()
        ir = _IR
    dry = sfx.air(call, metres)
    # Reflection off the water: path difference for source 8 m and listener 2 m above the surface.
    extra = (np.sqrt(metres ** 2 + 10 ** 2) - np.sqrt(metres ** 2 + 6 ** 2)) / 343
    shift = int(extra * SR)
    if shift > 0:
        bounced = np.concatenate([np.zeros(shift), dry])[:len(dry)]
        dry = dry + 0.55 * bounced
    level = 10 / max(metres, 10)
    wet_share = min(0.85, 0.12 + metres / 260)
    stereo = sfx.pan(dry, position) * level
    wet = sfx.convolve(dry, ir) * level * wet_share * 0.9
    out = np.zeros((len(wet), 2))
    out[:len(stereo)] += stereo * (1 - 0.5 * wet_share)
    out += wet
    return out


def place_wrapped(mix: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0) -> None:
    """Add `x` into the loop `mix` at `at` seconds; whatever runs past the end continues from the start."""
    n = len(mix)
    start = int(at * SR) % n
    index = (start + np.arange(len(x))) % n
    np.add.at(mix, index, x * gain)


def periodic_noise(n: int, rng: np.random.Generator, shape) -> np.ndarray:
    """Noise whose spectrum is `shape(hz)`; filtered in the frequency domain, so it repeats seamlessly after n."""
    spectrum = np.fft.rfft(rng.standard_normal(n))
    hz = np.fft.rfftfreq(n, 1 / SR)
    out = np.fft.irfft(spectrum * shape(hz), n=n)
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-12)


def periodic_lfo(n: int, rng: np.random.Generator, hz_max: float, count: int = 6) -> np.ndarray:
    """A slow random curve (0..1) made of sines with whole numbers of cycles in the loop."""
    t = np.arange(n) / n
    cycles_max = max(1, int(hz_max * n / SR))
    out = sum(rng.uniform(0.4, 1) * np.sin(2 * np.pi * rng.integers(1, cycles_max + 1) * t + rng.uniform(0, 6.28)) for _ in range(count))
    out -= out.min()
    return out / (out.max() + 1e-12)


def water_bed(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """Synthetic harbour water (stereo, seamless): a soft wash, slaps against the quay and small bubbles."""
    n = int(seconds * SR)
    mix = np.zeros((n, 2))
    for channel in range(2):
        wash = periodic_noise(n, rng, lambda hz: 1 / (1 + (hz / 450) ** 2) * (hz > 60) + 0.05 / (1 + (hz / 3000) ** 2))
        mix[:, channel] += wash * 0.05 * (0.5 + 0.5 * periodic_lfo(n, rng, 0.35))
    swell = periodic_lfo(n, rng, 0.25)
    at = 0.0
    while at < seconds:
        at += rng.exponential(0.55) + 0.12
        strength = 0.3 + 0.7 * swell[int(at * SR) % n] * rng.random()
        length = rng.uniform(0.25, 0.7)
        m = int(length * SR)
        t = np.arange(m) / SR
        burst = rng.standard_normal(m)
        centre = rng.uniform(350, 1400)
        sos = signal.butter(2, [centre * 0.6, centre * 1.9], "bandpass", fs=SR, output="sos")
        slap = signal.sosfilt(sos, burst) * (1 - np.exp(-t / rng.uniform(0.02, 0.07))) * np.exp(-t / (length * 0.3))
        place_wrapped(mix, sfx.pan(slap, rng.uniform(-0.8, 0.8)), at, 0.16 * strength)
        for _ in range(rng.integers(0, 4)):  # bubbles: short rising sine chirps
            bubble_len = rng.uniform(0.02, 0.07)
            tb = np.arange(int(bubble_len * SR)) / SR
            hz = rng.uniform(450, 1700)
            bubble = np.sin(2 * np.pi * hz * (tb + 3.0 * tb ** 2 / bubble_len * 0.1)) * np.exp(-tb / (bubble_len * 0.3))
            place_wrapped(mix, sfx.pan(bubble, rng.uniform(-0.9, 0.9)), at + rng.uniform(0, length), 0.035 * strength)
    return mix


def wind_bed(seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * SR)
    out = np.zeros((n, 2))
    for channel in range(2):
        gust = 0.35 + 0.65 * periodic_lfo(n, rng, 0.3)
        out[:, channel] = periodic_noise(n, rng, lambda hz: 1 / (1 + (hz / 220) ** 1.6) * (hz > 30)) * gust * 0.035
    return out
