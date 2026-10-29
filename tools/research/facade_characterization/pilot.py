"""Agent-read pilot preparation: cached panos -> rectification -> foreground masks.

No facade parser or opening detector runs here. The assistant reads contact sheets
and writes reviewed programs separately; unknown fields are never filled by a model.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageOps
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor
from turkutex.facade_cv import MODEL_DIR, MODEL_REVISION
from turkutex.streetview.camera import EquirectModel
from turkutex.streetview.rectify import rectified_points, sample_wall


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    a = p.parse_args()
    r = a.root
    rows = json.loads((r / "selection.json").read_text())
    groups = defaultdict(list)
    for row in rows:
        for i, v in enumerate(row["views"]):
            groups[v["panorama"]].append((row, i, v))
    start = time.monotonic()
    torch.set_num_threads(2)
    assert (MODEL_DIR / "revision.txt").read_text().strip() == MODEL_REVISION
    proc = SegformerImageProcessor.from_pretrained(MODEL_DIR, local_files_only=True)
    model = (
        SegformerForSemanticSegmentation.from_pretrained(MODEL_DIR, local_files_only=True)
        .eval()
        .to("mps")
    )
    times = dict(load=time.monotonic() - start, decode=0.0, rectify=0.0, mask=0.0, write=0.0)
    views = defaultdict(list)
    (r / "crops").mkdir(exist_ok=True)
    for pano, items in groups.items():
        t = time.monotonic()
        with Image.open(r / "panoramas" / pano) as im:
            rgb = np.array(im.convert("RGB"))
        times["decode"] += time.monotonic() - t
        for row, i, v in items:
            name = f"{row['facade_id']}.{i}"
            t = time.monotonic()
            points = rectified_points(v["facade_corners_EN_H"], pixels_per_metre=32, max_side=1280)
            crop, coverage = sample_wall(
                rgb,
                EquirectModel(0, rotation=np.array(v["image_to_grid"])),
                points,
                v["camera"]["pos_EN_H"],
            )
            crop = np.clip(crop, 0, 255).astype("uint8")
            times["rectify"] += time.monotonic() - t
            t = time.monotonic()
            x = proc(images=Image.fromarray(crop), return_tensors="pt").to("mps")
            with torch.inference_mode():
                y = model(**x).logits
                labels = (
                    torch.nn.functional.interpolate(
                        y, size=crop.shape[:2], mode="bilinear", align_corners=False
                    )
                    .argmax(1)[0]
                    .cpu()
                    .numpy()
                )
            excluded = (
                np.isin(labels, [2, 4, 6, 9, 11, 12, 17, 20, 21, 80, 83, 90, 102, 116, 127])
                | ~coverage
            )
            times["mask"] += time.monotonic() - t
            t = time.monotonic()
            Image.fromarray(crop).save(r / "crops" / f"{name}.png")
            Image.fromarray(excluded.astype("uint8") * 255).save(r / "crops" / f"{name}.mask.png")
            times["write"] += time.monotonic() - t
            views[row["facade_id"]].append(
                dict(name=name, visible_share=float((~excluded).mean()), view=v)
            )
    chosen = []
    for i, row in enumerate(rows):
        v = max(views[row["facade_id"]], key=lambda v: (v["visible_share"], v["view"]["score"]))
        chosen.append(dict(index=i + 1, **row, chosen=v))
    for page in range((len(chosen) + 9) // 10):
        part = chosen[page * 10 : page * 10 + 10]
        sheet = Image.new("RGB", (1400, 340 * ((len(part) + 1) // 2)), "#eeece8")
        d = ImageDraw.Draw(sheet)
        for j, row in enumerate(part):
            x = j % 2 * 700
            y = j // 2 * 340
            with Image.open(r / "crops" / f"{row['chosen']['name']}.png") as im:
                thumb = ImageOps.contain(im.convert("RGB"), (690, 302))
            sheet.paste(thumb, (x + (700 - thumb.width) // 2, y + 36))
            d.text(
                (x + 5, y + 4),
                f"{row['index']:02d} {row['building_id']} {row['attributes']['address']} | registered floors {row['attributes']['levels']}",
                fill="black",
            )
            d.text(
                (x + 5, y + 20),
                f"{row['width_m']:.1f} x {row['height_m']:.1f} m | visible {row['chosen']['visible_share']:.0%}",
                fill="black",
            )
        sheet.save(r / f"read-{page + 1}.jpg", quality=95)
    (r / "chosen.json").write_text(json.dumps(chosen, indent=1))
    times.update(
        walls=len(rows), views=sum(map(len, views.values())), total=time.monotonic() - start
    )
    (r / "preprocess-timing.json").write_text(json.dumps(times, indent=1))
    print(json.dumps(times))


if __name__ == "__main__":
    main()
