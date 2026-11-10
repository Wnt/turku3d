"""Research figures for the soundboard (Finnish captions are in sections.html; panel titles here are Finnish too).

Usage: python -I figures.py
Writes ~/turku3d/loops/sounds-fable/work/figures/fig-*.png (dark theme, same colour scale within a figure).
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import sfx  # noqa: E402

SR = sfx.SR
ROOT = Path.home() / "turku3d/loops/sounds-fable"
WORK = ROOT / "work"
OUT = WORK / "figures"
BG, FG, DIM, LINE = "#191c21", "#e6e8ec", "#9aa1ad", "#30343c"


def style(ax) -> None:
    ax.set_facecolor("#000")
    ax.tick_params(colors=DIM, labelsize=7)
    for spine in ax.spines.values():
        spine.set_color(LINE)
    ax.xaxis.label.set_color(DIM)
    ax.yaxis.label.set_color(DIM)


def spectrogram_panels(name: str, panels: list[tuple[str, np.ndarray]], fmax: float = 9000, nfft: int = 1024, height: float = 1.55) -> None:
    fig, axes = plt.subplots(len(panels), 1, figsize=(9.6, height * len(panels)), dpi=100)
    fig.patch.set_facecolor(BG)
    for ax, (title, x) in zip(np.atleast_1d(axes), panels):
        mono = x if x.ndim == 1 else x.mean(axis=1)
        hz, t, s = signal.spectrogram(mono, fs=SR, nperseg=nfft, noverlap=nfft - nfft // 8, window="hann")
        level = 10 * np.log10(s + 1e-14)
        level -= np.percentile(level, 99.9)
        ax.pcolormesh(t, hz / 1000, level, vmin=-65, vmax=0, cmap="magma", shading="auto", rasterized=True)
        ax.set_ylim(0, fmax / 1000)
        ax.set_ylabel("kHz", fontsize=7)
        ax.set_title(title, fontsize=8.5, loc="left", color=FG, pad=3)
        style(ax)
    np.atleast_1d(axes)[-1].set_xlabel("s", fontsize=7)
    fig.tight_layout(pad=0.5)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, facecolor=BG)
    plt.close(fig)


def clip(path: Path, start: float, seconds: float) -> np.ndarray:
    x = sfx.load(path)
    return x[int(start * SR):int((start + seconds) * SR)]


def gulls() -> None:
    cand = WORK / "gulls-cand"
    spectrogram_panels("fig-gulls.png", [
        ("Pelin nykyinen (gulls.ogg): kaksi liukuvaa siniääntä, sama huuto 4 s välein", clip(WORK / "current/gulls.wav", 0, 7.9)),
        ("Oikea harmaalokin naurusarja (xeno-canto XC1178162, CC0): kerrannaispinot, jokainen ääni laskee", clip(cand / "gull-rec-herring-longcall-a.wav", 3.2, 7.9)),
        ("Uudelleensynteesi samasta huudosta mitatuilla parametreilla", clip(cand / "gull-resyn-herring-longcall.wav", 3.2, 7.9)),
        ("Puhdas synteesi ilman mittauksia: muoto oikea, mutta korkeus ja rytmi arvattu väärin", clip(WORK / "gulls-syn/gull-syn-herring-longcall.wav", 0, 7.9)),
    ])


def tyres() -> None:
    tyre = WORK / "tyre"
    src = ROOT / "sources/tyres"
    real = sfx.load(next((src / "bsb-2369-screeching-tires-2").glob("*.wav")))
    spectrogram_panels("fig-tyres.png", [
        ("Pelin nykyinen (screech.ogg): yksi 1,4 kHz:n viiva ja tasainen kohina 12 kHz:iin", clip(WORK / "current/screech.wav", 0, 2.2)),
        ("Oikea vinkuna (BigSoundBank 2369, CC0): osaääniryhmä 1 kHz:ssä, kerrannaiset 2 ja 3 kHz:ssä, sävel laskee auton hidastuessa", real[: int(2.2 * SR)]),
        ("Uusi synteesi, lukkojarrutus 60 → 0 km/h (stick-slip-malli)", clip(tyre / "skid-stop.wav", 0.25, 2.2)),
    ], fmax=8000, height=1.5)
    # Long-term spectra, each normalised to its own peak.
    fig, ax = plt.subplots(figsize=(9.6, 2.9), dpi=100)
    fig.patch.set_facecolor(BG)
    for label, x, colour, width in [
        ("pelin nykyinen (screech.ogg)", clip(WORK / "current/screech.wav", 0.3, 3.2), "#6fb3ff", 1.4),
        ("oikea vinkuna (kolmen äänitteen keskiarvo)", None, "#ffb454", 2.0),
        ("uusi synteesi (tasainen luisto 4,2 m/s)", clip(tyre / "drift-mid-long.wav", 2, 6), "#7fd18b", 1.4),
    ]:
        if x is None:
            spectra = []
            for sid in ("bsb-2368-screeching-tires-1", "bsb-2369-screeching-tires-2", "bsb-2370-screeching-tires-3"):
                take = sfx.load(next((src / sid).glob("*.wav")))
                hz, power = signal.welch(take[int(0.2 * SR):int(1.8 * SR)], fs=SR, nperseg=4096)
                spectra.append(power / power.max())
            power = np.mean(spectra, axis=0)
        else:
            hz, power = signal.welch(x, fs=SR, nperseg=4096)
        level = 10 * np.log10(power / power.max() + 1e-12)
        ax.plot(hz, level, color=colour, linewidth=width, label=label)
    ax.set_xscale("log")
    ax.set_xlim(200, 16000)
    ax.set_ylim(-70, 3)
    ax.set_xticks([200, 500, 1000, 2000, 4000, 8000, 16000])
    ax.set_xticklabels(["200", "500", "1 k", "2 k", "4 k", "8 k", "16 k"])
    ax.set_xlabel("Hz", fontsize=7)
    ax.set_ylabel("dB (huippu = 0)", fontsize=7)
    ax.grid(color=LINE, linewidth=0.5)
    ax.legend(fontsize=7.5, facecolor=BG, edgecolor=LINE, labelcolor=FG, loc="lower left")
    ax.set_title("Pitkän ajan spektri: nykyinen on viiva kohinan päällä, oikea ja uusi synteesi ovat leveä ryhmä kerrannaisineen", fontsize=8.5, loc="left", color=FG)
    style(ax)
    ax.set_facecolor(BG)
    fig.tight_layout(pad=0.5)
    fig.savefig(OUT / "fig-tyres-spectrum.png", facecolor=BG)
    plt.close(fig)


def music() -> None:
    panels = [("Pelin nykyinen (Aura Neon, vapaa ajo): yksittäisiä ääniä puolen sekunnin välein, välissä tyhjää", clip(WORK / "music-current/music-current-explore.wav", 4, 16))]
    own = WORK / "music-own/music-own-aura-neon.wav"
    if own.exists():
        panels.append(("Oma moottori (Aura Neon): rummut, basso, soinnut, arpeggio ja melodia kaikuineen", clip(own, 62, 16)))
    gen = ROOT / "gen/out/synthwave-s1.wav"
    if gen.exists():
        panels.append(("Generoitu (ACE-Step 1.5, synthwave): täysi tuotanto", clip(gen, 20, 16)))
    spectrogram_panels("fig-music.png", panels, fmax=12000, nfft=2048, height=1.5)


if __name__ == "__main__":
    gulls()
    tyres()
    music()
    print(sorted(p.name for p in OUT.glob("fig-*.png")))
