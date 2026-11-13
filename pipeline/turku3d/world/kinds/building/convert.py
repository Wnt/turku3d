"""convert: the photographed facade programs of the old game as building descriptions.

    python -m turku3d.world.kinds.building.convert (--out DIR | --store) [--programs FILE] [KEY ...]

One description per building of textures/library/facade-programs.json (`facade-program-prototype-1`), tier
`observed`. Measured window, ground-floor and door rectangles become placed openings of a few opening types; a wall
known only by its rows (`structure.floors`) becomes storeys with one opening per bay; wall colour and material, the
base storey's band, string courses and the cornice carry over. What a program does not say is left to the priors.
`--store` writes through the description store and never replaces a description somebody else stored.
"""

import argparse
import json
import math
import sys
import zlib
from pathlib import Path

from . import priors
from .description import SCHEMA_ID, errors

PROGRAMS = Path(__file__).resolve().parents[5] / "textures/library/facade-programs.json"
BY = "building.convert"

MATERIALS = {
    "render": {"family": "plaster", "finish": "fine"},
    "brick": {"family": "brick", "bond": "stretcher"},
    "wood": {
        "family": "weatherboard",
        "orientation": "horizontal",
        "profile": "bevel",
        "scale_m": 0.13,
    },
    "stone": {"family": "stone", "finish": "ashlar"},
    "concrete": {"family": "concrete", "finish": "cast"},
    "concrete_panel": {"family": "concrete", "finish": "panel"},
    "metal": {"family": "sheet_metal", "profile": "cassette"},
    "metal_panel": {"family": "sheet_metal", "profile": "cassette"},
    "glass": {"family": "metal", "finish": "gloss"},
}
# The program's material words in the register's vocabulary, whose usual colours the priors know.
REGISTER_WORD = {
    "render": "kivi",
    "stone": "kivi",
    "brick": "tiili",
    "wood": "puu",
    "concrete": "betoni",
    "concrete_panel": "betoni",
    "metal": "metallilevy",
    "metal_panel": "metallilevy",
    "glass": "lasi",
}
CORNICES = {
    "thin": (0.25, 0.15, "stepped"),
    "projecting": (0.45, 0.35, "stepped"),
    "ornate": (0.6, 0.4, "dentil"),
}
HEADS = {
    "flat": "flat",
    "round": "round",
    "round-arch": "round",
    "segmental": "segmental",
    "segmental-arch": "segmental",
    "pointed": "pointed",
    "pointed-arch": "pointed",
}


def value(field):
    """A program field is `{value, source, support}` or the bare value."""
    return field.get("value") if isinstance(field, dict) else field


def at(values, index: int, default=None):
    """The entry of a per-opening list, or the one value given for all of them."""
    if isinstance(values, list):
        return values[index] if index < len(values) and values[index] is not None else default
    return default if values is None else values


def number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def size(x: float) -> float:
    """Sizes to 5 cm, so windows measured a centimetre apart are one opening type."""
    return round(max(0.2, round(x / 0.05) * 0.05), 2)


class Types:
    """The building's opening types, one per distinct specification."""

    def __init__(self):
        self.names, self.specs = {}, {}

    def name(self, prefix: str, spec: dict) -> str:
        key = json.dumps(spec, sort_keys=True)
        if key not in self.names:
            self.names[key] = f"{prefix}_{sum(n.startswith(prefix + '_') for n in self.specs) + 1}"
            self.specs[self.names[key]] = spec
        return self.names[key]


def trim_of(shape, colour, width: float) -> dict:
    """Surround and pediment of an opening from the program's surround word."""
    paint = {"colour": colour} if colour else {}
    if shape in ("frame", "pediment"):
        out = {"surround": {"kind": "flat", "width_m": width, "projection_m": 0.03, **paint}}
        if shape == "pediment":
            out["pediment"] = {"kind": "triangular", **paint}
        return out
    if shape == "rustication":
        return {"surround": {"kind": "rusticated", "width_m": max(width, 0.2), **paint}}
    return {}


def lights_of(divisions) -> dict | None:
    if not (isinstance(divisions, list) and len(divisions) == 2):
        return None
    cols, rows = (max(1, min(12, int(n))) for n in divisions)
    if (cols, rows) == (1, 1):
        return {"pattern": "single"}
    if (cols, rows) == (2, 1):
        return {"pattern": "two_leaf"}
    if (cols, rows) == (3, 1):
        return {"pattern": "three_leaf"}
    return {"pattern": "grid", "cols": cols, "rows": rows}


def material_of(wall: dict, building_material) -> dict | None:
    """The wall's material when its colour was measured; the family from the wall, else from the building."""
    colour = value(wall.get("wall", {}).get("colour"))
    if not colour:
        return None
    family = MATERIALS.get(value(wall.get("wall", {}).get("material")) or building_material)
    return {**(family or MATERIALS["render"]), "colour": colour}


def wall_of(program: dict, index: int, types: Types, building_material) -> dict:
    geometry = program["geometry"]
    p0, p1 = geometry["p0"], geometry["p1"]
    width = math.dist(p0, p1)
    along = ((p1[0] - p0[0]) / width, (p1[1] - p0[1]) / width)
    # A description's wall runs left to right as seen from outside: outward is to the right of p0 -> p1.
    flipped = along[1] * geometry["normal"][0] - along[0] * geometry["normal"][1] < 0
    if flipped:
        p0, p1 = p1, p0

    def u(x: float) -> float:
        return round(width - x if flipped else x, 3)

    height = program.get("height_m") or 0.0
    trim = program.get("trim", {})
    paint = value(trim.get("colour"))
    paint_field = {"colour": paint} if paint else {}
    surround_width = value(trim.get("window_surrounds"))
    surround_width = (
        round(min(0.25, max(0.08, surround_width)), 2) if number(surround_width) else 0.12
    )
    out = {
        "id": f"w{index:02d}",
        "frame": {
            "p0": [round(c, 3) for c in p0],
            "p1": [round(c, 3) for c in p1],
            "base_h": round(geometry["base_h"], 3),
        },
        "bays": [],
        "storeys": [],
        "conf": {"*": round(float(program.get("bays", {}).get("support") or 0.5), 2)},
    }
    material = material_of(program, building_material)
    if material:
        out["material"] = material

    windows, base = program.get("windows", {}), program.get("base", {})
    frame = value(windows.get("frame_colour"))
    lights = lights_of(value(windows.get("divisions")))
    placed = []
    for i, (x, y, w, h) in enumerate(windows.get("rectangles_m", [])):
        head = at(windows.get("head_shapes"), i, value(windows.get("head_shape")))
        spec = {
            "kind": "window",
            "w_m": size(w),
            "h_m": size(h),
            "head": HEADS.get(head, "flat"),
            **trim_of(at(windows.get("surround_shapes"), i), paint, surround_width),
        }
        if frame:
            spec["frame"] = {"colour": frame}
        if lights:
            spec["lights"] = lights
        placed.append({"type": types.name("window", spec), "u_m": u(x + w / 2), "v_m": round(y, 3)})
    for i, (x, y, w, h) in enumerate(base.get("openings_m", [])):
        head = HEADS.get(at(base.get("head_shapes"), i, value(base.get("head_shape"))), "flat")
        if w >= 1.8 and y < 1.3:
            spec = {
                "kind": "shopfront",
                "w_m": size(w),
                "h_m": size(h),
                "head": head,
                "shopfront": {
                    "panes": max(1, min(12, round(w / 1.6))),
                    "stallriser_m": 0.0 if y > 0.3 else 0.45,
                },
            }
            name = types.name("shop", spec)
        else:
            spec = {
                "kind": "window",
                "w_m": size(w),
                "h_m": size(h),
                "head": head,
                **trim_of(at(base.get("surround_shapes"), i), paint, surround_width),
            }
            name = types.name("window", spec)
        placed.append({"type": name, "u_m": u(x + w / 2), "v_m": round(y, 3)})
    for candidate in program.get("doors", {}).get("candidates", []):
        if "rect_m" in candidate:
            x, y, w, h = candidate["rect_m"]
            spec = {
                "kind": "door",
                "w_m": size(w),
                "h_m": size(h),
                "head": HEADS.get(candidate.get("head_shape"), "flat"),
                "leaf": {"leaves": 2 if w > 1.4 else 1, "style": "half_glazed"},
                **trim_of(candidate.get("surround"), paint, surround_width),
            }
            placed.append(
                {"type": types.name("door", spec), "u_m": u(x + w / 2), "v_m": round(y, 3)}
            )
        elif number(candidate.get("u_m")):
            # A door known only by its place (from the map) stands on the ground.
            placed.append(
                {
                    "type": types.name("door", {"kind": "door"}),
                    "u_m": u(candidate["u_m"]),
                    "v_m": 0,
                }
            )
    if placed:
        out["openings"] = placed

    structure = program.get("structure", {})
    floors = structure.get("floors") or []
    if not placed and floors and height > 2.2:
        each = height / len(floors)
        for k, floor in enumerate(floors):
            bays = floor.get("bays") or []
            pitch = width / max(1, len(bays))
            row = []
            for b, kind in enumerate(bays):
                head = HEADS.get(at(floor.get("head"), b), "flat")
                if kind == "window":
                    spec = {
                        "kind": "window",
                        "w_m": size(min(1.3, pitch * 0.5)),
                        "h_m": size(min(1.8, each * 0.55)),
                        "head": head,
                        **trim_of(at(floor.get("surround"), b), paint, surround_width),
                    }
                    name = types.name("window", spec)
                elif kind == "shop":
                    spec = {
                        "kind": "shopfront",
                        "w_m": size(min(3.6, max(1.2, pitch - 0.6))),
                        "h_m": size(min(2.7, each - 0.7)),
                        "head": head,
                    }
                    name = types.name("shop", spec)
                elif kind == "door":
                    name = types.name("door", {"kind": "door", "head": head})
                elif kind == "arched portal":
                    spec = {
                        "kind": "gateway",
                        "w_m": size(min(2.8, pitch - 0.4)),
                        "h_m": size(min(3.4, each - 0.4)),
                        "head": "round",
                    }
                    name = types.name("gate", spec)
                else:
                    continue
                row.append({"type": name, "u_m": u((b + 0.5) * pitch)})
            out["storeys"].append(
                {
                    "kind": "ground" if k == 0 else "upper",
                    "height_m": round(each, 2),
                    "sill_m": 0.9,
                    "openings": row,
                }
            )

    zones = []
    base_colour = value(base.get("colour"))
    base_height = value(program.get("split", {}).get("base_height_m"))
    if base_colour and number(base_height) and 1.0 < base_height < height:
        family = (material or MATERIALS["render"]) | {"colour": base_colour}
        zones.append(
            {"name": "base", "from_m": 0, "to_m": round(base_height, 2), "material": family}
        )
    if zones:
        out["zones"] = zones

    bands = []
    courses = value(trim.get("string_courses"))
    if not isinstance(courses, list):
        # The structure record counts floors instead: a course above the n-th floor.
        each = height / len(floors) if floors else 0.0
        courses = [
            base_height if n == 1 and number(base_height) else n * each
            for n in structure.get("string_courses") or []
            if number(n) and each
        ]
    for course in courses:
        if number(course) and 0.5 < course < height - 0.3:
            bands.append(
                {
                    "kind": "string_course",
                    "v_m": round(course - 0.07, 2),
                    "height_m": 0.14,
                    "projection_m": 0.05,
                    **paint_field,
                }
            )
    cornice = value(trim.get("cornice"))
    profile = "stepped"
    if number(cornice) and 0.1 < cornice < min(2.0, height / 3):
        projection = min(0.45, max(0.12, cornice * 0.5))
    elif structure.get("cornice") in CORNICES and height > 4:
        cornice, projection, profile = CORNICES[structure["cornice"]]
    else:
        cornice = None
    if cornice:
        bands.append(
            {
                "kind": "eaves_cornice",
                "v_m": round(height - cornice, 2),
                "height_m": round(cornice, 2),
                "projection_m": round(projection, 2),
                "profile": profile,
                **paint_field,
            }
        )
    if bands:
        out["bands"] = bands
    return out


def convert(program: dict) -> dict | None:
    """The description of one facade program; None where the program read nothing a description can hold."""
    types = Types()
    building_material = value(program.get("material"))
    representative = program.get("representative_wall")
    walls, shown, word = [], None, None
    for index, wall in enumerate(program.get("walls", [])):
        described = wall_of(wall, index, types, building_material)
        main = wall.get("wall_id") == representative
        seen = value(wall.get("wall", {}).get("material")) or wall.get("structure", {}).get(
            "material"
        )
        if seen in REGISTER_WORD and (word is None or main):
            word = seen
        if "material" in described and (shown is None or main):
            shown = described["material"]
        # A wall of which nothing was read stays with the priors: described without openings it would be blank.
        if described.get("openings") or described["storeys"]:
            walls.append(described)
    doc = {
        "schema": SCHEMA_ID,
        "id": f"building/{program['building_id']}",
        "tier": "observed",
        "provenance": [
            {
                "source": "facade-program",
                "key": "facade-programs",
                "by": BY,
                "note": f"{len(walls)} wall(s) of textures/library/facade-programs.json"
                + (", flagged needs_review there" if program.get("needs_review") else ""),
            }
        ],
        "walls": walls,
        "conf": {"*": 0.4 if program.get("needs_review") else 0.6},
    }
    if types.specs and walls:
        doc["opening_types"] = types.specs
    # Every wall takes the look of the photographed one: its measured colour, or the material that was read in one
    # of that material's usual colours.
    if shown is None and word:
        family, colours = priors.REGISTER_WALLS[REGISTER_WORD[word]]
        pick = zlib.crc32(doc["id"].encode())
        shown = {**family, "colour": colours[pick % len(colours)], "conf": {"colour": 0.2}}
    if shown:
        doc["materials"] = {"wall": shown}
        doc["wall_defaults"] = {"material": "wall"}
    return doc if walls or shown else None


def ours(stored: dict | None) -> bool:
    """True where the store holds nothing, or only an earlier conversion."""
    return stored is None or all(p.get("by") == BY for p in stored.get("provenance", [{}]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="building.convert")
    parser.add_argument("keys", nargs="*", help="building keys; all programs when omitted")
    parser.add_argument("--programs", type=Path, default=PROGRAMS)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--out", type=Path, help="write KEY.json files into this directory")
    target.add_argument("--store", action="store_true", help="write into the description store")
    args = parser.parse_args(argv)
    programs = json.loads(args.programs.read_text())
    written = kept = invalid = empty = 0
    for program in programs:
        key = program["building_id"]
        if args.keys and key not in args.keys:
            continue
        doc = convert(program)
        if doc is None:
            empty += 1
            continue
        problems = errors(doc)
        if problems:
            invalid += 1
            print(f"{key}: {problems[0]}", file=sys.stderr)
            continue
        if args.store:
            from ...store import read_description, write_description

            if not ours(read_description(doc["id"])):
                kept += 1
                continue
            write_description(doc, "w2-buildings", "converted from facade-programs.json")
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"{key}.json").write_text(
                json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False)
            )
        written += 1
    print(
        json.dumps(
            {
                "written": written,
                "kept_stored": kept,
                "nothing_read": empty,
                "invalid": invalid,
                "of": len(programs),
            }
        )
    )
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
