"""Replaceable register-conditioned wall distributions; no building-specific measurements."""

import hashlib
import json
import math

import numpy as np


def lab(rgb):
    rgb = np.asarray(rgb, dtype=float) / 255
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = (
        linear
        @ np.array(
            [
                [0.4124564, 0.3575761, 0.1804375],
                [0.2126729, 0.7151522, 0.0721750],
                [0.0193339, 0.1191920, 0.9503041],
            ]
        ).T
        / [0.95047, 1, 1.08883]
    )
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack(
        (116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])),
        axis=-1,
    )


def rgb(value):
    value = np.asarray(value, dtype=float)
    y = (value[..., 0] + 16) / 116
    f = np.stack((y + value[..., 1] / 500, y, y - value[..., 2] / 200), axis=-1)
    xyz = np.where(f > 6 / 29, f**3, 3 * (6 / 29) ** 2 * (f - 4 / 29)) * [0.95047, 1, 1.08883]
    linear = (
        xyz
        @ np.linalg.inv(
            np.array(
                [
                    [0.4124564, 0.3575761, 0.1804375],
                    [0.2126729, 0.7151522, 0.0721750],
                    [0.0193339, 0.1191920, 0.9503041],
                ]
            )
        ).T
    )
    return np.clip(
        np.where(
            linear <= 0.0031308, linear * 12.92, 1.055 * np.maximum(linear, 0) ** (1 / 2.4) - 0.055
        )
        * 255,
        0,
        255,
    )


def group_keys(attributes):
    material = attributes.get("material_class") or "unknown"
    era = attributes.get("era") or "unknown"
    district = attributes.get("district") or "unknown"
    return [
        json.dumps(parts, separators=(",", ":"))
        for parts in [(material, era, district), (material, era), (material,), ()]
    ]


def sample(building_id, attributes, model):
    """Stable identity controls the draw, independently of facade index or tile order."""
    group = next(
        (model["groups"][key] for key in group_keys(attributes) if key in model["groups"]), None
    )
    if group is None:
        return None
    digest = hashlib.sha256(str(building_id).encode()).digest()
    uniform = [(int.from_bytes(digest[i : i + 8], "big") + 0.5) / 2**64 for i in range(0, 32, 8)]
    # Box-Muller avoids relying on a NumPy random-distribution implementation/version.
    normal = np.array(
        [
            math.sqrt(-2 * math.log(uniform[0])) * math.cos(2 * math.pi * uniform[1]),
            math.sqrt(-2 * math.log(uniform[0])) * math.sin(2 * math.pi * uniform[1]),
            math.sqrt(-2 * math.log(uniform[2])) * math.cos(2 * math.pi * uniform[3]),
        ]
    )
    covariance = np.asarray(group["covariance_lab"])
    # Shrink sparse observational variation: unknown lighting contributes to its spread.
    value = np.asarray(group["mean_lab"]) + model["spread_scale"] * (
        np.linalg.cholesky(covariance + np.eye(3) * 1e-6) @ normal
    )
    value = np.clip(value, group["lower_lab"], group["upper_lab"])
    return "#" + "".join(f"{int(round(v)):02x}" for v in rgb(value))
