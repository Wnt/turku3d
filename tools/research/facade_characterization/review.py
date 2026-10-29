"""Compile the assistant's direct Linnankatu crop readings into facade programs.

READINGS are hand-authored after inspecting the local contact sheets and crops.
Normalised rectangles use image x/y/w/h, not detector output. No model runs here.
Source photos and pixel evidence remain local; public output contains descriptors.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from turkutex import facade_program as fp


def grid(xs, ys, width, height):
    return [[x - width / 2, y, width, height] for y in ys for x in xs]


# Architectural groups, not individual panes. Bounds are approximate photo readings.
READINGS = {
    1: dict(
        material="brick",
        style="19th-century classicism",
        base=0.31,
        windows=grid([0.05, 0.177, 0.305, 0.432, 0.558, 0.687, 0.812], [0.215, 0.48], 0.052, 0.145)
        + [[0.932, 0.28, 0.035, 0.11], [0.932, 0.49, 0.035, 0.14]],
        centers=[0.05, 0.177, 0.305, 0.432, 0.558, 0.687, 0.812, 0.949],
        glass=[
            [0, 0.77, 0.105, 0.18],
            [0.223, 0.77, 0.103, 0.18],
            [0.534, 0.77, 0.113, 0.18],
            [0.731, 0.77, 0.112, 0.18],
            [0.90, 0.77, 0.10, 0.18],
        ],
        doors=[[0.13, 0.78, 0.07, 0.17], [0.393, 0.78, 0.066, 0.17], [0.658, 0.78, 0.063, 0.17]],
        patch=[0.462, 0.28, 0.025, 0.07],
        trim_patch=[0.352, 0.27, 0.022, 0.08],
        frame="#b7bdb3",
        divisions=[2, 3],
        cornice=0.65,
        courses=[5.0, 9.8, 13.8],
        surround=0.22,
        trim="Pilasters, capitals, pediments, balustrades and frieze relief",
        note="Eight upper opening columns; seven main bays plus a shorter right wing. Pink brick and cream plaster ornament. Ground doors are visible; glass reflections are not wall colour.",
    ),
    2: dict(
        material="brick",
        style="19th-century classicism",
        base=0.35,
        windows=grid([0.267, 0.73], [0.19, 0.44], 0.13, 0.135),
        centers=[0.267, 0.73],
        glass=[],
        doors=[],
        patch=[0.55, 0.20, 0.06, 0.07],
        trim_patch=[0.02, 0.25, 0.06, 0.08],
        frame="#bec4b5",
        divisions=[2, 3],
        cornice=0.65,
        courses=[5.0, 9.8],
        surround=0.20,
        trim="Central medallion and niche; pediment and pilasters",
        hidden=[[0, 0.73, 0.85, 0.27]],
        note="Same building as wall 1. Four upper windows. Construction hoarding hides the ground; no door positions or shop glazing inferred.",
    ),
    3: dict(
        material="wood",
        style="historic shopfront",
        base=1,
        windows=[],
        centers=[0.216, 0.45, 0.68, 0.86, 0.97],
        glass=[
            [0.116, 0.47, 0.20, 0.35],
            [0.348, 0.47, 0.20, 0.35],
            [0.585, 0.47, 0.19, 0.35],
            [0.945, 0.47, 0.055, 0.35],
        ],
        doors=[[0.826, 0.47, 0.07, 0.43]],
        patch=[0.336, 0.57, 0.017, 0.13],
        frame="#244739",
        divisions=[2, 1],
        canopy=True,
        cornice=0.12,
        courses=[],
        surround=0.08,
        trim_colour="#294a39",
        note="Dark green timber restaurant frontage, three broad windows, entrance and a clipped right window. Rounded striped awnings; terrace furniture hides the lower glazing. No upper floor.",
    ),
    4: dict(
        view=0,
        material="render",
        style="19th-century classicism",
        base=0.39,
        windows=[
            [0.055, 0.10, 0.11, 0.085],
            [0.253, 0.08, 0.052, 0.09],
            [0.332, 0.071, 0.052, 0.09],
            [0.414, 0.063, 0.052, 0.09],
            [0.497, 0.055, 0.052, 0.09],
            [0.578, 0.048, 0.052, 0.09],
            [0.663, 0.039, 0.052, 0.09],
            [0.814, 0.02, 0.08, 0.10],
        ]
        + grid(
            [0.163, 0.287, 0.368, 0.451, 0.535, 0.616, 0.698, 0.827, 0.902], [0.36], 0.052, 0.17
        ),
        centers=[0.083, 0.163, 0.287, 0.368, 0.451, 0.535, 0.616, 0.698, 0.827, 0.902],
        glass=[],
        doors=[],
        patch=[0.46, 0.26, 0.06, 0.025],
        frame="#b8bca7",
        divisions=[2, 3],
        cornice=0.45,
        courses=[4.9, 9.4],
        surround=0.15,
        trim_colour="#b7b7a8",
        hidden=[[0, 0.77, 1, 0.23]],
        note="Eight attic opening groups and nine visible middle windows; one left middle opening boarded over. Ground arches are visible only above construction hoarding. No complete ground opening or door inferred. View 0 chosen manually for clarity.",
    ),
    5: dict(
        material="render",
        style="early-20th-century classicism",
        base=0.25,
        windows=grid([0.203, 0.46, 0.715], [0.117], 0.063, 0.074)
        + grid([0.213, 0.475, 0.738], [0.274, 0.443], 0.075, 0.095)
        + grid([0.24, 0.505, 0.775], [0.625], 0.075, 0.12),
        centers=[0.23, 0.48, 0.74],
        glass=[[0.16, 0.86, 0.18, 0.14], [0.44, 0.86, 0.18, 0.14], [0.707, 0.86, 0.177, 0.14]],
        doors=[[0.08, 0.89, 0.06, 0.11]],
        patch=[0.56, 0.34, 0.09, 0.06],
        frame="#303a2f",
        divisions=[2, 3],
        cornice=0.25,
        courses=[4.3, 14.2],
        surround=0.10,
        trim_colour="#908d6f",
        conflict=True,
        note="Twelve upper windows in three columns; lowest upper row arched, top row shorter. Four visible upper rows plus shop base conflict with register 4; retain register and explicit rectangles. Slight perspective residual.",
    ),
    6: dict(
        material="stone",
        style="National Romantic / Jugend",
        base=0.40,
        windows=grid([0.087, 0.224, 0.36, 0.496, 0.631, 0.766, 0.9], [0.075], 0.052, 0.14)
        + grid([0.092, 0.233, 0.368, 0.502, 0.64, 0.775, 0.906], [0.36], 0.06, 0.16),
        centers=[0.09, 0.23, 0.365, 0.5, 0.635, 0.77, 0.905],
        glass=grid([0.10, 0.24, 0.37, 0.641, 0.776, 0.913], [0.68], 0.058, 0.16),
        doors=[[0.466, 0.65, 0.073, 0.26]],
        patch=[0.268, 0.24, 0.043, 0.06],
        frame="#9c9c80",
        divisions=[3, 3],
        cornice=0.20,
        courses=[5.8, 6.7],
        surround=0.18,
        trim_colour="#665649",
        note="Brown-red rusticated stone bank facade; seven bays, fourteen upper windows, six ground windows and central arched door. Arched middle/ground windows and carved central portal. Photo is deeply shaded; colour is appearance, not albedo.",
    ),
    7: dict(
        material="concrete_panel",
        style="1960s modernism",
        base=0.19,
        windows=[[0.078, y, 0.922, 0.083] for y in [0.05, 0.36, 0.523, 0.69]]
        + [[0.078, 0.21, 0.56, 0.083], [0.843, 0.21, 0.157, 0.083]],
        centers=[0.17, 0.36, 0.55, 0.74, 0.93],
        glass=[
            [0.083, 0.86, 0.16, 0.12],
            [0.34, 0.86, 0.18, 0.12],
            [0.66, 0.86, 0.16, 0.12],
            [0.88, 0.86, 0.12, 0.12],
        ],
        doors=[[0.548, 0.85, 0.045, 0.13]],
        patch=[0.56, 0.16, 0.10, 0.026],
        frame="#b5c3bd",
        divisions=[5, 1],
        cornice=0,
        courses=[],
        surround=0,
        balconies=[[0.66, 0.21, 0.17, 0.083]],
        note="Five visible ribbon bands, with a recessed balcony interrupting the second band. Pale horizontal spandrels, glass railing over retail arcade. Seven registered floors retained; crop top is incomplete. Five structural bays, not a reliable individual-window count.",
    ),
    8: dict(
        material="concrete_panel",
        style="1960s modernism",
        base=0.22,
        windows=[
            [0.21, 0.05, 0.53, 0.078],
            [0.40, 0.211, 0.46, 0.073],
            [0.20, 0.366, 0.31, 0.071],
            [0.00, 0.519, 0.71, 0.072],
            [0.17, 0.68, 0.68, 0.065],
        ],
        centers=[],
        glass=[],
        doors=[],
        patch=[0.44, 0.16, 0.13, 0.026],
        frame="#b4b9ad",
        divisions=None,
        cornice=0,
        courses=[],
        surround=0,
        hidden=[[0, 0, 0.20, 0.45], [0.74, 0.25, 0.26, 0.45], [0, 0.85, 1, 0.15]],
        note="Five white-frame ribbon bands with dark ventilation panels. Trees hide opening boundaries; rectangles mark visible spans only. Ground glazing and entrances are obscured by bus/trees. Bay count and door locations unknown.",
    ),
    9: dict(
        material=None,
        style="1960s retail base",
        base=1,
        scope="ground-only",
        windows=[],
        centers=[],
        glass=[[0.035, 0.16, 0.117, 0.37], [0.17, 0.16, 0.112, 0.37]],
        doors=[],
        patch=None,
        frame="#abb5aa",
        divisions=None,
        cornice=0,
        courses=[],
        surround=0,
        hidden=[[0, 0.60, 1, 0.40], [0.69, 0.18, 0.31, 0.82]],
        note="Only a 4.01m shop strip from a registered eight-storey building. Left second-hand shop glazing is visible; supermarket windows have opaque graphics. Vans/cars hide entrances. Do not infer upper facade or sample this wall as a full-building archetype.",
    ),
    10: dict(
        material="metal_panel",
        style="1960s modernism",
        base=0.18,
        windows=[
            [x, y, 0.078, 0.077]
            for y in [0.054, 0.211, 0.366, 0.52]
            for x in [0.058, 0.198, 0.338, 0.478, 0.618, 0.758, 0.898]
        ]
        + [
            [x, y, 0.022, 0.077]
            for y in [0.054, 0.211, 0.366, 0.52]
            for x in [0.143, 0.283, 0.423, 0.563, 0.703, 0.843, 0.978]
        ]
        + [
            [x, 0.688, 0.042, 0.078]
            for x in [
                0.063,
                0.122,
                0.203,
                0.262,
                0.343,
                0.402,
                0.483,
                0.542,
                0.623,
                0.682,
                0.763,
                0.822,
                0.903,
                0.958,
            ]
        ],
        centers=[0.115, 0.255, 0.395, 0.535, 0.675, 0.815, 0.95],
        glass=[
            [0.06, 0.865, 0.08, 0.11],
            [0.19, 0.865, 0.11, 0.11],
            [0.34, 0.865, 0.09, 0.11],
            [0.48, 0.865, 0.09, 0.11],
            [0.63, 0.865, 0.09, 0.11],
            [0.77, 0.865, 0.09, 0.11],
        ],
        doors=[],
        patch=[0.45, 0.29, 0.03, 0.055],
        frame="#c3c7bd",
        divisions=[2, 1],
        cornice=0,
        courses=[],
        surround=0,
        conflict=True,
        note="Green vertically ribbed spandrels; seven repeating wide+narrow opening groups in four rows, fourteen singles in lowest row: 70 complete opening units. Left clipped edge omitted. Cream projecting boxes and concrete retail arcade. Register says one storey despite five visible upper rows; do not change register or use as a storey-conditioned donor.",
    ),
    11: dict(
        material="brick",
        style="1960s modernism",
        base=0.18,
        windows=[[0.24, y, 0.177, 0.086] for y in [0.057, 0.213, 0.37, 0.527, 0.684]]
        + [[0.46, y, 0.195, 0.086] for y in [0.057, 0.213, 0.37, 0.527, 0.684]],
        centers=[0.10, 0.33, 0.56],
        glass=[[0.027, 0.86, 0.32, 0.12], [0.48, 0.86, 0.50, 0.12]],
        doors=[[0.369, 0.85, 0.05, 0.13]],
        patch=[0.78, 0.35, 0.10, 0.12],
        frame="#b9c6c3",
        divisions=[3, 1],
        cornice=0,
        courses=[],
        surround=0,
        balconies=[[0, y, 0.20, 0.15] for y in [0.06, 0.215, 0.37, 0.525, 0.68]],
        canopy=True,
        note="White painted brick at right, pale panel spandrels in window zone. Ten grouped upper openings, five recessed balconies left; blank right third. Ground restaurant/shop glazing with black awning and one clear central entrance.",
    ),
    12: dict(
        material="concrete_panel",
        style="postwar retail base",
        base=1,
        scope="ground-only",
        windows=[],
        centers=[],
        glass=[
            [0.03, 0.20, 0.095, 0.32],
            [0.14, 0.20, 0.14, 0.32],
            [0.35, 0.20, 0.07, 0.32],
            [0.44, 0.20, 0.065, 0.32],
            [0.57, 0.20, 0.18, 0.32],
            [0.78, 0.20, 0.20, 0.32],
        ],
        doors=[],
        patch=[0.48, 0.012, 0.10, 0.07],
        frame="#494e45",
        divisions=None,
        cornice=0,
        courses=[],
        surround=0,
        canopy=True,
        hidden=[[0, 0.56, 1, 0.44]],
        note="Only a 5.04m ground strip on registered eight-storey building. White panel sign band and dark glazed shopfront with restaurant awnings. Cars obscure lower openings; no confident door positions. Upper facade unknown.",
    ),
    13: dict(
        reject=True,
        material=None,
        style=None,
        base=None,
        windows=[],
        centers=[],
        glass=[],
        doors=[],
        patch=None,
        frame=None,
        divisions=None,
        note="Both retained views are fully covered by construction scaffold sheeting. ADE marks it visible, which is wrong for facade evidence. Reject all appearance/opening inference; retain register attributes only.",
    ),
}


def compile_wall(row, reading, root):
    W, H = row["width_m"], row["height_m"]
    name = row["chosen"]["name"]
    if "view" in reading:
        name = f"{row['facade_id']}.{reading['view']}"
    im = Image.open(root / "crops" / f"{name}.png").convert("RGB")

    def metric(box):
        x, y, w, h = box
        return [round(x * W, 3), round((1 - y - h) * H, 3), round(w * W, 3), round(h * H, 3)]

    def patch_colour(box):
        if box is None:
            return None
        x, y, w, h = box
        pixels = np.asarray(im)[
            round(y * im.height) : round((y + h) * im.height),
            round(x * im.width) : round((x + w) * im.width),
        ]
        c = np.median(pixels.reshape(-1, 3), axis=0).round().astype(int)
        return "#" + "".join(f"{v:02x}" for v in c)

    def v(x, support=0.8):
        return (
            fp.value(x, "assistant direct crop review", support) if x is not None else fp.unknown()
        )

    reject = reading.get("reject", False)
    boxes = [metric(b) for b in reading["windows"]]
    sizes = np.median([b[2:] for b in boxes], axis=0).tolist() if boxes else None
    centres = [round(x * W, 3) for x in reading["centers"]]
    colour = patch_colour(reading["patch"])
    trim_colour = patch_colour(reading.get("trim_patch")) or reading.get("trim_colour")
    base_h = reading["base"] * H if reading["base"] is not None else None
    wall = dict(
        wall_id=row["facade_id"],
        width_m=W,
        height_m=H,
        geometry={k: row[k] for k in ("p0", "p1", "normal", "base_h")},
        observation=dict(
            visible_share=0 if reject else row["chosen"]["visible_share"],
            visibility_source="ADE upper bound; manually rejected scaffold"
            if reject
            else "ADE; see manual hidden regions",
            wall_share=0.4 if colour else 0,
            needs_review=reject,
            scope=reading.get("scope", "full-wall"),
            register_height_conflict=reading.get("conflict", False),
            excluded_rectangles_m=[metric(b) for b in reading.get("hidden", [])],
            colour_space="sRGB photo appearance; not calibrated albedo",
            notes=reading["note"],
            opening_extent="observed only; omitted areas are unknown",
        ),
        split=dict(base_height_m=v(base_h), middle_repeats=None, top_height_m=fp.unknown()),
        wall=dict(
            colour=fp.value(colour, "median of assistant-selected wall patch", 0.65)
            if colour
            else fp.unknown(),
            material=v(reading["material"], 0.7),
        ),
        bays=dict(
            count=len(centres) if centres else None,
            centers_m=centres,
            pitch_m=round(float(np.median(np.diff(centres))), 3) if len(centres) > 1 else None,
            symmetry=None,
            support=0.8 if centres else 0,
            rhythm=v("regular") if centres else fp.unknown(),
        ),
        windows=dict(
            size_m=v(sizes),
            aspect=v(sizes[0] / sizes[1] if sizes else None),
            rectangles_m=boxes,
            sill_fraction=fp.unknown(),
            frame_colour=v(reading["frame"], 0.6),
            divisions=v(reading["divisions"], 0.6),
            recess_m=fp.unknown(),
        ),
        base=dict(
            colour=fp.unknown(),
            material=fp.unknown(),
            plinth_height_m=fp.unknown(),
            glazing_share=fp.unknown(),
            shopfront=v(True)
            if reading["glass"] and reading.get("style") != "National Romantic / Jugend"
            else fp.unknown(),
            openings_m=[metric(b) for b in reading["glass"]],
            variant=v("shops") if reading["glass"] and row["index"] != 6 else fp.unknown(),
        ),
        doors=dict(
            candidates=[
                dict(rect_m=metric(b), source="assistant direct crop review", support=0.8)
                for b in reading["doors"]
            ],
            type=fp.unknown(),
            canopy=v(reading.get("canopy")),
        ),
        trim=dict(
            cornice=v(reading.get("cornice")),
            string_courses=v(reading.get("courses")),
            window_surrounds=v(reading.get("surround")),
            colour=v(trim_colour, 0.6),
        ),
        corners={k: fp.unknown() for k in ("quoins", "shape", "tower")},
        balconies=v([metric(b) for b in reading["balconies"]])
        if "balconies" in reading
        else fp.unknown(),
        bay_windows=fp.unknown(),
        style_era=v(reading["style"], 0.65),
    )
    return wall, dict(
        wall_id=row["facade_id"],
        crop=str(root / "crops" / f"{name}.png"),
        selected_wall_patch_xywh=reading["patch"],
        notes=reading.get("trim"),
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--publish", type=Path, required=True)
    p.add_argument(
        "--context-root", type=Path, help="Existing enriched covariates and OSM projections"
    )
    a = p.parse_args()
    start = time.monotonic()
    groups, attributes, evidence = defaultdict(list), {}, []
    rows = json.loads((a.root / "chosen.json").read_text())
    context = a.context_root or a.root.parent
    covariates = json.loads((context / "covariates.json").read_text())
    entrances = json.loads((context / "entrances.json").read_text())
    for row in rows:
        wall, item = compile_wall(row, READINGS[row["index"]], a.root)
        wall["doors"]["candidates"].extend(
            dict(
                u_m=e["u_m"],
                source="OSM entrance; not photo-confirmed",
                osm_id=e["id"],
                type=e.get("entrance"),
            )
            for e in entrances.get(row["facade_id"], [])
        )
        groups[row["building_id"]].append(wall)
        attributes[row["building_id"]] = covariates[row["building_id"]]
        evidence.append(item)
    programs = []
    for bid, walls in groups.items():
        b = fp.merge_building(bid, attributes[bid], walls)
        b["needs_review"] = all(w["observation"]["needs_review"] for w in walls)
        b["review"] = dict(
            method="assistant direct crop reading", scope="matched walls only", independent=False
        )
        programs.append(b)
    encoded = json.dumps(programs, indent=2) + "\n"
    (a.root / "programs.json").write_text(encoded)
    a.publish.parent.mkdir(parents=True, exist_ok=True)
    a.publish.write_text(encoded)
    (a.root / "review-evidence.json").write_text(json.dumps(evidence, indent=2))
    # Small faithful diagnostic: explicit observed openings, never automatic floors.
    render_start = time.monotonic()
    renders = {}
    for b in programs:
        for wall in b["walls"]:
            renders[wall["wall_id"]] = render(wall)
    render_seconds = time.monotonic() - render_start
    sheet = Image.new("RGB", (1400, len(evidence) * 240), "#eeece8")
    d = ImageDraw.Draw(sheet)
    for i, e in enumerate(evidence):
        wall = next(w for b in programs for w in b["walls"] if w["wall_id"] == e["wall_id"])
        label = f"{i + 1:02d} {e['wall_id']} | {wall['observation']['scope']} | {'REJECTED scaffold' if wall['observation']['needs_review'] else 'reviewed'}"
        d.text((8, i * 240 + 5), label, fill="black")
        for j, im in enumerate([Image.open(e["crop"]), renders[e["wall_id"]]]):
            thumb = ImageOps.contain(im.convert("RGB"), (680, 208))
            sheet.paste(thumb, (j * 700 + (700 - thumb.width) // 2, i * 240 + 28))
    sheet.save(a.root / "reviewed-contact.jpg", quality=94)
    (a.root / "review-build-timing.json").write_text(
        json.dumps(
            dict(
                total=time.monotonic() - start,
                render=render_seconds,
                walls=len(evidence),
                buildings=len(programs),
                review_accepted=sum(not b["needs_review"] for b in programs),
            ),
            indent=2,
        )
    )
    print(f"{len(programs)} buildings, {len(evidence)} walls -> {a.publish}")


def render(wall, size=680):
    """Preview the reviewed schema; decorations are schematic, not photorealistic."""
    im = fp.paint(wall, floors=None, size=size)
    d = ImageDraw.Draw(im)
    sx, sy = im.width / wall["width_m"], im.height / wall["height_m"]

    def box(b, **kw):
        x, y, w, h = b
        d.rectangle((x * sx, im.height - (y + h) * sy, (x + w) * sx, im.height - y * sy), **kw)

    trim = wall["trim"]["colour"]["value"] or "#b8b5a9"
    surround = wall["trim"]["window_surrounds"]["value"] or 0
    for x, y, w, h in wall["windows"]["rectangles_m"]:
        if surround:
            box([x - surround, y - surround, w + 2 * surround, h + 2 * surround], fill=trim)
    for b in wall["base"]["openings_m"] + wall["windows"]["rectangles_m"]:
        box(
            b,
            fill="#34454e",
            outline=wall["windows"]["frame_colour"]["value"] or "#bfc4bf",
            width=max(1, round(0.07 * sx)),
        )
    for door in wall["doors"]["candidates"]:
        if "rect_m" in door:
            box(door["rect_m"], fill="#34372f", outline="#c4bdae", width=1)
    for b in wall["balconies"]["value"] or []:
        box(b, fill="#5c615c", outline=trim, width=max(1, round(0.1 * sx)))
    for y in wall["trim"]["string_courses"]["value"] or []:
        box([0, y, wall["width_m"], 0.13], fill=trim)
    cornice = wall["trim"]["cornice"]["value"] or 0
    if cornice:
        box([0, wall["height_m"] - cornice, wall["width_m"], cornice], fill=trim)
    if wall["observation"]["needs_review"]:
        d.text((4, 4), "NO FACADE EVIDENCE", fill="black")
    return im


if __name__ == "__main__":
    main()
