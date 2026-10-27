"""Freeze geometry-derived label proxies and deterministic harder QA differences."""

import hashlib
import json
import math
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image, ImageDraw, ImageStat

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ["TURKU3D_WORK"]) / "reports/costbench/v2"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def labels():
    layer = Path.home() / "turku3d/wt/w1-03-buildings/work/build/layers/buildings"
    records = {
        r["building_id"]: r for r in json.loads((layer / "metrics.json").read_text())["buildings"]
    }
    facades = {r["id"]: r for r in json.loads((layer / "facades.geojson").read_text())["features"]}
    city_path = Path(os.environ["TURKU3D_DATA"]) / "raw/wfs/work-3x3/bldg_Building_LOD2.parquet"
    city = gpd.read_parquet(city_path)
    roof = city[city.surface_type == "RoofSurface"]
    cases = []
    images = []
    used = set()
    refs = Path.home() / "turku3d/wt/w1-09b-facades/work/textures/refs"
    candidates = []
    for p in sorted(refs.glob("*/refs.json")):
        d = json.loads(p.read_text())
        f = d["facade"]
        prop = f["properties"]
        obj = prop.get("object_id")
        if obj not in facades or obj in used:
            continue
        auth = facades[obj]
        a = auth["properties"]
        b = records[a["building_id"]]
        if (
            not a["street_facing"]
            or a["width_m"] < 3
            or a["top_height"] - a["base_height"] < 3
            or not b["pass"]
        ):
            continue
        if not np.allclose(
            f["geometry"]["coordinates"], auth["geometry"]["coordinates"], rtol=0, atol=0.02
        ):
            continue
        if not d["views"]:
            continue
        v = json.loads((p.parent / (d["views"][0] + ".json")).read_text())
        image = p.parent / v["files"]["outline"]
        with Image.open(image) as im:
            stats = ImageStat.Stat(im.convert("RGB"))
            if np.mean(stats.mean) < 35 or np.mean(stats.stddev) < 15:
                continue
        candidates.append((a["width_m"], a, b, image))
        used.add(obj)
    # Prefer broad walls, one per building first, then additional walls to reach 12.
    candidates.sort(key=lambda v: -v[0])
    selected = []
    buildings = set()
    for candidate in candidates:
        if candidate[1]["building_id"] not in buildings:
            selected.append(candidate)
            buildings.add(candidate[1]["building_id"])
    selected += [c for c in candidates if c not in selected]
    for _, a, b, image in selected[:12]:
        i = len(cases)
        dest = WORK / f"label-{i:02d}.jpg"
        with Image.open(image) as im:
            im.thumbnail((1024, 1024))
            im.convert("RGB").save(dest, quality=92)
        props = b["semantics"]
        material = str(props.get("facade_material", "")).lower()
        wall = next(
            (
                v
                for k, v in [
                    ("tiili", "brick"),
                    ("puu", "wood"),
                    ("rapp", "render"),
                    ("beton", "concrete"),
                    ("metalli", "metal"),
                ]
                if k in material
            ),
            None,
        )
        city_id = (b.get("city_match") or {}).get("city_id")
        rs = roof[roof.gml_id == city_id]
        slopes = []
        for geom in rs.geometry:
            if geom.geom_type != "Polygon":
                continue
            pts = np.asarray(geom.exterior.coords)
            if pts.shape[1] != 3:
                continue
            _, _, vh = np.linalg.svd(pts - pts.mean(0))
            normal = vh[-1]
            slopes.append(math.degrees(math.acos(min(1, abs(normal[2])))))
        shape = ("flat" if np.median(slopes) < 10 else "pitched") if slopes else None
        rgb = b["roof"].get("rgb_median")
        key = b["roof"]["material_key"]
        colour = key.rsplit("_", 1)[-1] if rgb else None
        truth = {
            "floors": max(1, int(math.floor((a["top_height"] - a["base_height"]) / 3.2 + 0.5)))
        }
        if wall:
            truth["wall_material"] = wall
        if shape:
            truth["roof_shape"] = shape
        if colour:
            truth["roof_colour"] = colour
        cases.append(
            {
                "id": a["object_id"],
                "truth": truth,
                "truth_kind": "geometry-derived",
                "base_height": a["base_height"],
                "eave_top_proxy": a["top_height"],
                "floors_rule": "round_half_up((wall top - base)/3.2); gables and split-level walls remain proxy uncertainty",
                "roof_shape_source": "City LoD2 RoofSurface median plane slope; flat <10deg",
                "city_id": city_id,
                "roof_planes": len(slopes),
                "wall_source_value": material,
                "roof_colour_rgb": rgb,
                "roof_material": None,
                "input": str(dest),
                "sha256": digest(dest),
            }
        )
        images.append(str(dest))
    if len(cases) != 12:
        raise ValueError(f"Only {len(cases)} valid geometry-matched reference facades")
    properties = {
        "floors": {"type": "integer", "minimum": 0},
        "wall_material": {
            "type": "string",
            "enum": ["brick", "wood", "render", "concrete", "metal", "unknown"],
        },
        "roof_shape": {"type": "string", "enum": ["flat", "pitched", "unknown"]},
        "roof_colour": {"type": "string", "enum": ["gray", "dark", "red", "green", "unknown"]},
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["facades"],
        "properties": {
            "facades": {
                "type": "array",
                "minItems": 12,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(properties),
                    "properties": properties,
                },
            }
        },
    }
    (ROOT / "bench/codex/label-v2.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
    truth = {
        "version": "geometry-derived-v2",
        "sources": {
            str(layer / "metrics.json"): digest(layer / "metrics.json"),
            str(layer / "facades.geojson"): digest(layer / "facades.geojson"),
            str(city_path): digest(city_path),
        },
        "cases": cases,
    }
    (ROOT / "bench/codex/label-v2.json").write_text(json.dumps(truth, indent=2) + "\n")
    prompt = "Label the 12 numbered street facade images in filename order. Count visible above-ground floors on the outlined target facade; estimate when partly occluded. Classify wall material, roof shape flat/pitched, and roof colour gray/dark/red/green. Use unknown if unobservable. Do not inspect external files or use tools. Return exactly 12 facade labels. No geometry proxy answers are provided."
    return {
        "prompt": prompt,
        "images": images,
        "schema": str(ROOT / "bench/codex/label-v2.schema.json"),
        "meta": {
            "kind": "building_label",
            "truth_file": "bench/codex/label-v2.json",
            "facade_ids": [c["id"] for c in cases],
        },
    }


def hard_qa():
    images = []
    cases = []
    for i in range(8):
        src = WORK.parent / "qa-inputs" / f"{[0, 6, 2, 4, 6, 6, 6, 2][i]:02d}_reference.png"
        with Image.open(src) as im:
            ref = im.convert("RGB")
        out = ref.copy()
        w, h = ref.size
        if i < 4:
            polygons = [
                [(0.23, 0.28), (0.51, 0.42), (0.51, 0.70), (0.23, 0.72)],
                [(0.78, 0.16), (0.99, 0.03), (0.99, 0.74), (0.78, 0.66)],
                [(0.57, 0.27), (0.99, 0), (0.99, 0.12), (0.57, 0.34)],
                [(0.62, 0.42), (0.82, 0.34), (0.84, 0.43), (0.63, 0.49)],
            ]
            polygon = polygons[i]
            mask = Image.new("L", ref.size)
            ImageDraw.Draw(mask).polygon([(int(x * w), int(y * h)) for x, y in polygon], fill=255)
            arr = np.asarray(ref, dtype=float)
            gain = np.array([1.09, 0.98, 0.91]) if i < 2 else np.array([0.65, 1.08, 0.85])
            changed = Image.fromarray(np.uint8(np.clip(arr * gain, 0, 255)))
            out.paste(changed, (0, 0), mask)
            bbox = [
                min(p[0] for p in polygon),
                min(p[1] for p in polygon),
                max(p[0] for p in polygon),
                max(p[1] for p in polygon),
            ]
            what = "subtle facade colour shift" if i < 2 else "wrong roof colour"
            category = "material"
        elif i < 6:
            # Controlled procedural balcony on the reference; remove it in the render.
            bbox = [0.82 if i == 4 else 0.72, 0.35, 0.94 if i == 4 else 0.82, 0.43]
            x0, y0, x1, y1 = [int(v * (w if k % 2 == 0 else h)) for k, v in enumerate(bbox)]
            draw = ImageDraw.Draw(ref)
            draw.rectangle((x0, y0, x1, y1), fill=(91, 88, 79), outline=(35, 35, 35), width=2)
            for x in range(x0, x1, 7):
                draw.line((x, y0, x, y1), fill=(170, 165, 150), width=2)
            what = "missing planted balcony"
            category = "missing_object"
        else:
            bbox = [0.72, 0.14, 0.99, 0.38] if i == 6 else [0.67, 0.10, 0.99, 0.32]
            x0, y0, x1, y1 = [int(v * (w if k % 2 == 0 else h)) for k, v in enumerate(bbox)]
            strip = ref.crop((x0, y1, x1, min(h, y1 + y1 - y0))).resize((x1 - x0, y1 - y0))
            out.paste(strip, (x0, y0))
            what = "extra storey cloned above existing facade"
            category = "geometry"
        rp = WORK / f"qa-{i:02d}-reference.png"
        op = WORK / f"qa-{i:02d}-render.png"
        ref.save(rp)
        out.save(op)
        images += [str(rp), str(op)]
        cases.append(
            {
                "id": i,
                "truth": [{"bbox": bbox, "category": category, "what": what}],
                "source": str(src),
                "source_sha256": digest(src),
                "input_sha256": [digest(rp), digest(op)],
            }
        )
    (ROOT / "bench/codex/qa-v2.json").write_text(
        json.dumps({"version": "hard-planted-v2", "cases": cases}, indent=2) + "\n"
    )
    import sys

    sys.path.insert(0, str(ROOT / "qa"))
    from judge.judge import build_prompt

    return {
        "prompt": "Judge eight independent pairs qa-00 through qa-07, reference then render. Return judgements in numerical order. Do not use external tools.\n"
        + build_prompt(),
        "images": images,
        "schema": str(ROOT / "bench/codex/qa-batch.schema.json"),
        "meta": {
            "kind": "qa_batch",
            "truth_file": "bench/codex/qa-v2.json",
            "views": list(range(8)),
        },
    }


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    jobs = [
        {**labels(), "id": "label.template"},
        {**hard_qa(), "id": "qa.template"},
    ]
    (WORK / "jobs.jsonl").write_text("".join(json.dumps(j) + "\n" for j in jobs))
    print(len(jobs), "job templates")
