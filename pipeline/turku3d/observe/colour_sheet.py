"""A contact sheet of measured colours: one row a building, for judging the measurement at a glance.

    python -m turku3d.observe colour --sheet OUT.jpg --building KEY... --near E,N   (packets prepared by `views`)

Row: the whole house from a panorama | the wall crops that were sampled, the sampled stretch outlined | swatches:
each strip's pixel colour, the combined pixel colour, the calibrated paint colour, the stored `measured` block.
A sheet shows photographs: it stays outside Git.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from . import colour

ROW_H = 250
CONTEXT_W = 320
CROP_W = 330
SWATCH_W = 330
CROPS = 3
INK, PAPER, RULE = (20, 20, 20), (250, 250, 248), (200, 200, 200)


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = min(width / image.width, height / image.height)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)


def _swatch(draw: ImageDraw.ImageDraw, box, fill: str, label: str):
    draw.rectangle(box, fill=fill, outline=INK)
    draw.text((box[0], box[3] + 2), label, fill=INK)


def row(packet: Path) -> Image.Image | None:
    """One building's row, or None when it has no views record."""
    if not (packet / "views.json").exists():
        return None
    record = json.loads((packet / "views.json").read_text())
    walls = {w["id"]: w for w in record["walls"]}
    sample = colour.of_packet(packet)
    sheet = Image.new("RGB", (CONTEXT_W + CROPS * CROP_W + SWATCH_W, ROW_H), PAPER)
    draw = ImageDraw.Draw(sheet)
    register = record.get("register", {})
    draw.text((6, 4), f"{register.get('address') or '?'}  ·  {record['building']}  ·  {register.get('year') or '?'}", fill=INK)
    context = packet / "context-1.jpg"
    if context.exists():
        sheet.paste(_fit(Image.open(context).convert("RGB"), CONTEXT_W - 10, ROW_H - 26), (5, 20))
    else:
        draw.text((6, 40), "no panorama sees it", fill=INK)
    by_file = {s["file"]: s for s in (sample or {}).get("samples", [])}
    crops = sorted(record["crops"], key=lambda c: -(by_file.get(Path(c["file"]).name, {}).get("share", 0.0)
                                                    * (c["x1_m"] - c["x0_m"])))
    for k, crop in enumerate(crops[:CROPS]):
        path = packet / Path(crop["file"]).name
        if not path.exists():
            continue
        picture = Image.fromarray(colour.picture_of(path))
        box = colour.strip_box(crop, walls[crop["wall"]]) if crop["wall"] in walls else None
        if box:
            ImageDraw.Draw(picture).rectangle([box[1].start, box[0].start, box[1].stop, box[0].stop],
                                              outline=(255, 0, 255), width=max(2, picture.width // 250))
        small = _fit(picture, CROP_W - 10, ROW_H - 42)
        x = CONTEXT_W + k * CROP_W + 5
        sheet.paste(small, (x, 20))
        pano = crop["panorama"]
        s = by_file.get(path.name)
        note = f"{pano['provider'][:6]} {str(pano['date'])[:7]} {crop['distance_m']:.0f} m {crop['oblique_deg']:.0f}°"
        draw.text((x, ROW_H - 21), note + (f"  share {s['share']:.2f}" if s else "  not sampled"), fill=INK)
        if s:
            draw.rectangle([x + CROP_W - 40, ROW_H - 22, x + CROP_W - 12, ROW_H - 4], fill=colour.to_hex(s["lab"]), outline=INK)
    x = CONTEXT_W + CROPS * CROP_W + 8
    if sample:
        block = colour.measured(sample)
        _swatch(draw, [x, 24, x + 90, 114], colour.to_hex(sample["lab"]), "pixels " + colour.to_hex(sample["lab"]))
        _swatch(draw, [x + 110, 24, x + 200, 114], block["colour"], "wall " + block["colour"])
        draw.text((x, 140), f"share {sample['share']}  spread {sample['spread']}", fill=INK)
        draw.text((x, 156), f"agreement {sample['agreement']}  strips {sample['strips']}  conf {sample['conf']}", fill=INK)
        for k, s in enumerate(sample["samples"][:9]):
            draw.rectangle([x + k * 34, 180, x + k * 34 + 28, 208], fill=colour.to_hex(s["lab"]), outline=INK)
        draw.text((x, 212), "each strip's pixel colour", fill=INK)
    else:
        draw.text((x, 60), "no wall colour: too little plain wall", fill=INK)
    draw.line([0, ROW_H - 1, sheet.width, ROW_H - 1], fill=RULE)
    return sheet


def sheet(packets: list[Path], out: Path, title: str = "") -> dict:
    rows = [r for r in (row(p) for p in packets) if r is not None]
    if not rows:
        return {"rows": 0}
    head = 26
    page = Image.new("RGB", (rows[0].width, head + ROW_H * len(rows)), PAPER)
    ImageDraw.Draw(page).text((6, 6), title + "   house | sampled wall crops (magenta: the sampled stretch) | "
                              "pixel colour, calibrated wall colour, per-strip colours", fill=INK)
    for k, r in enumerate(rows):
        page.paste(r, (0, head + k * ROW_H))
    out.parent.mkdir(parents=True, exist_ok=True)
    page.save(out, quality=85)
    return {"rows": len(rows), "sheet": str(out), "size": list(page.size)}


# --- parts: house | chosen crop | part map | swatches, sketch and the candidate panoramas ---

PART_ROW_H = 300
SKETCH = (190, 150)
IN_FRONT_INK, OFF_WALL = (70, 110, 70), 0.3


def _rgb(hex_colour: str) -> tuple:
    return tuple(int(hex_colour[k:k + 2], 16) for k in (1, 3, 5))


def part_map(picture, row: dict):
    """The crop with every measured part painted flat in its measured colour; what stands in front of the wall
    in green-grey, everything else dimmed."""
    import numpy as np

    from . import colour_parts

    index = row["map"]
    out = (picture.astype(np.float32) * OFF_WALL).astype(np.uint8)
    out[index == 255] = IN_FRONT_INK
    for k, name in enumerate(colour_parts.PARTS, 1):
        if name in row["parts"]:
            out[index == k] = _rgb(colour.to_hex(np.array(row["parts"][name]["lab"])))
    return Image.fromarray(out)


def sketch(parts: dict) -> Image.Image:
    """A flat-shaded house front in the measured colours (pixel colours): plinth, cladding zones, corner boards,
    two windows with frames, a door, the roof."""
    w, h = SKETCH
    image = Image.new("RGB", (w, h), (225, 230, 235))
    draw = ImageDraw.Draw(image)
    get = lambda name, default=None: parts[name]["pixel"] if name in parts else default  # noqa: E731
    wall = get("wall", "#888888")
    left, right, eaves, foot = 22, w - 22, 58, h - 14
    plinth_h = 14 if "plinth" in parts else 0
    draw.polygon([(left - 12, eaves), (w // 2, 12), (right + 12, eaves)], fill=get("roof", "#555555"), outline=INK)
    gable = get("wall2") if parts.get("wall2", {}).get("zone") == "gable" else wall
    draw.polygon([(left, eaves), (w // 2, 24), (right, eaves)], fill=gable)
    draw.rectangle([left, eaves, right, foot - plinth_h], fill=wall)
    if "wall2" in parts and parts["wall2"].get("zone") in ("upper", "lower"):
        middle = (eaves + foot - plinth_h) // 2
        box = [left, eaves, right, middle] if parts["wall2"]["zone"] == "upper" else [left, middle, right, foot - plinth_h]
        draw.rectangle(box, fill=get("wall2"))
    if plinth_h:
        draw.rectangle([left, foot - plinth_h, right, foot], fill=get("plinth"))
    trim = get("trim") or get("frame")
    if trim:
        for x in (left, right - 4):
            draw.rectangle([x, eaves, x + 4, foot - plinth_h], fill=trim)
        draw.line([(left - 12, eaves), (right + 12, eaves)], fill=trim, width=3)
    frame = get("frame") or trim or "#eeeeee"
    for x in (left + 22, right - 50):
        draw.rectangle([x, eaves + 16, x + 28, eaves + 46], fill=frame)
        draw.rectangle([x + 4, eaves + 20, x + 24, eaves + 42], fill=(50, 60, 70))
    if "door" in parts:
        draw.rectangle([w // 2 - 9, foot - plinth_h - 40, w // 2 + 9, foot - plinth_h], fill=get("door"), outline=frame)
    return image


def parts_row(packet: Path, tag: str, overcast: dict | None = None) -> tuple[Image.Image, dict] | None:
    from . import colour_parts

    result = colour_parts.of_packet(packet, tag, overcast, keep_maps=True)
    if result is None:
        return None
    width = CONTEXT_W + 2 * 400 + 560
    sheet = Image.new("RGB", (width, PART_ROW_H), PAPER)
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 4), f"{result['address'] or '?'}  ·  {result['building']}  ·  {result['year'] or '?'}", fill=INK)
    context = packet / "context-1.jpg"
    if context.exists():
        sheet.paste(_fit(Image.open(context).convert("RGB"), CONTEXT_W - 10, PART_ROW_H - 30), (5, 22))
    rows = result["candidates"]
    x = CONTEXT_W + 2 * 400 + 8
    if not rows:
        draw.text((CONTEXT_W + 8, 60), "no panorama sees a wall of it", fill=INK)
    else:
        best = rows[0]
        picture = colour.picture_of(packet / best["file"])
        for k, image in enumerate((Image.fromarray(picture), part_map(picture, best))):
            sheet.paste(_fit(image, 390, PART_ROW_H - 46), (CONTEXT_W + k * 400 + 5, 22))
        pano = best["panorama"]
        front = ", ".join(f"{name} {share:.0%}" for name, share in best["in_front"].items()) or "nothing in front"
        draw.text((CONTEXT_W + 5, PART_ROW_H - 22),
                  f"chosen of {len(rows)}: {pano['provider']} {pano['date']} {best['distance_m']:.0f} m "
                  f"{best['oblique_deg']:.0f}°, wall free {best['visible']:.0%} ({front})", fill=INK)
        for k, (name, part) in enumerate(result["parts"].items()):
            px, py = x + (k % 4) * 92, 22 + (k // 4) * 84
            draw.rectangle([px, py, px + 58, py + 44], fill=part["pixel"], outline=INK)
            draw.rectangle([px + 60, py, px + 82, py + 44], fill=part["colour"], outline=INK)
            extra = part.get("zone") or (f"{part['height_m']} m" if "height_m" in part else "")
            draw.text((px, py + 46), f"{name} {extra}", fill=INK)
            draw.text((px, py + 58), f"{part['pixel']} c{part['conf']}", fill=INK)
        sheet.paste(sketch(result["parts"]), (x + 368, 22))
        draw.text((x + 368, 22 + SKETCH[1] + 2), "sketch in the measured colours", fill=INK)
        for k, r in enumerate(rows[:6]):
            p = r["panorama"]
            cloud = "?" if r["overcast"] is None else f"{r['overcast']:.2f}"
            wall_hex = colour.to_hex(__import__("numpy").array(r["parts"]["wall"]["lab"])) if "wall" in r["parts"] else None
            ty = 196 + k * 15
            draw.text((x, ty), f"{k + 1}. {p['provider'][:6]} {str(p['date'])[:7]} {r['wall'][-3:]} {r['distance_m']:.0f} m "
                      f"{r['oblique_deg']:.0f}° free {r['visible']:.2f} overcast {cloud} score {r['score']:.2f}", fill=INK)
            if wall_hex:
                draw.rectangle([x + 350, ty + 1, x + 364, ty + 12], fill=wall_hex, outline=INK)
    draw.line([0, PART_ROW_H - 1, width, PART_ROW_H - 1], fill=RULE)
    for r in rows:
        r.pop("map", None)
    return sheet, result


def parts_sheet(packets: list[Path], out: Path, title: str = "", tag: str = "m2f", overcast: dict | None = None) -> dict:
    """The sheet and, beside it, `<out>.jsonl`: one line a building with its candidates and combined parts."""
    done = [r for r in (parts_row(p, tag, overcast) for p in packets) if r is not None]
    if not done:
        return {"rows": 0}
    head = 40
    page = Image.new("RGB", (done[0][0].width, head + PART_ROW_H * len(done)), PAPER)
    draw = ImageDraw.Draw(page)
    draw.text((6, 4), title, fill=INK)
    draw.text((6, 20), "house | chosen wall crop | parts painted in their measured colours (green-grey: in front of the wall) | "
              "swatches: pixel colour, narrow: calibrated paint colour; c = confidence | candidate panoramas, best first", fill=INK)
    for k, (image, _) in enumerate(done):
        page.paste(image, (0, head + k * PART_ROW_H))
    out.parent.mkdir(parents=True, exist_ok=True)
    page.save(out, quality=85)
    out.with_suffix(".jsonl").write_text("".join(json.dumps(result, ensure_ascii=False) + "\n" for _, result in done))
    return {"rows": len(done), "sheet": str(out), "size": list(page.size),
            "with_wall": sum("wall" in r["parts"] for _, r in done)}
