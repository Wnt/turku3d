"""landmark: the proven game's landmark descriptions as a layer of a building's effective description.

The old game drew its landmarks (churches, the market hall, the castle, the old library...) from parameter
descriptions measured on photographs (`pipeline/turku3d/landmarks`, docs there): reviewed ones in the repository
(`landmarks/descriptions/`), automatic ones in `$TURKU3D_DATA/landmark-descriptions/`, used only when the
reference was verified with confidence 0.5 or more. They are chosen automatically by the proven ranking and drawn
procedurally from their parameters; no photograph is used here.

The old appearance code painted those parameters into one texture per wall. Here they become the building
description's own fields, so the same generator draws them as real geometry:

- materials: the brick (or render, stone, boards) with its mortar, the stone plinth, the trim colour, the roof;
- a measured elevation (`elevations`, engine frame of the old game) becomes the wall of every shell facade that
  faces its way: string courses, pilasters with their pale courses, arched openings with trim and glazing bars,
  doors, medallions and the extruded relief (copings, gables, shoulder arches, pier panels) in the wall's frame;
- without elevations, the body's windows by their spacing and rows, the stone base below 1.5 m, and on a tower
  (taller than the description's tower height and narrower than its tower width) the stone stage, the tower's
  opening rows, cornices and the metal stage above it; the entrance on the tower's west face.

The layer lies between the automatic fit and the stored description (describe.effective): a stored reading of the
building wins where it says something.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

ORIGIN_E, ORIGIN_N = 23459500.0, 6704500.0  # the old game's engine frame (docs/specs/coordinates.md)
REVIEWED = Path(__file__).resolve().parents[3] / "landmarks" / "descriptions"
MIN_CONFIDENCE = 0.5
BY = "building.landmark"
FACING = 0.9  # a facade takes an elevation whose outward normal is this close to its own (the old `select`)
BODY_STONE_M = 1.5  # stone below this height above the base on the body's walls (the old wall_maps)
TRIM_M = 0.16  # the trim round a body or tower opening (0.32 m wider and taller than the opening)


def data_root() -> Path:
    from turku3d import config

    return Path(os.environ.get("TURKU3D_DATA") or config.data_root())


def find(key: str) -> dict | None:
    """The proven landmark description of a building: the reviewed one in the repository, else an automatic one
    whose reference was verified with confidence MIN_CONFIDENCE or more."""
    reviewed = REVIEWED / f"{key}.json"
    if reviewed.is_file():
        d = json.loads(reviewed.read_text())
        d["_tier"] = "reviewed"
    else:
        path = data_root() / "landmark-descriptions" / f"{key}.json"
        if not path.is_file():
            return None
        d = json.loads(path.read_text())
        if d.get("reference_match") != "verified" or (d.get("reference_confidence") or 0) < MIN_CONFIDENCE:
            return None
        d["_tier"] = "observed"
    if d.get("version") != 1 or str(d.get("building_id")) != key:
        return None
    return d


def hexc(rgb) -> str:
    r, g, b = (int(round(min(255, max(0, float(c))))) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


BONDS = {"running": "stretcher", "stretcher": "stretcher", "flemish": "flemish", "english": "english",
         "monk": "flemish", "header": "header", "stack": "stack"}


def materials(d: dict) -> dict:
    """The landmark's materials by name: wall, plinth (stone), trim, roof."""
    brick, stone = d.get("brick") or {}, d.get("stone") or {}
    kind = d.get("wall_material", "brick")
    weathering = round(float(d.get("weathering") or 0.0), 2)
    colour = hexc(brick.get("colour", [150, 90, 70]))
    if kind == "stone":
        wall = {"family": "rubble", "colour": hexc(stone.get("colour", [130, 125, 115])),
                "colour2": hexc(stone.get("mortar_colour", [160, 155, 145])),
                "scale_m": round(float((stone.get("size_m") or [0.5])[0]), 2)}
    elif kind in ("render", "concrete"):
        wall = {"family": "plaster", "colour": colour, "finish": "fine"}
    elif kind == "wood":
        wall = {"family": "weatherboard", "colour": colour, "orientation": "vertical", "profile": "board_batten",
                "scale_m": 0.16}
    else:
        wall = {"family": "brick", "colour": colour, "colour2": hexc(brick.get("mortar_colour", [170, 160, 140])),
                "bond": BONDS.get(brick.get("bond", "running"), "stretcher")}
    size = stone.get("size_m") or [0.6, 0.3]
    plinth = {"family": "granite", "colour": hexc(stone.get("colour", [130, 125, 115])),
              "colour2": hexc(stone.get("mortar_colour", [150, 145, 135])), "finish": "ashlar",
              "scale_m": round(float(size[0]), 2)}
    out = {"wall": wall, "plinth": plinth, "roof_metal": roof_material(d)}
    if weathering:
        for m in out.values():
            m["weathering"] = weathering
    return out


def roof_material(d: dict) -> dict:
    roof = d.get("roof") or {}
    colour = hexc(roof.get("colour", [90, 90, 90]))
    kind = roof.get("material", "metal")
    if kind == "copper":
        return {"family": "copper", "colour": colour, "finish": "weathered"}
    if kind == "slate":
        return {"family": "slate", "colour": colour}
    if kind in ("tile", "clay_tile"):
        return {"family": "clay_tile", "colour": colour, "profile": "pantile"}
    seam = roof.get("seam_m") or 0.58
    return {"family": "sheet_metal", "colour": colour, "profile": "standing_seam", "scale_m": round(float(seam), 2)}


def style_type(d: dict, automatic: str) -> str:
    words = f"{d.get('style', '')} {d.get('name', '')}".lower()
    if any(w in words for w in ("church", "cathedral", "kirkko", "chapel", "kappeli", "tapuli", "bell")):
        return "church"
    if automatic in ("stone_apartment", "classicist_apartment", "industrial", "commercial", "office", "slab_block"):
        return "public"
    return automatic


class Types:
    """The landmark's opening types, one per distinct specification."""

    def __init__(self):
        self.names, self.specs = {}, {}

    def name(self, prefix: str, spec: dict) -> str:
        key = json.dumps(spec, sort_keys=True)
        if key not in self.names:
            self.names[key] = f"lm_{prefix}_{sum(n.startswith(f'lm_{prefix}_') for n in self.specs) + 1}"
            self.specs[self.names[key]] = spec
        return self.names[key]


def r2(x: float) -> float:
    return round(float(x), 3)


# --------------------------------------------------------------------------- measured elevations


def _relief_polygon(part: dict):
    """The old relief part as a polygon in elevation metres (x, v): a polygon, or a path drawn `width_m` wide."""
    if "path_m" in part:
        return shapely.LineString(part["path_m"]).buffer(part["width_m"] / 2, cap_style="flat", join_style="mitre")
    return Polygon(part["polygon_m"], part.get("holes_m"))


def _rings(polygon, to_u):
    """A polygon's rings in wall (u, v) metres."""
    out = []
    for poly in shapely.get_parts(polygon):
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        rings = [poly.exterior, *poly.interiors]
        out.append([[[r2(to_u(x)), r2(v)] for x, v in list(ring.coords)[:-1]] for ring in rings])
    return out


def opening_type(opening: dict, trim: str, types: Types) -> tuple[str, dict]:
    """The old elevation opening as an opening type and its placement fields."""
    door = opening.get("kind") == "door"
    shape = opening.get("shape", "round")
    head = {"rect": "flat", "pointed": "pointed"}.get(shape, "round")
    columns, rows = int(opening.get("columns", 2)), int(opening.get("rows", 3))
    frame = hexc(opening.get("frame_colour", [180, 179, 160]))
    spec = {"kind": "door" if door else "window", "head": head, "recess_m": 0.15 if door else 0.12,
            "surround": {"kind": "flat", "width_m": round(float(opening.get("trim_m", 0.12)), 3),
                         "projection_m": 0.045, "colour": trim}}
    if door:
        style = "half_glazed" if opening.get("style") == "panelled_glazed" else "panelled"
        spec["leaf"] = {"leaves": 2 if columns >= 2 else 1, "style": style,
                        "colour": hexc(opening.get("colour", [91, 63, 44])), "fanlight": "none"}
        spec["frame"] = {"colour": hexc(opening.get("colour", [91, 63, 44])), "width_m": 0.08}
    else:
        spec["lights"] = {"pattern": "grid", "cols": max(1, columns), "rows": max(1, rows)}
        spec["frame"] = {"colour": frame, "width_m": 0.045}
        spec["sill"] = {"kind": "stone", "projection_m": 0.04, "height_m": 0.06, "colour": trim}
        spec["glass"] = {"interior": "dark", "curtains": 0.0, "blinds": 0.0}
    w, h = r2(opening["width_m"]), r2(opening["height_m"])
    return types.name("door" if door else "window", spec), {"w_m": w, "h_m": h}


def elevation_wall(row: dict, facade, base: float, d: dict, types: Types, index: int) -> dict:
    """One shell facade drawn with a measured elevation: every measured part placed by its position in the old
    engine frame, so it lands where the old game drew it."""
    axis = np.asarray(row["axis_xz"], float)
    origin = np.asarray(row["origin_xz"], float)
    width = float(row["width_m"])
    trim = hexc(d.get("trim_colour", [200, 195, 180]))
    p0 = np.asarray(facade.p0[:2], float)

    def to_u(x: float) -> float:
        p = origin + axis * x
        return float((np.array([ORIGIN_E + p[0], ORIGIN_N - p[1]]) - p0) @ facade.u_dir[:2])

    a, b = sorted((to_u(0.0), to_u(width)))
    lo, hi = max(a, 0.0), min(b, facade.width)
    wall = {
        "id": f"landmark_{index}",
        "frame": {"p0": [r2(v) for v in facade.p0[:2]], "p1": [r2(v) for v in facade.p1[:2]],
                  "base_h": r2(base + float(row.get("base_offset_m", 0.0)))},
        "material": "wall",
        "plinth": {"height_m": r2(row.get("plinth_m", 0.55)), "projection_m": 0.03, "material": "plinth"},
        "storeys": [],
        "bays": [],
        "bands": [
            {"kind": "string_course", "v_m": r2(low), "height_m": r2(h), "projection_m": 0.04, "profile": "flat",
             "colour": trim, "u_from_m": r2(lo), "u_to_m": r2(hi)}
            for low, h in row.get("bands", [])
        ],
    }
    reliefs = [(part, _relief_polygon(part)) for part in row.get("relief", [])]
    # A pilaster that the relief already builds as a projecting pier (a panel in the wall's material over its
    # line) stands as far out as that panel, so its pale courses lie on the pier's face as the old game painted them.
    piers = [(poly, float(part.get("offset_m", 0.025)) + float(part["depth_m"]))
             for part, poly in reliefs if part.get("material") in ("elevation", "brick")]
    verticals = []
    for x, w, low, high in row.get("pilasters", []):
        u = to_u(x)
        if not 0.0 <= u <= facade.width:
            continue
        middle = shapely.Point(x, (low + high) / 2)
        front = max([front for poly, front in piers if poly.buffer(0.05).contains(middle)], default=0.0)
        verticals.append({"kind": "pilaster", "u_m": r2(u), "width_m": r2(w), "from_m": r2(low), "to_m": r2(high),
                          "projection_m": r2(max(0.08, front + 0.005)), "capital": "none", "material": "wall",
                          "courses": {"pitch_m": 0.65, "first_m": 0.3, "height_m": 0.14, "width_m": r2(w * 1.2),
                                      "colour": trim}})
    if verticals:
        wall["verticals"] = verticals
    openings = list(row.get("openings", []))
    for repeat in row.get("repeated_openings", []):
        for x in np.arange(repeat["start_m"], width - repeat.get("end_margin_m", 1), repeat["spacing_m"]):
            openings.append({**repeat, "x_m": float(x)})
    placed = []
    for opening in openings:
        name, size = opening_type(opening, trim, types)
        placed.append({"type": name, "u_m": r2(to_u(opening["x_m"])),
                       "v_m": r2(max(0.0, opening["sill_m"])), **size})
    if placed:
        wall["openings"] = placed
    attached = [
        {"kind": "ornament", "type": "medallion", "u_m": r2(to_u(x)), "v_m": r2(low - radius),
         "w_m": r2(2 * radius), "h_m": r2(2 * radius), "colour": trim}
        for x, low, radius in row.get("medallions", [])
    ]
    if attached:
        wall["attached"] = attached
    out_reliefs = []
    for part, polygon in reliefs:
        if polygon.is_empty or not polygon.is_valid:
            continue
        kind = part.get("material", "trim")
        depth = float(part["depth_m"])
        if not 0.0 < depth <= 2.0:
            continue
        for rings in _rings(polygon, to_u):
            entry = {"polygon_m": rings[0], "offset_m": r2(part.get("offset_m", 0.025)), "depth_m": r2(depth)}
            if len(rings) > 1:
                entry["holes_m"] = rings[1:]
            if "anchor_m" in part:
                # The old game built a part on the wall piece that holds its anchor, even where the part itself
                # stands above the surveyed wall (the corner pediments over the eaves).
                entry["anchor_m"] = [r2(to_u(part["anchor_m"][0])), r2(part["anchor_m"][1])]
            if kind == "trim":
                entry["colour"] = trim
            else:
                entry["material"] = "wall"
            out_reliefs.append(entry)
    if out_reliefs:
        wall["reliefs"] = out_reliefs
    return wall


# --------------------------------------------------------------------------- body and tower walls


def window_rows(windows: dict, body: float, storeys) -> list[float]:
    """The old `procedural.window_rows`: sill heights over the base of the body's window rows."""
    rows = windows.get("rows_m")
    if rows:
        return sorted(float(r) for r in rows)
    sill = float(windows.get("sill_m", 1.0))
    if rows is None:
        return [sill]
    height = float(windows.get("height_m", 1.6))
    count = int(storeys) if storeys and storeys > 0 else max(1, round(body / 3.4))
    pitch = max(body / count, height + 0.6)
    first = min(int(sill // pitch), count - 1)
    candidates = (round(sill + (i - first) * pitch, 3) for i in range(count))
    return [r for r in candidates if r >= 0.6 and r + height <= body - 0.3]


def head_of(kind: str):
    kind = (kind or "").lower()
    if any(k in kind for k in ("gothic", "pointed", "lancet")):
        return "pointed"
    if "arch" in kind or "round" in kind:
        return "round"
    return "flat"


def window_spec(w: float, h: float, head: str, mullions: int, trim: str) -> dict:
    return {
        "kind": "window", "w_m": w, "h_m": h, "head": head, "recess_m": 0.18,
        "lights": {"pattern": "grid", "cols": max(1, int(mullions) + 1), "rows": max(1, round(h / 1.1))},
        "frame": {"colour": "#65695e", "width_m": 0.05},
        "surround": {"kind": "flat", "width_m": TRIM_M, "projection_m": 0.035, "colour": trim},
        "sill": {"kind": "stone", "projection_m": 0.05, "height_m": 0.08, "colour": trim},
        "glass": {"interior": "dark", "curtains": 0.0, "blinds": 0.0},
    }


def generic_wall(facade, low: float, high: float, base: float, d: dict, types: Types, rows: list, index: int,
                 tower: bool) -> dict:
    """A body or tower facade of a landmark without measured elevations, as the old wall_maps drew it."""
    trim = hexc(d.get("trim_colour", [200, 195, 180]))
    width = float(facade.width)
    wall = {
        "id": f"landmark_{index}",
        "frame": {"p0": [r2(v) for v in facade.p0[:2]], "p1": [r2(v) for v in facade.p1[:2]], "base_h": r2(base)},
        "material": "wall",
        "storeys": [],
        "bays": [],
    }
    t = d.get("tower") or {}
    copper = copper_start(d)
    zones = []
    # The body's stone base is the plinth (wall_defaults); a tower's stone stage reaches higher.
    stone_top = float(t.get("stone_top_m") or 0.0) if tower else 0.0
    if stone_top > max(low, BODY_STONE_M) + 0.05:
        zones.append({"material": "plinth", "from_m": r2(low - 1.0), "to_m": r2(stone_top)})
    if copper < high:
        zones.append({"material": "roof_metal", "from_m": r2(max(copper, low - 1.0)), "to_m": r2(high + 1.0)})
    if zones:
        wall["zones"] = zones

    def fits(w: float, sill: float, h: float) -> bool:
        return w + 2 * TRIM_M <= width and sill >= low + 0.3 and sill + h <= high - 0.3 and sill + h <= copper

    placed = []
    if tower:
        for sill, w, h, count in t.get("opening_rows") or []:
            count = int(count)
            if count < 1 or not fits(w, sill, h):
                continue
            name = types.name("tower", window_spec(r2(w), r2(h), "pointed" if "gothic" in d.get("style", "") else "round",
                                                   1, trim))
            placed += [{"type": name, "u_m": r2(width * (i + 1) / (count + 1)), "v_m": r2(sill), "w_m": r2(w),
                        "h_m": r2(h)} for i in range(count)]
        bands = [{"kind": "cornice", "v_m": r2(level - 0.22), "height_m": 0.22, "projection_m": 0.12,
                  "profile": "stepped", "colour": trim}
                 for level in t.get("cornices_m") or [] if low < level < high]
        if bands:
            wall["bands"] = bands
    else:
        w = d.get("windows") or {}
        if w.get("width_m") and w.get("height_m") and w.get("spacing_m"):
            count = max(0, int(width / float(w["spacing_m"])))
            name = types.name("window", window_spec(r2(w["width_m"]), r2(w["height_m"]), head_of(w.get("type")),
                                                    int(w.get("mullions") or 1), trim))
            for sill in rows:
                if not fits(float(w["width_m"]), sill, float(w["height_m"])):
                    continue
                placed += [{"type": name, "u_m": r2(width * (i + 0.5) / count), "v_m": r2(sill)}
                           for i in range(count)]
    entrance = d.get("entrance") or {}
    # The old game put the entrance on the tower's west face.
    if tower and entrance.get("width_m") and float(facade.normal[0]) < -0.9:
        spec = {"kind": "door", "head": "pointed", "recess_m": 0.35,
                "leaf": {"leaves": 2, "style": "panelled", "colour": "#4f3b2c", "fanlight": "none"},
                "surround": {"kind": "moulded", "width_m": 0.3, "projection_m": 0.06, "colour": trim}}
        name = types.name("entrance", spec)
        placed.append({"type": name, "u_m": r2(width / 2), "v_m": r2(entrance.get("sill_m", 0.1)),
                       "w_m": r2(entrance["width_m"]), "h_m": r2(entrance["height_m"])})
    if placed:
        wall["openings"] = placed
    return wall


def copper_start(d: dict) -> float:
    """Height over the base where the walls turn to the roof's metal (the old procedural.copper_start, without its
    reading of metal cladding in the prose): a tower's metal stage, else none."""
    start = float((d.get("tower") or {}).get("copper_start_m") or 0.0)
    return start if start > 0 else math.inf


# --------------------------------------------------------------------------- the layer


def layer(key: str, doc: dict, stored: dict | None = None, d: dict | None = None) -> dict | None:
    """What the proven landmark description of building `key` says, as description fields laid over the automatic
    fit `doc` (which holds the shell and the register); None for a building without one. As in the old game, a
    reading of the building's own walls (`stored` with walls: a vision reading or a photographed facade program)
    outranks an automatic landmark estimate; a reviewed landmark description always applies."""
    d = d if d is not None else find(key)
    read = bool((stored or {}).get("walls"))
    if d is None or (d["_tier"] != "reviewed" and read):
        return None
    # `surfaces` says what the old game took from the description; it drew the other surfaces from the building's
    # facade programs. A building with no reading of its walls here has nothing better than the description.
    surfaces = d.get("surfaces", ["wall", "roof"])
    wall_part, roof_part = "wall" in surfaces or not read, "roof" in surfaces
    if not (wall_part or roof_part):
        return None
    from . import compile as compile_module
    from . import priors
    from . import shell as shell_module

    origin = compile_module.building_origin(doc["shell"])
    local = compile_module._localise({"shell": doc["shell"]}, origin)
    shell = shell_module.read(local["shell"])
    base = float(origin[2])
    types = Types()
    walls = []
    elevations = d.get("elevations") or []
    t = d.get("tower") or {}
    pieces = []
    for facade in shell.facades:
        b = facade.outline.bounds
        low, high = float(facade.origin[2] + b[1]), float(facade.origin[2] + b[3])
        pieces.append((facade, low, high))
    # The body's height: the width-weighted median top of the walls (the old appearance.body_height).
    tops = sorted((high, float(f.width)) for f, _low, high in pieces)
    total, run, body = sum(w for _, w in tops), 0.0, 0.0
    for top, w in tops:
        run += w
        if run >= total / 2:
            body = top
            break
    rows = window_rows(d.get("windows") or {}, body, (doc.get("register") or {}).get("storeys"))
    for facade, low, high in pieces:
        absolute = _absolute(facade, origin)
        if elevations:
            normal = np.array([facade.normal[0], -facade.normal[1]])  # survey normal in the old engine's x/z
            score, row = max(((float(normal @ np.asarray(r["normal_xz"], float)), r) for r in elevations),
                             key=lambda pair: pair[0])
            if score > FACING and facade.width >= 1.5:
                walls.append(elevation_wall(row, absolute, base, d, types, facade.index))
            continue
        if facade.width < 3.0 and high < copper_start(d):
            continue
        tower = bool(t) and high > float(t.get("minimum_height_m") or math.inf) and facade.width < float(
            t.get("maximum_width_m") or 0.0)
        walls.append(generic_wall(absolute, low, high, base, d, types, rows, facade.index, tower))
    named = materials(d)
    out = {
        "style": {"type": style_type(d, priors.style_type(doc)), "conf": {"*": 0.8}},
        "provenance": [{"source": "landmark-description", "key": f"landmark/{key}", "by": BY,
                        "name": d.get("name", ""), "style": d.get("style", ""), "tier": d["_tier"]}],
        "materials": {"roof_metal": named["roof_metal"]},
    }
    if wall_part:
        out["materials"].update(wall=named["wall"], plinth=named["plinth"])
        out["opening_types"] = types.specs
        out["wall_defaults"] = {
            "material": "wall",
            "plinth": {"height_m": BODY_STONE_M, "projection_m": 0.03, "material": "plinth"},
            "storeys": [],
            "bays": [],
        }
        if walls:
            out["walls"] = walls
    if roof_part:
        out["roof"] = {"material": "roof_metal", "chimneys": []}
    return out


def unclaimed(walls: list, stored: list, shell: dict) -> list:
    """The landmark walls whose shell facade no stored wall's frame lies on (shell.match, as compile matches)."""
    from . import compile as compile_module
    from . import shell as shell_module

    origin = compile_module.building_origin(shell)
    facades = shell_module.read(compile_module._localise({"shell": shell}, origin)["shell"]).facades
    claimed = set()
    for wall in stored:
        frame = wall.get("frame")
        if frame:
            p0, p1 = np.asarray(frame["p0"], float) - origin[:2], np.asarray(frame["p1"], float) - origin[:2]
            claimed.update(f.index for f in shell_module.matches(facades, p0, p1))
    return [w for w in walls if int(w["id"].rsplit("_", 1)[1]) not in claimed]


def _absolute(facade, origin):
    """The facade's frame in survey coordinates (a light view of shell.Facade)."""

    class Frame:
        pass

    f = Frame()
    f.index = facade.index
    f.width = float(facade.width)
    f.u_dir = np.asarray(facade.u_dir, float)
    f.normal = np.asarray(facade.normal, float)
    f.p0 = np.asarray(facade.p0, float) + origin[: len(facade.p0)]
    f.p1 = np.asarray(facade.p1, float) + origin[: len(facade.p1)]
    return f
