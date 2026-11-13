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
BLOCKS = {"functionalist_apartment", "slab_block", "tower_block", "modern_apartment", "office"}
WORKPLACES = {"office", "public", "commercial"}
OLD_WOODEN = {"wooden_house", "wooden_villa"}
OLD_MASONRY = {"stone_apartment", "classicist_apartment"}

# bldg:function of the city's LoD2 model (the CityGML code list), by what the building is drawn as. In Turku's
# model 1020 is the office and business building (every one read was an office, a technology-centre block or an
# old town house turned into offices), 1050 the house of shops with flats or offices over them.
SMALL_RESIDENTIAL = {"1000"}
FLATS = {"1010", "1030", "1040", "1060", "1070", "1090", "1110"}
ANCILLARY = {"1360", "1370", "outbuilding"}
INDUSTRIAL = {"1310", "1320", "1330", "1410", "1630", "1640", "1880"}
RETAIL = {"1050", "1080", "1150", "1160", "1170", "1180", "1190", "1210", "1220", "1240", "1340"}
OFFICE = {"1020", "1120", "1130", "1140", "1970"}
UNKNOWN = {None, "", "0", "Ei arvoa"}


def use_class(use) -> str | None:
    """What a building is used for, as far as its look goes: house, flats, ancillary, industrial, retail, office
    or public (schools, hospitals, museums, churches, halls of assembly), from the LoD2 function code or, where
    the model carries the national three-digit class instead, from that."""
    if use in UNKNOWN:
        return None
    if use in SMALL_RESIDENTIAL:
        return "house"
    if use in FLATS:
        return "flats"
    if use in ANCILLARY:
        return "ancillary"
    if use in INDUSTRIAL:
        return "industrial"
    if use in RETAIL:
        return "retail"
    if use in OFFICE:
        return "office"
    if not str(use).isdigit():
        return None
    code = int(use)
    if 1960 <= code <= 2999:
        return "public"
    if len(str(use)) == 3:
        # Rakennusluokitus 1994: 1xx trade, hotels, offices (15x) and traffic (16x), 2xx care, 3xx assembly,
        # 5xx education, 6xx industry, 7xx storage, 8xx fire stations.
        if 150 <= code <= 159:
            return "office"
        if 160 <= code <= 169 or 600 <= code <= 799:
            return "industrial"
        if 100 <= code <= 149:
            return "retail"
        if 200 <= code <= 599 or 800 <= code <= 899:
            return "public"
    return None


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
    used = use_class(use)
    if used == "industrial":
        return "industrial"
    old_wood = material in ("puu", None, "") and (dated or register.get("year_near") or 1900) < 1940
    # Shops, offices and public buildings in a house older than 1940 look like the houses of their time (a town
    # house with shops, a wooden house turned into offices): only later ones are a type of their own. A public
    # building is one at any age unless it is a low wooden house.
    if used == "retail" and year >= 1940:
        return "commercial"
    if used == "office" and year >= 1940:
        return "office"
    if used == "public" and not (old_wood and material == "puu" and storeys <= 2):
        return "public"
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
    if material == "puu" and year < 1940 and storeys <= 2:
        return "wooden_house"  # a tall roof over two wooden storeys: the model's height is the ridge
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
RENDER_BLOCK = ["#cfc8b8", "#d6d0c2", "#dddddb", "#e4e1d6", "#d5d5cf", "#e3dcc4", "#d4aa6a", "#dcc79a", "#cdc5b9", "#e8e6df"]
# Old wooden houses as read: three in five are yellow ochre, the rest white, grey-green, blue-grey, beige, red.
WOOD_OLD = [
    "#e2c672", "#e2cf8e", "#d8bc62", "#e0b272", "#e6d590", "#d3c08f", "#e2c67c", "#e3d9b3", "#dccb9a",
    "#d9dad3", "#d6d9d0", "#a6bfa2", "#c3cfc6", "#c3d1dc", "#b3a690", "#9c3b30",
]  # fmt: skip
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

# Offices and public buildings: metal cassettes are pale grey, white or silver, a few dark; facing tile and
# concrete elements pale warm greys (as read at Kupittaa and in the centre).
OFFICE_SHEET = ["#c6c9ca", "#d8d8d6", "#b9bbb8", "#a4a9a9", "#e2e2e0", "#cfd2d0", "#8e9498", "#b3b7bb", "#5a5c61", "#3a3b40"]
OFFICE_TILE = ["#cbc4b6", "#b8b4a9", "#d3cdbf", "#c2b8a6", "#dad6ca"]
OFFICE_RENDER = ["#e6e4dc", "#e3dcc4", "#dddddb", "#e8e6df", "#d6d0c2", "#dcc79a", "#e9e9e6"]

BRICK = {"family": "brick", "bond": "stretcher", "colour2": "#b9b2a5"}
BRICK_PALE = {"family": "brick", "bond": "stretcher", "colour2": "#cfc9bb"}
PLASTER = {"family": "plaster", "finish": "fine"}
BOARDS = {"family": "weatherboard", "orientation": "horizontal", "profile": "bevel", "scale_m": 0.13}
BOARDS_UPRIGHT = {"family": "weatherboard", "orientation": "vertical", "profile": "board_batten", "scale_m": 0.15}
PANELS = {"family": "concrete", "finish": "cast"}
SHEET = {"family": "sheet_metal", "profile": "standing_seam", "scale_m": 0.3}
CASSETTE = {"family": "sheet_metal", "profile": "cassette", "orientation": "horizontal", "scale_m": 0.9, "finish": "matt"}
ELEMENTS = {"family": "concrete", "finish": "panel", "scale_m": 3.0}
TILED = {"family": "ceramic_tile", "scale_m": 0.2}


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
    if workplace(doc, kind):
        if material == "metallilevy":
            return {**CASSETTE, "colour": pick(doc, "wall", OFFICE_SHEET)}
        if material == "lasi":
            return {**CASSETTE, "colour": pick(doc, "wall", ["#5c6a70", "#6f7f88", "#8fa0aa", "#3a3b40"])}
        if material == "betoni":
            return {**ELEMENTS, "colour": pick(doc, "wall", CONCRETE_PANEL if year < 1980 else CONCRETE_LATE + OFFICE_RENDER[:3])}
        if material == "tiili":
            if year < 1970:
                colours = BRICK_BUFF[4:] * 2 + BRICK_RED
            elif year < 1990:
                colours = BRICK_RED + BRICK_BROWN + BRICK_BUFF[:3]
            else:
                colours = BRICK_RED * 2 + BRICK_BUFF[:3] + BRICK_WHITE[:1] + BRICK_BROWN
            colour = pick(doc, "wall", colours)
            return {**(BRICK_PALE if colour in BRICK_BUFF + BRICK_WHITE else BRICK), "colour": colour}
        if material == "puu":
            return {**BOARDS, "colour": pick(doc, "wall", WOOD_TRADITIONAL if year < 2000 else WOOD_NEW)}
        # "muu materiaali" and no value: render until the sixties, then facing tile and concrete elements, brick
        # and render in the eighties and nineties, metal cassettes and white render since.
        if year < 1960:
            return {**PLASTER, "colour": pick(doc, "wall", OFFICE_RENDER)}
        if year < 1980:
            clad = pick(doc, "clad", [TILED, TILED, ELEMENTS, ELEMENTS, PLASTER])
        elif year < 2000:
            clad = pick(doc, "clad", [BRICK, BRICK, TILED, CASSETTE, PLASTER, PLASTER])
        else:
            clad = pick(doc, "clad", [CASSETTE, CASSETTE, CASSETTE, PLASTER, PLASTER, ELEMENTS])
        colours = {"brick": BRICK_RED + BRICK_BROWN, "ceramic_tile": OFFICE_TILE, "sheet_metal": OFFICE_SHEET,
                   "plaster": OFFICE_RENDER, "concrete": CONCRETE_PANEL if year < 1980 else CONCRETE_LATE}[clad["family"]]  # fmt: skip
        return {**clad, "colour": pick(doc, "wall", colours)}
    if kind in HALLS:
        if material == "tiili":
            return {**BRICK, "colour": pick(doc, "wall", BRICK_RED if year < 1970 else BRICK_BUFF + BRICK_RED)}
        if material == "betoni":
            return {**PANELS, "colour": pick(doc, "wall", CONCRETE_PANEL)}
        if material == "puu":
            return {**BOARDS_UPRIGHT, "colour": pick(doc, "wall", WOOD_SHED)}
        if kind == "commercial" and material != "metallilevy":
            return {**PLASTER, "colour": pick(doc, "wall", RENDER_BLOCK)}
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
    if kind in OLD_WOODEN and material in ("puu", None, ""):
        return {**BOARDS, "colour": pick(doc, "wall", WOOD_OLD)}
    if material == "tiili" and year >= 1940:
        if year < 1970:
            colours = BRICK_BUFF[4:] * 2 + BRICK_RED
        elif year < 1990:
            colours = BRICK_RED + BRICK_BROWN + BRICK_BUFF[:3]
        else:
            colours = BRICK_RED * 2 + BRICK_BUFF[:3] + BRICK_WHITE[:1]
        colour = pick(doc, "wall", colours)
        return {**(BRICK_PALE if colour in BRICK_BUFF + BRICK_WHITE else BRICK), "colour": colour}
    if material == "betoni" and year >= 1980:
        return {**PANELS, "colour": pick(doc, "wall", CONCRETE_LATE)}
    if material in ("muu materiaali", None, "") and year >= 1940:
        return {**PLASTER, "colour": pick(doc, "wall", RENDER_BLOCK)}
    if material == "betoni" and year < 1980 and kind in BLOCKS:
        # Concrete in the register is the frame as often as the face: half of these were read as render.
        colour = pick(doc, "wall", CONCRETE_PANEL + RENDER_BLOCK[:5])
        return {**(PANELS if colour in CONCRETE_PANEL else PLASTER), "colour": colour}
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
    the hue and chroma as measured, the lightness within the family's range, weighed by the measurement's
    confidence against the colour the kind and decade give. Under confidence 0.2 it is ignored; from 0.2 it counts
    half, rising to all of it at 0.5. On the 139 read buildings (`observe against --measured`) this beat a hard cut
    at 0.4 on the mean and the upper quartile (dE 13.7 -> 13.1, 17.0 -> 15.8) with the same median."""
    if not measured or "colour" not in measured:
        return material
    confidence = measured.get("conf", 1.0)
    if isinstance(confidence, dict):
        confidence = confidence.get("colour", confidence.get("*", 1.0))
    if confidence < 0.2:
        return material
    weight = 0.5 + 0.5 * min(1.0, (confidence - 0.2) / 0.3)
    lightness, a, b = lab(measured["colour"])
    low, high = LIGHTNESS.get(material.get("family"), (20.0, 93.0))
    lightness = min(high, max(low, lightness))
    if weight < 1.0 and material.get("colour"):
        base = lab(material["colour"])
        lightness, a, b = (p + (q - p) * weight for p, q in zip(base, (lightness, a, b)))
    out = {**material, "colour": from_lab(lightness, a, b)}
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
        "veteran": dict(ground=2.8, sill=0.85, pitch=3.4, w=1.0, h=1.25, lights={"pattern": "two_leaf"},
                        plinth=0.8, plinth_colour="#8d8f8c", eaves=0.45, fascia=0.16,
                        roof={"family": "clay_tile", "colour": "#96483a", "profile": "pantile", "scale_m": 0.3}),
        "sixties": dict(ground=2.7, sill=0.9, pitch=3.3, w=1.5, h=1.2,
                        lights={"pattern": "side_vent", "vent": "left", "vent_share": 0.28},
                        plinth=0.5, plinth_colour="#8d8b86", eaves=0.5, fascia=0.2,
                        roof={"family": "felt", "colour": "#3f3f41", "profile": "plain"}),
        "eighties": dict(ground=2.7, sill=0.95, pitch=3.4, w=1.2, h=1.1, lights={"pattern": "single"},
                         plinth=0.35, plinth_colour="#a9a59b", eaves=0.6, fascia=0.2,
                         roof={"family": "concrete_tile", "colour": "#6a4638", "profile": "pantile", "scale_m": 0.33}),
        "new": dict(ground=2.8, sill=0.5, pitch=3.4, w=1.0, h=1.65, lights={"pattern": "single"},
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


def block_band(year: int) -> str:
    return "postwar" if year < 1960 else "sixties" if year < 1980 else "eighties" if year < 2000 else "new"


@functools.cache
def block(band: str) -> dict:
    """Defaults of a block of flats or offices by the decade it was built in, sizes as the readings have them:
    small two-leaf windows close together after the war, wide windows with a vent leaf in the element blocks of
    the sixties and seventies, wider bays since."""
    spec = {
        "postwar": dict(w=1.25, h=1.35, pitch=3.2, lights={"pattern": "two_leaf"}, ground=3.0, upper=2.9,
                        recess=0.14, eaves=0.35, gutter=True, plinth=0.6, plinth_colour="#7d7a74"),
        "sixties": dict(w=2.1, h=1.4, pitch=3.6, lights={"pattern": "side_vent", "vent": "left", "vent_share": 0.24},
                        ground=2.9, upper=2.8, recess=0.1, eaves=0.15, gutter=False, plinth=0.5, plinth_colour="#76726c"),
        "eighties": dict(w=1.9, h=1.45, pitch=4.0, lights={"pattern": "side_vent", "vent": "left", "vent_share": 0.28},
                         ground=3.0, upper=2.9, recess=0.12, eaves=0.2, gutter=False, plinth=0.5, plinth_colour="#807c75"),
        "new": dict(w=2.1, h=1.6, pitch=4.4, lights={"pattern": "side_vent", "vent": "right", "vent_share": 0.3},
                    ground=3.1, upper=3.0, recess=0.14, eaves=0.15, gutter=False, plinth=0.45, plinth_colour="#6f6f6d"),
    }[band]  # fmt: skip
    return {
        "wall": {**PLASTER, "colour": "#d5cfc2"},
        "plinth": {"height_m": spec["plinth"], "projection_m": 0.0, "material": {**CAST, "colour": spec["plinth_colour"]}},
        "ground_h": spec["ground"],
        "upper_h": spec["upper"],
        "sill": 0.9,
        "pitch": spec["pitch"],
        "window": {
            "w_m": spec["w"],
            "h_m": spec["h"],
            "recess_m": spec["recess"],
            "lights": spec["lights"],
            "surround": {"kind": "none"},
            "sill": {"kind": "sheet_metal", "projection_m": 0.04, "height_m": 0.03, "colour": "#8a8d8f"},
        },
        "trim": "#e8e6e0",
        "roof": {
            "material": {"family": "felt", "colour": "#3f3f41", "profile": "plain"},
            "eaves": {"overhang_m": spec["eaves"], "fascia_m": 0.25, "soffit": "boxed", "gutter": spec["gutter"]},
            "verge": {"overhang_m": 0.1, "bargeboard": "none"},
        },
    }


def workplace(doc: dict, kind: str) -> bool:
    """A building drawn as an office: every office, public building and house of shops of three storeys or more
    built since 1940. Older ones look like the stone houses of their time; a low shop is a hall."""
    if kind not in WORKPLACES or era_year(doc) < 1940:
        return False
    return kind != "commercial" or (doc.get("register", {}).get("storeys") or 1) >= 3


def office_band(year: int) -> str:
    return "postwar" if year < 1960 else "sixties" if year < 1980 else "eighties" if year < 2000 else "new"


@functools.cache
def office(band: str, scheme: int) -> dict:
    """Defaults of an office, a school, a hospital or a business block by its decade, as read at Kupittaa, in
    the centre and in the work areas: close-set upright windows in render after the war; ribbons of windows
    between plain spandrels in the sixties and seventies; ribbons or square windows with dark frames in the
    eighties and nineties; since 2000 one of three faces picked by id: narrow storey-high lights close
    together, dark ribbons, or a glass wall in a grid of mullions. Storeys are taller than a dwelling's, the
    ground storey is glazed lower and wider, the roof is flat behind a metal fascia, and nothing has balconies
    or chimneys."""
    spec = {
        ("postwar", 0): dict(w=1.2, h=1.6, pitch=2.2, lights={"pattern": "single"}, sill=0.95, ground=3.6, upper=3.4,
                             recess=0.12, frame="#e9e7e0", fascia=0.2, out=0.3, edge="#d8d6ce", plinth=0.7),
        ("sixties", 0): dict(w=2.75, h=1.5, pitch=3.0, lights={"pattern": "ribbon", "cols": 2}, sill=0.95, ground=3.8,
                             upper=3.4, recess=0.08, frame="#d9d9d5", fascia=0.35, out=0.06, edge="#5a5d60", plinth=0.5),
        ("sixties", 2): dict(w=2.3, h=1.7, pitch=3.3, lights={"pattern": "three_leaf"}, sill=0.9, ground=3.8,
                             upper=3.5, recess=0.1, frame="#e4e4e0", fascia=0.35, out=0.06, edge="#5a5d60", plinth=0.5),
        ("eighties", 0): dict(w=2.6, h=1.5, pitch=3.0, lights={"pattern": "ribbon", "cols": 2}, sill=0.95, ground=3.8,
                              upper=3.5, recess=0.1, frame="#4a4c50", fascia=0.35, out=0.06, edge="#4a4c50", plinth=0.45),
        ("eighties", 1): dict(w=1.5, h=1.5, pitch=2.4, lights={"pattern": "single"}, sill=0.95, ground=3.8,
                              upper=3.5, recess=0.14, frame="#5a4334", fascia=0.35, out=0.06, edge="#5a4334", plinth=0.45),
        ("new", 0): dict(w=1.0, h=2.3, pitch=1.8, lights={"pattern": "single"}, sill=0.6, ground=4.4, upper=3.8,
                         recess=0.12, frame="#3a3b40", fascia=0.4, out=0.05, edge="#3a3b40", plinth=0.3),
        ("new", 1): dict(w=2.62, h=1.8, pitch=2.7, lights={"pattern": "ribbon", "cols": 2}, sill=0.9, ground=4.4,
                         upper=3.8, recess=0.08, frame="#2f3235", fascia=0.4, out=0.05, edge="#3a3b40", plinth=0.3),
        ("new", 2): dict(w=2.64, h=3.0, pitch=2.7, lights={"pattern": "grid", "cols": 2, "rows": 2}, sill=0.3,
                         ground=4.4, upper=3.8, recess=0.03, frame="#5c6268", fascia=0.4, out=0.05, edge="#5c6268", plinth=0.2),
    }  # fmt: skip
    spec = spec.get((band, scheme)) or spec[(band, 0)]
    glass = {"interior": "office", "reflectivity": 0.65, "curtains": 0.0, "blinds": 0.3}
    window = {
        "w_m": spec["w"],
        "h_m": spec["h"],
        "recess_m": spec["recess"],
        "lights": spec["lights"],
        "frame": {"colour": spec["frame"], "width_m": 0.06},
        "glass": glass,
        "surround": {"kind": "none"},
        "sill": {"kind": "sheet_metal", "projection_m": 0.03, "height_m": 0.03, "colour": "#8a8d8f"},
    }
    out = {
        "wall": {**PLASTER, "colour": "#dddddb"},
        "plinth": {"height_m": spec["plinth"], "projection_m": 0.0, "material": {**CAST, "colour": "#77756f"}},
        "ground_h": spec["ground"],
        "upper_h": spec["upper"],
        "sill": spec["sill"],
        "pitch": spec["pitch"],
        "window": window,
        "trim": "#d8d8d4",
        "roof": {
            "material": {"family": "felt", "colour": "#3f3f41", "profile": "plain"},
            "eaves": {"overhang_m": spec["out"], "fascia_m": spec["fascia"], "soffit": "boxed", "gutter": False,
                      "colour": spec["edge"]},  # fmt: skip
            "verge": {"overhang_m": spec["out"], "bargeboard": "none", "colour": spec["edge"]},
        },
    }
    if band != "postwar":
        # The ground storey: glass from near the floor, a bay wide, in the frames of the storeys above.
        out["ground_window"] = {
            "kind": "window",
            "w_m": round(spec["pitch"] - 0.3, 2),
            "h_m": round(spec["ground"] - 1.25, 2),
            "recess_m": 0.1,
            "lights": {"pattern": "ribbon", "cols": 2},
            "frame": {"colour": spec["frame"], "width_m": 0.07},
            "glass": {"interior": "shop", "reflectivity": 0.65, "curtains": 0.0, "blinds": 0.0},
            "sill": {"kind": "none"},
        }
        out["ground_sill"] = 0.35
    return out


def group(doc: dict, kind: str) -> dict | None:
    """The defaults of a house, an outbuilding, a hall, a block or a workplace; None for the kinds `priors`
    keeps itself (old wooden and stone houses, and workplaces in houses older than 1940)."""
    if workplace(doc, kind):
        year = era_year(doc)
        material = doc.get("register", {}).get("material")
        scheme = zlib.crc32(f"{doc.get('id', '')}/face".encode()) % 3
        if office_band(year) == "new" and material == "lasi":
            scheme = 2
        return office(office_band(year), scheme)
    if kind in WORKPLACES and (kind != "commercial" or (doc.get("register", {}).get("storeys") or 1) >= 3):
        return None
    if kind in BLOCKS:
        return block(block_band(era_year(doc)))
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


def roofing(colour: str, kind: str, year: int, pitch_deg: float, storeys: int, base: dict) -> dict | None:
    """The roofing of any unread building from the orthophoto's colour, corrected against the readings.

    The photo is taken in full sun from above; from the street the same roof is far darker (read lightness is
    about 33 where the photo has 70, whatever the photo's value), so a grey roof is drawn dark, and pale only
    where a pitched roof is very bright and colourless in the photo (galvanised or pale painted sheet). Red is
    tile on post-war houses and low blocks and red-painted sheet metal on older and taller buildings; a faint
    green on a pitched roof is green paint or patina. A pitched roof that is not tile is seamed sheet metal on
    every kind but the block of flats built since 1980 and the shed, which keep felt. `base` is the roofing of
    the style, which a flat roof keeps.
    """
    if kind in HOUSES:
        return house_roofing(colour, year, pitch_deg)
    hue, saturation, value = colorsys.rgb_to_hsv(*(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)))
    degrees = hue * 360
    if 60 < degrees < 110 and saturation >= 0.15:
        return None  # leaf green: tree crowns over the roof
    lightness, a, b = lab(colour)
    dark = from_lab(min(46.0, max(22.0, 0.3 * lightness + 12.0)), a * 0.4, b * 0.4)
    sheet = {"family": "sheet_metal", "profile": "standing_seam", "scale_m": 0.55}
    if pitch_deg <= 10:
        return {**base, "colour": dark}
    red = (degrees >= 345 or degrees <= 26) and saturation >= 0.13
    brown = 26 < degrees <= 55 and saturation >= 0.1
    if (red or brown) and kind not in HALLS and year >= 1940 and (storeys <= 2 or (year < 1980 and storeys <= 4)):
        return house_roofing(colour, year, pitch_deg)
    if red:
        deep = colorsys.hsv_to_rgb(hue, min(0.62, max(0.5, saturation * 3.0)), min(0.56, max(0.42, value * 0.66)))
        return {**sheet, "colour": _hex(deep)}
    if brown:
        deep = colorsys.hsv_to_rgb(hue, min(0.3, saturation * 1.6), min(0.4, max(0.26, value * 0.42)))
        return {**sheet, "colour": _hex(deep)}
    if 110 <= degrees < 175 and saturation >= 0.05 and a <= -4.0:
        return {**sheet, "colour": from_lab(min(62.0, max(38.0, lightness - 24.0)), a * 2.6, b * 1.6)}
    material = dict(base) if (kind == "modern_apartment" or kind in SHEDS) and base.get("family") == "felt" else sheet
    if lightness >= 80 and math.hypot(a, b) < 7 and material is sheet:
        return {**sheet, "colour": from_lab(68.0, a * 0.6, b * 0.6)}
    return {**material, "colour": dark}


def _ridge(doc: dict):
    """The ridge of the building's largest sloping roof plane and a point at its eaves: (a, b, low), or None."""
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
    if len(ridge) < 2:
        return None
    a, b = max(((p, q) for p in ridge for q in ridge), key=lambda pq: math.dist(pq[0][:2], pq[1][:2]))
    if math.dist(a[:2], b[:2]) < 2.5:
        return None
    return a, b, min(ring, key=lambda p: p[2])


def chimneys(doc: dict, kind: str) -> list:
    """The chimneys of an unread building, a little below the ridge of its largest roof plane.

    As read: every old wooden house has them (two on a long one), four in five stone houses of flats (one about
    every nine metres of ridge), post-war blocks under pitched roofs and nearly every detached house built
    before 2020; row houses, halls and later blocks have none that the street sees."""
    year = era_year(doc)
    found = _ridge(doc)
    if found is None:
        return []
    a, b, low = found
    length = math.dist(a[:2], b[:2])
    brick = {"family": "brick", "colour": "#8a4b3a", "colour2": "#b9b2a6"}
    metal = {"family": "sheet_metal", "colour": "#3a3c3f", "profile": "flat_seam"}
    render = {"family": "plaster", "colour": "#b9b4a8", "finish": "fine"}
    jitter = (zlib.crc32(f"{doc.get('id', '')}/chimney".encode()) % 100) / 100
    if kind == "detached_house" and year < 2020:
        shares = [0.3 + 0.4 * jitter]
        size = {"w_m": 0.75, "d_m": 0.55, "h_m": 0.9, "material": brick if year < 1980 else metal, "cap": "slab"}
    elif kind in OLD_WOODEN:
        shares = [0.25 + 0.1 * jitter, 0.68 + 0.1 * jitter] if length >= 11 else [0.35 + 0.3 * jitter]
        size = {"w_m": 0.8, "d_m": 0.6, "h_m": 1.1, "material": brick if jitter < 0.5 else metal, "cap": "slab"}
    elif kind in OLD_MASONRY or (kind in ("slab_block", "functionalist_apartment") and year < 1960):
        count = max(1, min(4, round(length / 9.0)))
        shares = [(k + 0.35 + 0.3 * jitter) / count for k in range(count)]
        size = {"w_m": 1.1, "d_m": 0.6, "h_m": 1.2, "material": render if jitter < 0.6 else brick, "cap": "slab"}
    else:
        return []
    # Down the slope: square to the ridge, on the side of the plane's lowest point.
    across = [-(b[1] - a[1]) / length, (b[0] - a[0]) / length]
    if across[0] * (low[0] - a[0]) + across[1] * (low[1] - a[1]) < 0:
        across = [-across[0], -across[1]]
    out = []
    for share in shares:
        at = [a[0] + (b[0] - a[0]) * share + across[0] * 0.9, a[1] + (b[1] - a[1]) * share + across[1] * 0.9]
        out.append({"anchor": {"at": [round(at[0], 3), round(at[1], 3)]}, **size})
    return out


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
    outline climbs over the eaves line), apex (the u of its highest point), height (ground to eaves) and, where
    known, its ends p0 and p1 and its outward normal in the survey frame. A role may hold `doors` (u of each
    street door), `porch` (the doors stand under a porch roof), `garage` (u of each garage door), `loading`
    (u of each loading door), `gable` ((apex u, rise) of a gable to give a window), `blank` (no windows),
    `shops` (the ground storey's row of shopfronts, one cell a bay) and `balconies` (parts for the wall's
    `balconies` list).
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
    elif kind in HALLS and not workplace(doc, kind):
        if kind == "commercial":
            if longest["width"] > 4.0:
                roles[longest["index"]]["doors"] = [nearest_bay(longest["width"], pitch, 0.5 * longest["width"])]
        elif longest["width"] >= 10.0:
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
    if workplace(doc, kind) and roles[longest["index"]].get("doors"):
        roles[longest["index"]]["canopy"] = True
    if kind in BLOCKS:
        part = balcony(doc, kind)
        tall = [w for w in walls if w["width"] >= max(9.0, 0.6 * longest["width"]) and w.get("height", 0.0) >= 7.5]
        if part and tall:
            # Balconies look to the sun: the long wall that faces nearest south-west carries them.
            sunny = max(tall, key=lambda w: -0.5 * w.get("normal", (0.0, 0.0))[0] - 0.87 * w.get("normal", (0.0, 0.0))[1])
            centres = bay_centres(sunny["width"], pitch)
            every, first = part.pop("every"), part.pop("first")
            taken = roles[sunny["index"]].get("doors", [])
            us = [c for k, c in enumerate(centres) if k % every == first % every and all(abs(c - u) > 0.1 for u in taken)]
            width = min(part["w_m"], pitch - 0.3) if every == 1 else part["w_m"]
            roles[sunny["index"]]["balconies"] = [{**part, "w_m": round(width, 2), "u_m": round(u, 2)} for u in us]
    if kind in BLOCKS | OLD_MASONRY | HALLS:
        fronts = shop_walls(doc, kind, walls, longest)
        for w in fronts:
            count = len(bay_centres(w["width"], pitch))
            if count:
                turn = zlib.crc32(f"{doc.get('id', '')}/shop/{w['index']}".encode()) % 3
                roles[w["index"]]["shops"] = ["@shop_door" if k % 3 == turn else "@shop" for k in range(count)]
    return roles


def balcony(doc: dict, kind: str) -> dict | None:
    """The balconies a block of flats of its decade has, as the readings count them (seven in ten slab blocks,
    five in six towers, three in four later blocks): small cantilevered slabs with a sheet front after the war,
    recessed bays or cantilevers with concrete fronts in the element blocks, glazed since the eighties. None for
    the blocks that have none, picked by id, and for offices."""
    if kind == "office":
        return None
    year = era_year(doc)
    chance = zlib.crc32(f"{doc.get('id', '')}/balcony".encode()) % 100
    band = block_band(year)
    if band == "postwar":
        if year < 1940 or chance >= 60:
            return None
        return {"type": "cantilever", "w_m": 2.4, "depth_m": 1.1, "every": 3, "first": 1,
                "railing": {"kind": "sheet", "height_m": 1.0, "colour": pick(doc, "rail", ["#d6cdb8", "#b9bcb4", "#8f9a8c", "#c9b99a"])},
                "slab": {"thickness_m": 0.14, "colour": "#c9c6bc"}}  # fmt: skip
    if band == "sixties":
        if chance >= 90:
            return None
        front = {"kind": "solid", "height_m": 1.05, "colour": pick(doc, "rail", ["#d9d6cc", "#c9c6bc", "#e3e0d6", "#b3ab9c"])}
        if chance % 2:
            return {"type": "recessed", "w_m": 3.2, "depth_m": 1.4, "every": 2, "first": 0, "railing": front,
                    "slab": {"thickness_m": 0.2, "colour": "#c9c6bc"}}  # fmt: skip
        return {"type": "cantilever", "w_m": 3.2, "depth_m": 1.4, "every": 2, "first": 0, "railing": front,
                "slab": {"thickness_m": 0.18, "colour": "#c9c6bc"}}  # fmt: skip
    if chance >= (75 if band == "eighties" else 90):
        return None
    rail = (
        {"kind": "solid", "height_m": 1.05, "colour": pick(doc, "rail", ["#e3e0d6", "#d9d6cc", "#c9c3b4", "#b9bcbd"])}
        if band == "eighties"
        else {"kind": "glass", "height_m": 1.1, "colour": pick(doc, "rail", ["#a9b6b5", "#9fb0bb", "#b9c3c2"])}
    )
    return {"type": "glazed", "w_m": 3.6, "depth_m": 1.6 if band == "eighties" else 1.8, "every": 2, "first": 0,
            "railing": rail, "slab": {"thickness_m": 0.22, "colour": "#d0cfc8"}}  # fmt: skip


def shop_walls(doc: dict, kind: str, walls: list, longest: dict) -> list:
    """The walls whose ground storey is shopfronts: those that the building's mapped premises (shops, cafes,
    banks: `register.premises`, points of the open map inside the footprint) lie at, and the longest wall of a
    building whose register use is trade, whatever its style."""
    premises = doc.get("register", {}).get("premises") or []
    fronts = {}
    for e, n in premises:
        gaps = []
        for w in walls:
            if "p0" not in w or w["width"] < 4.0:
                continue
            (ax, ay), (bx, by) = w["p0"], w["p1"]
            t = max(0.0, min(1.0, ((e - ax) * (bx - ax) + (n - ay) * (by - ay)) / (w["width"] ** 2)))
            gaps.append((math.hypot(e - ax - t * (bx - ax), n - ay - t * (by - ay)), w["index"], w))
        gaps.sort(key=lambda g: g[:2])
        # A premise is mapped at its door or in its room: the wall nearest it, and on a corner the other wall
        # within a room's depth.
        for k, (gap, index, w) in enumerate(gaps):
            if gap <= (14.0 if k == 0 else 8.0):
                fronts[index] = w
    trade = use_class(doc.get("register", {}).get("use")) == "retail"
    if (kind == "commercial" or trade) and not fronts and longest["width"] >= 6.0:
        fronts[longest["index"]] = longest
    return list(fronts.values())
