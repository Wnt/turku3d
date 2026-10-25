"""Pilot step 6: small review previews into docs/pilot/textures/ + library/manifest.jsonl entries.

usage: .venv/bin/python pilot/make_previews.py [PILOT_DIR]   (default <data>/textures/pilot)
Previews are small JPEGs (<= 1024 px wide). Street View crops are shrunk to 480 px and only used for
side-by-side review; full-size reference imagery stays in the data dir and is never committed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from turkutex.paths import REPO_ROOT, TEXTURES_DIR, textures_data_dir

PILOT = Path(sys.argv[1]) if len(sys.argv) > 1 else textures_data_dir("pilot")
REFS = textures_data_dir("refs")
DOCS = REPO_ROOT / "docs" / "pilot" / "textures"
MANIFEST = TEXTURES_DIR / "library" / "manifest.jsonl"


def thumb(src: Path, dst: Path, w: int, q: int = 82):
    im = Image.open(src).convert("RGB")
    im.thumbnail((w, w * 4))
    im.save(dst, quality=q)
    return dst


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    improve = {
        json.loads(x)["meta"]["facade_id"]: json.loads(x)
        for x in (PILOT / "run_improve" / "out.jsonl").read_text().splitlines()
    }
    label = {
        json.loads(x)["meta"]["facade_id"]: json.loads(x)
        for x in (PILOT / "run_label" / "out.jsonl").read_text().splitlines()
    }
    usage = [json.loads(x) for x in (PILOT / "usage_log.jsonl").read_text().splitlines()]
    summary = {"usage_log": usage, "facades": {}}
    manifest_lines = []
    for fid, rec in improve.items():
        post = json.loads((PILOT / "post" / fid / "post.json").read_text())
        refs = json.loads((REFS / fid / "refs.json").read_text())
        # reference strip: shrunk outline crops side by side
        crops = [Image.open(REFS / fid / f"{v}_outline.jpg").convert("RGB") for v in refs["views"]]
        for c in crops:
            c.thumbnail((480, 480))
        strip = Image.new(
            "RGB",
            (sum(c.width for c in crops) + 8 * (len(crops) - 1), max(c.height for c in crops)),
            "white",
        )
        x = 0
        for c in crops:
            strip.paste(c, (x, 0))
            x += c.width + 8
        strip.save(DOCS / f"{fid}_refs.jpg", quality=80)
        thumb(Path(rec["primary"]), DOCS / f"{fid}_codex_raw.jpg", 1024)
        thumb(PILOT / "post" / fid / "albedo.png", DOCS / f"{fid}_albedo.jpg", 1024)
        if post["tile"] != "none":
            thumb(PILOT / "post" / fid / "tile2x2.jpg", DOCS / f"{fid}_tile2x2.jpg", 1024)
        maps = [
            Image.open(PILOT / "post" / fid / f"{k}.png").convert("RGB")
            for k in ("normal", "height", "orm")
        ]
        for m in maps:
            m.thumbnail((512, 512))
        sheet = Image.new("RGB", (512, sum(m.height for m in maps) + 40), "white")
        y = 0
        dr = ImageDraw.Draw(sheet)
        for k, m in zip(
            ("normal (DeepBump)", "height (DeepBump)", "ORM (heuristic)"), maps, strict=True
        ):
            sheet.paste(m, (0, y))
            dr.text((6, y + 4), k, fill=(255, 255, 0))
            y += m.height + 20
        sheet.save(DOCS / f"{fid}_maps.jpg", quality=80)
        lab = label[fid]
        summary["facades"][fid] = {
