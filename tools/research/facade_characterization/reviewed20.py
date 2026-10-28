"""Re-evaluate recovered original wall IDs/colour targets with retained reference crops.

This is explicitly a reconstructed-input comparison: the deleted original input
JPEG hashes cannot be authenticated. Targets are recovered from a retained local
command log, never copied into the public repository.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor
from turku3d import config
from turkutex import facade_program as fp
from turkutex.facade_cv import MODEL_DIR, MODEL_REVISION
from turkutex.facade_derive import building_attributes
from turkutex.facade_fast_render import register_description
from turkutex.facade_models import Engine
from turkutex.facade_models_benchmark import crop_bytes, delta


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--refs", type=Path, required=True)
    a = p.parse_args()
    targets = json.loads((a.root / "original20-colour.json").read_text())
    attrs = building_attributes(
        config.raw_dir("wfs/work-3x3"), [r[0].rsplit("_", 1)[0] for r in targets]
    )
    parser = Engine("cmp")
    detector = Engine("grounding-dino-openings", size=800)
    if (MODEL_DIR / "revision.txt").read_text().strip() != MODEL_REVISION:
        raise ValueError("ADE revision mismatch")
    proc = SegformerImageProcessor.from_pretrained(MODEL_DIR, local_files_only=True)
    ade = (
        SegformerForSemanticSegmentation.from_pretrained(MODEL_DIR, local_files_only=True)
        .eval()
        .to("mps")
    )
    output = []
    for fid, _, _, target, _, _ in targets:
        directory = a.refs / fid
        candidates = [
            json.loads(p.read_text()) for p in directory.glob("*.json") if p.name != "refs.json"
        ]
        full = [
            v
            for v in candidates
            if v.get("part") == "full" and (directory / v["files"]["rectified"]).exists()
        ]
        view = max(full, key=lambda v: v["score"])
        data = crop_bytes(directory / view["files"]["rectified"])
        rgb = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
        corners = np.array(view["facade_corners_EN_H"])
        width = float(np.linalg.norm(corners[1] - corners[0]))
        height = float(np.linalg.norm(corners[3] - corners[0]))
        start = time.monotonic()

        def labels(model, processor, rgb=rgb):
            x = processor(images=Image.fromarray(rgb), return_tensors="pt").to("mps")
            with torch.inference_mode():
                y = model(**x).logits
                return (
                    torch.nn.functional.interpolate(
                        y, size=rgb.shape[:2], mode="bilinear", align_corners=False
                    )
                    .argmax(1)[0]
                    .cpu()
                    .numpy()
                )

        cmp = labels(parser.model, parser.processor)
        semantic = labels(ade, proc)
        excluded = np.isin(semantic, [2, 4, 6, 9, 11, 12, 17, 20, 21, 80, 83, 90, 102, 116, 127])
        boxes = detector.detect(rgb)
        cmp, count = fp.opening_labels(cmp, boxes, excluded, width, height)
        bid = fid.rsplit("_", 1)[0]
        attribute = attrs[bid]
        floors = attribute.get("levels") or None
        wall = fp.fit_wall(
            rgb, cmp, excluded, wall_id=fid, width=width, height=height, floors=floors, doors=boxes
        )
        measured = wall["wall"]["colour"]["value"]
        colour = measured
        if colour is None:
            colour = register_description(fid, bid, attribute, width, height)["wall_colour"]
        output.append(
            dict(
                wall_id=fid,
                view_id=view["view_id"],
                crop_sha256=hashlib.sha256(data).hexdigest(),
                width_m=width,
                height_m=height,
                storeys=floors,
                wall=wall,
                seconds=time.monotonic() - start,
                colour_delta=delta(colour, target),
                measured_colour=bool(measured),
            )
        )
    summary = dict(
        walls=len(output),
        measured_colour=sum(r["measured_colour"] for r in output),
        median_colour_delta=float(np.median([r["colour_delta"] for r in output])),
        seconds=sum(r["seconds"] for r in output),
        original_input_hash_verified=False,
        reconstruction="Original IDs and colour.json targets from facade-cv-describe events item_12; highest-score retained full view, same 768px JPEG transform. Original deleted input hashes unavailable.",
        truth="Accepted generated-map wall colour, not calibrated photographic albedo",
    )
    (a.root / "reviewed20.json").write_text(
        json.dumps(dict(summary=summary, rows=output), indent=1)
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
