"""Transcodes the six chosen ACE-Step tracks for the game: the same loudness as on the soundboard (-18 dB RMS, peak
at most -1 dBFS), the silent tail after the song's ending trimmed, Ogg/Opus 96 kbit/s stereo, and manifest rows.

Usage: ~/turku3d/loops/sounds-fable/venv/bin/python -I prepare_music.py
Reads ~/turku3d/loops/sounds-fable/gen/out/<slug>-s1.wav; writes game/public/assets/audio/music/<slug>.ogg.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import sfx  # noqa: E402

SR = sfx.SR
GEN = Path.home() / "turku3d/loops/sounds-fable/gen/out"
AUDIO = HERE.parents[2] / "game/public/assets/audio"
SLUGS = ["menu", "synthwave", "lofi", "funk", "dubtechno", "dnb"]

manifest_path = AUDIO / "manifest.json"
manifest = json.loads(manifest_path.read_text())
(AUDIO / "music").mkdir(exist_ok=True)
for slug in SLUGS:
    x = sfx.load(GEN / f"{slug}-s1.wav", mono=False)
    x = sfx.normalise(x, -1.0, -18.0)
    # The model writes a song with an ending and pads the rest of the two minutes with silence: keep 0.4 s of it.
    level = np.abs(x).max(axis=1)
    loud = np.flatnonzero(level > 10 ** (-50 / 20))
    end = min(len(x), int(loud[-1] + 0.4 * SR))
    x = sfx.fade(x[:end], 0.0, 0.15)
    temp = AUDIO / "music" / f"{slug}.tmp.wav"
    sfx.save(temp, x)
    out = AUDIO / "music" / f"{slug}.ogg"
    sfx.encode(temp, out, "96k")
    temp.unlink()
    data = out.read_bytes()
    seconds = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    manifest[f"music/{slug}.ogg"] = {"bytes": len(data), "channels": 2, "seconds": round(seconds, 3), "sha256": hashlib.sha256(data).hexdigest(), "license": "CC0-1.0"}
    print(f"{slug:<10} {seconds:7.2f} s (trimmed {120 - seconds:4.1f} s)  {len(data):>8} bytes  rms {sfx.db(sfx.rms(x)):.1f} dB  peak {sfx.db(float(np.abs(x).max())):.1f} dB")
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
print("total", sum(v["bytes"] for k, v in manifest.items() if k.startswith("music/")), "bytes")
