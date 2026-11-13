"""What is in front of a house and where its windows and doors are: a local segmentation model over prepared crops.

Standalone on purpose (torch, transformers, PIL, numpy; nothing of turku3d): it runs in a torch environment on any
of our machines or a GPU server, beside the pipeline environment that has no torch. Pictures in, label PNGs out.

    python colour_segment.py --root WORK/observe/buildings [--model HF_ID] [--tag NAME] [--device mps|cuda|cpu]
                             [--only KEY...] [--ruler 30] [--max-side 1024] [--watch] [--walls-only]
                             [--remote USER@HOST:DIR]

With --remote the packets are on another machine (a batch fetching panoramas there, `colour --batch`): --root is a
local mirror; each round copies the packets that have a views.json from DIR over ssh (crops and record only), labels
them here, and puts the label maps back next to the crops (each one renamed into place, so the batch never reads
half a file). Packets the batch has removed are removed from the mirror. It stops when DIR/../batch/stop exists.

For every `w*.jpg` (rectified wall crop, rulers cut off) and `context-*.jpg` of each packet under --root it writes
`<name>.<tag>.png`: one byte a pixel, the classes of LABELS below, the size of the picture without rulers. The
model's own classes are folded into ours by name, so ADE20K and Mapillary Vistas models give the same labels.
Timings go to `<root>/segment-<tag>.json`.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
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


class Relay:
    """Packets of a batch on another machine (USER@HOST:DIR of building packets), labelled in a local mirror."""

    def __init__(self, remote: str, mirror: Path, tag: str):
        self.host, self.dir = remote.split(":", 1)
        self.mirror, self.tag = mirror, tag
        self.pushed: set = set()
        self.bytes_in = self.bytes_out = 0
        mirror.mkdir(parents=True, exist_ok=True)

    def ssh(self, command: str, data: bytes | None = None, timeout: float = 300.0) -> bytes:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30",
                               self.host, command], input=data, capture_output=True, timeout=timeout, check=True).stdout

    def pull(self) -> bool:
        """New packets in; packets gone there removed here. Returns whether the batch asked to stop."""
        d = shlex.quote(self.dir)
        listing = self.ssh(f"cd {d} && for f in */views.json; do [ -e \"$f\" ] && echo \"$f\"; done; "
                           f"[ -e ../batch/stop ] && echo STOP; true").decode().split()
        there = {line.split("/")[0] for line in listing if line.endswith("/views.json")}
        for packet in [p for p in self.mirror.iterdir() if p.is_dir() and p.name not in there]:
            shutil.rmtree(packet, ignore_errors=True)
            self.pushed = {k for k in self.pushed if not k.startswith(packet.name + "/")}
        new = sorted(p for p in there if not (self.mirror / p / "views.json").exists())
        for start in range(0, len(new), 40):
            names = " ".join(shlex.quote(p) for p in new[start:start + 40])
            data = self.ssh(f"cd {d} && find {names} -maxdepth 1 \\( -name 'w*.jpg' -o -name views.json \\) -print0 "
                            f"| tar cf - --null -T -")
            self.bytes_in += len(data)
            subprocess.run(["tar", "xf", "-", "-C", str(self.mirror)], input=data, check=True, timeout=300)
        return "STOP" in listing

    def push(self) -> int:
        """Label maps not yet sent go next to their crops there. Returns how many."""
        maps = sorted(str(p.relative_to(self.mirror)) for p in self.mirror.glob(f"*/w*.{self.tag}.png"))
        maps = [m for m in maps if m not in self.pushed]
        if not maps:
            return 0
        data = subprocess.run(["tar", "cf", "-", "-C", str(self.mirror), *maps], capture_output=True, check=True,
                              timeout=300).stdout
        self.bytes_out += len(data)
        d = shlex.quote(self.dir)
        stage = shlex.quote(self.dir.rstrip("/") + "/../batch/relay-stage")
        self.ssh(f"mkdir -p {stage} && tar xf - -C {stage} && cd {stage} && for f in */*.png; do "
                 f"[ -d {d}/\"$(dirname \"$f\")\" ] && mv \"$f\" {d}/\"$f\"; done; cd / && rm -rf {stage}", data)
        self.pushed.update(maps)
        return len(maps)


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
    ap.add_argument("--remote", help="USER@HOST:DIR: the packets are there (see above); --root is the local mirror")
    ap.add_argument("--also", type=Path, nargs="*", default=[],
                    help="more local directories of packets to label (one model in memory serves them all: two "
                         "segmenters on an 8 GB Air thrash)")
    a = ap.parse_args(argv)
    if a.remote:
        a.watch = a.walls_only = True
    relay = Relay(a.remote, a.root, a.tag) if a.remote else None
    import torch

    device = a.device or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    started = time.time()
    segmenter = Segmenter(a.model, device, a.max_side)
    loaded = time.time()
    done, seconds = 0, []

    def pending():
        packets = [p for root in [a.root, *a.also] if root.is_dir() for p in root.iterdir()]
        for packet in sorted((p for p in packets if p.is_dir() and (not a.only or p.name in a.only)), key=lambda p: p.name):
            if a.watch and not (packet / "views.json").exists():
                continue  # a batch writes views.json after the crops: until then a crop may be half written
            pictures = list(packet.glob("w*.jpg")) + ([] if a.walls_only else list(packet.glob("context-*.jpg")))
            for path in sorted(pictures):
                if a.force or not path.with_suffix(f".{a.tag}.png").exists():
                    yield path

    idle_since = pushed_at = time.time()
    stop = False
    while True:
        worked = False
        if relay is not None:
            try:
                stop = relay.pull()
            except (subprocess.SubprocessError, OSError) as error:  # the network or the host away for a while
                print(f"relay: pull failed: {error}", flush=True)
                time.sleep(30.0)
                continue
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
            if relay is not None and time.time() - pushed_at > 20.0:  # a batch waits for a whole cell's maps
                try:
                    relay.push()
                except (subprocess.SubprocessError, OSError) as error:
                    print(f"relay: push failed: {error}", flush=True)
                pushed_at = time.time()
        if relay is not None:
            try:
                relay.push()
            except (subprocess.SubprocessError, OSError) as error:
                print(f"relay: push failed: {error}", flush=True)
            if done and done % 200 == 0 and worked:
                print(json.dumps({"images": done, "median_s": round(float(np.median(seconds[-200:])), 2),
                                  "in_mb": round(relay.bytes_in / 1e6), "out_mb": round(relay.bytes_out / 1e6),
                                  "at": time.strftime("%H:%M:%SZ", time.gmtime())}), flush=True)
        if not a.watch:
            break
        if worked:
            idle_since = time.time()
        elif stop or (relay is None and (a.root.parent / "batch/stop").exists()) or time.time() - idle_since > a.idle_exit:
            break
        else:
            time.sleep(1.0 if relay is None else 3.0)
    summary = {"model": a.model, "tag": a.tag, "device": device, "images": done, "load_s": round(loaded - started, 1),
               "median_s": round(float(np.median(seconds)), 2) if seconds else None,
               "total_s": round(time.time() - started, 1), "labels": list(LABELS),
               **({"in_mb": round(relay.bytes_in / 1e6, 1), "out_mb": round(relay.bytes_out / 1e6, 1)} if relay else {})}
    (a.root / f"segment-{a.tag}.json").write_text(json.dumps(summary))
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
