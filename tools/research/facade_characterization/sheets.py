"""Pair regenerated pilot crops with stored programs, without recompiling readings."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from review import render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--programs", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads((args.root / "chosen.json").read_text())
    raw = args.programs.read_bytes()
    programs = json.loads(raw)
    walls = {w["wall_id"]: w for b in programs for w in b["walls"]}
    assert {r["facade_id"] for r in rows} == set(walls), "Pilot wall selection changed"
    sheet = Image.new("RGB", (1400, len(rows) * 240), "#eeece8")
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(rows):
        wall = walls[row["facade_id"]]
        state = "REJECTED scaffold" if wall["observation"]["needs_review"] else "reviewed"
        draw.text(
            (8, i * 240 + 5),
            f"{i + 1:02d} {wall['wall_id']} | {wall['observation']['scope']} | {state}",
            fill="black",
        )
        with Image.open(args.root / "crops" / f"{row['chosen']['name']}.png") as crop:
            for j, im in enumerate((crop, render(wall))):
                thumb = ImageOps.contain(im.convert("RGB"), (680, 208))
                sheet.paste(thumb, (j * 700 + (700 - thumb.width) // 2, i * 240 + 28))
    sheet.save(args.root / "reviewed-contact.jpg", quality=94)
    (args.root / "programs.json").write_bytes(raw)
    (args.root / "sheet-provenance.json").write_text(
        json.dumps(
            {
                "programs_sha256": hashlib.sha256(raw).hexdigest(),
                "walls": len(rows),
                "views": sum(len(r["views"]) for r in rows),
                "programs_recompiled": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{len(rows)} stored wall programs paired with regenerated crops")


if __name__ == "__main__":
    main()
