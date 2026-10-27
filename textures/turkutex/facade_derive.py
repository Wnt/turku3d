"""Building styles for the office core: choose each building's source map, plan every
street-facing core wall, stage the derived maps, draw the contact sheet and publish.

styles   one style record per building with an accepted generated map (facade_style)
plan     status of every street-facing core wall: generate, derived, derived-neighbour
derive   stage the derived and derived-neighbour walls (KTX2 maps and sidecars)
contact  source map and derived walls per building at one metric scale
publish  bind the staged walls into the approval root under the export lock
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .facade_batch import asset_path, uv_transform, write_json
from .facade_style import (
    MAP_KINDS,
    STYLE_VERSION,
    code_hash,
    derive_maps,
    derived_qa,
    style_record,
)
from .paths import tools_dir
from .post.pipeline import encode_ktx2, sha

# ------------------------------------------------------------------------------------------
# KTX2 maps on disk
# ------------------------------------------------------------------------------------------


def decode_ktx2(path: Path) -> np.ndarray:
    """Level 0 of a KTX2 map as RGB uint8 (transcoded by KTX-Software)."""
    tool = tools_dir() / "ktx/bin/ktx"
    if not tool.is_file():
        raise RuntimeError("KTX-Software is required; run make textures-ktx")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "level0.png"
        subprocess.run(
            [str(tool), "extract", "--transcode", "rgba8", str(path), str(out)],
            check=True,
            capture_output=True,
        )
        with Image.open(out) as image:
            return np.asarray(image.convert("RGB"))


def published_maps(assembly: Path, meta: dict, kinds=MAP_KINDS) -> dict[str, np.ndarray]:
    building = assembly / "facades" / meta["building_id"]
    maps = {}
    for kind in kinds:
        path = building / meta["files"][kind]
        if sha(path) != meta["hashes"][path.name]:
            raise ValueError(f"{path}: map does not match its sidecar hash")
        maps[kind] = decode_ktx2(path)
    return maps


# ------------------------------------------------------------------------------------------
# Buildings, walls and style selection
# ------------------------------------------------------------------------------------------

KANTA_COLUMNS = ["feature_key", "rakennustunnus", "julkisivumateriaali", "kerrosluku"] + [
    "kottovuosi",
    "teksti",
    "osoitenumero",
]
GIS_COLUMNS = ["feature_key", "PysyvaRakennusTunnus", "Valmistunut", "Kerrosluku"] + [
    "Osoitenimi_fi",
    "Osoitenumero",
]
# Storey height used only to estimate a missing register storey count.
STOREY_M = 3.3
MATERIAL_CLASSES = {"puu": "wood", "tiili": "brick", "betoni": "concrete", "metallilevy": "metal"}


def registry_key(building_id: str) -> tuple[str, str]:
    """('registry' | 'kanta' | 'gis', key) of a buildings-layer ID."""
    base = building_id.split("-part")[0]
    if base.startswith("kanta-"):
        return "kanta", base[len("kanta-") :]
    if base.startswith("gis-"):
        return "gis", base[len("gis-") :].split("-")[0]
    return "registry", base


def _year(value) -> int | None:
    import re

    found = re.search(r"(1[6-9]\d\d|20\d\d)", str(value or ""))
    return int(found.group(1)) if found else None


def building_attributes(wfs: Path, building_ids) -> dict[str, dict]:
    """Facade material, levels, completion year and address from the kantakartta and GIS
    building registers (the buildings layer's own inputs)."""
    import pyarrow.parquet as pq

    kanta = pq.read_table(wfs / "kanta_Rakennus.parquet", columns=KANTA_COLUMNS).to_pylist()
    gis = pq.read_table(wfs / "GIS_Rakennukset.parquet", columns=GIS_COLUMNS).to_pylist()
    out = {}
    for building_id in building_ids:
        kind, key = registry_key(building_id)
        rows_k = [
            r
            for r in kanta
            if (r["feature_key"] == key if kind == "kanta" else r["rakennustunnus"] == key)
        ]
        rows_g = [
            r
            for r in gis
            if (r["PysyvaRakennusTunnus"] == key or r["feature_key"] == key) and kind != "kanta"
        ]
        material = next((r["julkisivumateriaali"] for r in rows_k if r["julkisivumateriaali"]), "")
        year = next(
            (y for r in rows_k if (y := _year(r["kottovuosi"]))),
            next((y for r in rows_g if (y := _year(r["Valmistunut"]))), None),
        )
        levels = [
            int(v)
            for r in rows_k + rows_g
            for v in (r.get("kerrosluku"), r.get("Kerrosluku"))
            if str(v or "").isdigit()
        ]
        address = next(
            (f"{r['teksti']} {r['osoitenumero'] or ''}".strip() for r in rows_k if r["teksti"]),
            next(
                (
                    f"{r['Osoitenimi_fi']} {r['Osoitenumero'] or ''}".strip()
                    for r in rows_g
                    if r["Osoitenimi_fi"]
                ),
                "",
            ),
        )
        out[building_id] = {
            "material": material,
            "material_class": MATERIAL_CLASSES.get(material, "render"),
            "year": year,
            "era": era(year),
            "levels": max(levels) if levels else None,
            "address": address,
        }
    return out


def era(year: int | None) -> str:
    if year is None:
        return "unknown"
    for limit, name in ((1850, "pre-1850"), (1920, "1850-1919"), (1960, "1920-1959")):
        if year < limit:
            return name
    return "1960+"


def core_walls(inventory: Path, building: str | None = None) -> list:
    """Street-facing office-core walls, or all street-facing walls of one building."""
    from turku3d import config

    from .streetview.facades import load_facades

    core = config.OFFICE_BLOCK.core.to_en()
    return [
        f
        for f in load_facades(inventory)
        if f.props.get("street_facing")
        and (
            f.props["building_id"] == building
            if building is not None
            else core.emin <= f.centre[0] <= core.emax and core.nmin <= f.centre[1] <= core.nmax
        )
    ]


def generated_sidecars(assembly: Path) -> dict[str, dict]:
    """Published sidecars of accepted generated maps, by facade ID."""
    found = {}
    for path in sorted((assembly / "facades").glob("*/*.json")):
        if path.name == "style.json":
            continue
        meta = json.loads(path.read_text())
        if meta.get("source_mode") == "generate" and meta.get("generated_qa"):
            found[f"{meta['building_id']}_{meta['facade_index']}"] = meta
    return found


def trial(style: dict, albedo: np.ndarray, walls) -> int:
    """How many of the walls derive from this style without a QA problem (albedo only)."""
    passed = 0
    for wall in walls:
        for plain_columns in (False, True):
            maps = {"albedo": albedo, "normal": albedo, "orm": albedo}
            _, plan = derive_maps(
                style, maps, wall.length, wall.height_m, plain_columns=plain_columns
            )
            if derived_qa(style, albedo, plan, wall.length, wall.height_m)["pass"]:
                passed += 1
                break
    return passed


def choose_styles(
    assembly: Path, inventory: Path, wfs: Path, output: Path, building: str | None = None
) -> dict:
    """One style record per building with an accepted generated map: the candidate whose
    derivations pass QA on most of the building's other core walls, then the largest."""
    walls = core_walls(inventory, building)
    generated = generated_sidecars(assembly)
    by_building: dict[str, list] = {}
    for wall in walls:
        by_building.setdefault(wall.props["building_id"], []).append(wall)
    candidates: dict[str, list[dict]] = {}
    for meta in generated.values():
        if building is not None and meta["building_id"] != building:
            continue
        candidates.setdefault(meta["building_id"], []).append(meta)
    attributes = building_attributes(wfs, sorted(set(by_building) | set(candidates)))
    index = {}
    for building_id, metas in sorted(candidates.items()):
        targets = [w for w in by_building.get(building_id, []) if w.id not in generated]
        scored = []
        for meta in metas:
            albedo = published_maps(assembly, meta, ("albedo",))["albedo"]
            style = style_record(albedo, meta, attributes[building_id])
            passed = trial(style, albedo, targets)
            area = meta["width_m"] * meta["height_m"]
            openings = len(style["plan"]["rows"]["openings_px"])
            scored.append(((passed, openings > 0, area), style, passed))
        scored.sort(key=lambda item: item[0], reverse=True)
        _, best, passed = scored[0]
        best["selection"] = {
            "rule": "most derived walls passing QA, then rows of openings, then area",
            "target_walls": len(targets),
            "candidates": [
                {"source": s["source"], "walls_passing": p, "area_m2": round(key[2], 1)}
                for key, s, p in scored
            ],
        }
        path = output / "facades" / building_id / "style.json"
        write_json(path, best)
        index[building_id] = {"source": best["source"], "walls_passing": passed, "of": len(targets)}
    write_json(output / "styles.json", {"styles": index, "attributes": attributes})
    return index


def assign_neighbour(building: str, centre, attributes: dict, styled: dict[str, dict]) -> dict:
    """The styled building with the closest storey count (3 per storey, at most 6), facade
    material class (2) and era (1), then the nearest."""
    own = attributes[building]

    def rank(other):
        theirs = attributes.get(other, {})
        levels = (own.get("levels") or 0, theirs.get("levels") or 0)
        mismatch = (
            3 * min(abs(levels[0] - levels[1]), 2)
            + 2 * (theirs.get("material_class") != own["material_class"])
            + (theirs.get("era") != own["era"])
        )
        return mismatch, float(np.linalg.norm(styled[other]["centre"] - centre))

    best = min(styled, key=rank)
    mismatch, distance = rank(best)
    return {"style_building": best, "mismatch": mismatch, "distance_m": round(distance, 1)}


def plan_walls(
    assembly: Path, inventory: Path, styles: Path, output: Path, building: str | None = None
) -> dict:
    """Status of every street-facing core wall: generate, derived, derived-neighbour."""
    walls = core_walls(inventory, building)
    generated = generated_sidecars(assembly)
    index = json.loads((styles / "styles.json").read_text())
    attributes = index["attributes"]
    centres: dict[str, list] = {}
    for wall in walls:
        centres.setdefault(wall.props["building_id"], []).append(wall.centre)
    centre = {b: np.mean(c, axis=0) for b, c in centres.items()}
    for building in centres:
        if not attributes.get(building, {}).get("levels"):
            # No register storey count: estimate it from the tallest wall.
            tallest = max(w.height_m for w in walls if w.props["building_id"] == building)
            attributes.setdefault(building, {})["levels"] = max(1, round(tallest / STOREY_M))
    styled = {b: {"centre": centre[b]} for b in index["styles"] if b in centre}
    neighbours = {
        b: assign_neighbour(b, centre[b], attributes, styled)
        for b in sorted(centres)
        if b not in index["styles"]
    }
    rows = []
    for wall in walls:
        building = wall.props["building_id"]
        row = {
            "facade_id": wall.id,
            "building_id": building,
            "width_m": round(wall.length, 3),
            "height_m": round(wall.height_m, 3),
        }
        if wall.id in generated:
            row["status"] = "generate"
        elif building in index["styles"]:
            row.update(status="derived", style_building=building)
        else:
            row.update(status="derived-neighbour", **neighbours[building])
        rows.append(row)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    result = {
        "inventory_sha256": sha(inventory),
        "counts": counts,
        "neighbours": {
            b: {**n, **{k: attributes[b][k] for k in ("material", "year", "levels", "address")}}
            for b, n in neighbours.items()
        },
        "walls": rows,
    }
    write_json(output, result)
    return result


# ------------------------------------------------------------------------------------------
# Derived walls: staging, contact sheet, publication
# ------------------------------------------------------------------------------------------

# Minimum packaged density (px/m) of every generated and derived office-core map: equal for a
# building's walls, so the packager scales them together (the style source is not sharper than
# its derived walls) and 16 px/m keeps 6 cm detail where the 8 MB tile cap allows nothing more.
DENSITY_FLOOR = 16
ENCODINGS = {"albedo": ("etc1s", False), "orm": ("etc1s", True), "normal": ("uastc-normal", True)}


def derive_wall(facade, style: dict, style_path: Path, maps: dict, output: Path, mode: str):
    """Stage one derived wall (three KTX2 maps and a sidecar) under output/facades; the bay
    plan first, the plain-wall columns if its QA fails. Returns the sidecar or None."""
    width, height = facade.length, facade.height_m
    for plain_columns in (False, True):
        derived, plan = derive_maps(style, maps, width, height, plain_columns=plain_columns)
        qa = derived_qa(style, maps["albedo"], plan, width, height)
        if qa["pass"]:
            break
    else:
        return {"facade_id": facade.id, "qa": qa, "staged": False}
    target = asset_path(facade, output)
    post = output / "post" / facade.id
    post.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    files, valid = {}, True
    for kind in MAP_KINDS:
        png = post / f"{kind}.png"
        Image.fromarray(derived[kind]).save(png)
        name = target.name + (".ktx2" if kind == "albedo" else f".{kind}.ktx2")
        valid &= encode_ktx2(png, target.parent / name, *ENCODINGS[kind])["gltf_basisu_valid"]
        files[kind] = name
    size = plan["size"]
    params = {
        "version": STYLE_VERSION,
        "code": code_hash(),
        "style_sha256": sha(style_path),
        "facade": facade.to_feature(),
        "mode": mode,
    }
    record = {
        "key": hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest(),
        "facade_id": facade.id,
        "building_id": facade.props["building_id"],
        "facade_index": facade.props["facade_index"],
        "width_m": width,
        "height_m": height,
        "size": size,
        "pixels_per_metre": [size[0] / width, size[1] / height],
        "uv": uv_transform(facade),
        "references": [],
        "source_mode": mode,
        "derived_from": style["source"],
        "style": {
            "building_id": style["building_id"],
            "path": f"facades/{style['building_id']}/style.json",
            "sha256": sha(style_path),
        },
        "geometry_source": facade.props.get("height_source", "buildings layer"),
        "min_pixels_per_metre": DENSITY_FLOOR,
        "preview_sha256": sha(post / "albedo.png"),
        "files": files,
        "hashes": {n: sha(target.parent / n) for n in files.values()},
        "derive": {
            "rows": {k: plan["rows"][k] for k in ("mode", "seams")},
            "plain_columns": plan["plain_columns"],
            "groups": [
                {k: v for k, v in g.items() if k in ("label", "rows", "mode", "seams", "repeats")}
                for g in plan["groups"]
            ],
        },
        "qa": {
            "seam": "not applicable: unique wall, clamped UVs",
            "scale_relative_error": qa["scale_relative_error"],
            "content_aspect_error": 0.0,
            "gltf_basisu_valid": bool(valid),
            "normal_method": "source normal map cut along the derive plan, renormalized",
            "derived": qa,
        },
    }
    write_json(target.with_suffix(".json"), record)
    return {"facade_id": facade.id, "qa": qa, "staged": True, "plain_columns": plain_columns}


def derive_planned(plan_path: Path, assembly: Path, inventory: Path, styles: Path, output: Path):
    """Stage every derived and derived-neighbour wall of the plan; writes output/derived.json."""
    import concurrent.futures as cf

    from .streetview.facades import load_facades

    plan = json.loads(plan_path.read_text())
    if plan["inventory_sha256"] != sha(inventory):
        raise ValueError("inventory changed since the plan was written")
    walls = {f.id: f for f in load_facades(inventory)}
    generated = generated_sidecars(assembly)
    loaded: dict[str, tuple] = {}

    def style_of(building):
        if building not in loaded:
            path = styles / "facades" / building / "style.json"
            style = json.loads(path.read_text())
            loaded[building] = (style, path, published_maps(assembly, generated[style["source"]]))
        return loaded[building]

    jobs = [r for r in plan["walls"] if r["status"] in ("derived", "derived-neighbour")]
    for row in jobs:
        style_of(row["style_building"])
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                derive_wall,
                walls[r["facade_id"]],
                *style_of(r["style_building"]),
                output,
                r["status"],
            ): r
            for r in jobs
        }
        results = [f.result() for f in cf.as_completed(futures)]
    results.sort(key=lambda r: r["facade_id"])
    summary = {
        "staged": sum(r["staged"] for r in results),
        "failed": [r["facade_id"] for r in results if not r["staged"]],
        "plain_columns": sum(bool(r.get("plain_columns")) for r in results if r["staged"]),
        "rows": results,
    }
    write_json(output / "derived.json", summary)
    return summary


def contact_sheet(plan_path: Path, assembly: Path, styles: Path, staging: Path, output: Path):
    """Per building: the source map, then each derived wall at the same metric scale with its
    index, size, per-section column mode and QA verdict."""
    from PIL import ImageDraw

    plan = json.loads(plan_path.read_text())
    staged = {r["facade_id"]: r for r in json.loads((staging / "derived.json").read_text())["rows"]}
    generated = generated_sidecars(assembly)
    scale, label_w, width = 6.0, 132, 2400  # sheet pixels per metre, label column, sheet width
    blocks = []
    for building in sorted({r["building_id"] for r in plan["walls"]}):
        rows = [r for r in plan["walls"] if r["building_id"] == building]
        style_building = next((r["style_building"] for r in rows if "style_building" in r), None)
        tiles = []
        if style_building:
            style = json.loads((styles / "facades" / style_building / "style.json").read_text())
            meta = generated[style["source"]]
            albedo = published_maps(assembly, meta, ("albedo",))["albedo"]
            size = f"{meta['width_m']:.1f}x{meta['height_m']:.1f}m"
            tiles.append(([f"SOURCE {style['source']}", size], albedo, meta, (0, 0, 140)))
        for row in rows:
            if row["status"] == "generate":
                continue
            result = staged.get(row["facade_id"], {})
            image, lines = (
                None,
                [
                    row["facade_id"].rsplit("_", 1)[1],
                    f"{row['width_m']:.1f}x{row['height_m']:.1f}m",
                ],
            )
            if result.get("staged"):
                sidecar = asset_path_for(staging, row["facade_id"]).with_suffix(".json")
                derive = json.loads(sidecar.read_text())["derive"]
                lines.append(" ".join(f"{g['label']}:{g['mode']}" for g in derive["groups"]))
                preview = staging / "post" / row["facade_id"] / "albedo.png"
                image = np.asarray(Image.open(preview).convert("RGB"))
            else:
                lines.append("QA FAILED")
            colour = (0, 110, 40) if image is not None else (170, 20, 20)
            tiles.append((lines, image, row, colour))
        title = building
        if style_building and style_building != building:
            title += f": neighbour style from {style_building} (derived-neighbour)"
        elif not style_building:
            title += ": every core wall has a generated map"
        blocks.append((title, tiles))
    layout, y = [], 0
    for title, tiles in blocks:
        layout.append(("title", title, 10, y))
        y += 20
        x, line_h = 10, 0
        for lines, image, meta, colour in tiles:
            w = max(round(meta["width_m"] * scale), label_w)
            h = round(meta["height_m"] * scale) + 14 * len(lines) + 8
            if x + w > width:
                x, y, line_h = 10, y + line_h + 10, 0
            layout.append(("tile", (lines, image, meta, colour), x, y))
            x, line_h = x + w + 10, max(line_h, h)
        y += line_h + 24
    sheet = Image.new("RGB", (width, y + 10), (245, 244, 240))
    draw = ImageDraw.Draw(sheet)
    for kind, item, x, y0 in layout:
        if kind == "title":
            draw.text((x, y0), item, fill=(0, 0, 0))
            continue
        lines, image, meta, colour = item
        for k, line in enumerate(lines):
            draw.text((x, y0 + 14 * k), line, fill=colour)
        if image is not None:
            size = (max(1, round(meta["width_m"] * scale)), max(1, round(meta["height_m"] * scale)))
            sheet.paste(Image.fromarray(image).resize(size), (x, y0 + 14 * len(lines) + 4))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=80)


def asset_path_for(root: Path, facade_id: str) -> Path:
    building, index = facade_id.rsplit("_", 1)
    return root / "facades" / building / index


def publish(plan_path: Path, staging: Path, inventory: Path, assembly: Path) -> dict:
    """Bind the staged derived walls into the approval root under the export lock, with every
    staged style record, and give every generated map the shared DENSITY_FLOOR; generated
    maps themselves are never replaced."""
    from types import SimpleNamespace

    from .export import copy, export_assets, export_lock
    from .facade_rollout import subset

    if json.loads(plan_path.read_text())["inventory_sha256"] != sha(inventory):
        raise ValueError("inventory changed since the plan was written")
    staged = [
        r["facade_id"]
        for r in json.loads((staging / "derived.json").read_text())["rows"]
        if r["staged"]
    ]
    generated = set(generated_sidecars(assembly))
    if generated & set(staged):
        raise ValueError(f"derived maps would replace generated ones: {generated & set(staged)}")
    subset(inventory, staged, staging / "derived.geojson")
    rows = [{"facade_id": f, "complete": True, "street_facing": True} for f in staged]
    write_json(
        staging / "qa.json", {"inventory_sha256": sha(staging / "derived.geojson"), "rows": rows}
    )
    # Every building's style record goes next to its source map, used by a derived wall or not.
    styles_used = {p.parent.name for p in (staging / "facades").glob("*/style.json")}
    with export_lock(assembly):
        before = json.loads((assembly / "MANIFEST.json").read_text())
        result = export_assets(
            SimpleNamespace(
                facades=inventory,
                source_inventory=staging / "derived.geojson",
                source=staging,
                qa=staging / "qa.json",
                output=assembly,
                merge=True,
            )
        )
        for meta in generated_sidecars(assembly).values():
            if meta.get("min_pixels_per_metre") != DENSITY_FLOOR:
                meta["min_pixels_per_metre"] = DENSITY_FLOOR
                write_json(
                    asset_path_for(
                        assembly, f"{meta['building_id']}_{meta['facade_index']}"
                    ).with_suffix(".json"),
                    meta,
                )
        for building in sorted(styles_used):
            copy(
                staging / "facades" / building / "style.json",
                assembly / "facades" / building / "style.json",
            )
    return {
        "published": len(staged),
        "styles": sorted(styles_used),
        "bound_before": len(before["bound"]),
        "bound_after": len(result["bound"]),
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("styles", "plan", "derive", "contact", "publish"):
        p = sub.add_parser(name)
        p.add_argument("--assembly", type=Path, required=True, help="approval root")
        p.add_argument("--inventory", type=Path, required=True)
        p.add_argument("--staging", type=Path, required=True, help="style and derived staging")
        if name == "styles":
            p.add_argument("--wfs", type=Path, required=True, help="raw WFS parquet directory")
        if name in ("styles", "plan"):
            p.add_argument(
                "--building", help="derive one building, including walls outside the office core"
            )
        if name == "contact":
            p.add_argument("--output", type=Path, required=True)
    a = ap.parse_args(argv)
    plan = a.staging / "plan.json"
    if a.command == "styles":
        result = choose_styles(a.assembly, a.inventory, a.wfs, a.staging, a.building)
    elif a.command == "plan":
        result = plan_walls(a.assembly, a.inventory, a.staging, plan, a.building)["counts"]
    elif a.command == "derive":
        summary = derive_planned(plan, a.assembly, a.inventory, a.staging, a.staging)
        result = {k: summary[k] for k in ("staged", "failed", "plain_columns")}
    elif a.command == "contact":
        contact_sheet(plan, a.assembly, a.staging, a.staging, a.output)
        result = {"contact": str(a.output)}
    else:
        result = publish(plan, a.staging, a.inventory, a.assembly)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
