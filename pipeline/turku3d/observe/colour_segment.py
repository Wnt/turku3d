"""What is in front of a house and where its windows and doors are: a local segmentation model over prepared crops.

Standalone on purpose (torch, transformers, PIL, numpy; nothing of turku3d): it runs in a torch environment on any
of our machines or a GPU server, beside the pipeline environment that has no torch. Pictures in, label PNGs out.

    python colour_segment.py --root WORK/observe/buildings [--model HF_ID] [--tag NAME] [--device mps|cuda|cpu]
                             [--only KEY...] [--ruler 30] [--max-side 1024] [--watch] [--walls-only]

For every `w*.jpg` (rectified wall crop, rulers cut off) and `context-*.jpg` of each packet under --root it writes
`<name>.<tag>.png`: one byte a pixel, the classes of LABELS below, the size of the picture without rulers. The
model's own classes are folded into ours by name, so ADE20K and Mapillary Vistas models give the same labels.
Timings go to `<root>/segment-<tag>.json`.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

LABELS = ("other", "building", "window", "door", "vegetation", "vehicle", "person", "fence", "sky", "ground",
          "pole", "attached", "roof")
# Our class for a model class, by words of its name (first match wins; names are lower case).
WORDS = (
    ("window", ("windowpane", "window", "glass")),
    ("door", ("door", "garage")),
    ("roof", ("roof",)),
    ("vegetation", ("tree", "plant", "flower", "palm", "vegetation", "bush", "hedge")),
    ("vehicle", ("car", "bus", "truck", "van", "minibike", "bicycle", "motorcycle", "trailer", "caravan", "boat",
                 "vehicle", "wheeled")),
    ("person", ("person", "rider", "bicyclist", "motorcyclist")),
    ("fence", ("fence", "railing", "bannister", "guard rail", "barrier")),
    ("sky", ("sky",)),
    ("ground", ("road", "sidewalk", "earth", "grass", "path", "dirt", "floor", "field", "sand", "land", "terrain",
                "curb", "parking", "lane", "crosswalk", "snow", "water", "manhole", "rail track", "runway")),
    ("pole", ("pole", "streetlight", "street light", "traffic", "signboard", "sign", "ashcan", "trash", "mailbox",
              "box", "bench", "utility")),
    ("attached", ("stairs", "stairway", "step", "awning", "canopy", "column", "balcony", "chimney")),
    ("building", ("wall", "building", "house", "skyscraper", "hovel", "tower", "booth")),
)


def fold(id2label: dict) -> np.ndarray:
    """Our class index for each of the model's class indices."""
    table = np.zeros(max(int(k) for k in id2label) + 1, np.uint8)
    for index, name in id2label.items():
        names = [n.strip() for n in str(name).lower().replace("--", ",").replace("-", " ").split(",")]
        for ours, words in WORDS:
            if any(n == w or n.startswith(w + " ") or n.endswith(" " + w) for n in names for w in words):
                table[int(index)] = LABELS.index(ours)
                break
    return table


class Segmenter:
    def __init__(self, model_id: str, device: str, max_side: int):
        import torch
        from transformers import AutoImageProcessor

        self.torch = torch
        self.device = device
        self.max_side = max_side
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        try:
            from transformers import AutoModelForUniversalSegmentation

            self.model = AutoModelForUniversalSegmentation.from_pretrained(model_id)
            self.universal = True
        except Exception:  # noqa: BLE001 - a plain semantic head (UperNet, DPT, BEiT)
            from transformers import AutoModelForSemanticSegmentation

            self.model = AutoModelForSemanticSegmentation.from_pretrained(model_id)
            self.universal = False
        self.model.to(device).eval()
        self.table = fold(self.model.config.id2label)

    def labels(self, image: Image.Image) -> np.ndarray:
        torch = self.torch
        size = image.size
        scale = min(1.0, self.max_side / max(size))
        small = image.resize((max(32, round(size[0] * scale)), max(32, round(size[1] * scale))), Image.BILINEAR)
        inputs = self.processor(images=small, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        if self.universal:
            classes = self.processor.post_process_semantic_segmentation(outputs, target_sizes=[small.size[::-1]])[0]
        else:
            logits = torch.nn.functional.interpolate(outputs.logits, size=small.size[::-1], mode="bilinear",
                                                     align_corners=False)
            classes = logits.argmax(1)[0]
        ours = self.table[classes.cpu().numpy()]
        return np.asarray(Image.fromarray(ours).resize(size, Image.NEAREST))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="directory of building packets")
    ap.add_argument("--model", default="facebook/mask2former-swin-large-ade-semantic")
    ap.add_argument("--tag", default="seg")
    ap.add_argument("--device", default=None)
    ap.add_argument("--only", nargs="*", help="packet names")
    ap.add_argument("--ruler", type=int, default=30, help="ruler margin of wall crops (left and bottom), pixels")
    ap.add_argument("--max-side", type=int, default=1024)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--watch", action="store_true",
                    help="keep looking for new crops (a batch writes them meanwhile) until <root>/../batch/stop exists "
                         "or nothing came for --idle-exit seconds")
    ap.add_argument("--idle-exit", type=float, default=3600.0)
    ap.add_argument("--walls-only", action="store_true", help="wall crops only, no context pictures")
    a = ap.parse_args(argv)
    import torch

    device = a.device or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    started = time.time()
    segmenter = Segmenter(a.model, device, a.max_side)
    loaded = time.time()
    done, seconds = 0, []

    def pending():
        for packet in sorted(p for p in a.root.iterdir() if p.is_dir() and (not a.only or p.name in a.only)):
            pictures = list(packet.glob("w*.jpg")) + ([] if a.walls_only else list(packet.glob("context-*.jpg")))
            for path in sorted(pictures):
                if a.force or not path.with_suffix(f".{a.tag}.png").exists():
                    yield path

    idle_since = time.time()
    while True:
        worked = False
        for path in pending():
            out = path.with_suffix(f".{a.tag}.png")
            try:
                image = Image.open(path).convert("RGB")
            except Exception:  # noqa: BLE001 - a crop still being written, or removed meanwhile: next round
                continue
            if path.name.startswith("w") and a.ruler:
                image = image.crop((a.ruler, 0, image.width, image.height - a.ruler))
            t = time.time()
            temporary = out.with_suffix(".tmp")
            Image.fromarray(segmenter.labels(image)).save(temporary, format="PNG")
            try:
                temporary.replace(out)
            except FileNotFoundError:  # the packet was cleared meanwhile
                continue
            seconds.append(time.time() - t)
            done += 1
            worked = True
        if not a.watch:
            break
        if worked:
            idle_since = time.time()
        elif (a.root.parent / "batch/stop").exists() or time.time() - idle_since > a.idle_exit:
            break
        else:
            time.sleep(1.0)
    summary = {"model": a.model, "tag": a.tag, "device": device, "images": done, "load_s": round(loaded - started, 1),
               "median_s": round(float(np.median(seconds)), 2) if seconds else None,
               "total_s": round(time.time() - started, 1), "labels": list(LABELS)}
    (a.root / f"segment-{a.tag}.json").write_text(json.dumps(summary))
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
