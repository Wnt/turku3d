"""The trophy truck's current runtime squeal (game/src/audio/engine/chassis.ts, `screech` layer at full level),
rendered offline for A/B: a 1050 Hz sine with a 6.5 Hz, ±40 Hz vibrato plus noise through a 1050 Hz band-pass (Q 6).
Usage: python -I current_truck.py OUT.wav"""
import sys
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sfx  # noqa: E402

SR = sfx.SR
n = 6 * SR
t = np.arange(n) / SR
rng = np.random.default_rng(1)
phase = 2 * np.pi * np.cumsum(1050 + 40 * np.sin(2 * np.pi * 6.5 * t)) / SR
w0 = 2 * np.pi * 1050 / SR
alpha = np.sin(w0) / (2 * 6)
b = np.array([alpha, 0, -alpha]) / (1 + alpha)
a = np.array([1, -2 * np.cos(w0) / (1 + alpha), (1 - alpha) / (1 + alpha)])
noise = signal.lfilter(b, a, rng.uniform(-1, 1, n))
sfx.save(sys.argv[1], 0.07 * (0.5 * np.sin(phase) + 4 * noise))
