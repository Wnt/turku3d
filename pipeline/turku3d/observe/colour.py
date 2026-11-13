"""Wall colours measured from the photographs, not estimated by the reader.

The reader places a wall's storeys, zones and openings; the generator's own layout of that description, moved back
onto the crop the wall was measured on, says which pixels show plain wall of each named material. Their median
colour is the measurement. The reader's own colour stays as the cross-check: it is what tells wall from the trees,
cars and shadows in front of it, and it is kept wherever too little plain wall is in sight.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from . import elevation, reader
from .crops import RULER_PX

MIN_AREA_M2 = 3.0  # plain wall in sight below which the reader's colour stands
NEAR_READER = 25.0  # CIELAB distance within which a pixel counts as this material rather than something before it
MAX_HIDDEN = 0.5  # share of a crop hidden by foreground objects above which it is not measured
STREET_M = 2.6  # height over the wall's foot below which vehicles and people stand before it


def lab(rgb) -> np.ndarray:
    """CIELAB (D65) of sRGB values in 0..255; the last axis holds the three channels."""
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    f = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(f > 0.008856, np.cbrt(f), 7.787 * f + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def to_hex(value: np.ndarray) -> str:
    """sRGB hex of a CIELAB colour."""
    fy = (value[0] + 16) / 116
    f = np.array([fy + value[1] / 500, fy, fy - value[2] / 200])
    xyz = np.where(f > 0.206893, f ** 3, (f - 16 / 116) / 7.787) * np.array([0.95047, 1.0, 1.08883])
    c = xyz @ np.array([[3.2406, -1.5372, -0.4986], [-0.9689, 1.8758, 0.0415], [0.0557, -0.2040, 1.0570]]).T
    c = np.clip(c, 0, 1)
    c = np.where(c > 0.0031308, 1.055 * c ** (1 / 2.4) - 0.055, 12.92 * c)
    return "#" + "".join(f"{int(round(x * 255)):02x}" for x in c)


def from_hex(colour: str) -> np.ndarray:
    return lab([int(colour[i:i + 2], 16) for i in (1, 3, 5)])


def measure(record: dict, reading: dict, doc: dict, ground=None) -> dict:
    """{material: {"colour", "reader", "dE", "area_m2"}} for the wall materials with enough plain wall in sight."""
    local = elevation.localised(doc)
    seen = {v["file"]: v for v in reading.get("views", [])}
    pixels: dict[str, list[np.ndarray]] = {}
    area: dict[str, float] = {}
    for wall in reading["description"].get("walls", []):
        crop = next((c for c in record["crops"] if Path(c["file"]).name == wall.get("measured_on")), None)
        if crop is None or seen.get(Path(crop["file"]).name, {}).get("hidden_share", 0.0) > MAX_HIDDEN:
            continue
        registration = reader.wall_registration(record, reading, wall["id"], ground)
        index, names = elevation.material_areas(local, wall["id"], crop, registration)
        if not names:
            continue
        photo = np.asarray(Image.open(crop["file"]).convert("RGB"))[:index.shape[0], RULER_PX:RULER_PX + index.shape[1]]
        street = int(round((crop["z_high_m"] - registration[2] - STREET_M) * crop["px_per_m"]))
        for i, name in enumerate(names, 1):
            mask = index == i
            above = mask.copy()
            above[max(0, street):] = False
            if above.sum() >= 0.3 * mask.sum():  # a ground-floor material is measured where it is
                mask = above
            if mask.any():
                pixels.setdefault(name, []).append(lab(photo[mask][:: max(1, int(mask.sum()) // 20000)]))
                area[name] = area.get(name, 0.0) + float(mask.sum()) / crop["px_per_m"] ** 2
    out = {}
    for name, parts in pixels.items():
        read = doc.get("materials", {}).get(name, {}).get("colour")
        if not read or area[name] < MIN_AREA_M2:
            continue
        values, expected = np.concatenate(parts), from_hex(read)
        near = values[np.linalg.norm(values - expected, axis=1) < NEAR_READER]
        if len(near) < 0.3 * len(values):  # most of it is not what the reader saw: do not choose between them
            continue
        measured = np.median(near, axis=0)
        out[name] = {"colour": to_hex(measured), "reader": read, "area_m2": round(area[name], 1),
                     "dE": round(float(np.linalg.norm(measured - expected)), 1)}
    return out


def apply(doc: dict, measured: dict) -> dict:
    """Put measured colours into the description's materials; the provenance says which, and what the reader said."""
    if not measured:
        return doc
    for name, m in measured.items():
        doc["materials"][name]["colour"] = m["colour"]
    note = "; ".join(f"{name} {m['colour']} (reader {m['reader']})" for name, m in sorted(measured.items()))
    provenance = doc.get("provenance")
    if isinstance(provenance, dict):
        provenance["note"] = (provenance.get("note", "") + "; colours measured from pixels: " + note).lstrip("; ")
    return doc
