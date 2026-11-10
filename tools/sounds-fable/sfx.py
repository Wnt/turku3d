"""Shared helpers for the sounds-fable research scripts: audio I/O, loops, reverb, distance, plots.

Run with the analysis venv: ~/turku3d/loops/sounds-fable/venv/bin/python -I SCRIPT ...
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

SR = 48000


def load(path: str | Path, sr: int = SR, mono: bool = True) -> np.ndarray:
    """Decode any audio file through ffmpeg to float32 at `sr`. Returns (n,) mono or (n, channels)."""
    channels = 1 if mono else 2
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ar", str(sr), "-ac", str(channels), "pipe:1"],
        check=True, capture_output=True).stdout
    data = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    return data if mono else data.reshape(-1, 2)


def save(path: str | Path, x: np.ndarray, sr: int = SR) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(x, dtype=np.float32), sr, subtype="FLOAT")


def encode(wav: str | Path, out: str | Path, bitrate: str = "64k", channels: int | None = None) -> None:
    """Ogg/Opus at 48 kHz without metadata."""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(wav)]
    if channels:
        command += ["-ac", str(channels)]
    command += ["-c:a", "libopus", "-b:a", bitrate, "-application", "audio", "-map_metadata", "-1", str(out)]
    subprocess.run(command, check=True)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + 1e-20))


def db(value: float) -> float:
    return 20 * np.log10(max(value, 1e-10))


def normalise(x: np.ndarray, peak_db: float = -1.0, rms_db: float | None = None) -> np.ndarray:
    """Scale to a target RMS (if given) without exceeding the peak ceiling."""
    peak = np.max(np.abs(x)) + 1e-12
    gain = 10 ** (peak_db / 20) / peak
    if rms_db is not None:
        gain = min(gain, 10 ** (rms_db / 20) / rms(x))
    return x * gain


def fade(x: np.ndarray, start: float = 0.005, end: float = 0.02, sr: int = SR) -> np.ndarray:
    y = x.copy()
    a, b = int(start * sr), int(end * sr)
    ramp_in = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    ramp_out = np.cos(np.linspace(0, np.pi / 2, b)) ** 2
    if y.ndim == 2:
        ramp_in, ramp_out = ramp_in[:, None], ramp_out[:, None]
    if a:
        y[:a] *= ramp_in
    if b:
        y[-b:] *= ramp_out
    return y


def make_loop(x: np.ndarray, seconds: float, cross: float = 1.0, sr: int = SR, offset: float = 0.5) -> np.ndarray:
    """Cut a seamless loop of `seconds` from a longer take with an equal-power crossfade of `cross` seconds.

    The loop is body[0:L] where the first `cross` seconds are a blend of the take's continuation after the loop end
    (fading out) and the loop start (fading in), so sample L-1 is followed naturally by sample 0.
    """
    n, c, o = int(seconds * sr), int(cross * sr), int(offset * sr)
    if len(x) < o + n + c:
        raise ValueError("take too short for the loop")
    body = x[o:o + n].copy()
    tail = x[o + n:o + n + c]
    u = np.linspace(0, 1, c, endpoint=False)
    fade_in, fade_out = np.sin(u * np.pi / 2), np.cos(u * np.pi / 2)
    if body.ndim == 2:
        fade_in, fade_out = fade_in[:, None], fade_out[:, None]
    body[:c] = body[:c] * fade_in + tail * fade_out
    return body


def guard(loop: np.ndarray, seconds: float = 0.25, sr: int = SR) -> tuple[np.ndarray, float, float]:
    """Pad a loop with its own end before the start and its own start after the end.

    A lossy codec then sees continuous audio across both loop points; the player loops [start, end) inside the file.
    """
    g = int(seconds * sr)
    padded = np.concatenate([loop[-g:], loop, loop[:g]])
    return padded, g / sr, (g + len(loop)) / sr


def seam_report(decoded: np.ndarray, start: float, end: float, sr: int = SR) -> dict:
    """How audible is the wrap from `end` back to `start`? Compares the jump and the high-band energy at the seam
    with the same measures inside the loop."""
    mono = decoded if decoded.ndim == 1 else decoded.mean(axis=1)
    a, b = int(round(start * sr)), int(round(end * sr))
    body = mono[a:b]
    wrapped = np.concatenate([body[-2400:], body[:2400]])
    step = np.abs(np.diff(wrapped))
    typical = np.percentile(np.abs(np.diff(body)), 99.9) + 1e-9
    sos = signal.butter(4, 6000, "highpass", fs=sr, output="sos")
    high = signal.sosfilt(sos, wrapped)
    reference = signal.sosfilt(sos, body[: 4800 * 4])
    seam_energy = rms(high[2400 - 96:2400 + 96])
    return {"jump_vs_p999": float(step[2399] / typical), "seam_high_db_vs_body": float(db(seam_energy) - db(rms(reference)))}


def synthetic_ir(seconds: float, sr: int = SR, seed: int = 1, bright: float = 6000, early: list[tuple[float, float]] | None = None,
                 stereo: bool = True) -> np.ndarray:
    """Exponentially decaying noise impulse response (RT60 = `seconds`), darker over time, with optional early echoes.

    Outdoor spaces have few, late reflections: use a short decay with strong discrete echoes (quay wall, far bank).
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * 1.3 * sr)
    t = np.arange(n) / sr
    out = []
    for channel in range(2 if stereo else 1):
        noise = rng.standard_normal(n)
        # Frequency-dependent decay: split into bands that fade at different rates.
        sos_low = signal.butter(2, 800, "lowpass", fs=sr, output="sos")
        sos_mid = signal.butter(2, [800, bright], "bandpass", fs=sr, output="sos")
        sos_high = signal.butter(2, bright, "highpass", fs=sr, output="sos")
        ir = (signal.sosfilt(sos_low, noise) * np.exp(-6.9 * t / seconds)
              + signal.sosfilt(sos_mid, noise) * np.exp(-6.9 * t / (seconds * 0.75))
              + signal.sosfilt(sos_high, noise) * np.exp(-6.9 * t / (seconds * 0.35)) * 0.7)
        ir *= np.minimum(1, t / 0.012)  # the diffuse field builds up after the first reflections
        for delay, gain in early or []:
            index = int((delay + 0.0007 * channel) * sr)
            if index < n:
                ir[index] += gain * 12
        out.append(ir / (np.sqrt(np.sum(ir ** 2)) + 1e-9))
    return np.stack(out, axis=1) if stereo else out[0]


def convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Mono or stereo signal through a mono or stereo impulse response; returns stereo if either is stereo."""
    if x.ndim == 1 and ir.ndim == 1:
        return signal.fftconvolve(x, ir)
    xs = x if x.ndim == 2 else np.stack([x, x], axis=1)
    irs = ir if ir.ndim == 2 else np.stack([ir, ir], axis=1)
    return np.stack([signal.fftconvolve(xs[:, c], irs[:, c]) for c in range(2)], axis=1)


def air(x: np.ndarray, metres: float, sr: int = SR) -> np.ndarray:
    """Distance colouring without the level drop: air absorption rises with frequency squared (about 20 °C, 60 % RH;
    roughly 0.5 dB per 100 m at 1 kHz, 3 dB at 4 kHz, 10 dB at 8 kHz, 30 dB at 16 kHz), applied as a linear-phase curve."""
    n = len(x)
    spectrum = np.fft.rfft(x, axis=0)
    hz = np.fft.rfftfreq(n, 1 / sr)
    loss_db = (0.5 * (hz / 1000) + 0.14 * (hz / 1000) ** 2) * metres / 100
    curve = 10 ** (-loss_db / 20)
    if x.ndim == 2:
        curve = curve[:, None]
    return np.fft.irfft(spectrum * curve, n=n, axis=0)


def pan(x: np.ndarray, position: float) -> np.ndarray:
    """Equal-power pan of a mono signal, position -1..1."""
    angle = (position + 1) * np.pi / 4
    return np.stack([x * np.cos(angle), x * np.sin(angle)], axis=1)


def place(mix: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0, sr: int = SR) -> None:
    """Add `x` into `mix` at `at` seconds (clipped to the mix length)."""
    start = int(at * sr)
    if start >= len(mix) or start + len(x) <= 0:
        return
    source = x[max(0, -start):len(mix) - start]
    begin = max(0, start)
    mix[begin:begin + len(source)] += source * gain


def spectrogram_png(x: np.ndarray, out: str | Path, title: str = "", sr: int = SR, fmax: float = 10000, width: float = 5.2,
                    height: float = 1.9, nfft: int = 2048, floor_db: float = -80, dark: bool = False) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mono = x if x.ndim == 1 else x.mean(axis=1)
    hz, t, s = signal.spectrogram(mono, fs=sr, nperseg=nfft, noverlap=nfft - nfft // 8, window="hann", scaling="spectrum")
    level = 10 * np.log10(s + 1e-14)
    level -= level.max()
    fig, ax = plt.subplots(figsize=(width, height), dpi=110)
    if dark:
        fig.patch.set_facecolor("#1d2026")
        ax.tick_params(colors="#9aa1ad")
        ax.xaxis.label.set_color("#9aa1ad")
        ax.yaxis.label.set_color("#9aa1ad")
        ax.title.set_color("#e6e8ec")
        for spine in ax.spines.values():
            spine.set_color("#30343c")
    ax.pcolormesh(t, hz / 1000, level, vmin=floor_db, vmax=0, cmap="magma", shading="auto", rasterized=True)
    ax.set_ylim(0, fmax / 1000)
    ax.set_ylabel("kHz", fontsize=7)
    ax.set_xlabel("s", fontsize=7)
    ax.tick_params(labelsize=6)
    if title:
        ax.set_title(title, fontsize=8, loc="left")
    fig.tight_layout(pad=0.4)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), facecolor=fig.get_facecolor())
    plt.close(fig)


def write_json(path: str | Path, value) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
