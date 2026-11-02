"""Deterministic Turku facade rules; no imagery, fitted model or height-to-floor inference.

The ordered assignment table is ``archetype`` below. Distributions are architectural
priors constrained by the 132 photo-supported descriptions in the 320-row OCI store
(2026-10-29). Register-derived rows are not counted as observations. The compiler
retains the separately calibrated register palette (docs/reports/facade-archetypes.md).
All ranges are metres except window width/height/sill, which are fractions of a bay/floor.
"""

from __future__ import annotations

import random
import re

from .facade_program import VERSION, stable_seed, unknown, value

# Restrained pigment families from supported descriptions, grouped by observed cladding.
PALETTES = {
    "plaster": ("#d8c89e", "#e7e5d6", "#c5c8bd", "#c3c8c9", "#b8b394", "#dfc477"),
    "brick": ("#704b49", "#79523d", "#a29477", "#b9ad8d", "#aaa18b"),
    "wood": ("#c9b576", "#8caa92", "#b8b9b0", "#b9433e", "#bdb29d", "#7e878a"),
    "concrete": ("#b9b7aa", "#b3b4aa", "#8c8f93", "#c4c6bf", "#e5e6e2"),
    "stone": ("#929187", "#827d74", "#a6a293", "#a99b90"),
    "glass": ("#a9b1ad", "#87958d", "#b6c2c0", "#657367"),
    "metal": ("#555e57", "#e3e1d3", "#c5c7bf", "#929286", "#326f67"),
}
LIGHT_TRIM = ("#deded3", "#e1e3dc", "#dce0df", "#d0cdbb")
DARK_TRIM = ("#454b49", "#747d79", "#657367")
# CityGML 1.0 Annex C.1 (OGC 08-007r1); unknown/local codes stay unknown.
USE = {
    **{str(c): "office" for c in (1120, 1130, 1140, 1970)},
    **{str(c): "retail" for c in (1160, 1170, 1180, 1190)},
    **{str(c): "industrial" for c in (1310, 1320, 1330, 1410)},
    **{str(c): "warehouse" for c in (1360, 1370, 1630, 1640, 1880)},
    **{
        str(c): "civic"
        for c in (
            1960,
            1980,
            1990,
            2070,
            2080,
            2090,
            2100,
            2110,
            2120,
            2140,
            2150,
            2160,
            2190,
            2210,
            2220,
            2300,
            2310,
        )
    },
}


def family(
    materials,
    bay,
    window,
    height,
    *,
    sill=(0.20, 0.28),
    cornice=(0, 0.12),
    courses=(0, 0),
    corners=(0, 0.12),
    surrounds=(0.02, 0.07),
    balconies=0,
    ground=("windows", "doors"),
    plinth=(0.25, 0.65),
    rhythm=("regular",),
    trim=LIGHT_TRIM,
):
    return dict(
        materials=materials,
        bay=bay,
        window=window,
        height=height,
        sill=sill,
        cornice=cornice,
        courses=courses,
        corners=corners,
        surrounds=surrounds,
        balconies=balconies,
        ground=ground,
        plinth=plinth,
        rhythm=rhythm,
        trim=trim,
    )


ARCHETYPES = {
    "empire": family(
        ("plaster",),
        (2.5, 3.7),
        (0.36, 0.50),
        (0.56, 0.70),
        cornice=(0.25, 0.50),
        courses=(0.08, 0.18),
        corners=(0.25, 0.50),
    ),
    "neo_renaissance": family(
        ("plaster", "plaster", "stone"),
        (2.4, 3.5),
        (0.35, 0.51),
        (0.58, 0.72),
        cornice=(0.30, 0.55),
        courses=(0.12, 0.24),
        corners=(0.35, 0.65),
        surrounds=(0.10, 0.18),
    ),
    "jugend": family(
        ("plaster", "plaster", "stone"),
        (2.4, 3.8),
        (0.40, 0.58),
        (0.54, 0.68),
        cornice=(0.18, 0.36),
        corners=(0.12, 0.35),
        surrounds=(0.07, 0.15),
        rhythm=("regular", "paired"),
    ),
    "nordic_classicism": family(
        ("plaster", "brick"),
        (2.5, 3.6),
        (0.37, 0.50),
        (0.52, 0.66),
        cornice=(0.20, 0.40),
        courses=(0.05, 0.12),
        corners=(0.15, 0.30),
    ),
    "functionalism": family(
        ("plaster",), (2.8, 4.1), (0.50, 0.72), (0.45, 0.59), cornice=(0.03, 0.12), balconies=0.18
    ),
    "postwar_plaster": family(("plaster",), (2.6, 3.6), (0.40, 0.58), (0.48, 0.62), balconies=0.24),
    "postwar_brick": family(("brick",), (2.7, 3.8), (0.42, 0.62), (0.47, 0.61), balconies=0.20),
    "sixties_slab": family(
        ("concrete", "plaster", "brick"),
        (3.0, 4.0),
        (0.56, 0.78),
        (0.44, 0.59),
        balconies=0.45,
        rhythm=("regular", "paired"),
    ),
    "panel_seventies": family(
        ("concrete", "concrete", "brick"),
        (3.0, 3.9),
        (0.58, 0.82),
        (0.44, 0.59),
        balconies=0.55,
        surrounds=(0.02, 0.05),
        trim=LIGHT_TRIM + DARK_TRIM,
    ),
    "postmodern_brick": family(
        ("brick",), (2.7, 3.7), (0.43, 0.63), (0.48, 0.65), courses=(0.04, 0.14), balconies=0.35
    ),
    "postmodern_plaster": family(
        ("plaster", "concrete"),
        (2.7, 3.8),
        (0.43, 0.64),
        (0.49, 0.65),
        courses=(0.04, 0.14),
        corners=(0.10, 0.30),
        balconies=0.32,
    ),
    "contemporary_housing": family(
        ("plaster", "brick", "concrete"),
        (2.6, 4.0),
        (0.45, 0.74),
        (0.50, 0.72),
        balconies=0.45,
        trim=LIGHT_TRIM + DARK_TRIM,
    ),
    "glass_office": family(
        ("glass",),
        (1.4, 2.8),
        (0.78, 0.92),
        (0.74, 0.87),
        sill=(0.06, 0.13),
        plinth=(0.08, 0.25),
        surrounds=(0.02, 0.04),
        trim=DARK_TRIM,
    ),
    "metal_office": family(
        ("metal", "glass"),
        (2.0, 3.4),
        (0.58, 0.82),
        (0.50, 0.73),
        surrounds=(0.02, 0.05),
        trim=DARK_TRIM,
    ),
    "timber_traditional": family(
        ("wood",),
        (2.0, 3.2),
        (0.35, 0.58),
        (0.50, 0.65),
        corners=(0.14, 0.22),
        cornice=(0.12, 0.25),
        surrounds=(0.08, 0.13),
    ),
    "timber_modern": family(
        ("wood",),
        (2.2, 3.6),
        (0.40, 0.68),
        (0.50, 0.72),
        corners=(0.06, 0.15),
        trim=LIGHT_TRIM + DARK_TRIM,
    ),
    "industrial_brick": family(
        ("brick",),
        (3.0, 4.8),
        (0.48, 0.72),
        (0.54, 0.72),
        courses=(0.03, 0.10),
        ground=("windows", "doors", "plain"),
    ),
    "warehouse": family(
        ("metal", "concrete", "brick"),
        (4.0, 6.0),
        (0.28, 0.45),
        (0.30, 0.45),
        ground=("plain", "doors"),
        plinth=(0.2, 0.5),
        trim=DARK_TRIM,
    ),
    "shopping": family(
        ("plaster", "metal", "brick"),
        (3.2, 4.8),
        (0.60, 0.80),
        (0.50, 0.66),
        ground=("shops",),
        plinth=(0.08, 0.20),
        trim=LIGHT_TRIM + DARK_TRIM,
    ),
    "civic": family(
        ("stone", "brick", "plaster"),
        (3.3, 4.8),
        (0.42, 0.65),
        (0.54, 0.70),
        cornice=(0.18, 0.40),
        corners=(0.20, 0.45),
        plinth=(0.50, 0.95),
    ),
}


# Central cadastral districts I–IX. These are architectural priors, not measured dates.
CENTRE_DISTRICTS = {f"853{i:03d}" for i in range(1, 10)}
CENTRE_PERIODS = ((1830, 1), (1885, 2), (1910, 3), (1925, 2), (1938, 2),
                  (1955, 3), (1965, 3), (1975, 1), (1990, 1))


def parsed_year(raw):
    match = re.search(r"(?<!\d)(1[6-9]\d{2}|20\d{2})(?!\d)", str(raw or ""))
    return int(match[0]) if match else None


def inferred_period(attrs):
    """Seeded period draw: nearby dated footprints dominate weaker area/shape priors.

    Donors contain only explicit dates; inferred assignments never become donors.
    Sorting and a per-building stream avoid dependence on tile/build iteration order.
    """
    seed = attrs.get("register_id") or attrs.get("osm_id") or attrs.get("seed_id", "unknown")
    rng = random.Random(stable_seed(seed, "fallback-period"))
    historic = attrs.get("historic") not in (None, "", "no", "0")
    heritage = attrs.get("heritage") not in (None, "", "no", "0")
    if historic or heritage:
        return rng.choices((1830, 1885, 1910, 1925), weights=(1, 2, 3, 1), k=1)[0], "heritage"
    donors = sorted(attrs.get("period_neighbours") or [], key=lambda d: (d[0], d[1], d[2]))
    if donors:
        years, weights = [], []
        for key, year, distance in donors:
            if parsed_year(year) and 0 <= distance <= 80:
                years.append(year)
                weights.append(1 / (5 + distance) ** 2)
        if years:
            return rng.choices(years, weights=weights, k=1)[0], "neighbours"
    centre = str(attrs.get("district")) in CENTRE_DISTRICTS
    if not centre and attrs.get("location_xz"):
        x, z = attrs["location_xz"]
        centre = -950 <= x <= 1150 and -650 <= z <= 1450
    if centre:
        periods = list(CENTRE_PERIODS)
        # Low courtyard wings and perimeter forms slightly favour the older stock.
        old_form = (attrs.get("courtyard") or (attrs.get("levels") or 99) <= 2
                    or (attrs.get("compactness", 1) < 0.4 and attrs.get("footprint_m2", 0) > 400)
                    or attrs.get("roof_shape") in ("gambrel", "mansard"))
        weights = [w * (1.35 if old_form and y < 1930 else 1) for y, w in periods]
        return rng.choices([y for y, _ in periods], weights=weights, k=1)[0], "centre"
    return None, "plain"


def archetype(attrs):
    """Ordered rule table: explicit use/cladding, then construction period.

    CityGML function codes are kept separate from OSM strings. Unknown register
    use codes deliberately abstain. Shape distinguishes low sheds from housing;
    district/frontage only refine supported urban forms, never invent shop POIs.
    """
    use = str(attrs.get("osm_use") or attrs.get("use") or "").lower()
    use = USE.get(use, use)
    material = attrs.get("material_class")
    year = parsed_year(attrs.get("construction_year") or attrs.get("year") or attrs.get("start_date"))
    levels = attrs.get("levels")
    area = attrs.get("footprint_m2", 0) or 0
    if use in ("warehouse", "shed", "garage", "garages") or attrs.get("blank"):
        return "warehouse"
    if use in ("industrial", "factory", "workshop"):
        return "industrial_brick" if material == "brick" or year and year < 1950 else "warehouse"
    # Ground-floor commerce does not turn a dated historic facade into a mall.
    # Keep the explicit shop use for recipe() while selecting its period style.
    historic_retail = (year is not None and year < 1930) or (
        year is None and attrs.get("era") in ("pre-1850", "1850-1919")
    )
    if use in ("retail", "supermarket", "mall") and not historic_retail:
        return "shopping"
    if use in ("school", "university", "civic", "church", "museum", "hospital"):
        return "civic"
    if material == "glass":
        return "glass_office"
    if use in ("office", "administrative") and (year or 0) >= 1980:
        return "metal_office" if material == "metal" else "glass_office"
    if material == "wood":
        return "timber_modern" if year and year >= 1980 else "timber_traditional"
    if material == "metal" and levels and levels <= 2 and area > 300:
        return "warehouse"
    if not year:
        era = attrs.get("era")
        year = {"pre-1850": 1830, "1850-1919": 1905, "1920-1959": 1950}.get(era)
    if not year:
        year, _ = inferred_period(attrs)
    if year and year < 1870:
        return "empire"
    if year and year < 1900:
        return "neo_renaissance"
    if year and year < 1920:
        return "jugend"
    if year and year < 1930:
        return "nordic_classicism"
    if year and year < 1945:
        return "functionalism"
    if year and year < 1960:
        return "postwar_brick" if material == "brick" else "postwar_plaster"
    if year and year < 1970:
        return "sixties_slab"
    if year and year < 1980:
        return "panel_seventies"
    if year and year < 2000:
        return "postmodern_brick" if material == "brick" else "postmodern_plaster"
    if year:
        return "contemporary_housing"
    if attrs.get("monumental"):
        return "civic"
    if levels and levels <= 2 and area < 100 and attrs.get("compactness", 1) > 0.65:
        return "warehouse" if material == "metal" else "postwar_plaster"
    if material == "brick":
        return "postwar_brick"
    return "panel_seventies" if material == "concrete" else "contemporary_housing"


def _colour(base, rng):
    shift = rng.uniform(-7, 7)
    return "#" + "".join(
        f"{max(0, min(255, round(int(base[i : i + 2], 16) + shift))):02x}" for i in (1, 3, 5)
    )


def recipe(building_id, attrs):
    """A random stream per field keeps unrelated edits from changing existing choices."""
    attrs = dict(attrs, seed_id=building_id)
    name = archetype(attrs)
    spec = ARCHETYPES[name]

    def rng(field):
        return random.Random(stable_seed(building_id, field))

    result = {
        k: round(rng(k).uniform(*spec[k]), 4)
        for k in (
            "bay",
            "window",
            "height",
            "sill",
            "cornice",
            "courses",
            "corners",
            "surrounds",
            "plinth",
        )
    }
    material = {"render": "plaster"}.get(attrs.get("material_class"), attrs.get("material_class"))
    material = material if material in PALETTES else rng("material").choice(spec["materials"])
    result.update(
        archetype=name,
        material=material,
        wall=_colour(rng("wall").choice(PALETTES[material]), rng("pigment")),
        trim=rng("trim").choice(spec["trim"]),
        ground=rng("ground").choice(spec["ground"]),
        rhythm=rng("rhythm").choice(spec["rhythm"]),
        balcony=rng("balcony").random() < spec["balconies"],
    )
    # Use a separate, visible shop/frontage observation rather than guessing commerce by year.
    if attrs.get("ground_floor") == "shops":
        result["ground"] = "shops"
    # Register business use supports ground commerce, not an entirely glazed historic block.
    if str(attrs.get("use")) in ("1050", "1080", "1150", "1240"):
        result["ground"] = "shops"
    # Shape/frontage adjust the rhythm inside the archetype's bounded distribution.
    if attrs.get("frontage_m", 0) > 25 and attrs.get("compactness", 1) < 0.55:
        result["rhythm"] = "paired" if "paired" in spec["rhythm"] else "regular"
    if str(attrs.get("district", "")) in ("853006", "853007") and (attrs.get("levels") or 0) > 2:
        if result["ground"] == "windows":
            result["ground"] = "doors"
    if attrs.get("wall_colour"):
        # Preserve the established named-OSM paint interpretation, including yellow/ochre.
        from .facade_fast_render import register_description

        result["wall"] = register_description(building_id, building_id, attrs, 10, 10)[
            "wall_colour"
        ]
    if attrs.get("blank"):
        result["ground"] = "plain"
    return result


def sample(building_id, attrs, geometry):
    """Make a program on real wall dimensions. Missing register storeys stay null."""
    seed_id = attrs.get("register_id") or attrs.get("osm_id") or building_id
    r = recipe(seed_id, attrs)
    floors = attrs.get("levels")
    if attrs.get("levels_source") == "osm":
        floors = None
    floors = int(floors) if isinstance(floors, (int, float)) and floors > 0 else None
    heads = ("flat", "segmental-arch", "round-arch") if r["archetype"] in (
        "neo_renaissance", "jugend", "civic", "empire", "nordic_classicism"
    ) else ("flat",)
    head = random.Random(stable_seed(seed_id, "head_shape")).choice(heads)
    v = lambda x: value(x, "rule sample")  # noqa: E731
    walls = []
    for g in geometry:
        width, height = g["width_m"], g.get("layout_height_m", g["height_m"])
        count = max(1, round(width / r["bay"])) if width >= 1.6 and not attrs.get("blank") else 0
        pitch = width / count if count else width
        ground = height / floors if floors else None
        if floors and floors > 1 and r["ground"] == "shops":
            ground = min(ground * 1.25, height * 0.6)
        upper = (height - ground) / (floors - 1) if floors and floors > 1 else ground
        wh = min((upper or height) * r["height"], (upper or height) * (1 - r["sill"] - 0.06))
        centers = [(i + 0.5) * pitch for i in range(count)]
        if r["rhythm"] == "paired" and count >= 4:
            centers = [c + (0.10 if i % 2 == 0 else -0.10) * pitch for i, c in enumerate(centers)]
        balconies = [
            [c - pitch * 0.35, ground + (f - 1) * upper, pitch * 0.70, min(1, upper * 0.32)]
            for f in range(1, floors or 1)
            for i, c in enumerate(centers)
            if r["balcony"] and i % 2 == 0
        ]
        walls.append(
            dict(
                **g,
                observation=dict(visible_share=0, wall_share=0, needs_review=False),
                split=dict(
                    base_height_m=value(ground, "register storeys / rule ground split"),
                    middle_repeats=max(0, floors - 2) if floors else None,
                    top_height_m=unknown(),
                ),
                wall=dict(colour=v(r["wall"]), material=v(r["material"])),
                bays=dict(
                    count=count,
                    centers_m=centers,
                    pitch_m=pitch,
                    symmetry=1,
                    support=0,
                    rhythm=r["rhythm"],
                ),
                windows=dict(
                    head_shape=v(head),
                    size_m=v([pitch * r["window"], wh]),
                    aspect=v(pitch * r["window"] / wh),
                    rectangles_m=[],
                    sill_fraction=v(r["sill"]),
                    frame_colour=v(r["trim"]),
                    divisions=v(2),
                    recess_m=v(0.12),
                ),
                base=dict(
                    colour=v(r["wall"]),
                    material=v(r["material"]),
                    plinth_height_m=v(r["plinth"]),
                    glazing_share=unknown(),
                    shopfront=v(r["ground"] == "shops"),
                    variant=v(r["ground"]),
                    openings_m=[],
                    head_shape=v(head),
                ),
                doors=dict(candidates=[], type=v("single"), canopy=unknown(), head_shape=v(head)),
                trim=dict(
                    cornice=v(r["cornice"]),
                    string_courses=v(r["courses"]),
                    window_surrounds=v(r["surrounds"]),
                    colour=v(r["trim"]),
                ),
                corners=dict(quoins=v(r["corners"]), shape=unknown(), tower=unknown()),
                balconies=v(balconies),
                bay_windows=unknown(),
                style_era=v(r["archetype"]),
            )
        )
    return dict(
        schema=VERSION,
        building_id=str(building_id),
        seed=str(stable_seed(seed_id)),
        source="rule-sampled",
        needs_review=False,
        archetype=r["archetype"],
        attributes=attrs,
        storeys=value(floors, "register" if floors else "unavailable", 1 if floors else 0),
        material=v(r["material"]),
        style_era=v(r["archetype"]),
        representative_wall=None,
        walls=walls,
    )
