"""Band levels (dB re the strongest band) and envelope statistics of tyre sounds, to compare the synthesiser with
recordings. Usage: python -I bands.py FILE [START END] ..."""
import sys
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sfx  # noqa: E402

BANDS = [(300, 700), (850, 1300), (1800, 2500), (2800, 3700), (3800, 4900), (5000, 7000), (7000, 10000), (10000, 16000)]


def measure(x: np.ndarray) -> dict:
    hz, power = signal.welch(x, fs=sfx.SR, nperseg=4096)
    levels = np.array([10 * np.log10(np.sum(power[(hz >= a) & (hz < b)]) + 1e-20) for a, b in BANDS])
    # Tonality in the fundamental band: peak-to-median of the fine spectrum.
    hz2, p2 = signal.welch(x, fs=sfx.SR, nperseg=16384)
    band = (hz2 > 800) & (hz2 < 1400)
    tonal = 10 * np.log10(p2[band].max() / np.median(p2[band]))
    envelope = np.abs(signal.hilbert(signal.sosfilt(signal.butter(4, [800, 2600], "bandpass", fs=sfx.SR, output="sos"), x)))
    envelope = signal.sosfilt(signal.butter(2, 120, "lowpass", fs=sfx.SR, output="sos"), envelope)[2400:]
    return {"bands": np.round(levels - levels.max(), 1), "tonal_db": round(float(tonal), 1),
            "env_cv": round(float(np.std(envelope) / (np.mean(envelope) + 1e-12)), 2), "peak_hz": round(float(hz2[band][np.argmax(p2[band])]))}


if __name__ == "__main__":
    print("bands:", " ".join(f"{a/1000:g}-{b/1000:g}k" for a, b in BANDS))
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        path = args[i]
        x = sfx.load(path)
        if i + 2 < len(args) + 0 and i + 2 <= len(args) - 0 and len(args) > i + 2 and args[i + 1].replace(".", "").isdigit():
            x = x[int(float(args[i + 1]) * sfx.SR):int(float(args[i + 2]) * sfx.SR)]
            i += 2
        m = measure(x)
        print(f"{Path(path).name[:28]:<28}", " ".join(f"{v:6.1f}" for v in m["bands"]), f" tonal {m['tonal_db']:5.1f} dB  env cv {m['env_cv']:.2f}  peak {m['peak_hz']} Hz")
        i += 1
