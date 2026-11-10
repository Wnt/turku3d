"""Objective measures of a music file, for comparing the game's current music with candidates.

Usage: python -I analyse.py FILE [FILE ...] [--json OUT.json]
Measures: tempo (onset autocorrelation), loudness (RMS, crest factor), stereo width, spectral balance (share of energy
in bass/mid/treble and how many third-octave bands lie within 30 dB of the loudest), onset density, and how much of
the piece is a literal repeat of itself four bars earlier.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sfx  # noqa: E402

SR = sfx.SR
HOP = 480  # 10 ms


def mel_like(mono: np.ndarray) -> np.ndarray:
    """(bands, frames) log energies in 40 log-spaced bands, 10 ms hop."""
    hz, _, s = signal.spectrogram(mono, fs=SR, nperseg=2048, noverlap=2048 - HOP, window="hann")
    edges = np.geomspace(40, 16000, 41)
    bands = np.stack([s[(hz >= a) & (hz < b)].sum(axis=0) for a, b in zip(edges[:-1], edges[1:])])
    return np.log10(bands + 1e-12)


def tempo_of(onset: np.ndarray, hint: float | None = None) -> float:
    onset = onset - onset.mean()
    corr = signal.correlate(onset, onset, mode="full")[len(onset) - 1:]
    lags = np.arange(len(corr))
    bpm = 60 / (np.maximum(lags, 1) * HOP / SR)
    ok = (bpm >= 60) & (bpm <= 190)
    weight = np.exp(-0.5 * (np.log2(bpm / (hint or 115)) / (0.25 if hint else 0.9)) ** 2)
    best = np.argmax(np.where(ok, corr * weight, -np.inf))
    # Parabolic refinement of the lag.
    if 1 <= best < len(corr) - 1:
        a, b, c = corr[best - 1], corr[best], corr[best + 1]
        best = best + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
    return float(60 / (best * HOP / SR))


def measure(path: str, hint: float | None = None) -> dict:
    x = sfx.load(path, mono=False)
    mono = x.mean(axis=1)
    side = (x[:, 0] - x[:, 1]) / 2
    bands = mel_like(mono)
    flux = np.maximum(np.diff(bands, axis=1), 0).sum(axis=0)
    tempo = tempo_of(flux, hint)
    hz, power = signal.welch(mono, fs=SR, nperseg=8192)
    total = power[(hz > 30)].sum()
    edges = 1000 * 2 ** (np.arange(-15, 13) / 3)  # third-octave centres 31 Hz .. 16 kHz
    third = np.array([power[(hz >= c / 2 ** (1 / 6)) & (hz < c * 2 ** (1 / 6))].sum() for c in edges])
    third_db = 10 * np.log10(third + 1e-20)
    # Literal repetition: correlation of the band-energy pattern with itself four bars later.
    bar = int(round(4 * 60 / tempo * SR / HOP))
    lag = 4 * bar
    frames = bands - bands.mean(axis=1, keepdims=True)
    repeat = float(np.sum(frames[:, :-lag] * frames[:, lag:]) / np.sqrt(np.sum(frames[:, :-lag] ** 2) * np.sum(frames[:, lag:] ** 2))) if frames.shape[1] > lag * 2 else float("nan")
    peaks, _ = signal.find_peaks(flux, height=np.percentile(flux, 75) + 0.5 * flux.std(), distance=5)
    # Section changes: novelty of 4-second average spectra.
    block = int(4 * SR / HOP)
    blocks = np.stack([bands[:, i:i + block].mean(axis=1) for i in range(0, bands.shape[1] - block, block)], axis=1)
    change = np.sqrt(np.sum(np.diff(blocks, axis=1) ** 2, axis=0))
    return {
        "file": Path(path).name, "seconds": round(len(mono) / SR, 1), "tempo_bpm": round(tempo, 1),
        "rms_db": round(sfx.db(sfx.rms(mono)), 1), "crest_db": round(sfx.db(np.max(np.abs(mono))) - sfx.db(sfx.rms(mono)), 1),
        "width_db": round(sfx.db(sfx.rms(side)) - sfx.db(sfx.rms(mono)), 1),
        "bass_share": round(float(power[(hz > 30) & (hz < 200)].sum() / total), 3), "treble_share": round(float(power[hz > 4000].sum() / total), 4),
        "third_octaves_within_30db": int(np.sum(third_db > third_db.max() - 30)), "centroid_hz": round(float(np.sum(hz * power) / power.sum())),
        "onsets_per_s": round(len(peaks) / (len(mono) / SR), 2), "repeat_4bars": round(repeat, 3),
        "section_changes": int(np.sum(change > np.median(change) * 2.2)),
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    out = None
    if "--json" in args:
        out = args[args.index("--json") + 1]
        args = args[: args.index("--json")] + args[args.index("--json") + 2:]
    rows = [measure(p) for p in args]
    keys = [k for k in rows[0] if k != "file"]
    print(f"{'file':<30}" + "".join(f"{k[:11]:>12}" for k in keys))
    for r in rows:
        print(f"{r['file'][:29]:<30}" + "".join(f"{r[k]:>12}" for k in keys))
    if out:
        sfx.write_json(out, rows)
