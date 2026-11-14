"""Resolve facade programs and compile metric wall instructions for the shared atlas.

Reviewed walls remain generated in coarse.stored_facades. Supported photographed
fields override only their own wall; missing fields retain explicit appearance priors.
No measured count changes register storeys. Null fields never erase a supported prior.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.facade_assets import aliases, opaque, root
from turku3d.facade_inference import overlay
from turku3d.facade_kit import MATERIALS, read_description, renderer


def source_paths():
    return [
        Path(__file__),
        Path(__file__).with_name("night_lighting.py"),  # use_class() packs window occupancy
        Path(__file__).with_name("facade_inference.py"),
        Path(__file__).with_name("facade_style_geometry.py"),
        Path(__file__).with_name("facade_program_geometry.py"),
        Path(__file__).with_name("facade_relief.py"),
        Path(__file__).with_name("facade_sections.py"),
        Path(__file__).with_name("facade_repetition.py"),
        Path(__file__).with_name("facade_modernist.py"),
        Path(__file__).with_name("open_ground.py"),
        Path(__file__).with_name("facade_garage.py"),
        Path(__file__).with_name("facade_slab.py"),
        Path(__file__).with_name("facade_timber.py"),
        Path(__file__).with_name("facade_blocks.py"),
        Path(__file__).parent / "coarse" / "register_timber.py",
        Path(__file__).parent / "coarse" / "register_timber.json",
        Path(__file__).with_name("facade_apertures.py"),
        Path(__file__).with_name("facade_components.py"),
        Path(__file__).with_name("facade_component_geometry.py"),
        Path(__file__).with_name("facade_learning.py"),
        Path(__file__).with_name("facade_monolith.py"),
        C.REPO_ROOT / "textures/turkutex/facade_learning.py",
        C.REPO_ROOT / "textures/turkutex/facade_learned_layout.py",
        C.REPO_ROOT / "textures/turkutex/facade_sections.py",
        C.REPO_ROOT / "textures/turkutex/facade_structure.py",
        C.REPO_ROOT / "textures/turkutex/facade_archetypes.py",
        C.REPO_ROOT / "textures/turkutex/facade_program.py",
    ]


@lru_cache(maxsize=8)
def _bundled(path, stamp):
    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = [rows] if "building_id" in rows else rows.get("programs", [])
    renderer()
    from turku3d.facade_sections import prepare

    return {str(r["building_id"]): prepare(r) for r in rows}


def measured(key):
    """Private exported programs may override the reviewed, descriptors-only pilot."""
    for directory in (C.work_root() / "facade-programs", root() / "facade-programs"):
        for identity in aliases(key):
            path = directory / opaque(identity)
            if path.is_file():
                row = _bundled(str(path), (path.stat().st_mtime_ns, path.stat().st_size))
                row = next(iter(row.values()))
                if str(row.get("building_id")) in aliases(key):
                    return row
    path = C.REPO_ROOT / "textures/library/facade-programs.json"
    if path.exists():
        rows = _bundled(str(path), (path.stat().st_mtime_ns, path.stat().st_size))
        return next((rows[k] for k in aliases(key) if k in rows), None)
    return None


def program_hash(key):
    # A pilot JSON edit invalidates just the tiles containing its changed building.
    from turku3d.facade_inference import evidence
    from turku3d.facade_learning import digest

    return json.dumps(
        [measured(key), evidence(key)], sort_keys=True, separators=(",", ":")
    ).encode() + digest(key)


def field(section, key, default=None):
    item = section.get(key, {})
    result = item.get("value") if isinstance(item, dict) else item
    return default if result is None else result


def matching_wall(vertices, program, *, unpainted=False):
    """The program's reviewed wall on this quad. ``unpainted`` also matches readings with
    nothing renderable, which still count as evidence about the wall (named windows)."""
    if not program or (program.get("needs_review", True) and not unpainted):
        return None
    from turku3d.coarse.stored_facades import match_prepared, prepare_walls

    facades = []
    for wall in program.get("walls", []):
        if wall.get("observation", {}).get("needs_review", True) and not unpainted:
            continue
        g = wall.get("geometry", {})
        points = g.get("coordinates") or ([g["p0"], g["p1"]] if "p0" in g and "p1" in g else None)
        if points is not None:
            normal = g.get("normal")
            if normal is not None and len(normal) == 2:
                normal = [normal[0], 0, -normal[1]]  # measured EN outward -> engine xyz
            facades.append(
                dict(
                    coordinates=points,
                    normal=normal,
                    base_height=g.get("base_h", vertices[0, 1]),
                    metadata={},
                    wall=wall,
                )
            )
    # Roof fragments may extend beyond the surveyed span. Match any meaningful
    # collinear overlap; wall_parts clips the observation and preserves the rest.
    length = float(np.linalg.norm(vertices[1] - vertices[0]))
    # A complete reviewed wall outranks a partial characterization of the same
    # frontage; partial readings still own walls no full-wall reading covers.
    full = [f for f in facades if f["wall"].get("observation", {}).get("scope") == "full-wall"]
    for group in (full, facades) if full and len(full) < len(facades) else (facades,):
        match = match_prepared(
            vertices, prepare_walls(group), min_coverage=max(0.02, 0.1 / max(length, 0.1))
        )
        if match:
            return match[0]["wall"]
    return None


def names_windows(reading):
    """A reviewed reading of this wall that names windows (not 'no windows'/'windowless')."""
    if not reading:
        return False
    form = reading.get("structure", {})
    text = " ".join(
        [str(s.get("kind", "")) + " " + " ".join(map(str, s.get("features", [])))
         for s in form.get("facade_observations", {}).get("sections", [])]
        + [str(f.get("kind", "")) for f in form.get("features", []) if isinstance(f, dict)]
        + [" ".join(map(str, r.get("bays", []))) for r in form.get("floors", [])]
    ).lower()
    text = re.sub(r"\b(no|without)\s+(\w+\s+)?windows?\b|windowless", " ", text)
    return bool(re.search(r"\bwindows?\b", text))


def reading_head(observed):
    """One window head shape named by every reviewed reading section that states one."""
    from turkutex.facade_sections import shape

    heads = set()
    for wall in (observed or {}).get("walls", []):
        if wall.get("observation", {}).get("needs_review", True):
            continue
        for section in wall.get("structure", {}).get("facade_observations", {}).get("sections", []):
            if isinstance(section.get("head"), str):
                head = shape(section["head"])
                heads.add(head if head == "flat" else head + "-arch")
    return heads.pop() if len(heads) == 1 else None


def described_blank_walls(key, vertices, description, observed=None):
    """A zero count is evidence for its source wall, never a whole building. A reviewed
    reading of the same wall that names windows outranks the description's zero."""
    if (description.get("window_columns") or not description.get("wall_visible", True)
            or description.get("problem")):
        return set()
    from turku3d.facade_assets import building
    from turku3d.coarse.stored_facades import match_prepared, prepare_walls

    sources = [f for f in building(key)["facades"]
               if f["metadata"].get("facade_id") == description.get("facade_id")]
    prepared = prepare_walls(sources)
    result = set()
    for index, points in enumerate(np.asarray(vertices).reshape(-1, 4, 3)):
        matched = match_prepared(points, prepared, min_coverage=.95)
        if not matched:
            continue
        source = matched[0]
        base = source["base_height"]
        height = source["metadata"].get("height_m", 0)
        if points[:, 1].min() >= base - .25 and points[:, 1].max() <= base + height + .25:
            if not names_windows(matching_wall(points, observed, unpainted=True)):
                result.add(index)
    return result


# Description fields a read wall states for itself (``facade_readings_convert.wall_layout``).
WALL_LAYOUT = ("window_columns", "width_m", "window_width", "window_height", "ground_floor", "lights")


def described_walls(vertices, row):
    """What the description's source read on each generated wall quad: the matched read wall's own
    layout fields (``WALL_LAYOUT``) and its balcony rails (``rails``: ``[[x, y, w, h], ...]`` with x
    along the quad from its first vertex), as ``{index: {...}}``."""
    walls = (row or {}).get("walls") or []
    if not walls:
        return {}
    from turku3d.coarse.stored_facades import match_prepared, prepare_walls

    prepared = prepare_walls([dict(coordinates=w["coordinates"], normal=None, metadata={},
                                   base_height=w.get("base_height") or 0, read=w)
                              for w in walls])
    result = {}
    for index, points in enumerate(np.asarray(vertices).reshape(-1, 4, 3)):
        matched = match_prepared(points, prepared, min_coverage=0.5)
        if not matched:
            continue
        source, uv = matched
        width = float(np.linalg.norm(points[1, [0, 2]] - points[0, [0, 2]]))
        start, forward = uv[0, 0], uv[1, 0] >= uv[0, 0]
        rails = []
        for u, y, w, h in source["read"].get("balconies") or []:
            x = u - start if forward else start - (u + w)
            if x + w > 0 and x < width:
                rails.append([x, y, w, h])
        found = {k: source["read"][k] for k in WALL_LAYOUT if k in source["read"]}
        if rails:
            found["rails"] = rails
        if found:
            result[index] = found
    return result


# The window bay pitch a description is drawn at on every wall, whatever its source width says.
DRAWN_PITCH_M = (1.5, 4.8)


def describe(program, description, source_width, *, blank_walls=frozenset(), walls=None,
             frame_colour=None, lights=None):
    """Keep existing AI/kit results, adapting only the bay count to each real wall. A row's own
    ``frame_colour`` (a reading's window frames) outranks the trim colour on the frames. ``walls``
    (``described_walls``) gives a wall the rhythm, ground storey and balcony rails its source read
    on that very wall; ``lights`` draws each window as a group of that many lights."""
    from turkutex.facade_program import value

    if not description.get("wall_visible", True):
        return
    features = description["features"]
    for index, w in enumerate(program["walls"]):
        read = (walls or {}).get(index, {})
        d = {**description, **{k: read[k] for k in WALL_LAYOUT if k in read}}
        pitch = read.get("width_m", source_width) / max(1, d["window_columns"])
        # Descriptions are a building-wide rhythm, not a licence to stretch a single
        # source bay over an unrelated long wall or squeeze it into a roof sliver.
        pitch = min(DRAWN_PITCH_M[1], max(DRAWN_PITCH_M[0], pitch))
        width, height = w["width_m"], w.get("layout_height_m", w["height_m"])
        has_windows = d["window_columns"] > 0
        located_blank = index in blank_walls
        count = (max(1, round(width / pitch)) if has_windows else w["bays"]["count"])
        if located_blank or width < 1.4:
            count = 0
        bay = width / max(count, 1)
        floors = program["storeys"]["value"]
        ground = height / floors if floors else height
        # One floor grid per building: a wall's own shop storey keeps the building's split.
        if floors and floors > 1 and description["ground_floor"] == "shops":
            ground = min(ground * 1.25, height * 0.6)
        upper = (height - ground) / (floors - 1) if floors and floors > 1 else ground
        v = lambda x: value(x, "existing description", 1)  # noqa: E731
        w["wall"].update(colour=v(d["wall_colour"]), material=v(d["material"]))
        if has_windows or located_blank or width < 1.4:
            w["bays"].update(
                count=count,
                pitch_m=bay,
                centers_m=[(i + 0.5) * bay for i in range(count)],
                rhythm="regular",
            )
        if has_windows:
            w["windows"].update(
                size_m=v([bay * max(0.1, d["window_width"]), upper * max(0.1, d["window_height"])]),
                sill_fraction=v(0.21),
                frame_colour=v(frame_colour or d["trim_colour"]),
            )
            # A wall read with its own rhythm groups only what was read on it.
            group = read.get("lights") if "window_columns" in read else lights
            if group and group >= 2:
                # A read pair or triple draws as one opening of that many lights (rows by height).
                w["windows"]["divisions"] = v([int(group), 0])
        w["split"]["base_height_m"] = v(ground)
        w["base"].update(
            colour=v(d["wall_colour"]),
            plinth_height_m=v(0.6 if d["plinth"] else 0),
        )
        if has_windows or located_blank:
            w["base"].update(variant=v(d["ground_floor"]), shopfront=v(d["ground_floor"] == "shops"))
        w["trim"].update(
            colour=v(d["trim_colour"]),
            cornice=v(0.35 if d["roof_line"] == "cornice" else 0.08),
            string_courses=v(0.15 if "string_courses" in features else 0),
            window_surrounds=v(0.12 if "ornament" in features else 0.04),
        )
        w["corners"]["quoins"] = v(
            0.30 if "pilasters" in features else 0.15 if d["material"] == "wood" else 0
        )
        # Existing generated balcony maps remain bound before this painter; a reading's
        # balcony walls (no maps) keep their rails.
        rails = read.get("rails", [])
        w["balconies"] = v([[x + w.get("uv_offset", 0), y, bw, bh] for x, y, bw, bh in rails])


def order_evidence(observed, description):
    """Reviewed feature names/notes and vision description features, as one text."""
    text = []
    for wall in (observed or {}).get("walls", []):
        if wall.get("observation", {}).get("needs_review", True):
            continue
        form = wall.get("structure", {})
        text += [str(f.get("kind", "")) for f in form.get("features", []) if isinstance(f, dict)]
        text += [str(form.get("occluded_parts", "")), str(wall["observation"].get("notes", ""))]
    if description:
        text += [str(f) for f in description.get("features", [])]
    return " ".join(text)


def classical(program, attrs, evidence):
    """Give a selected building's walls the measured classical order.

    Supported readings, descriptions and same-wall pilaster details keep priority:
    only unsupported fields take the order's measured priors.
    """
    from turkutex.facade_archetypes import ORDER, ORDER_SOURCE, classical_order
    from turkutex.facade_program import value

    from turku3d.facade_inference import supported

    if not classical_order(attrs, evidence):
        return
    program["classical_order"] = ORDER_SOURCE
    v = lambda x: value(x, ORDER_SOURCE, 0.6)  # noqa: E731
    for wall in program["walls"]:
        if field(wall.get("appearance_details", {}), "pilasters", False):
            continue  # facade_style_geometry already paints this wall's pilasters.
        if not wall.get("outline", True):
            continue  # The order dresses the outer facade, never stacked roof rings.
        wall["order"] = copy.deepcopy(ORDER)
        windows, trim = wall["windows"], wall["trim"]
        for section, key, item in (
            (windows, "divisions", ORDER["panes"]),
            (windows, "frame_colour", ORDER["frame"]),
            (trim, "colour", ORDER["trim"]),
            (trim, "window_surrounds", ORDER["surrounds_m"]),
            (trim, "cornice", ORDER["cornice_m"]),
        ):
            if not supported(section.get(key)):
                section[key] = v(item)
        if not supported(wall["corners"].get("quoins")):
            wall["corners"]["quoins"] = v(0)  # Corner pilasters replace painted quoins.
        # An ordered ground storey is windows over a plinth; a sampled door in every
        # bay is no observation. Observed doors and shopfronts keep their variant.
        base = wall["base"]
        if not supported(base.get("variant")) and field(base, "variant") == "doors":
            base["variant"] = v("windows")


PREMISES = "mapped customer premises in the town"
# Public buildings keep their own front: a cafe or restaurant inside a theatre, museum, church, school,
# college, community house or the old prison opens no shopfront. Photographed (pp-town, 2026-11-14): the
# museum's blank wall on Itainen Rantakatu, the park pavilion on Vartiovuori, the academy row on
# Henrikinkatu. Values of turkutex ``building_use``; coarse.premises also drops a premise mapped inside a
# public building's OSM outline. Together they keep 12 of the town's 70 undescribed premise buildings closed.
PREMISE_CLOSED_USES = frozenset({
    "civic", "public", "townhall", "museum", "theatre", "library", "arts_centre", "concert_hall", "gallery",
    "place_of_worship", "church", "chapel", "cathedral", "school", "college", "university", "kindergarten",
    "hospital", "prison", "community_centre",
})


def on_front(points, front, tolerance=0.2):
    """Whether a wall quad's ground line lies along a footprint edge ``(x0, z0, x1, z1)``."""
    a, b = np.asarray(front[:2], float), np.asarray(front[2:], float)
    p0, p1 = np.asarray(points[0])[[0, 2]], np.asarray(points[1])[[0, 2]]
    edge, wall = b - a, p1 - p0
    length, width = float(np.linalg.norm(edge)), float(np.linalg.norm(wall))
    if length < 1e-6 or width < 1e-6 or abs(float(edge @ wall)) < 0.995 * length * width:
        return False
    middle = (p0 + p1) / 2 - a
    along = float(middle @ edge) / length
    across = abs(float(middle[0] * edge[1] - middle[1] * edge[0])) / length
    return across <= tolerance and -tolerance <= along <= length + tolerance


# Small historic houses keep their windows: in historic Turku (the city's milieu type, register fact
# ``milieu``) a house of at most two register storeys dated before 1940 with a cafe or restaurant in it
# has the ordinary windows of a house in its street storey, the premise behind a door of its own.
# Photographed (pp-readings2, 2026-11-14, the 5 such houses of pp-town's 58 premise buildings): ordinary
# windows at Linnankatu 2's cafe (1817), Läntinen Rantakatu 1 (1830), Piispankatu 15's restaurant manor
# (1833) and Linnankatu 85's restaurant (1915); Tehtaankatu 6 (1891) has one display window among them.
# The glazed 2003 restaurant pavilion on Brahenkatu and undated buildings keep their fronts, and so do
# the taller blocks. Scope: the "Historiallinen Turku" zones only.
HOUSE_MILIEUS = frozenset({"Historiallinen Turku"})
HOUSE_STOREYS = 2
HOUSE_BEFORE = 1940


def small_historic_house(attrs) -> bool:
    from turkutex.facade_archetypes import parsed_year

    levels = attrs.get("levels")
    year = parsed_year(attrs.get("construction_year") or attrs.get("year") or attrs.get("start_date"))
    return (attrs.get("milieu") in HOUSE_MILIEUS and isinstance(levels, (int, float))
            and 1 <= levels <= HOUSE_STOREYS and year is not None and year < HOUSE_BEFORE)


def opens_premises(program, attrs, description) -> bool:
    """Whether the building's mapped premises open its walls: none describes it, its composition is not
    a masonry, monolith or civic one, no public use keeps its front closed and it is no small historic
    house."""
    from turkutex.facade_archetypes import MASONRY, MONOLITH, building_use

    return (bool(attrs.get("premise_fronts")) and not description
            and program["archetype"] not in (MASONRY, MONOLITH, "civic")
            and building_use(attrs) not in PREMISE_CLOSED_USES
            and not small_historic_house(attrs))


def premise_shopfronts(program, vertices, fronts):
    """Open the street storey of the walls on a mapped premise's front (``coarse.premises``) as
    shopfronts, as a mapped shop opens its building's. Reviewed walls keep what was read; walls
    without bays (blank or too narrow) stay closed. The storey heights are the building's own."""
    from turkutex.facade_program import value

    for index, wall in enumerate(program["walls"]):
        if wall.get("measured_wall_id") or not wall["bays"]["count"]:
            continue
        if any(on_front(vertices[index * 4 : index * 4 + 4], front) for front in fronts):
            wall["base"].update(variant=value("shops", PREMISES, 0.6), shopfront=value(True, PREMISES, 0.6))
            wall["premise_front"] = True


STOREY_MIN_M = 2.2  # resolve()'s storey plausibility band starts here; a lower storey is no floor grid
TOWER_WALL_M = 4.0  # walls of at least this much width must reach a floor-grid height


def register_levels(building):
    """The register's storey count of a building, or None (OSM storeys are checked against the shell)."""
    attrs = getattr(building, "attributes", None) or {}
    levels = attrs.get("levels")
    if attrs.get("levels_source") == "osm" or not isinstance(levels, (int, float)) or levels < 1:
        return None
    return levels


def floor_grid_height(rows, levels=None):
    """The building's floor-grid height from its walls' [(height m, width m)].

    The width-weighted median of the wall heights, so fragments a roof or tunnel clipped do not rescale
    the grid. Register storeys count the tallest part, though: a tower over wide low wings put the median
    at the wings and the tower drew its storeys half as tall (Koulukatu 19: 10 storeys, median 14.4 m,
    1.44 m storeys, twice its rows of small windows). When the median gives the register storeys less
    than 2.2 m each, the grid spans the lowest wall height that gives them at least 2.2 m and that walls
    of 4 m or more reach within a metre; lower walls are clipped by their own tops as before.
    """
    rows = sorted((float(h), float(w)) for h, w in rows)
    if not rows:
        return None
    half, accumulated = sum(w for _, w in rows) / 2, 0
    height = rows[-1][0]
    for h, w in rows:
        accumulated += w
        if accumulated >= half:
            height = h
            break
    if levels and height / levels < STOREY_MIN_M:
        for h, _ in rows:
            if h / levels >= STOREY_MIN_M and sum(w for g, w in rows if h <= g <= h + 1.0) >= TOWER_WALL_M:
                return h
    return height


def resolve(building, vertices, eaves, *, layout_height=None):
    renderer()  # Make the shared textures package importable in the OCI pipeline interpreter.
    from turkutex.facade_archetypes import MASONRY, MEASURED_PIGMENT, MONOLITH, sample

    geometry = []
    polygons = getattr(building.footprint, "geoms", [building.footprint])
    rings = [np.asarray(p.exterior.coords)[:, :2] for p in polygons]
    edge_a = np.concatenate([ring[:-1] for ring in rings])
    edge_b = np.concatenate([ring[1:] for ring in rings])
    vectors = edge_b - edge_a
    lengths = np.linalg.norm(vectors, axis=1)
    tangents = vectors / np.maximum(lengths[:, None], 1e-9)
    normal = np.column_stack((tangents[:, 1], -tangents[:, 0]))
    for index in range(0, len(vertices), 4):
        p = vertices[index : index + 4]
        width = float(np.linalg.norm(p[1, [0, 2]] - p[0, [0, 2]]))
        direction = (p[1, [0, 2]] - p[0, [0, 2]]) / max(width, 1e-9)
        along = np.sum((p[0, [0, 2]] - edge_a) * tangents, axis=1)
        distance = np.abs(np.sum((p[0, [0, 2]] - edge_a) * normal, axis=1))
        candidates = np.flatnonzero(
            (tangents @ direction > 0.995)
            & (distance < 0.1)
            & (along > -0.05)
            & (along + width < lengths + 0.05)
        )
        whole = max(candidates, key=lambda j: lengths[j]) if len(candidates) else None
        geometry.append(
            dict(
                wall_id=f"{building.facade_key}:{index // 4}",
                # Inner LoD2 roof rings and setbacks do not lie on the footprint outline.
                outline=whole is not None,
                width_m=float(lengths[whole]) if whole is not None else width,
                fragment_width_m=width,
                uv_offset=float(along[whole]) if whole is not None else 0,
                height_m=max(0.1, float(eaves.get(index, min(p[2:, 1])) - building.base)),
            )
        )
    from turku3d.facade_inference import (
        apply_prior,
        building_material,
        evidence,
        finish_material,
        reading_fields,
        supported,
    )

    # Roof/tunnel ownership splits a facade into fragments with different tops.
    # Those tops clip the building's floor grid; they must not rescale that grid.
    if layout_height is None:
        layout_height = floor_grid_height(
            [(g["height_m"], g["fragment_width_m"]) for g in geometry], register_levels(building))
    for g in geometry:
        g["layout_height_m"] = layout_height

    record = evidence(building.facade_key)
    attrs = dict(building.attributes)
    for key, item in record.get("building", {}).items():
        if supported(item):
            attrs[key] = item["value"]
    d = read_description(building.facade_key)
    if d and not d.get("wall_visible", True):
        d = None
    from turku3d.facade_assets import description_row
    from turku3d.facade_readings import SOURCE as READING

    row = (description_row(building.facade_key) or {}) if d else {}
    observed = measured(building.facade_key)
    family = building_material(attrs, record, observed, d, reading=row.get("source") == READING)
    from turku3d.facade_inference import material as normalize_material

    if family and normalize_material(attrs.get("material_class")) != family["value"]:
        attrs["material_class"] = family["value"]
    area = building.footprint.area
    attrs.update(
        E=float(building.footprint.centroid.x + C.ORIGIN_E),
        N=float(C.ORIGIN_N - building.footprint.centroid.y),
        log_area=float(np.log(max(area, 1))),
        footprint_m2=float(area),
        compactness=float(4 * np.pi * area / building.footprint.length**2),
        frontage_m=max(g["width_m"] for g in geometry),
    )
    from turku3d import facade_monolith
    from turkutex.facade_archetypes import public_monolith

    # Photographed blank walls select the public-monolith class (archetype owns the use/era rule).
    if public_monolith({**attrs, "monolith_blank": True}):
        attrs["monolith_blank"] = facade_monolith.evidence(building.facade_key, observed)["blank"]
    from turku3d.facade_learning import apply as apply_learned

    # Missing register data must not turn a known building shell into a blank wall.
    # This is a visual layout estimate, never a change to the source building/register.
    levels = attrs.get("levels")
    visual_levels = None
    if not levels or attrs.get("levels_source") == "osm":
        height = layout_height
        if d and d.get("storeys") and 2.2 <= height / d["storeys"] <= 5.5:
            levels, visual_levels = d["storeys"], "existing facade description"
        elif levels and 2.2 <= height / levels <= 5.5:
            visual_levels = "OSM storeys checked against shell height"
        else:
            storey = 3.0 if attrs.get("material_class") == "wood" else (
                3.6 if attrs.get("era") in ("pre-1850", "1850-1919") else 3.2
            )
            levels = max(1, round(height / storey))
            visual_levels = "visual estimate from shell eave height"
        attrs = {**attrs, "levels": levels, "levels_source": visual_levels}
    program = sample(building.facade_key, attrs, geometry)
    if visual_levels:
        program["storeys"].update(source=visual_levels, support=0.4)
    # Donor readings of ordinary city blocks are no prior for a reference-measured
    # masonry class (they would restore shopfronts and thin-walled layouts).
    if not d and program["archetype"] not in (MASONRY, MONOLITH):
        program = apply_learned(program)
    program["part_id"] = building.key
    # The calibrated register fallback owns colour; archetypes own layout and material priors.
    palettes = {}
    for wall in program["walls"]:
        if wall["wall"]["colour"].get("source") == MEASURED_PIGMENT:
            continue
        # A learned timber/brick family needs that material's pigment distribution,
        # rather than the generic blue-grey unknown-register palette.
        material = field(wall["wall"], "material", "plaster")
        material = "render" if material == "plaster" else material
        if material not in palettes:
            palettes[material] = renderer().register_description(
                str(building.facade_key), str(building.facade_key),
                {**attrs, "material_class": material}, attrs["frontage_m"],
                building.top - building.base,
            )["wall_colour"]
        wall["wall"]["colour"].update(
            value=palettes[material],
            source="measured wall colour"
            if attrs.get("wall_colour_source") == "measured"
            else "material palette",
        )
        wall["base"]["colour"] = dict(wall["wall"]["colour"])
    if d:
        describe(program, d, row.get("width_m") or attrs["frontage_m"],
                 blank_walls=described_blank_walls(building.facade_key, vertices, d, observed),
                 walls=described_walls(vertices, row),
                 frame_colour=row.get("frame_colour"), lights=row.get("lights"))
        program["description_source"] = row.get("source", "existing")
    program["facade_material"] = family
    head = reading_head(observed)
    if head:
        from turkutex.facade_program import value

        for wall in program["walls"]:
            # The building's photographed window heads replace the archetype's period draw.
            # The ground storey's windows share them; door heads keep their own vocabulary.
            wall["windows"]["head_shape"] = value(head, "unopposed reviewed window heads", 0.6)
            wall["base"]["head_shape"] = value(head, "unopposed reviewed window heads", 0.6)
    for index, wall in enumerate(program["walls"]):
        own = matching_wall(vertices[index * 4 : index * 4 + 4], observed)
        apply_prior(wall, program["archetype"], attrs, own, record)
        if own:
            # Geometry dimensions and register storeys belong to this actual generated shell.
            colour = copy.deepcopy(wall["wall"]["colour"])
            base_colour = copy.deepcopy(wall["base"]["colour"])
            overlay(
                wall,
                {
                    k: v
                    for k, v in reading_fields(own).items()
                    if k not in ("wall_id", "width_m", "height_m", "geometry", "observation")
                },
            )
            for key in (
                "structure",
                "render_sections",
                "geometry_components",
                "geometry_pending",
                "unsupported",
            ):
                if key in own:
                    wall[key] = copy.deepcopy(own[key])
            if own.get("bays", {}).get("support", 0) >= 0.6:
                wall["niches"] = copy.deepcopy(own.get("niches", []))
            wall["material_bands"] = [
                copy.deepcopy(band)
                for band in own.get("material_bands", [])
                if band.get("support", 0) >= 0.6
            ]
            wall["observation"] = copy.deepcopy(own["observation"])
            wall["measured_wall_id"] = own["wall_id"]
            wall["measured_geometry"] = copy.deepcopy(own.get("geometry", {}))
            wall["measured_size"] = [own["width_m"], own["height_m"]]
            wall["paint_source"] = "reviewed-program"
            # Uncalibrated photographed RGB includes light/shadow; do not call it pigment.
            if "calibrated albedo" != own.get("observation", {}).get("colour_space"):
                wall["wall"]["colour"] = colour
                wall["base"]["colour"] = base_colour
        elif wall.get("learned_layout") and not d:
            from turkutex.facade_learned_layout import expand

            points = vertices[index * 4 : index * 4 + 4]
            tangent = (points[1] - points[0]) / max(
                float(np.linalg.norm(points[1] - points[0])), 1e-6
            )
            start = points[0] - tangent * wall.get("uv_offset", 0)
            end = start + tangent * wall["width_m"]
            expand(
                wall,
                program["storeys"]["value"],
                dict(
                    p0=[float(start[0] + C.ORIGIN_E), float(C.ORIGIN_N - start[2])],
                    p1=[float(end[0] + C.ORIGIN_E), float(C.ORIGIN_N - end[2])],
                    base_h=building.base,
                ),
            )
        finish_material(wall, family, own, record, d, building.facade_key)
    if opens_premises(program, attrs, d):
        premise_shopfronts(program, vertices, attrs["premise_fronts"])
    if program["archetype"] == MONOLITH:
        facade_monolith.apply(program, building.facade_key, vertices, observed, d,
                              getattr(building, "entrances", ()), building.footprint)
    else:
        classical(program, attrs, order_evidence(observed, d))
    reviewed = [w for w in program["walls"] if w.get("measured_wall_id")]
    if reviewed:
        program["source"] = "reviewed-overlay"
        program["representative_wall"] = reviewed[0]["measured_wall_id"]
    return program


HEAD_SHAPES = {"flat": 0, "segmental-arch": 1, "round-arch": 2, "pointed-arch": 3}


def panel_grid(wall):
    """Blank cladding joints: [panel width m, panel height m, joint m, -1] (shader flag -1)."""
    grid = field(wall, "panel_grid")
    if (isinstance(grid, list) and len(grid) == 3 and all(isinstance(x, (int, float)) for x in grid)
            and grid[0] > 0.2 and grid[1] > 0.2 and 0 < grid[2] < 0.2):
        return [float(grid[0]), float(grid[1]), float(grid[2]), -1.0]
    return None


def compile_wall(wall, floors):
    width, height = wall["width_m"], wall.get("layout_height_m", wall["height_m"])
    material = field(wall["wall"], "material", "plaster")
    from turku3d.facade_inference import material as normalize_material

    material = normalize_material(material)
    material = material if material in MATERIALS else "plaster"
    count = max(0, int(wall["bays"]["count"]))
    count = count if floors else 0
    bay = width / max(1, count)
    ground_kind = field(wall["base"], "variant", "windows")
    if field(wall["base"], "shopfront", False):
        ground_kind = "shops"
    ground = field(wall["split"], "base_height_m", height / floors if floors else height)
    ground = min(height, max(0.1, ground))
    upper = (height - ground) / (floors - 1) if floors and floors > 1 else ground
    upper = max(0.1, upper)
    window_size = field(wall["windows"], "size_m", [bay * 0.45, upper * 0.5])
    ww, wh = window_size
    # A curtain wall is glazed edge to edge: only thin joints between its panes.
    curtain = bool(field(wall["windows"], "curtain_wall", False))
    ww = min(0.975 if curtain else 0.90, max(0.15, ww / bay))
    sill = min(0.6, max(0.01 if curtain else 0.03, field(wall["windows"], "sill_fraction", 0.21)))
    wh = min(1 - sill - (0.012 if curtain else 0.04), max(0.15, wh / upper))
    module = (
        MATERIALS.index(material) * 6
        + int(ww > 0.55) * 3
        + {"doors": 1, "shops": 2}.get(ground_kind, 0)
    )
    if not count:
        module = 42 + MATERIALS.index(material)

    def dimension(section, key, default=0):
        x = field(section, key, default)
        return float(x) if isinstance(x, (int, float)) else default

    trim_material = normalize_material(field(wall["trim"], "material", material))
    from turku3d.facade_inference import supported

    windows = wall["windows"]
    divisions = field(windows, "divisions") if supported(windows.get("divisions")) else None
    columns, rows = (divisions if isinstance(divisions, list) and len(divisions) == 2
                     else [divisions, None])
    def pane_count(value):
        return min(32, max(0, round(value))) if isinstance(value, (int, float)) else 0

    def pane_fraction(name, default):
        item = windows.get(name)
        return min(.95, max(0, item["value"])) if supported(item) and isinstance(item["value"], (int, float)) else default

    span = field(windows, "pane_span_m") if supported(windows.get("pane_span_m")) else None
    if not (isinstance(span, list) and len(span) == 2
            and all(isinstance(x, (int, float)) and np.isfinite(x) for x in span) and span[1] > .1):
        span = None
    return dict(
        module=module,
        trim_module=42 + MATERIALS.index(trim_material if trim_material in MATERIALS else material),
        bay=bay,
        storey=height / floors if floors else height,
        ground=ground,
        upper=upper,
        wall=field(wall["wall"], "colour", "#c5c8bd"),
        base=field(wall["base"], "colour", field(wall["wall"], "colour", "#c5c8bd")),
        trim=field(wall["trim"], "colour", "#deded3"),
        frame=field(windows, "frame_colour") if supported(windows.get("frame_colour"))
        else field(wall["trim"], "colour", "#deded3"),
        glazing=[pane_count(columns), pane_count(rows),
                 pane_fraction("pane_split_fraction", 0), pane_fraction("transom_fraction", -1)],
        glazing_span=span,
        glazing_scope=field(windows, "pane_scope", "window"),
        plinth=False,
        size=[width, wall["height_m"]],
        origin=[-wall.get("uv_offset", 0), wall.get("base_h", 0)],
        opening=panel_grid(wall) if not count and panel_grid(wall)
        else [ww, wh, sill, dimension(wall["trim"], "window_surrounds", 0.04)],
        bands=[
            dimension(wall["base"], "plinth_height_m"),
            dimension(wall["trim"], "cornice"),
            dimension(wall["trim"], "string_courses"),
            dimension(wall["corners"], "quoins"),
        ],
        detail=[
            0.1 if wall["bays"].get("rhythm") == "paired" else 0,
            int(bool(field(wall, "balconies", []))),
            int(ground_kind == "plain")
            + 2
            * HEAD_SHAPES.get(
                field(
                    wall["doors"] if ground_kind == "doors" else wall["base"], "head_shape", "flat"
                ),
                0,
            ),
            -HEAD_SHAPES.get(field(wall["windows"], "head_shape", "flat"), 0),
        ],
    )
