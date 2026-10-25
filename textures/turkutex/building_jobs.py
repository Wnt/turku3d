"""One strict-schema label call per building; one unique improvement per street wall."""
from __future__ import annotations

import json
from pathlib import Path

from .jobs import _views
from .paths import PROMPTS_DIR, SCHEMAS_DIR
from .streetview.facades import load_facades


def reference_matches(facade, root: Path) -> bool:
    path = root / facade.id / "refs.json"
    if not path.is_file():
        return False
    recorded = json.loads(path.read_text()).get("facade")
    current = facade.to_feature()
    if not recorded or recorded["geometry"] != current["geometry"]:
        return False
    return all(recorded["properties"].get(k) == current["properties"].get(k)
               for k in ("height_m", "base_h", "normal"))


def groups(facades):
    result = {}
    for f in facades:
        if f.props.get("street_facing", True):
            result.setdefault(str(f.props.get("building_id", f.id)), []).append(f)
    return result


def label_jobs(facades_path: Path, refs_root: Path) -> list[dict]:
    jobs = []
    for bid, facades in groups(load_facades(facades_path)).items():
        if not all(reference_matches(f, refs_root) for f in facades):
            continue
        images, descriptions = [], []
        for f in facades:
            d = refs_root / f.id
            if not (d / "refs.json").exists():
                continue
            views = _views(d)
            if not views:
                continue
            files = [str(d / v["files"]["outline"]) for v in views]
            images.extend(files)
            descriptions.append({"facade_id": f.id, "width_m": f.length, "height_m": f.height_m,
                                 "geometry_bays": f.props.get("bay_count"), "properties": f.props,
                                 "views": [{"file": Path(i).name, "part": v["part"], "date": v["date"]}
                                           for i, v in zip(files, views)]})
        if not images:
            continue
        prompt = ("Label ONE building in Turku, Finland from the attached reference crops. Do not generate images "
                  "or run commands. Return only the strict schema JSON, all strings in English. "
                  "Each red outline marks one target wall; never describe the neighbouring building. "
                  "Return building_id exactly as supplied and one facade object for each facade_id listed below. "
                  "Use the image filename for each views[].view. Mark a crop unusable only if the target wall is MOSTLY "
                  "hidden, is the wrong building, or its architecture cannot be read. Minor foreground objects or shadows "
                  "do not make a reference unusable: the improvement step removes those. Colours are unshadowed surface colours. Bay counts from photos are only priors: "
                  "geometry_bays and height_m override them in downstream work. Do not invent certainty.\n" +
                  json.dumps({"building_id": bid, "facades": descriptions}, ensure_ascii=False))
        jobs.append({"id": f"{bid}.label", "prompt": prompt, "images": images,
                     "schema": str(SCHEMAS_DIR / "building_label.schema.json"), "expect_images": 0,
                     "timeout": 900, "prompt_version": "building-label-v1",
                     "meta": {"building_id": bid, "kind": "building_label",
                              "facade_ids": [d["facade_id"] for d in descriptions]}})
    return jobs


def latest(path: Path) -> dict:
    result = {}
    if path.exists():
        text = path.read_text()
        lines = text.splitlines()
        for index, line in enumerate(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not text.endswith("\n"):
                    break  # Another stage may be appending its final record right now.
                raise
            result[row["id"]] = row
    return result


def labels(path: Path) -> dict:
    result = {}
    for record in latest(path).values():
        if record.get("ok") and record.get("answer"):
            for label in record["answer"].get("facades", []):
                if label["facade_id"] in record["meta"]["facade_ids"]:
                    result[label["facade_id"]] = label
    return result


def improve_jobs(facades_path: Path, refs_root: Path, labels_out: Path) -> list[dict]:
    specs = labels(labels_out)
    fragments = {k: (PROMPTS_DIR / "fragments" / f"{k}.md").read_text().strip()
                 for k in ("style", "albedo_rules", "negatives")}
    jobs = []
    for f in load_facades(facades_path):
        if not f.props.get("street_facing", True) or f.id not in specs:
            continue
        label = specs[f.id]
        d = refs_root / f.id
        if not reference_matches(f, refs_root):
            continue
        usable = {Path(v["view"]).stem.removesuffix("_outline") for v in label["views"] if v["usable"]}
        views = [v for v in _views(d) if v["view_id"] in usable][:4]
        # Never silently turn a rejected view into an accepted reference.
        if not views:
            continue
        images = [str(d / v["files"]["rectified"]) for v in views]
        best = max(views, key=lambda v: (v["part"] == "full", v["score"]))
        images += [str(d / best["files"]["outline"])]
        count = f.props.get("bay_count")
        extent = (f"Cover the WHOLE facade edge to edge: {f.length:.3f} m wide and {f.height_m:.3f} m high. "
                  "Preserve unique doors, corners and ornaments; do not replace the wall with a repeatable strip. "
                  "The geometrical dimensions above are authoritative, not estimated dimensions in the label. ")
        if count is not None:
            extent += f"Geometry requires exactly {int(count)} bays; override the photo count prior."
        else:
            extent += "No geometry bay count is available yet; retain the reference rhythm without claiming a verified count."
        jobs.append({"id": f"{f.id}.improve", "prompt_file": str(PROMPTS_DIR / "facade_improve.md"),
                     "vars": {"view_list": "\n".join(
                         f"- {v['files']['rectified']}: {v['part']} rectified view, u={v['section_m']} metres" for v in views) +
                         f"\n- {best['files']['outline']}: best source perspective with target in red",
                         "address": f.props.get("address"), "year": f.props.get("year"),
                         "label_json": json.dumps(label), "extent": extent,
                         "aspect": f"{f.length / f.height_m:.3f}:1", **fragments},
                     "images": images, "expect_images": 1, "timeout": 900,
                     "prompt_version": "facade-whole-v1", "meta": {
                         "kind": "improve", "facade_id": f.id,
                         "building_id": str(f.props.get("building_id", f.id)),
                         "width_m": f.length, "height_m": f.height_m, "bay_count": count,
                         "bay_count_source": "geometry" if count is not None else "unavailable",
                         "tile": "none", "views_used": [v["view_id"] for v in views]}})
    return jobs
