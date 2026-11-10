"""Tyre squeal candidates from CC0 recordings: trimmed originals, crossfade loops, a granular long loop and squeal
events found in a car-park ambience.

Usage: python -I render_rec.py SOURCES_MANIFEST.json OUT_DIR
Writes WAVs and rec.json (Finnish titles/descriptions for the board, source ids, loop flag).
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


def trim(x: np.ndarray, threshold_db: float = -45) -> np.ndarray:
    mono = x if x.ndim == 1 else x.mean(axis=1)
    level = ndimage.maximum_filter1d(np.abs(mono), SR // 50)
    live = np.flatnonzero(level > np.max(level) * 10 ** (threshold_db / 20))
    return x[max(0, live[0] - SR // 100):live[-1] + SR // 20]


def tonal_track(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per 20 ms frame: level (dB) in 0.8-4 kHz and how far the strongest peak there stands above the band's median."""
    hz, t, s = signal.spectrogram(x, fs=SR, nperseg=2048, noverlap=2048 - 960)
    band = (hz > 800) & (hz < 4000)
    level = 10 * np.log10(np.sum(s[band], axis=0) + 1e-16)
    tonal = 10 * np.log10(np.max(s[band], axis=0) / (np.median(s[band], axis=0) + 1e-18))
    return level, tonal


def granular_loop(material: list[np.ndarray], seconds: float, rng: np.random.Generator, grain: float = 0.16, density: float = 5.0) -> np.ndarray:
    """Overlap-add of windowed grains taken at random from the steady parts, on a circular timeline: a long loop that
    never repeats the 2-second takes verbatim. Grains keep their pitch (no resampling), so the tone stays real."""
    n = int(seconds * SR)
    out = np.zeros(n)
    weight = np.zeros(n)
    size = int(grain * SR)
    window = np.hanning(size)
    count = int(seconds / grain * density)
    for i in range(count):
        take = material[rng.integers(len(material))]
        start = rng.integers(0, len(take) - size)
        at = int(i * n / count + rng.integers(-size // 8, size // 8)) % n
        index = (at + np.arange(size)) % n
        np.add.at(out, index, take[start:start + size] * window * rng.uniform(0.8, 1.0))
        np.add.at(weight, index, window ** 2)
    return out / np.sqrt(np.maximum(weight, 1e-3))


def main() -> None:
    manifest = {e["id"]: e for e in json.loads(Path(sys.argv[1]).read_text())}
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    note = ("Oikeita renkaita. Yksittäisinä tehosteina (sirkaisu, lyhyt luisto) nämä ovat valmiita; jatkuvaan driftiin äänite taipuu huonosti, "
            "koska sen sävelkorkeus ja karheus eivät seuraa ajoa. Luupit näyttävät, mihin näytteillä päästään.")

    def add(name: str, audio: np.ndarray, title: str, desc: str, sources: list[str], rating: int, loop: bool = False) -> None:
        sfx.save(out / f"{name}.wav", audio)
        entries.append({"id": name, "title_fi": title, "desc_fi": desc, "sources": sources, "rating": rating, "loop": loop, "group_note_fi": note})
        print(name, f"{len(audio) / SR:.2f} s")

    takes = {}
    for number, sid in enumerate(["bsb-2368-screeching-tires-1", "bsb-2369-screeching-tires-2", "bsb-2370-screeching-tires-3", "bsb-2371-screeching-tires-4"], start=1):
        if sid not in manifest:
            continue
        x = trim(sfx.load(manifest[sid]["path"]))
        takes[sid] = x
        seconds = len(x) / SR
        what = "Neljän renkaan vinkuna ulkona, mono 48 kHz / 24 bit." if number < 4 else "Pidempi otos: kaksi lyhyttä sirkaisua ja 1,7 s:n luisto, jossa ääni syttyy kohinapurskeella."
        add(f"tyre-rec-squeal-{number}", sfx.fade(x, 0.003, 0.03), f"Äänite: renkaiden vinkuna {number} ({seconds:.1f} s)".replace(".", ","),
            f"{what} Menetelmä: alkuperäinen, vain hiljaiset päät leikattu.", [sid], 5 if number < 4 else 4)
    steady = []
    for number, sid in enumerate(list(takes)[:3], start=1):
        x = takes[sid]
        body = x[int(0.15 * SR):len(x) - int(0.35 * SR)]
        steady.append(body)
        seconds = min(1.4, len(body) / SR - 0.35)
        loop = sfx.make_loop(body, seconds, 0.25, offset=0.02)
        add(f"tyre-rec-loop-{number}", loop, f"Äänite luuppina: vinkuna {number} ({seconds:.1f} s)".replace(".", ","),
            "Äänitteen tasaisin kohta, 0,25 s:n ristihäivytys. Lyhyt luuppi alkaa toistaa itseään parissa sekunnissa; peli voi peittää sitä vaihtelemalla soittonopeutta ±10 %.",
            [sid], 3, loop=True)
    if len(steady) == 3:
        rng = np.random.default_rng(7)
        add("tyre-rec-granular-loop", granular_loop(steady, 9.0, rng), "Äänitteistä rakeistettu pitkä luuppi (9 s)",
            "Kolmen äänitteen tasaisista kohdista poimittuja 0,16 s:n rakeita päällekkäin ympyräaikajanalla: pitkä luuppi ilman kuultavaa toistoa. "
            "Rakeiden päällekkäisyys tekee äänestä kuoromaisemman kuin yksittäinen otos.", list(takes)[:3], 3, loop=True)
    extra = [
        ("tyre-rec-chirp-koenig", "sb-1178-tires-squealing", None, "Äänite: lyhyt, puhdas vinkaisu (0,9 s)",
         "Ison auton renkaat: hyvin tonaalinen (perusääni 1,0 kHz, kerrannaiset 2,1 ja 3,1 kHz), kaista päättyy 6,3 kHz:iin. Sopii sirkaisuksi sellaisenaan. Menetelmä: alkuperäinen, päät leikattu.", 4, False),
        ("tyre-rec-cornering-koenig", "sb-1044-car-cornering-fast", None, "Äänite: auto kaartaa kovaa (5,5 s)",
         "Vinkuna kuuluu 0,5–1,6 s:n kohdalla moottorin ja tuulen seassa; näyttää, miltä kokonaisuus kuulostaa auton vierestä. Menetelmä: alkuperäinen, päät leikattu.", 3, False),
        ("tyre-rec-loop-haigh", "oga-tom-haigh-tire-squeal-loop", None, "Äänite: jatkuva vinkuna, valmis luuppi (3 s)",
         "Pelikäyttöön leikattu luuppi: kaksi vahvaa kaistaa (0,9 ja 1,8 kHz) ja renkaan pyörimisen tahdissa toistuva naksahdus. Menetelmä: alkuperäinen luuppi, 96 kHz:stä 48 kHz:iin.", 4, True),
    ]
    for name, sid, _, title, desc, rating, loop in extra:
        if sid not in manifest:
            continue
        x = sfx.load(manifest[sid]["path"])
        add(name, x if loop else sfx.fade(trim(x), 0.003, 0.03), title, desc, [sid], rating, loop=loop)
    garage = manifest.get("bsb-0604-underground-parking-squeal")
    if garage:
        x = sfx.load(garage["path"])
        level, tonal = tonal_track(x)
        score = ndimage.uniform_filter1d(np.where(level > np.percentile(level, 70), tonal, 0), 15)
        picked: list[int] = []
        order = np.argsort(score)[::-1]
        for frame in order:
            if all(abs(frame - other) > 250 for other in picked):  # 5 s apart
                picked.append(int(frame))
            if len(picked) == 4:
                break
        parts = []
        for frame in sorted(picked):
            centre = frame * 960 / SR
            a, b = max(0, int((centre - 1.2) * SR)), min(len(x), int((centre + 2.2) * SR))
            parts += [sfx.normalise(sfx.fade(x[a:b], 0.15, 0.5), -3), np.zeros(int(0.3 * SR))]
            print(f"  garage event at {centre:.1f} s, tonal {score[frame]:.1f} dB")
        add("tyre-rec-garage", np.concatenate(parts[:-1]), "Äänite: vinkunaa parkkihallissa (neljä kohtaa)",
            "Sileällä hallin lattialla rengas vinkuu jo kävelyvauhdissa, ja halli kaikuu. Neljä tonaalisinta kohtaa 4 min 47 s:n äänimaisemasta, automaattisesti haettuna. "
            "Menetelmä: leikkaus ja häivytys.", [garage["id"]], 3)
    sfx.write_json(out / "rec.json", entries)


if __name__ == "__main__":
    main()
