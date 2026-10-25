"""Build Codex jobs.jsonl files for facades from reference crops (turkutex refs output).

  turkutex jobs label   FACADES.geojson --refs DIR --out label_jobs.jsonl
  turkutex jobs improve FACADES.geojson --refs DIR --labels RUN/out.jsonl --out improve_jobs.jsonl
                        [--strip-max-aspect 3.2] [--prompt-version v1]

label:   strict JSON (schemas/facade_label.schema.json) from the *_outline crops.
improve: one image per facade: the usable plain crops + the best outline crop + the label JSON.
         Facades longer than strip-max-aspect x height get a repeatable section of whole bays
         (post-processed with --tile x); shorter ones get the whole facade (--tile none).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .paths import PROMPTS_DIR, SCHEMAS_DIR
from .streetview.facades import load_facades

FRAG = PROMPTS_DIR / "fragments"


def _views(refs_dir: Path) -> list[dict]:
    s = json.loads((refs_dir / "refs.json").read_text())
    return [json.loads((refs_dir / f"{v}.json").read_text()) for v in s["views"]]


def label_jobs(facades_path: Path, refs_root: Path, prompt_version: str = "v1") -> list[dict]:
    jobs = []
    for f in load_facades(facades_path):
        d = refs_root / f.id
        if not (d / "refs.json").exists():
            continue
        vs = _views(d)
        imgs = [str(d / v["files"]["outline"]) for v in vs]
        lst = "\n".join(f"- {Path(i).name}: {v['part']} view, {v['distance_m']} m away, "
                        f"{v['view_angle_deg']} deg off-normal, captured {v['date']}" for i, v in zip(imgs, vs))
        jobs.append({"id": f"{f.id}.label", "prompt_file": str(PROMPTS_DIR / "facade_label.md"),
                     "vars": {"view_list": lst, "address": f.props.get("address"), "floors": f.props.get("floors"),
                              "material": f.props.get("material"), "year": f.props.get("year"),
                              "length_m": round(f.length, 1), "height_m": f.height_m},
                     "images": imgs, "schema": str(SCHEMAS_DIR / "facade_label.schema.json"),
                     "expect_images": 0, "timeout": 600, "prompt_version": f"label-{prompt_version}",
                     "meta": {"facade_id": f.id, "kind": "label", "views": [v["view_id"] for v in vs]}})
    return jobs


def improve_jobs(facades_path: Path, refs_root: Path, labels_out: Path, strip_max_aspect: float = 3.2,
                 prompt_version: str = "v1") -> list[dict]:
    labels = {}
    for line in open(labels_out):
        r = json.loads(line)
        if r.get("ok") and r.get("answer"):
            labels[r["meta"]["facade_id"]] = r["answer"]
    frags = {k: (FRAG / f"{k}.md").read_text().strip() for k in ("style", "albedo_rules", "negatives")}
    jobs = []
    for f in load_facades(facades_path):
        d = refs_root / f.id
        lab = labels.get(f.id)
        if lab is None or not (d / "refs.json").exists():
            continue
        vs = _views(d)
        usable = {Path(v["view"]).name.split(".")[0].replace("_outline", "") for v in lab["views"] if v["usable"]}
        use = [v for v in vs if v["view_id"] in usable] or vs[:1]
        best = max(use, key=lambda v: (v["part"] == "full", v["score"]))
        imgs = [str(d / v["files"]["crop"]) for v in use] + [str(d / best["files"]["outline"])]
        lst = "\n".join(f"- {v['files']['crop']}: {v['part']} view ({'whole facade' if v['part'] == 'full' else 'a section of it'}), "
                        f"{v['view_angle_deg']} deg off-normal, captured {v['date']}" for v in use)
        lst += f"\n- {best['files']['outline']}: the same as {best['files']['crop']} with the target facade outlined in red"
        H = float(lab.get("wall_height_m") or f.height_m) or f.height_m
        H = f.height_m if not (0.5 * f.height_m < H < 2 * f.height_m) else H
        L = f.length
        bays = max(1, int(lab.get("bays") or round(L / 3.5)))
        if L / H <= strip_max_aspect:
            mode, w = "whole", L
            extent = (f"Cover the WHOLE facade, edge to edge: about {L:.1f} m wide and {H:.1f} m high "
                      f"(ground line to the top of the cornice/eaves), with all {bays} bays.")
        else:
            bay_w = L / bays
            n = max(2, min(bays, int(strip_max_aspect * H / bay_w)))
            w = n * bay_w
            mode = "strip"
            extent = (f"The facade is {L:.1f} m long with about {bays} bays of {bay_w:.1f} m. Produce a REPEATABLE "
                      f"SECTION of exactly {n} typical bays, about {w:.1f} m wide and {H:.1f} m high (ground line to "
                      f"the top of the eaves), with the left and right image edges cut through the middle of the wall "
                      f"between two bays so the section repeats seamlessly side by side; no corner boards or building ends.")
        aspect = f"{w / H:.2f}:1"
        jobs.append({"id": f"{f.id}.improve", "prompt_file": str(PROMPTS_DIR / "facade_improve.md"),
                     "vars": {"view_list": lst, "address": f.props.get("address"), "year": f.props.get("year"),
                              "label_json": json.dumps({k: v for k, v in lab.items() if k not in ("views", "notes")}),
                              "extent": extent, "aspect": aspect, **frags},
                     "images": imgs, "expect_images": 1, "timeout": 900,
                     "prompt_version": f"improve-{prompt_version}",
                     "meta": {"facade_id": f.id, "kind": "improve", "mode": mode, "width_m": round(w, 2),
                              "height_m": round(H, 2), "bays": bays, "aspect": round(w / H, 3),
                              "views_used": [v["view_id"] for v in use],
                              "tile": "x" if mode == "strip" else "none"}})
    return jobs


def _write(jobs: list[dict], out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs))
    print(f"{len(jobs)} jobs -> {out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="turkutex jobs", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["label", "improve"])
    ap.add_argument("facades", type=Path)
    ap.add_argument("--refs", type=Path, required=True)
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--strip-max-aspect", type=float, default=3.2)
    ap.add_argument("--prompt-version", default="v1")
    a = ap.parse_args(argv)
    if a.kind == "label":
        _write(label_jobs(a.facades, a.refs, a.prompt_version), a.out)
    else:
        if not a.labels:
            ap.error("--labels is required for improve")
        _write(improve_jobs(a.facades, a.refs, a.labels, a.strip_max_aspect, a.prompt_version), a.out)
    return 0
