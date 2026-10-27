"""Deterministic metrics; unknown truth fields are excluded, never guessed."""

import numpy as np
from PIL import Image


def labels(answer, truth):
    hits = sum(answer.get(k) == v for k, v in truth.items())
    return {"correct": hits, "fields": len(truth), "accuracy": hits / len(truth) if truth else None}


def qa(answer, planted):
    # One-to-one, category-aware matching prevents duplicate issues inflating recall.
    from qalib.degrade import iou

    remaining = list(planted)
    tp = 0
    issues = answer.get("issues", [])
    for issue in sorted(issues, key=lambda x: -x.get("severity", 0)):
        matches = [
            (iou(issue["bbox"], p["bbox"]), n)
            for n, p in enumerate(remaining)
            if issue.get("category") == p["category"]
        ]
        if matches and max(matches)[0] >= 0.25:
            remaining.pop(max(matches)[1])
            tp += 1
    return {
        "true_positives": tp,
        "planted": len(planted),
        "recall": tp / len(planted) if planted else None,
        "false_positives": len(issues) - tp,
    }


def improve(path, reference, target_aspect):
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB"), dtype=float) / 255
        aspect = im.width / im.height
    with Image.open(reference) as im:
        ref = np.asarray(im.convert("RGB"), dtype=float) / 255
    return {
        "aspect_relative_error": abs(aspect / target_aspect - 1),
        "seam_mae": float(np.abs(arr[:, 0] - arr[:, -1]).mean()),
        "mean_colour_distance": float(np.linalg.norm(arr.mean((0, 1)) - ref.mean((0, 1)))),
    }
