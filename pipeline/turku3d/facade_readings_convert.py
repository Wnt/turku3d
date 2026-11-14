"""Convert W2 building readings into proven facade description rows (an offline tool).

A reading (``turku-description/building/1``, a vision model's per-building record of materials,
colours, walls, storeys, bays, opening types, balconies and roof) becomes the facade-fast
description row that ``facade_assets.description_row`` returns through ``turku3d.facade_readings``.
The converter leaves out buildings proven already describes or reviews. A row carries, beside the
description, the reading's balcony walls (``walls``: surveyed E/N segment and positioned rails),
so the walls a reading saw balconies on keep them.

Nothing in the generator imports this module: changing it changes the rows only, so a new set of
rows ships as a new version of the facade-readings migration step.

  python -m turku3d.facade_readings_convert probe KEYS.json > proven.json      (farm env, base data root)
  python -m turku3d.facade_readings_convert convert --readings DIR --proven proven.json [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from turku3d.facade_readings import SOURCE, directory, opaque

READERS = ("w2-observe", "w2-observe light reader 1", "w2-buildings")
MIN_CONF = 0.3
NOMINAL_WIDTH_M = 12.0

# W2 material families -> the facade-fast wall materials.
FAMILY = {
    "plaster": "plaster",
    "render": "plaster",
    "weatherboard": "wood",
    "painted_wood": "wood",
    "wood": "wood",
    "log": "wood",
    "shingle": "wood",
    "brick": "brick",
    "concrete": "concrete",
    "concrete_panel": "concrete",
    "ceramic_tile": "concrete",
    "granite": "stone",
    "stone": "stone",
    "rubble": "stone",
    "sheet_metal": "metal",
    "metal": "metal",
    "copper": "metal",
    "glass": "glass",
}
TRIM = {"wood": "#e8e4da", "plaster": "#e4ddd0", "brick": "#d0ccc4", "concrete": "#8f8d88"}
HOUSES = ("detached_house", "wooden_house", "wooden_villa", "outbuilding", "row_house", "garage")
STOREYS = ("ground", "upper", "mezzanine")


def proven_key(doc: dict) -> str:
    """The building's key in proven's tiles: W2 names an LoD2-only building ``gml-Building_N``, proven
    ``Building_N``; register buildings share their permanent building id."""
    key = doc["id"].split("/", 1)[1]
    return key.removeprefix("gml-")


def is_reading(doc: dict) -> bool:
    return any(p.get("by") in READERS for p in doc.get("provenance", []))


def conf(node: dict, default=0.0) -> float:
    return float((node or {}).get("conf", {}).get("*", default))


def expand(storeys):
    out = []
    for s in storeys or []:
        out.extend(dict(s) for _ in range(int(s.get("count", 1))))
    return out


def wall_width(wall) -> float:
    f = wall.get("frame") or {}
    if "p0" not in f or "p1" not in f:
        return 0.0
    return math.dist(f["p0"], f["p1"])


def resolved_walls(doc):
    """Walls with ``like`` and wall_defaults filled in (material, storeys, bays, plinth)."""
    defaults = doc.get("wall_defaults", {})
    walls = {w.get("id"): w for w in doc.get("walls", [])}
    out = []
    for w in doc.get("walls", []):
        like = walls.get(w.get("like")) or {}
        merged = {**defaults, **like, **w}
        out.append(merged)
    return out


def weight(wall) -> float:
    seen = (wall.get("observed") or {}).get("visible_share", 0.5)
    height = (wall.get("frame") or {}).get("height_m", 3.0)
    return wall_width(wall) * max(height, 1.0) * max(seen, 0.05) * max(conf(wall, 0.3), 0.05)


def bay_count(wall, width):
    bays = wall.get("bays")
    if isinstance(bays, list):
        return len(bays)
    if isinstance(bays, dict):
        if bays.get("count"):
            return int(bays["count"])
        if bays.get("pitch_m") and width:
            return max(1, round(width / float(bays["pitch_m"])))
    return 0


def storey_openings(storey, types):
    """Opening types placed in a storey: its window/door names, row cells and openings."""
    names = []
    for key in ("window", "door"):
        if isinstance(storey.get(key), str):
            names.append(storey[key])
    names += [c for c in storey.get("row") or [] if isinstance(c, str)]
    names += [o.get("type") for o in storey.get("openings") or [] if isinstance(o.get("type"), str)]
    return [n for n in names if n in types]


TRIM_BANDS = ("cornice", "eaves_cornice", "string_course", "frieze", "sill_band")
TRIM_VERTICALS = ("pilaster", "lesene")


def rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def accent(colour, wall_colour, step=24) -> bool:
    """A part reads as a colour accent, not relief in the wall's own colour."""
    return max(abs(a - b) for a, b in zip(rgb(colour), rgb(wall_colour), strict=True)) >= step


def architectural_trim(walls, materials, wall_colour):
    """The accent colour most of the read pilasters and cornice/string-course bands carry, by wall
    size. Parts in the wall's own colour are relief and leave the trim to the windows' colours."""
    votes = {}
    for w in walls:
        parts = [b for b in w.get("bands") or [] if b.get("kind") in TRIM_BANDS]
        parts += [v for v in w.get("verticals") or [] if v.get("kind") in TRIM_VERTICALS]
        for part in parts:
            named = part.get("material")
            named = named if isinstance(named, dict) else materials.get(named) if isinstance(named, str) else None
            colour = part.get("colour") or (named or {}).get("colour")
            if isinstance(colour, str) and len(colour) == 7 and accent(colour, wall_colour):
                votes[colour.lower()] = votes.get(colour.lower(), 0) + weight(w)
    return max(votes, key=votes.get) if votes else None


def main_window(walls, types):
    """The window type most of the read storeys use, weighted by wall size."""
    votes = {}
    for w in walls:
        for s in expand(w.get("storeys")):
            for name in storey_openings(s, types):
                if types[name].get("kind") == "window":
                    votes[name] = votes.get(name, 0) + weight(w)
    return max(votes, key=votes.get) if votes else None


def ground_floor(wall, types, wood):
    """(ground_floor, openings) of the ground storey of the reference wall, when it was read."""
    storeys = expand(wall.get("storeys"))
    ground = next((s for s in storeys if s.get("kind") == "ground"), None)
    names = storey_openings(ground, types) if ground else []
    # Storeys stand on the ground floor level; a raised basement's openings below it are not the
    # ground storey's (a wooden house's cellar door is no entrance in the ground storey).
    datum = float(wall.get("ground_floor_m", (wall.get("plinth") or {}).get("height_m", 0)) or 0)
    top = datum + float((ground or {}).get("height_m", 3.5))
    names += [
        o.get("type")
        for o in wall.get("openings") or []
        if o.get("type") in types and datum - 0.3 <= float(o.get("v_m", -99)) < top
    ]
    kinds = [types[n].get("kind") for n in names]
    if not kinds:
        return ("windows" if wood else "doors"), None
    count = sum(k in ("window", "door", "shopfront", "garage", "gateway") for k in kinds)
    if "shopfront" in kinds:
        return "shops", count
    if any(k in ("door", "garage", "gateway") for k in kinds):
        return "doors", count
    if "window" in kinds:
        return "windows", count
    return "plain", 0


def balcony_rects(wall):
    """Positioned balcony rails [x, y, w, h] in the reading wall's frame (x from p0, y above its base)."""
    rects = []
    storeys = expand(wall.get("storeys"))
    plinth = (wall.get("plinth") or {}).get("height_m", 0)
    datum = wall.get("ground_floor_m", plinth) or 0
    floors, level = [], float(datum)
    for s in storeys:
        floors.append((level, float(s.get("height_m", 3.0))))
        level += float(s.get("height_m", 3.0))
    width = wall_width(wall)
    for part in wall.get("balconies") or []:
        u, w = part.get("u_m"), part.get("w_m")
        if u is None or w is None:
            # A continuous balcony without a position runs the wall's length.
            u, w = (width / 2, width) if part.get("type") == "continuous" else (None, None)
        if u is None:
            continue
        rail = float((part.get("railing") or {}).get("height_m", 1.0))
        if "storeys" in part:
            levels = [floors[i] for i in part["storeys"] if 0 <= i < len(floors)]
        elif "v_m" in part:
            levels = [(float(datum) + float(part["v_m"]), 3.0)]
        else:
            levels = floors[1:]
        for floor, height in levels:
            rects.append([round(float(u) - float(w) / 2, 3), round(floor, 3), round(float(w), 3),
                          round(min(rail, 0.45 * height), 3)])
    return rects


def convert(doc: dict) -> tuple[dict | None, str]:
    """The proven facade description row of one reading, or None and why it has none."""
    key = proven_key(doc)
    materials = doc.get("materials", {})
    types = doc.get("opening_types", {})
    walls = resolved_walls(doc)
    defaults = doc.get("wall_defaults", {})
    style = doc.get("style", {}).get("type")

    votes, named = {}, {}
    for w in walls or [defaults]:
        found = w.get("material")
        # A wall names a shared material or carries its own inline.
        name = json.dumps(found, sort_keys=True) if isinstance(found, dict) else found
        named[name] = found if isinstance(found, dict) else materials.get(found) or {}
        family = FAMILY.get(named[name].get("family"))
        if family:
            votes.setdefault(family, {}).setdefault(name, 0.0)
            votes[family][name] += weight(w) if walls else 1.0
    if not votes:
        return None, "no wall material read"
    material = max(votes, key=lambda f: sum(votes[f].values()))
    # A hand-written description states its fields without a confidence.
    certainty = conf(doc, 1.0 if "conf" not in doc else 0.0)
    name = max(votes[material],
               key=lambda n: (conf(named[n], certainty) >= MIN_CONF, votes[material][n]))
    wall_material = named[name]
    if conf(wall_material, certainty) < MIN_CONF:
        return None, f"wall material read below conf {MIN_CONF}"
    wall_colour = wall_material.get("colour")
    if not isinstance(wall_colour, str) or len(wall_colour) != 7:
        return None, "no wall colour read"
    wood = material == "wood" or style in HOUSES

    reference = max(walls, key=weight) if walls else None
    windows = main_window(walls, types)
    window = types.get(windows, {})
    # Trim (cornices, bands, corners, window surrounds) from the read pilasters and cornices first;
    # the window frames keep their own read colour on the row.
    frame = (window.get("frame") or {}).get("colour")
    frame = frame.lower() if isinstance(frame, str) and len(frame) == 7 else None
    trim = (architectural_trim(walls, materials, wall_colour)
            or (window.get("surround") or {}).get("colour")
            or next((m.get("colour") for n, m in materials.items() if "trim" in n and m.get("colour")), None)
            or frame
            or TRIM.get(material, TRIM["plaster"]))

    if reference is not None:
        width = wall_width(reference)
        storeys = expand(reference.get("storeys"))
        columns = bay_count(reference, width)
        if not columns and storeys:
            columns = max(
                (sum(types.get(n, {}).get("kind") == "window" for n in storey_openings(s, types))
                 for s in storeys), default=0)
    else:
        width, storeys, columns = NOMINAL_WIDTH_M, expand(defaults.get("storeys")), 0
    if not columns:
        pitch = (defaults.get("bays") or {}).get("pitch_m") or (2.6 if wood else 3.0)
        width = width or NOMINAL_WIDTH_M
        columns = max(1, round(width / float(pitch)))
    if columns > 60:  # The schema's limit: keep the read pitch over a shorter source width.
        width, columns = width * 60 / columns, 60
    floors = sum(s.get("kind") in STOREYS for s in storeys) + sum(
        s.get("kind") == "attic" and bool(storey_openings(s, types)) for s in storeys)
    # Proven draws the rhythm at a bounded pitch on every wall: size the read window against the
    # bay it is drawn in, or a sparse reading (wide bays) would get slits.
    from turku3d.facade_programs import DRAWN_PITCH_M

    pitch = min(DRAWN_PITCH_M[1], max(DRAWN_PITCH_M[0], width / max(columns, 1)))
    upper = next((s for s in storeys if s.get("kind") == "upper"), None) or next(
        (s for s in storeys if s.get("kind") == "ground"), None)
    window_width = min(0.9, max(0.1, window["w_m"] / pitch)) if window.get("w_m") else 0.45
    window_height = (min(0.85, max(0.2, window["h_m"] / float(upper["height_m"])))
                     if window.get("h_m") and upper and upper.get("height_m") else 0.5)

    if reference is not None:
        ground, openings = ground_floor(reference, types, wood)
    else:
        ground, openings = ("windows" if wood else "doors"), None
    if openings is None:
        openings = columns if ground == "windows" else 1 if ground == "doors" else max(1, round(width / 4))

    roof = doc.get("roof", {})
    bands = {b.get("kind") for w in walls for b in w.get("bands") or []}
    form = roof.get("form_hint")
    overhang = float((roof.get("eaves") or {}).get("overhang_m") or 0)
    if roof.get("parapet") or (form == "flat" and overhang < 0.3):
        roof_line = "parapet"
    elif bands & {"cornice", "eaves_cornice"}:
        roof_line = "cornice"
    elif form in ("gable", "hip", "shed", "mansard", "complex") or roof.get("eaves"):
        roof_line = "eaves"
    else:
        roof_line = "none"
    plinth = any(float((w.get("plinth") or {}).get("height_m", 0)) >= 0.3 for w in walls or [defaults])

    verticals = {v.get("kind") for w in walls for v in w.get("verticals") or []}
    features = []
    if any(w.get("balconies") for w in walls):
        features.append("balconies")
    if verticals & {"pilaster", "lesene"}:
        features.append("pilasters")
    if bands & {"string_course", "sill_band", "frieze"}:
        features.append("string_courses")
    if (any(t.get("pediment") or (t.get("surround") or {}).get("kind") in ("carved", "moulded", "eared")
            for t in types.values())
            or any(a.get("kind") == "ornament" for w in walls for a in w.get("attached") or [])):
        features.append("ornament")

    description = dict(
        facade_id=f"reading-{key}",
        wall_visible=True,
        problem="",
        material=material,
        wall_colour=wall_colour.lower(),
        trim_colour=str(trim).lower(),
        storeys=int(floors),
        window_columns=int(columns),
        window_width=round(window_width, 4),
        window_height=round(window_height, 4),
        ground_floor=ground,
        ground_openings=int(min(60, openings)),
        roof_line=roof_line,
        plinth=bool(plinth),
        features=features,
    )
    from turku3d.facade_kit import renderer

    problems = renderer().validate(description, description["facade_id"])
    if problems:
        return None, "; ".join(problems)
    balcony_walls = [
        dict(coordinates=[w["frame"]["p0"], w["frame"]["p1"]], base_height=w["frame"].get("base_h"),
             balconies=rects)
        for w in walls
        if w.get("frame") and (rects := balcony_rects(w))
    ]
    reading = next(p for p in doc.get("provenance", []) if p.get("by") in READERS)
    result = dict(
        building_key=key,
        description=description,
        width_m=round(width, 3),
        engine="stored",
        source=SOURCE,
        provenance=dict(reading=reading.get("key"), by=reading.get("by"), at=reading.get("at"),
                        tier=doc.get("tier"), style=style),
    )
    if frame and frame != description["trim_colour"]:
        result["frame_colour"] = frame
    if balcony_walls:
        result["walls"] = balcony_walls
    return result, "converted"


def proven_status(key) -> dict:
    """What proven already has for a building (run with the base's data root)."""
    from turku3d.facade_assets import description_row
    from turku3d.facade_inference import evidence
    from turku3d.facade_programs import measured

    found = description_row(key)
    return dict(
        description=(found or {}).get("source") if (found or {}).get("source") != SOURCE else None,
        program=measured(key) is not None,
        appearance=bool(evidence(key)),
    )


def run_convert(readings: Path, proven: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report = dict(read=0, converted=0, skipped_proven_description=0, skipped_proven_program=0,
                  unconverted={}, buildings=[])
    written = set()
    for path in sorted(readings.glob("*.json")):
        doc = json.loads(path.read_text())
        if not is_reading(doc):
            continue
        key = proven_key(doc)
        report["read"] += 1
        status = proven.get(key)
        if status is None:
            report["unconverted"][key] = "not probed against proven"
            continue
        if status.get("description"):
            report["skipped_proven_description"] += 1
            continue
        if status.get("program") or status.get("appearance"):
            report["skipped_proven_program"] += 1
            continue
        result, why = convert(doc)
        if result is None:
            report["unconverted"][key] = why
            continue
        target = out / opaque(key)
        target.write_text(json.dumps(result, sort_keys=True, indent=1) + "\n")
        written.add(target.name)
        report["buildings"].append(key)
        report["converted"] += 1
    for stale in out.glob("*.json"):
        if stale.name != "index.json" and stale.name not in written:
            stale.unlink()
    (out / "index.json").write_text(json.dumps(
        dict(format="turku3d-facade-readings", version=1, source=SOURCE,
             buildings=sorted(report["buildings"])), indent=1) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("keys", type=Path)
    conv = sub.add_parser("convert")
    conv.add_argument("--readings", type=Path, required=True)
    conv.add_argument("--proven", type=Path, required=True)
    conv.add_argument("--out", type=Path, default=directory())
    args = parser.parse_args(argv)
    if args.action == "probe":
        print(json.dumps({k: proven_status(k) for k in json.loads(args.keys.read_text())}, sort_keys=True))
        return 0
    report = run_convert(args.readings, json.loads(args.proven.read_text()), args.out)
    print(json.dumps({k: v for k, v in report.items() if k != "buildings"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
