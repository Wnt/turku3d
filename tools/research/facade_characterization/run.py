"""Resumable offline MPS extraction, per-building fitting and local contact sheets."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from turkutex import facade_program as fp
from turkutex.facade_cv import MODEL_DIR, MODEL_REVISION
from turkutex.facade_models import MODELS, Engine
from turkutex.streetview.camera import EquirectModel
from turkutex.streetview.facades import load_facades
from turkutex.streetview.rectify import rectified_points, sample_wall


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, allow_nan=False))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def content_key(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def cache_matches(path, key, files=()):
    try:
        return json.loads(path.read_text()).get("input_key") == key and all(
            p.is_file() for p in files
        )
    except (OSError, ValueError):
        return False


def enrich(root):
    """Read-only WFS/OSM covariates and entrance projection; no photo features."""
    import shapely
    from turku3d import config as C
    from turku3d.coarse.sources import Sources
    from turku3d.spatial import rows
    from turkutex.facade_register_eval import footprints

    walls = load_facades(root / "inventory.geojson")
    attrs = json.loads((root / "attributes.json").read_text())
    shapes = footprints(walls)
    sources = Sources()
    paths = set()
    for wall in walls:
        tile = C.VisualTile.at(wall.centre[0] - C.ORIGIN_E, C.ORIGIN_N - wall.centre[1])
        p = sources.osm_source(tile)
        if p:
            paths.add(p)
    osm = {}
    for p in sorted(paths):
        for _, r in rows(p):
            tags = json.loads(r["tags"]) if r.get("tags") else {}
            if tags.get("building") or tags.get("entrance"):
                g = shapely.force_2d(r["geometry_local"])
                osm[(str(r.get("osm_id", r.get("feature_key"))), g.wkb)] = (g, tags)
    buildings = []
    entrances = []
    for (oid, _), (g, tags) in osm.items():
        if tags.get("entrance") and g.geom_type == "Point":
            entrances.append((oid, g, tags))
        if not tags.get("building"):
            continue
        if g.geom_type == "LineString" and g.is_ring:
            g = shapely.Polygon(g)
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        buildings.append((shapely.make_valid(g), tags))
    groups = defaultdict(list)
    for w in walls:
        groups[w.props["building_id"]].append(w)
    for bid, ww in groups.items():
        a = attrs[bid]
        if not a.get("material"):
            a["material_class"] = None
        if not a.get("levels") or a["levels"] <= 0:
            a["levels"] = None
        shape = shapes.get(bid)
        a.update(
            height_m=float(np.median([w.height_m for w in ww])),
            frontage_m=sum(w.length for w in ww),
            E=float(np.mean([w.centre[0] for w in ww])),
            N=float(np.mean([w.centre[1] for w in ww])),
            use=None,
            district=None,
            log_area=None,
            compactness=None,
            elongation=None,
        )
        if shape is not None and shape.area > 0:
            rect = shape.minimum_rotated_rectangle
            edges = np.linalg.norm(np.diff(np.array(rect.exterior.coords), axis=0), axis=1)
            a.update(
                log_area=float(np.log(shape.area)),
                compactness=float(4 * np.pi * shape.area / shape.length**2),
                elongation=float(edges.max() / max(0.1, edges.min())),
            )
            overlaps = [
                (shape.intersection(g).area / shape.union(g).area, t)
                for g, t in buildings
                if shape.intersects(g)
            ]
            if overlaps and max(x[0] for x in overlaps) > 0.4:
                tags = max(overlaps, key=lambda x: x[0])[1]
                a["use"] = tags.get("building:use", tags.get("building"))
                if not a.get("year"):
                    text = tags.get("start_date", tags.get("construction_year", ""))
                    if str(text).isdigit():
                        a["year"] = int(text)
                a["district"] = tags.get("addr:suburb")
    # The cadastral district is a location proxy, not an inferred style label.
    import pyarrow.parquet as pq

    registry_path = C.raw_dir("wfs/work-3x3") / "bldg_Building_LOD2.parquet"
    for r in pq.read_table(
        registry_path, columns=["pysyvarakennustunnus", "kiinteistotunnus", "usage"]
    ).to_pylist():
        bid = str(r["pysyvarakennustunnus"])
        if bid not in attrs:
            continue
        if r["kiinteistotunnus"]:
            attrs[bid]["district"] = str(r["kiinteistotunnus"])[:6]
        if r["usage"]:
            attrs[bid]["use"] = str(r["usage"])
    for bid, a in attrs.items():
        near = [
            (np.hypot(a["E"] - b["E"], a["N"] - b["N"]), b["height_m"])
            for k, b in attrs.items()
            if k != bid
        ]
        a["neighbour_height_m"] = float(np.mean([h for _, h in sorted(near)[:5]])) if near else None
    matched = defaultdict(list)
    for oid, point, tags in entrances:
        e, n = point.x + C.ORIGIN_E, C.ORIGIN_N - point.y
        choices = []
        for wall in walls:
            u = float((np.array([e, n]) - wall.p0) @ wall.tangent)
            if not 0 <= u <= wall.length:
                continue
            distance = abs(float((np.array([e, n]) - wall.p0) @ wall.normal))
            if distance <= 2:
                choices.append(
                    (distance, wall, u if wall.normal_side == "right" else wall.length - u)
                )
        if choices:
            _, wall, u = min(choices, key=lambda x: (x[0], x[1].id))
            matched[wall.id].append(dict(id=oid, u_m=u, entrance=tags.get("entrance")))
    write(root / "covariates.json", attrs)
    write(root / "entrances.json", matched)
    print(
        json.dumps(
            dict(
                stage="attributes",
                buildings=len(attrs),
                footprints=len(shapes),
                entrances=sum(map(len, matched.values())),
                availability={
                    k: sum(a.get(k) is not None for a in attrs.values())
                    for k in ["levels", "year", "use", "material_class", "district", "log_area"]
                },
            )
        ),
        flush=True,
    )


def extract(root):
    import torch
    from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    if not torch.backends.mps.is_available():
        raise RuntimeError("This benchmark requires MPS")
    started = time.monotonic()
    parser = Engine("cmp", device="mps")
    if (MODEL_DIR / "revision.txt").read_text().strip() != MODEL_REVISION:
        raise ValueError("ADE revision mismatch")
    processor = SegformerImageProcessor.from_pretrained(MODEL_DIR, local_files_only=True)
    ade = (
        SegformerForSemanticSegmentation.from_pretrained(MODEL_DIR, local_files_only=True)
        .eval()
        .to("mps")
    )
    load = time.monotonic() - started
    rows = json.loads((root / "selection.json").read_text())
    grouped = defaultdict(list)
    for row in rows:
        for index, view in enumerate(row["views"]):
            grouped[view["panorama"]].append((row, index, view))
    extractor_key = content_key(
        inspect.getsource(extract),
        inspect.getsource(rectified_points),
        inspect.getsource(sample_wall),
        MODELS["cmp"],
        MODEL_REVISION,
        torch.__version__,
    )
    done = 0
    for pano, items in sorted(grouped.items()):
        source = root / "panoramas" / pano
        start = time.monotonic()
        sha = digest(source)
        pending = []
        for row, index, view in items:
            name = f"{row['facade_id']}.{index}"
            key = content_key(sha, view, extractor_key)
            files = [root / "raw" / f"{name}.png", root / "raw" / f"{name}.npz"]
            if not cache_matches(root / "raw" / f"{name}.json", key, files):
                pending.append((row, index, view))
        if not pending:
            continue
        with Image.open(source) as image:
            rgb = np.array(image.convert("RGB"))
        decode = time.monotonic() - start
        for row, index, view in pending:
            name = f"{row['facade_id']}.{index}"
            start = time.monotonic()
            points = rectified_points(
                view["facade_corners_EN_H"], pixels_per_metre=24, max_side=1024
            )
            crop, valid = sample_wall(
                rgb,
                EquirectModel(0, rotation=np.array(view["image_to_grid"])),
                points,
                view["camera"]["pos_EN_H"],
            )
            crop = np.clip(crop, 0, 255).astype("uint8")
            rectify = time.monotonic() - start
            start = time.monotonic()

            def labels(model, proc, crop=crop):
                inputs = proc(images=Image.fromarray(crop), return_tensors="pt").to("mps")
                with torch.inference_mode():
                    logits = model(**inputs).logits
                    result = (
                        torch.nn.functional.interpolate(
                            logits, size=crop.shape[:2], mode="bilinear", align_corners=False
                        )
                        .argmax(1)[0]
                        .cpu()
                        .numpy()
                    )
                return result.astype("uint8")

            cmp = labels(parser.model, parser.processor)
            semantic = labels(ade, processor)
            excluded = np.isin(
                semantic, [2, 4, 6, 9, 11, 12, 17, 20, 21, 80, 83, 90, 102, 116, 127]
            )
            inference = time.monotonic() - start
            (root / "raw").mkdir(exist_ok=True)
            Image.fromarray(crop).save(root / "raw" / f"{name}.png")
            np.savez_compressed(
                root / "raw" / f"{name}.npz", labels=cmp, excluded=excluded, coverage=valid
            )
            write(
                root / "raw" / f"{name}.json",
                dict(
                    input_key=content_key(sha, view, extractor_key),
                    panorama_sha256=sha,
                    view=view,
                    crop_sha256=digest(root / "raw" / f"{name}.png"),
                    cmp_revision=MODELS["cmp"][1],
                    ade_revision=MODEL_REVISION,
                    seconds=dict(
                        decode_share=decode / len(pending), rectify=rectify, inference=inference
                    ),
                ),
            )
            done += 1
            if done % 25 == 0:
                print(
                    json.dumps(
                        dict(stage="mps", views=done, seconds=round(time.monotonic() - started, 1))
                    ),
                    flush=True,
                )
        del rgb
    write(
        root / "model-run.json",
        dict(
            device="mps",
            torch=torch.__version__,
            model_load_seconds=load,
            total_invocation_seconds=time.monotonic() - started,
            new_views=done,
        ),
    )


def fit(root):
    rows = json.loads((root / "selection.json").read_text())
    attrs = json.loads((root / "covariates.json").read_text())
    entrances = json.loads((root / "entrances.json").read_text())
    groups = defaultdict(list)
    details = {}
    start = time.monotonic()
    for row in rows:
        choices = []
        for index, _view in enumerate(row["views"]):
            name = f"{row['facade_id']}.{index}"
            with Image.open(root / "raw" / f"{name}.png") as im:
                rgb = np.array(im)
            with np.load(root / "raw" / f"{name}.npz") as m:
                wall = fp.fit_wall(
                    rgb,
                    m["labels"],
                    m["excluded"],
                    coverage=m["coverage"],
                    wall_id=row["facade_id"],
                    width=row["width_m"],
                    height=row["height_m"],
                    floors=attrs[row["building_id"]]["levels"],
                    entrances=entrances.get(row["facade_id"], []),
                )
            score = wall["observation"]["wall_share"] * wall["observation"]["visible_share"]
            choices.append((score, wall, name))
        _, wall, name = max(choices, key=lambda r: r[0])
        wall["evidence"] = dict(
            selected_view=name,
            alternative_views=[n for _, _, n in choices if n != name],
            photo=str(root / "raw" / f"{name}.png"),
        )
        wall["geometry"] = {k: row[k] for k in ["p0", "p1", "normal", "base_h"]}
        groups[row["building_id"]].append(wall)
        details[wall["wall_id"]] = name
    buildings = [fp.merge_building(bid, attrs[bid], walls) for bid, walls in sorted(groups.items())]
    write(root / "programs.json", buildings)
    write(root / "fit-timing.json", dict(seconds=time.monotonic() - start, walls=len(rows)))
    print(json.dumps(dict(stage="fit", buildings=len(buildings), walls=len(rows))), flush=True)


def doors(root):
    programs = json.loads((root / "programs.json").read_text())
    start = time.monotonic()
    model = Engine("grounding-dino-openings", device="mps", size=800)
    load = time.monotonic() - start
    refine_seconds = 0.0
    for b in programs:
        for w in b["walls"]:
            out = root / "doors" / f"{w['wall_id']}.json"
            crop_hash = digest(Path(w["evidence"]["photo"]))
            detector_key = content_key(
                crop_hash, MODELS["grounding-dino"], inspect.getsource(Engine.detect), model.size
            )
            if not cache_matches(out, detector_key):
                with Image.open(w["evidence"]["photo"]) as im:
                    rgb = np.array(im)
                start = time.monotonic()
                boxes = model.detect(rgb)
                elapsed = time.monotonic() - start
                write(
                    out,
                    dict(
                        input_key=detector_key,
                        crop_sha256=crop_hash,
                        boxes=boxes,
                        seconds=elapsed,
                        model_revision=MODELS["grounding-dino"][1],
                    ),
                )
            refine_start = time.monotonic()
            d = json.loads(out.read_text())
            name = w["evidence"]["selected_view"]
            with Image.open(w["evidence"]["photo"]) as im:
                rgb = np.array(im)
            with np.load(root / "raw" / f"{name}.npz") as m:
                labels, count = fp.opening_labels(
                    m["labels"], d["boxes"], m["excluded"], w["width_m"], w["height_m"]
                )
                entrances = [
                    dict(u_m=e["u_m"], id=e["osm_id"], entrance=e.get("type"))
                    for e in w["doors"]["candidates"]
                    if e["source"] == "OSM entrance"
                ]
                refined = fp.fit_wall(
                    rgb,
                    labels,
                    m["excluded"],
                    coverage=m["coverage"],
                    wall_id=w["wall_id"],
                    width=w["width_m"],
                    height=w["height_m"],
                    floors=b["storeys"]["value"],
                    doors=d["boxes"],
                    entrances=entrances,
                )
            for field in ("size_m", "aspect", "sill_fraction"):
                refined["windows"][field]["source"] = "DINO boxes / registered bands"
            refined["bays"]["source"] = "DINO opening mask"
            evidence, geometry = w["evidence"], w["geometry"]
            w.update(refined)
            w.update(
                evidence=evidence,
                geometry=geometry,
                opening_source="DINO metric/NMS boxes",
                detector_window_count=count,
            )
            refine_seconds += time.monotonic() - refine_start

        if len(list((root / "doors").glob("*.json"))) % 20 == 0:
            print(
                json.dumps(dict(stage="doors", buildings_completed=programs.index(b) + 1)),
                flush=True,
            )
    programs = [fp.merge_building(b["building_id"], b["attributes"], b["walls"]) for b in programs]
    write(root / "programs.json", programs)
    write(root / "detector-run.json", dict(model_load_seconds=load, refine_seconds=refine_seconds))


def rules(root):
    programs = json.loads((root / "programs.json").read_text())
    rules = fp.fit_rules(programs)
    write(root / "rules.json", rules)
    attrs = json.loads((root / "covariates.json").read_text())
    known = {b["building_id"] for b in programs}
    geometry = defaultdict(list)
    for w in load_facades(root / "inventory.geojson"):
        geometry[w.props["building_id"]].append(
            dict(wall_id=w.id, width_m=w.length, height_m=w.height_m)
        )
    sampled = [
        fp.sample_building(bid, a, geometry[bid], rules)
        for bid, a in sorted(attrs.items())
        if bid not in known
    ]
    write(root / "sampled.json", sampled)
    # Building-held-out predictions: no wall of the target contributes to its donors.
    scores = []
    from turkutex.facade_style import lab

    def colour(c):
        return lab(np.array([int(c[i : i + 2], 16) for i in (1, 3, 5)]))

    for b in programs:
        if b["representative_wall"] is None:
            continue
        actual = next(w for w in b["walls"] if w["wall_id"] == b["representative_wall"])
        if actual["bays"]["count"] < 2:
            continue
        row = {}
        for conditional in [True, False]:
            p = fp.sample_building(
                b["building_id"],
                b["attributes"],
                [
                    dict(
                        wall_id=actual["wall_id"],
                        width_m=actual["width_m"],
                        height_m=actual["height_m"],
                    )
                ],
                rules,
                conditional=conditional,
            )
            w = p["walls"][0]
            row["conditional" if conditional else "unconditional"] = dict(
                colour_delta=float(
                    np.linalg.norm(
                        colour(w["wall"]["colour"]["value"])
                        - colour(actual["wall"]["colour"]["value"])
                    )
                ),
                bay_count_error=abs(w["bays"]["count"] - actual["bays"]["count"]),
                donor=p["donor_building_id"],
            )
        scores.append(row)
    write(
        root / "rules-evaluation.json",
        dict(
            n=len(scores),
            interpretation="Held-out agreement with noisy automatically characterized buildings, not human accuracy",
            summary={
                kind: {
                    field: float(np.median([r[kind][field] for r in scores]))
                    for field in ["colour_delta", "bay_count_error"]
                }
                for kind in ["conditional", "unconditional"]
            },
            rows=scores,
        ),
    )
    print(
        json.dumps(
            dict(
                stage="rules",
                donors=len(rules["exemplars"]),
                unphotographed=len(sampled),
                held_out=len(scores),
            )
        ),
        flush=True,
    )


def contact(root):
    programs = json.loads((root / "programs.json").read_text())
    # Deterministic spread across all buildings with an actual measured colour.
    candidates = [b for b in programs if b["representative_wall"]]
    candidates.sort(key=lambda b: fp.stable_seed(b["building_id"], "contact"))
    selected = candidates[:30]
    sheet = Image.new("RGB", (1500, 10 * 260), "#ecebe7")
    draw = ImageDraw.Draw(sheet)
    index = []
    paint_seconds = []
    for i, b in enumerate(selected):
        w = next(w for w in b["walls"] if w["wall_id"] == b["representative_wall"])
        x = (i % 3) * 500
        y = (i // 3) * 260
        with Image.open(w["evidence"]["photo"]) as im:
            photo = ImageOps.contain(im.convert("RGB"), (240, 215))
        t = time.monotonic()
        render = fp.paint(w, b["storeys"]["value"])
        paint_seconds.append(time.monotonic() - t)
        render = ImageOps.contain(render, (240, 215))
        sheet.paste(photo, (x, y + 35))
        sheet.paste(render, (x + 250, y + 35))
        draw.text(
            (x + 5, y + 4),
            f"{i + 1:02d} {b['building_id']} | floors {b['storeys']['value']} bays {w['bays']['count']}",
            fill="black",
        )
        draw.text(
            (x + 5, y + 20), "Photo                          Program (diagnostic)", fill="black"
        )
        index.append(dict(index=i + 1, building_id=b["building_id"], wall_id=w["wall_id"]))
    sheet.save(root / "contact-30.jpg", quality=92)
    write(root / "contact-index.json", index)
    write(
        root / "painter-timing.json",
        dict(
            walls=len(paint_seconds),
            seconds=sum(paint_seconds),
            seconds_per_wall=float(np.mean(paint_seconds)),
            resolution=512,
            excludes="contact layout, PNG/JPEG/KTX encoding",
        ),
    )
    sampled = json.loads((root / "sampled.json").read_text())[:12]
    im = Image.new("RGB", (1200, 3 * 240), "#ecebe7")
    d = ImageDraw.Draw(im)
    for i, b in enumerate(sampled):
        w = b["walls"][0]
        x = i % 4 * 300
        y = i // 4 * 240
        im.paste(ImageOps.contain(fp.paint(w, b["storeys"]["value"]), (290, 205)), (x, y + 30))
        d.text((x + 3, y + 3), b["building_id"] + " RULE SAMPLE", fill="black")
        d.text((x + 3, y + 17), "floors " + str(b["storeys"]["value"]), fill="black")
    im.save(root / "contact-no-photos.jpg", quality=92)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["enrich", "extract", "fit", "doors", "rules", "contact"])
    p.add_argument("--root", type=Path, required=True)
    a = p.parse_args()
    globals()[a.stage](a.root)


if __name__ == "__main__":
    main()
