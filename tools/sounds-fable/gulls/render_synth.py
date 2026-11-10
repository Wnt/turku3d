"""Synthetic gull candidates (no recordings): single calls, call series, a distance demo and a harbour loop.

Usage: python -I render_synth.py OUT_DIR
Writes 48 kHz WAVs and `synth.json` (ids, loop points, descriptions) for make_board.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import model  # noqa: E402
import scene  # noqa: E402
import sfx  # noqa: E402

SR = sfx.SR


def close_up(call: np.ndarray, metres: float = 25, position: float = 0.0) -> np.ndarray:
    """Single-call candidates are presented as heard from a quay: a little distance and space, peak-normalised."""
    return sfx.normalise(scene.at_distance(call, metres, position), -3)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    entries = []

    def add(name: str, audio: np.ndarray, note: str, loop: bool = False) -> None:
        """`loop`: the file is a loop body that wraps seamlessly (the board adds codec guard bands)."""
        sfx.save(out / f"{name}.wav", audio)
        entries.append({"id": name, "note": note, "loop": loop})
        print(name, f"{len(audio) / SR:.1f} s", "peak", round(float(np.max(np.abs(audio))), 3))

    rng = np.random.default_rng(20261110)
    add("gull-syn-herring-keow", close_up(np.concatenate([model.keow("herring", rng), model.gap(0.5), model.keow("herring", rng), model.gap(0.7), model.keow("herring", rng)])),
        "three single herring gull calls, three different birds")
    add("gull-syn-herring-longcall", close_up(model.long_call("herring", rng)), "herring gull long call (laughing series)")
    add("gull-syn-herring-alarm", close_up(model.alarm("herring", rng)), "herring gull alarm, gag-ag-ag")
    add("gull-syn-common-mew", close_up(np.concatenate([model.mew("common", rng), model.gap(0.6), model.mew("common", rng)])), "two common gull mews")
    add("gull-syn-common-longcall", close_up(model.long_call("common", rng)), "common gull long call")
    add("gull-syn-blackheaded-kree", close_up(np.concatenate([model.kree(rng), model.gap(0.4), model.kree(rng), model.gap(0.25), model.chatter(rng)])),
        "black-headed gull: two calls and a squabble")

    # The same long call at three distances over water (level kept as it would be heard: far is quiet).
    call = model.long_call("herring", np.random.default_rng(5))
    parts = []
    for metres in (15, 80, 300):
        heard = scene.at_distance(call, metres, 0.0)
        parts += [heard, np.zeros((int(0.8 * SR), 2))]
    demo = np.concatenate(parts)
    add("gull-syn-distance", demo / (np.max(np.abs(demo)) + 1e-9) * 0.9, "one long call at 15 m, 80 m and 300 m over water")

    # Harbour loop: 48 s, built on a circular timeline, so it wraps without a crossfade.
    seconds = 48.0
    n = int(seconds * SR)
    rng = np.random.default_rng(77)
    mix = scene.water_bed(seconds, rng) + scene.wind_bed(seconds, rng)
    events = []
    # A distant colony: many faint calls far away.
    for _ in range(46):
        kind = rng.choice(["keow", "kree", "chatter", "long", "mew"], p=[0.34, 0.26, 0.16, 0.1, 0.14])
        species = rng.choice(["herring", "common"])
        call = {"keow": lambda: model.keow(species, rng), "kree": lambda: model.kree(rng), "chatter": lambda: model.chatter(rng),
                "long": lambda: model.long_call(species, rng), "mew": lambda: model.mew(species, rng)}[kind]()
        events.append((rng.uniform(0, seconds), call, rng.uniform(140, 420), rng.uniform(-0.9, 0.9)))
    # A few birds nearby, answering one another.
    lead = rng.uniform(4, 8)
    events.append((lead, model.long_call("herring", rng), 35, -0.35))
    events.append((lead + 3.4, model.long_call("herring", rng), 70, 0.5))
    events.append((lead + 12, model.keow("common", rng), 28, 0.2))
    events.append((lead + 13.1, model.keow("common", rng), 45, -0.6))
    events.append((lead + 21, np.concatenate([model.kree(rng), model.gap(0.3), model.kree(rng)]), 22, 0.65))
    events.append((lead + 27.5, model.mew("common", rng), 50, -0.2))
    events.append((lead + 33, model.alarm("herring", rng), 60, 0.1))
    events.append((lead + 38, model.keow("herring", rng), 32, -0.5))
    for at, call, metres, position in events:
        scene.place_wrapped(mix, scene.at_distance(call, metres, position), at, 1.6)
    mix = sfx.normalise(mix, -2)
    add("gull-syn-harbour-loop", mix, "synthetic harbour: water, wind, a distant colony and a few near birds", True)
    sfx.write_json(out / "synth.json", entries)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
