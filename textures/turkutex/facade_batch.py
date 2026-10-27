"""Resumable AOI facade production and an honest coverage/contact-sheet report.

Stages are individually repeatable; report never counts missing/rejected references or
library fallbacks as generated street textures. No shipped geometry is written here.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import html
import inspect
import io
import json
import math
import shutil
from functools import cache, lru_cache
from pathlib import Path

from PIL import Image

from . import building_jobs as jobs
from .codex.runner import Runner, load_jobs
from .codex.usage import snapshot
from .jobs import _views, _write
from .paths import textures_data_dir
from .post.pipeline import POST_VERSION, ktx2_header, native_encoder, process, sha
from .streetview.facades import load_facades

VERSION = "facades-v1"


def post_code_hash(*, facade: bool = True) -> str:
    from .paths import TEXTURES_DIR

    files = [
        TEXTURES_DIR / "turkutex/post" / name
        for name in ("pipeline.py", "maps.py", "tileable.py", "qa.py", "deepbump_worker.py")
    ]
    if facade:
        files.append(TEXTURES_DIR / "turkutex/post/frame.py")
    native = native_encoder()
    if native:
        files.append(native)
    # Contact-sheet/CLI edits do not alter texture pixels or UVs.
    transforms = (
        "".join(inspect.getsource(f) for f in (post_facade, metric_size, uv_transform))
        if facade
        else ""
    )
    return hashlib.sha256(("".join(sha(p) for p in files) + transforms).encode()).hexdigest()


def safe_id(value) -> str:
    import re

    value = str(value)
    if value in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"Unsafe artifact identifier: {value!r}")
    return value


def asset_path(f, output: Path) -> Path:
    bid = safe_id(f.props.get("building_id", f.id))
    if "facade_index" not in f.props:
        raise ValueError(f"{f.id}: facade_index is required (do not guess assembler indices)")
    index = f.props["facade_index"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError(f"{f.id}: facade_index must be a nonnegative integer")
    return output / "facades" / bid / str(index)


def validate_inventory(path: Path, output: Path):
    facades = load_facades(path)
    ids, paths = set(), set()
    for f in facades:
        if f.id in ids:
            raise ValueError(f"Duplicate facade id: {f.id}")
        ids.add(f.id)
        target = asset_path(f, output)
        if target in paths:
            raise ValueError(f"Duplicate building/facade_index: {target}")
        paths.add(target)
        if not math.isfinite(f.length + f.height_m) or min(f.length, f.height_m) <= 0:
            raise ValueError(f"{f.id}: invalid facade dimensions")
    if not facades:
        raise ValueError("Facade inventory is empty")
    return facades


def metric_size(
    width: float, height: float, ppm: float = 64, max_side: int = 2048
) -> tuple[int, int]:
    """Keep metres/pixel consistent on both axes (KTX2 supports non-power-of-two textures)."""
    if min(width, height, ppm, max_side) <= 0 or max_side % 4:
        raise ValueError("Positive dimensions/ppm and a max-side divisible by four are required")
    scale = min(ppm, max_side / max(width, height))
    # Quantize the smaller axis first, then derive the larger one at the same scale.
    # Otherwise a 0.1 m return wall can acquire a 25% pixel-density mismatch.
    small = min(width, height)
    small_pixels = max(4, 4 * math.floor(small * scale / 4))
    scale = small_pixels / small
    result = max(4, 4 * round(width * scale / 4)), max(4, 4 * round(height * scale / 4))
    if max(result) > max_side:
        raise ValueError("Facade aspect ratio is too extreme for the requested texture size")
    return result


def write_json(path: Path, value):
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text() == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def uv_transform(f) -> dict:
    """Map u-right/v-up metre coordinates to top-left-origin KTX texture coordinates."""
    left = f.normal_side == "left"
    return {
        "units": "metres",
        "origin": "start vertex at facade base",
        "u": "along facade",
        "v": "up",
        "scale": [(-1 if left else 1) / f.length, -1 / f.height_m],
        "offset": [1 if left else 0, 1],
        "image_origin": "top-left",
        "wrap": "clamp",
    }


def post_facade(f, record: dict, refs: Path, output: Path, *, ppm=64, max_side=2048) -> dict:
    if not record.get("ok") or not record.get("primary"):
        raise ValueError(f"{f.id}: no successful image result")
    if not jobs.reference_matches(f, refs):
        raise ValueError(f"{f.id}: reference geometry is stale")
    if record["meta"]["width_m"] != f.length or record["meta"]["height_m"] != f.height_m:
        raise ValueError(f"{f.id}: generated image geometry is stale")
    source = Path(record["primary"])
    width, height = f.length, f.height_m
    size = metric_size(width, height, ppm, max_side)
    target = asset_path(f, output)
    post_dir = output / "post" / f.id
    views = [v for v in _views(refs / f.id) if v["view_id"] in record["meta"]["views_used"]]
    if not views:
        raise ValueError(f"{f.id}: generated image has no logged usable reference")
    params = {
        "version": VERSION,
        "post_version": POST_VERSION,
        "code": post_code_hash(),
        "source": sha(source),
        "size": size,
        "facade": f.to_feature(),
        "job_key": record["key"],
        "references": [
            {
                "source": v["source"],
                "pano_id": v["pano_id"],
                "date": v["date"],
                "view_id": v["view_id"],
                "sha256": sha(refs / f.id / v["files"]["rectified"]),
            }
            for v in views
        ],
    }
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    metadata_path = target.with_suffix(".json")
    if metadata_path.exists():
        old = json.loads(metadata_path.read_text())
        if old.get("key") == key and all(
            (metadata_path.parent / n).is_file() and sha(metadata_path.parent / n) == h
            for n, h in old.get("hashes", {}).items()
        ):
            preview = post_dir / "albedo.png"
            if (
                old.get("hashes")
                and preview.is_file()
                and sha(preview) == old.get("preview_sha256")
            ):
                return old
    from .post.maps import deepbump_available

    if not deepbump_available():
        raise RuntimeError("Production facades require DeepBump; run make textures-deepbump")
    from .post.frame import facade_content_box

    with Image.open(source) as image:
        content_box = facade_content_box(image)
        source_size = image.size
        content_aspect = (content_box[2] - content_box[0]) / (content_box[3] - content_box[1])
        aspect_error = max(content_aspect / (width / height), (width / height) / content_aspect) - 1
        post_dir.mkdir(parents=True, exist_ok=True)
        cropped_source = post_dir / "source.png"
        image.crop(content_box).convert("RGB").save(cropped_source)
    post = process(
        cropped_source,
        post_dir,
        name=f.id,
        tile="none",
        flatten="none",
        size=f"{size[0]}x{size[1]}",
        period_crop=False,
        material="facade_mixed",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "albedo": target.name + ".ktx2",
        "normal": target.name + ".normal.ktx2",
        "orm": target.name + ".orm.ktx2",
    }
    for kind, name in files.items():
        shutil.copy2(post_dir / f"{kind}.ktx2", target.parent / name)
    rec = {
        "key": key,
        "facade_id": f.id,
        "building_id": f.props.get("building_id", f.id),
        "facade_index": f.props["facade_index"],
        "width_m": width,
        "height_m": height,
        "size": list(size),
        "pixels_per_metre": [size[0] / width, size[1] / height],
        "source_size": source_size,
        "content_box": content_box,
        "uv": uv_transform(f),
        "references": params["references"],
        "job_key": record["key"],
        "thread_id": record.get("thread_id"),
        "imagegen_calls": len(record.get("imagegen_calls", [])),
        "geometry_source": f.props.get("height_source", "buildings layer"),
        "preview_sha256": sha(post_dir / "albedo.png"),
        "bay_count": f.props.get("bay_count"),
        "files": files,
        "hashes": {n: sha(target.parent / n) for n in files.values()},
        "qa": {
            "seam": "not applicable: unique wall, clamped UVs",
            "scale_relative_error": abs(size[0] / width / (size[1] / height) - 1),
            "content_aspect_error": aspect_error,
            "gltf_basisu_valid": all(v["gltf_basisu_valid"] for v in post["ktx2"].values()),
            "normal_method": post["maps"],
        },
    }
    write_json(metadata_path, rec)
    return rec


def thumbnail(path: Path | None) -> str:
    if path is None or not path.is_file():
        return '<div class="missing">Missing</div>'
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((480, 240))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=75)
    return (
        '<img alt="Facade preview" src="data:image/jpeg;base64,'
        + base64.b64encode(buf.getvalue()).decode()
        + '">'
    )


def report(facades_path: Path, refs: Path, run: Path, output: Path) -> dict:
    facades = validate_inventory(facades_path, output)
    improvements = jobs.latest(run / "run_improve/out.jsonl")
    improvements.update(jobs.latest(run / "run_generate/out.jsonl"))
    labels = jobs.labels(run / "run_label/out.jsonl")
    rows, cards = [], []
    from .library_batch import material_valid

    @cache
    def valid_material(key):
        return material_valid(output / "library" / safe_id(key))

    render_thumbnail = lru_cache(maxsize=None)(thumbnail)
    library_previews = {}
    calls, seen = {}, set()
    # Include targeted repair runs, not only the two standard batch directories.
    for path in sorted(run.glob("*/attempts.jsonl")):
        lines = path.read_text().splitlines()
        for line in lines:
            attempt = json.loads(line)
            meta = attempt.get("meta", {})
            stage = {"building_label": "label", "improve": "improve", "generate": "generate"}.get(
                meta.get("kind")
            )
            identity = attempt.get("thread_id") or hashlib.sha256(line.encode()).hexdigest()
            if stage is None or identity in seen:
                continue
            seen.add(identity)
            ids = meta.get("facade_ids", [meta.get("facade_id")])
            for fid in ids:
                counts = calls.setdefault(fid, {"label": 0, "improve": 0, "imagegen": 0})
                counts[stage] = counts.get(stage, 0) + 1
                counts["imagegen"] += len(attempt.get("imagegen_calls", []))
    for f in facades:
        target = asset_path(f, output)
        views = _views(refs / f.id) if jobs.reference_matches(f, refs) else []
        metadata = (
            json.loads(target.with_suffix(".json").read_text())
            if target.with_suffix(".json").exists()
            else None
        )
        record = improvements.get(f.id + ".generate", improvements.get(f.id + ".improve", {}))
        problems = []
        if f.props.get("street_facing", True):
            if not views:
                problems.append("no reference")
            if not metadata:
                problems.append("no cleaned texture")
                label = labels.get(f.id)
                if label is not None and not any(v["usable"] for v in label["views"]):
                    problems.append("all references rejected by label")
            else:
                if record and (
                    not record.get("ok") or record.get("key") != metadata.get("job_key")
                ):
                    problems.append("processed texture does not match latest generation")
                if not metadata.get("references"):
                    problems.append("no logged usable reference")
                if record.get("meta", {}).get("kind") == "generate" and not metadata.get(
                    "generated_qa"
                ):
                    problems.append("generated image lacks opening and foreign-object QA")
                if (
                    abs(metadata["width_m"] - f.length) > 1e-6
                    or abs(metadata["height_m"] - f.height_m) > 1e-6
                ):
                    problems.append("stale geometry scale")
                if metadata["qa"]["scale_relative_error"] > 0.03:
                    problems.append("pixel scale mismatch")
                if metadata["qa"].get("content_aspect_error", float("inf")) > 0.30:
                    problems.append("generated facade aspect mismatch")
                if not metadata["qa"].get("gltf_basisu_valid"):
                    problems.append("KTX2 not validated for glTF")
                if len(metadata.get("hashes", {})) != 3:
                    problems.append("incomplete PBR map set")
                for name, digest in metadata["hashes"].items():
                    path = target.parent / name
                    if not path.is_file() or sha(path) != digest or not ktx2_header(path)["valid"]:
                        problems.append(f"missing/corrupt {name}")
        else:
            key = f.props.get("material_key")
            if not key or not valid_material(key):
                problems.append("missing library material")
        row = {
            "facade_id": f.id,
            "building_id": f.props.get("building_id"),
            "street_facing": f.props.get("street_facing", True),
            "reference_count": len(views),
            "sources": sorted({v["source"] for v in views}),
            "calls": calls.get(f.id, {"label": 0, "improve": 0, "imagegen": 0}),
            "problems": problems,
            "complete": not problems,
            "imagegen_calls": len(record.get("imagegen_calls", [])),
        }
        rows.append(row)
        ref = (
            refs / f.id / views[0]["files"].get("rectified", views[0]["files"]["crop"])
            if views
            else None
        )
        result = output / "post" / f.id / "albedo.png"
        status = ", ".join(problems) or "PASS"
        title = html.escape(f"{f.id} — {f.props.get('address') or ''}")
        if not f.props.get("street_facing", True):
            material = safe_id(f.props.get("material_key", "missing"))
            result = output / "library" / material / "albedo.png"
            library_previews[material] = render_thumbnail(result)
            result_preview = f'<a href="#library-{material}">Library material: {material}</a>'
        else:
            result_preview = render_thumbnail(result)
        street = f.props.get("street_facing", True)
        ref_preview = (
            render_thumbnail(ref) if street else "<p>Courtyard: no photo reference required.</p>"
        )
        cards.append(
            f'<article class="facade" id="{safe_id(f.id)}" data-street="{int(street)}" '
            f'data-complete="{int(not problems)}"><h2>{title}</h2>'
            f"<p>{f.length:.2f} × {f.height_m:.2f} m · {len(views)} references · "
            f'{html.escape(status)}</p><div class="pair">{ref_preview}{result_preview}</div></article>'
        )
    usage_path = run / "usage_log.jsonl"
    usage = (
        [json.loads(s) for s in usage_path.read_text().splitlines()] if usage_path.exists() else []
    )
    buildings = {}
    bindings = {}
    accepted = {row["facade_id"] for row in rows if row["complete"]}
    for f in facades:
        bid = str(f.props.get("building_id", f.id))
        key = f.props.get("roof_material_key", "roof_standing_seam_metal")
        buildings.setdefault(
            bid,
            {"building_id": bid, "roof_material_key": key, "roof_complete": valid_material(key)},
        )
        bindings.setdefault(bid, []).append(
            {
                "facade_id": f.id,
                "facade_index": f.props["facade_index"],
                "street_facing": f.props.get("street_facing", True),
                "material_key": f.props.get("material_key", "render_smooth"),
                "texture": f"{f.props['facade_index']}.ktx2"
                if f.id in accepted and f.props.get("street_facing", True)
                else None,
            }
        )
    for bid, records in bindings.items():
        write_json(
            output / "facades" / safe_id(bid) / "bindings.json",
            {
                "building_id": bid,
                "roof_material_key": buildings[bid]["roof_material_key"],
                "facades": records,
            },
        )
    summary = {
        "version": VERSION,
        "inventory_sha256": sha(facades_path),
        "facades": len(rows),
        "complete": sum(r["complete"] for r in rows),
        "missing": sum(not r["complete"] for r in rows),
        "missing_roofs": sum(not b["roof_complete"] for b in buildings.values()),
        "buildings": list(buildings.values()),
        "usage": usage,
        "rows": rows,
    }
    write_json(run / "qa.json", summary)
    write_json(
        output / "MANIFEST.json",
        {
            "version": VERSION,
            "code_hash": post_code_hash(),
            "inventory": str(facades_path),
            "inventory_sha256": sha(facades_path),
            "facades": rows,
            "buildings": list(buildings.values()),
        },
    )
    page = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Turku facade QA</title>'
        "<style>body{font:16px system-ui;background:#182026;color:#eee;margin:2rem}h1{font-size:2rem}"
        "h2{font-size:1rem}article{border-top:1px solid #657078;padding:1rem 0}.pair{display:grid;"
        "grid-template-columns:1fr 1fr;gap:1rem}.pair img{max-width:100%;max-height:240px;object-fit:contain}"
        ".missing{background:#502b2b;padding:2rem}p{color:#cad4db}a{color:#8dd8ff}"
        "input,select{font:inherit;padding:.5rem;margin:0 1rem 1rem 0}nav{position:sticky;top:0;"
        "background:#182026;padding:1rem 0}article[hidden]{display:none}</style>"
        f"<h1>Facade references / cleaned results</h1><p>{summary['complete']}/{len(rows)} complete. "
        "Left: rectified reference. Right: processed output. Missing results remain visible. "
        f"{summary['missing_roofs']} roofs lack library materials. "
        "Images are offline QA references, never runtime assets. Courtyard rows link to their shared material.</p>"
        '<nav><label>Show <select id="scope"><option value="street">Street facades</option>'
        '<option value="all">All facades</option><option value="missing">Missing</option>'
        '<option value="complete">Complete</option><option value="courtyard">Courtyard</option></select></label>'
        '<label>Find <input id="query" placeholder="Address or facade ID"></label><span id="count"></span></nav>'
        "<details><summary>Library preview</summary>"
        + "".join(
            f'<article id="library-{key}"><h2>Library: {key}</h2>{preview}</article>'
            for key, preview in sorted(library_previews.items())
        )
        + "</details>"
        + "".join(cards)
        + '<script>const scope=document.querySelector("#scope"), query=document.querySelector("#query");'
        'function filter(){let count=0;for(const card of document.querySelectorAll(".facade")){'
        "const s=scope.value,d=card.dataset;card.hidden=!(("
        's==="all"||s==="street"&&d.street==="1"||s==="courtyard"&&d.street==="0"||'
        's==="missing"&&d.complete==="0"||s==="complete"&&d.complete==="1")&&'
        'card.querySelector("h2").textContent.toLowerCase().includes(query.value.toLowerCase()));'
        'if(!card.hidden)count++;}document.querySelector("#count").textContent=count+" shown";}'
        'scope.addEventListener("change",filter);query.addEventListener("input",filter);filter();'
        'for(const a of document.querySelectorAll("a[href^=\\"#library-\\"]"))'
        'a.addEventListener("click",()=>document.querySelector("details").open=true);</script></html>'
    )
    contact = run / "contact-sheet.html"
    if not contact.exists() or contact.read_text() != page:
        contact.parent.mkdir(parents=True, exist_ok=True)
        contact.write_text(page)
    return summary


def log_usage(run: Path, stage: str):
    with (run / "usage_log.jsonl").open("a") as stream:
        stream.write(json.dumps({"stage": stage, **snapshot()}) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "stage",
        choices=[
            "refs",
            "label",
            "improve",
            "refgrid",
            "generate",
            "visualqa",
            "post",
            "report",
            "all",
        ],
    )
    ap.add_argument("facades", type=Path)
    ap.add_argument("--run", type=Path, default=textures_data_dir("aoi"))
    ap.add_argument("--refs", type=Path, default=textures_data_dir("refs"))
    ap.add_argument("--output", type=Path, default=textures_data_dir())
    ap.add_argument("--sources", default="google,lookaround,mapillary,photospheres")
    ap.add_argument("-j", type=int, default=6)
    ap.add_argument("--ref-workers", type=int, default=3)
    ap.add_argument("--ref-max-views", type=int, default=4)
    ap.add_argument("--post-workers", type=int, default=2)
    ap.add_argument("--max-pct", type=float, default=100)
    ap.add_argument("--max-side", type=int, default=2048)
    ap.add_argument("--ppm", type=float, default=64)
    ap.add_argument("--mode", choices=["improve", "generate"], default="improve")
    ap.add_argument(
        "--feedback",
        type=Path,
        help="generate: JSON {facade_id: rejection reason}; regenerate only those walls once",
    )
    a = ap.parse_args(argv)
    if a.feedback and a.stage != "generate":
        ap.error("--feedback applies only to the generate stage")
    if not 1 <= a.ref_max_views <= 12:
        ap.error("--ref-max-views must be between 1 and 12")
    facades = validate_inventory(a.facades, a.output)
    a.run.mkdir(parents=True, exist_ok=True)
    stages = ["refs", "label", a.mode, "post", "report"] if a.stage == "all" else [a.stage]
    errors = []
    for stage in stages:
        if stage == "refs":
            from .streetview.panos import PanoCache
            from .streetview.refs import SelectParams, facade_refs

            cache = PanoCache()

            def make_reference(f, cache=cache):
                return facade_refs(
                    cache,
                    f,
                    a.refs,
                    SelectParams(max_views=a.ref_max_views),
                    tuple(a.sources.split(",")),
                )

            with cf.ThreadPoolExecutor(max_workers=a.ref_workers) as pool:
                futures = {
                    pool.submit(make_reference, f): f
                    for f in facades
                    if f.props.get("street_facing", True)
                }
                for future in cf.as_completed(futures):
                    f = futures[future]
                    try:
                        rec = future.result()
                        print(f"{f.id}: {len(rec['views'])} references", flush=True)
                    except Exception as exc:
                        errors.append(
                            {
                                "facade_id": f.id,
                                "stage": stage,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        print(f"{f.id}: reference failed ({type(exc).__name__})", flush=True)
        elif stage in ("label", "improve", "refgrid", "generate", "visualqa"):
            builder = {
                "label": jobs.label_jobs,
                "improve": jobs.improve_jobs,
                "refgrid": jobs.reference_grid_jobs,
                "generate": jobs.generate_jobs,
                "visualqa": jobs.visual_qa_jobs,
            }[stage]
            args = [a.facades, a.refs] + (
                [a.run / "run_label/out.jsonl"] if stage == "improve" else []
            )
            if stage == "generate":
                label_path = a.run / "run_label/out.jsonl"
                args.append(label_path if label_path.exists() else None)
                args.append(json.loads(a.feedback.read_text()) if a.feedback else None)
                grid_path = a.run / "run_refgrid/out.jsonl"
                args.append(grid_path if grid_path.exists() else None)
            if stage == "visualqa":
                args += [a.run / "run_generate/out.jsonl", a.run / "generated"]
            job_path = a.run / f"{stage}_jobs.jsonl"
            _write(builder(*args), job_path)
            log_usage(a.run, stage + "_before")
            runner = Runner(
                a.run / f"run_{stage}",
                parallel=a.j,
                max_pct=a.max_pct,
                cache_dir=textures_data_dir("codex-cache"),
            )
            code = runner.run(load_jobs(job_path))
            log_usage(a.run, stage + "_after")
            if code:
                report(a.facades, a.refs, a.run, a.output)
                return code
            if stage == "label":
                for r in jobs.latest(a.run / "run_label/out.jsonl").values():
                    if r.get("ok"):
                        bid = safe_id(r["meta"]["building_id"])
                        answer = r["answer"]
                        if answer["building_id"] != bid or sorted(
                            x["facade_id"] for x in answer["facades"]
                        ) != sorted(r["meta"]["facade_ids"]):
                            raise ValueError(f"{bid}: label returned mismatched facade IDs")
                        write_json(a.output / "facades" / bid / "spec.json", answer)
        elif stage == "post":
            records = jobs.latest(a.run / f"run_{a.mode}/out.jsonl")
            with cf.ThreadPoolExecutor(max_workers=a.post_workers) as pool:
                futures = {
                    pool.submit(
                        post_facade,
                        f,
                        records[f.id + f".{a.mode}"],
                        a.refs,
                        a.output,
                        ppm=a.ppm,
                        max_side=a.max_side,
                    ): f
                    for f in facades
                    if records.get(f.id + f".{a.mode}", {}).get("ok")
                }
                for future in cf.as_completed(futures):
                    f = futures[future]
                    try:
                        future.result()
                        print(f"{f.id}: post ready", flush=True)
                    except Exception as exc:
                        errors.append(
                            {
                                "facade_id": f.id,
                                "stage": stage,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
        else:
            summary = report(a.facades, a.refs, a.run, a.output)
            print(json.dumps({k: summary[k] for k in ("facades", "complete", "missing")}))
        if stage in ("refs", "post"):
            error_path = a.run / "stage-errors.json"
            previous = json.loads(error_path.read_text()) if error_path.exists() else {}
            if isinstance(previous, list):
                previous = {
                    s: [e for e in previous if e.get("stage") == s] for s in ("refs", "post")
                }
            previous[stage] = [e for e in errors if e["stage"] == stage]
            write_json(error_path, previous)
    if a.stage in ("all", "report"):
        return 1 if summary["missing"] or summary["missing_roofs"] or errors else 0
    return 1 if errors else 0
