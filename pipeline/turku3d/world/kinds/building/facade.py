"""One facade plane of the shell built from its wall specification: wall faces with real holes, the openings'
assemblies, plinth, bands, vertical trim and the parts fixed to the wall.

Wall coordinates here are the facade's own: u from its left end, v from the lowest point of the shell wall. The
description measures v from the wall base (`datum`): the ground at the wall's lowest point.
"""

import zlib
from dataclasses import dataclass, field

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.geometry import box as rectangle
from shapely.ops import unary_union

from . import mesh, openings, priors
from .description import expand_storeys
from .materials import Palette, shade

FLOOR_KINDS = ("door", "gateway", "garage", "shopfront")


@dataclass
class Placed:
    name: str
    spec: dict
    u0: float
    v0: float
    width: float
    height: float
    storey: int | None = None
    shape: object = None


@dataclass
class Layout:
    datum: float
    eaves: float
    floors: list = field(default_factory=list)  # (v of the floor, storey height, storey spec)
    bays: list = field(default_factory=list)
    placed: list = field(default_factory=list)


def eaves_height(outline, width: float) -> float:
    """The height of the wall's top where it is lowest for a while: the eaves line under a gable."""
    tops = []
    for x in np.linspace(width * 0.04, width * 0.96, 13):
        cut = outline.intersection(LineString([(x, -1.0), (x, 1e4)]))
        if not cut.is_empty:
            tops.append(cut.bounds[3])
    return float(np.percentile(tops, 20)) if tops else float(outline.bounds[3])


def ground_v(facade, datum: float, u: float) -> float:
    """The facade v of the ground at u: the shell's terrain line, or the wall base where it has none."""
    return facade.ground_at(u) if len(facade.ground) else datum


def u_mapping(facade, wall: dict):
    """Description u to facade u: the described frame may start elsewhere than the shell wall and differ in length."""
    frame = wall.get("frame")
    if not frame:
        return lambda u: u
    p0, p1 = np.asarray(frame["p0"], float), np.asarray(frame["p1"], float)
    start = float((p0 - facade.p0) @ facade.u_dir[:2])
    length = float(np.linalg.norm(p1 - p0))
    if (
        length > 1e-6
        and abs(start) < 0.03 * facade.width
        and abs(length - facade.width) < 0.03 * facade.width
    ):
        scale = facade.width / length
        return lambda u: u * scale
    return lambda u: start + u


def layout(facade, wall: dict, doc: dict, report: list) -> Layout:
    """Where every storey, bay and opening of a wall specification falls on a facade."""
    if "frame" in wall and "base_h" in wall["frame"]:
        datum = wall["frame"]["base_h"] - float(facade.origin[2])
    elif len(facade.ground):
        datum = float(facade.ground[:, 1].min())
    else:
        datum = 0.0
    eaves = eaves_height(facade.outline, facade.width)
    out = Layout(datum, eaves)
    to_u = u_mapping(facade, wall)
    storeys = expand_storeys(wall.get("storeys", []))
    ground_floor = wall.get("ground_floor_m", wall.get("plinth", {}).get("height_m", 0.0))
    heights = [s.get("height_m", 3.0) for s in storeys]
    available = eaves - datum - ground_floor
    # The shell wall's height is measured and a description's storey heights are read from a photo, so the storeys
    # are scaled together onto the wall: stretched a little, compressed more (a row of windows is worth more than
    # a storey height). A larger misfit means the wall is not the one that was described, and is reported.
    total = sum(heights)
    if heights and available > 2.0 and abs(total - available) > 0.03:
        misfit = (total - available) / available
        if -0.2 <= misfit <= 0.3 if "frame" in wall else abs(misfit) < 0.4:
            heights = [h * available / total for h in heights]
        elif abs(total - available) > 0.3 and "frame" in wall:
            report.append(
                f"facade {facade.index}: storeys add up to {total + ground_floor:.1f} m, the shell wall is {eaves - datum:.1f} m"
            )
    v = datum + ground_floor
    for storey, height in zip(storeys, heights, strict=True):
        out.floors.append((v, height, storey))
        v += height

    bays = wall.get("bays", [])
    if isinstance(bays, dict):
        pitch = bays.get("pitch_m")
        count = bays.get("count")
        # An automatic grid keeps its end windows clear of the corner boards and quoins.
        margin = 1.6 if "frame" in wall else 2.4
        fits = max(0, int((facade.width - margin) / pitch) + 1) if pitch else 0
        count = fits if count is None else min(count, fits) if pitch else count
        if facade.width < 2.2:
            count = 0
        if count and pitch is None:
            pitch = facade.width / count
        first = bays.get("first_m")
        if first is None:
            centres = [facade.width / 2 + (k - (count - 1) / 2) * pitch for k in range(count)]
        else:
            centres = [to_u(first + k * pitch) for k in range(count)]
    else:
        centres = [to_u(b["u_m"]) for b in bays]
    out.bays = centres

    def add(name, u_centre, v_bottom, storey_index, floor_v, w=None, h=None):
        spec = priors.resolve_opening(doc, name)
        width = w or spec["w_m"]
        height = h or spec["h_m"]
        count = spec.get("multiple", {}).get("count", 1)
        gap = spec.get("multiple", {}).get("gap_m", 0.12)
        span = count * width + (count - 1) * gap
        for k in range(count):
            u0 = u_centre - span / 2 + k * (width + gap)
            bottom, placed_spec = v_bottom, spec
            if spec["kind"] in FLOOR_KINDS:
                # Doors, gateways and shopfronts stand on the ground at their place, whatever height was read.
                ground = ground_v(facade, datum, u0 + width / 2)
                level = spec["kind"] in ("gateway", "garage")
                steps = spec.get("leaf", {}).get("steps", 0)
                stood = ground + steps * openings.STEP_RISE + (0.0 if level else 0.03)
                if bottom is None or level:
                    bottom = stood
                else:
                    bottom = max(bottom, ground + 0.03)
                    if spec["kind"] == "door":
                        steps = int(round((bottom - ground - 0.03) / openings.STEP_RISE))
                        placed_spec = priors.merged(spec, {"leaf": {"steps": max(0, steps)}})
                if spec["kind"] == "shopfront" and floor_v is not None:
                    bottom = max(ground + 0.05, min(bottom, floor_v))
            out.placed.append(Placed(name, placed_spec, u0, bottom, width, height, storey_index))

    for index, (floor_v, _height, storey) in enumerate(out.floors):
        sill = floor_v + storey.get("sill_m", 0.9)
        # What was placed by measurement comes first, so it keeps its place where the grid would put a window.
        for opening in storey.get("openings", []):
            kind = priors.resolve_opening(doc, opening["type"])["kind"]
            bottom = (
                datum + opening["v_m"]
                if "v_m" in opening
                else None
                if kind in FLOOR_KINDS
                else sill
            )
            add(
                opening["type"],
                to_u(opening["u_m"]),
                bottom,
                index,
                floor_v,
                opening.get("w_m"),
                opening.get("h_m"),
            )
        row = storey.get("row")
        default = storey.get("window")
        if row is not None or default is not None:
            for k, centre in enumerate(centres):
                name = (
                    row[k] if row is not None and k < len(row) else default if row is None else None
                )
                if name is None:
                    continue
                kind = priors.resolve_opening(doc, name)["kind"]
                add(name, centre, None if kind in FLOOR_KINDS else sill, index, floor_v)
    for opening in wall.get("openings", []):
        add(
            opening["type"],
            to_u(opening["u_m"]),
            datum + opening.get("v_m", 0.0),
            None,
            None,
            opening.get("w_m"),
            opening.get("h_m"),
        )
    return out


def anchor(layout_: Layout, at: dict, doc: dict) -> float:
    """The facade v of a storey edge."""
    if not layout_.floors:
        return layout_.datum
    index = max(-len(layout_.floors), min(len(layout_.floors) - 1, at["storey"]))
    floor_v, height, storey = layout_.floors[index]
    edge = at["edge"]
    v = floor_v
    if edge == "top":
        v = floor_v + height
    elif edge in ("sill", "head"):
        v = floor_v + storey.get("sill_m", 0.9)
        if edge == "head":
            name = storey.get("window") or next((c for c in storey.get("row", []) if c), None)
            v += priors.resolve_opening(doc, name)["h_m"] if name else 1.6
    return v + at.get("offset_m", 0.0)


def band_profile(kind: str, v: float, height: float, projection: float):
    p, h = projection, height
    if kind == "stepped":
        return [
            (0, v),
            (p * 0.5, v),
            (p * 0.5, v + h * 0.5),
            (p, v + h * 0.5),
            (p, v + h),
            (0, v + h),
        ]
    if kind == "cove":
        curve = [
            (p * (0.25 + 0.75 * (1 - np.cos(t))), v + h * 0.85 * np.sin(t))
            for t in np.linspace(0, np.pi / 2, 6)
        ]
        return [(0, v), (p * 0.25, v)] + curve[1:] + [(p, v + h), (0, v + h)]
    if kind == "ogee":
        curve = [
            (p * (0.2 + 0.8 * (0.5 - 0.5 * np.cos(t))), v + h * 0.85 * t / np.pi)
            for t in np.linspace(0, np.pi, 8)
        ]
        return [(0, v), (p * 0.2, v)] + curve[1:] + [(p, v + h), (0, v + h)]
    if kind == "meander":
        return [
            (0, v),
            (p * 0.1, v),
            (p * 0.1, v + h * 0.6),
            (p * 0.55, v + h * 0.7),
            (p, v + h * 0.82),
            (p, v + h),
            (0, v + h),
        ]
    if kind in ("dentil", "bracketed"):
        return [
            (0, v),
            (p * 0.35, v),
            (p * 0.35, v + h * 0.5),
            (p * 0.8, v + h * 0.62),
            (p, v + h * 0.7),
            (p, v + h),
            (0, v + h),
        ]
    return [(0, v), (p, v), (p, v + h), (0, v + h)]


def meander(u0: float, u1: float, v0: float, v1: float):
    """A Greek key as one shape: whole key units centred between u0 and u1, as tall as v0..v1."""
    t = (v1 - v0) / 6
    pitch = 7 * t
    count = int((u1 - u0) / pitch)
    if count < 1 or t <= 0:
        return Polygon()
    start = (u0 + u1) / 2 - count * pitch / 2
    cells = [(0, 0, 7, 1), (0, 0, 1, 6), (0, 5, 5, 6), (4, 2, 5, 6), (2, 2, 5, 3), (2, 2, 3, 4)]
    parts = [
        rectangle(start + k * pitch + a * t, v0 + b * t, start + k * pitch + c * t, v0 + d * t)
        for k in range(count)
        for a, b, c, d in cells
    ]
    return unary_union(parts)


def ornament(kind: str, u: float, v: float, w: float, h: float):
    """A flat relief motif in the rectangle whose bottom centre is (u, v)."""

    def disc(cx, cy, r, sides=12):
        angles = np.linspace(0, 2 * np.pi, sides + 1)[:-1]
        return Polygon([(cx + r * np.cos(a), cy + r * np.sin(a)) for a in angles])

    if kind == "swag":
        # A garland hung between two rosettes, with a short drop under each.
        r = min(h * 0.22, w * 0.1)
        s = np.linspace(-1, 1, 15)
        x = u + s * (w / 2 - r)
        top = v + h - r - (h - 2 * r) * 0.45 * (1 - s**2)
        low = v + h - r - (h - 2 * r) * 0.95 * (1 - s**2) - r * 0.25
        garland = Polygon(
            list(zip(x, top, strict=True)) + list(zip(x[::-1], low[::-1], strict=True))
        )
        drops = [
            Polygon([(cx - r * 0.5, v + h - r), (cx + r * 0.5, v + h - r), (cx, v + h * 0.2)])
            for cx in (x[0], x[-1])
        ]
        return unary_union(
            [garland.buffer(0), disc(x[0], v + h - r, r), disc(x[-1], v + h - r, r)] + drops
        )
    if kind == "crest":
        # A palmette between two scrolls on a base bar.
        r = min(w * 0.16, h * 0.3)
        leaf = affinity.scale(disc(u, v + h * 0.6, r, 14), 0.7, 1.3)
        scrolls = [disc(u + side * w * 0.33, v + h * 0.3, r * 0.75) for side in (-1, 1)]
        arms = [
            Polygon(
                [
                    (u, v + h * 0.12),
                    (u + side * w * 0.5, v + h * 0.12),
                    (u + side * w * 0.3, v + h * 0.42),
                ]
            )
            for side in (-1, 1)
        ]
        bar = rectangle(u - w / 2, v, u + w / 2, v + h * 0.12)
        return unary_union([leaf, bar] + scrolls + [a.buffer(0) for a in arms])
    if kind in ("medallion", "rosette"):
        r = min(w, h) / 2
        return (
            disc(u, v + h / 2, r, 16)
            .difference(disc(u, v + h / 2, r * 0.55, 12))
            .union(disc(u, v + h / 2, r * 0.3, 10))
        )
    # A plaque: a framed panel.
    return rectangle(u - w / 2, v, u + w / 2, v + h).difference(
        rectangle(u - w / 2 + 0.06, v + 0.06, u + w / 2 - 0.06, v + h - 0.06)
    )


def free_spans(u0: float, u1: float, v0: float, v1: float, placed: list, margin: float = 0.0):
    """The parts of [u0, u1] not crossed by an opening between heights v0 and v1."""
    cuts = sorted(
        (p.u0 - margin, p.u0 + p.width + margin)
        for p in placed
        if p.v0 < v1 - 0.02 and p.v0 + p.height > v0 + 0.02
    )
    spans, at = [], u0
    for a, b in cuts:
        if a > at + 0.02:
            spans.append((at, min(a, u1)))
        at = max(at, b)
        if at >= u1:
            break
    if at < u1 - 0.02:
        spans.append((at, u1))
    return [(a, b) for a, b in spans if b - a > 0.03]


def build(
    soup: mesh.Soup,
    facade,
    wall: dict,
    doc: dict,
    palette: Palette,
    report: list,
    blocked=None,
    templates=None,
):
    """Add one facade's geometry to the soup."""
    templates = {} if templates is None else templates
    trim = priors.trim_colour(doc)
    plan = layout(facade, wall, doc, report)
    width, datum = facade.width, plan.datum
    outline = facade.outline
    wall_record = palette.record(wall.get("material"))
    wall_key = palette.key(wall.get("material"))

    # Openings that fit: inside the wall, clear of a neighbour's party wall and of each other.
    inside = outline.buffer(-0.12)
    kept, holes, misfits = [], [], []
    for p in plan.placed:
        shape = affinity.translate(openings.outline(p.spec, p.width, p.height), p.u0, p.v0)
        # What stands on the ground reaches the wall's lower edge, so only its upper part is tested; an opening
        # read a little past a wall end is moved in rather than lost.
        floor = p.spec["kind"] in FLOOR_KINDS
        lift = min(0.3, p.height / 3) if floor else 0.0
        tested = shape.intersection(rectangle(-1e3, p.v0 + lift, 1e3, 1e3)) if floor else shape
        if not inside.contains(tested):
            shift = max(0.15 - p.u0, 0.0) + min(width - 0.15 - (p.u0 + p.width), 0.0)
            moved = affinity.translate(tested, shift, 0.0)
            if shift and abs(shift) <= 0.6 * p.width and inside.contains(moved):
                p.u0 += shift
                shape = affinity.translate(shape, shift, 0.0)
            else:
                misfits.append(f"{p.name} at u {p.u0 + p.width / 2:.1f} v {p.v0 - datum:.1f}")
                continue
        if blocked is not None and blocked.intersects(shape):
            continue
        if any(shape.intersects(h) for h in holes):
            misfits.append(f"{p.name} at u {p.u0 + p.width / 2:.1f} (overlaps another opening)")
            continue
        p.shape = shape
        kept.append(p)
        holes.append(shape)
    if misfits and "frame" in wall:
        more = f" and {len(misfits) - 3} more" if len(misfits) > 3 else ""
        report.append(f"facade {facade.index}: left out {', '.join(misfits[:3])}{more}")
    cut = unary_union(holes) if holes else None

    # Material regions, later ones over earlier ones.
    plinth = wall.get("plinth", {})
    plinth_top = datum + plinth.get("height_m", 0.0)
    regions = [(outline, wall_key, wall_record)]
    for floor_v, height, storey in plan.floors:
        if "material" in storey:
            regions.append(
                (
                    rectangle(0.0, floor_v, width, floor_v + height),
                    palette.key(storey["material"], **_relief(storey)),
                    None,
                )
            )
    for zone in wall.get("zones", []):
        lo = anchor(plan, zone["from"], doc) if "from" in zone else datum + zone.get("from_m", 0.0)
        hi = anchor(plan, zone["to"], doc) if "to" in zone else datum + zone.get("to_m", 1e3)
        to_u = u_mapping(facade, wall)
        a = to_u(zone["u_from_m"]) if "u_from_m" in zone else 0.0
        b = to_u(zone["u_to_m"]) if "u_to_m" in zone else width
        if hi > lo and b > a:
            regions.append(
                (rectangle(a, lo, b, hi), palette.key(zone["material"], **_relief(zone)), None)
            )
    plinth_region = None
    if plinth_top > 0.02:
        plinth_key = palette.key(
            plinth.get("material"), fallback={"family": "concrete", "colour": "#77736d"}
        )
        plinth_region = outline.intersection(rectangle(-1.0, -1.0, width + 1.0, plinth_top))
        regions.append((plinth_region, plinth_key, None))
    resolved = []
    for k, (shape, key, _record) in enumerate(regions):
        shape = shape.intersection(outline) if k else shape
        for later, _, _ in regions[k + 1 :]:
            shape = shape.difference(later)
        if cut is not None:
            shape = shape.difference(cut)
        resolved.append((shape, key))
    projection = plinth.get("projection_m", 0.0)
    for k, (shape, key) in enumerate(resolved):
        if shape.is_empty:
            continue
        local = mesh.Soup()
        is_plinth = plinth_region is not None and k == len(resolved) - 1
        if is_plinth and projection > 0.005:
            mesh.prism(local, "plinth", key, shape, 0.0, projection)
        else:
            mesh.face(local, "plinth" if is_plinth else "walls", key, shape)
        soup.place(local, facade)

    # Opening assemblies, one template per type, size and surrounding wall material.
    rng = np.random.default_rng(zlib.crc32(f"{doc.get('id', '')}/{facade.index}".encode()))
    for p in kept:
        centre = p.shape.representative_point()
        around = next(
            (key for shape, key in reversed(resolved) if shape.buffer(0.3).contains(centre)),
            wall_key,
        )
        steps = p.spec.get("leaf", {}).get("steps", 0)
        template_key = (p.name, round(p.width, 3), round(p.height, 3), around, steps)
        if template_key not in templates:
            templates[template_key] = openings.build(
                p.spec, p.width, p.height, around, palette, trim
            )
        glass = p.spec.get("glass", {})
        curtain = rng.uniform(0.3, 1.0) if rng.random() < glass.get("curtains", 0.5) else 0.0
        blind = rng.uniform(0.15, 0.95) if rng.random() < glass.get("blinds", 0.15) else 0.0
        extra = np.array([curtain, blind, rng.random(), rng.random()])
        soup.place(templates[template_key], facade, p.u0, p.v0, extra=extra)

    _bands(soup, facade, wall, plan, kept, doc, palette, wall_record, trim)
    _verticals(soup, facade, wall, plan, kept, palette, wall_record, trim, plinth_top)
    _attached(soup, facade, wall, plan, doc, palette, wall_record, trim, templates)
    return plan


def _relief(part: dict) -> dict:
    relief = part.get("relief")
    if not relief or relief.get("kind", "none") == "none":
        return {}
    return {"relief": relief["kind"], "course_m": relief.get("course_m", 0.4)}


def _bands(soup, facade, wall, plan, kept, doc, palette, wall_record, trim):
    to_u = u_mapping(facade, wall)
    for band in wall.get("bands", []):
        v = anchor(plan, band["at"], doc) if "at" in band else plan.datum + band.get("v_m", 0.0)
        kind = band["kind"]
        height = band.get(
            "height_m", {"cornice": 0.3, "eaves_cornice": 0.4, "frieze": 0.4}.get(kind, 0.14)
        )
        projection = band.get(
            "projection_m", {"cornice": 0.15, "eaves_cornice": 0.3, "frieze": 0.02}.get(kind, 0.04)
        )
        key = (
            palette.key(band["material"])
            if "material" in band
            else palette.paint(band.get("colour", trim), wall_record)
        )
        a = to_u(band["u_from_m"]) if "u_from_m" in band else 0.0
        b = to_u(band["u_to_m"]) if "u_to_m" in band else facade.width
        profile_kind = band.get("profile", "flat")
        local = mesh.Soup()
        for u0, u1 in free_spans(
            max(0.0, a), min(facade.width, b), v, v + height, kept, margin=0.0
        ):
            mesh.extrude_profile(
                local, "trim", key, u0, u1, band_profile(profile_kind, v, height, projection)
            )
            if profile_kind == "meander":
                # The key stands out of a frieze in shadow: a darker ground behind a raised fret.
                ground = palette.paint(shade(band.get("colour", trim), 0.62), wall_record)
                mesh.box(
                    local,
                    "trim",
                    ground,
                    u0,
                    v + height * 0.06,
                    u1,
                    v + height * 0.58,
                    projection * 0.1,
                    projection * 0.1 + 0.004,
                )
                mesh.prism(
                    local,
                    "trim",
                    key,
                    meander(u0 + 0.05, u1 - 0.05, v + height * 0.12, v + height * 0.52),
                    projection * 0.1,
                    projection * 0.1 + 0.035,
                )
            if profile_kind in ("dentil", "bracketed"):
                pitch, block = (0.26, 0.13) if profile_kind == "dentil" else (0.9, 0.16)
                count = int((u1 - u0 - block) / pitch) + 1
                start = (u0 + u1) / 2 - (count - 1) * pitch / 2
                low, high = (
                    (v + height * 0.32, v + height * 0.55)
                    if profile_kind == "dentil"
                    else (v - height * 0.1, v + height * 0.62)
                )
                for k in range(max(0, count)):
                    x = start + k * pitch
                    mesh.box(
                        local,
                        "trim",
                        key,
                        x - block / 2,
                        low,
                        x + block / 2,
                        high,
                        projection * 0.3,
                        projection * 0.72,
                    )
        soup.place(local, facade)


def _verticals(soup, facade, wall, plan, kept, palette, wall_record, trim, plinth_top):
    to_u = u_mapping(facade, wall)
    local = mesh.Soup()
    for part in wall.get("verticals", []):
        kind = part["kind"]
        part_width = part.get(
            "width_m",
            {
                "downpipe": 0.1,
                "corner_board": 0.2,
                "quoins": 0.45,
                "pilaster": 0.5,
                "pier": 0.6,
            }.get(kind, 0.3),
        )
        projection = part.get(
            "projection_m",
            {"pilaster": 0.1, "pier": 0.12, "lesene": 0.05, "quoins": 0.04}.get(kind, 0.03),
        )
        where = part.get("where")
        if "u_m" in part:
            centres = [to_u(part["u_m"])]
        elif where == "left":
            centres = [part_width / 2 + (0.2 if kind == "downpipe" else 0.0)]
        elif where == "right":
            centres = [facade.width - part_width / 2 - (0.2 if kind == "downpipe" else 0.0)]
        elif where == "between_bays":
            centres = [(a + b) / 2 for a, b in zip(plan.bays[:-1], plan.bays[1:], strict=True)]
        else:
            centres = [part_width / 2, facade.width - part_width / 2]
        low = plan.datum + part["from_m"] if "from_m" in part else plinth_top
        high = plan.datum + part["to_m"] if "to_m" in part else plan.eaves
        key = (
            palette.key(part["material"])
            if "material" in part
            else palette.paint(part.get("colour", trim), wall_record)
        )
        for u in centres:
            if kind == "downpipe":
                pipe = palette.key({"family": "metal", "colour": part.get("colour", "#4a4a4a")})
                mesh.cylinder(
                    local,
                    "attached",
                    pipe,
                    u,
                    0.09,
                    ground_v(facade, plan.datum, u),
                    high,
                    part_width / 2,
                    sides=8,
                )
                continue
            if kind == "quoins":
                course = 0.32
                for k in range(int((high - low) / course)):
                    w = part_width if k % 2 == 0 else part_width * 0.62
                    x0 = u - part_width / 2 if u < facade.width / 2 else u + part_width / 2 - w
                    mesh.box(
                        local,
                        "trim",
                        key,
                        x0,
                        low + k * course,
                        x0 + w,
                        low + (k + 1) * course - 0.025,
                        0.0,
                        projection,
                    )
                continue
            for a, b in _vertical_spans(u - part_width / 2, u + part_width / 2, low, high, kept):
                mesh.box(
                    local,
                    "trim",
                    key,
                    u - part_width / 2,
                    a,
                    u + part_width / 2,
                    b,
                    0.0,
                    projection,
                )
            capital = part.get("capital", "plain" if kind == "pilaster" else "none")
            if capital != "none":
                mesh.box(
                    local,
                    "trim",
                    key,
                    u - part_width * 0.62,
                    high - 0.28,
                    u + part_width * 0.62,
                    high,
                    0.0,
                    projection * 1.5,
                )
                mesh.box(
                    local,
                    "trim",
                    key,
                    u - part_width * 0.58,
                    low,
                    u + part_width * 0.58,
                    low + 0.25,
                    0.0,
                    projection * 1.4,
                )
    soup.place(local, facade)


def _vertical_spans(u0, u1, v0, v1, placed):
    """The parts of a vertical strip not crossed by an opening."""
    cuts = sorted(
        (p.v0, p.v0 + p.height) for p in placed if p.u0 < u1 - 0.02 and p.u0 + p.width > u0 + 0.02
    )
    spans, at = [], v0
    for a, b in cuts:
        if a > at + 0.02:
            spans.append((at, min(a, v1)))
        at = max(at, b)
    if at < v1 - 0.02:
        spans.append((at, v1))
    return [(a, b) for a, b in spans if b - a > 0.05]


def _attached(soup, facade, wall, plan, doc, palette, wall_record, trim, templates):
    to_u = u_mapping(facade, wall)
    local = mesh.Soup()
    for part in wall.get("attached", []):
        kind = part["kind"]
        u = to_u(part.get("u_m", facade.width / 2))
        v = plan.datum + part.get("v_m", 0.0)
        w, h, depth = part.get("w_m", 1.5), part.get("h_m", 0.3), part.get("depth_m", 1.0)
        key = (
            palette.key(part["material"])
            if "material" in part
            else palette.paint(part.get("colour", trim), wall_record)
        )
        if kind == "downpipe":
            pipe = palette.key({"family": "metal", "colour": part.get("colour", "#4a4a4a")})
            mesh.cylinder(
                local,
                "attached",
                pipe,
                u,
                0.09,
                plan.datum,
                plan.datum + part["h_m"] if "h_m" in part else plan.eaves,
                0.05,
                sides=8,
            )
        elif kind == "canopy":
            variant = part.get("type", "slab")
            if variant == "entablature":
                frieze_top, cornice_top = v + h * 0.6, v + h * 0.84
                mesh.box(
                    local,
                    "attached",
                    key,
                    u - w / 2,
                    v,
                    u + w / 2,
                    frieze_top,
                    0.0,
                    depth,
                    back=True,
                )
                lip = 0.14
                mesh.extrude_profile(
                    local,
                    "attached",
                    key,
                    u - w / 2 - lip,
                    u + w / 2 + lip,
                    [
                        (0.0, frieze_top),
                        (depth + lip * 0.3, frieze_top),
                        (depth + lip, frieze_top + (cornice_top - frieze_top) * 0.7),
                        (depth + lip, cornice_top),
                        (0.0, cornice_top),
                    ],
                )
                roofing = palette.key(
                    part.get("roof_material"),
                    fallback={"family": "sheet_metal", "colour": "#4b4e4c", "profile": "flat_seam"},
                )
                mesh.extrude_profile(
                    local,
                    "attached",
                    roofing,
                    u - w / 2 - lip - 0.04,
                    u + w / 2 + lip + 0.04,
                    [
                        (0.0, cornice_top),
                        (depth + lip + 0.04, cornice_top),
                        (depth + lip + 0.04, cornice_top + 0.03),
                        (0.0, v + h),
                    ],
                )
                if "colour2" in part:
                    fret = palette.paint(part["colour2"], wall_record)
                    mesh.prism(
                        local,
                        "attached",
                        fret,
                        meander(u - w / 2 + 0.08, u + w / 2 - 0.08, v + h * 0.12, v + h * 0.5),
                        depth,
                        depth + 0.02,
                    )
            elif variant in ("hood", "pitched"):
                mesh.extrude_profile(
                    local,
                    "attached",
                    key,
                    u - w / 2,
                    u + w / 2,
                    [(0.0, v), (depth, v), (depth, v + 0.06), (0.0, v + h)],
                )
            elif variant == "glazed":
                glass = palette.key({"family": "glass", "interior": "frosted"})
                mesh.box(
                    local,
                    "attached",
                    glass,
                    u - w / 2,
                    v,
                    u + w / 2,
                    v + 0.03,
                    0.0,
                    depth,
                    back=True,
                )
                metal = palette.key({"family": "metal", "colour": part.get("colour", "#3a3a3a")})
                for x in (u - w / 2, u + w / 2 - 0.05):
                    mesh.box(
                        local, "attached", metal, x, v - 0.06, x + 0.05, v, 0.0, depth, back=True
                    )
            else:
                mesh.box(
                    local, "attached", key, u - w / 2, v, u + w / 2, v + h, 0.0, depth, back=True
                )
            supports = part.get("supports", {})
            support_kind = supports.get("kind", "none")
            if support_kind in ("columns", "posts"):
                count = supports.get("count", 2)
                radius = supports.get("diameter_m", 0.25) / 2
                support_key = (
                    palette.key(supports["material"])
                    if "material" in supports
                    else palette.key(
                        {
                            "family": "painted_wood" if support_kind == "posts" else "plaster",
                            "colour": supports.get("colour", trim),
                            "finish": "smooth",
                        }
                    )
                )
                xs = (
                    np.linspace(
                        u - w / 2 + radius + 0.06, u + w / 2 - radius - 0.06, max(2, min(count, 6))
                    )
                    if count >= 2
                    else [u]
                )
                for x in xs:
                    d = depth - radius - 0.06
                    foot = ground_v(facade, plan.datum, x) + supports.get("base_m", 0.0)
                    if support_kind == "posts":
                        mesh.box(
                            local,
                            "attached",
                            support_key,
                            x - radius,
                            foot,
                            x + radius,
                            v,
                            d - radius,
                            d + radius,
                            back=True,
                        )
                    else:
                        mesh.cylinder(
                            local,
                            "attached",
                            support_key,
                            x,
                            d,
                            foot + 0.3,
                            v - 0.16,
                            radius,
                            top_radius=radius * 0.86,
                        )
                        mesh.cylinder(
                            local,
                            "attached",
                            support_key,
                            x,
                            d,
                            foot,
                            foot + 0.3,
                            radius * 1.25,
                            sides=8,
                        )
                        mesh.cylinder(
                            local, "attached", support_key, x, d, v - 0.16, v, radius * 1.2, sides=8
                        )
        elif kind == "sign":
            sign = palette.flat(part.get("colour", "#2b3a4a"), roughness=0.5)
            if part.get("type") == "projecting":
                mesh.box(
                    local, "attached", sign, u - 0.04, v, u + 0.04, v + h, 0.1, 0.1 + w, back=True
                )
            else:
                mesh.box(local, "attached", sign, u - w / 2, v, u + w / 2, v + h, 0.0, 0.06)
        elif kind == "steps":
            stone = palette.key(
                part.get("material"),
                fallback={
                    "family": "granite",
                    "colour": "#9d968c",
                    "finish": "slab",
                    "scale_m": 2.0,
                },
            )
            count = max(1, round(h / openings.STEP_RISE))
            for k in range(count):
                mesh.box(
                    local,
                    "plinth",
                    stone,
                    u - w / 2,
                    v + h - (k + 1) * h / count,
                    u + w / 2,
                    v + h - k * h / count,
                    0.0,
                    depth * (k + 1) / count,
                )
        elif kind == "flagpole":
            metal = palette.key({"family": "metal", "colour": part.get("colour", "#d8d8d8")})
            if part.get("type") == "angled":
                # A pole in a wall socket, leaning out over the pavement.
                mesh.tube(local, "attached", metal, (u, v, 0.0), (u, v + h * 0.82, h * 0.57), 0.03)
            else:
                mesh.cylinder(local, "attached", metal, u, 0.25, v, v + h, 0.035, sides=6)
        elif kind == "ornament":
            mesh.prism(
                local, "trim", key, ornament(part.get("type", "plaque"), u, v, w, h), 0.0, 0.04
            )
        elif kind == "lamp":
            metal = palette.key({"family": "metal", "colour": part.get("colour", "#2a2a2a")})
            mesh.box(local, "attached", metal, u - 0.1, v, u + 0.1, v + 0.3, 0.05, 0.3, back=True)
        elif kind == "vent_grille":
            metal = palette.key({"family": "metal", "colour": part.get("colour", "#3c3c3b")})
            w, h = part.get("w_m", 0.16), part.get("h_m", 0.16)
            mesh.box(local, "attached", metal, u - w / 2, v, u + w / 2, v + h, 0.0, 0.015)
    _balconies(local, facade, wall, plan, palette, to_u)
    soup.place(local, facade)


RAILING_FAMILY = {
    "bars": "metal",
    "glass": "metal",
    "sheet": "metal",
    "mesh": "metal",
    "boards": "painted_wood",
}


def _balconies(local, facade, wall, plan, palette, to_u):
    """Balconies: a slab at each of their storeys' floors with a railing round it. A french balcony is the railing
    alone in front of its door; a recessed one is drawn as its front at the wall plane (the recess is not cut)."""
    for part in wall.get("balconies", []):
        kind = part.get("type", "cantilever")
        u, w = to_u(part.get("u_m", facade.width / 2)), part.get("w_m", 3.0)
        u0, u1 = max(0.0, u - w / 2), min(facade.width, u + w / 2)
        if u1 - u0 < 0.3:
            continue
        flat = kind in ("french", "recessed")
        depth = 0.1 if flat else max(0.4, part.get("depth_m", 1.4))
        railing, slab = part.get("railing", {}), part.get("slab", {})
        height, style = railing.get("height_m", 1.05), railing.get("kind", "bars")
        rail = palette.key(
            railing.get("material"),
            fallback={
                "family": RAILING_FAMILY.get(style, "concrete"),
                "colour": railing.get("colour", "#8a8d8f"),
            },
        )
        deck = palette.key(
            slab.get("material"),
            fallback={
                "family": "concrete",
                "colour": slab.get("colour", "#b9b8b2"),
                "finish": "cast",
            },
        )
        thickness = slab.get("thickness_m", 0.18)
        if "storeys" in part:
            floors = [plan.floors[i][0] for i in part["storeys"] if 0 <= i < len(plan.floors)]
        elif "v_m" in part:
            floors = [plan.datum + part["v_m"]]
        else:
            floors = [floor[0] for floor in plan.floors[1:]]
        for floor in floors:
            if kind != "french":
                mesh.box(
                    local, "attached", deck, u0, floor - thickness, u1, floor, 0.0, depth, back=True
                )
            if style == "bars":
                # Top and bottom rails with upright bars; a long balcony keeps the count down with wider spacing.
                for low, high in (
                    (floor + height - 0.05, floor + height),
                    (floor + 0.08, floor + 0.12),
                ):
                    mesh.box(
                        local, "attached", rail, u0, low, u1, high, depth - 0.05, depth, back=True
                    )
                    if not flat:
                        for x in (u0, u1 - 0.04):
                            mesh.box(
                                local, "attached", rail, x, low, x + 0.04, high, 0.0, depth - 0.05
                            )
                count = min(60, max(2, round((u1 - u0) / 0.13)))
                for k in range(count + 1):
                    x = u0 + (u1 - u0 - 0.02) * k / count
                    mesh.box(
                        local,
                        "attached",
                        rail,
                        x,
                        floor + 0.12,
                        x + 0.02,
                        floor + height - 0.05,
                        depth - 0.035,
                        depth - 0.015,
                        back=True,
                    )
            else:
                mesh.box(
                    local,
                    "attached",
                    rail,
                    u0,
                    floor,
                    u1,
                    floor + height,
                    depth - 0.05,
                    depth,
                    back=True,
                )
                if not flat:
                    for x in (u0, u1 - 0.05):
                        mesh.box(
                            local,
                            "attached",
                            rail,
                            x,
                            floor,
                            x + 0.05,
                            floor + height,
                            0.0,
                            depth - 0.05,
                        )


def blocked_region(facade, neighbours: list):
    """The part of a facade that stands against a touching neighbour: a party wall, where nothing opens."""
    regions = []
    for neighbour in neighbours:
        ring = np.asarray(neighbour["footprint"], float)
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        # The facade's plan line, pushed half a metre outwards, crossed with the neighbour's footprint.
        a = facade.p0 + facade.normal[:2] * 0.45
        b = facade.p1 + facade.normal[:2] * 0.45
        crossing = LineString([a, b]).intersection(polygon)
        for segment in getattr(crossing, "geoms", [crossing]):
            if segment.is_empty or segment.geom_type != "LineString":
                continue
            us = [float((np.asarray(p) - a) @ facade.u_dir[:2]) for p in segment.coords]
            top = neighbour["top_h"] - float(facade.origin[2])
            if max(us) - min(us) > 0.3 and top > 0.5:
                regions.append(rectangle(min(us) - 0.1, -1.0, max(us) + 0.1, top))
    return unary_union(regions) if regions else None
