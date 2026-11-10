"""Parametric gull voice: a harmonic source (syrinx) with jitter, subharmonic rasp and breath noise through a formant
envelope (trachea and bill), plus call grammars for the three gulls of Turku. Original Turku3D code, CC0-1.0.

The core takes contours (f0, level, rasp, breath per sample), so the same renderer plays both invented calls
(`note`, the grammars below) and contours measured from recordings (resynth.py).
"""
from __future__ import annotations

import numpy as np
from scipy import signal

SR = 48000


def formant_curve(formants: list[tuple[float, float, float]], tilt_db_per_octave: float = -3.0):
    """Returns gain(hz) for a list of (centre Hz, bandwidth Hz, level dB) resonances on a tilted floor."""
    def gain(hz: np.ndarray) -> np.ndarray:
        hz = np.maximum(hz, 30.0)
        level = np.full(hz.shape, -22.0) + tilt_db_per_octave * np.log2(hz / 1000.0)
        for centre, width, peak in formants:
            level = np.maximum(level, peak - 12.0 * ((hz - centre) / width) ** 2 + tilt_db_per_octave * 0.3 * np.log2(hz / 1000.0))
        level = np.where(hz < 350, level - 18 * np.log2(350 / hz), level)  # a small body radiates no bass
        return 10 ** (level / 20)
    return gain


def render(f0: np.ndarray, level: np.ndarray, rasp: np.ndarray, breath: np.ndarray, gain, rng: np.random.Generator,
           jitter: float = 0.012, shimmer: float = 0.08, sr: int = SR, max_hz: float = 14000,
           measured: np.ndarray | None = None) -> np.ndarray:
    """Additive synthesis of one voiced stretch.

    f0, level, rasp (0..1 strength of the half-harmonics: period doubling) and breath (0..1 noise share) are arrays of
    equal length. `gain(hz)` is the formant curve. `measured`, if given, is (frames, harmonics) linear amplitudes that
    replace the formant curve (resynthesis), interpolated over the stretch.
    """
    n = len(f0)
    # Jitter: slow random pitch drift plus a faster flutter; shimmer likewise on level.
    drift = signal.lfilter([1], [1, -0.9995], rng.standard_normal(n)) * 0.02
    flutter = signal.lfilter([1], [1, -0.97], rng.standard_normal(n)) * 0.12
    f = f0 * (1 + jitter * (drift + flutter))
    phase = 2 * np.pi * np.cumsum(f) / sr
    wobble = 1 + shimmer * signal.lfilter([1], [1, -0.995], rng.standard_normal(n)) * 0.25
    out = np.zeros(n)
    harmonics = int(min(40, max_hz / max(60.0, float(np.min(f0)))))
    frames = None if measured is None else np.linspace(0, measured.shape[0] - 1, n)
    for k in range(1, harmonics + 1):
        hz = k * f
        if measured is not None:
            if k > measured.shape[1]:
                break
            amp = np.interp(frames, np.arange(measured.shape[0]), measured[:, k - 1])
        else:
            amp = gain(hz) / k ** 0.5
        amp = np.where(hz < max_hz, amp, 0.0)
        out += amp * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
        # Period doubling: partials halfway between the harmonics, the rasp of a loud gull.
        if np.any(rasp > 0.01):
            half = (k - 0.5) * f
            half_amp = (gain(half) if measured is None else amp) / k ** 0.5 * rasp
            out += np.where(half < max_hz, half_amp, 0.0) * np.sin((k - 0.5) * phase + rng.uniform(0, 2 * np.pi))
    out *= wobble
    # Breath and turbulence: noise coloured by the same resonances, strongest where the voice is loud.
    if np.any(breath > 0.001):
        noise = rng.standard_normal(n)
        spectrum = np.fft.rfft(noise)
        hz = np.fft.rfftfreq(n, 1 / sr)
        shaped = np.fft.irfft(spectrum * gain(hz) * (hz < max_hz), n=n)
        shaped /= np.sqrt(np.mean(shaped ** 2)) + 1e-12
        out += shaped * breath * (np.sqrt(np.mean(out ** 2)) + 1e-9) * 1.5
    return out * level


def contour(points: list[tuple[float, float]], n: int) -> np.ndarray:
    """Smooth curve through (position 0..1, value) points (cosine interpolation)."""
    x = np.linspace(0, 1, n)
    xs, ys = np.array([p[0] for p in points]), np.array([p[1] for p in points])
    index = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, len(xs) - 2)
    u = np.clip((x - xs[index]) / (xs[index + 1] - xs[index] + 1e-12), 0, 1)
    u = (1 - np.cos(np.pi * u)) / 2
    return ys[index] * (1 - u) + ys[index + 1] * u


def note(seconds: float, f0_points: list[tuple[float, float]], gain, rng: np.random.Generator, rasp: float = 0.0,
         breath: float = 0.05, attack: float = 0.03, release: float = 0.08, loudness: float = 1.0, roll_hz: float = 0.0,
         roll_depth: float = 0.0, sr: int = SR) -> np.ndarray:
    """One call note. `roll_hz`/`roll_depth`: the rolled r of a black-headed gull (amplitude flutter)."""
    n = int(seconds * sr)
    f0 = contour(f0_points, n)
    t = np.arange(n) / sr
    envelope = np.minimum(1, t / attack) ** 1.5 * np.minimum(1, (seconds - t) / release) ** 1.2
    envelope *= 0.75 + 0.25 * np.sin(np.pi * np.linspace(0, 1, n)) ** 0.7
    # Rasp follows effort: strongest around the loud, high middle of the note.
    effort = np.sin(np.pi * np.linspace(0, 1, n)) ** 2
    rasp_curve = rasp * (0.35 + 0.65 * effort) * (0.6 + 0.4 * rng.random())
    breath_curve = np.full(n, breath) + 0.25 * breath * (1 - envelope)
    y = render(f0, envelope * loudness, rasp_curve, breath_curve, gain, rng, sr=sr)
    if roll_hz > 0:
        y *= 1 - roll_depth * (0.5 + 0.5 * np.sin(2 * np.pi * roll_hz * t + rng.uniform(0, 6.28))) * effort ** 0.3
    return y


# Formant envelopes and voices. Starting values from the literature and spectrogram habit; calibrate.py replaces the
# numbers marked "measured" once recordings are analysed.
VOICES = {
    # Larus argentatus (harmaalokki): big bird, f0 0.5-1.0 kHz, energy in harmonics 2-5, loud and clanging.
    "herring": {"formants": [(1250, 420, 0), (2350, 600, -2), (3600, 900, -8), (5600, 1500, -17)], "tilt": -4.0, "pitch": 1.0},
    # Larus canus (kalalokki): smaller, higher and thinner, mewing.
    "common": {"formants": [(1700, 500, 0), (3100, 800, -4), (4700, 1100, -12), (6800, 1600, -20)], "tilt": -4.0, "pitch": 1.45},
    # Chroicocephalus ridibundus (naurulokki): harsh, rolling "krreeah" with a dense rough spectrum.
    "blackheaded": {"formants": [(1500, 600, 0), (2700, 800, -1), (4100, 1100, -6), (6200, 1600, -14)], "tilt": -2.5, "pitch": 0.72},
}


def voice_gain(species: str, rng: np.random.Generator, spread: float = 0.06):
    """A formant curve for one individual: every bird's resonances differ by a few percent."""
    v = VOICES[species]
    scale = 1 + rng.uniform(-spread, spread)
    return formant_curve([(c * scale * (1 + rng.uniform(-0.03, 0.03)), w, p + rng.uniform(-1.5, 1.5)) for c, w, p in v["formants"]], v["tilt"])


def gap(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sr))


def long_call(species: str, rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """The "laughing" long call: a few low introductory notes, two or three long rising screams, then a run of
    shorter notes that slows and sinks. Durations and intervals vary per call."""
    g = voice_gain(species, rng)
    p = VOICES[species]["pitch"] * (1 + rng.uniform(-0.07, 0.07))
    rasp = {"herring": 0.35, "common": 0.15, "blackheaded": 0.7}[species]
    parts = []
    for _ in range(rng.integers(2, 4)):  # head-down "ow, ow"
        parts += [note(rng.uniform(0.11, 0.17), [(0, 430 * p), (0.4, 560 * p), (1, 470 * p)], g, rng, rasp * 0.5, loudness=0.45), gap(rng.uniform(0.08, 0.14))]
    for i in range(rng.integers(2, 4)):  # head thrown back: long screams
        top = rng.uniform(880, 1010) * p
        parts += [note(rng.uniform(0.42, 0.62), [(0, 560 * p), (0.22, top), (0.6, top * 0.97), (1, 640 * p)], g, rng, rasp, loudness=1.0), gap(rng.uniform(0.07, 0.12))]
    count = rng.integers(6, 12)
    for i in range(count):  # "kyow kyow kyow", slowing down
        u = i / max(1, count - 1)
        top = (900 - 130 * u) * p * rng.uniform(0.97, 1.03)
        parts += [note(0.2 + 0.07 * u + rng.uniform(-0.02, 0.02), [(0, 600 * p), (0.25, top), (1, 520 * p)], g, rng, rasp * (1 - 0.4 * u), loudness=0.95 - 0.5 * u),
                  gap(0.09 + 0.07 * u + rng.uniform(0, 0.03))]
    return np.concatenate(parts)


def keow(species: str, rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """A single contact call."""
    g = voice_gain(species, rng)
    p = VOICES[species]["pitch"] * (1 + rng.uniform(-0.08, 0.08))
    rasp = {"herring": 0.25, "common": 0.1, "blackheaded": 0.6}[species]
    top = rng.uniform(820, 960) * p
    return note(rng.uniform(0.32, 0.5), [(0, 600 * p), (0.18, top), (0.5, top * 0.93), (1, 480 * p)], g, rng, rasp)


def mew(species: str, rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """The drawn-out mew: long, plaintive, nearly level with a slow sag."""
    g = voice_gain(species, rng)
    p = VOICES[species]["pitch"] * (1 + rng.uniform(-0.06, 0.06))
    top = rng.uniform(640, 720) * p
    return note(rng.uniform(0.8, 1.2), [(0, 520 * p), (0.15, top), (0.7, top * 0.96), (1, 560 * p)], g, rng, 0.08, breath=0.08, attack=0.08, release=0.2)


def alarm(species: str, rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """"Gag-ag-ag": groups of short low notes."""
    g = voice_gain(species, rng)
    p = VOICES[species]["pitch"] * (1 + rng.uniform(-0.06, 0.06))
    parts = []
    for group in range(rng.integers(2, 4)):
        for i in range(rng.integers(3, 5)):
            parts += [note(rng.uniform(0.07, 0.1), [(0, 440 * p), (0.4, 540 * p), (1, 450 * p)], g, rng, 0.3, loudness=0.9 if i == 0 else 0.7, attack=0.012, release=0.03),
                      gap(rng.uniform(0.055, 0.08))]
        parts.append(gap(rng.uniform(0.25, 0.5)))
    return np.concatenate(parts)


def kree(rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """Black-headed gull "krreeah": harsh, rolled, falling."""
    g = voice_gain("blackheaded", rng)
    p = VOICES["blackheaded"]["pitch"] * (1 + rng.uniform(-0.08, 0.08))
    top = rng.uniform(760, 880) * p
    return note(rng.uniform(0.3, 0.55), [(0, 620 * p), (0.15, top), (0.6, top * 0.9), (1, 500 * p)], g, rng, 0.75, breath=0.16,
                roll_hz=rng.uniform(42, 60), roll_depth=0.65)


def chatter(rng: np.random.Generator, sr: int = SR) -> np.ndarray:
    """Black-headed gull squabble: a run of short harsh notes."""
    parts = []
    for _ in range(rng.integers(3, 7)):
        g = voice_gain("blackheaded", rng)
        p = VOICES["blackheaded"]["pitch"] * rng.uniform(0.92, 1.1)
        parts += [note(rng.uniform(0.12, 0.24), [(0, 600 * p), (0.3, 800 * p), (1, 560 * p)], g, rng, 0.8, breath=0.18, roll_hz=rng.uniform(45, 65), roll_depth=0.6, attack=0.015),
                  gap(rng.uniform(0.05, 0.16))]
    return np.concatenate(parts)
