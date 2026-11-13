"""The colour of each distinguishable part of a house, from rectified wall crops and their label maps.

Geometry says where a part can be (the wall's own plane from LoD2: its seen stretch, its foot, its eaves and gable),
the label map of `colour_segment.py` says what is in front (vegetation, vehicles, fences, people) and where windows
and doors are, and colour clustering in CIELAB inside what is left says which paints there are: the cladding (one
zone or two), trim (a thin second colour), window frames (the ring round the glass), the plinth (the band at the
foot that is not the cladding's colour), the roof (above the eaves of an eaves-side wall) and the door.

Colours here are pixel colours (CIELAB of the photograph); `colour.calibrated` brings them to the paint scale.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import colour
from .colour_segment import LABELS

PARTS = ("wall", "wall2", "trim", "frame", "plinth", "roof", "door")
L = {name: k for k, name in enumerate(LABELS)}
HOUSE = (L["building"], L["window"], L["door"], L["attached"], L["roof"])
IN_FRONT = ("vegetation", "vehicle", "person", "fence", "pole")
CLUSTER_DE = 9.0
MIN_PART_PX = 250
MIN_VISIBLE = 0.2  # a crop showing less of its wall than this measures nothing
PLINTH_MAX_M = 1.6
TAG = "m2f"


def mode(pixels: np.ndarray, radius: float = CLUSTER_DE) -> tuple[np.ndarray, np.ndarray] | None:
    """The colour most pixels share and which pixels are within `radius` of it (CIELAB rows)."""
    if len(pixels) < MIN_PART_PX:
        return None
    cell = np.array([5.0, 4.0, 4.0])
    cells = np.floor(pixels / cell).astype(np.int64)
    low = cells.min(0)
    shape = cells.max(0) - low + 3
    grid = np.zeros(shape)
    np.add.at(grid, tuple((cells - low + 1).T), 1)
    smooth = sum(np.roll(grid, shift, axis) for axis in range(3) for shift in (-1, 1)) + 2 * grid
    centre = (np.array(np.unravel_index(smooth.argmax(), shape)) + low - 1 + 0.5) * cell
    near = np.zeros(len(pixels), bool)
    for _ in range(3):
        near = np.linalg.norm(pixels - centre, axis=1) <= radius
        if near.sum() < MIN_PART_PX / 4:
            return None
        centre = np.median(pixels[near], axis=0)
    return centre, near


def _grow(mask: np.ndarray, px: int) -> np.ndarray:
    """The mask grown by `px` pixels (a square, by shifted ORs: no scipy on the hosts' pipeline environment)."""
    out = mask.copy()
    for _ in range(max(0, px)):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _part(lab_pixels: np.ndarray, near: np.ndarray, centre: np.ndarray, of: int) -> dict:
    members = lab_pixels[near]
    return {"lab": [round(float(v), 1) for v in centre], "px": int(near.sum()),
            "share": round(float(near.sum() / max(1, of)), 2),
            "spread": round(float(np.linalg.norm(members - centre, axis=1).mean()), 1),
            "light_range": round(float(np.quantile(members[:, 0], 0.9) - np.quantile(members[:, 0], 0.1)), 1)}


def of_crop(picture: np.ndarray, labels: np.ndarray, crop: dict, wall: dict) -> dict:
    """Parts of one wall in one crop. Returns {'visible', 'in_front': {class: share}, 'parts': {name: {...}},
    'map': uint8 image of part indices (0 none, 1.. PARTS order, 255 in front)}."""
    h, w = labels.shape
    ppm = crop["px_per_m"]
    x = crop["x_left_m"] + (np.arange(w) + 0.5) / ppm
    z = crop["z_high_m"] - (np.arange(h) + 0.5) / ppm
    eaves, top = wall["eaves_h"] - wall["base_h"], wall["top_h"] - wall["base_h"]
    gable = top - eaves > 1.0
    edge = eaves + (top - eaves) * np.clip(1 - np.abs(2 * x / max(wall["length_m"], 0.1) - 1), 0, 1) if gable \
        else np.full(w, eaves)
    inside = (x >= crop["x0_m"] + 0.2) & (x <= crop["x1_m"] - 0.2)
    X, Z = np.meshgrid(x, z)
    on_wall = inside[None, :] & (Z >= 0.0) & (Z <= edge[None, :] - 0.15)
    out = {"visible": 0.0, "in_front": {}, "parts": {}, "gable": bool(gable), "map": np.zeros((h, w), np.uint8)}
    area = int(on_wall.sum())
    if area < 4 * MIN_PART_PX:
        return out
    house = np.isin(labels, HOUSE)
    out["visible"] = round(float(house[on_wall].mean()), 2)
    out["in_front"] = {name: round(float((labels[on_wall] == L[name]).mean()), 2) for name in IN_FRONT
                       if (labels[on_wall] == L[name]).mean() >= 0.01}
    out["map"][on_wall & ~house] = 255
    if out["visible"] < MIN_VISIBLE:
        return out
    lab = colour.to_lab(picture.reshape(-1, 3)).reshape(h, w, 3)
    plain = labels == L["building"]
    openings = (labels == L["window"]) | (labels == L["door"])
    ring = _grow(openings, max(2, round(0.14 * ppm))) & ~_grow(openings, 1) & plain & on_wall
    cladding = plain & on_wall & ~_grow(openings, max(3, round(0.2 * ppm)))

    def record(name: str, mask: np.ndarray, near: np.ndarray, centre: np.ndarray, **more):
        index = np.flatnonzero(mask.ravel())[near]
        out["map"].ravel()[index] = PARTS.index(name) + 1
        out["parts"][name] = {**_part(lab.reshape(-1, 3)[mask.ravel()], near, centre, int(mask.sum())), **more}
        return index

    # The plinth: what the lowest visible rows share, when it is not the cladding's colour.
    whole = mode(lab[cladding])
    if whole is None:
        return out
    wall_colour = whole[0]
    foot = cladding & (Z < PLINTH_MAX_M)
    plinth_top = 0.0
    low = mode(lab[cladding & (Z < 0.45)])
    if low is not None and np.linalg.norm(low[0] - wall_colour) > 8.0:
        is_plinth = np.linalg.norm(lab - low[0], axis=2) <= CLUSTER_DE
        rows = np.flatnonzero(foot.any(1))
        share = np.array([is_plinth[r][foot[r]].mean() for r in rows])
        tall = z[rows][share >= 0.5]
        if len(tall):
            plinth_top = float(min(PLINTH_MAX_M, tall.max() + 0.5 / ppm))
            band = cladding & (Z < plinth_top)
            found = mode(lab[band])
            if found is not None:
                record("plinth", band, found[1], found[0], height_m=round(plinth_top, 2))
    body = cladding & (Z >= plinth_top + 0.08)
    pixels = lab[body]
    first = mode(pixels)
    if first is None:
        return out
    centre, near = first
    record("wall", body, near, centre)
    rest = body.copy()
    rest.ravel()[np.flatnonzero(body.ravel())[near]] = False
    # A second colour on the wall: a zone of cladding when it holds together, trim when it is thin lines.
    for _ in range(2):
        second = mode(lab[rest]) if rest.sum() >= 0.06 * body.sum() else None
        if second is None or second[1].sum() < 0.04 * body.sum() or np.linalg.norm(second[0] - centre) < 10.0:
            break
        members = np.zeros(h * w, bool)
        members[np.flatnonzero(rest.ravel())[second[1]]] = True
        members = members.reshape(h, w)
        thick = max(2, round(0.18 * ppm))
        core = ~_grow(~members, thick)  # what survives an erosion by 18 cm
        solid = float(core.sum() / max(1, members.sum()))
        rows_z = Z[members]
        if solid >= 0.2 and "wall2" not in out["parts"]:
            where = "gable" if np.median(rows_z) > eaves else "upper" if np.median(rows_z) > np.median(Z[body]) else "lower"
            record("wall2", rest, second[1], second[0], zone=where, solid=round(solid, 2))
        elif solid < 0.2 and "trim" not in out["parts"]:
            record("trim", rest, second[1], second[0], solid=round(solid, 2))
        rest = rest & ~members
    frame = mode(lab[ring])
    if frame is not None:
        record("frame", ring, frame[1], frame[0])
    door = (labels == L["door"]) & on_wall
    found = mode(lab[door])
    if found is not None:
        record("door", door, found[1], found[0])
    if not gable:  # the roof slope shows above the eaves of an eaves-side wall
        above = inside[None, :] & (Z > eaves + 0.35) & (Z < eaves + 2.3) & np.isin(labels, (L["building"], L["roof"]))
        found = mode(lab[above])
        if found is not None and above.sum() >= 0.15 * inside.sum() * 1.9 * ppm:
            record("roof", above, found[1], found[0])
    return out


def score(crop: dict, parts: dict, overcast: float | None) -> float:
    """How good a crop is for colour: overcast light first, then free wall, then the view's own quality (frontal,
    near, sharp, recent). Overcast unknown counts as half."""
    return round((0.35 + 0.65 * (0.5 if overcast is None else overcast)) * parts["visible"] * crop["quality"], 3)


def combine(rows: list[dict], best: int = 3) -> dict:
    """One colour a part from the best crops: the weighted median in CIELAB over the `best` highest-scoring crops
    that hold the part; `agreement` is the weighted mean distance of those crops from it."""
    out = {}
    for name in PARTS:
        held = sorted((r for r in rows if name in r["parts"] and r["visible"] >= MIN_VISIBLE), key=lambda r: -r["score"])[:best]
        if not held:
            continue
        labs = np.array([r["parts"][name]["lab"] for r in held])
        weights = np.array([r["score"] * r["parts"][name]["px"] ** 0.5 for r in held]) + 1e-6
        centre = np.array([labs[o, k][min(len(held) - 1, np.searchsorted(np.cumsum(weights[o]), weights.sum() / 2))]
                           for k, o in ((k, np.argsort(labs[:, k])) for k in range(3))])
        distance = np.linalg.norm(labs - centre, axis=1)
        agreement = float((distance * weights).sum() / weights.sum())
        top = held[0]
        visible = float(np.mean([r["visible"] for r in held]))
        conf = 0.3 + 0.35 * min(visible, 0.8) / 0.8 + (0.2 if len(held) > 1 and agreement < 6 else 0.0) \
            - (0.2 if agreement > 12 else 0.0) + 0.1 * min(1.0, top["parts"][name]["px"] / 4000)
        part = {"lab": [round(float(v), 1) for v in centre], "pixel": colour.to_hex(centre),
                "colour": colour.to_hex(colour.calibrated(centre)),
                "spread": round(float(np.mean([r["parts"][name]["spread"] for r in held])), 1),
                "share": round(float(np.mean([r["parts"][name]["share"] for r in held])), 2),
                "visible": round(visible, 2), "agreement": round(agreement, 1), "n": len(held),
                "conf": round(float(np.clip(conf, 0.1, 0.95)), 2),
                "from": {k: top["panorama"][k] for k in ("provider", "id", "date")}}
        for extra in ("zone", "height_m"):
            if extra in top["parts"][name]:
                part[extra] = top["parts"][name][extra]
        out[name] = part
    return out


def of_packet(packet: Path, tag: str = TAG, overcast: dict | None = None, keep_maps: bool = False) -> dict | None:
    """Every crop of a prepared building measured and ranked; the parts combined. None without a views record."""
    if not (packet / "views.json").exists():
        return None
    record = json.loads((packet / "views.json").read_text())
    walls = {w["id"]: w for w in record["walls"]}
    rows = []
    for crop in record["crops"]:
        path = packet / Path(crop["file"]).name
        labels_path = path.with_suffix(f".{tag}.png")
        wall = walls.get(crop["wall"])
        if not path.exists() or not labels_path.exists() or wall is None:
            continue
        picture = colour.picture_of(path)
        labels = np.asarray(Image.open(labels_path))
        if labels.shape != picture.shape[:2]:
            continue
        measured = of_crop(picture, labels, crop, wall)
        pano = crop["panorama"]
        cloud = (overcast or {}).get(f"{pano['provider']}:{pano['id']}")
        row = {"file": path.name, "wall": crop["wall"], "panorama": pano, "distance_m": crop["distance_m"],
               "oblique_deg": crop["oblique_deg"], "quality": crop["quality"], "overcast": cloud,
               "visible": measured["visible"], "in_front": measured["in_front"], "gable": measured["gable"],
               "parts": measured["parts"], "score": score(crop, measured, cloud)}
        if keep_maps:
            row["map"] = measured["map"]
        rows.append(row)
    rows.sort(key=lambda r: -r["score"])
    return {"building": record["building"], "address": record.get("register", {}).get("address"),
            "year": record.get("register", {}).get("year"), "candidates": rows, "parts": combine(rows)}
