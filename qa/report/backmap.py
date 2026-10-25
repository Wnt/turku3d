"""Issue bbox -> game objects, via the object-ID render pass (?pass=id).

ID image: RGB PNG, object id = R<<16 | G<<8 | B, 0 = sky / nothing. The id table (id.json written by
render.mjs from window.__qaIdTable) maps str(id) -> {"kind", "id", "tile", "material", ...}.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image


def load_ids(id_png: str | Path) -> np.ndarray:
    a = np.asarray(Image.open(id_png).convert("RGB")).astype(np.uint32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def objects_in_bbox(ids: np.ndarray, table: dict, bbox, min_frac: float = 0.02, top: int = 5) -> list[dict]:
    h, w = ids.shape
    x0, y0, x1, y1 = bbox
    sub = ids[int(y0 * h):max(int(y0 * h) + 1, int(np.ceil(y1 * h))), int(x0 * w):max(int(x0 * w) + 1, int(np.ceil(x1 * w)))]
    vals, counts = np.unique(sub, return_counts=True)
    tot = counts.sum()
    out = []
    for v, c in sorted(zip(vals.tolist(), counts.tolist()), key=lambda t: -t[1]):
        if v == 0 or c / tot < min_frac:
            continue
        info = dict(table.get(str(v), {"kind": "unknown"}))
        info.update({"oid": v, "frac": round(c / tot, 3)})
        out.append(info)
        if len(out) >= top:
            break
    return out


def annotate(issues: list[dict], id_png, id_json) -> list[dict]:
    if not id_png or not Path(id_png).exists() or not id_json or not Path(id_json).exists():
        return issues
    ids = load_ids(id_png)
    table = json.loads(Path(id_json).read_text())
    for it in issues:
        it["objects"] = objects_in_bbox(ids, table, it["bbox"])
    return issues
