"""Building style records and wall maps cut from one accepted generated facade.

A style record reads one accepted generated map of a building: wall colour and texture, the
storey rows (cornice, upper storeys, ground floor, plinth) and the bay columns. Rows and columns
are cut only where the map is plain wall: a column is a cut candidate when every row of its
section matches that row's dominant colour (piers, string courses and cornices are horizontal and
match; window frames, glass and pilasters do not) and it holds no window glass. Rows are cut the
same way against each column's dominant colour (spandrels).

A derived map of another wall of the same building keeps the source's end pieces (corners,
ground floor, cornice), repeats the interior bays and storeys in source order until the measured
wall is filled, and resamples by at most 12 % to the exact size. Walls narrower than one bay or
lower than one storey get the plain wall: corners and repeated pier columns, plinth, spandrel
rows and cornice. Albedo, normal and ORM follow the same plan and are encoded with the facade
post encoder at the generated maps' 32 pixels per metre.
"""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .facade_batch import metric_size

STYLE_VERSION = "building-style-v1"
PPM = 32
MAX_STRETCH = 0.12
# Glass is darker than this fraction of its row's 75th lightness percentile (and 15 L* below it).
GLASS_RATIO = 0.72
# Glass and dark door leaves are near-neutral (C*ab below this); painted walls are not.
GLASS_CHROMA = 20.0
# Energy (ΔE) a glass pixel contributes to row and column profiles.
GLASS_ENERGY = 50.0
# A ground-floor or upper section thinner than this is no section of its own.
MIN_SECTION_PX = 4
# A cut line may cross at most this many glass pixels (or this share of its length): a pipe or
# a thin shadow line, never a window.
CROSSING_MIN_PX = 4
CROSSING_SHARE = 0.02
# A row or column belongs to an opening when its glass fraction exceeds this share of the
# axis's highest glass fraction (and at least OPENING_MIN).
OPENING_LEVEL = 0.35
OPENING_MIN = 0.05
MAP_KINDS = ("albedo", "normal", "orm")
# A seam's median ΔE step may be at most this multiple of the 90th percentile step between any
# two of the source's own cut positions (and always SEAM_STEP_FLOOR).
SEAM_STEP_MAX = 2.0
SEAM_STEP_FLOOR = 8.0


# ------------------------------------------------------------------------------------------
# Colour and energy profiles
# ------------------------------------------------------------------------------------------


def lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 (H, W, 3) to CIE L*a*b* (D65)."""
    c = rgb[..., :3].astype(np.float32) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array(
        [[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]],
        np.float32,
    )
    xyz = c @ m.T / np.array([0.9505, 1.0, 1.089], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack(
        [116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1
    )


def glass_mask(lab_image: np.ndarray, ppm: float = PPM) -> np.ndarray:
    """Window panes and dark door leaves: near-neutral pixels much darker than the bright part
    of their own row, closed over reflections and glazing bars, without specks and thin lines.

    Glass is near-neutral, so a red or ochre wall darker than its white trim is not glass.
    The row's 75th lightness percentile stands for its wall, so a dark ground floor, plinth or
    shadow line (dark across the row) is not glass, and a shop-window row still is.
    """
    from scipy import ndimage

    lightness = lab_image[..., 0]
    chroma = np.hypot(lab_image[..., 1], lab_image[..., 2])
    wall = np.percentile(lightness, 75, axis=1, keepdims=True)
    dark = (lightness < np.minimum(wall * GLASS_RATIO, wall - 15)) & (chroma < GLASS_CHROMA)
    closed = ndimage.binary_fill_holes(ndimage.binary_closing(dark, np.ones((3, 3))))
    labels, count = ndimage.label(closed)
    if not count:
        return closed
    boxes = ndimage.find_objects(labels)
    keep = np.zeros(count + 1, bool)
    for index, box in enumerate(boxes, start=1):
        height, width = box[0].stop - box[0].start, box[1].stop - box[1].start
        keep[index] = height >= 0.25 * ppm and width >= 0.2 * ppm
    return keep[labels]


def smooth(values: np.ndarray, radius: int = 1) -> np.ndarray:
    if radius < 1 or len(values) < 2 * radius + 1:
        return values
    padded = np.pad(values, radius, mode="edge")
    kernel = np.ones(2 * radius + 1) / (2 * radius + 1)
    return np.convolve(padded, kernel, mode="valid")


def _deviation(lab_image: np.ndarray, glass: np.ndarray, axis: int) -> np.ndarray:
    """ΔE of each pixel to the dominant non-glass colour of its row (axis=1) or column (axis=0);
    glass pixels count as GLASS_ENERGY, so dense window rows cannot pass for wall."""
    masked = np.where(glass[..., None], np.nan, lab_image)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-glass rows: plain median below
        reference = np.nanmedian(masked, axis=axis, keepdims=True)
    reference = np.where(
        np.isnan(reference), np.median(lab_image, axis=axis, keepdims=True), reference
    )
    deviation = np.linalg.norm(lab_image - reference, axis=-1)
    return np.where(glass, np.maximum(deviation, GLASS_ENERGY), deviation)


def column_energy(lab_band: np.ndarray, glass: np.ndarray) -> np.ndarray:
    """Mean deviation of each column from its rows' wall colour (piers and bands score low)."""
    return smooth(_deviation(lab_band, glass, 1).mean(0))


def row_energy(lab_image: np.ndarray, glass: np.ndarray) -> np.ndarray:
    """Mean deviation of each row from its columns' wall colour (spandrels score low)."""
    return smooth(_deviation(lab_image, glass, 0).mean(1))


def runs(flags) -> list[tuple[int, int]]:
    out, start = [], None
    for i, flag in enumerate([*list(flags), False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i))
            start = None
    return out


@dataclass
class Axis:
    """Openings and cut positions along one image axis (index 0 = left or top).

    `cuts` holds one plain-wall position in every gap between neighbouring openings, plus one
    before the first and after the last opening when the edge gap is wide enough (`pre`, `post`).
    """

    length: int
    openings: list[tuple[int, int]]
    cuts: list[int]
    pre: bool
    post: bool

    @property
    def flat(self) -> bool:
        return not self.openings

    def to_json(self) -> dict:
        return {
            "length_px": self.length,
            "openings_px": [list(p) for p in self.openings],
            "cuts_px": self.cuts,
            "pre": self.pre,
            "post": self.post,
        }

    @classmethod
    def from_json(cls, value: dict) -> Axis:
        return cls(
            value["length_px"],
            [tuple(p) for p in value["openings_px"]],
            list(value["cuts_px"]),
            value["pre"],
            value["post"],
        )


def _gap_cut(energy: np.ndarray, free: np.ndarray, a: int, b: int) -> int | None:
    """The calmest free position of a gap, nearest its centre among near-minimal ones."""
    positions = np.arange(a, b)[free[a:b]]
    if not len(positions):
        return None
    inner = energy[positions]
    calm = positions[inner <= inner.min() + 0.25 * (inner.max() - inner.min()) + 1e-6]
    return int(calm[np.argmin(np.abs(calm - (a + b - 1) / 2))])


def free_positions(glass: np.ndarray, axis: int) -> np.ndarray:
    """Positions c (0..length) where a cut between c-1 and c crosses no opening: at most a few
    glass pixels (a pipe, a thin shadow line) are on both sides of the cut line.

    axis=1: column cuts through the given rows; axis=0: row cuts through all columns."""
    mask = glass if axis == 1 else glass.T
    crossing = (mask[:, :-1] & mask[:, 1:]).sum(0)
    limit = max(CROSSING_MIN_PX, CROSSING_SHARE * mask.shape[0])
    return np.concatenate([[True], crossing <= limit, [True]])


def read_axis(
    energy: np.ndarray, glass: np.ndarray, free: np.ndarray, ppm: float, *, merge_m, min_m
) -> Axis:
    """Openings are glass runs (gaps below merge_m joined, runs below min_m dropped) plus every
    stretch a cut would cross glass in; one cut per gap between them, at its calmest position."""
    n = len(energy)
    # Thin dark lines (cornice shadows, joints) are not openings; transoms inside one are merged.
    level = max(OPENING_MIN, OPENING_LEVEL * float(glass.max(initial=0)))
    found = [(a, b) for a, b in runs(glass > level) if b - a >= 0.12 * ppm]
    merged: list[list[int]] = []
    for a, b in found:
        if merged and a - merged[-1][1] < merge_m * ppm:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    openings = [(a, b) for a, b in merged if b - a >= min_m * ppm]
    # Any stretch a cut line would cross glass in is part of an opening, however small.
    occupied = np.zeros(n, bool)
    for a, b in openings:
        occupied[a:b] = True
    blocked = np.flatnonzero(~free[1:n]) + 1
    occupied[blocked - 1] = True
    occupied[blocked] = True
    openings = runs(occupied)
    if not openings:
        return Axis(n, [], [], False, False)
    edge = 2
    pre = openings[0][0] >= edge
    post = n - openings[-1][1] >= edge
    gaps = [(b, c) for (_, b), (c, _) in zip(openings, openings[1:], strict=False)]
    if pre:
        gaps.insert(0, (0, openings[0][0]))
    if post:
        gaps.append((openings[-1][1], n))
    cuts = [_gap_cut(energy, free, max(a, 1), min(b, n - 1)) for a, b in gaps]
    if pre and cuts[0] is None:
        pre = False
    if post and cuts[-1] is None:
        post = False
    cuts = [c for c in cuts if c is not None]
    if not cuts:
        pre = post = False
    return Axis(n, openings, cuts, pre, post)


# ------------------------------------------------------------------------------------------
# One-axis composition
# ------------------------------------------------------------------------------------------


def _filler(axis: Axis) -> np.ndarray:
    """Plain wall indices to repeat: the middle of the widest wall gap between openings (a pier,
    or a spandrel), walked forward and back (a mirrored repeat has no seam; derive_maps flips
    the normal map's slope there)."""
    n = axis.length
    if not axis.openings:
        return np.arange(n)
    gaps = [(b, c) for (_, b), (c, _) in zip(axis.openings, axis.openings[1:], strict=False)]
    gaps = [g for g in gaps if g[1] - g[0] >= 3] or [
        g for g in ((0, axis.openings[0][0]), (axis.openings[-1][1], n)) if g[1] - g[0] >= 3
    ]
    if not gaps:
        return np.array([axis.cuts[0] if axis.cuts else n // 2])
    a, b = max(gaps, key=lambda g: g[1] - g[0])
    trim = max(1, round(0.2 * (b - a)))
    forward = np.arange(a + trim, b - trim) if b - a > 2 * trim + 1 else np.arange(a, b)
    return np.concatenate([forward, forward[-2:0:-1]]) if len(forward) > 2 else forward


def _repeat(indices: np.ndarray, length: int) -> np.ndarray:
    if length <= 0:
        return np.zeros(0, int)
    return np.resize(indices, length)


def pieces(axis: Axis, storeys: bool):
    """Start piece, repeatable interior pieces and end piece as index ranges.

    Bays (columns): an edge opening without a wall gap is a cropped window and is dropped.
    Storeys (rows): the image top and bottom are the wall's own edges; the start piece keeps
    the cornice, the end piece keeps the whole ground floor and plinth.
    """
    n, cuts = axis.length, axis.cuts
    if storeys:
        lowest = [c for c in cuts if c <= axis.openings[-1][0]]
        tail = lowest[-1] if len(lowest) >= 1 and (len(axis.openings) > 1 or axis.pre) else 0
        head = cuts[0] if cuts and cuts[0] <= tail else tail
        inner = [c for c in cuts if head <= c <= tail]
        return (0, head), list(zip(inner, inner[1:], strict=False)), (tail, n)
    start = (0, cuts[0]) if axis.pre else (0, 0)
    end = (cuts[-1], n) if axis.post else (n, n)
    return start, list(zip(cuts, cuts[1:], strict=False)), end


def compose(axis: Axis, target: float, *, storeys: bool = False) -> dict:
    """Source indices (natural size) whose resample to `target` source pixels fills the wall.

    The interior repeats in source order (storeys from the bottom up) to the count whose total
    is nearest the target; the remainder is a resample of at most MAX_STRETCH, else pier padding.
    """
    n = axis.length
    target = max(1.0, float(target))
    if axis.flat:
        return flat(n, target)
    (s0, s1), interior, (e0, e1) = pieces(axis, storeys)
    start, end = np.arange(s0, s1), np.arange(e0, e1)
    order = interior[::-1] if storeys else interior
    fixed = len(start) + len(end)
    if fixed > target * (1 + MAX_STRETCH) or (not order and fixed < target / 2):
        return plain(axis, target, storeys=storeys)
    best = None
    for count in range(0, 401):
        if count and not order:
            break
        seq = [order[i % len(order)] for i in range(count)]
        total = fixed + sum(b - a for a, b in seq)
        error = abs(np.log(target / max(total, 1)))
        if best is None or error < best[0]:
            best = (error, seq, total)
        if total > target * (1 + MAX_STRETCH):
            break
    _, seq, total = best
    if not seq:
        # Not even one bay or storey fits: the plain wall between the corner pieces.
        return plain(axis, target, storeys=storeys)
    if storeys:
        seq = seq[::-1]
    parts = [start, *(np.arange(a, b) for a, b in seq), end]
    if total < target / (1 + MAX_STRETCH):
        # The interior cannot reach the target without over-stretching: pad with plain wall.
        pad = _repeat(_filler(axis), round(target / (1 + MAX_STRETCH / 2) - total))
        middle = max(1, len(parts) // 2) if not storeys else len(parts) - 1
        parts = parts[:middle] + [pad] + parts[middle:]
    indices = np.concatenate([p for p in parts if len(p)])
    return {"indices": indices, "seams": _seams(indices), "mode": "bays", "repeats": len(seq)}


def flat(n: int, target: float) -> dict:
    """An axis without openings (blank wall): resampled when close, else its outer thirds kept
    (plinth and cornice, or corners) with the middle walked forward and back between them."""
    target = max(1, round(target))
    if abs(np.log(target / n)) <= np.log(1 + MAX_STRETCH):
        indices = np.arange(n)
    else:
        edge = min(n // 3, target // 3)
        middle = np.arange(edge, n - edge) if n - 2 * edge >= 3 else np.arange(n)
        walk = np.concatenate([middle, middle[-2:0:-1]]) if len(middle) > 2 else middle
        fill = _repeat(walk, target - 2 * edge)
        indices = np.concatenate([np.arange(edge), fill, np.arange(n - edge, n)])
    return {"indices": indices, "seams": _seams(indices), "mode": "flat"}


def plain(axis: Axis, target: float, *, storeys: bool = False) -> dict:
    """Plain wall: the edge bands outside every opening (corners, or cornice and plinth) and
    repeated calm wall between them."""
    n = axis.length
    target = max(1, round(target))
    head = np.arange(0, axis.cuts[0]) if axis.pre else np.zeros(0, int)
    tail = np.arange(axis.cuts[-1], n) if axis.post else np.zeros(0, int)
    if len(head) + len(tail) > target * 0.7:
        # Keep the plinth and cornice (or corners) in proportion on a very small wall.
        head = head[: round(target * (0.25 if storeys else 0.35))]
        keep = round(target * (0.45 if storeys else 0.35))
        tail = tail[len(tail) - keep :] if keep and len(tail) else np.zeros(0, int)
    fill = _repeat(_filler(axis), target - len(head) - len(tail))
    indices = np.concatenate([head, fill, tail]).astype(int)
    return {
        "indices": indices,
        "seams": _seams(indices),
        "mode": "plain",
        "head": len(head),
        "tail": len(tail),
    }


def _seams(indices: np.ndarray) -> list[int]:
    """Output positions where the source index jumps (a mirrored walk continues by -1)."""
    return [int(i) for i in np.flatnonzero(np.abs(np.diff(indices)) != 1) + 1]


def _mirrored(indices: np.ndarray) -> np.ndarray:
    """True where the source is walked backwards (a mirrored filler)."""
    step = np.diff(indices, prepend=indices[0] - 1, append=indices[-1] + 1)
    backward = (step[:-1] == -1) | (step[1:] == -1)
    return backward & ~((step[:-1] == 1) & (step[1:] == 1))


# ------------------------------------------------------------------------------------------
# Style record
# ------------------------------------------------------------------------------------------


def read_structure(albedo: np.ndarray, ppm: tuple[float, float]) -> dict:
    """Window rows, the ground-floor line and each section's bay columns from an albedo map."""
    image = lab(albedo)
    glass = glass_mask(image)
    height = albedo.shape[0]
    rows = read_axis(
        row_energy(image, glass),
        glass.mean(1),
        free_positions(glass, 0),
        ppm[1],
        merge_m=0.35,
        min_m=0.4,
    )
    ground_top = height
    if len(rows.openings) > 1:
        ground_top = pieces(rows, True)[2][0]
    if ground_top < MIN_SECTION_PX or height - ground_top < MIN_SECTION_PX:
        ground_top = height
    sections = {"upper": (0, ground_top), "ground": (ground_top, height)}
    columns = {}
    for name, (a, b) in sections.items():
        if b - a >= MIN_SECTION_PX:
            columns[name] = read_axis(
                column_energy(image[a:b], glass[a:b]),
                glass[a:b].mean(0),
                free_positions(glass[a:b], 1),
                ppm[0],
                merge_m=0.3,
                min_m=0.25,
            )
    return {"rows": rows, "ground_top_px": int(ground_top), "columns": columns, "glass": glass}


def wall_colour(albedo: np.ndarray, glass: np.ndarray) -> dict:
    image = lab(albedo)
    deviation = np.linalg.norm(image - np.median(image, axis=1, keepdims=True), axis=-1)
    calm = (deviation < np.percentile(deviation, 40)) & ~glass
    pixels = albedo[..., :3][calm] if calm.any() else albedo[..., :3].reshape(-1, 3)
    srgb = np.median(pixels, axis=0).astype(int)
    lab_pixels = image[calm] if calm.any() else image.reshape(-1, 3)
    return {
        "srgb": [int(v) for v in srgb],
        "hex": "#" + "".join(f"{int(v):02x}" for v in srgb),
        "lab": [round(float(v), 1) for v in np.median(lab_pixels, axis=0)],
        "texture_l_std": round(float(lab_pixels[:, 0].std()), 2),
    }


def metres(px: float, ppm: float) -> float:
    return round(float(px) / ppm, 3)


def style_record(albedo: np.ndarray, meta: dict, attributes: dict | None = None) -> dict:
    """Style JSON for a building from its source map's albedo and published sidecar."""
    ppm_x, ppm_y = meta["pixels_per_metre"]
    structure = read_structure(albedo, (ppm_x, ppm_y))
    rows: Axis = structure["rows"]
    height = albedo.shape[0]
    ground_top = structure["ground_top_px"]
    upper = structure["columns"].get("upper")

    def spacing(openings):
        centres = [(a + b) / 2 for a, b in openings]
        return list(np.diff(centres))

    storeys = spacing(rows.openings)
    bays = spacing(upper.openings) if upper is not None else []
    cornice = metres(rows.openings[0][0], ppm_y) if rows.openings else None
    plinth = metres(height - rows.openings[-1][1], ppm_y) if rows.openings else None
    windows = upper.openings if upper is not None else []
    answer = (meta.get("generated_qa") or {}).get("visual_qa_answer") or {}
    return {
        "version": STYLE_VERSION,
        "code": code_hash(),
        "building_id": meta["building_id"],
        "source": f"{meta['building_id']}_{meta['facade_index']}",
        "source_key": meta["key"],
        "source_mode": meta.get("source_mode"),
        "source_files": {k: meta["files"][k] for k in MAP_KINDS},
        "source_hashes": {meta["files"][k]: meta["hashes"][meta["files"][k]] for k in MAP_KINDS},
        "source_size_px": [int(albedo.shape[1]), int(height)],
        "source_size_m": [meta["width_m"], meta["height_m"]],
        "pixels_per_metre": [ppm_x, ppm_y],
        "attributes": attributes or {},
        "wall": wall_colour(albedo, structure["glass"]),
        "bands": {
            "cornice_m": cornice,
            "ground_floor_m": metres(height - ground_top, ppm_y),
            "plinth_m": plinth,
            "storey_m": metres(float(np.median(storeys)), ppm_y) if storeys else None,
            "window_rows_read": len(rows.openings),
        },
        "window_module": {
            "bay_m": metres(float(np.median(bays)), ppm_x) if bays else None,
            "openings_per_row": len(windows),
            "window_width_m": (
                metres(float(np.median([b - a for a, b in windows])), ppm_x) if windows else None
            ),
            "window_rows": [
                {"lintel_m": metres(height - a, ppm_y), "sill_m": metres(height - b, ppm_y)}
                for a, b in rows.openings
            ],
        },
        "reviewer_reading": {
            k: answer[k] for k in ("storeys", "upper_openings", "grid_matches") if k in answer
        },
        "plan": {
            "rows": rows.to_json(),
            "ground_top_px": ground_top,
            "columns": {name: axis.to_json() for name, axis in structure["columns"].items()},
        },
    }


# ------------------------------------------------------------------------------------------
# Derivation
# ------------------------------------------------------------------------------------------


def code_hash() -> str:
    """Hash of the code that turns a source map into derived maps (this module and staging)."""
    here = Path(__file__).parent
    return hashlib.sha256(
        b"".join((here / name).read_bytes() for name in ("facade_style.py", "facade_derive.py"))
    ).hexdigest()


def _resize(array: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    channels = [
        np.asarray(
            Image.fromarray(array[..., c].astype(np.float32), mode="F").resize(size, Image.BILINEAR)
        )
        for c in range(array.shape[-1])
    ]
    return np.stack(channels, -1)


def row_groups(
    style: dict, width_m: float, height_m: float, *, plain_columns: bool = False
) -> tuple[dict, list[dict]]:
    """The vertical plan and, top to bottom, contiguous row groups with their column plans.

    Bays and storeys: each source section (upper storeys, ground floor) gets its own bay plan.
    Plain (lower than one storey): cornice and plinth bands span the wall; the wall between
    them is calm wall repeated in both directions.
    """
    ppm_x, ppm_y = style["pixels_per_metre"]
    plan = style["plan"]
    rows = Axis.from_json(plan["rows"])
    columns = {name: Axis.from_json(v) for name, v in plan["columns"].items()}
    vertical = compose(rows, height_m * ppm_y, storeys=True)
    row_index = vertical["indices"]
    target = width_m * ppm_x
    if vertical["mode"] == "plain":
        head, tail = vertical["head"], vertical["tail"]
        labels = ["band"] * head + ["wall"] * (len(row_index) - head - tail) + ["band"] * tail
    else:
        labels = ["ground" if r >= plan["ground_top_px"] else "upper" for r in row_index]
    wall_axis = columns.get("upper") or columns["ground"]
    plans = {}
    groups = []
    for start, stop in runs_of(labels):
        label = labels[start]
        if label not in plans:
            if label == "band":
                plans[label] = flat(wall_axis.length, target)
            elif label == "wall" or plain_columns:
                plans[label] = plain(columns.get(label, wall_axis), target)
            else:
                plans[label] = compose(columns[label], target)
        groups.append({"label": label, "rows": [start, stop], **plans[label]})
    return {**vertical, "labels": labels}, groups


def runs_of(labels: list[str]) -> list[tuple[int, int]]:
    out = []
    for i, label in enumerate(labels):
        if out and labels[out[-1][0]] == label and out[-1][1] == i:
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


def derive_maps(
    style: dict,
    maps: dict[str, np.ndarray],
    width_m: float,
    height_m: float,
    *,
    plain_columns: bool = False,
):
    """Cut the source maps along the style plan to the measured wall; returns maps and plan.

    plain_columns repeats calm wall columns instead of bays (the fallback when a bay plan
    fails its QA)."""
    size = metric_size(width_m, height_m, PPM)
    vertical, groups = row_groups(style, width_m, height_m, plain_columns=plain_columns)
    row_index = vertical["indices"]
    out = {}
    for kind in MAP_KINDS:
        source = maps[kind].astype(np.float32)
        stacked = source[row_index]
        if kind == "normal":
            # A backward walk mirrors the surface: its slope along that axis changes sign.
            flip = _mirrored(row_index)
            stacked[flip, :, 1] = 255 - stacked[flip, :, 1]
        parts = []
        for group in groups:
            a, b = group["rows"]
            used = group["indices"]
            block = stacked[a:b][:, used]
            if kind == "normal":
                flip = _mirrored(used)
                block[:, flip, 0] = 255 - block[:, flip, 0]
            parts.append(_resize(block, (size[0], b - a)))
        result = _resize(np.concatenate(parts, 0), size)
        if kind == "normal":
            vector = result[..., :3] / 127.5 - 1
            vector /= np.maximum(np.linalg.norm(vector, axis=-1, keepdims=True), 1e-6)
            result = (vector + 1) * 127.5
        out[kind] = np.clip(np.rint(result[..., :3]), 0, 255).astype(np.uint8)
    derived_plan = {
        "size": list(size),
        "plain_columns": plain_columns,
        "rows": {
            "mode": vertical["mode"],
            "seams": vertical["seams"],
            "indices": [int(v) for v in row_index],
        },
        "groups": [
            {
                **{k: v for k, v in g.items() if k != "indices"},
                "indices": [int(v) for v in g["indices"]],
            }
            for g in groups
        ],
    }
    return out, derived_plan


def _boundaries(indices: np.ndarray, length: int) -> list[int]:
    """Source boundaries a derived axis cuts: every jump, and output edges inside the source."""
    found = []
    for p in _seams(indices):
        found += [int(indices[p - 1]) + 1, int(indices[p])]
    if indices[0] != 0:
        found.append(int(indices[0]))
    if indices[-1] != length - 1:
        found.append(int(indices[-1]) + 1)
    return found


def _step(image: np.ndarray, a: int, b: int, axis: int, span: np.ndarray) -> float:
    if axis == 1:
        return float(np.median(np.linalg.norm(image[span, a] - image[span, b], axis=-1)))
    return float(np.median(np.linalg.norm(image[a, span] - image[b, span], axis=-1)))


def derived_qa(style: dict, source_albedo: np.ndarray, derived: dict, width_m, height_m) -> dict:
    """No cut windows at any seam or kept edge, bands continuous across seams, exact aspect."""
    image = lab(source_albedo)
    glass = glass_mask(image)
    height, width = image.shape[:2]
    row_index = np.array(derived["rows"]["indices"])
    cut_windows, ratios = [], [0.0]
    for group in derived["groups"]:
        a, b = group["rows"]
        rows_used = np.unique(row_index[a:b])
        span = (int(rows_used.min()), int(rows_used.max()) + 1)
        indices = np.array(group["indices"])
        cut_windows += [
            f"column {c} ({group['label']})"
            for c in _boundaries(indices, width)
            if not free_positions(glass[span[0] : span[1]], 1)[c]
        ]
        joins = [(int(indices[p - 1]), int(indices[p])) for p in group["seams"]]
        if joins:
            ends = sorted({c for pair in joins for c in pair})
            pairs = [_step(image, x, y, 1, rows_used) for x in ends for y in ends if x < y]
            allowed = max(
                SEAM_STEP_FLOOR, SEAM_STEP_MAX * float(np.percentile(pairs, 90) if pairs else 0)
            )
            ratios += [_step(image, x, y, 1, rows_used) / allowed for x, y in joins]
    columns_used = np.unique(np.concatenate([np.array(g["indices"]) for g in derived["groups"]]))
    full = (int(columns_used.min()), int(columns_used.max()) + 1)
    cut_windows += [
        f"row {r}"
        for r in _boundaries(row_index, height)
        if not free_positions(glass[:, full[0] : full[1]], 0)[r]
    ]
    joins = [(int(row_index[p - 1]), int(row_index[p])) for p in derived["rows"]["seams"]]
    if joins:
        ends = sorted({r for pair in joins for r in pair})
        pairs = [_step(image, x, y, 0, columns_used) for x in ends for y in ends if x < y]
        allowed = max(
            SEAM_STEP_FLOOR, SEAM_STEP_MAX * float(np.percentile(pairs, 90) if pairs else 0)
        )
        ratios += [_step(image, x, y, 0, columns_used) / allowed for x, y in joins]
    problems = []
    if cut_windows:
        problems.append("a seam or edge cuts an opening: " + ", ".join(cut_windows[:4]))
    if max(ratios) > 1:
        problems.append(f"band discontinuity at a seam ({max(ratios):.2f} of the allowed step)")
    size = derived["size"]
    scale_error = abs(size[0] / width_m / (size[1] / height_m) - 1)
    exact = list(metric_size(width_m, height_m, PPM)) == list(size) and scale_error <= 0.03
    if not exact:
        problems.append("derived size does not match the measured wall")
    return {
        "cut_openings": len(cut_windows),
        "seam_step_ratio_max": round(max(ratios), 3),
        "seams": len(derived["rows"]["seams"]) + sum(len(g["seams"]) for g in derived["groups"]),
        "scale_relative_error": scale_error,
        "aspect_exact": exact,
        "problems": problems,
        "pass": not problems,
    }
