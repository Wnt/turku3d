"""What kind of building the register and the map describe, and what such a building looks like in Turku when
nobody has read it: its type from the function code, storeys and footprint; its wall by facade material and decade;
the groups of defaults for small houses, outbuildings and halls; which wall takes the door, the garage doors or a
gable window.

Every rule is a statement about buildings of a kind, started from the old generator's tables (use codes, the wood
paint mix measured from 636 coloured wooden buildings, decade palettes) and corrected against the stored readings.
Nothing here knows one building. `priors` asks this module; this module asks nobody.
"""

import colorsys
import functools
import math
import zlib

from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

HOUSES = {"detached_house", "row_house"}
SHEDS = {"outbuilding", "garage"}
HALLS = {"industrial", "commercial"}

# bldg:function of the city's LoD2 model (the CityGML code list), by what the building is drawn as.
SMALL_RESIDENTIAL = {"1000"}
FLATS = {"1010", "1020", "1030", "1040", "1060", "1070", "1090", "1110"}
ANCILLARY = {"1360", "1370", "outbuilding"}
INDUSTRIAL = {"1310", "1320", "1330", "1410", "1630", "1640", "1880"}
RETAIL = {"1050", "1080", "1150", "1160", "1170", "1180", "1190", "1240"}
OFFICE = {"1120", "1130", "1140", "1970"}
UNKNOWN = {None, "", "0", "Ei arvoa"}


def footprint(doc: dict) -> tuple[float, float, float]:
    """(area, long side, short side) of the shell's footprint, the sides those of its smallest enclosing rectangle."""
    surfaces = doc.get("shell", {}).get("surfaces", [])
    rings = [s["rings"][0] for s in surfaces if s.get("kind") == "ground"]
    shape = None
    if rings:
        shape = unary_union([Polygon([p[:2] for p in ring]).buffer(0) for ring in rings if len(ring) >= 3])
    if shape is None or shape.is_empty:
        points = [p[:2] for s in surfaces if s.get("kind") == "wall" for p in s["rings"][0]]
        if len(points) < 3:
            return 0.0, 0.0, 0.0
        shape = MultiPoint(points).convex_hull
    box = shape.minimum_rotated_rectangle
    if box.geom_type != "Polygon":
        return float(shape.area), 0.0, 0.0
    corners = list(box.exterior.coords)
    a = math.dist(corners[0], corners[1])
    b = math.dist(corners[1], corners[2])
    return float(shape.area), max(a, b), min(a, b)


def era_year(doc: dict) -> int:
    """The year a building is drawn as: its own, else that of its dated neighbours, else recent (a building the
    register has not dated yet is most often new)."""
    register = doc.get("register", {})
    return register.get("year") or register.get("year_near") or 2015


def infer(doc: dict) -> str:
    """The style type of a building nobody has looked at."""
    register = doc.get("register", {})
    use, material = register.get("use"), register.get("material")
    dated = register.get("year")
    year = era_year(doc)
    storeys = register.get("storeys") or 0
    height = register.get("height_m") or 0.0
    area, _long, short = footprint(doc)
    low = storeys <= 2 and (not height or height < 10.5)
    single = storeys <= 1 and (not height or height < 6.5)
    if use == "outbuilding" or (single and area < 70 and (use in ANCILLARY or use in UNKNOWN or use == "1100")):
        return "garage" if area >= 15 and short >= 2.8 else "outbuilding"
    if use in ANCILLARY:
        return "garage" if area < 150 and single else "industrial"
    if use in INDUSTRIAL:
        return "industrial"
    if use in RETAIL:
        return "commercial"
    if use in OFFICE:
        return "office"
    old_wood = material in ("puu", None, "") and (dated or register.get("year_near") or 1900) < 1940
    if use in SMALL_RESIDENTIAL and low:
        return "wooden_house" if old_wood else "detached_house"
    if use in FLATS and low:
        if old_wood:
            return "wooden_house"
        if year >= 1940:
            return "row_house"
    if use in UNKNOWN and low and area < 300 and not dated:
        return "wooden_house" if material == "puu" and old_wood else "detached_house"
    if material == "puu" and low:
        return "wooden_house" if year < 1940 else "detached_house"
    if year < 1920:
        return "wooden_house" if low and material in ("puu", None, "") else "stone_apartment"
    if year < 1940:
        if low and material in ("puu", None, ""):
            return "wooden_house"
        return "classicist_apartment" if year < 1932 else "functionalist_apartment"
    if year < 1980:
        return "tower_block" if storeys >= 8 else "slab_block"
    return "modern_apartment"


# ---------------------------------------------------------------------------------------------------------------
# Walls: the register's facade material in a colour such a building usually has, by type and decade. sRGB as in
# overcast daylight. A list is a population: one entry is picked per building by its id, so repeats are weights.

BRICK_RED = ["#9a4a38", "#8c4e3e", "#a8694f", "#7a4234", "#8a5a42", "#96573f"]
BRICK_BUFF = ["#d3bf97", "#d8c6a0", "#cdb682", "#d8b877", "#c9a973", "#d2b98c", "#c2a57c", "#b8976c"]
BRICK_WHITE = ["#e3e0d5", "#e6ddcc", "#ddd3bc", "#e6e4dc"]
BRICK_BROWN = ["#6e4a3a", "#7b5544", "#5f4336"]
RENDER_VETERAN = ["#e6e7e4", "#e3e4e1", "#d3dad1", "#ddd3a8", "#e4d9b4", "#c9d3c0", "#e2cfc0", "#d6dde2", "#ead9a0"]
RENDER_NEW = ["#e9e9e6", "#ebe8e3", "#e6e6e2", "#dcd9d2", "#e9e9e6", "#d3d0c8", "#6a6c6e"]
RENDER_BLOCK = ["#cfc8b8", "#d6d0c2", "#c8c5ba", "#dddddb", "#cdc5b9", "#d9cdbb", "#e3dcc4"]
# The old generator's four families of wood paint, weights 0.42 / 0.30 / 0.145 / 0.138.
WOOD_TRADITIONAL = [
    "#d9c48e", "#cdc191", "#e2cf8e", "#d2bd84", "#dccb9a", "#c9b576",
    "#e3e4e0", "#d6d9d0", "#bcbdb0", "#e6e3d8",
    "#909f99", "#a3b1bf", "#b3bca6",
    "#9c3b30", "#ae443d",
]  # fmt: skip
WOOD_NEW = ["#e3e5e5", "#e3e4e0", "#e9e8e4", "#c9cbc8", "#d9cfb4", "#3a3c3f", "#8a8f92", "#b7a58a", "#e3e5e5"]
WOOD_SHED = ["#7b3a2e", "#8a3b30", "#7b3a2e", "#3b2a20", "#4a3a2e", "#243a36", "#8d8f8c", "#e6e7e5", "#d9c48e"]
CONCRETE_PANEL = ["#c4c2bc", "#cfc9bc", "#b8b8b6", "#d6d2c6", "#b3ab9c"]
CONCRETE_LATE = ["#d9cdbb", "#dddddb", "#cfc2b0", "#d6d2c6", "#c9c3b4"]
# Halls are mostly pale: white, light grey and beige sheet; a dark or strongly coloured one is the exception.
SHEET_HALL = [
    "#c6c9c8", "#d9dbd8", "#b3b7bb", "#a9b0bb", "#c8c096", "#e3e1d3", "#cfd2d0", "#bfc4c6", "#d6d0c0",
    "#8e9398", "#9aa3a8", "#7d7a5a", "#555e57", "#3a3028", "#6f8494",
]  # fmt: skip

BRICK = {"family": "brick", "bond": "stretcher", "colour2": "#b9b2a5"}
BRICK_PALE = {"family": "brick", "bond": "stretcher", "colour2": "#cfc9bb"}
PLASTER = {"family": "plaster", "finish": "fine"}
BOARDS = {"family": "weatherboard", "orientation": "horizontal", "profile": "bevel", "scale_m": 0.13}
BOARDS_UPRIGHT = {"family": "weatherboard", "orientation": "vertical", "profile": "board_batten", "scale_m": 0.15}
PANELS = {"family": "concrete", "finish": "cast"}
SHEET = {"family": "sheet_metal", "profile": "standing_seam", "scale_m": 0.3}


def pick(doc: dict, salt: str, options: list):
    return options[zlib.crc32(f"{doc.get('id', '')}/{salt}".encode()) % len(options)]


def wall(doc: dict, kind: str) -> dict | None:
    """The wall of an unread building of a kind: material by the register, colour by kind and decade. None where
    the kind and the register say nothing (the caller's style default applies)."""
    material = doc.get("register", {}).get("material")
    year = era_year(doc)
    if kind in SHEDS:
        if material == "tiili":
            return {**BRICK, "colour": pick(doc, "wall", BRICK_RED + BRICK_BUFF)}
        if material == "metallilevy":
            return {**SHEET, "colour": pick(doc, "wall", SHEET_HALL)}
        if material == "betoni":
            return {**PANELS, "colour": pick(doc, "wall", CONCRETE_PANEL)}
        return {**BOARDS_UPRIGHT, "colour": pick(doc, "wall", WOOD_SHED)}
    if kind in HALLS:
        if material == "tiili":
            return {**BRICK, "colour": pick(doc, "wall", BRICK_RED if year < 1970 else BRICK_BUFF + BRICK_RED)}
        if material == "betoni":
            return {**PANELS, "colour": pick(doc, "wall", CONCRETE_PANEL)}
        if material == "puu":
            return {**BOARDS_UPRIGHT, "colour": pick(doc, "wall", WOOD_SHED)}
        return {**SHEET, "colour": pick(doc, "wall", SHEET_HALL)}
    if kind in HOUSES:
        if material == "tiili":
            if year < 1955:
                colours, base = BRICK_RED, BRICK
            elif year < 1975:
                colours, base = BRICK_RED * 2 + BRICK_BROWN + BRICK_WHITE, BRICK
            else:
                colours, base = BRICK_BUFF * 2 + BRICK_WHITE + BRICK_RED[:4], BRICK
            colour = pick(doc, "wall", colours)
            return {**(BRICK_PALE if colour in BRICK_BUFF + BRICK_WHITE else base), "colour": colour}
        if material == "puu":
            return {**BOARDS, "colour": pick(doc, "wall", WOOD_TRADITIONAL if year < 2000 else WOOD_NEW)}
        if material == "betoni":
            return {**PLASTER, "colour": pick(doc, "wall", RENDER_NEW if year >= 2000 else RENDER_VETERAN)}
        if material == "metallilevy":
            return {**SHEET, "colour": pick(doc, "wall", SHEET_HALL)}
        # "muu materiaali" and no value: render on the post-war house and the new one, pale brick between.
        if year < 1965:
            return {**PLASTER, "colour": pick(doc, "wall", RENDER_VETERAN)}
        if year < 2000:
            colour = pick(doc, "wall", BRICK_WHITE + BRICK_BUFF[:3] + RENDER_VETERAN[:3])
            return {**(PLASTER if colour in RENDER_VETERAN else BRICK_PALE), "colour": colour}
        return {**PLASTER, "colour": pick(doc, "wall", RENDER_NEW)}
    # Blocks of flats, offices and the rest: only what the readings correct in the register's own palette.
    if material == "tiili" and year >= 1940:
        if year < 1970:
            colours = BRICK_BUFF[4:] * 2 + BRICK_RED
        elif year < 1990:
            colours = BRICK_RED + BRICK_BROWN + BRICK_BUFF[:3]
        else:
            colours = BRICK_RED + BRICK_BUFF[:4] + BRICK_WHITE[:2]
        colour = pick(doc, "wall", colours)
        return {**(BRICK_PALE if colour in BRICK_BUFF + BRICK_WHITE else BRICK), "colour": colour}
    if material == "betoni" and year >= 1980:
        return {**PANELS, "colour": pick(doc, "wall", CONCRETE_LATE)}
    if material in ("muu materiaali", None, "") and year >= 1940:
        return {**PLASTER, "colour": pick(doc, "wall", RENDER_BLOCK)}
    return None


def lab(colour: str) -> tuple[float, float, float]:
    """CIELAB (D65) of an sRGB hex colour."""
    rgb = []
    for k in (1, 3, 5):
        c = int(colour[k : k + 2], 16) / 255.0
        rgb.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = rgb
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    fx, fy, fz = (v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116 for v in (x, y, z))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def from_lab(lightness: float, a: float, b: float) -> str:
    fy = (lightness + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    x, y, z = (v**3 if v**3 > 0.008856 else (v - 16 / 116) / 7.787 for v in (fx, fy, fz))
    x, z = x * 0.95047, z * 1.08883
    linear = (
        3.2406 * x - 1.5372 * y - 0.4986 * z,
        -0.9689 * x + 1.8758 * y + 0.0415 * z,
        0.0557 * x - 0.2040 * y + 1.0570 * z,
    )
    out = []
    for c in linear:
        c = min(1.0, max(0.0, c))
        out.append(round(255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)))
    return "#" + "".join(f"{v:02x}" for v in out)


# The lightness (CIELAB L) a wall of a family has in overcast daylight: a measured colour is kept inside it,
# because a photograph's pixels carry its exposure and the colour of its light.
LIGHTNESS = {
    "brick": (30.0, 88.0),
    "plaster": (45.0, 93.0),
    "weatherboard": (20.0, 93.0),
    "concrete": (50.0, 90.0),
    "sheet_metal": (18.0, 92.0),
}


def measured_colour(material: dict, measured: dict | None) -> dict:
    """A wall material in the colour measured for it (a robust sample of the building's wall in a street photo):
    the hue and chroma as measured, the lightness within the family's range. Under confidence 0.4 it is ignored."""
    if not measured or "colour" not in measured:
        return material
    confidence = measured.get("conf", 1.0)
    if isinstance(confidence, dict):
        confidence = confidence.get("colour", confidence.get("*", 1.0))
    if confidence < 0.4:
        return material
    lightness, a, b = lab(measured["colour"])
    low, high = LIGHTNESS.get(material.get("family"), (20.0, 93.0))
    out = {**material, "colour": from_lab(min(high, max(low, lightness)), a, b)}
    if material.get("family") == "brick":
        out["colour2"] = "#cfc9bb" if lightness > 62 else "#b9b2a5"
    return out


# ---------------------------------------------------------------------------------------------------------------
# Groups of defaults.

TRIM = "#f0efe9"
DARK_TRIM = "#2f3235"
CAST = {"family": "concrete", "finish": "cast"}

SHED = {
    "wall": {**BOARDS_UPRIGHT, "colour": "#7b3a2e"},
    "plinth": {"height_m": 0.15, "projection_m": 0.0, "material": {**CAST, "colour": "#8f8d88"}},
    "ground_h": 2.4,
    "upper_h": 2.4,
    "sill": 1.2,
    "pitch": 4.5,
    "window": {
        "w_m": 0.8,
        "h_m": 0.6,
        "recess_m": 0.04,
        "lights": {"pattern": "single"},
        "surround": {"kind": "flat", "width_m": 0.08, "projection_m": 0.02, "colour": "#ecebe4"},
        "sill": {"kind": "none"},
    },
    "trim": "#ecebe4",
    "roof": {
        "material": {"family": "felt", "colour": "#3a3a3c", "profile": "plain"},
        "eaves": {"overhang_m": 0.3, "fascia_m": 0.14, "soffit": "open_rafters", "gutter": False},
        "verge": {"overhang_m": 0.25, "bargeboard": "plain"},
    },
}

HALL = {
    "wall": {**SHEET, "colour": "#a9b0bb"},
    "plinth": {"height_m": 0.3, "projection_m": 0.02, "material": {**CAST, "colour": "#8f8d88"}},
    "ground_h": 4.2,
    "upper_h": 3.4,
    "sill": 1.0,
    "pitch": 6.0,
    "window": {
        "w_m": 3.6,
        "h_m": 1.2,
        "recess_m": 0.08,
        "lights": {"pattern": "ribbon", "cols": 3},
        "frame": {"colour": "#e4e4e0", "width_m": 0.06},
        "surround": {"kind": "none"},
        "sill": {"kind": "sheet_metal", "projection_m": 0.03, "height_m": 0.03, "colour": "#8a8d8f"},
    },
    "trim": "#d8d8d4",
    "roof": {
        "material": {"family": "felt", "colour": "#44464a", "profile": "plain"},
        "eaves": {"overhang_m": 0.12, "fascia_m": 0.35, "soffit": "boxed", "gutter": False},
        "verge": {"overhang_m": 0.1, "bargeboard": "none"},
    },
}


def house_band(year: int) -> str:
    return "veteran" if year < 1960 else "sixties" if year < 1980 else "eighties" if year < 2000 else "new"


@functools.cache
def house(band: str, wooden: bool, dark: bool) -> dict:
    """Defaults of a detached or row house by the decade it was built in.

    Post-war houses stand tall on a cast basement under a steep tiled roof with small two-leaf windows; houses of
    the sixties and seventies are low and wide-windowed under shallow roofs; from the eighties on deep boxed eaves
    over tile, and since 2000 pale render or boards under dark sheet metal, some with dark frames and eaves.
    """
    spec = {
        "veteran": dict(ground=2.8, sill=0.85, pitch=2.9, w=1.0, h=1.25, lights={"pattern": "two_leaf"},
                        plinth=0.8, plinth_colour="#8d8f8c", eaves=0.45, fascia=0.16,
                        roof={"family": "clay_tile", "colour": "#96483a", "profile": "pantile", "scale_m": 0.3}),
        "sixties": dict(ground=2.7, sill=0.9, pitch=3.1, w=1.5, h=1.2,
                        lights={"pattern": "side_vent", "vent": "left", "vent_share": 0.28},
                        plinth=0.5, plinth_colour="#8d8b86", eaves=0.5, fascia=0.2,
                        roof={"family": "felt", "colour": "#3f3f41", "profile": "plain"}),
        "eighties": dict(ground=2.7, sill=0.8, pitch=2.9, w=1.2, h=1.3,
                         lights={"pattern": "side_vent", "vent": "left", "vent_share": 0.3},
                         plinth=0.35, plinth_colour="#a9a59b", eaves=0.6, fascia=0.2,
                         roof={"family": "concrete_tile", "colour": "#6a4638", "profile": "pantile", "scale_m": 0.33}),
        "new": dict(ground=2.8, sill=0.7, pitch=3.0, w=1.2, h=1.45,
                    lights={"pattern": "side_vent", "vent": "right", "vent_share": 0.3},
                    plinth=0.4, plinth_colour="#6a6c6e", eaves=0.6, fascia=0.22,
                    roof={"family": "sheet_metal", "colour": "#33363a", "profile": "standing_seam", "scale_m": 0.5}),
    }[band]  # fmt: skip
    trim = DARK_TRIM if dark else TRIM
    window = {
        "w_m": spec["w"],
        "h_m": spec["h"],
        "recess_m": 0.05 if wooden else 0.1,
        "lights": spec["lights"],
        "frame": {"colour": trim, "width_m": 0.06},
        "surround": (
            {"kind": "flat", "width_m": 0.07 if band == "new" else 0.09, "projection_m": 0.025, "colour": TRIM}
            if wooden
            else {"kind": "none"}
        ),
        "sill": (
            {"kind": "wood", "projection_m": 0.04, "height_m": 0.04, "colour": TRIM}
            if wooden
            else {"kind": "sheet_metal", "projection_m": 0.04, "height_m": 0.03, "colour": "#8a8d8f"}
        ),
    }
    return {
        "wall": {**PLASTER, "colour": "#e3e4e1"},
        "plinth": {
            "height_m": spec["plinth"],
            "projection_m": 0.0,
            "material": {**CAST, "colour": spec["plinth_colour"]},
        },
        "ground_h": spec["ground"],
        "upper_h": 2.7,
        "sill": spec["sill"],
        "pitch": spec["pitch"],
        "window": window,
        "trim": TRIM,
        "roof": {
            "material": spec["roof"],
            "eaves": {
                "overhang_m": spec["eaves"],
                "fascia_m": spec["fascia"],
                "soffit": "boxed",
                "gutter": True,
                "colour": trim,
            },
            "verge": {"overhang_m": max(0.3, spec["eaves"] - 0.15), "bargeboard": "plain", "colour": trim},
        },
    }


def group(doc: dict, kind: str) -> dict | None:
    """The defaults of a house, an outbuilding or a hall; None for the kinds `priors` keeps itself."""
    if kind in HOUSES:
        year = era_year(doc)
        wooden = doc.get("register", {}).get("material") == "puu"
        dark = year >= 2008 and zlib.crc32(f"{doc.get('id', '')}/trim".encode()) % 5 < 2
        return house(house_band(year), wooden, dark)
    if kind in SHEDS:
        return SHED
    if kind in HALLS:
        return HALL
    return None


# ---------------------------------------------------------------------------------------------------------------
# Roofing of a house from the orthophoto's colour, corrected against the readings: warm reds are tile, warm greys
# and browns are dark concrete tile, cold greys (dark roofing mirrors the sky) are seamed sheet metal.


def house_roofing(colour: str, year: int, pitch_deg: float) -> dict | None:
    hue, saturation, value = colorsys.rgb_to_hsv(*(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)))
    degrees = hue * 360
    if 60 < degrees < 110 and saturation >= 0.15:
        return None  # leaf green: tree crowns over the roof
    if pitch_deg <= 10:
        return None
    tile = "clay_tile" if year < 1965 else "concrete_tile"
    scale = 0.3 if tile == "clay_tile" else 0.33
    if (degrees >= 345 or degrees <= 26) and saturation >= 0.13:
        deep = colorsys.hsv_to_rgb(hue, min(0.68, max(0.55, saturation * 3.2)), min(0.66, max(0.5, value * 0.72)))
        return {"family": tile, "colour": _hex(deep), "profile": "pantile", "scale_m": scale}
    if 26 < degrees <= 55 and saturation >= 0.07:
        deep = colorsys.hsv_to_rgb(hue, min(0.3, saturation * 1.6), min(0.42, max(0.28, value * 0.45)))
        return {"family": tile, "colour": _hex(deep), "profile": "pantile", "scale_m": scale}
    if 110 <= degrees < 170 and saturation >= 0.12:
        deep = colorsys.hsv_to_rgb(hue, min(0.5, saturation * 1.6), min(0.5, max(0.3, value * 0.75)))
        return {"family": "sheet_metal", "colour": _hex(deep), "profile": "standing_seam", "scale_m": 0.5}
    grey = colorsys.hsv_to_rgb(hue, min(0.08, saturation), min(0.5, max(0.2, value * 0.42)))
    if value > 0.78 and saturation < 0.08:
        grey = colorsys.hsv_to_rgb(hue, min(0.05, saturation), 0.62)  # a pale metal roof
    return {"family": "sheet_metal", "colour": _hex(grey), "profile": "standing_seam", "scale_m": 0.5}


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def chimney(doc: dict, kind: str) -> dict | None:
    """Where the chimney of a house stands: a little below the ridge of its largest roof plane, a third along it.
    Houses heated by a stove or an oil burner have one; houses built since about 2005 mostly do not."""
    if kind != "detached_house" or era_year(doc) >= 2005:
        return None
    best = None
    for surface in doc.get("shell", {}).get("surfaces", []):
        if surface.get("kind") != "roof":
            continue
        ring = surface["rings"][0]
        if len(ring) < 3:
            continue
        area = Polygon([p[:2] for p in ring]).area
        heights = [p[2] for p in ring]
        if max(heights) - min(heights) > 0.8 and (best is None or area > best[0]):
            best = (area, ring)
    if best is None or best[0] < 20:
        return None
    ring = best[1]
    top = max(p[2] for p in ring)
    ridge = [p for p in ring if p[2] > top - 0.15]
    low = min(ring, key=lambda p: p[2])
    if len(ridge) < 2:
        return None
    a, b = max(((p, q) for p in ridge for q in ridge), key=lambda pq: math.dist(pq[0][:2], pq[1][:2]))
    if math.dist(a[:2], b[:2]) < 2.5:
        return None
    share = 0.3 + 0.4 * (zlib.crc32(f"{doc.get('id', '')}/chimney".encode()) % 100) / 100
    at = [a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share]
    down = [low[0] - at[0], low[1] - at[1]]
    length = math.hypot(*down) or 1.0
    at = [round(at[0] + down[0] / length * 0.9, 3), round(at[1] + down[1] / length * 0.9, 3)]
    band = house_band(era_year(doc))
    material = (
        {"family": "brick", "colour": "#8a4b3a", "colour2": "#b9b2a6"}
        if band in ("veteran", "sixties")
        else {"family": "sheet_metal", "colour": "#3a3c3f", "profile": "flat_seam"}
    )
    return {"anchor": {"at": at}, "w_m": 0.75, "d_m": 0.55, "h_m": 0.9, "material": material, "cap": "slab"}


# ---------------------------------------------------------------------------------------------------------------
# Which wall takes what.


def bay_centres(width: float, pitch: float) -> list:
    """The centres of an automatic wall's bays (the rule of `facade.layout`)."""
    count = max(0, int((width - 2.4) / pitch) + 1) if width >= 2.2 else 0
    return [width / 2 + (k - (count - 1) / 2) * pitch for k in range(count)]


def nearest_bay(width: float, pitch: float, u: float) -> float:
    centres = bay_centres(width, pitch)
    return min(centres, key=lambda c: abs(c - u)) if centres else width / 2


def wall_roles(doc: dict, kind: str, walls: list, pitch: float) -> dict:
    """What each free wall of an unread building carries besides its windows: {wall index: role}.

    `walls`: one dict a wall that stands on the ground and nobody described: index, width, rise (how far its
    outline climbs over the eaves line) and apex (the u of its highest point). A role may hold `doors` (u of each
    street door), `porch` (the doors stand under a porch roof), `garage` (u of each garage door), `loading`
    (u of each loading door), `gable` ((apex u, rise) of a gable to give a window) and `blank` (no windows).
    """
    walls = [w for w in walls if w["width"] >= 2.2]
    roles = {w["index"]: {} for w in walls}
    if not walls:
        return roles
    longest = max(walls, key=lambda w: w["width"])
    area, long_side, short_side = footprint(doc)
    if kind in HOUSES:
        sides = [w for w in walls if w["rise"] < 0.8] or walls
        front = max(sides, key=lambda w: w["width"])
        if kind == "detached_house":
            if front["width"] >= 4.5:
                third = front["width"] * (0.3 if zlib.crc32(doc.get("id", "").encode()) % 2 else 0.7)
                roles[front["index"]].update(doors=[nearest_bay(front["width"], pitch, third)], porch=True)
        else:
            units = max(1, round(front["width"] / 8.0))
            each = front["width"] / units
            doors = sorted({round(nearest_bay(front["width"], pitch, (k + 0.3) * each), 2) for k in range(units)})
            roles[front["index"]].update(doors=doors, porch=True)
        for w in walls:
            if w["rise"] >= 1.4 and 0.25 < w["apex"] / w["width"] < 0.75:
                roles[w["index"]]["gable"] = (w["apex"], w["rise"])
    elif kind == "garage":
        for w in walls:
            roles[w["index"]]["blank"] = True
        if long_side >= 9.0 and long_side >= 2.2 * short_side and short_side <= 8.5:
            # A row of garages: a door every three metres along its long side.
            count = max(1, int((longest["width"] - 0.4) / 3.0))
            first = (longest["width"] - (count - 1) * 3.0) / 2
            roles[longest["index"]]["garage"] = [first + k * 3.0 for k in range(count)]
        else:
            fits = [w for w in walls if w["width"] >= 3.0 and w["rise"] < 2.5]
            front = min(fits, key=lambda w: w["width"]) if fits else longest
            if front["width"] >= 5.8:
                roles[front["index"]]["garage"] = [front["width"] / 2 - 1.45, front["width"] / 2 + 1.45]
            elif front["width"] >= 2.9:
                roles[front["index"]]["garage"] = [front["width"] / 2]
            else:
                roles[front["index"]]["doors"] = [front["width"] / 2]
    elif kind == "outbuilding":
        for w in walls:
            roles[w["index"]]["blank"] = True
        side = 0.35 if zlib.crc32(doc.get("id", "").encode()) % 2 else 0.65
        roles[longest["index"]]["doors"] = [max(0.7, min(longest["width"] - 0.7, longest["width"] * side))]
    elif kind in HALLS:
        if longest["width"] >= 10.0:
            count = max(1, min(4, round(longest["width"] / 22.0)))
            each = longest["width"] / count
            roles[longest["index"]]["loading"] = [nearest_bay(longest["width"], pitch, (k + 0.65) * each) for k in range(count)]
            roles[longest["index"]]["doors"] = [nearest_bay(longest["width"], pitch, 0.12 * longest["width"])]
        elif longest["width"] > 4.0:
            roles[longest["index"]]["doors"] = [longest["width"] / 2]
    elif kind not in ("wooden_house", "wooden_villa") and longest["width"] > 6.0:
        # The street door of a block of flats: in the bay nearest the middle of its longest wall.
        centres = bay_centres(longest["width"], pitch)
        middle = longest["width"] / 2 + (0 if len(centres) % 2 else pitch / 2)
        roles[longest["index"]]["doors"] = [middle]
    return roles
