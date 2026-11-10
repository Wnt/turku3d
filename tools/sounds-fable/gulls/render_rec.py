"""Gull candidates from recordings: cleaned cuts, colony loops and a harbour scene.

Usage: python -I render_rec.py SOURCES_MANIFEST.json EXTRACT_DIR OUT_DIR
EXTRACT_DIR is the output of extract.py (cleaned.wav per recording). Writes WAVs and rec.json (id, source ids, loop).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scene  # noqa: E402
import sfx  # noqa: E402

SR = sfx.SR

# name, recording id prefix, start s, end s, English note
CUTS = [
    ("gull-rec-herring-keow", "xc1002938", 0.80, 5.10, "four single calls, close"),
    ("gull-rec-herring-alarm", "xc1002938", 10.05, 12.40, "alarm series gag-ag-ag"),
    ("gull-rec-herring-longcall-a", "xc1178162", 10.6, 22.0, "long call with its lead-in notes, close"),
    ("gull-rec-herring-longcall-b", "xc707075", 4.55, 13.25, "long call in flight"),
    ("gull-rec-herring-flight", "xc797496", 14.45, 18.0, "three flight calls"),
    ("gull-rec-herring-series", "xc730804", 0.15, 16.0, "long laughing series at a breeding islet"),
    ("gull-rec-common-longcall-a", "xc803773", 0.2, 9.25, "complete long call (Tromsø)"),
    ("gull-rec-common-longcall-b", "xc834776", 1.5, 9.5, "long call on the Baltic coast (Lidingö)"),
    ("gull-rec-common-calls", "xc834776", 30.9, 39.2, "call series and single calls (Lidingö)"),
    ("gull-rec-common-mew", "xc707073", 8.7, 11.8, "three short mew notes in flight"),
    ("gull-rec-blackheaded-kree", "xc1182964", 3.05, 5.65, "three harsh flight calls"),
    ("gull-rec-blackheaded-calls", "xc776698", 16.35, 22.25, "a long call and a call series"),
    ("gull-rec-lbb-alarm", "xc776692", 0.0, 5.5, "lesser black-backed gull: alarm calls"),
    ("gull-rec-lbb-longcall", "xc776692", 5.85, 11.0, "lesser black-backed gull: long call"),
    ("gull-rec-gbb-longcall", "xc803774", 0.8, 9.5, "great black-backed gull: long call"),
]
# Single calls for the scene and for the live demo sample set: (recording prefix, start, end)
SINGLES = [
    ("xc1002938", 0.80, 1.35), ("xc1002938", 2.58, 3.20), ("xc1002938", 3.53, 4.15), ("xc1002938", 4.57, 5.15), ("xc1002938", 5.63, 6.25),
    ("xc1002938", 6.61, 7.22), ("xc1002938", 7.89, 8.50), ("xc1002938", 9.05, 9.65), ("xc1002938", 10.05, 12.40),
    ("xc797496", 9.50, 10.25), ("xc797496", 14.50, 15.30), ("xc797496", 15.80, 16.60), ("xc797496", 17.20, 17.95),
    ("xc707075", 0.30, 4.20), ("xc707075", 4.55, 13.25), ("xc1178162", 10.6, 22.0), ("xc1178162", 13.85, 14.55),
    ("xc803773", 0.2, 9.25), ("xc834776", 12.40, 12.90), ("xc834776", 25.83, 26.30), ("xc834776", 38.60, 39.15), ("xc834776", 31.0, 35.3),
    ("xc834776", 24.42, 25.05), ("xc707073", 8.80, 9.12), ("xc707073", 11.38, 11.75),
    ("xc1182964", 3.05, 3.75), ("xc1182964", 3.85, 4.60), ("xc1182964", 4.75, 5.60), ("xc776698", 16.38, 17.35), ("xc776698", 18.30, 22.20),
    ("xc776692", 0.02, 1.35), ("xc776692", 2.60, 3.10), ("xc776692", 5.85, 11.0),
]


def cut(cleaned: np.ndarray, start: float, end: float) -> np.ndarray:
    a, b = max(0, int((start - 0.05) * SR)), min(len(cleaned), int((end + 0.25) * SR))
    return sfx.fade(cleaned[a:b], 0.04, 0.2)


def steady_stretch(x: np.ndarray, seconds: float) -> int:
    """Start (samples) of the stretch whose level varies least: avoids one-off events in an ambience bed."""
    mono = x if x.ndim == 1 else x.mean(axis=1)
    hop = SR // 2
    level = np.array([np.sqrt(np.mean(mono[i:i + hop] ** 2)) for i in range(0, len(mono) - hop, hop)])
    window = int(seconds * 2)
    spread = [np.max(level[i:i + window]) / (np.median(level[i:i + window]) + 1e-9) for i in range(0, len(level) - window)]
    return int(np.argmin(spread)) * hop if spread else 0


def main() -> None:
    manifest = {e["id"]: e for e in json.loads(Path(sys.argv[1]).read_text())}
    extract, out = Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)

    def entry_of(prefix: str) -> dict:
        return next(e for key, e in manifest.items() if key.startswith(prefix))

    cleaned_cache: dict[str, np.ndarray] = {}

    def cleaned(prefix: str) -> np.ndarray:
        if prefix not in cleaned_cache:
            cleaned_cache[prefix] = sfx.load(extract / entry_of(prefix)["id"] / "cleaned.wav")
        return cleaned_cache[prefix]

    entries = []
    for name, prefix, start, end, note in CUTS:
        audio = sfx.normalise(cut(cleaned(prefix), start, end), -3)
        sfx.save(out / f"{name}.wav", audio)
        entries.append({"id": name, "sources": [entry_of(prefix)["id"]], "note": note, "loop": False})
        print(name, f"{len(audio) / SR:.1f} s")

    # Sample set for the scene and the live demo: every single normalised to the same peak, mono.
    singles = []
    samples = out / "samples"
    samples.mkdir(exist_ok=True)
    for index, (prefix, start, end) in enumerate(SINGLES):
        audio = sfx.normalise(cut(cleaned(prefix), start, end), -3)
        species = entry_of(prefix).get("species", "")
        kind = "herring" if "argentatus" in species else "common" if "canus" in species else "blackheaded" if "ridibundus" in species else "other"
        sfx.save(samples / f"s{index:02d}-{kind}.wav", audio)
        singles.append({"file": f"samples/s{index:02d}-{kind}.wav", "kind": kind, "seconds": round(len(audio) / SR, 2), "long": end - start > 2.5, "source": entry_of(prefix)["id"]})
    sfx.write_json(out / "samples.json", singles)

    # Colony beds as loops: only a high-pass, no noise reduction (the ambience is the point).
    for name, prefix, seconds, note in (("gull-rec-blackheaded-colony-loop", "xc1091839", 36.0, "black-headed gull colony at the nest site"),
                                        ("gull-rec-herring-roost-loop", "xc953928", 44.0, "roost of more than 2400 herring gulls")):
        raw = sfx.load(entry_of(prefix)["path"], mono=False)
        raw = signal.sosfiltfilt(signal.butter(4, 200, "highpass", fs=SR, output="sos"), raw, axis=0)
        loop = sfx.make_loop(raw, seconds, 3.0, offset=1.0)
        sfx.save(out / f"{name}.wav", sfx.normalise(loop, -2))
        entries.append({"id": name, "sources": [entry_of(prefix)["id"]], "note": note, "loop": True})
        print(name, f"{seconds:.0f} s loop")

    # Whole soundscapes as recorded: a fishing port with gulls, and gulls over a town at dawn.
    for name, sid, start, seconds, note in (("gull-rec-port-loop", "bsb-2573-gulls-on-the-harbor", 42.0, 60.0, "fishing port soundscape with gulls"),
                                            ("gull-rec-town-dawn-loop", "wc-pdsounds-gulls-street-dawn", 1.0, 50.0, "gulls above the streets of a seaside town at dawn")):
        if sid not in manifest:
            continue
        raw = sfx.load(manifest[sid]["path"], mono=False)
        raw = signal.sosfiltfilt(signal.butter(2, 80, "highpass", fs=SR, output="sos"), raw, axis=0)
        loop = sfx.make_loop(raw, seconds, 4.0, offset=start)
        sfx.save(out / f"{name}.wav", sfx.normalise(loop, -2))
        entries.append({"id": name, "sources": [sid], "note": note, "loop": True})
        print(name, f"{seconds:.0f} s loop")

    # Harbour scene from recordings on a circular timeline: real water, a far roost, and single birds placed around.
    seconds = 60.0
    n = int(seconds * SR)
    rng = np.random.default_rng(1110)
    used = set()
    mix = np.zeros((n, 2))
    water_entry = manifest.get("bsb-1444-pontoon-marina-deauville")
    if water_entry:
        water = sfx.load(water_entry["path"], mono=False)
        water = signal.sosfiltfilt(signal.butter(2, 90, "highpass", fs=SR, output="sos"), water, axis=0)
        start = steady_stretch(water[: len(water) - int((seconds + 6) * SR)], seconds)
        bed = sfx.make_loop(water[start:], seconds, 4.0, offset=0.0)
        sfx.save(out / "bed-water.wav", sfx.normalise(sfx.make_loop(water[start:], 30.0, 3.0, offset=0.0), -6))
        mix += bed / (sfx.rms(bed) + 1e-9) * 10 ** (-27 / 20)
        used.add(water_entry["id"])
    else:
        mix += scene.water_bed(seconds, rng) + scene.wind_bed(seconds, rng)
    roost = sfx.load(entry_of("xc953928")["path"])
    roost = signal.sosfiltfilt(signal.butter(4, 400, "highpass", fs=SR, output="sos"), roost)
    far = sfx.make_loop(roost, 44.0, 3.0, offset=1.0)
    far = np.concatenate([far, far])[:n]  # 44 s bed under a 60 s loop: fade it around the wrap instead of cutting
    far *= np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (3.0 * SR))
    heard = scene.at_distance(far, 400, -0.3)[:n]
    mix += heard / (sfx.rms(heard) + 1e-9) * 10 ** (-36 / 20)
    used.add(entry_of("xc953928")["id"])
    by_kind: dict[str, list[dict]] = {}
    for s in singles:
        by_kind.setdefault(s["kind"], []).append(s)

    def sample(kind: str, long: bool | None = None) -> tuple[np.ndarray, dict]:
        pool = [s for s in by_kind[kind] if long is None or s["long"] == long]
        chosen = pool[rng.integers(len(pool))]
        used.add(chosen["source"])
        x = sfx.load(out / chosen["file"])
        rate = rng.uniform(0.95, 1.06)  # every placement is a slightly different bird
        return signal.resample(x, int(len(x) / rate)), chosen

    events = [
        (3.0, "herring", True, 45, -0.4), (9.5, "herring", False, 90, 0.5), (11.0, "herring", False, 120, 0.2), (15.0, "common", True, 60, 0.6),
        (22.0, "blackheaded", False, 30, -0.6), (23.4, "blackheaded", False, 55, -0.2), (27.0, "common", False, 40, 0.3), (28.2, "common", False, 75, -0.5),
        (33.0, "herring", True, 110, 0.1), (41.0, "blackheaded", True, 70, 0.7), (46.0, "herring", False, 35, -0.3), (47.5, "herring", False, 80, 0.4),
        (52.0, "common", False, 50, -0.7), (55.5, "blackheaded", False, 95, 0.0),
    ]
    for _ in range(26):  # scattered far birds
        events.append((rng.uniform(0, seconds), rng.choice(["herring", "common", "blackheaded"], p=[0.4, 0.3, 0.3]), False, rng.uniform(150, 380), rng.uniform(-0.9, 0.9)))
    for at, kind, long, metres, position in events:
        x, _ = sample(kind, long)
        scene.place_wrapped(mix, scene.at_distance(x, metres, position), at, 1.3)
    sfx.save(out / "gull-mix-harbour-loop.wav", sfx.normalise(mix, -2))
    entries.append({"id": "gull-mix-harbour-loop", "sources": sorted(used), "note": "harbour scene from recordings: water, far roost, placed single birds", "loop": True})
    print("gull-mix-harbour-loop", f"{seconds:.0f} s loop, sources:", len(used))
    sfx.write_json(out / "rec.json", entries)


if __name__ == "__main__":
    main()
