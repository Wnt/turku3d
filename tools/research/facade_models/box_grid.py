"""Rejected NMS/row-mode ablation, replaying saved DINO detections without inference."""

import copy
import json
import os
from pathlib import Path

import numpy as np
from turkutex.facade_cv_benchmark import benchmark


def box_columns(records, shape, storeys):
    """Suppress duplicate window boxes, then take the modal count of upper rows."""
    from collections import Counter

    h, w = shape
    if storeys <= 1:
        return 0
    boxes = []
    for record in sorted(records, key=lambda r: -r["score"]):
        if "window" not in record["label"]:
            continue
        box = np.asarray(record["box"], dtype=float)
        x0, y0, x1, y1 = box
        area = (x1 - x0) * (y1 - y0)
        if area <= 4 or area > h * w * 0.2 or (y0 + y1) / 2 > h * (1 - 1 / max(storeys, 2)):
            continue
        duplicate = False
        for other in boxes:
            overlap = np.maximum(
                0, np.minimum(box[2:], other[2:]) - np.maximum(box[:2], other[:2])
            ).prod()
            other_area = (other[2] - other[0]) * (other[3] - other[1])
            if overlap / (area + other_area - overlap) > 0.35:
                duplicate = True
                break
        if not duplicate:
            boxes.append(box)
    if not boxes:
        return 0
    tolerance = float(np.median([b[3] - b[1] for b in boxes])) * 0.5
    rows = []
    for box in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        y = (box[1] + box[3]) / 2
        if not rows or abs(y - np.median(rows[-1])) > tolerance:
            rows.append([y])
        else:
            rows[-1].append(y)
    votes = Counter(map(len, rows))
    return min(60, max(votes, key=lambda n: (votes[n], n)))


root = Path(os.environ["TURKU3D_WORK"]) / "reports/models"
base = json.loads((root / "grounding-dino-800.json").read_text())
rows = {r["facade_id"]: r for r in base["rows"]}


def measure(rgb, fid, w, h):
    r = rows[fid]
    d = copy.deepcopy(r["description"])
    d["window_columns"] = box_columns(r["cv"]["boxes"], rgb.shape[:2], d["storeys"])
    return d, r["cv"]


benchmark(
    Path.home() / "turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure",
    Path(os.environ["TURKU3D_DATA"]) / "facade-approvals/assembly",
    root / "grounding-dino-box-grid.json",
    "classic",
    measure_fn=measure,
    engine="grounding-dino-box-grid",
)
