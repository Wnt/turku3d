"""Offline facade measurements on the refs stage's plane-rectified RGB crops.

Confidence scores are evidence heuristics, not calibrated probabilities. Semantic fields
without sufficient local evidence stay low confidence and require a vision review.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

VERSION = "facade-cv-v1"
MODEL = "nvidia/segformer-b0-finetuned-ade-512-512"
MODEL_REVISION = "489d5cd81a0b59fab9b7ea758d3548ebe99677da"
MODEL_DIR = Path.home() / "turku3d/tools/cv/segformer-b0-ade"


def grid_axis(mask: np.ndarray, axis: int, maximum: int) -> tuple[int, float, float]:
    """Projection peaks -> count, median cell spacing, regularity on a binary mask."""
    profile = mask.astype(float).mean(axis=axis)
    profile = gaussian_filter1d(profile, max(0.8, len(profile) / 250))
    span = float(np.ptp(profile))
    if span < 0.025:
        return 0, 0.0, 0.0
    peaks, _ = find_peaks(
        profile, prominence=max(0.025, span * 0.22), distance=max(2, len(profile) // (maximum * 2))
    )
    if not len(peaks):
        return 0, 0.0, 0.0
    spacing = float(np.median(np.diff(peaks))) if len(peaks) > 1 else float(len(profile))
    regularity = (
        float(np.clip(1 - np.std(np.diff(peaks)) / spacing, 0, 1)) if len(peaks) > 2 else 0.5
    )
    return min(maximum, len(peaks)), spacing, regularity


def colours(rgb: np.ndarray, mask: np.ndarray) -> tuple[str, str, float]:
    """Deterministic Lab k-means on selected wall pixels, capped at 8k samples."""
    pixels = rgb[mask][:: max(1, int(mask.sum()) // 8000)]
    if len(pixels) < 16:
        return "#b5b2aa", "#dedbd4", 0.0
    lab = (
        cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    )
    # Fixed quantile seeds avoid changing OpenCV's process-global RNG in concurrent callers.
    order = np.argsort(lab[:, 0])
    labels = np.empty(len(lab), np.int32)
    for k, indices in enumerate(np.array_split(order, 3)):
        labels[indices] = k
    _, labels, centers = cv2.kmeans(
        lab,
        3,
        labels,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1),
        1,
        cv2.KMEANS_USE_INITIAL_LABELS,
    )
    counts = np.bincount(labels.ravel(), minlength=3)
    ranked = np.argsort(counts)[::-1]
    palette = cv2.cvtColor(np.uint8(np.clip(centers, 0, 255)).reshape(1, 3, 3), cv2.COLOR_LAB2RGB)[
        0
    ]

    def hex_colour(i):
        return "#" + "".join(f"{v:02x}" for v in palette[i])

    return hex_colour(ranked[0]), hex_colour(ranked[1]), float(counts.max() / len(lab))


def classic_masks(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Conservative colour/connected-component baseline; semantic occluders need a model."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    vegetation = (hsv[..., 0] > 30) & (hsv[..., 0] < 95) & (hsv[..., 1] > 65)
    sky = (
        (np.indices(grey.shape)[0] < grey.shape[0] * 0.12)
        & (hsv[..., 0] > 95)
        & (hsv[..., 0] < 125)
        & (hsv[..., 1] > 60)
        & (hsv[..., 2] > 160)
    )
    excluded = vegetation | sky | (rgb.max(-1) <= 2)
    dark = (grey < float(np.percentile(grey, 40))) & ~excluded
    dark = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark)
    openings = np.zeros(grey.shape, bool)
    h, w = grey.shape
    for i in range(1, n):
        _, _, bw, bh, area = stats[i]
        if 0.0003 < area / (h * w) < 0.12 and 0.15 < bw / bh < 4 and area / (bw * bh) > 0.4:
            openings |= labels == i
    return ~excluded & ~openings, openings, excluded


class Segmenter:
    """Publisher-pinned ADE20K model; loading and inference never contact the network."""

    def __init__(self, directory: Path = MODEL_DIR):
        import torch
        from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

        self.torch = torch
        torch.set_num_threads(2)
        if (directory / "revision.txt").read_text().strip() != MODEL_REVISION:
            raise ValueError("CV model revision mismatch; run prepare-model")
        self.processor = SegformerImageProcessor.from_pretrained(directory, local_files_only=True)
        self.model = SegformerForSemanticSegmentation.from_pretrained(
            directory, local_files_only=True
        ).eval()

    def masks(self, rgb: np.ndarray):
        torch = self.torch
        inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt")
        with torch.inference_mode():
            logits = self.model(**inputs).logits
            labels = (
                torch.nn.functional.interpolate(
                    logits, size=rgb.shape[:2], mode="bilinear", align_corners=False
                )
                .argmax(1)[0]
                .numpy()
            )
        # ADE20K zero-based ids: wall/building, windowpane/door, sky/tree/person/car/etc.
        wall = np.isin(labels, [0, 1, 25])
        openings = np.isin(labels, [8, 14])
        excluded = np.isin(labels, [2, 4, 6, 9, 11, 12, 17, 20, 21, 80, 83, 90, 102, 116, 127])
        # ADE20K often labels the whole facade as building, including windows. In that case
        # retain classical opening candidates inside the semantic building region.
        _, candidates, _ = classic_masks(rgb)
        openings |= candidates & wall
        wall &= ~openings
        return wall, openings, excluded


def measure(
    rgb: np.ndarray, facade_id: str, width_m: float, height_m: float, segmenter=None
) -> tuple[dict, dict]:
    from .streetview.refs import screen_pixels, screen_problem

    wall, openings, excluded = segmenter.masks(rgb) if segmenter else classic_masks(rgb)
    h, w = openings.shape
    rows, row_pitch, row_conf = grid_axis(openings, 1, 30)
    # Ground-floor doors/shop glass must not add columns to the upper-storey grid.
    upper = openings[: max(1, round(h * (1 - 1 / max(2, rows))))]
    columns, col_pitch, col_conf = grid_axis(upper, 0, 60)
    ground = openings[round(h * (1 - 1 / max(1, rows))) :]
    ground_count, _, _ = grid_axis(ground, 0, 60) if ground.size else (0, 0, 0)
    colour, trim, colour_conf = colours(rgb, wall)
    n, _, stats, _ = cv2.connectedComponentsWithStats(openings.astype(np.uint8))
    cells = [s for s in stats[1:n] if s[4] > h * w * 0.0003]
    ww = float(np.median([s[2] for s in cells]) / (col_pitch or w)) if cells else 0.0
    wh = float(np.median([s[3] for s in cells]) / (row_pitch or h)) if cells else 0.0
    screen = screen_pixels(rgb)
    problem = screen_problem(screen)
    occluded = float(excluded.mean())
    visible = problem is None and occluded < 0.5 and float(wall.mean()) > 0.12
    if not visible and not problem:
        problem = f"insufficient facade evidence; occlusion {occluded:.0%}"
    # Small explicit classifier: appearance evidence only; cannot reliably distinguish metal,
    # stone and plaster under illumination, or infer ornament hidden by the semantic mask.
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    red = ((hsv[..., 0] < 15) | (hsv[..., 0] > 170)) & (hsv[..., 1] > 90) & wall
    edges = cv2.Canny(rgb, 70, 150)
    texture = float((edges[wall] > 0).mean()) if wall.any() else 0.0
    material = "brick" if red.sum() / max(1, wall.sum()) > 0.4 and texture > 0.08 else "plaster"
    if openings.mean() > 0.65:
        material = "glass"
    bottom_share = float(ground.mean()) if ground.size else 0.0
    ground_type = (
        "shops"
        if bottom_share > 0.45
        else "doors"
        if wh > 0.75 and ground_count
        else "windows"
        if ground_count
        else "plain"
    )
    # A small appearance classifier for horizontal architectural bands. These deliberately
    # carry low confidence: a shadow or railing can produce the same evidence.
    horizontal = (
        cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((1, max(3, w // 8)), np.uint8)).mean(1)
        / 255
    )
    roof_line = "cornice" if horizontal[: max(1, h // 6)].max() > 0.3 else "none"
    plinth = bool(
        np.median(rgb[-max(1, h // 12) :]) < np.median(rgb) * 0.7
        and horizontal[-max(1, h // 5) :].max() > 0.3
    )
    bands, _ = find_peaks(horizontal, prominence=0.3, distance=max(1, h // 10))
    features = ["string_courses"] if sum((bands > h * 0.2) & (bands < h * 0.8)) >= 2 else []
    answer = dict(
        facade_id=facade_id,
        wall_visible=bool(visible),
        problem=problem or "",
        material=material,
        wall_colour=colour,
        trim_colour=trim,
        storeys=int(rows),
        window_columns=int(columns if rows > 1 else 0),
        window_width=round(min(1, ww), 3),
        window_height=round(min(1, wh), 3),
        ground_floor=ground_type,
        ground_openings=int(ground_count),
        roof_line=roof_line,
        plinth=plinth,
        features=features,
    )
    confidence = {k: 0.15 for k in answer if k != "facade_id"}
    confidence.update(
        wall_colour=round(colour_conf * min(1, float(wall.mean()) * 3), 3),
        storeys=round(row_conf * (1 - occluded), 3),
        window_columns=round(col_conf * (1 - occluded), 3),
        wall_visible=0.7 if segmenter else 0.3,
        material=0.25,
    )
    sidecar = dict(
        engine=VERSION,
        segmentation=MODEL if segmenter else "classic",
        model_revision=MODEL_REVISION if segmenter else None,
        confidence=confidence,
        needs_review=any(v < 0.65 for v in confidence.values()),
        occlusion_share=round(occluded, 4),
        screen=screen,
        rectification="refs wall-plane homography",
        width_m=width_m,
        height_m=height_m,
    )
    return answer, sidecar


def describe(run: Path, inventory: Path, refs: Path, *, segmentation="classic") -> dict:
    from . import facade_fast as fast
    from . import facade_fast_render as fr

    _, facades = fast.planned_facades(run, inventory)
    segmenter = Segmenter() if segmentation == "segformer" else None
    output = run / "run_describe/out.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    reasons, failures, count = {}, {}, 0
    start = time.monotonic()
    # CV runs have their own run directory; never append over an Astra run accidentally.
    if output.exists():
        for line in output.read_text().splitlines():
            if json.loads(line).get("model") != VERSION:
                raise ValueError("use a separate run directory for the CV engine")
    with output.open("w") as stream:
        for facade in facades:
            view, reason = fast.best_view(facade, refs)
            if view is None:
                reasons[facade.id] = reason
                continue
            try:
                crop = fast.crop_for_call(facade, view, refs, run / "inputs")
                with Image.open(crop) as image:
                    answer, sidecar = measure(
                        np.asarray(image.convert("RGB")),
                        facade.id,
                        facade.length,
                        facade.height_m,
                        segmenter,
                    )
                errors = fr.validate(answer, facade.id)
                if errors:
                    raise ValueError(errors)
                reference = dict(
                    source=view["source"], view_id=view["view_id"], crop_sha256=fast.sha(crop)
                )
                fast.write_json(run / "confidence" / f"{facade.id}.json", sidecar)
                key = hashlib.sha256(
                    (VERSION + reference["crop_sha256"] + segmentation).encode()
                ).hexdigest()
                record = dict(
                    id=f"{facade.id}.fast",
                    ok=True,
                    answer=answer,
                    model=VERSION,
                    effort="local",
                    key=key,
                    cached=False,
                    meta=dict(reference=reference, cv=sidecar),
                )
                stream.write(json.dumps(record) + "\n")
                count += 1
            except Exception as exc:  # noqa: BLE001 - retain per-wall failure without losing the run
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


def prepare_model():
    """Explicit opt-in download from the original publisher; describe never downloads."""
    import requests

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    revision = MODEL_DIR / "revision.txt"
    if revision.exists() and revision.read_text().strip() != MODEL_REVISION:
        raise ValueError("Existing cache belongs to another revision; remove it before preparing")
    for name in ("config.json", "preprocessor_config.json", "model.safetensors"):
        target = MODEL_DIR / name
        if target.exists():
            continue
        url = f"https://huggingface.co/{MODEL}/resolve/{MODEL_REVISION}/{name}"
        with requests.get(url, timeout=120, stream=True) as response:
            response.raise_for_status()
            temp = target.with_suffix(".partial")
            with temp.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
            temp.replace(target)
    (MODEL_DIR / "revision.txt").write_text(MODEL_REVISION + "\n")
    print(
        json.dumps(
            dict(
                model=MODEL,
                revision=MODEL_REVISION,
                bytes=sum(p.stat().st_size for p in MODEL_DIR.iterdir() if p.is_file()),
            )
        )
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-model"])
    parser.parse_args()
    prepare_model()
