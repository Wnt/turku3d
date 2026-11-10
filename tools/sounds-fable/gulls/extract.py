"""Find, clean and measure gull calls in field recordings.

Usage: python -I extract.py MANIFEST.json OUT_DIR [id-substring ...]
For every gull recording of the manifest: high-pass, spectral noise reduction, note and call segmentation, f0 tracking
and harmonic measurement. Writes OUT_DIR/<id>/overview.png (spectrogram with the detected calls), cleaned call cuts
OUT_DIR/<id>/call-NN.wav, their analysis OUT_DIR/<id>/call-NN.npz (f0 and harmonic amplitudes for resynthesis) and
OUT_DIR/calls.json (one row per call: timing, notes, f0 statistics, signal-to-noise ratio, type guess).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage, signal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sfx  # noqa: E402

SR = sfx.SR
NFFT, HOP = 2048, 240  # 43 ms window, 5 ms hop
F0_RANGE = {"default": (300.0, 2400.0), "ridibundus": (170.0, 900.0)}  # the black-headed gull's voice is a low buzz
HARMONICS = 32
NFFT_AMP = 1024  # 21 ms window for harmonic amplitudes: less smearing of fast notes


def denoise(x: np.ndarray, strength: float = 2.0, floor_db: float = -15.0) -> tuple[np.ndarray, np.ndarray]:
    """Spectral subtraction against the recording's own quiet frames. Returns (cleaned signal, noise spectrum)."""
    _, _, spectrum = signal.stft(x, fs=SR, nperseg=NFFT, noverlap=NFFT - NFFT // 4, window="hann")
    magnitude = np.abs(spectrum)
    noise = np.percentile(magnitude, 20, axis=1, keepdims=True) * 1.4
    gain = np.sqrt(np.maximum(1 - strength * (noise / (magnitude + 1e-12)) ** 2, 10 ** (floor_db / 10)))
    gain = ndimage.uniform_filter(ndimage.median_filter(gain, size=(3, 5)), size=(3, 3))
    _, cleaned = signal.istft(spectrum * gain, fs=SR, nperseg=NFFT, noverlap=NFFT - NFFT // 4, window="hann")
    return cleaned[:len(x)], noise[:, 0]


def stft_mag(x: np.ndarray, nfft: int = NFFT) -> tuple[np.ndarray, np.ndarray]:
    hz, _, spectrum = signal.stft(x, fs=SR, nperseg=nfft, noverlap=nfft - HOP, window="hann", boundary="zeros", padded=True)
    return hz, np.abs(spectrum)


def track_f0(magnitude: np.ndarray, hz: np.ndarray, active: np.ndarray, f0_range: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Harmonic-sum salience with a Viterbi path. Returns (f0 per frame, harmonicity 0..1 per frame); 0 where inactive."""
    f0_min, f0_max = f0_range
    candidates = f0_min * 2 ** (np.arange(0, int(np.log2(f0_max / f0_min) * 48) + 1) / 48)
    bin_hz = hz[1] - hz[0]
    frames = magnitude.shape[1]
    weights = 1 / np.sqrt(np.arange(1, 11))
    salience = np.zeros((len(candidates), frames))
    total = np.sqrt(np.sum(magnitude[int(250 / bin_hz):int(10000 / bin_hz)] ** 2, axis=0)) + 1e-12
    for ci, f0 in enumerate(candidates):
        on = np.zeros(frames)
        off = np.zeros(frames)
        for k in range(1, 11):
            if k * f0 > 11000:
                break
            b = k * f0 / bin_hz
            lo = int(b)
            on += weights[k - 1] * np.maximum(magnitude[lo], magnitude[lo + 1])
            between = int((k - 0.5) * f0 / bin_hz)
            off += weights[k - 1] * magnitude[between]
        salience[ci] = (on - 0.7 * off) / total
    # Viterbi over active frames: prefer salience, punish jumps (octave errors show up as jumps of 48 steps).
    f0 = np.zeros(frames)
    harmonicity = np.zeros(frames)
    index = np.flatnonzero(active)
    if len(index) == 0:
        return f0, harmonicity
    steps = np.arange(len(candidates))
    jump = np.abs(steps[:, None] - steps[None, :]) * 0.02
    # Split into runs of consecutive active frames.
    runs = np.split(index, np.flatnonzero(np.diff(index) > 1) + 1)
    for run in runs:
        score = salience[:, run[0]].copy()
        back = np.zeros((len(candidates), len(run)), dtype=int)
        for i, frame in enumerate(run[1:], start=1):
            through = score[None, :] - jump
            back[:, i] = np.argmax(through, axis=1)
            score = through[steps, back[:, i]] + salience[:, frame]
        state = int(np.argmax(score))
        for i in range(len(run) - 1, -1, -1):
            f0[run[i]] = candidates[state]
            harmonicity[run[i]] = salience[state, run[i]]
            state = back[state, i]
    return f0, harmonicity


def harmonic_amplitudes(magnitude: np.ndarray, hz: np.ndarray, f0: np.ndarray) -> np.ndarray:
    """(frames, HARMONICS) linear amplitudes read at k·f0 (peak within ±1 bin); zero where f0 is zero."""
    bin_hz = hz[1] - hz[0]
    frames = magnitude.shape[1]
    out = np.zeros((frames, HARMONICS))
    top = magnitude.shape[0] - 2
    for k in range(1, HARMONICS + 1):
        b = np.round(k * f0 / bin_hz).astype(int)
        ok = (f0 > 0) & (b < top)
        b = np.clip(b, 1, top)
        peak = np.maximum(np.maximum(magnitude[b - 1, np.arange(frames)], magnitude[b, np.arange(frames)]), magnitude[b + 1, np.arange(frames)])
        out[:, k - 1] = np.where(ok, peak, 0.0)
    return out


def segments(mask: np.ndarray, min_len: int, join: int) -> list[tuple[int, int]]:
    """Runs of True at least `min_len` frames long, after joining gaps shorter than `join` frames."""
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(int)))
    runs = [(a, b) for a, b in zip(edges[::2], edges[1::2])]
    joined: list[list[int]] = []
    for a, b in runs:
        if joined and a - joined[-1][1] < join:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    return [(a, b) for a, b in joined if b - a >= min_len]


def guess_type(notes: list[tuple[float, float]]) -> str:
    durations = [b - a for a, b in notes]
    if len(notes) >= 6:
        return "longcall"
    if len(notes) >= 3 and np.median(durations) < 0.16:
        return "alarm"
    if len(notes) == 1 and durations[0] >= 0.7:
        return "mew"
    if len(notes) == 1:
        return "single"
    return "series"


def analyse(entry: dict, out: Path) -> list[dict]:
    rid = entry["id"]
    x = sfx.load(entry["path"])
    x = signal.sosfiltfilt(signal.butter(4, 280, "highpass", fs=SR, output="sos"), x)
    cleaned, noise = denoise(x)
    hz, magnitude = stft_mag(cleaned)
    _, raw_magnitude = stft_mag(x)
    band = (hz >= 700) & (hz <= 7000)
    level = 10 * np.log10(np.sum(magnitude[band] ** 2, axis=0) + 1e-14)
    level = ndimage.uniform_filter1d(level, 4)
    floor = np.percentile(level, 15)
    peak = np.percentile(level, 99.5)
    threshold = max(floor + 14, peak - 32)
    note_mask = level > threshold
    notes = segments(note_mask, min_len=8, join=5)  # >= 40 ms, gaps < 25 ms belong to one note
    calls: list[list[tuple[int, int]]] = []
    for a, b in notes:
        if calls and a - calls[-1][-1][1] < int(0.55 * SR / HOP):
            calls[-1].append((a, b))
        else:
            calls.append([(a, b)])
    active = np.zeros(len(level), dtype=bool)
    for a, b in notes:
        active[a:b] = True
    f0_range = F0_RANGE["ridibundus" if "ridibundus" in entry.get("species", "") else "default"]
    f0, harmonicity = track_f0(magnitude, hz, active, f0_range)
    hz_amp, magnitude_amp = stft_mag(cleaned, NFFT_AMP)
    frames_total = min(magnitude.shape[1], magnitude_amp.shape[1])
    amplitudes = harmonic_amplitudes(magnitude_amp[:, :frames_total], hz_amp, f0[:frames_total])
    # Residual: what is left between the harmonics, smoothed over frequency (the breath and rasp of the voice).
    residual = magnitude_amp[:, :frames_total].copy()
    bin_amp = hz_amp[1] - hz_amp[0]
    for k in range(1, HARMONICS + 1):
        b = np.round(k * f0[:frames_total] / bin_amp).astype(int)
        for offset in (-2, -1, 0, 1, 2):
            rows = np.clip(b + offset, 0, residual.shape[0] - 1)
            cols = np.flatnonzero(f0[:frames_total] > 0)
            residual[rows[cols], cols] = 0
    residual = ndimage.maximum_filter1d(ndimage.uniform_filter1d(residual, 9, axis=0), 5, axis=0)
    raw_level = 10 * np.log10(np.sum(raw_magnitude[band] ** 2, axis=0) + 1e-14)
    raw_floor = np.percentile(raw_level, 15)
    target = out / rid
    target.mkdir(parents=True, exist_ok=True)
    sfx.save(target / "cleaned.wav", cleaned)
    np.savez_compressed(target / "tracks.npz", f0=f0[:frames_total], amplitudes=amplitudes.astype(np.float32), level=level[:frames_total], hop=HOP,
                        residual=residual[::4].astype(np.float32), residual_hz=hz_amp[::4])
    rows = []
    for number, call in enumerate(calls):
        a, b = call[0][0], call[-1][1]
        if b - a < int(0.08 * SR / HOP):
            continue
        frames = np.concatenate([np.arange(s, e) for s, e in call])
        voiced = frames[f0[frames] > 0]
        if len(voiced) < 8:
            continue
        snr = float(np.percentile(raw_level[frames], 90) - raw_floor)
        start = max(0, a * HOP - int(0.04 * SR))
        end = min(len(cleaned), b * HOP + int(0.22 * SR))
        cut = sfx.fade(cleaned[start:end], 0.03, 0.12)
        clipped = float(np.mean(np.abs(x[start:end]) > 0.985 * np.max(np.abs(x))) > 0.002) if np.max(np.abs(x)) > 0.98 else 0.0
        note_times = [((s - a) * HOP / SR, (e - a) * HOP / SR) for s, e in call]
        spectrum = np.mean(amplitudes[voiced] ** 2, axis=0)
        strongest = int(np.argmax(spectrum)) + 1
        audible = int(np.sum(spectrum > spectrum.max() * 10 ** (-30 / 10)))
        row = {
            "recording": rid, "call": number, "file": f"{rid}/call-{number:02d}.wav", "species": entry.get("species", ""), "licence": entry.get("licence", ""),
            "start_s": round(a * HOP / SR, 2), "seconds": round((b - a) * HOP / SR, 2), "notes": len(call), "type": guess_type(note_times),
            "note_seconds_median": round(float(np.median([e - s for s, e in note_times])), 3),
            "note_rate_hz": round((len(call) - 1) / max(1e-6, note_times[-1][0] - note_times[0][0]), 2) if len(call) > 1 else 0,
            "f0_median": round(float(np.median(f0[voiced])), 0), "f0_p10": round(float(np.percentile(f0[voiced], 10)), 0), "f0_p90": round(float(np.percentile(f0[voiced], 90)), 0),
            "harmonicity": round(float(np.median(harmonicity[voiced])), 3), "strongest_harmonic": strongest, "harmonics_within_30db": audible,
            "centroid_hz": round(float(np.sum(hz[band, None] * magnitude[band][:, frames] ** 2) / np.sum(magnitude[band][:, frames] ** 2)), 0),
            "snr_db": round(snr, 1), "clipped": clipped, "peak": round(float(np.max(np.abs(cut))), 4),
        }
        sfx.save(target / f"call-{number:02d}.wav", cut)
        np.savez_compressed(target / f"call-{number:02d}.npz", f0=f0[a:b], amplitudes=amplitudes[a:b], level=level[a:b], notes=np.array(note_times), hop=HOP,
                            residual=residual[::4, a:b].astype(np.float32), residual_hz=hz_amp[::4], start=a)
        rows.append(row)
    # Overview: spectrogram of the cleaned recording with call boxes.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    seconds = len(x) / SR
    fig, ax = plt.subplots(figsize=(min(24, max(8, seconds * 0.45)), 3.0), dpi=80)
    shown = 20 * np.log10(magnitude[: int(9000 / (hz[1] - hz[0]))] + 1e-9)
    ax.imshow(shown, origin="lower", aspect="auto", extent=(0, magnitude.shape[1] * HOP / SR, 0, 9), cmap="magma", vmin=shown.max() - 75, vmax=shown.max())
    t = np.arange(len(f0)) * HOP / SR
    ax.plot(t[f0 > 0], f0[f0 > 0] / 1000, ".", color="#5ff", markersize=1.2)
    for row in rows:
        ax.axvspan(row["start_s"], row["start_s"] + row["seconds"], ymin=0.94, ymax=1, color="#7f7" if row["snr_db"] > 25 else "#fa5")
        ax.text(row["start_s"], 8.2, f"{row['call']}:{row['type'][:4]} {row['snr_db']:.0f}dB", color="white", fontsize=6)
    ax.set_title(f"{rid} ({entry.get('species', '')}; {entry.get('licence', '')})", fontsize=8, loc="left")
    fig.tight_layout(pad=0.3)
    fig.savefig(target / "overview.png")
    plt.close(fig)
    return rows


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    wanted = sys.argv[3:]
    rows_path = out / "calls.json"
    rows = json.loads(rows_path.read_text()) if rows_path.exists() else []
    for entry in manifest:
        if entry.get("area") != "gulls" or (wanted and not any(w in entry["id"] for w in wanted)):
            continue
        if not wanted and any(r["recording"] == entry["id"] for r in rows):
            continue
        try:
            new = analyse(entry, out)
        except Exception as error:  # one broken download must not stop the batch
            print(entry["id"], "FAILED", repr(error))
            continue
        rows = [r for r in rows if r["recording"] != entry["id"]] + new
        good = [r for r in new if r["snr_db"] > 25]
        print(f"{entry['id']:<40} calls {len(new):>3}  snr>25: {len(good):>3}  types: " + ", ".join(sorted({r['type'] for r in new})))
        sfx.write_json(rows_path, rows)


if __name__ == "__main__":
    main()
