"""Offline replica of the game's current music sequencer (game/src/audio/music/index.ts), for A/B on the board.

Same notes, waveforms, envelopes, layer gains, hurry curve, crossfade and low-pass as `MusicSystem.scheduleBeat`,
rendered with band-limited oscillators. Usage: python -I current_engine.py OUT_DIR
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
REPO = Path(__file__).resolve().parents[3]
PARAMS = json.loads((REPO / "game/src/audio/music/params.json").read_text())
# id, bpm, root, progression, wave, liftStart, tempoLift (tracks.ts)
TRACKS = {
    "aura-neon": (108, 220.0, [0, 5, 3, 7], "sawtooth", 0.62, 0.2),
    "market-sun": (102, 261.63, [0, 5, 2, 7], "triangle", 0.7, 0.15),
    "river-dub": (92, 293.66, [0, 3, 7, 5], "sine", 0.58, 0.24),
    "ring-road": (120, 329.63, [0, 5, 7, 3], "square", 0.64, 0.18),
    "harbour-glass": (84, 196.0, [0, 4, 7, 2], "triangle", 0.72, 0.22),
}


def oscillator(wave: str, hz: float, n: int) -> np.ndarray:
    phase = 2 * np.pi * hz * np.arange(n) / SR
    if wave == "sine":
        return np.sin(phase)
    out = np.zeros(n)
    top = int(16000 / hz)
    if wave == "sawtooth":
        for k in range(1, top + 1):
            out += (-1) ** (k + 1) * np.sin(k * phase) / k
        return out * 2 / np.pi
    for k in range(1, top + 1, 2):
        out += np.sin(k * phase) / k if wave == "square" else (-1) ** ((k - 1) // 2) * np.sin(k * phase) / k ** 2
    return out * (4 / np.pi if wave == "square" else 8 / np.pi ** 2)


def voice(wave: str, hz: float, level: float, length: float) -> np.ndarray:
    """linearRamp to `level` in 12 ms, setTargetAtTime(0) from min(length, 0.22) with tau max(0.03, length/4), stop at length + 0.15."""
    n = int((length + 0.15) * SR)
    t = np.arange(n) / SR
    hold = min(length, 0.22)
    tau = max(0.03, length / 4)
    envelope = np.where(t < 0.012, t / 0.012, 1.0) * np.where(t < hold, 1.0, np.exp(-(t - hold) / tau))
    return oscillator(wave, hz, n) * envelope * level


def hurry(progress: float, start: float, lift: float) -> dict:
    x = min(1.0, max(0.0, (progress - start) / (1 - start)))
    smooth = x * x * (3 - 2 * x)
    return {"intensity": smooth, "tempo": 1 + lift * smooth, "percussion": x * x, "cutoff": 2300 + 9500 * x}


def chime(mix: np.ndarray, at: float, root: float, resolved: bool) -> None:
    base = root * (2 if resolved else 3)
    for i, ratio in enumerate([1, 1.25, 1.5] if resolved else [1, 1.2]):
        n = int(0.45 * SR)
        t = np.arange(n) / SR
        envelope = np.where(t < 0.01, t / 0.01, 1.0) * np.where(t < 0.07, 1.0, np.exp(-(t - 0.07) / 0.1)) * 0.09
        sfx.place(mix, np.sin(2 * np.pi * base * ratio * t) * envelope, at + i * 0.09)


def render(plan: list[tuple[str, str, int]], progress=None, seconds_cap: float = 200.0) -> np.ndarray:
    """`plan`: (track id, state, bars) segments; `progress(time)`: delivery progress 0..1 for mission segments."""
    gains = PARAMS["layerGains"]
    mix = np.zeros(int(seconds_cap * SR))
    cutoffs: list[tuple[float, float]] = []
    time, beat = 0.05, 0
    fade_beats = max(1, PARAMS["crossfadeBars"] * 4)
    previous = None
    for index, (track_id, state, bars) in enumerate(plan):
        segment_start = beat
        if state == "mission" and (previous is None or previous[1] != "mission"):
            chime(mix, time, TRACKS[track_id][1], False)
        for _ in range(bars * 4):
            bpm, root, progression, wave, lift_start, tempo_lift = TRACKS[track_id]
            p = progress(time) if progress and state == "mission" else 0.0
            curve = hurry(p, lift_start, tempo_lift)
            weight = min(1.0, (beat - segment_start + 1) / fade_beats) if previous else 1.0

            def schedule(tid: str, st: str, w: float) -> None:
                _, r, prog, wv, _, _ = TRACKS[tid]
                mission = st == "mission"
                hz = r * 2 ** (prog[(beat // 4) % len(prog)] / 12)

                def layer(stem: str, gain: float, freq: float, length: float, shape: str) -> None:
                    level = gain * gains[stem] * w
                    if level >= 0.001:
                        sfx.place(mix, voice(shape, freq, level, length), time)
                if beat % 4 == 0:
                    layer("pad", 0.085, hz, 1.45, "sine")
                if beat % 2 == 0:
                    layer("bass", 0.12 if mission else 0.075, hz / 2, 0.32, "triangle")
                if mission or beat % 2 == 0:
                    layer("drums", 0.12 if mission else 0.06, 160 if beat % 2 else 65, 0.095, "sine")
                if beat % 4 == 2 or (mission and beat % 2 == 1):
                    layer("lead", 0.09 if mission else 0.045, hz * (1.5 if beat % 2 else 2), 0.18, wv)
                if mission:
                    layer("percussion", 0.025 + curve["percussion"] * 0.08, 2600, 0.035, "square")
                    if curve["intensity"] > 0.35:
                        layer("panic", curve["intensity"] * 0.09, hz * 3, 0.11, "sawtooth")
                    if p >= 1 - PARAMS["stingerLeadBars"] * 0.09 and beat % 4 == 0:
                        chime(mix, time, r, False)
            if previous and weight < 1:
                schedule(previous[0], previous[1], 1 - weight)
            if state != "menu":
                schedule(track_id, state, weight)
            cutoffs.append((time, curve["cutoff"] if state == "mission" else 6500.0))
            beat += 1
            time += 60 / (bpm * (curve["tempo"] if state == "mission" else 1))
        previous = (track_id, state)
    end = int((time + 1.0) * SR)
    mix = mix[:end]
    # Master low-pass (Web Audio biquad, Q 1 dB), cutoff stepped per beat with filter state carried over.
    out = np.zeros_like(mix)
    state = np.zeros(2)
    edges = [int(t * SR) for t, _ in cutoffs] + [end]
    edges[0] = 0
    for (_, cutoff), a, b in zip(cutoffs, edges[:-1], edges[1:]):
        w0 = 2 * np.pi * cutoff / SR
        alpha = np.sin(w0) / (2 * 10 ** (1 / 20))
        b0 = (1 - np.cos(w0)) / 2
        coeff_b = np.array([b0, 2 * b0, b0]) / (1 + alpha)
        coeff_a = np.array([1, -2 * np.cos(w0) / (1 + alpha), (1 - alpha) / (1 + alpha)])
        out[a:b], state = signal.lfilter(coeff_b, coeff_a, mix[a:b], zi=state)
    return out * PARAMS["volume"]


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    explore = render([("aura-neon", "explore", 12), ("market-sun", "explore", 10), ("river-dub", "explore", 8), ("ring-road", "explore", 10), ("harbour-glass", "explore", 6)])
    sfx.save(out / "music-current-explore.wav", explore)
    total = 75.0
    mission = render([("ring-road", "explore", 4), ("ring-road", "mission", 40)], progress=lambda t: min(1.0, max(0.0, (t - 8) / (total - 16))), seconds_cap=120)
    sfx.save(out / "music-current-mission.wav", mission)
    for name, x in (("explore", explore), ("mission", mission)):
        print(name, f"{len(x) / SR:.1f} s peak {np.max(np.abs(x)):.3f} rms {sfx.db(sfx.rms(x)):.1f} dB")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
