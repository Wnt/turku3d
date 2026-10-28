"""Fast facade maps: a small wall description in, deterministic albedo/normal/ORM maps out.

A description is a few parameters read from a street photo by one vision call (material, wall
and trim colour, storeys, window grid and proportions, ground-floor type, roof line, features),
or taken from the building's register attributes when there is no usable photo. The renderer
draws a tiled material base with grit and weathering, the opening grid with frames and glass,
the ground-floor variant and the bands. No photo pixel is ever an input, so the maps ship.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image

from .paths import SCHEMAS_DIR

DESCRIPTION_VERSION = "facade-fast-v1"
SCHEMA = SCHEMAS_DIR / "facade_fast_description.schema.json"
MATERIALS = ("plaster", "brick", "stone", "wood", "concrete", "glass", "metal")
FEATURES = ("balconies", "pilasters", "string_courses", "shutters", "ornament", "arcade")
SUPERSAMPLE = 2
# Storey heights outside this range mean the storey count was misread.
STOREY_RANGE_M = (2.4, 5.5)
DEFAULT_STOREY_M = 3.2
# Window centre spacing outside this range means the column count was misread.
BAY_RANGE_M = (0.9, 8.0)
ROOF_BAND_M = {"cornice": 0.45, "parapet": 0.35, "eaves": 0.3, "none": 0.0}
PLINTH_M = 0.6
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Roughness and metallic of each wall material; glass openings are always 0.08 / 0.
SURFACE = {
    "plaster": (0.92, 0.0),
    "brick": (0.88, 0.0),
    "stone": (0.8, 0.0),
    "wood": (0.78, 0.0),
    "concrete": (0.9, 0.0),
    "glass": (0.12, 0.0),
    "metal": (0.5, 0.35),
}


# ------------------------------------------------------------------------------------------
# Descriptions
# ------------------------------------------------------------------------------------------


def schema() -> dict:
    return json.loads(SCHEMA.read_text())


def validate(answer: dict, facade_id: str) -> list[str]:
    """Problems that make a model answer unusable (empty when it can be rendered)."""
    from jsonschema import Draft7Validator

    problems = [e.message for e in Draft7Validator(schema()).iter_errors(answer)]
    if problems:
        return problems
    if answer["facade_id"] != facade_id:
        problems.append(f"answer is for {answer['facade_id']}, not {facade_id}")
    if len(set(answer["features"])) != len(answer["features"]):
        problems.append("duplicate features")
    return problems


def srgb(hex_colour: str) -> np.ndarray:
    if not HEX.match(hex_colour):
        raise ValueError(f"not a #rrggbb colour: {hex_colour!r}")
    return np.array([int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def to_hex(rgb) -> str:
    return "#" + "".join(f"{int(round(float(np.clip(v, 0, 1)) * 255)):02x}" for v in rgb)


def layout(description: dict, width_m: float, height_m: float) -> dict:
    """The metric layout drawn for a description on a wall of this size: bands, storeys and
    the opening grid, with every misread count replaced by a plausible one."""
    roof = min(ROOF_BAND_M[description["roof_line"]], 0.08 * height_m)
    plinth = min(PLINTH_M, 0.12 * height_m) if description["plinth"] else 0.0
    usable = max(height_m - roof, 0.5)
    storeys = description["storeys"]
    if storeys < 1 or not STOREY_RANGE_M[0] <= usable / storeys <= STOREY_RANGE_M[1]:
        storeys = max(1, round(usable / DEFAULT_STOREY_M))
    shops = description["ground_floor"] == "shops"
    if storeys == 1:
        ground = usable
    else:
        ground = usable / storeys * (1.25 if shops else 1.08)
        ground = min(max(ground, 2.8), 5.0, 0.6 * usable)
    upper = (usable - ground) / (storeys - 1) if storeys > 1 else 0.0
    columns = description["window_columns"]
    if storeys > 1 and description["material"] != "glass":
        if columns < 1 or not BAY_RANGE_M[0] <= width_m / columns <= BAY_RANGE_M[1]:
            columns = max(0, round(width_m / 2.8)) if columns else 0
    if description["material"] == "glass":
        columns = max(1, columns or round(width_m / 1.5))
    bay = width_m / columns if columns else width_m
    ww = float(np.clip(description["window_width"] or 0.45, 0.2, 0.85)) * bay
    ww = min(ww, 2.4) if description["material"] != "glass" else 0.92 * bay
    wh = float(np.clip(description["window_height"] or 0.5, 0.3, 0.8)) * (upper or ground)
    if description["material"] == "glass":
        wh = 0.72 * (upper or ground)
    return {
        "roof_m": roof,
        "plinth_m": plinth,
        "storeys": storeys,
        "ground_m": ground,
        "upper_m": upper,
        "columns": columns,
        "bay_m": bay,
        "window_m": [ww, wh],
    }


# Register palettes (sRGB hex): Turku's common cladding colours per material class and era.
# A wall without a usable photo takes one by a stable hash of its building ID.
PALETTES = {
    ("wood", "old"): ["#c9a35a", "#8b3a2b", "#d9c48e", "#a9b49a", "#d8cdb4", "#b98a4a"],
    ("render", "old"): ["#d9c7a3", "#d8bf7a", "#c99a86", "#b9b4aa", "#c2a365", "#cdb9a0"],
    ("render", "1920-1959"): ["#bdb7ab", "#cbbd9f", "#d3c58e", "#c4b6a2"],
    ("render", "1960+"): ["#d4d1c9", "#b8b5ae", "#cfc8b8"],
    ("brick", "any"): ["#8a4f3d", "#7a4a3b", "#9b5a44", "#c4a777"],
    ("concrete", "any"): ["#c9c6bd", "#b5b2aa", "#a8a49b"],
    ("metal", "any"): ["#8f969b", "#6d7378"],
}
TRIM = {"wood": "#e8e4da", "render": "#e4ddd0", "brick": "#d0ccc4", "concrete": "#8f8d88"}
MATERIAL_OF_CLASS = {
    "wood": "wood",
    "brick": "brick",
    "concrete": "concrete",
    "metal": "metal",
    "render": "plaster",
    "stone": "stone",
    "glass": "glass",
}


def stable_index(key: str, n: int) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % n


def register_description(
    facade_id: str, building_id: str, attributes: dict, width_m: float, height_m: float
) -> dict:
    """A description from the building register (facade material class, era, storeys): the
    fallback for a wall with no usable reference anywhere in its building. No model call."""
    cls = attributes.get("material_class") or "render"
    era = attributes.get("era") or "unknown"
    old = era in ("pre-1850", "1850-1919")
    key = (
        (cls, "any")
        if cls in ("brick", "concrete", "metal")
        else (cls, "old")
        if cls == "wood" or old
        else (cls, era)
        if (cls, era) in PALETTES
        else ("render", "1960+")
    )
    palette = PALETTES.get(key, PALETTES[("render", "1960+")])
    colour = palette[stable_index(building_id, len(palette))]
    if HEX.match(str(attributes.get("wall_colour", ""))):
        colour = attributes["wall_colour"]
    levels = attributes.get("levels") or max(1, round(height_m / DEFAULT_STOREY_M))
    bay = 2.6 if old or cls == "wood" else 3.0
    return {
        "facade_id": facade_id,
        "wall_visible": False,
        "problem": "no usable reference in the building",
        "material": MATERIAL_OF_CLASS.get(cls, "plaster"),
        "wall_colour": colour,
        "trim_colour": TRIM.get(cls, TRIM["render"]),
        "storeys": int(levels),
        "window_columns": max(0, round(width_m / bay))
        if width_m >= 1.6 and not attributes.get("blank")
        else 0,
        "window_width": 0.45,
        "window_height": 0.5,
        "ground_floor": "plain"
        if attributes.get("blank")
        else "shops"
        if attributes.get("ground_floor") == "shops"
        else "windows"
        if cls == "wood" or width_m < 6
        else "doors",
        "ground_openings": 0
        if attributes.get("blank")
        else max(1, round(width_m / 4))
        if attributes.get("ground_floor") == "shops"
        else 1
        if width_m >= 6
        else 0,
        "roof_line": "eaves" if cls == "wood" else "cornice" if old else "parapet",
        "plinth": cls != "wood",
        "features": [],
    }


def adopt(description: dict, facade_id: str, width_m: float, source_width_m: float) -> dict:
    """Another wall's description of the same building for this wall: the same material,
    colours, storeys and proportions, with the window columns at the same spacing."""
    out = dict(description, facade_id=facade_id)
    if description["window_columns"]:
        bay = source_width_m / description["window_columns"]
        out["window_columns"] = max(0, round(width_m / bay))
    if description["ground_openings"]:
        out["ground_openings"] = max(
            0, round(description["ground_openings"] * width_m / source_width_m)
        )
    return out


# ------------------------------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------------------------------


def seed_of(facade_id: str) -> int:
    return int(hashlib.sha256(f"{DESCRIPTION_VERSION}:{facade_id}".encode()).hexdigest()[:16], 16)


def value_noise(rng, shape, cell_px: float) -> np.ndarray:
    """Smooth noise in [-1, 1] with features about cell_px pixels across (bicubic grid)."""
    h, w = shape
    gh, gw = max(2, math.ceil(h / cell_px) + 2), max(2, math.ceil(w / cell_px) + 2)
    grid = rng.uniform(-1, 1, (gh, gw)).astype(np.float32)
    image = Image.fromarray(grid, mode="F").resize(
        (round(gw * cell_px), round(gh * cell_px)), Image.BICUBIC
    )
    return np.asarray(image)[:h, :w]


class Canvas:
    """Supersampled metric drawing surface: albedo (sRGB 0..1), height (m), roughness,
    metallic and occlusion, with rectangles given in metres from the wall's bottom-left."""

    def __init__(self, size_px, width_m, height_m):
        self.w, self.h = size_px[0] * SUPERSAMPLE, size_px[1] * SUPERSAMPLE
        self.sx, self.sy = self.w / width_m, self.h / height_m
        self.width_m, self.height_m = width_m, height_m
        self.albedo = np.zeros((self.h, self.w, 3), np.float32)
        self.height = np.zeros((self.h, self.w), np.float32)
        self.rough = np.zeros((self.h, self.w), np.float32)
        self.metal = np.zeros((self.h, self.w), np.float32)
        self.ao = np.ones((self.h, self.w), np.float32)
        # Metric coordinates of pixel centres: x from the left, y up from the base.
        self.x = (np.arange(self.w, dtype=np.float32) + 0.5) / self.sx
        self.y = self.height_m - (np.arange(self.h, dtype=np.float32) + 0.5) / self.sy

    def box(self, x0, y0, x1, y1):
        """Row and column slices of a metric rectangle (clipped; may be empty)."""
        c0 = max(0, round(x0 * self.sx))
        c1 = min(self.w, round(x1 * self.sx))
        r0 = max(0, round((self.height_m - y1) * self.sy))
        r1 = min(self.h, round((self.height_m - y0) * self.sy))
        return slice(r0, max(r0, r1)), slice(c0, max(c0, c1))

    def fill(self, rect, colour=None, height=None, rough=None, metal=None, ao=None, shade=None):
        rows, cols = self.box(*rect)
        if rows.start == rows.stop or cols.start == cols.stop:
            return
        if colour is not None:
            self.albedo[rows, cols] = colour
        if shade is not None:
            self.albedo[rows, cols] *= shade
        if height is not None:
            self.height[rows, cols] = height
        if rough is not None:
            self.rough[rows, cols] = rough
        if metal is not None:
            self.metal[rows, cols] = metal
        if ao is not None:
            self.ao[rows, cols] *= ao

    def finish(self, normal_strength: float = 1.0):
        """Downsample to the map size; returns uint8 albedo, normal and ORM arrays."""
        s = SUPERSAMPLE

        def down(a):
            h, w = a.shape[0] // s, a.shape[1] // s
            return a.reshape(h, s, w, s, *a.shape[2:]).mean(axis=(1, 3))

        from scipy import ndimage

        albedo = down(np.clip(self.albedo, 0, 1))
        height = down(self.height)
        # Height in metres -> slope per output pixel; +Y up (OpenGL / glTF), as post.maps.
        px_m = 1.0 / (self.sx / s)
        gx = ndimage.sobel(height, axis=1, mode="nearest") / (8 * px_m)
        gy = ndimage.sobel(height, axis=0, mode="nearest") / (8 * px_m)
        n = np.stack([-gx * normal_strength, gy * normal_strength, np.ones_like(height)], -1)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        orm = np.stack([down(self.ao), down(self.rough), down(self.metal)], -1)
        to8 = lambda a: np.clip(np.rint(a * 255), 0, 255).astype(np.uint8)  # noqa: E731
        return {"albedo": to8(albedo), "normal": to8(n * 0.5 + 0.5), "orm": to8(orm)}


def _material_base(c: Canvas, material: str, wall, trim, rng):
    """Tile the wall material over the whole canvas: colour variation, joints and grit."""
    X, Y = np.meshgrid(c.x, c.y)
    shade = np.ones((c.h, c.w), np.float32)
    relief = np.zeros((c.h, c.w), np.float32)
    joint_colour = None
    if material == "brick":
        course, length, mortar = 0.075, 0.25, 0.011
        row = np.floor(Y / course)
        offset = (row % 2) * length / 2
        col = np.floor((X + offset) / length)
        brick_id = (row * 7919 + col * 104729).astype(np.int64)
        shade *= 1 + 0.09 * np.sin(brick_id * 12.9898) * np.cos(brick_id * 78.233)
        joint = ((Y % course) < mortar) | (((X + offset) % length) < mortar)
        relief -= 0.006 * joint
        joint_colour = np.array([0.72, 0.69, 0.64], np.float32)
    elif material == "stone":
        course, length, gap = 0.45, 0.9, 0.018
        row = np.floor(Y / course)
        offset = (row % 2) * length * 0.37
        col = np.floor((X + offset) / length)
        block = (row * 7919 + col * 104729).astype(np.int64)
        shade *= 1 + 0.07 * np.sin(block * 12.9898) * np.cos(block * 4.1414)
        joint = ((Y % course) < gap) | (((X + offset) % length) < gap)
        relief -= 0.012 * joint
        shade *= 1 - 0.12 * joint
    elif material == "wood":
        board = 0.13
        row = np.floor(Y / board)
        shade *= 1 + 0.04 * np.sin(row * 12.9898)
        frac = (Y % board) / board
        relief += 0.012 * frac  # weatherboards overlap: thicker at the lower edge
        shade *= 1 - 0.18 * (frac < 0.07)
        grain = value_noise(rng, (c.h, c.w), max(2.0, 0.02 * c.sy))
        shade *= 1 + 0.03 * grain
    elif material == "concrete":
        panel_w, panel_h, gap = 3.0, 3.0, 0.02
        joint = ((X % panel_w) < gap) | ((Y % panel_h) < gap)
        relief -= 0.01 * joint
        shade *= 1 - 0.2 * joint
        panel = (np.floor(X / panel_w) * 31 + np.floor(Y / panel_h)).astype(np.int64)
        shade *= 1 + 0.035 * np.sin(panel * 12.9898)
    elif material == "metal":
        seam = 0.5
        seams = (X % seam) < 0.03
        relief += 0.01 * seams
        shade *= 1 + 0.06 * seams
    # Grit and broad patches common to every material (mild, so ETC1S stays small).
    patches = value_noise(rng, (c.h, c.w), max(4.0, 2.5 * c.sx))
    grit = rng.normal(0, 1, (c.h, c.w)).astype(np.float32)
    shade *= 1 + 0.045 * patches + 0.025 * grit
    if material == "plaster":
        relief += 0.0015 * grit
    c.albedo[:] = wall * shade[..., None]
    if joint_colour is not None:
        c.albedo[joint] = joint_colour * (1 + 0.03 * grit[joint, None])
    c.height[:] = relief
    c.rough[:] = SURFACE[material][0]
    c.metal[:] = SURFACE[material][1]


def _weathering(c: Canvas, rng, sills):
    """Grime near the base, under the roof band and in soft streaks below window sills."""
    base = np.clip(1 - c.y / 1.2, 0, 1)
    top = np.clip(1 - (c.height_m - c.y) / 1.0, 0, 1)
    vertical = 1 - 0.1 * base**1.5 - 0.06 * top**2
    c.albedo *= vertical[:, None, None]
    streak_noise = value_noise(rng, (c.h, c.w), max(3.0, 0.4 * c.sy))
    for x0, x1, y in sills:
        rows, cols = c.box(x0 + 0.05, max(0.0, y - 1.4), x1 - 0.05, y)
        if rows.start == rows.stop or cols.start == cols.stop:
            continue
        fade = np.linspace(1, 0, rows.stop - rows.start, dtype=np.float32)[::-1]
        fade = fade[:, None] * (0.6 + 0.4 * streak_noise[rows, cols])
        c.albedo[rows, cols] *= (1 - 0.07 * fade)[..., None]


def _window(c: Canvas, x0, y0, x1, y1, trim, glass, features, arched=False):
    frame = min(0.07, 0.12 * (x1 - x0))
    if "ornament" in features:
        c.fill((x0 - 0.12, y0 - 0.05, x1 + 0.12, y1 + 0.18), colour=trim, height=0.02)
    if "shutters" in features:
        sw = 0.45 * (x1 - x0)
        dark = trim * 0.75
        for sx0, sx1 in ((x0 - sw - 0.03, x0 - 0.03), (x1 + 0.03, x1 + sw + 0.03)):
            c.fill((sx0, y0, sx1, y1), colour=dark, height=0.015)
    c.fill((x0, y0, x1, y1), colour=trim, height=-0.04, rough=0.6)
    gx0, gy0, gx1, gy1 = x0 + frame, y0 + frame, x1 - frame, y1 - frame
    rows, cols = c.box(gx0, gy0, gx1, gy1)
    if rows.start == rows.stop or cols.start == cols.stop:
        return
    # Glass: darker below, a faint sky reflection towards the top.
    t = np.linspace(1, 0, rows.stop - rows.start, dtype=np.float32)[:, None, None]
    pane = glass * (0.8 + 0.45 * t**2)
    c.albedo[rows, cols] = pane
    c.height[rows, cols] = -0.12
    c.rough[rows, cols] = 0.08
    c.metal[rows, cols] = 0.0
    c.ao[rows, cols] *= 0.8
    if arched:
        # Round the head: blank out the corners above a semicircle.
        r = (gx1 - gx0) / 2
        cy = gy1 - r
        X, Y = np.meshgrid(c.x[cols], c.y[rows])
        outside = (Y > cy) & ((X - (gx0 + r)) ** 2 + (Y - cy) ** 2 > r * r)
        sub_a = c.albedo[rows, cols]
        sub_a[outside] = trim
        c.height[rows, cols][outside] = 0.0
    # Glazing bars: a centre mullion on wide windows, a transom at two thirds.
    bar = 0.04
    if gx1 - gx0 > 0.8:
        mid = (gx0 + gx1) / 2
        c.fill((mid - bar / 2, gy0, mid + bar / 2, gy1), colour=trim, height=-0.08, rough=0.6)
    if gy1 - gy0 > 1.1:
        tr = gy0 + 0.68 * (gy1 - gy0)
        c.fill((gx0, tr - bar / 2, gx1, tr + bar / 2), colour=trim, height=-0.08, rough=0.6)
    # Sill.
    c.fill((x0 - 0.06, y0 - 0.06, x1 + 0.06, y0), colour=trim * 1.05, height=0.04)


def render(description: dict, width_m: float, height_m: float, size_px, facade_id: str) -> dict:
    """Albedo, normal and ORM uint8 maps (top row = wall top) for one wall; deterministic."""
    rng = np.random.default_rng(seed_of(facade_id))
    plan = layout(description, width_m, height_m)
    c = Canvas(size_px, width_m, height_m)
    material = description["material"]
    wall, trim = srgb(description["wall_colour"]), srgb(description["trim_colour"])
    features = set(description["features"])
    glass = np.array([0.16, 0.2, 0.24], np.float32)
    _material_base(c, "glass" if material == "glass" else material, wall, trim, rng)
    if material == "glass":
        c.albedo[:] = wall * 0.35 + glass * 0.65
    roof, plinth = plan["roof_m"], plan["plinth_m"]
    ground, upper, storeys = plan["ground_m"], plan["upper_m"], plan["storeys"]
    columns, bay = plan["columns"], plan["bay_m"]
    ww, wh = plan["window_m"]
    sills = []
    # Bands: plinth, storey courses, pilasters at the corners, the roof line.
    if plinth:
        dark = wall * 0.62 if material in ("plaster", "wood") else np.array([0.42, 0.41, 0.4])
        c.fill((0, 0, width_m, plinth), colour=dark, height=0.03, rough=0.85)
    if "string_courses" in features and storeys > 1:
        for k in range(storeys - 1):
            y = ground + k * upper
            c.fill((0, y - 0.1, width_m, y + 0.1), colour=trim, height=0.04)
    if "pilasters" in features or material == "wood":
        pw = 0.15 if material == "wood" else 0.45
        if width_m > 3 * pw:
            for x0 in (0.0, width_m - pw):
                c.fill((x0, plinth, x0 + pw, height_m - roof), colour=trim, height=0.03)
    # Upper storeys.
    if material == "glass":
        for k in range(storeys):
            y0 = 0 if k == 0 else ground + (k - 1) * upper
            hgt = ground if k == 0 else upper
            for j in range(columns):
                x0 = j * bay + 0.04 * bay
                _window(
                    c, x0, y0 + 0.15 * hgt, x0 + 0.92 * bay, y0 + 0.87 * hgt, trim, glass, set()
                )
    elif columns:
        for k in range(1, storeys):
            y0 = ground + (k - 1) * upper
            sill = y0 + 0.42 * (upper - wh)
            for j in range(columns):
                cx = (j + 0.5) * bay
                if "balconies" in features and k >= 1 and j % 2 == 0 and ww + 0.8 < bay * 1.5:
                    c.fill(
                        (cx - ww / 2 - 0.4, y0, cx + ww / 2 + 0.4, y0 + 0.15),
                        colour=trim * 0.8,
                        height=0.2,
                        ao=0.85,
                    )
                    c.fill(
                        (cx - ww / 2 - 0.4, y0 + 0.15, cx + ww / 2 + 0.4, y0 + 1.05),
                        shade=0.55,
                        height=0.15,
                    )
                _window(c, cx - ww / 2, sill, cx + ww / 2, sill + wh, trim, glass, features)
                sills.append((cx - ww / 2, cx + ww / 2, sill))
    # Ground floor.
    kind = description["ground_floor"]
    base = plinth if kind != "shops" else min(plinth, 0.35)
    if material != "glass" and kind != "plain" and width_m >= 0.9:
        openings = description["ground_openings"]
        if kind == "shops":
            n = openings or max(1, round(width_m / 4.5))
            span = width_m / n
            ow = min(0.78 * span, 4.5)
            oh = min(0.74 * ground, 3.2)
            for j in range(n):
                cx = (j + 0.5) * span
                _window(
                    c,
                    cx - ow / 2,
                    base + 0.05,
                    cx + ow / 2,
                    base + 0.05 + oh,
                    trim,
                    glass,
                    set(),
                    arched="arcade" in features,
                )
            c.fill((0, base + oh + 0.25, width_m, base + oh + 0.6), colour=trim, height=0.03)
        else:
            n_col = columns or max(1, round(width_m / 2.8))
            if storeys == 1:
                n_col = max(openings, round(width_m / 2.6), 1)
            span = width_m / n_col
            doors = set()
            if kind == "doors":
                n_doors = min(n_col, 1 if width_m < 15 else 2)
                doors = {round((d + 0.5) * n_col / n_doors - 0.5) for d in range(n_doors)}
            door = trim * 0.45 + np.array([0.12, 0.07, 0.04], np.float32)
            for j in range(n_col):
                cx = (j + 0.5) * span
                if j in doors:
                    dw, dh = min(1.3, 0.8 * span), min(2.4, 0.85 * ground)
                    c.fill(
                        (cx - dw / 2 - 0.08, base, cx + dw / 2 + 0.08, base + dh + 0.08),
                        colour=trim,
                        height=-0.02,
                    )
                    c.fill(
                        (cx - dw / 2, base, cx + dw / 2, base + dh),
                        colour=door,
                        height=-0.1,
                        rough=0.6,
                        ao=0.85,
                    )
                    if dw > 1.0:
                        c.fill((cx - 0.02, base, cx + 0.02, base + dh), shade=0.6)
                elif storeys > 1 or kind == "windows" or kind == "doors":
                    gw = min(ww if columns else 0.45 * span, 0.8 * span)
                    gh = min(wh if upper else 0.5 * ground, 0.7 * ground)
                    sill = max(base + 0.5, ground * 0.42 - gh * 0.3)
                    if sill + gh > ground - 0.2:
                        sill = max(base + 0.2, ground - 0.2 - gh)
                    _window(
                        c,
                        cx - gw / 2,
                        sill,
                        cx + gw / 2,
                        sill + gh,
                        trim,
                        glass,
                        features,
                        arched="arcade" in features,
                    )
                    sills.append((cx - gw / 2, cx + gw / 2, sill))
    # Roof line.
    if roof:
        top = height_m - roof
        if description["roof_line"] == "eaves":
            c.fill((0, top, width_m, height_m), colour=wall * 0.35, height=0.0, ao=0.6)
        else:
            c.fill((0, top, width_m, height_m), colour=trim, height=0.12)
            c.fill((0, top - 0.08, width_m, top), shade=0.7, ao=0.75)
    _weathering(c, rng, sills)
    maps = c.finish()
    return {**maps, "layout": plan}


def save_png(array: np.ndarray, path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path)
