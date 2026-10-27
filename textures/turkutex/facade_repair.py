"""Targeted image improvement for failed direct facades visible from the spawn."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from turku3d import config

from .building_jobs import latest, layout_guide
from .codex.runner import Runner, load_jobs
from .codex.usage import used_percent
from .export import copy, export_lock
from .facade_batch import asset_path, post_facade, write_json
from .jobs import _views, _write
from .paths import PROMPTS_DIR, textures_data_dir
from .post.pipeline import sha
from .streetview.facades import load_facades


def repair_jobs(facades, refs: Path, labels: dict, limit: int = 25):
    fragments = {
        key: (PROMPTS_DIR / "fragments" / f"{key}.md").read_text().strip()
        for key in ("style", "albedo_rules", "negatives")
    }
    eligible = []
    for facade in facades:
        if math.dist(facade.centre, (config.OFFICE_E, config.OFFICE_N)) > 60:
            continue
        label = labels.get(facade.id)
        if not label or label["usable"]:
            continue
        from .building_jobs import reference_matches

        if not reference_matches(facade, refs):
            continue
        views = [v for v in _views(refs / facade.id) if v.get("part") == "full"][:2]
        if not views:
            continue
        eligible.append((facade, label, views))
    for facade, label, views in eligible[:limit]:
        best = max(views, key=lambda v: v.get("score") or 0)
        guide = (
            layout_guide(facade, refs / facade.id)
            if facade.length / facade.height_m > 3 or facade.length / facade.height_m < 1 / 3
            else None
        )
        images = [str(refs / facade.id / v["files"]["rectified"]) for v in views]
        images.append(str(refs / facade.id / best["files"]["outline"]))
        if guide:
            images.append(str(guide))
        extent = (
            f"Cover the entire wall: {facade.length:.3f} m wide and "
            f"{facade.height_m:.3f} m high. Restore areas blocked by "
            f"{label['problem']}. Preserve the observed windows and ornaments. "
        )
        if guide:
            extent += (
                "Use the grey rectangle in the final attachment as the exact content "
                "aspect and keep white margins outside it; the guide is not a photo. "
            )
        yield {
            "id": f"{facade.id}.improve",
            "prompt_file": str(PROMPTS_DIR / "facade_improve.md"),
            "vars": {
                "view_list": "\n".join(v["files"]["rectified"] for v in views),
                "address": facade.props.get("address", "Turku"),
                "year": facade.props.get("year", "unknown"),
                "label_json": json.dumps(label),
                "extent": extent,
                "aspect": f"{facade.length / facade.height_m:.3f}:1",
                **fragments,
            },
            "images": images,
            "expect_images": 1,
            "timeout": 900,
            "prompt_version": "facade-targeted-repair-v1",
            "meta": {
                "kind": "improve",
                "facade_id": facade.id,
                "building_id": facade.props["building_id"],
                "width_m": facade.length,
                "height_m": facade.height_m,
                "views_used": [v["view_id"] for v in views],
            },
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facades", type=Path)
    ap.add_argument("--refs", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--staging", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args(argv)
    if not 0 <= a.limit <= 25:
        ap.error("--limit must be between 0 and 25")
    quota = used_percent()
    if quota is None or quota >= 70:
        raise RuntimeError(f"Codex quota guard: weekly used percent is {quota}")
    facades = load_facades(a.facades)
    by_id = {f.id: f for f in facades}
    labels = json.loads(a.labels.read_text())
    a.run.mkdir(parents=True, exist_ok=True)
    job_path = a.run / "repair_jobs.jsonl"
    specs = list(repair_jobs(facades, a.refs, labels, a.limit))
    _write(specs, job_path)
    code = Runner(
        a.run / "run_improve", parallel=2, max_pct=70, cache_dir=textures_data_dir("codex-cache")
    ).run(load_jobs(job_path))
    rows = []
    for spec in specs:
        facade = by_id[spec["meta"]["facade_id"]]
        record = latest(a.run / "run_improve/out.jsonl").get(spec["id"], {})
        try:
            meta = post_facade(facade, record, a.refs, a.staging, ppm=32)
            aspect_error = meta["qa"]["content_aspect_error"]
            if aspect_error > 0.30:
                raise ValueError(f"generated content aspect error {aspect_error:.1%} exceeds 30%")
            if not meta["qa"]["gltf_basisu_valid"]:
                raise ValueError("generated image failed KTX validation")
            meta["visual_label"] = labels[facade.id]
            write_json(asset_path(facade, a.staging).with_suffix(".json"), meta)
            with export_lock(a.output):
                manifest = a.output / "MANIFEST.json"
                current = json.loads(manifest.read_text())
                authoritative = Path(config.data_root()) / "build/layers/buildings/facades.geojson"
                if current["inventory_sha256"] != sha(authoritative):
                    raise ValueError("assembly inventory changed")
                source = asset_path(facade, a.staging)
                target = asset_path(facade, a.output)
                for name in meta["files"].values():
                    copy(source.parent / name, target.parent / name)
                copy(source.with_suffix(".json"), target.with_suffix(".json"))
                current["bound"] = sorted(set(current["bound"]) | {facade.id})
                write_json(manifest, current)
            rows.append({"facade_id": facade.id, "bound": True})
        except Exception as exc:
            rows.append(
                {"facade_id": facade.id, "bound": False, "reason": f"{type(exc).__name__}: {exc}"}
            )
        print(json.dumps(rows[-1]), flush=True)
    write_json(a.run / "results.json", rows)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
