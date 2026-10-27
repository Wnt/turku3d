"""Offline facade input transforms and metric opening-grid experiments.

All imagery stays local. Experimental confidence is not a calibrated probability;
these variants must not bypass the existing Astra/reviewer fallback.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

from . import facade_cv as cv

VERSION = "facade-cv-transforms-v1"
VARIANTS = (
    "baseline",
    "metric16",
    "metric32",
    "metric64",
    "clahe",
    "shade",
    "whitebalance",
    "median",
    "vote",
    "periodic",
    "contrast",
)


def metric_resize(rgb, width_m, height_m, ppm=32, max_side=1536):
    """Uniform metric sampling; preserves the wall aspect ratio including narrow slivers."""
    if min(width_m, height_m, ppm) <= 0:
        raise ValueError("wall extents and pixel density must be positive")
    density = min(ppm, max_side / max(width_m, height_m))
    size = (max(1, round(width_m * density)), max(1, round(height_m * density)))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)


def normalize_light(rgb, method):
    """Explicit ablations; grey-world is untrustworthy when a coloured wall fills the crop."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    if method == "clahe":
        lab[..., 0] = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(lab[..., 0])
    elif method == "shade":
        low = cv2.GaussianBlur(
            lab[..., 0].astype(np.float32), (0, 0), max(1, min(rgb.shape[:2]) / 12)
        )
        lab[..., 0] = np.clip(lab[..., 0] + np.median(low) - low, 0, 255).astype(np.uint8)
    elif method == "whitebalance":
        values = rgb.astype(np.float32)
        means = values.mean((0, 1))
        gains = np.clip(means.mean() / np.maximum(means, 1), 0.75, 1.33)
        return np.clip(values * gains, 0, 255).astype(np.uint8)
    else:
        raise ValueError(method)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def fuse_images(images):
    """Masked median on an equal wall grid; unsupported pixels retain the first view.

    No registration is inferred: camera-pose errors can ghost the result, which is
    why this is a measured alternative to answer voting, not an assumed improvement.
    """
    size = images[0].shape[1::-1]
    stack = np.stack([cv2.resize(im, size, interpolation=cv2.INTER_AREA) for im in images])
    valid = np.stack([~cv.classic_masks(im)[2] for im in stack])
    # Median ranks only supported samples, avoiding all-NaN warnings on empty pixels.
    ordered = np.sort(np.where(valid[..., None], stack.astype(float), np.inf), axis=0)
    n = valid.sum(0)
    lower = np.maximum(0, (n - 1) // 2)
    upper = np.maximum(0, n // 2)
    lo = np.take_along_axis(ordered, lower[None, ..., None], axis=0)[0]
    hi = np.take_along_axis(ordered, upper[None, ..., None], axis=0)[0]
    result = np.where((n > 0)[..., None], (lo + hi) / 2, stack[0])
    return np.clip(result, 0, 255).astype(np.uint8)


def metric_peaks(mask, valid, axis, extent_m, minimum_pitch_m):
    """Count supported opening bands with smoothing/separation expressed in metres."""
    profile = mask.sum(axis=axis) / np.maximum(1, valid.sum(axis=axis))
    ppm = len(profile) / extent_m
    profile = gaussian_filter1d(profile, max(0.7, ppm * 0.12))
    span = float(np.ptp(profile))
    if span < 0.04:
        return 0, 0.0
    # Padding lets a clipped opening at the facade edge count once.
    padded = np.pad(profile, 1)
    peaks, _ = find_peaks(
        padded, prominence=max(0.04, span * 0.25), distance=max(1, round(ppm * minimum_pitch_m))
    )
    peaks = peaks[(peaks > 0) & (peaks <= len(profile))] - 1
    if len(peaks) < 2:
        return len(peaks), 0.15 if len(peaks) else 0.0
    spacing = np.diff(peaks)
    regularity = float(np.clip(1 - np.std(spacing) / max(1, np.mean(spacing)), 0, 1))
    return len(peaks), regularity * float(valid.mean())


def vote_answers(results):
    """Vote counts, ties to the best-ranked view; Lab medoid for wall/trim colour."""
    valid_results = [(a, s) for a, s in results if a["wall_visible"]]
    answer, sidecar = (valid_results or results)[0]
    answer, sidecar = answer.copy(), {**sidecar, "confidence": sidecar["confidence"].copy()}
    usable = [a for a, _ in valid_results]
    if not usable:
        return answer, sidecar
    for field in ("storeys", "window_columns", "ground_openings"):
        answer[field] = Counter(a[field] for a in usable).most_common(1)[0][0]
        sidecar["confidence"][field] = round(
            sidecar["confidence"].get(field, 0.15)
            * sum(a[field] == answer[field] for a in usable)
            / len(usable),
            3,
        )
    for field in ("wall_colour", "trim_colour"):
        rgb = np.array(
            [[int(a[field][i : i + 2], 16) for i in (1, 3, 5)] for a in usable], np.uint8
        )
        lab = cv2.cvtColor(rgb[None].astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[0]
        medoid = np.argmin(np.linalg.norm(lab[:, None] - lab[None], axis=-1).sum(1))
        answer[field] = usable[medoid][field]
    if answer["storeys"] <= 1:
        answer["window_columns"] = 0
    sidecar["view_count"] = len(results)
    sidecar["needs_review"] = True
    return answer, sidecar


def measure_variant(
    rgb, facade_id, width_m, height_m, segmenter=None, *, variant="periodic", views=()
):
    if variant not in VARIANTS:
        raise ValueError(f"unknown transform: {variant}")
    if variant == "vote":
        result = vote_answers(
            [cv.measure(im, facade_id, width_m, height_m, segmenter) for im in [rgb, *views]]
        )
    else:
        transformed = rgb
        if variant.startswith("metric"):
            transformed = metric_resize(rgb, width_m, height_m, int(variant[6:]))
        elif variant in ("clahe", "shade", "whitebalance"):
            transformed = normalize_light(rgb, variant)
        elif variant == "median":
            transformed = fuse_images([rgb, *views])
        elif variant in ("periodic", "contrast"):
            transformed = metric_resize(rgb, width_m, height_m)
        answer, sidecar = cv.measure(transformed, facade_id, width_m, height_m, segmenter)
        if variant in ("periodic", "contrast"):
            _, openings, excluded = (
                segmenter.masks(transformed) if segmenter else cv.classic_masks(transformed)
            )
            if variant == "contrast":
                grey = cv2.cvtColor(transformed, cv2.COLOR_RGB2GRAY).astype(np.float32)
                ppm = transformed.shape[0] / height_m
                background = cv2.GaussianBlur(grey, (0, 0), max(1, ppm * 0.45))
                openings = (background - grey > 8) & ~excluded
            rows, row_conf = metric_peaks(openings, ~excluded, 1, height_m, 1.8)
            upper_height = max(1, round(len(openings) * (1 - 1 / max(2, rows))))
            cols, col_conf = metric_peaks(
                openings[:upper_height], ~excluded[:upper_height], 0, width_m, 0.7
            )
            answer["storeys"] = min(30, rows)
            answer["window_columns"] = min(60, cols) if rows > 1 else 0
            sidecar["confidence"].update(
                storeys=round(row_conf, 3), window_columns=round(col_conf, 3)
            )
        result = answer, sidecar
    result[1].update(
        engine=VERSION, transform=variant, rectification="direct panorama-to-wall-plane sampling"
    )
    return result


def describe(run: Path, inventory: Path, refs: Path, *, segmentation="classic") -> dict:
    """Describe screened local views by count vote and colour medoid; never calls a service."""
    import hashlib
    import json
    import time

    from . import facade_fast as fast
    from .building_jobs import screened_full_views
    from .facade_fast_render import validate

    _, facades = fast.planned_facades(run, inventory)
    segmenter = cv.Segmenter() if segmentation == "segformer" else None
    output = run / "run_describe/out.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and any(
        json.loads(line).get("model") != VERSION for line in output.read_text().splitlines()
    ):
        raise ValueError("use a separate run directory for the transforms engine")
    reasons, failures, count = {}, {}, 0
    start = time.monotonic()
    with output.open("w") as stream:
        for facade in facades:
            try:
                views, reason = screened_full_views(facade, refs)
                if not views:
                    reasons[facade.id] = reason
                    continue
                images, references = [], []
                for view in views:
                    path = refs / facade.id / view["files"]["rectified"]
                    with Image.open(path) as image:
                        image = image.convert("RGB")
                        image.thumbnail((fast.CROP_SIDE, fast.CROP_SIDE), Image.Resampling.LANCZOS)
                        images.append(np.asarray(image))
                    references.append(
                        dict(
                            source=view["source"],
                            view_id=view["view_id"],
                            crop_sha256=fast.sha(path),
                        )
                    )
                answer, sidecar = measure_variant(
                    images[0],
                    facade.id,
                    facade.length,
                    facade.height_m,
                    segmenter,
                    variant="vote",
                    views=images[1:],
                )
                errors = validate(answer, facade.id)
                if errors:
                    raise ValueError(errors)
                fast.write_json(run / "confidence" / f"{facade.id}.json", sidecar)
                key = hashlib.sha256(
                    (VERSION + segmentation + json.dumps(references, sort_keys=True)).encode()
                ).hexdigest()
                stream.write(
                    json.dumps(
                        dict(
                            id=f"{facade.id}.fast",
                            ok=True,
                            answer=answer,
                            model=VERSION,
                            effort="local",
                            key=key,
                            cached=False,
                            meta=dict(reference=references[0], references=references, cv=sidecar),
                        )
                    )
                    + "\n"
                )
                count += 1
            except Exception as exc:  # noqa: BLE001 - keep other walls and report this failure
                failures[facade.id] = f"{type(exc).__name__}: {exc}"
    result = dict(
        engine=VERSION,
        segmentation=segmentation,
        walls=len(facades),
        jobs=count,
        no_reference=reasons,
        failed=failures,
        calls=0,
        seconds=round(time.monotonic() - start, 3),
        exit=int(bool(failures)),
    )
    fast.write_json(run / "describe.json", result)
    return result
