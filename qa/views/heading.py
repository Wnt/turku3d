"""Solve a panorama's grid heading offset from independently located landmarks."""

import math

import numpy as np
from qalib.geo import bearing


def solve_heading(e, n, landmarks):
    if len(landmarks) < 2:
        raise ValueError("at least two landmarks required")
    offsets = np.array(
        [(bearing(e, n, p["E"], p["N"]) - p["tour_yaw"] + 180) % 360 - 180 for p in landmarks]
    )
    angle = math.degrees(
        math.atan2(np.sin(np.radians(offsets)).mean(), np.cos(np.radians(offsets)).mean())
    )
    residuals = (offsets - angle + 180) % 360 - 180
    return {
        "heading_offset_grid_deg": angle,
        "landmark_offsets_deg": offsets.tolist(),
        "rms_deg": float(np.sqrt(np.mean(residuals**2))),
        "landmarks": landmarks,
    }
