"""Harmonic-plus-noise resynthesis of measured gull calls, with new "individuals" from one measurement.

Usage: python -I resynth.py SOURCES_MANIFEST.json EXTRACT_DIR OUT_DIR
Reads tracks.npz written by extract.py (f0, 32 harmonic amplitudes and the between-harmonics residual every 5 ms)
and plays them back with an oscillator bank and shaped noise: no sample of the recording is reproduced, only its
measured parameters. Pitch, tempo and formants can then be changed independently, which a recording cannot do.
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
HOP = 240
NFFT = 1024


def resynthesise(tracks: dict, start: float, end: float, rng: np.random.Generator, pitch: float = 1.0, stretch: float = 1.0, formant: float = 1.0,
                 noise_gain: float = 0.7, jitter: float = 0.004) -> np.ndarray:
    a, b = int(start * SR / HOP), int(end * SR / HOP)
    f0 = tracks["f0"][a:b].astype(float)
    amplitudes = tracks["amplitudes"][a:b].astype(float) * 2  # STFT 'spectrum' scaling: a sine of amplitude A reads A/2
    residual = tracks["residual"][:, a:b].astype(float)
    residual_hz = tracks["residual_hz"]
    frames, harmonics = amplitudes.shape
    voiced = f0 > 0
    # Hold the pitch through unvoiced frames so the oscillators glide instead of jumping; their amplitudes are zero there.
    held = f0.copy()
    last = np.flatnonzero(voiced)
    if len(last) == 0:
        return np.zeros(int((end - start) * SR * stretch))
    index = np.maximum.accumulate(np.where(voiced, np.arange(frames), 0))
    index[: last[0]] = last[0]
    held = f0[index]
    # New harmonic amplitudes from each frame's spectral envelope (formant-preserving pitch change, optional formant scale).
    new = np.zeros_like(amplitudes)
    k = np.arange(1, harmonics + 1)
    for i in np.flatnonzero(voiced):
        source_hz = k * f0[i]
        ok = amplitudes[i] > 0
        if ok.sum() < 2:
            continue
        log_env = np.log(np.maximum(amplitudes[i][ok], 1e-9))
        target_hz = k * f0[i] * pitch / formant
        new[i] = np.exp(np.interp(target_hz, source_hz[ok], log_env, left=log_env[0] - 3.0, right=-20.0))
        new[i][k * f0[i] * pitch > 15000] = 0
    # A jump of the tracked pitch inside a note (octave error, voice break) would click: silence the frames around it.
    jump = np.abs(np.log2(held[1:] / held[:-1])) > 0.14
    for i in np.flatnonzero(jump):
        new[max(0, i - 1):i + 3] = 0
    # Frame tracks -> sample rate, with time stretch.
    n = int(frames * HOP * stretch)
    position = np.linspace(0, frames - 1, n)
    f = np.interp(position, np.arange(frames), held) * pitch
    wobble = signal.lfilter([1], [1, -0.98], rng.standard_normal(n)) * 0.15
    phase = 2 * np.pi * np.cumsum(f * (1 + jitter * wobble)) / SR
    out = np.zeros(n)
    for h in range(harmonics):
        amp = np.interp(position, np.arange(frames), new[:, h])
        if amp.max() <= 0:
            continue
        out += amp * np.sin((h + 1) * phase + rng.uniform(0, 2 * np.pi))
    # Noise part: white noise shaped frame by frame with the measured residual envelope.
    white = rng.standard_normal(n + NFFT)
    _, _, spectrum = signal.stft(white, fs=SR, nperseg=NFFT, noverlap=NFFT - HOP, window="hann")
    hz = np.fft.rfftfreq(NFFT, 1 / SR)
    frame_position = np.linspace(0, frames - 1, spectrum.shape[1])
    shaped = np.zeros(spectrum.shape)
    lower = np.clip(frame_position.astype(int), 0, frames - 1)
    for column, frame in enumerate(lower):
        shaped[:, column] = np.interp(hz / formant, residual_hz, residual[:, frame], right=0.0)
    _, noise = signal.istft(spectrum * shaped / 0.0383 * noise_gain, fs=SR, nperseg=NFFT, noverlap=NFFT - HOP, window="hann")
    out += noise[:n]
    return sfx.fade(out, 0.02, 0.1)


def main() -> None:
    manifest = {e["id"]: e for e in json.loads(Path(sys.argv[1]).read_text())}
    extract, out = Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)

    def tracks_of(prefix: str) -> tuple[dict, str]:
        rid = next(key for key in manifest if key.startswith(prefix))
        return dict(np.load(extract / rid / "tracks.npz")), rid

    rng = np.random.default_rng(42)
    gap = np.zeros(int(0.6 * SR))
    entries = []
    plans = [
        ("gull-resyn-herring-keow", "xc1002938", 0.75, 5.35, "herring gull single calls: faithful resynthesis"),
        ("gull-resyn-herring-longcall", "xc1178162", 10.55, 22.2, "herring gull long call: faithful resynthesis"),
        ("gull-resyn-common-longcall", "xc803773", 0.15, 9.45, "common gull long call: faithful resynthesis"),
        ("gull-resyn-blackheaded-kree", "xc1182964", 3.0, 5.85, "black-headed gull calls: faithful resynthesis"),
    ]
    for name, prefix, start, end, note in plans:
        tracks, rid = tracks_of(prefix)
        audio = sfx.normalise(resynthesise(tracks, start, end, rng), -3)
        sfx.save(out / f"{name}.wav", audio)
        entries.append({"id": name, "sources": [rid], "note": note})
        print(name, f"{len(audio) / SR:.1f} s")
    # New individuals: the same measured call as a larger, slower bird and a smaller, quicker one.
    variants = [
        ("gull-resyn-herring-keow-birds", "xc1002938", 0.75, 2.0, "one measured herring gull call as five different birds"),
        ("gull-resyn-common-longcall-birds", "xc803773", 0.15, 9.45, "one measured common gull long call as two other birds"),
    ]
    for name, prefix, start, end, note in variants:
        tracks, rid = tracks_of(prefix)
        settings = [(0.9, 1.12, 0.94), (1.0, 1.0, 1.0), (1.08, 0.9, 1.05), (0.95, 0.95, 1.0), (1.13, 1.05, 1.08)] if "keow" in name else [(0.92, 1.1, 0.95), (1.09, 0.9, 1.06)]
        parts = []
        for pitch, stretch, formant in settings:
            parts += [sfx.normalise(resynthesise(tracks, start, end, rng, pitch, stretch, formant), -3), gap]
        audio = np.concatenate(parts[:-1])
        sfx.save(out / f"{name}.wav", audio)
        entries.append({"id": name, "sources": [rid], "note": note})
        print(name, f"{len(audio) / SR:.1f} s")
    sfx.write_json(out / "resyn.json", entries)


if __name__ == "__main__":
    main()
