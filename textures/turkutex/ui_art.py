"""Shop menu art (game/src/ui/README.md): Codex image jobs and the WebP encode step.

    python -m turkutex.ui_art jobs  JOBS.jsonl          # one image_gen job per ART entry
    python -m turkutex codex run JOBS.jsonl --out RUN   # the shared quota-guarded, cached runner
    python -m turkutex.ui_art encode RUN DEST           # RUN/out.jsonl -> DEST/<name>.webp

Backgrounds are centre-cropped to 16:9 and scaled to 1280x720, icons to a 256x256 square. Each WebP is
re-encoded at falling quality until it fits MAX_BYTES.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

PROMPT_VERSION = "ui-art-v1"
MAX_BYTES = 200 * 1024
SIZES = {"background": (1280, 720), "icon": (256, 256)}
QUALITIES = (86, 80, 74, 68, 62, 56, 50, 44, 38)

STYLE = (
    "Style: flat-shaded vector illustration for a video game menu, clean shapes, soft cel shading, no outlines "
    "thicker than 2 px, limited palette of deep navy #141a2e, slate blue #2c3550, warm amber #f2a541, "
    "brick red #c8553d, teal #3fa7a0 and off-white #efe6d8. Dark humour mood, Turku, Finland, year 2027. "
    "Absolutely no text, letters, numbers, logos, signatures or watermarks anywhere in the image."
)
FRAMING = {
    "background": "Wide 16:9 landscape composition (draw it as a wide landscape image), "
    "keep the left half calm and dark because a menu panel will cover it; put the main subject on the right half.",
    "icon": "Square composition: one single object, centred, filling about 70 % of the frame, "
    "on a plain flat background of solid #1b2133 with no floor, no scenery and no drop shadow.",
}


@dataclass(frozen=True)
class Art:
    name: str
    kind: str
    subject: str


ART: tuple[Art, ...] = (
    Art(
        "bg_market",
        "background",
        "A hidden black-market stall at night under the stone arch of the Auransilta "
        "bridge in Turku: granite riverside quay, the bridge arch overhead, a folding table with a cash box and a "
        "picnic cooler, one bare light bulb, wet cobbles reflecting the light, the dark Aura river behind. "
        "A hooded seller seen from behind, no visible face.",
    ),
    Art(
        "bg_pizzeria",
        "background",
        "The counter of a small old family pizzeria in Turku in the evening: tiled "
        "wall, a glowing wood-fired oven behind the counter, stacked pizza boxes, an insulated delivery bag on the "
        "counter, an empty blank menu board.",
    ),
    Art(
        "bg_dealer",
        "background",
        "A small used-vehicle lot on the Aura riverbank in Turku at dusk: a row of "
        "second-hand compact cars and two bicycles by the river embankment, string lights, a tiny sales cabin, "
        "trees and old stone buildings across the river.",
    ),
    Art(
        "icon_permit",
        "icon",
        "A laminated courier work permit card with a blank photo square and a lanyard, "
        "the card face blank.",
    ),
    Art(
        "icon_plates",
        "icon",
        "Two blank vehicle licence plates held together with a rubber band, plates blank.",
    ),
    Art(
        "icon_kidney",
        "icon",
        "A cartoon human kidney resting on ice cubes inside an open red-and-white picnic "
        "cooler, comic and not gory.",
    ),
    Art(
        "icon_pizza_box",
        "icon",
        "A closed cardboard pizza delivery box with three wavy steam lines rising.",
    ),
    Art("icon_slice", "icon", "A single slice of pizza with melted cheese and pepperoni."),
    Art(
        "icon_scooter",
        "icon",
        "A small second-hand moped scooter with a red pizza delivery box on the rear rack, side view.",
    ),
    Art("icon_car", "icon", "A small dented second-hand hatchback car, three-quarter front view."),
    Art(
        "icon_coins",
        "icon",
        "A small pile of gold and silver coins with two folded banknotes, no numbers.",
    ),
    Art(
        "icon_warning",
        "icon",
        "A smartphone whose screen shows one large exclamation-mark alert bubble.",
    ),
)


def prompt(art: Art) -> str:
    return (
        "Use built-in image_gen exactly once to generate one image. Do not run commands, write or copy files. "
        "Reply in English with a one-line note.\n"
        f"Subject: {art.subject}\n{FRAMING[art.kind]}\n{STYLE}"
    )


def write_jobs(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for art in ART:
            job = {
                "id": art.name,
                "prompt": prompt(art),
                "expect_images": 1,
                "timeout": 900,
                "prompt_version": PROMPT_VERSION,
                "meta": {"kind": "library", "ui_art": art.kind},
            }
            f.write(json.dumps(job) + "\n")


def fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Centre-crop to the target aspect ratio, then scale to `size`."""
    w, h = image.size
    tw, th = size
    if w * th > h * tw:
        cw, ch = round(h * tw / th), h
    else:
        cw, ch = w, round(w * th / tw)
    left, top = (w - cw) // 2, (h - ch) // 2
    return image.crop((left, top, left + cw, top + ch)).resize(size, Image.Resampling.LANCZOS)


def encode_webp(image: Image.Image, dest: Path) -> int:
    """Writes the highest quality in QUALITIES that fits MAX_BYTES; returns the byte size."""
    for quality in QUALITIES:
        image.save(dest, "WEBP", quality=quality, method=6)
        size = dest.stat().st_size
        if size <= MAX_BYTES:
            return size
    raise ValueError(f"{dest.name}: {size} bytes at quality {QUALITIES[-1]} exceeds {MAX_BYTES}")


def primary_images(run: Path) -> dict[str, Path]:
    """The primary PNG of each successful job (last record per id wins, as in the runner)."""
    records: dict[str, dict] = {}
    for line in (run / "out.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            records[rec["id"]] = rec
    out: dict[str, Path] = {}
    for rid, rec in records.items():
        if rec.get("ok") and rec.get("images"):
            out[rid] = Path(rec.get("primary") or rec["images"][0]["path"])
    return out


def encode(run: Path, dest: Path) -> dict[str, int]:
    dest.mkdir(parents=True, exist_ok=True)
    images = primary_images(run)
    missing = [a.name for a in ART if a.name not in images]
    if missing:
        raise SystemExit(f"no image for {', '.join(missing)} in {run}/out.jsonl")
    sizes = {}
    for art in ART:
        with Image.open(images[art.name]) as image:
            sizes[art.name] = encode_webp(
                fit(image.convert("RGB"), SIZES[art.kind]), dest / f"{art.name}.webp"
            )
    return sizes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m turkutex.ui_art",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("jobs").add_argument("jobs", type=Path)
    enc = sub.add_parser("encode")
    enc.add_argument("run", type=Path)
    enc.add_argument("dest", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "jobs":
        write_jobs(args.jobs)
        print(f"{len(ART)} jobs -> {args.jobs}")
    else:
        for name, size in encode(args.run, args.dest).items():
            print(f"{name}.webp {size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
