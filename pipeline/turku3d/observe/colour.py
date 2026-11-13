"""colour: the wall colour of a building from its rectified wall strips, without a model call.

A strip is a panorama sampled on a wall's own plane (crops.py). Most of a facade above the ground storey is plain
wall: the colour most of the strip's pixels share is the wall's, once leaves, glass and missing picture are left
out. A photograph's pixels carry its exposure and the colour of its light, so the sample is a measurement of hue
and chroma first; `calibrated` maps it onto the scale the readers' paint colours have (fitted on the buildings
both read and sampled, by `python -m turku3d.observe colour --check`).

    python -m turku3d.observe colour --check     sampled against read colours on the packets with crops: the fit
                                                 for CALIBRATION and the held-out error, rows in against/colour-check.json
    python -m turku3d.observe colour (--at E,N --radius R | --view ID... | --building KEY... --near E,N) [--no-store]
                                                 one JSON line per building: its `measured` block; stored for
                                                 buildings nobody described (tier auto, measured.wall)
    python -m turku3d.observe colour --apply FILE   store the lines another machine measured with --no-store

Measuring fetches panoramas: run it where they may stay (scripts/dev/on-host air), with --no-store, and apply the
lines here.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from .against import delta_e, lab
from .crops import RULER_PX

STEP = 4  # every fourth pixel of a crop each way: about 10 px per metre of wall is plenty for a colour
CLUSTER_DE = 9.0  # CIELAB distance within which pixels are the same paint in the same light
LEAF = (95.0, 165.0, 20.0)  # hue range and least chroma of foliage in front of a wall
MIN_PIXELS = 400
DARK = 24.0  # CIELAB lightness under which a pixel is glass, deep shade or dark foliage, unless the wall itself is dark
# Paint colour (as readers give it, overcast daylight) from the pixel colour: gain and offset per CIELAB axis,
# fitted by `colour --check` on the buildings both read and sampled. The offsets take out the cameras' exposure
# and the blue of shade.
CALIBRATION = {"L": (0.484, 42.744), "a": (1.512, 2.154), "b": (1.516, 6.598)}  # 59 buildings, 2026-11-13
AXES = ("L", "a", "b")


def to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = c @ np.array([[0.4124, 0.2126, 0.0193], [0.3576, 0.7152, 0.1192], [0.1805, 0.0722, 0.9505]])
    f = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(f > 0.008856, np.cbrt(f), 7.787 * f + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def to_hex(colour: np.ndarray) -> str:
    """sRGB hex of one CIELAB colour."""
    fy = (colour[0] + 16) / 116
    f = np.array([fy + colour[1] / 500, fy, fy - colour[2] / 200])
    xyz = np.where(f > 0.206897, f ** 3, (f - 16 / 116) / 7.787) * np.array([0.95047, 1.0, 1.08883])
    rgb = xyz @ np.array([[3.2406, -0.9689, 0.0557], [-1.5372, 1.8758, -0.2040], [-0.4986, 0.0415, 1.0570]])
    rgb = np.clip(rgb, 0, 1)
    rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)
    return "#" + "".join(f"{round(float(v) * 255):02x}" for v in rgb)


def dominant(rgb: np.ndarray) -> dict | None:
    """The colour most pixels of a wall strip share: CIELAB `lab`, `share` of the strip, `spread` (mean distance
    of its pixels from it). None when too little of the strip is wall."""
    pixels = to_lab(rgb.reshape(-1, 3))
    total = len(pixels)
    chroma = np.hypot(pixels[:, 1], pixels[:, 2])
    hue = np.degrees(np.arctan2(pixels[:, 2], pixels[:, 1])) % 360
    keep = (rgb.reshape(-1, 3).max(1) >= 12) & ~((hue > LEAF[0]) & (hue < LEAF[1]) & (chroma > LEAF[2]))
    light = keep & (pixels[:, 0] >= DARK)
    pixels = pixels[light if light.sum() >= 0.25 * keep.sum() else keep]
    if len(pixels) < MIN_PIXELS:
        return None
    cells = np.floor(pixels / np.array([5.0, 4.0, 4.0])).astype(np.int64)
    low = cells.min(0)
    shape = cells.max(0) - low + 3
    grid = np.zeros(shape)
    np.add.at(grid, tuple((cells - low + 1).T), 1)
    smooth = sum(np.roll(grid, shift, axis) for axis in range(3) for shift in (-1, 1)) + 2 * grid
    peak = (np.array(np.unravel_index(smooth.argmax(), shape)) + low - 1 + 0.5) * np.array([5.0, 4.0, 4.0])
    centre = peak
    for _ in range(3):  # the peak cell's neighbourhood, re-centred on its own median
        near = np.linalg.norm(pixels - centre, axis=1) <= CLUSTER_DE
        if near.sum() < MIN_PIXELS / 4:
            return None
        centre = np.median(pixels[near], axis=0)
    return {"lab": centre, "share": float(near.sum() / total),
            "spread": float(np.linalg.norm(pixels[near] - centre, axis=1).mean())}


def strip_of_crop(path: Path, crop: dict, wall: dict) -> np.ndarray | None:
    """The plain-wall part of a stored crop: its seen stretch, above the ground storey, under the eaves."""
    image = np.asarray(Image.open(path).convert("RGB"))
    picture = image[: image.shape[0] - RULER_PX, RULER_PX:]
    ppm = crop["px_per_m"]
    height = wall["eaves_h"] - wall["base_h"]
    z_low, z_high = min(2.8, 0.35 * height), height - 0.5
    if z_high - z_low < 0.6:
        return None
    rows = slice(max(0, round((crop["z_high_m"] - z_high) * ppm)), max(0, round((crop["z_high_m"] - z_low) * ppm)))
    columns = slice(max(0, round((crop["x0_m"] + 0.3 - crop["x_left_m"]) * ppm)),
                    max(0, round((crop["x1_m"] - 0.3 - crop["x_left_m"]) * ppm)))
    strip = picture[rows, columns][::STEP, ::STEP]
    return strip if strip.size >= 3 * MIN_PIXELS else None


def combined(samples: list[dict]) -> dict | None:
    """One colour from the samples of several strips (walls, panoramas): the weighted median in CIELAB, the
    weight being the wall area the sample stands for; `agreement` is how far the strips are from it."""
    samples = [s for s in samples if s]
    if not samples:
        return None
    weights = np.array([s["share"] * s.get("area_m2", 1.0) for s in samples])
    labs = np.array([s["lab"] for s in samples])
    order = [np.argsort(labs[:, k]) for k in range(3)]
    centre = np.array([labs[o, k][np.searchsorted(np.cumsum(weights[o]), weights.sum() / 2)] for k, o in enumerate(order)])
    distance = np.linalg.norm(labs - centre, axis=1)
    agreement = float((distance * weights).sum() / weights.sum())
    share = float((np.array([s["share"] for s in samples]) * weights).sum() / weights.sum())
    spread = float((np.array([s["spread"] for s in samples]) * weights).sum() / weights.sum())
    conf = 0.25 + 0.5 * min(share, 0.6) / 0.6 + (0.15 if len(samples) > 1 and agreement < 6 else 0.0) - (0.2 if agreement > 12 else 0.0)
    return {"lab": centre, "share": round(share, 2), "spread": round(spread, 1), "agreement": round(agreement, 1),
            "strips": len(samples), "conf": round(float(np.clip(conf, 0.1, 0.9)), 2)}


def calibrated(colour: np.ndarray, calibration: dict | None = None) -> np.ndarray:
    """The paint colour for a pixel colour, on the readers' scale."""
    calibration = calibration or CALIBRATION
    out = np.array([calibration[axis][0] * colour[k] + calibration[axis][1] for k, axis in enumerate(AXES)])
    out[0] = float(np.clip(out[0], 5, 97))
    return out


def of_packet(packet: Path) -> dict | None:
    """The wall colour of a prepared building (views.json and its crops): combined over its strips."""
    record = json.loads((packet / "views.json").read_text())
    walls = {w["id"]: w for w in record["walls"]}
    samples = []
    for crop in record["crops"]:
        path = packet / Path(crop["file"]).name
        wall = walls.get(crop["wall"])
        if not path.exists() or wall is None:
            continue
        strip = strip_of_crop(path, crop, wall)
        sample = dominant(strip) if strip is not None else None
        if sample:
            sample.update(wall=crop["wall"], area_m2=(crop["x1_m"] - crop["x0_m"]) * (wall["eaves_h"] - wall["base_h"]),
                          provider=crop["panorama"]["provider"], date=crop["panorama"]["date"])
            samples.append(sample)
    return combined(samples)


def measured(sample: dict) -> dict:
    """The `measured.wall` block of a stored description (docs/specs/building-description.md)."""
    return {"colour": to_hex(calibrated(sample["lab"])), "spread": sample["spread"], "share": sample["share"],
            "conf": {"*": sample["conf"]}}


def check(root: Path, store_colour) -> dict:
    """Sampled against read wall colours over every packet that has crops and a read description.
    `store_colour(key)` gives the read wall colour of a building's most visible wall, or None."""
    rows = []
    for packet in sorted((root / "buildings").iterdir()):
        read = store_colour(packet.name) if (packet / "views.json").exists() else None
        sample = of_packet(packet) if read else None
        if sample:
            rows.append({"key": packet.name, "read": read, "sample": sample})
    if len(rows) < 8:
        return {"buildings": len(rows)}
    read_lab = np.array([lab(r["read"]) for r in rows])
    pixel = np.array([r["sample"]["lab"] for r in rows])
    chroma = lambda a: np.hypot(a[:, 1], a[:, 2])  # noqa: E731
    fitted = lambda rows_: {axis: np.polyfit(pixel[rows_, k], read_lab[rows_, k], 1) for k, axis in enumerate(AXES)}  # noqa: E731
    fit = {axis: [round(float(v), 3) for v in line] for axis, line in fitted(np.ones(len(rows), bool)).items()}

    def stats(colours: list[str]) -> dict:
        d = sorted(delta_e(c, r["read"]) for c, r in zip(colours, rows, strict=True))
        return {"median": round(statistics.median(d), 1), "mean": round(statistics.fmean(d), 1),
                "upper_quartile": round(d[int(len(d) * 0.75)], 1), "within_10": round(sum(v <= 10 for v in d) / len(d), 2)}

    # Leave-one-out: each building's colour calibrated by the fit of the others, so the figure is not flattered.
    held_out = []
    for k in range(len(rows)):
        held_out.append(to_hex(calibrated(pixel[k], fitted(np.arange(len(rows)) != k))))
    hue = lambda a: np.degrees(np.arctan2(a[:, 2], a[:, 1]))  # noqa: E731
    coloured = (chroma(read_lab) > 10) & (chroma(pixel) > 5)
    turn = np.abs((hue(pixel) - hue(read_lab) + 180) % 360 - 180)[coloured]
    sure = [k for k, r in enumerate(rows) if r["sample"]["conf"] >= 0.6]
    return {"buildings": len(rows), "fit": fit,
            "raw_pixels": stats([to_hex(p) for p in pixel]),
            "calibrated_held_out": stats(held_out),
            "calibrated_held_out_conf_0.6": {"n": len(sure), **stats_of([delta_e(held_out[k], rows[k]["read"]) for k in sure])},
            "lightness_read_minus_pixel": round(float(np.median(read_lab[:, 0] - pixel[:, 0])), 1),
            "chroma_read_minus_pixel": round(float(np.median(chroma(read_lab) - chroma(pixel))), 1),
            "hue_turn_deg_median": round(float(np.median(turn)), 1) if coloured.any() else None,
            "rows": [{"key": r["key"], "read": r["read"], "pixel": to_hex(r["sample"]["lab"]), "calibrated": held_out[k],
                      "dE": round(delta_e(held_out[k], r["read"]), 1), **{f: r["sample"][f] for f in ("share", "spread", "agreement", "strips", "conf")}}
                     for k, r in enumerate(rows)]}


def stats_of(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {}
    return {"median": round(statistics.median(values), 1), "mean": round(statistics.fmean(values), 1),
            "within_10": round(sum(v <= 10 for v in values) / len(values), 2)}


BY = "w2-observe colour sampler 1"


def description(key: str, block: dict, panoramas: list) -> dict:
    """The stored description of a building only measured: tier auto, `measured` alone."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"schema": "turku-description/building/1", "id": f"building/{key}", "tier": "auto",
            "provenance": [{"source": "panorama", "key": f"colour-{now}", "at": now, "by": BY,
                            "note": "wall colour sampled from rectified strips of " + ", ".join(panoramas[:6])}],
            "measured": {"wall": block}}


def only_measured(held: dict | None) -> bool:
    """Whether a stored description is nothing but colour samples (or is not there at all): nobody has read it."""
    return held is None or (held.get("tier") == "auto" and not set(held) - {"schema", "id", "tier", "provenance", "measured"})


def apply(lines: list[dict]) -> dict:
    """Stores measured blocks for the buildings that have no stored description (a described material always wins,
    and the store never lowers a tier). A block the description schema refuses is not stored."""
    from turku3d.world import store
    from turku3d.world.kinds.building import description as building_description

    stored = skipped = refused = 0
    for line in lines:
        if not line.get("measured"):
            continue
        if building_description.errors(description(line["building"], line["measured"], line.get("panoramas", []))):
            refused += 1
            continue
        if not only_measured(store.read_description(f"building/{line['building']}")):
            skipped += 1
            continue
        store.write_description(description(line["building"], line["measured"], line.get("panoramas", [])), BY, "wall colour sample")
        stored += 1
    return {"stored": stored, "described_already": skipped, "refused": refused}


def measure(scene, building, panos, root: Path) -> dict:
    """One building: its strips (a packet with crops when there is one, else prepared now and its crops removed
    again), sampled and calibrated."""
    from . import prepare as prepare_module

    packet = root / "buildings" / building.id
    temporary = None
    if not (packet / "views.json").exists() or not any(packet.glob("w*.jpg")):
        temporary = packet = root / "colour" / building.id
        record = prepare_module.prepare(scene, building, panos, packet, per_wall=2)
        packet.mkdir(parents=True, exist_ok=True)
        (packet / "views.json").write_text(json.dumps(record))
    record = json.loads((packet / "views.json").read_text())
    sample = of_packet(packet)
    if temporary is not None:
        for crop in temporary.glob("*.jpg"):
            crop.unlink()
    line = {"building": building.id, "address": building.register.get("address"), "strips": len(record["crops"]),
            "panoramas": sorted({f"{c['panorama']['provider']}:{c['panorama']['id']}" for c in record["crops"]})}
    if sample:
        line.update(measured=measured(sample), pixel=to_hex(sample["lab"]), agreement=sample["agreement"])
    return line
