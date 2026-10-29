"""Offline facade grammar prototype. No production generator imports this module.

Coordinates are metres from the viewed wall's bottom-left corner. Register floors
are authoritative; an absent floor count stays unknown. Evidence scores describe
support, not calibrated probabilities. Unsupported architectural fields stay null.
"""

from __future__ import annotations

import copy
import hashlib
import math

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

from .facade_cv import colours

VERSION = "facade-program-prototype-1"


def stable_seed(building_id: str, field: str = "") -> int:
    """Do not use Python's randomised hash or a traversal-dependent random stream."""
    return int.from_bytes(
        hashlib.sha256(f"{VERSION}:{building_id}:{field}".encode()).digest()[:8], "big"
    )


def value(v, source, support=0.0):
    return dict(value=v, source=source, support=round(float(support), 4))


def unknown():
    return value(None, "unmeasured")


def components(mask, width, height, minimum_area=0.08):
    """Metric component bounds; remove slivers and image-spanning misclassifications."""
    h, w = mask.shape
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype("uint8"), 8)
    result = []
    for x, y, bw, bh, pixels in stats[1:]:
        mw, mh = bw * width / w, bh * height / h
        if pixels * width * height / (w * h) < minimum_area or mw < 0.2 or mh < 0.3:
            continue
        if mw > min(8, width * 0.8) or mh > min(6, height * 0.85):
            continue
        result.append(
            [float(x * width / w), float((h - y - bh) * height / h), float(mw), float(mh)]
        )
    return result


def bay_grid(window_mask, width, height, floors):
    """Measure horizontal opening peaks separately by registered floor band.

    A supported row wins by peak contrast. Missing rows do not change storeys.
    No extrapolation fills hidden columns; the measured count is a lower bound.
    """
    h, w = window_mask.shape
    bands = max(1, floors or 1)
    profiles = []
    for floor in range(1 if bands > 1 else 0, bands):
        a, b = round(h * (1 - (floor + 1) / bands)), round(h * (1 - floor / bands))
        if b - a < 2:
            continue
        profile = gaussian_filter1d(
            window_mask[a:b].mean(0).astype(float), max(0.8, w / width * 0.1)
        )
        peaks, _ = find_peaks(
            profile, distance=max(1, w / width * 0.7), prominence=max(0.04, np.ptp(profile) * 0.2)
        )
        peaks = peaks[profile[peaks] > 0.12]
        if len(peaks):
            profiles.append((float(profile[peaks].sum()), peaks))
    if not profiles:
        return dict(count=0, centers_m=[], pitch_m=None, symmetry=None, support=0.0)
    _, peaks = max(profiles, key=lambda p: p[0])
    centers = (peaks + 0.5) * width / w
    spacing = np.diff(centers)
    support = min(1, window_mask.mean() * 5)
    symmetry = float(np.mean(np.abs(centers - (width - centers[::-1])) < 0.35))
    return dict(
        count=len(peaks),
        centers_m=centers.round(3).tolist(),
        pitch_m=round(float(np.median(spacing)), 3) if len(spacing) else None,
        symmetry=round(symmetry, 3),
        support=round(support, 3),
    )


def fit_wall(
    rgb, labels, excluded, *, wall_id, width, height, floors, coverage=None, doors=(), entrances=()
):
    """Fit a compact split grammar from CMP labels and an independent occluder mask."""
    if labels.shape != rgb.shape[:2] or excluded.shape != labels.shape:
        raise ValueError("mask/image dimensions differ")
    if not math.isfinite(width * height) or min(width, height) <= 0:
        raise ValueError("wall dimensions must be finite and positive")
    if floors is not None and (not isinstance(floors, int) or not 1 <= floors <= 100):
        raise ValueError("register floors must be a positive integer or null")
    visible = ~excluded & (rgb.max(-1) > 2)
    if coverage is not None:
        visible &= coverage
    wall_mask = (labels == 1) & visible
    wall_mask = cv2.erode(wall_mask.astype("uint8"), np.ones((3, 3), np.uint8)).astype(bool)
    window_mask = (labels == 2) & visible
    ground_mask = (labels == 3) & visible  # Publisher combines doors and shopfronts.
    measured = float(wall_mask.mean()) >= 0.035
    colour, _, support = colours(rgb, wall_mask) if measured else (None, None, 0)
    floor_h = height / floors if floors else None
    base_h = floor_h
    h, w = labels.shape
    lower = np.arange(h)[:, None] >= h * (1 - 1 / max(floors or 1, 1))
    base_colour = colours(rgb, wall_mask & lower)[0] if (wall_mask & lower).sum() >= 16 else None
    boxes = components(window_mask, width, height)
    grid = bay_grid(window_mask, width, height, floors)
    upper_boxes = [b for b in boxes if not floor_h or b[1] + b[3] / 2 >= floor_h]
    typical = upper_boxes or boxes
    sizes = np.median([b[2:] for b in typical], axis=0).tolist() if typical else None
    # Preserve measured exceptions: a regular grid is useful for unobserved walls,
    # but explicit observed boxes remain available to the diagnostic painter.
    window_conf = min(1, len(typical) / 8) * float(visible.mean())
    detected = []
    for d in doors:
        if "door" not in d["label"] or d["score"] < 0.2:
            continue
        x0, y0, x1, y1 = d["box"]
        b = [
            max(0, x0) * width / w,
            max(0, h - y1) * height / h,
            min(w, x1 - x0) * width / w,
            min(h, y1 - y0) * height / h,
        ]
        if 0.4 <= b[2] <= 4 and 0.8 <= b[3] <= 5 and b[1] < 1.2:
            detected.append(
                dict(rect_m=[round(float(v), 3) for v in b], source="DINO door", support=d["score"])
            )
    for e in entrances:
        detected.append(
            dict(u_m=e["u_m"], source="OSM entrance", osm_id=e["id"], type=e.get("entrance"))
        )
    # Null means unmeasured; false means a supported negative, which we do not infer.
    return dict(
        wall_id=wall_id,
        width_m=width,
        height_m=height,
        observation=dict(
            visible_share=round(float(visible.mean()), 4),
            wall_share=round(float(wall_mask.mean()), 4),
            needs_review=True,
            register_height_conflict=bool(floors and not 1.8 <= height / floors <= 6.5),
        ),
        split=dict(
            base_height_m=value(base_h, "register equal-height prior", 0.2)
            if base_h
            else unknown(),
            middle_repeats=max(0, floors - 2) if floors else None,
            top_height_m=unknown(),
        ),
        wall=dict(
            colour=value(colour, "CMP wall pixels", support * visible.mean()), material=unknown()
        ),
        bays=grid,
        windows=dict(
            size_m=value(sizes, "CMP components", window_conf),
            aspect=value(sizes[0] / sizes[1] if sizes else None, "CMP components", window_conf),
            rectangles_m=[[round(v, 3) for v in b] for b in boxes],
            sill_fraction=value(
                float(np.median([(b[1] % floor_h) / floor_h for b in typical]))
                if floor_h and typical
                else None,
                "register bands / CMP",
                0.2,
            ),
            frame_colour=unknown(),
            divisions=unknown(),
            recess_m=unknown(),
        ),
        base=dict(
            colour=value(base_colour, "CMP lower registered band", 0.3 if base_colour else 0),
            material=unknown(),
            plinth_height_m=unknown(),
            glazing_share=value(
                float((ground_mask | window_mask)[np.broadcast_to(lower, (h, w))].mean()),
                "CMP lower registered band",
                visible.mean() * 0.5,
            ),
            shopfront=unknown(),
            openings_m=components(ground_mask, width, height),
        ),
        doors=dict(candidates=detected, type=unknown(), canopy=unknown()),
        trim={k: unknown() for k in ("cornice", "string_courses", "window_surrounds", "colour")},
        corners={k: unknown() for k in ("quoins", "shape", "tower")},
        balconies=value(components((labels == 4) & visible, width, height), "CMP balcony", 0.2),
        bay_windows=unknown(),
        style_era=unknown(),
    )


def merge_building(building_id, attributes, walls):
    """Keep each wall and colour variation; do not copy one facade over the building."""
    walls = sorted(walls, key=lambda w: w["wall_id"])
    supported = [w for w in walls if w["wall"]["colour"]["value"] is not None]
    dominant = (
        max(supported, key=lambda w: w["width_m"] * w["height_m"] * w["observation"]["wall_share"])
        if supported
        else None
    )
    return dict(
        schema=VERSION,
        building_id=building_id,
        seed=str(stable_seed(building_id)),
        source="photographed",
        needs_review=True,
        attributes=attributes,
        storeys=value(
            attributes.get("levels"),
            "register" if attributes.get("levels") else "unavailable",
            1 if attributes.get("levels") else 0,
        ),
        material=value(attributes.get("material_class"), "register", 0.4),
        style_era=value(attributes.get("era"), "register year band", 0.4),
        representative_wall=dominant["wall_id"] if dominant else None,
        walls=walls,
    )


NUMERIC = {
    "levels": 3,
    "height_m": 10,
    "year": 60,
    "log_area": 1.5,
    "compactness": 0.3,
    "elongation": 2,
    "frontage_m": 25,
    "neighbour_height_m": 10,
    "E": 600,
    "N": 600,
}
CATEGORICAL = ("material_class", "use", "district")


def attribute_distance(a, b):
    terms = []
    for key, scale in NUMERIC.items():
        x, y = a.get(key), b.get(key)
        if x is not None and y is not None:
            terms.append(min(4, abs(x - y) / scale))
    terms.extend(float(a[k] != b[k]) for k in CATEGORICAL if a.get(k) and b.get(k))
    return float(np.mean(terms)) if terms else 4.0


def fit_rules(buildings):
    """An empirical conditional distribution: nearest buildings, one donor each.

    Fitting stores measured grammar exemplars with available covariates. It never
    treats propagated/sampled walls as observations or learns floors from photos.
    """
    exemplars = []
    for b in sorted(buildings, key=lambda b: b["building_id"]):
        if b["source"] != "photographed" or b["representative_wall"] is None:
            continue
        wall = next(w for w in b["walls"] if w["wall_id"] == b["representative_wall"])
        if (
            wall["observation"]["visible_share"] < 0.55
            or (wall["bays"]["count"] or 0) < 2
            or wall["observation"].get("scope", "full-wall") != "full-wall"
            or wall["observation"].get("register_height_conflict", False)
        ):
            continue
        exemplars.append(dict(building_id=b["building_id"], attributes=b["attributes"], wall=wall))
    return dict(
        schema=VERSION,
        method="8-neighbour weighted empirical conditional sample",
        exemplars=exemplars,
    )


def sample_building(building_id, attributes, geometry, rules, *, conditional=True):
    candidates = [r for r in rules["exemplars"] if r["building_id"] != building_id]
    if not candidates:
        raise ValueError("no independent characterized donors")
    ranked = sorted(
        candidates,
        key=lambda r: (
            attribute_distance(attributes, r["attributes"]) if conditional else 0,
            r["building_id"],
        ),
    )
    if conditional:
        ranked = ranked[:8]
    weights = np.exp(
        -np.array(
            [attribute_distance(attributes, r["attributes"]) if conditional else 0 for r in ranked]
        )
    )
    weights /= weights.sum()
    rng = np.random.default_rng(stable_seed(building_id, "donor"))
    donor = ranked[int(rng.choice(len(ranked), p=weights))]
    walls = []
    floors = attributes.get("levels")
    for g in sorted(geometry, key=lambda g: g["wall_id"]):
        wall = copy.deepcopy(donor["wall"])
        for key in ("evidence", "geometry", "opening_source", "detector_window_count"):
            wall.pop(key, None)
        wall.update(g)
        pitch = wall["bays"]["pitch_m"] or 3
        count = max(1, round(g["width_m"] / pitch))
        centers = [(i + 0.5) * g["width_m"] / count for i in range(count)]
        wall["bays"].update(count=count, centers_m=centers, pitch_m=g["width_m"] / count, support=0)
        wall["windows"]["rectangles_m"] = []
        wall["base"]["openings_m"] = []
        wall["doors"]["candidates"] = []
        wall["balconies"] = unknown()
        wall["observation"] = dict(
            visible_share=0,
            wall_share=0,
            needs_review=True,
            register_height_conflict=bool(floors and not 1.8 <= g["height_m"] / floors <= 6.5),
        )
        wall["split"]["base_height_m"] = value(
            g["height_m"] / floors if floors else None, "register equal-height prior", 0.2
        )
        wall["split"]["middle_repeats"] = max(0, floors - 2) if floors else None

        def mark(v):
            if isinstance(v, dict):
                if "value" in v and v["value"] is not None:
                    v.update(source="rule sample", support=0)
                else:
                    for child in v.values():
                        mark(child)

        mark(wall)
        walls.append(wall)
    result = merge_building(building_id, attributes, walls)
    result.update(
        source="rule-sampled",
        donor_building_id=donor["building_id"],
        donor_distance=attribute_distance(attributes, donor["attributes"]),
    )
    return result


def paint(wall, floors, size=512):
    """Small CPU diagnostic painter, no image/model input or production binding."""
    w = max(32, round(size * min(1, wall["width_m"] / wall["height_m"])))
    h = max(32, round(size * min(1, wall["height_m"] / wall["width_m"])))
    image = Image.new("RGB", (w, h), wall["wall"]["colour"]["value"] or "#b5b2aa")
    d = ImageDraw.Draw(image)
    sx, sy = w / wall["width_m"], h / wall["height_m"]
    base_h = wall["split"]["base_height_m"]["value"]
    if base_h:
        d.rectangle(
            (0, h - base_h * sy, w, h),
            fill=wall["base"]["colour"]["value"] or wall["wall"]["colour"]["value"] or "#b5b2aa",
        )
    boxes = list(wall["windows"]["rectangles_m"])
    if not boxes and floors:
        ww, wh = wall["windows"]["size_m"]["value"] or [1.2, 1.7]
        fh = wall["height_m"] / floors
        boxes = [
            [
                x - min(ww, wall["bays"]["pitch_m"] * 0.75) / 2,
                f * fh + fh * 0.25,
                min(ww, wall["bays"]["pitch_m"] * 0.75),
                min(wh, fh * 0.65),
            ]
            for f in range(floors)
            for x in wall["bays"]["centers_m"]
        ]
    for x, y, bw, bh in boxes:
        rect = (x * sx, h - (y + bh) * sy, (x + bw) * sx, h - y * sy)
        d.rectangle(rect, fill="#34454e", outline="#d9d8d1", width=max(1, round(sx * 0.08)))
    for door in wall["doors"]["candidates"]:
        box = door.get("rect_m")
        if box is not None:
            x, y, bw, bh = box
            d.rectangle((x * sx, h - (y + bh) * sy, (x + bw) * sx, h - y * sy), fill="#514a40")
    return image


def opening_labels(labels, detections, excluded, width, height):
    """Replace collapsed CMP windows with NMS-filtered, metric DINO boxes.

    CMP supplies wall/ground/balcony classes; DINO supplies opening extents.
    Occluded boxes and image-sized detections cannot create a facade grid.
    """
    h, w = labels.shape
    accepted = []
    for d in sorted(detections, key=lambda d: -d["score"]):
        if "window" not in d["label"] or d["score"] < 0.25:
            continue
        x0, y0, x1, y1 = np.clip(d["box"], [0, 0, 0, 0], [w, h, w, h])
        bw, bh = (x1 - x0) * width / w, (y1 - y0) * height / h
        if not (0.35 <= bw <= 5 and 0.35 <= bh <= 4.5):
            continue
        if (x1 - x0) * (y1 - y0) > w * h * 0.2:
            continue
        a, b, c, e = map(int, (x0, y0, x1, y1))
        if c <= a or e <= b or excluded[b:e, a:c].mean() > 0.4:
            continue
        area = (x1 - x0) * (y1 - y0)
        duplicate = False
        for xx0, yy0, xx1, yy1 in accepted:
            inter = max(0, min(x1, xx1) - max(x0, xx0)) * max(0, min(y1, yy1) - max(y0, yy0))
            if inter / max(1, area + (xx1 - xx0) * (yy1 - yy0) - inter) > 0.4:
                duplicate = True
                break
        if not duplicate:
            accepted.append((x0, y0, x1, y1))
    result = labels.copy()
    result[result == 2] = 1
    for x0, y0, x1, y1 in accepted:
        a, b, c, d = map(int, (x0, y0, x1, y1))
        result[b:d, a:c] = 2
    return result, len(accepted)
