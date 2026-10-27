"""Ogg Opus encoding through ffmpeg (same codec settings family as game/scripts/prepare-audio.mjs)."""

import subprocess

import numpy as np


def encode_opus(samples, rate, path, bitrate_kbps):
    """Encode mono float samples; bitexact flags keep the Ogg serial and headers reproducible."""
    pcm = np.clip(np.asarray(samples, dtype="<f4"), -1.0, 1.0).tobytes()
    codec = f"-c:a libopus -b:a {bitrate_kbps}k -application audio -map_metadata -1"
    exact = "-fflags +bitexact -flags:a +bitexact"
    command = [
        *f"ffmpeg -v error -y -f f32le -ar {rate} -ac 1 -i pipe:0 {codec} {exact}".split(),
        str(path),
    ]
    result = subprocess.run(command, input=pcm, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"ffmpeg failed for {path}: {result.stderr.decode(errors='replace')}")
