"""Build photo-derived facade materials without an image-generation call.

The input is the authoritative buildings inventory and wall-plane rectified
references. Only full-wall views with enough visible source pixels are admitted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from turku3d import config

from .export import copy, export_lock
from .facade_batch import asset_path, metric_size, uv_transform, write_json
from .jobs import _views
from .post.pipeline import ktx2_header, process, sha
from .streetview.facades import load_facades


def nearest_bay_fill(image: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Replace masked pixels from visible repetitions on the same wall row."""
    if valid.mean() < 0.35:
        raise ValueError("less than 35% of wall pixels visible")
    rgb = image.copy()
    height, width = valid.shape
    # Estimate the repeating bay period from the horizontal edge profile.
    edge = np.abs(np.diff(rgb.astype(np.float32).mean(axis=2), axis=1)).mean(axis=0)
    edge -= edge.mean()
    low, high = max(8, width // 16), max(9, width // 2)
    period = max(range(low, high), key=lambda shift: float(edge[:-shift] @ edge[shift:]))
    for row in range(height):
        good = np.flatnonzero(valid[row])
        if not good.size:
            continue
        missing = np.flatnonzero(~valid[row])
        if not missing.size:
            continue
        for shift in (period, -period, 2 * period, -2 * period):
            src = missing + shift
            usable = (src >= 0) & (src < width)
            chosen = missing[usable & valid[row, np.clip(src, 0, width - 1)]]
            rgb[row, chosen] = rgb[row, chosen + shift]
            valid[row, chosen] = True
            missing = np.flatnonzero(~valid[row])
            if not missing.size:
                break
        if missing.size:
            closest = good[np.abs(good[:, None] - missing[None, :]).argmin(axis=0)]
            rgb[row, missing] = rgb[row, closest]
            valid[row, missing] = True
    if not valid.all():
        _, indices = ndimage.distance_transform_edt(~valid, return_indices=True)
        rgb[~valid] = rgb[indices[0][~valid], indices[1][~valid]]
    return rgb


def clean_reference(
    image: Image.Image,
    mask: Image.Image,
    plinth: Image.Image,
    roof: Image.Image,
    size: tuple[int, int],
    height_m: float,
) -> tuple[Image.Image, float]:
    rgb = np.asarray(image.convert("RGB").resize(size, Image.Resampling.LANCZOS)).copy()
    m = np.asarray(mask.convert("L").resize(size, Image.Resampling.NEAREST))
    valid = m >= 192
    coverage = float(valid.mean())
    rgb = nearest_bay_fill(rgb, valid.copy())
    # Correct broad exposure changes while retaining window and ornament contrast.
    light = ndimage.gaussian_filter(
        rgb.astype(np.float32), (max(8, size[1] / 12), max(8, size[0] / 12), 0)
    )
    target = np.median(light.reshape(-1, 3), axis=0)
    rgb = np.clip(rgb.astype(np.float32) + 0.35 * (target - light), 0, 255).astype(np.uint8)
    lower = min(size[1] // 8, max(2, round(0.3 / height_m * size[1])))
    upper = min(size[1] // 8, max(2, round(0.2 / height_m * size[1])))
    rgb[-lower:] = np.asarray(
        plinth.convert("RGB").resize((size[0], lower), Image.Resampling.LANCZOS)
    )
    rgb[:upper] = np.asarray(roof.convert("RGB").resize((size[0], upper), Image.Resampling.LANCZOS))
    return Image.fromarray(rgb), coverage


def best_view(f, refs: Path):
    from .building_jobs import reference_matches

    if not (refs / f.id / "refs.json").is_file():
        raise ValueError("reference acquisition did not complete")
    if not reference_matches(f, refs):
        raise ValueError("reference geometry does not match current wall")
    views = [v for v in _views(refs / f.id) if v.get("part") == "full"]
    if not views:
        raise ValueError("no usable full-wall panorama view")
    preferred = refs / f.id / "preferred.json"
    if preferred.is_file():
        view_id = json.loads(preferred.read_text())["view_id"]
        selected = next((v for v in views if v["view_id"] == view_id), None)
        if selected is None:
            raise ValueError("preferred full-wall panorama view is unavailable")
        return selected
    return max(views, key=lambda v: (v.get("score") or 0, v.get("px_per_m") or 0))


def build_one(f, refs: Path, output: Path, library: Path, *, ppm: int = 32):
    view = best_view(f, refs)
    source = refs / f.id / view["files"]["rectified"]
    mask = refs / f.id / view["files"]["rectified_mask"]
    size = metric_size(f.length, f.height_m, ppm=ppm)
    input_key = hashlib.sha256(
        json.dumps(
            {
                "version": "rectified-photo-bay-inpaint-v1",
                "source": sha(source),
                "mask": sha(mask),
                "plinth": sha(library / "plinth_granite.png"),
                "roof": sha(library / "roof_standing_seam_metal.png"),
                "size": size,
                "height_m": f.height_m,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    target = asset_path(f, output)
    metadata = target.with_suffix(".json")
    if metadata.exists():
        old = json.loads(metadata.read_text())
        if (
            old.get("input_key") == input_key
            and all(
                (target.parent / name).is_file() and sha(target.parent / name) == digest
                for name, digest in old.get("hashes", {}).items()
            )
            and len(old.get("hashes", {})) == 3
        ):
            return old
    post_dir = output / "post" / f.id
    post_dir.mkdir(parents=True, exist_ok=True)
    with (
        Image.open(source) as photo,
        Image.open(mask) as matte,
        Image.open(library / "plinth_granite.png") as plinth,
        Image.open(library / "roof_standing_seam_metal.png") as roof,
    ):
        cleaned, coverage = clean_reference(photo, matte, plinth, roof, size, f.height_m)
    source_path = post_dir / "source.png"
    cleaned.save(source_path)
    result = process(
        source_path,
        post_dir,
        name=f.id,
        tile="none",
        flatten="none",
        size=f"{size[0]}x{size[1]}",
        period_crop=False,
        material="facade_mixed",
        sobel=True,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "albedo": f"{target.name}.ktx2",
        "normal": f"{target.name}.normal.ktx2",
        "orm": f"{target.name}.orm.ktx2",
    }
    for kind, name in files.items():
        shutil.copy2(post_dir / f"{kind}.ktx2", target.parent / name)
    meta = {
        "key": sha(source_path),
        "input_key": input_key,
        "facade_id": f.id,
        "building_id": f.props["building_id"],
        "facade_index": f.props["facade_index"],
        "width_m": f.length,
        "height_m": f.height_m,
        "size": list(size),
        "pixels_per_metre": [size[0] / f.length, size[1] / f.height_m],
        "uv": uv_transform(f),
        "references": [
            {
                "source": view["source"],
                "pano_id": view["pano_id"],
                "date": view.get("date"),
                "view_id": view["view_id"],
                "sha256": sha(source),
            }
        ],
        "method": "rectified-photo-bay-inpaint-v1",
        "visible_fraction": coverage,
        "ground_edge_material": "plinth_granite",
        "roof_edge_material": "roof_standing_seam_metal",
        "imagegen_calls": 0,
        "bay_count": f.props.get("bay_count"),
        "files": files,
        "hashes": {name: sha(target.parent / name) for name in files.values()},
        "preview_sha256": sha(post_dir / "albedo.png"),
        "qa": {
            "scale_relative_error": abs(size[0] / f.length / (size[1] / f.height_m) - 1),
            "gltf_basisu_valid": all(v["gltf_basisu_valid"] for v in result["ktx2"].values()),
            "normal_method": result["maps"],
            "reference_coverage": coverage,
        },
    }
    if not all(ktx2_header(target.parent / name)["valid"] for name in files.values()):
        raise ValueError("invalid KTX2 header")
    write_json(target.with_suffix(".json"), meta)
    return meta


def contact_sheet(
    inventory: Path,
    refs: Path,
    staging: Path,
    assembly: Path,
    results: list[dict],
    destination: Path,
    *,
    radius: float = 120,
):
    """Show every street wall near the spawn, including explicit missing reasons."""
    bound = set(json.loads((assembly / "MANIFEST.json").read_text())["bound"])
    reasons = {row["facade_id"]: row.get("reason") for row in results}
    repairs = staging.parent / "repair" / "results.json"
    if repairs.is_file():
        for row in json.loads(repairs.read_text()):
            if row.get("reason") and row["facade_id"] in reasons:
                reasons[row["facade_id"]] = (
                    (reasons[row["facade_id"]] or "") + "; improve: " + row["reason"]
                )
    cards, rows = [], []

    def thumb(path: Path | None):
        if not path or not path.is_file():
            return "<span>Unavailable</span>"
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((400, 200))
            data = io.BytesIO()
            image.save(data, "JPEG", quality=65)
        return (
            '<img src="data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode() + '">'
        )

    spawn = (config.OFFICE_E, config.OFFICE_N)
    for f in sorted(load_facades(inventory), key=lambda wall: math.dist(wall.centre, spawn)):
        if not f.props.get("street_facing"):
            continue
        distance = math.dist(f.centre, spawn)
        if distance > radius:
            continue
        reason = None if f.id in bound else reasons.get(f.id, "not produced")
        try:
            view = best_view(f, refs)
            reference = refs / f.id / view["files"]["rectified"]
        except ValueError:
            reference = None
        preview = staging / "post" / f.id / "albedo.png"
        target = asset_path(f, assembly).with_suffix(".json")
        if target.is_file() and json.loads(target.read_text()).get("imagegen_calls", 0):
            repaired = staging.parent / "repair-staging" / "post" / f.id / "albedo.png"
            if repaired.is_file():
                preview = repaired
        rows.append(
            {
                "facade_id": f.id,
                "distance_m": round(distance, 2),
                "bound": f.id in bound,
                "reason": reason,
                "reference": str(reference) if reference else None,
            }
        )
        cards.append(
            f"<article><h2>{html.escape(f.id)} · {distance:.1f} m</h2>"
            f"<p>{html.escape(reason or 'BOUND')}</p><div>{thumb(reference)}{thumb(preview)}</div></article>"
        )
    rows.sort(key=lambda r: r["distance_m"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        destination.with_suffix(".json"),
        {"rows": rows, "bound": sum(r["bound"] for r in rows), "total": len(rows)},
    )
    destination.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Office facade coverage</title>'
        "<style>body{font:15px system-ui;background:#182026;color:#eee;margin:2rem}"
        "article{border-top:1px solid #677;padding:1rem 0}article div{display:flex;gap:1rem}"
        "img{max-width:42vw;max-height:200px}p{color:#cbd}</style>"
        f"<h1>Street facades within {radius:.0f} m</h1><p>{sum(r['bound'] for r in rows)}"
        f"/{len(rows)} bound. Left: reference. Right: direct texture preview.</p>"
        + "".join(cards)
        + "</html>"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facades", type=Path)
    ap.add_argument("--refs", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--staging", type=Path, required=True)
    ap.add_argument("--library", type=Path, required=True)
    ap.add_argument("--ppm", type=int, default=32)
    ap.add_argument("--report-inventory", type=Path)
    ap.add_argument("--contact", type=Path)
    ap.add_argument("--contact-radius", type=float, default=120)
    ap.add_argument(
        "--labels", type=Path, help="grouped visual labels with usable/problem verdicts"
    )
    a = ap.parse_args(argv)
    facades = load_facades(a.facades)
    labels = json.loads(a.labels.read_text()) if a.labels and a.labels.exists() else {}
    rows = []
    for f in facades:
        try:
            verdict = labels.get(f.id)
            if verdict and not verdict["usable"]:
                with export_lock(a.output):
                    target = asset_path(f, a.output)
                    if target.with_suffix(".json").exists():
                        old = json.loads(target.with_suffix(".json").read_text())
                        if old.get("method") == "rectified-photo-bay-inpaint-v1":
                            for name in old["files"].values():
                                (target.parent / name).unlink(missing_ok=True)
                            target.with_suffix(".json").unlink()
                            manifest = a.output / "MANIFEST.json"
                            current = json.loads(manifest.read_text())
                            current["bound"] = [i for i in current["bound"] if i != f.id]
                            write_json(manifest, current)
                raise ValueError("visual label rejected reference: " + verdict["problem"])
            meta = build_one(f, a.refs, a.staging, a.library, ppm=a.ppm)
            if verdict and meta.get("visual_label") != verdict:
                meta["visual_label"] = verdict
                write_json(asset_path(f, a.staging).with_suffix(".json"), meta)
            with export_lock(a.output):
                manifest = a.output / "MANIFEST.json"
                current = json.loads(manifest.read_text())
                authoritative = a.output / "APPROVED_INVENTORY.geojson"
                if not authoritative.exists():
                    authoritative = (
                        Path(os.environ["TURKU3D_WORK"]) / "build/layers/buildings/facades.geojson"
                    )
                if current["inventory_sha256"] != sha(authoritative):
                    raise ValueError("assembly inventory changed")
                staged = asset_path(f, a.staging)
                target = asset_path(f, a.output)
                for name in meta["files"].values():
                    copy(staged.parent / name, target.parent / name)
                copy(staged.with_suffix(".json"), target.with_suffix(".json"))
                current["bound"] = sorted(set(current["bound"]) | {f.id})
                write_json(manifest, current)
            rows.append(
                {"facade_id": f.id, "bound": True, "reference_coverage": meta["visible_fraction"]}
            )
        except Exception as exc:
            rows.append(
                {"facade_id": f.id, "bound": False, "reason": f"{type(exc).__name__}: {exc}"}
            )
        print(json.dumps(rows[-1]), flush=True)
    write_json(a.staging.parent / "facade-direct-results.json", rows)
    if a.report_inventory and a.contact:
        contact_sheet(
            a.report_inventory,
            a.refs,
            a.staging,
            a.output,
            rows,
            a.contact,
            radius=a.contact_radius,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
