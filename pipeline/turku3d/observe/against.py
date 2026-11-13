"""against: what the automatic tier draws against what was read, per attribute. No model call.

For every building description a vision reading stored, the automatic description is made again from the current
sources and rules (shell, register, priors, orthophoto roof) and compared with the reading on the wall the street
sees most of: style, wall material and colour, storeys, plinth, ground storey, windows, bays, balconies, roof.
The stored fence readings are compared with the fence kind's automatic facts the same way. Run it again after a
rule change: the agreement rates are the measure of the automatic rules.

  python -m turku3d.observe against [--out DIR]     # DIR defaults to $TURKU3D_WORK/observe/against

Writes buildings.csv and fences.csv (one row per entity), summary.json (the rates) and report.md (the tables).
"""

import collections
import csv
import json
import math
import statistics
from pathlib import Path

import numpy as np

from turku3d import config
from turku3d.world.api import Area
from turku3d.world.kinds import building_shell
from turku3d.world.kinds.building import compile as compile_module
from turku3d.world.kinds.building import describe, priors
from turku3d.world.kinds.building import facade as facade_module
from turku3d.world.kinds.building import shell as shell_module
from turku3d.world.kinds.building.description import expand_storeys
from turku3d.world.sources import Sources
from turku3d.world.store import data_root

ERAS = ((1900, "-1899"), (1920, "1900-19"), (1940, "1920-39"), (1960, "1940-59"), (1980, "1960-79"),
        (2000, "1980-99"), (9999, "2000-"))
COLOUR_CLOSE = 10.0  # CIEDE2000 under which two wall colours read as the same paint in a street view
FULL_STOREYS = ("ground", "upper", "mezzanine")


def era(year) -> str:
    return "undated" if not year else next(name for limit, name in ERAS if year < limit)


def lab(colour: str) -> tuple[float, float, float]:
    rgb = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)
    xyz = (0.4124 * r + 0.3576 * g + 0.1805 * b, 0.2126 * r + 0.7152 * g + 0.0722 * b,
           0.0193 * r + 0.1192 * g + 0.9505 * b)
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (xyz[0] / 0.95047, xyz[1], xyz[2] / 1.08883)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def delta_e(one: str, other: str) -> float:
    """CIEDE2000 between two sRGB colours."""
    (l1, a1, b1), (l2, a2, b2) = lab(one), lab(other)
    c_mean = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_mean ** 7 / (c_mean ** 7 + 25 ** 7)))
    a1, a2 = a1 * (1 + g), a2 * (1 + g)
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    h1, h2 = math.degrees(math.atan2(b1, a1)) % 360, math.degrees(math.atan2(b2, a2)) % 360
    dh = h2 - h1
    if c1 * c2 == 0:
        dh = 0.0
    elif abs(dh) > 180:
        dh -= math.copysign(360, dh)
    d_l, d_c = l2 - l1, c2 - c1
    d_h = 2 * math.sqrt(c1 * c2) * math.sin(math.radians(dh / 2))
    l_mean, c_mean = (l1 + l2) / 2, (c1 + c2) / 2
    h_mean = h1 + h2 if c1 * c2 == 0 else (h1 + h2) / 2 if abs(h1 - h2) <= 180 else (h1 + h2 + 360) / 2 % 360
    t = (1 - 0.17 * math.cos(math.radians(h_mean - 30)) + 0.24 * math.cos(math.radians(2 * h_mean))
         + 0.32 * math.cos(math.radians(3 * h_mean + 6)) - 0.20 * math.cos(math.radians(4 * h_mean - 63)))
    s_l = 1 + 0.015 * (l_mean - 50) ** 2 / math.sqrt(20 + (l_mean - 50) ** 2)
    s_c, s_h = 1 + 0.045 * c_mean, 1 + 0.015 * c_mean * t
    r_t = (-2 * math.sqrt(c_mean ** 7 / (c_mean ** 7 + 25 ** 7))
           * math.sin(math.radians(60 * math.exp(-(((h_mean - 275) / 25) ** 2)))))
    return math.sqrt((d_l / s_l) ** 2 + (d_c / s_c) ** 2 + (d_h / s_h) ** 2 + r_t * (d_c / s_c) * (d_h / s_h))


def colour_parts(auto: str, read: str) -> dict:
    """The difference read minus automatic: lightness, chroma, and the whole as CIEDE2000."""
    (l1, a1, b1), (l2, a2, b2) = lab(auto), lab(read)
    return {"dE": round(delta_e(auto, read), 1), "dL": round(l2 - l1, 1),
            "dC": round(math.hypot(a2, b2) - math.hypot(a1, b1), 1)}


def colour_class(colour: str | None) -> str | None:
    """A coarse name for a wall or roof colour: what a passer-by would call it."""
    if not colour:
        return None
    lightness, a, b = lab(colour)
    chroma, hue = math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360
    if chroma < 7:
        return "white" if lightness > 85 else "light grey" if lightness > 65 else "grey" if lightness > 40 else "dark"
    if hue < 20 or hue >= 330:
        return "red"
    if hue < 60:
        return "cream" if chroma < 18 and lightness >= 70 else "red" if hue < 45 and chroma >= 22 else "brown"
    if hue < 105:
        return "cream" if chroma < 20 and lightness >= 70 else "yellow" if lightness >= 60 else "brown"
    return "green" if hue < 190 else "blue"


def material(doc: dict, value) -> dict:
    if isinstance(value, str):
        value = doc.get("materials", {}).get(value)
    return value if isinstance(value, dict) else {}


def facades_of(doc: dict, frames: list) -> tuple[list, dict]:
    """The shell facade under each wall frame, as `compile` finds it, and the roles (archetypes.wall_roles: street
    doors, shopfronts, balconies) `compile` gives the walls of an unread building, by facade index."""
    origin = compile_module.building_origin(doc["shell"])
    local = compile_module._localise({**doc, "walls": [{"frame": f} for f in frames]}, origin)  # noqa: SLF001
    shell = shell_module.read(local["shell"])
    roles = compile_module.wall_roles({**local, "walls": []}, shell)
    return [shell_module.match(shell.facades, w["frame"]["p0"], w["frame"]["p1"]) for w in local["walls"]], roles


def automatic_wall(auto: dict, facade, frame: dict, roles: dict | None = None) -> tuple[dict, float]:
    """The wall the automatic tier builds on a facade, with the role `compile` gives it, and the facade's width."""
    wall = priors.resolve_wall(auto, None)
    if facade is None:
        length = math.dist(frame["p0"], frame["p1"])
        return priors.fitted_wall(auto, wall, float(frame.get("height_m", 3.0))), length
    foot = float(facade.ground[:, 1].min()) if len(facade.ground) else 0.0
    height = facade_module.eaves_height(facade.outline, facade.width) - foot
    return priors.fitted_wall(auto, wall, height, (roles or {}).get(facade.index)), facade.width


def bay_count(wall: dict, width: float, described: bool) -> int:
    """Bays on a wall, by the generator's rule for a grid."""
    bays = wall.get("bays", [])
    if not isinstance(bays, dict):
        return len(bays)
    pitch, count = bays.get("pitch_m"), bays.get("count")
    fits = max(0, int((width - (1.6 if described else 2.4)) / pitch) + 1) if pitch else 0
    return fits if count is None else min(count, fits) if pitch else count


def bay_pitch(wall: dict) -> float | None:
    bays = wall.get("bays", [])
    if isinstance(bays, dict):
        return bays.get("pitch_m")
    centres = sorted(b["u_m"] for b in bays if "u_m" in b)
    return round(statistics.median(b - a for a, b in zip(centres, centres[1:], strict=False)), 2) if len(centres) > 1 else None


def main_window(doc: dict, wall: dict, bays: int) -> dict | None:
    """The window type most of the wall's windows have, resolved."""
    counts = collections.Counter()
    for storey in expand_storeys(wall.get("storeys", [])):
        if storey.get("kind") not in FULL_STOREYS:
            continue
        if storey.get("window"):
            counts[storey["window"]] += max(bays, 1)
        counts.update(cell for cell in storey.get("row", []) if cell)
        counts.update(o["type"] for o in storey.get("openings", []) if o.get("type"))
    counts.update(o["type"] for o in wall.get("openings", []) if o.get("type"))
    for name, _ in counts.most_common():
        spec = priors.resolve_opening(doc, name)
        if spec["kind"] == "window":
            return spec
    return None


def ground_storey(doc: dict, wall: dict) -> str:
    """How the ground storey differs from the storeys above: shopfronts, another material, or not at all."""
    storeys = expand_storeys(wall.get("storeys", []))
    names = [o.get("type") for o in wall.get("openings", []) if o.get("v_m", 0) < 2.0]
    if storeys:
        ground = storeys[0]
        names += [ground.get("window"), *ground.get("row", []), *(o.get("type") for o in ground.get("openings", []))]
    kinds = {priors.resolve_opening(doc, n)["kind"] for n in names if n}
    if "shopfront" in kinds:
        return "shopfront"
    body = material(doc, wall.get("material")).get("family")
    for zone in wall.get("zones", []):
        low = zone.get("v_m", zone.get("from_m", 0.0)) or 0.0
        family = material(doc, zone.get("material")).get("family")
        if low < 1.5 and family and family != body and (zone.get("h_m") or 3.0) >= 2.0:
            return "other material"
    return "plain"


def roof_form(doc: dict) -> str:
    return "pitched" if priors.roof_pitch_deg(doc) > 10 else "flat"


FLAT_HINTS = ("flat", "parapet", "low")


def building_row(key: str, stored: dict, sources, measured: dict | None = None) -> dict | None:
    """`measured`: the `measured` block the automatic tier gets for this building (a colour measured from
    panoramas, as a batch would store it for an unread building); None draws without one."""
    frames = [w for w in stored.get("walls", []) if "frame" in w]
    if not frames:
        return None
    near = frames[0]["frame"]["p0"]
    stubs = building_shell.discover(Area(float(near[0]), float(near[1]), 10.0), sources)
    stub = next((s for s in stubs if s.id == f"building/{key}"), None)
    if stub is None:
        return None
    shell, register = stub.facts["shell"], stub.facts["register"]
    auto = describe.automatic(key, shell, register, measured)
    read = describe.effective(key, shell, register, stored)

    def weight(wall):
        frame = wall["frame"]
        return math.dist(frame["p0"], frame["p1"]) * wall.get("observed", {}).get("visible_share", 0.5)

    wall = max(frames, key=weight)
    under, roles = facades_of(auto, [w["frame"] for w in frames])
    facade = under[frames.index(wall)]
    read_wall = priors.resolve_wall(read, wall)
    auto_wall, width = automatic_wall(auto, facade, wall["frame"], roles)
    read_mat, auto_mat = material(read, read_wall.get("material")), material(auto, auto_wall.get("material"))
    read_bays, auto_bays = bay_count(read_wall, width, True), bay_count(auto_wall, width, False)
    read_window, auto_window = main_window(read, read_wall, read_bays), main_window(auto, auto_wall, auto_bays)
    read_plinth, auto_plinth = read_wall.get("plinth", {}), auto_wall.get("plinth", {})
    read_roof, auto_roof = priors.resolve_roof(read), priors.resolve_roof(auto)
    read_roofing, auto_roofing = material(read, read_roof.get("material")), material(auto, auto_roof.get("material"))
    full = lambda w: sum(s.get("kind") in FULL_STOREYS for s in expand_storeys(w.get("storeys", [])))  # noqa: E731
    hint = str(stored.get("roof", {}).get("form_hint", "")).lower()
    balconies = [b for w in frames for b in priors.resolve_wall(read, w).get("balconies", [])]
    # The automatic balconies on the same walls as the read ones: those of the facades under every read frame.
    auto_balconies = sum(len((roles.get(index) or {}).get("balconies", [])) for index in {f.index for f in under if f is not None})
    row = {
        "key": key, "address": register.get("address"), "year": register.get("year"), "era": era(register.get("year")),
        "use": register.get("use"), "register_material": register.get("material"),
        "register_storeys": register.get("storeys"), "height_m": register.get("height_m"), "tier": stored.get("tier"),
        "wall": wall.get("id"), "wall_width_m": round(width, 1), "wall_matched": facade is not None,
        "style_auto": priors.style_type(auto), "style_read": stored.get("style", {}).get("type"),
        "wall_family_auto": auto_mat.get("family"), "wall_family_read": read_mat.get("family"),
        "wall_colour_auto": auto_mat.get("colour"), "wall_colour_read": read_mat.get("colour"),
        "wall_class_auto": colour_class(auto_mat.get("colour")), "wall_class_read": colour_class(read_mat.get("colour")),
        "storeys_auto": full(auto_wall), "storeys_read": full(read_wall),
        "plinth_family_auto": material(auto, auto_plinth.get("material")).get("family"),
        "plinth_family_read": material(read, read_plinth.get("material")).get("family"),
        "plinth_m_auto": auto_plinth.get("height_m"), "plinth_m_read": read_plinth.get("height_m"),
        "ground_auto": ground_storey(auto, auto_wall), "ground_read": ground_storey(read, read_wall),
        "bays_auto": auto_bays, "bays_read": read_bays,
        "pitch_m_auto": bay_pitch(auto_wall), "pitch_m_read": bay_pitch(read_wall),
        "window_w_auto": auto_window and auto_window["w_m"], "window_w_read": read_window and read_window["w_m"],
        "window_h_auto": auto_window and auto_window["h_m"], "window_h_read": read_window and read_window["h_m"],
        "lights_auto": auto_window and auto_window.get("lights", {}).get("pattern"),
        "lights_read": read_window and read_window.get("lights", {}).get("pattern"),
        "surround_auto": auto_window and auto_window["surround"].get("kind"),
        "surround_read": read_window and read_window["surround"].get("kind"),
        "balconies_auto": auto_balconies, "balconies_read": len(balconies),
        "balcony_type_read": collections.Counter(b.get("type") for b in balconies).most_common(1)[0][0] if balconies else None,
        "roof_form_shell": roof_form(auto), "roof_form_read": stored.get("roof", {}).get("form_hint"),
        "roof_form_read_class": None if not hint else "flat" if any(h in hint for h in FLAT_HINTS) else "pitched",
        "roof_family_auto": auto_roofing.get("family"), "roof_family_read": read_roofing.get("family"),
        "roof_colour_auto": auto_roofing.get("colour"), "roof_colour_read": read_roofing.get("colour"),
        "roof_class_auto": colour_class(auto_roofing.get("colour")), "roof_class_read": colour_class(read_roofing.get("colour")),
        "roof_register_colour": register.get("roof_colour"),
        "roof_conf": (read_roofing.get("conf") or stored.get("roof", {}).get("conf") or {}).get("*"),
        "eaves_m_auto": auto_roof.get("eaves", {}).get("overhang_m"), "eaves_m_read": read_roof.get("eaves", {}).get("overhang_m"),
        "dormers_auto": len(auto.get("roof", {}).get("dormers", [])), "dormers_read": len(stored.get("roof", {}).get("dormers", [])),
        "chimneys_auto": len(auto.get("roof", {}).get("chimneys", [])), "chimneys_read": len(stored.get("roof", {}).get("chimneys", [])),
    }
    if row["wall_colour_auto"] and row["wall_colour_read"]:
        row.update({f"wall_{k}": v for k, v in colour_parts(row["wall_colour_auto"], row["wall_colour_read"]).items()})
    if row["roof_colour_auto"] and row["roof_colour_read"]:
        row.update({f"roof_{k}": v for k, v in colour_parts(row["roof_colour_auto"], row["roof_colour_read"]).items()})
    return row


# One line per attribute of the table: name, how a row agrees (None: the row has no reading of it), and the pair
# of columns whose commonest disagreements are listed.
def _same(a, b):
    return lambda r: None if r.get(b) is None else r.get(a) == r.get(b)


def _within(a, b, tolerance):
    return lambda r: None if r.get(a) is None or r.get(b) is None else abs(r[a] - r[b]) <= tolerance


def _group(style):
    return "wooden" if style in priors.WOODEN else "old_masonry" if style in priors.OLD_MASONRY else "modern"


BUILDING_ATTRIBUTES = [
    ("style type", _same("style_auto", "style_read"), ("style_auto", "style_read")),
    ("style group (wooden / old masonry / modern)",
     lambda r: None if not r.get("style_read") else _group(r["style_auto"]) == _group(r["style_read"]), None),
    ("wall material family", _same("wall_family_auto", "wall_family_read"), ("wall_family_auto", "wall_family_read")),
    (f"wall colour within dE {COLOUR_CLOSE:.0f}",
     lambda r: None if r.get("wall_dE") is None else r["wall_dE"] <= COLOUR_CLOSE, None),
    ("wall colour class (white, cream, yellow, red, ...)", _same("wall_class_auto", "wall_class_read"),
     ("wall_class_auto", "wall_class_read")),
    ("storeys on the street wall, exact", _same("storeys_auto", "storeys_read"), ("storeys_auto", "storeys_read")),
    ("register storeys = read storeys", _same("register_storeys", "storeys_read"), ("register_storeys", "storeys_read")),
    ("plinth material family", _same("plinth_family_auto", "plinth_family_read"), ("plinth_family_auto", "plinth_family_read")),
    ("plinth height within 0.25 m", _within("plinth_m_auto", "plinth_m_read", 0.25), None),
    ("ground storey (plain / shopfront / other material)", _same("ground_auto", "ground_read"), ("ground_auto", "ground_read")),
    ("bays on the street wall within 1", _within("bays_auto", "bays_read", 1), None),
    ("window width within 0.25 m", _within("window_w_auto", "window_w_read", 0.25), None),
    ("window height within 0.25 m", _within("window_h_auto", "window_h_read", 0.25), None),
    ("window light pattern", _same("lights_auto", "lights_read"), ("lights_auto", "lights_read")),
    ("window surround kind", _same("surround_auto", "surround_read"), ("surround_auto", "surround_read")),
    ("balconies (has / has not)", lambda r: (r["balconies_read"] > 0) == (r["balconies_auto"] > 0), None),
    ("roof form class (shell against the reader's hint)", _same("roof_form_shell", "roof_form_read_class"),
     ("roof_form_shell", "roof_form_read")),
    ("roofing family", _same("roof_family_auto", "roof_family_read"), ("roof_family_auto", "roof_family_read")),
    (f"roof colour within dE {COLOUR_CLOSE:.0f}",
     lambda r: None if r.get("roof_dE") is None else r["roof_dE"] <= COLOUR_CLOSE, None),
    ("roof colour class", _same("roof_class_auto", "roof_class_read"), ("roof_class_auto", "roof_class_read")),
    ("dormers (has / has not)", lambda r: (r["dormers_read"] > 0) == (r["dormers_auto"] > 0), None),
    ("chimneys (has / has not)", lambda r: (r["chimneys_read"] > 0) == (r["chimneys_auto"] > 0), None),
]
FENCE_ATTRIBUTES = [
    ("a fence stands there", _same("present_auto", "present_read"), ("family_auto", "family_read")),
    ("family", lambda r: None if r["family_read"] == "none" else r["family_auto"] == r["family_read"],
     ("family_auto", "family_read")),
    ("height within 0.25 m", _within("height_auto", "height_read", 0.25), None),
    (f"colour within dE {COLOUR_CLOSE:.0f}", lambda r: None if r.get("dE") is None else r["dE"] <= COLOUR_CLOSE, None),
]


def rate(rows: list, agrees) -> tuple[int, int]:
    verdicts = [v for v in map(agrees, rows) if v is not None]
    return sum(verdicts), len(verdicts)


def confusions(rows: list, agrees, pair, limit: int = 4, words=("drawn", "read")) -> str:
    if not pair:
        return ""
    wrong = collections.Counter((r.get(pair[0]), r.get(pair[1])) for r in rows if agrees(r) is False)
    return "; ".join(f"{a} {words[0]}, {b} {words[1]} ×{n}" for (a, b), n in wrong.most_common(limit))


def table(rows: list, attributes: list, split: str | None, minimum: int = 4, words=("drawn", "read")) -> list[str]:
    groups = [("all", rows)]
    if split:
        by = collections.defaultdict(list)
        for row in rows:
            by[str(row.get(split))].append(row)
        groups = sorted(((k, v) for k, v in by.items() if len(v) >= minimum), key=lambda kv: -len(kv[1]))
    head = "| attribute | " + " | ".join(f"{name} ({len(group)})" for name, group in groups) + " |"
    if not split:
        head = "| attribute | agree | of | rate | commonest disagreements |"
    lines = [head, "|" + " --- |" * (head.count("|") - 1)]
    for name, agrees, pair in attributes:
        if split:
            cells = []
            for _, group in groups:
                good, total = rate(group, agrees)
                cells.append(f"{good}/{total}" if total else "-")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        else:
            good, total = rate(rows, agrees)
            share = f"{100 * good / total:.0f} %" if total else "-"
            lines.append(f"| {name} | {good} | {total} | {share} | {confusions(rows, agrees, pair, words=words)} |")
    return lines


def spread(rows: list, column: str) -> str:
    values = sorted(r[column] for r in rows if r.get(column) is not None)
    if not values:
        return "no values"
    return (f"median {statistics.median(values):.1f}, mean {statistics.fmean(values):.1f}, "
            f"upper quartile {values[int(len(values) * 0.75)]:.1f}, n {len(values)}")


def median_colour(colours: list[str]) -> str:
    rgb = np.median([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in colours], axis=0)
    return "#" + "".join(f"{int(v):02x}" for v in rgb)


def palette_floor(rows: list, by: str, column: str) -> str:
    """How close one colour per group could come: each read colour against the median read colour of its group."""
    groups = collections.defaultdict(list)
    for row in rows:
        if row.get(column):
            groups[str(row.get(by))].append(row[column])
    return spread([{"d": delta_e(median_colour(group), colour)} for group in groups.values() for colour in group], "d")


def classes(rows: list, column: str) -> str:
    return ", ".join(f"{name} ×{n}" for name, n in collections.Counter(r.get(column) for r in rows).most_common())


def read_wall_colour(stored: dict) -> str | None:
    """The wall colour a reading gave the wall the street sees most of."""
    frames = [w for w in stored.get("walls", []) if "frame" in w]
    if not frames:
        return None
    by_id = {w.get("id"): w for w in stored.get("walls", [])}
    wall = max(frames, key=lambda w: math.dist(w["frame"]["p0"], w["frame"]["p1"]) * w.get("observed", {}).get("visible_share", 0.5))
    for _ in range(8):
        if "material" in wall or wall.get("like") not in by_id:
            break
        wall = by_id[wall["like"]]
    return material(stored, wall.get("material", stored.get("wall_defaults", {}).get("material"))).get("colour")


def read_buildings(store: Path) -> dict:
    """Stored building descriptions that hold a vision reading of panoramas."""
    found = {}
    for path in sorted((store / "building").glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("tier") != "auto" and any(p.get("source") == "panorama" for p in doc.get("provenance", [])):
            found[path.stem] = doc
    return found


def fence_rows(store: Path, sources) -> list:
    from turku3d.world.kinds.fence import Fence

    rows, kind = [], Fence()
    for path in sorted((store / "fence").glob("*.json")):
        stored = json.loads(path.read_text())
        seen = stored.get("seen")
        if not seen:
            continue
        e, n = seen["panorama"]["position"][:2]
        stub = next((s for s in kind.discover(Area(float(e), float(n), 60.0), sources) if s.id == stored["id"]), None)
        if stub is None:
            continue
        facts = stub.facts
        read_family = "none" if seen.get("family") in (None, "none") else seen["family"]
        row = {"key": path.stem, "setting": facts.get("setting"), "register_type": facts.get("type"),
               "guessed": " ".join(facts.get("guessed", [])), "osm": json.dumps(facts.get("osm", {})) if facts.get("osm") else None,
               "family_auto": facts.get("family"), "family_read": read_family,
               "present_auto": True, "present_read": read_family != "none",
               "height_auto": facts.get("height"), "height_read": seen.get("height_m") if read_family != "none" else None,
               "lidar_p60": facts.get("measured", {}).get("lidar", {}).get("height_p60"),
               "colour_auto": facts.get("colour"), "colour_read": seen.get("colour") if read_family != "none" else None,
               "hedge_read": seen.get("hedge"), "confidence": seen.get("confidence"), "note": seen.get("note")}
        if row["colour_auto"] and row["colour_read"]:
            row.update(colour_parts(row["colour_auto"], row["colour_read"]))
        rows.append(row)
    return rows


def light_rows(store: Path, buildings: list) -> list:
    """Buildings a light reading of a judged photograph described (location files), beside the full reading and
    the automatic tier of the same building: one row per building, from the view that saw it best."""
    full = {row["key"]: row for row in buildings}
    best = {}
    for path in sorted((store / "location").glob("*.json")):
        for item in json.loads(path.read_text()).get("items", []):
            key = item.get("entity", "").removeprefix("building/")
            if item["kind"] == "building" and key in full and (key not in best or item["conf"] > best[key]["conf"]):
                best[key] = {**item, "location": path.stem}
    rows = []
    for key, item in best.items():
        row, wall, roof = full[key], item.get("wall", {}), item.get("roof", {})
        out = {"key": key, "location": item["location"], "view": item["view"], "conf": item["conf"], "share": item.get("share"),
               "wall_family_light": wall.get("family"), "wall_family_read": row["wall_family_read"],
               "wall_family_auto": row["wall_family_auto"],
               "wall_colour_light": wall.get("colour"), "wall_colour_read": row["wall_colour_read"],
               "storeys_light": item.get("storeys"), "storeys_read": row["storeys_read"], "storeys_auto": row["storeys_auto"],
               "roof_family_light": roof.get("family"), "roof_family_read": row["roof_family_read"],
               "roof_family_auto": row["roof_family_auto"], "wall_dE_auto": row.get("wall_dE")}
        if wall.get("colour") and row.get("wall_colour_read"):
            out["wall_dE_light"] = round(delta_e(wall["colour"], row["wall_colour_read"]), 1)
        if roof.get("colour") and row.get("roof_colour_read"):
            out["roof_dE_light"] = round(delta_e(roof["colour"], row["roof_colour_read"]), 1)
        rows.append(out)
    return rows


LIGHT_ATTRIBUTES = [
    ("wall family, light = full reading", _same("wall_family_light", "wall_family_read"), ("wall_family_light", "wall_family_read")),
    ("wall family, automatic = full reading", _same("wall_family_auto", "wall_family_read"), None),
    (f"wall colour within dE {COLOUR_CLOSE:.0f}, light to full",
     lambda r: None if r.get("wall_dE_light") is None else r["wall_dE_light"] <= COLOUR_CLOSE, None),
    (f"wall colour within dE {COLOUR_CLOSE:.0f}, automatic to full",
     lambda r: None if r.get("wall_dE_light") is None or r.get("wall_dE_auto") is None else r["wall_dE_auto"] <= COLOUR_CLOSE, None),
    ("storeys, light = full reading", lambda r: None if r.get("storeys_light") is None else r["storeys_light"] == r["storeys_read"],
     ("storeys_light", "storeys_read")),
    ("storeys, automatic = full reading", lambda r: None if r.get("storeys_light") is None else r["storeys_auto"] == r["storeys_read"], None),
    ("roofing family, light = full reading", lambda r: None if r.get("roof_family_light") is None else r["roof_family_light"] == r["roof_family_read"],
     ("roof_family_light", "roof_family_read")),
    ("roofing family, automatic = full reading",
     lambda r: None if r.get("roof_family_light") is None else r["roof_family_auto"] == r["roof_family_read"], None),
]


def write_csv(path: Path, rows: list):
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        writer.writerows(rows)


def run(out: Path | None = None, store: Path | None = None, measured: dict | None = None) -> dict:
    """`measured`: {building key: measured block} given to the automatic tier of those buildings (`--measured
    FILE`), so a measurement of read buildings is scored as it would draw where nobody has read."""
    out = out or config.work_root() / "observe/against"
    store = store or data_root() / "descriptions"
    out.mkdir(parents=True, exist_ok=True)
    sources = Sources(log=lambda *_: None)
    buildings, missing = [], []
    for key, stored in read_buildings(store).items():
        row = building_row(key, stored, sources, (measured or {}).get(key))
        if row and measured is not None:
            row["measured_wall"] = ((measured.get(key) or {}).get("wall") or {}).get("colour")
        (buildings if row else missing).append(row or key)
    fences = fence_rows(store, sources)
    light = light_rows(store, buildings)
    write_csv(out / "light.csv", light)
    write_csv(out / "buildings.csv", buildings)
    write_csv(out / "fences.csv", fences)
    summary = {
        "buildings": len(buildings), "fences": len(fences), "not_found": missing,
        **({"with_measured_wall": sum(bool(r.get("measured_wall")) for r in buildings)} if measured is not None else {}),
        "building_rates": {name: rate(buildings, agrees) for name, agrees, _ in BUILDING_ATTRIBUTES},
        "fence_rates": {name: rate(fences, agrees) for name, agrees, _ in FENCE_ATTRIBUTES},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    text = [f"# Automatic tier against readings ({len(buildings)} buildings, {len(fences)} fences)", "",
            "What `describe.automatic` and the priors draw today against what a vision reading stored, on the wall "
            "the street sees most of. Made by `python -m turku3d.observe against`; rows in buildings.csv and fences.csv.", "",
            "## Buildings, all", "", *table(buildings, BUILDING_ATTRIBUTES, None), "",
            f"Wall colour, CIEDE2000 automatic to read: {spread(buildings, 'wall_dE')}. "
            f"Read minus automatic lightness: {spread(buildings, 'wall_dL')}; chroma: {spread(buildings, 'wall_dC')}.", "",
            f"What one colour per group could reach (each read wall colour against the median read colour of its "
            f"group): by read family {palette_floor(buildings, 'wall_family_read', 'wall_colour_read')}; by register "
            f"facade material {palette_floor(buildings, 'register_material', 'wall_colour_read')}. Below that, a "
            f"building's colour has to be measured on the building.", "",
            f"Roof colour: {spread(buildings, 'roof_dE')}; read minus automatic lightness: {spread(buildings, 'roof_dL')}. "
            f"Roofs the reader was sure of (confidence 0.7 or more): "
            f"{spread([r for r in buildings if (r.get('roof_conf') or 0) >= 0.7], 'roof_dE')}; lightness "
            f"{spread([r for r in buildings if (r.get('roof_conf') or 0) >= 0.7], 'roof_dL')}. "
            f"Buildings without an orthophoto roof colour: {sum(not r['roof_register_colour'] for r in buildings)}.", "",
            f"Storeys read minus automatic: "
            f"{dict(sorted(collections.Counter(r['storeys_read'] - r['storeys_auto'] for r in buildings).items()))}. "
            f"Bays read minus automatic: {spread([{'d': r['bays_read'] - r['bays_auto']} for r in buildings], 'd')}. "
            f"Bay pitch read: {spread(buildings, 'pitch_m_read')}; automatic: {spread(buildings, 'pitch_m_auto')}.", ""]
    for title, column in (("the automatic style", "style_auto"), ("era of the register year", "era"),
                          ("register use code", "use"), ("register facade material", "register_material")):
        text += [f"## Buildings by {title} (agree/of; groups of four or more)", "", *table(buildings, BUILDING_ATTRIBUTES, column), ""]
    text += ["## What was read, for the rules", "",
             "| read wall family | n | read colour classes | median read colour | automatic families drawn there |",
             "| --- | --- | --- | --- | --- |"]
    by_family = collections.defaultdict(list)
    for row in buildings:
        by_family[row["wall_family_read"]].append(row)
    for family, group in sorted(by_family.items(), key=lambda kv: -len(kv[1])):
        colours = [r["wall_colour_read"] for r in group if r.get("wall_colour_read")]
        drawn = collections.Counter(r["wall_family_auto"] for r in group).most_common()
        text.append(f"| {family} | {len(group)} | {classes(group, 'wall_class_read')} | {median_colour(colours) if colours else '-'} | "
                    + ", ".join(f"{a} ×{n}" for a, n in drawn) + " |")
    text += ["", "| register facade material | n | read wall families | read colour classes |", "| --- | --- | --- | --- |"]
    by_material = collections.defaultdict(list)
    for row in buildings:
        by_material[str(row["register_material"])].append(row)
    for name, group in sorted(by_material.items(), key=lambda kv: -len(kv[1])):
        read = collections.Counter(r["wall_family_read"] for r in group).most_common()
        text.append(f"| {name} | {len(group)} | " + ", ".join(f"{a} ×{n}" for a, n in read)
                    + f" | {classes(group, 'wall_class_read')} |")
    text += ["", "| read roofing family | n | read colour classes | automatic colour classes |", "| --- | --- | --- | --- |"]
    by_roofing = collections.defaultdict(list)
    for row in buildings:
        by_roofing[str(row["roof_family_read"])].append(row)
    for name, group in sorted(by_roofing.items(), key=lambda kv: -len(kv[1])):
        text.append(f"| {name} | {len(group)} | {classes(group, 'roof_class_read')} | {classes(group, 'roof_class_auto')} |")
    text += ["", "| automatic style | n | shopfront ground storey | balconies | dormers | chimneys | read roofing |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    by_style = collections.defaultdict(list)
    for row in buildings:
        by_style[row["style_auto"]].append(row)
    for name, group in sorted(by_style.items(), key=lambda kv: -len(kv[1])):
        roofing = collections.Counter(r["roof_family_read"] for r in group).most_common(3)
        text.append(f"| {name} | {len(group)} | {sum(r['ground_read'] == 'shopfront' for r in group)} | "
                    f"{sum(r['balconies_read'] > 0 for r in group)} | {sum(r['dormers_read'] > 0 for r in group)} | "
                    f"{sum(r['chimneys_read'] > 0 for r in group)} | " + ", ".join(f"{a} ×{n}" for a, n in roofing) + " |")
    text += ["", "## Fences", "", *table(fences, FENCE_ATTRIBUTES, None), "",
             f"Height read minus automatic: {spread([{'d': r['height_read'] - r['height_auto']} for r in fences if r['height_read'] is not None and r['height_auto'] is not None], 'd')}. "
             f"Colour: {spread(fences, 'dE')}.", "",
             "| fence | setting | drawn | read | height drawn / read | colour drawn / read | guessed |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in fences:
        text.append(f"| {r['key']} | {r['setting']} | {r['family_auto']} | {r['family_read']}{' + hedge' if r['hedge_read'] else ''} | "
                    f"{r['height_auto']} / {r['height_read']} | {r['colour_auto']} / {r['colour_read']} | {r['guessed']} |")
    sure = [r for r in light if r["conf"] >= 0.5]
    text += ["", f"## Light readings of judged photographs against full readings ({len(light)} buildings; rows in light.csv)", "",
             "One cheap call per photograph (observe light) against the full reading of the same building, with the "
             "automatic tier on the same buildings beside it.", "",
             "### All", "", *table(light, LIGHT_ATTRIBUTES, None, words=("light", "full")), "",
             f"### Light confidence 0.5 or more ({len(sure)})", "", *table(sure, LIGHT_ATTRIBUTES, None, words=("light", "full")), "",
             f"Wall colour, light to full: {spread(light, 'wall_dE_light')}; at confidence 0.5 or more: {spread(sure, 'wall_dE_light')}. "
             f"Roof colour, light to full: {spread(light, 'roof_dE_light')}."]
    if missing:
        text += ["", f"Not found in the sources at their first wall frame: {', '.join(missing)}."]
    (out / "report.md").write_text("\n".join(text) + "\n")
    return {"out": str(out), **{k: v for k, v in summary.items() if k in ("buildings", "fences", "not_found")}}
