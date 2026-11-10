"""Build the stand-alone soundboard directory from the candidate registry.

Usage: python -I make_board.py OUT_DIR
Reads candidates.py (registry), template.html, sections.html, summary.html and the tyre synthesiser core; encodes
every candidate to Ogg/Opus, verifies loop seams on the decoded file, draws spectrogram thumbnails and writes
OUT_DIR/index.html plus OUT_DIR/audio and OUT_DIR/img. Unchanged candidates are not re-encoded.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import sfx  # noqa: E402
import candidates  # noqa: E402

SR = sfx.SR
GUARD = 0.3


def spec_hash(spec: dict) -> str:
    parts = {k: v for k, v in spec.items() if k not in ("title", "desc", "rating")}
    parts["builder"] = 2
    if spec.get("loop"):
        parts["seam_metric"] = 2  # bump to rebuild everything after a change in this script
    for key in ("src", "copy"):
        if spec.get(key):
            stat = Path(spec[key]).stat()
            parts[key + "_stat"] = [stat.st_size, int(stat.st_mtime)]
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def build_item(spec: dict, out: Path, cache: dict, scratch: Path) -> dict:
    item_id = spec["id"]
    audio_path = out / "audio" / f"{item_id}.ogg"
    image_path = out / "img" / f"{item_id}.png"
    key = spec_hash(spec)
    cached = cache.get(item_id)
    if cached and cached["hash"] == key and audio_path.exists() and (image_path.exists() or not cached["item"].get("image")):
        item = cached["item"]
    else:
        item = {"id": item_id, "file": f"audio/{item_id}.ogg", "kind": spec.get("kind", "sfx")}
        loop = spec.get("loop")
        if spec.get("copy"):
            # The game's own file, byte for byte.
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(spec["copy"], audio_path)
            x = sfx.load(audio_path, mono=False)
            if loop:
                item["loop"] = [0, 0]  # the whole file, as the game loops it
        else:
            x = sfx.load(spec["src"], mono=False)
            if spec.get("mono"):
                x = np.repeat(x.mean(axis=1, keepdims=True), 2, axis=1)
            if isinstance(loop, (list, tuple)) and loop[0] == "take":
                x = sfx.make_loop(x, loop[1], loop[2], offset=loop[3] if len(loop) > 3 else 0.5)
            mode, level = spec.get("norm", ("peak", -3))
            x = sfx.normalise(x, -1.0, level) if mode == "rms" else sfx.normalise(x, level)
            if not loop:
                x = sfx.fade(x, 0.003, 0.02)
            start = end = None
            if loop:
                x, start, end = sfx.guard(x, GUARD)
                item["loop"] = [round(start, 6), round(end, 6)]
            temp = scratch / f"{item_id}.wav"
            sfx.save(temp, x)
            sfx.encode(temp, audio_path, spec.get("bitrate", "64k"), 1 if spec.get("mono") else None)
            temp.unlink()
            if loop:
                decoded = sfx.load(audio_path, mono=False)
                item["seam"] = sfx.seam_report(decoded, start, end)
        body = x
        if item.get("loop") and item["loop"][1] > 0:
            body = x[int(item["loop"][0] * SR):int(item["loop"][1] * SR)]
        item["seconds"] = round(len(body) / SR, 2)
        item["rms_db"] = round(sfx.db(sfx.rms(body)), 1)
        if spec.get("match_rms") is not None:
            item["gain"] = round(float(10 ** ((spec["match_rms"] - item["rms_db"]) / 20)), 3)
        if spec.get("image", item["kind"] == "sfx"):
            shown = body[: int(spec.get("image_seconds", 12) * SR)]
            sfx.spectrogram_png(shown, image_path, width=6.4, height=1.25, fmax=spec.get("fmax", 9000), dark=True)
            item["image"] = f"img/{item_id}.png"
            item["imageSize"] = [704, 138]
        item["bytes"] = audio_path.stat().st_size
        cache[item_id] = {"hash": key, "item": item}
    shown = dict(item)
    for field in ("title", "desc", "rating", "current"):
        if spec.get(field) is not None:
            shown[field] = spec[field]
    return shown


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    scratch = out.parent / "work" / "board-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cache_path = out.parent / "work" / "board-cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    groups, index, used = [], {}, set()
    for group in candidates.groups():
        subgroups = []
        for sub in group["subgroups"]:
            items = []
            for spec in sub["items"]:
                item = build_item(spec, out, cache, scratch)
                items.append(item)
                index[item["id"]] = item
                used.add(item["id"])
                cache_path.write_text(json.dumps(cache, indent=1))
            subgroups.append({"title": sub.get("title"), "note": sub.get("note"), "items": items})
        groups.append({"id": group["id"], "subgroups": subgroups})
    # Drop files of candidates that left the registry.
    for folder, suffix in (("audio", ".ogg"), ("img", ".png")):
        for path in (out / folder).glob("*" + suffix):
            if path.stem not in used and not path.stem.startswith("fig-"):
                path.unlink()
    for figure in candidates.figures():
        target = out / "img" / figure.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(figure, target)
    data = {"version": time.strftime("%Y-%m-%d %H:%M"), "groups": groups, "index": index}
    html = (HERE / "template.html").read_text()
    html = html.replace("<!--__SECTIONS__-->", (HERE / "sections.html").read_text())
    html = html.replace("<!--__SUMMARY__-->", (HERE / "summary.html").read_text())
    html = html.replace("<!--__SOURCES__-->", candidates.sources_table())
    html = html.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    html = html.replace("/*__TYRE_CORE__*/''", json.dumps((HERE.parent / "tyre" / "core.js").read_text()).replace("</", "<\\/"))
    extra = HERE / "extra.js"
    html = html.replace("/*__EXTRA_JS__*/", extra.read_text() if extra.exists() else "")
    (out / "index.html").write_text(html)
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    seams = {i["id"]: i["seam"] for i in index.values() if "seam" in i}
    report = {"items": len(index), "bytes": total, "seams": seams}
    (out.parent / "work" / "board-report.json").write_text(json.dumps(report, indent=1))
    print(f"{len(index)} items, {total / 1e6:.1f} MB")
    for name, seam in seams.items():
        flag = "" if seam["jump_vs_p999"] < 1.5 and seam["seam_high_db_vs_body"] < 6 else "  <-- check"
        print(f"  seam {name}: jump {seam['jump_vs_p999']:.2f}x, high band {seam['seam_high_db_vs_body']:+.1f} dB{flag}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
