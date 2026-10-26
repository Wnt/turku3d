"""One strict-schema label call per building; one unique improvement per street wall."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .jobs import _views
from .paths import PROMPTS_DIR, SCHEMAS_DIR
from .streetview.facades import load_facades


def layout_guide(facade, directory: Path) -> Path:
    """A deterministic geometry guide, not another photo reference."""
    from PIL import Image, ImageDraw

    ratio = facade.length / facade.height_m
    width, height = (1536, 1024) if ratio >= 1 else (1024, 1536)
    scale = min((width - 80) / facade.length, (height - 80) / facade.height_m)
    w, h = max(2, round(facade.length * scale)), max(2, round(facade.height_m * scale))
    image = Image.new("RGB", (width, height), "white")
    x, y = (width - w) // 2, (height - h) // 2
    ImageDraw.Draw(image).rectangle((x, y, x + w - 1, y + h - 1), fill=(160, 160, 160))
    path = directory / f"{facade.id}_layout_{w}x{h}.png"
    if not path.exists():
        image.save(path)
    return path


def reference_matches(facade, root: Path) -> bool:
    path = root / facade.id / "refs.json"
    if not path.is_file():
        return False
    recorded = json.loads(path.read_text()).get("facade")
    current = facade.to_feature()
    if not recorded or recorded["geometry"] != current["geometry"]:
        return False
    return all(
        recorded["properties"].get(k) == current["properties"].get(k)
        for k in ("height_m", "base_h", "normal")
    )


def has_wall_pixels(path: Path, max_blank: float = 0.2) -> bool:
    """False for a rectified view that is mostly pure black, i.e. outside panorama coverage."""
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        pixels = np.asarray(image.convert("RGB"))
    return float((pixels.max(-1) <= 2).mean()) <= max_blank


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
            descriptions.append(
                {
                    "facade_id": f.id,
                    "width_m": f.length,
                    "height_m": f.height_m,
                    "geometry_bays": f.props.get("bay_count"),
                    "properties": f.props,
                    "views": [
                        {"file": Path(i).name, "part": v["part"], "date": v["date"]}
                        for i, v in zip(files, views, strict=True)
                    ],
                }
            )
        if not images:
            continue
        prompt = (
            "Label ONE building in Turku, Finland from the attached reference crops. Do not generate images "
            "or run commands. Return only the strict schema JSON, all strings in English. "
            "Each red outline marks one target wall; never describe the neighbouring building. "
            "Return building_id exactly as supplied and one facade object for each facade_id listed below. "
            "Use the image filename for each views[].view. Mark a crop unusable only if the target wall is MOSTLY "
            "hidden, is the wrong building, or its architecture cannot be read. Minor foreground objects or shadows "
            "do not make a reference unusable: the improvement step removes those. Colours are unshadowed surface colours. Bay counts from photos are only priors: "
            "geometry_bays and height_m override them in downstream work. Do not invent certainty.\n"
            + json.dumps({"building_id": bid, "facades": descriptions}, ensure_ascii=False)
        )
        jobs.append(
            {
                "id": f"{bid}.label",
                "prompt": prompt,
                "images": images,
                "schema": str(SCHEMAS_DIR / "building_label.schema.json"),
                "expect_images": 0,
                "timeout": 900,
                "prompt_version": "building-label-v1",
                "meta": {
                    "building_id": bid,
                    "kind": "building_label",
                    "facade_ids": [d["facade_id"] for d in descriptions],
                },
            }
        )
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
    fragments = {
        k: (PROMPTS_DIR / "fragments" / f"{k}.md").read_text().strip()
        for k in ("style", "albedo_rules", "negatives")
    }
    jobs = []
    for f in load_facades(facades_path):
        if not f.props.get("street_facing", True) or f.id not in specs:
            continue
        label = specs[f.id]
        d = refs_root / f.id
        if not reference_matches(f, refs_root):
            continue
        usable = {
            Path(v["view"]).stem.removesuffix("_outline") for v in label["views"] if v["usable"]
        }
        views = [v for v in _views(d) if v["view_id"] in usable][:4]
        # Never silently turn a rejected view into an accepted reference.
        if not views:
            continue
        best = max(views, key=lambda v: (v["part"] == "full", v["score"]))
        ratio = f.length / f.height_m
        guide = layout_guide(f, d) if ratio > 3 or ratio < 1 / 3 else None
        if guide:
            views = views[:3]
            if best not in views:
                views[-1] = best
        images = [str(d / v["files"]["rectified"]) for v in views]
        images += [str(d / best["files"]["outline"])]
        count = f.props.get("bay_count")
        extent = (
            f"Cover the WHOLE facade edge to edge: {f.length:.3f} m wide and {f.height_m:.3f} m high. "
            "Preserve unique doors, corners and ornaments; do not replace the wall with a repeatable strip. "
            "The geometrical dimensions above are authoritative, not estimated dimensions in the label. "
        )
        if count is not None:
            extent += (
                f"Geometry requires exactly {int(count)} bays; override the photo count prior."
            )
        else:
            extent += "No geometry bay count is available yet; retain the reference rhythm without claiming a verified count."
        if guide:
            images.append(str(guide))
            if count is None and label.get("bays"):
                extent += (
                    f" The photographic prior is approximately {label['bays']} bays across the complete wall. "
                    "Use that full run unless the photos clearly contradict it; do not shorten the building "
                    "to the number of bays visible in an individual partial crop. This is a photo prior, "
                    "not a verified geometry count."
                )
            extent += (
                f" CRITICAL LAYOUT: {guide.name} is a geometry guide, NOT a facade photo. "
                f"Replace ONLY its grey rectangle with the WHOLE unique facade, maintaining that rectangle's "
                f"exact {ratio:.4f}:1 content aspect ratio and its white surrounding canvas. "
                "Do not enlarge the rectangle to fill the canvas. The white margins will be removed locally. "
                "Do not squash a short facade, omit repeated bays, crop away ends, or add extra storeys to fit. "
                "Read all references together and preserve their full run of window bays. "
                "This geometry-guide layout overrides the generic edge-to-edge canvas instruction."
            )
        jobs.append(
            {
                "id": f"{f.id}.improve",
                "prompt_file": str(PROMPTS_DIR / "facade_improve.md"),
                "vars": {
                    "view_list": "\n".join(
                        f"- {v['files']['rectified']}: {v['part']} rectified view, u={v['section_m']} metres"
                        for v in views
                    )
                    + f"\n- {best['files']['outline']}: best source perspective with target in red",
                    "address": f.props.get("address"),
                    "year": f.props.get("year"),
                    "label_json": json.dumps(label),
                    "extent": extent,
                    "aspect": f"{f.length / f.height_m:.3f}:1",
                    **fragments,
                },
                "images": images,
                "expect_images": 1,
                "timeout": 900,
                "prompt_version": "facade-whole-v1",
                "meta": {
                    "kind": "improve",
                    "facade_id": f.id,
                    "building_id": str(f.props.get("building_id", f.id)),
                    "width_m": f.length,
                    "height_m": f.height_m,
                    "bay_count": count,
                    "bay_count_source": "geometry" if count is not None else "unavailable",
                    "tile": "none",
                    "views_used": [v["view_id"] for v in views],
                },
            }
        )
    return jobs


def generate_jobs(
    facades_path: Path,
    refs_root: Path,
    labels_out: Path | None = None,
    feedback: dict[str, str] | None = None,
) -> list[dict]:
    """Build reference-led, fully generated elevations without depending on a label pass.

    ``feedback`` maps facade IDs to the rejection reason of a previous image. Only those
    walls get a job, and the reason becomes part of the prompt (and so of the cache key).
    """
    specs = labels(labels_out) if labels_out is not None else {}
    fragments = {
        k: (PROMPTS_DIR / "fragments" / f"{k}.md").read_text().strip()
        for k in ("style", "albedo_rules")
    }
    result = []
    for f in load_facades(facades_path):
        if not f.props.get("street_facing", True) or not reference_matches(f, refs_root):
            continue
        if feedback is not None and f.id not in feedback:
            continue
        d = refs_root / f.id
        views = [
            v
            for v in _views(d)
            if v.get("part") == "full" and has_wall_pixels(d / v["files"]["rectified"])
        ][:3]
        if not views:
            continue
        best = max(views, key=lambda v: v.get("score") or 0)
        ratio = f.length / f.height_m
        guide = layout_guide(f, d) if ratio > 3 or ratio < 1 / 3 else None
        images = [str(d / v["files"]["rectified"]) for v in views]
        images.append(str(d / best["files"]["outline"]))
        if guide:
            images.append(str(guide))
        count = f.props.get("bay_count")
        result.append(
            {
                "id": f"{f.id}.generate",
                "prompt_file": str(PROMPTS_DIR / "facade_generate.md"),
                "vars": {
                    "width_m": f"{f.length:.3f}",
                    "height_m": f"{f.height_m:.3f}",
                    "aspect": f"{ratio:.4f}:1",
                    "bay_instruction": (
                        f"The authoritative geometry calls for {int(count)} bays."
                        if count is not None
                        else "Infer the opening grid from the references; do not invent a geometry bay count."
                    ),
                    "view_list": "\n".join(v["files"]["rectified"] for v in views),
                    "label_instruction": json.dumps(specs[f.id]) if f.id in specs else "",
                    "layout_instruction": (
                        f"The last attachment is a layout guide, not a photo. Put the complete wall "
                        f"inside its grey rectangle at {ratio:.4f}:1 and leave the white canvas outside."
                        if guide
                        else ""
                    ),
                    "retry_instruction": (
                        "A previous image of this wall was rejected in review: "
                        f"{feedback[f.id]} Correct that while following every rule below."
                        if feedback
                        else ""
                    ),
                    **fragments,
                },
                "images": images,
                "expect_images": 1,
                "timeout": 900,
                "prompt_version": "facade-generate-v1",
                "meta": {
                    "kind": "generate",
                    "facade_id": f.id,
                    "building_id": str(f.props.get("building_id", f.id)),
                    "width_m": f.length,
                    "height_m": f.height_m,
                    "bay_count": count,
                    "views_used": [v["view_id"] for v in views],
                },
            }
        )
    return result


def visual_qa_jobs(facades_path: Path, refs_root: Path, generate_out: Path, generated: Path):
    """One strict-schema review per generated wall, comparing its exact bytes to references.

    The reviewed image is copied to ``generated/<facade_id>.png``; the acceptance audit
    later proves that the review saw those bytes.
    """
    records = latest(generate_out)
    result = []
    for f in load_facades(facades_path):
        record = records.get(f"{f.id}.generate", {})
        if not record.get("ok") or not record.get("primary") or not reference_matches(f, refs_root):
            continue
        d = refs_root / f.id
        used = record["meta"]["views_used"]
        views = [v for v in _views(d) if v["view_id"] in used][:2]
        if not views:
            continue
        image = generated / f"{f.id}.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record["primary"], image)
        result.append(
            {
                "id": f"{f.id}.visualqa",
                "prompt_file": str(PROMPTS_DIR / "facade_visual_qa.md"),
                "vars": {
                    "facade_id": f.id,
                    "width_m": f"{f.length:.3f}",
                    "height_m": f"{f.height_m:.3f}",
                    "aspect": f"{f.length / f.height_m:.4f}:1",
                    "reference_count": str(len(views)),
                },
                "images": [str(image)] + [str(d / v["files"]["rectified"]) for v in views],
                "schema": str(SCHEMAS_DIR / "facade_visual_qa.schema.json"),
                "expect_images": 0,
                "timeout": 900,
                "prompt_version": "facade-visual-qa-v1",
                "meta": {
                    "kind": "generated_visual_qa",
                    "facade_id": f.id,
                    "generate_key": record["key"],
                    "views": [v["view_id"] for v in views],
                },
            }
        )
    return result
