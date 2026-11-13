"""The colour of each distinguishable part of a house, from rectified wall crops and their label maps.

Geometry says where a part can be (the wall's own plane from LoD2: its seen stretch, its foot, its eaves and gable),
the label map of `colour_segment.py` says what is in front (vegetation, vehicles, fences, people) and where windows
and doors are, and colour clustering in CIELAB inside what is left says which paints there are: the cladding (one
zone or two), trim (a thin second colour), window frames (the ring round the glass), the plinth (the band at the
foot that is not the cladding's colour), the roof (above the eaves of an eaves-side wall) and the door.

Colours here are pixel colours (CIELAB of the photograph); `colour.calibrated` brings them to the paint scale.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import colour
from .colour_segment import LABELS

PARTS = ("wall", "wall2", "trim", "frame", "plinth", "roof", "door")
L = {name: k for k, name in enumerate(LABELS)}
HOUSE = (L["building"], L["window"], L["door"], L["attached"], L["roof"])
IN_FRONT = ("vegetation", "vehicle", "person", "fence", "pole")
CLUSTER_DE = 9.0
MIN_PART_PX = 250
MIN_VISIBLE = 0.04  # a crop showing less of its wall than this share, or than MIN_WALL_M2, measures nothing
MIN_WALL_M2 = 1.5  # a strip of boards over a hedge is enough for a colour
ENOUGH_FREE = 0.3  # beyond this share of free wall a crop gains nothing for colour
NEAR_BEST = 0.45  # crops scoring under this share of the best one do not vote (a sunny view beside an overcast one)
PLINTH_MAX_M = 1.6
PLINTH_MIN_M = 0.25  # a lower band than this is a kerb, a shadow line or a car's roof
DOOR_TOP_M = 2.6  # a colour that keeps below this while the wall above is another is a garage or shop door
STORED = ("wall", "wall2", "trim", "frame", "plinth", "door")  # the roof is not: see `combine`
REPEATS_DE = 8.0  # a part other than the wall counts when two panoramas give it within this distance
WHITE_L = 93.0  # CIELAB lightness of white paint under cloud, the reference a visit's white point is brought to
SHADE_AB = 6.0  # a second colour nearer than this in a*, b* and not lighter is the same paint in shade
TAG = "m2f"


def mode(pixels: np.ndarray, radius: float = CLUSTER_DE) -> tuple[np.ndarray, np.ndarray] | None:
    """The colour most pixels share and which pixels are within `radius` of it (CIELAB rows)."""
    if len(pixels) < MIN_PART_PX:
        return None
    cell = np.array([5.0, 4.0, 4.0])
    cells = np.floor(pixels / cell).astype(np.int64)
    low = cells.min(0)
    shape = cells.max(0) - low + 3
    grid = np.zeros(shape)
    np.add.at(grid, tuple((cells - low + 1).T), 1)
    smooth = sum(np.roll(grid, shift, axis) for axis in range(3) for shift in (-1, 1)) + 2 * grid
    centre = (np.array(np.unravel_index(smooth.argmax(), shape)) + low - 1 + 0.5) * cell
    near = np.zeros(len(pixels), bool)
    for _ in range(3):
        near = np.linalg.norm(pixels - centre, axis=1) <= radius
        if near.sum() < MIN_PART_PX / 4:
            return None
        centre = np.median(pixels[near], axis=0)
    return centre, near


def wall_mode(pixels: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """The cladding's colour among the wall's pixels (CIELAB rows, their heights in metres above the foot).

    The densest colour is not always the wall's: planks in overcast light spread their paint over a wider cluster
    than a smooth grey (a red shed's boards lose to its grey roof edge), and a garage or shop door is one smooth
    colour at the foot of the wall. So: of the two most common colours the one with more pixels, and a colour that
    keeps under door height (DOOR_TOP_M) while the wall above it is another colour is the door, not the wall."""
    first = mode(pixels)
    if first is None:
        return None
    centre, near = first
    second = mode(pixels[~near]) if (~near).sum() >= MIN_PART_PX else None
    shade = second is not None and np.linalg.norm(second[0][1:] - centre[1:]) < SHADE_AB and second[0][0] < centre[0]
    if second is not None and not shade and np.linalg.norm(second[0] - centre) >= 10.0 and second[1].sum() > near.sum():
        centre = second[0]
        near = np.linalg.norm(pixels - centre, axis=1) <= CLUSTER_DE
    high = z > DOOR_TOP_M + 0.2
    if np.quantile(z[near], 0.9) <= DOOR_TOP_M and high.sum() >= max(MIN_PART_PX, 0.15 * len(pixels)):
        upper = mode(pixels[high])
        if upper is not None and np.linalg.norm(upper[0] - centre) >= 10.0:
            centre = upper[0]
            near = np.linalg.norm(pixels - centre, axis=1) <= CLUSTER_DE
    return centre, near


def _box(mask: np.ndarray, px: int, axis: int) -> np.ndarray:
    """Whether any pixel within `px` along one axis is set (running sums: the cost does not grow with px)."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (px + 1, px)
    sums = np.cumsum(np.pad(mask.astype(np.int32), pad), axis=axis)
    n = mask.shape[axis]
    return (np.take(sums, np.arange(2 * px + 1, 2 * px + 1 + n), axis=axis) - np.take(sums, np.arange(n), axis=axis)) > 0


def _grow(mask: np.ndarray, px: int) -> np.ndarray:
    """The mask grown by `px` pixels each way (a square; no scipy in the hosts' pipeline environment)."""
    return _box(_box(mask, px, 0), px, 1) if px > 0 else mask.copy()


def _part(lab_pixels: np.ndarray, near: np.ndarray, centre: np.ndarray, of: int) -> dict:
    members = lab_pixels[near]
    return {"lab": [round(float(v), 1) for v in centre], "px": int(near.sum()),
            "share": round(float(near.sum() / max(1, of)), 2),
            "spread": round(float(np.linalg.norm(members - centre, axis=1).mean()), 1),
            "light_range": round(float(np.quantile(members[:, 0], 0.9) - np.quantile(members[:, 0], 0.1)), 1)}


def white_of(pixels: np.ndarray) -> list | None:
    """The lightest paint of a house in a crop (CIELAB rows of its `building` pixels): the median of the pixels at
    or above the 97th percentile of lightness that are not burnt out. On most houses that is white trim, so over
    the crops of one visit of a camera car it tells the exposure and the colour of the light (`visit_white`)."""
    if len(pixels) < 4 * MIN_PART_PX:
        return None
    top = pixels[(pixels[:, 0] >= np.quantile(pixels[:, 0], 0.97)) & (pixels[:, 0] < 99.0)]
    if len(top) < MIN_PART_PX / 5:
        return None
    return [round(float(v), 1) for v in np.median(top, axis=0)]


def eaves_in_picture(lab: np.ndarray, plain: np.ndarray, inside: np.ndarray, z: np.ndarray, eaves: float,
                     ppm: float, reach: float = 1.2) -> tuple[float, float] | None:
    """Where the wall's colour ends and where the roof's begins, in metres above the wall's foot: the lowest and
    the highest strong change of the row colour within `reach` of the LoD2 eaves (between them the eaves board,
    soffit and gutter). None when no row changes colour there (a hidden eaves, a wall and roof alike)."""
    rows = np.flatnonzero((z >= eaves - reach) & (z <= eaves + reach))
    if len(rows) < 8:
        return None
    colours, held = np.full((len(rows), 3), np.nan), np.zeros(len(rows), bool)
    for k, r in enumerate(rows):
        mask = plain[r] & inside
        if mask.sum() >= 0.25 * inside.sum():
            colours[k], held[k] = np.median(lab[r][mask], axis=0), True
    band = max(2, round(0.25 * ppm))
    steps = []
    for k in range(band, len(rows) - band):
        below, above = colours[k + 1:k + 1 + band][held[k + 1:k + 1 + band]], colours[k - band:k][held[k - band:k]]
        if len(below) >= band // 2 and len(above) >= band // 2:  # rows run downwards: larger index is lower
            steps.append((k, float(np.linalg.norm(np.median(below, axis=0) - np.median(above, axis=0)))))
    strong = [k for k, d in steps if d >= 12.0]
    if not strong:
        return None
    wall_top, roof_from = float(z[rows[max(strong)]]), float(z[rows[min(strong)]])
    return wall_top + 0.15 - 0.5 * band / ppm, roof_from + 0.5 * band / ppm + 0.1


def of_crop(picture: np.ndarray, labels: np.ndarray, crop: dict, wall: dict) -> dict:
    """Parts of one wall in one crop. Returns {'visible', 'in_front': {class: share}, 'parts': {name: {...}},
    'map': uint8 image of part indices (0 none, 1.. PARTS order, 255 in front)}."""
    h, w = labels.shape
    ppm = crop["px_per_m"]
    x = crop["x_left_m"] + (np.arange(w) + 0.5) / ppm
    z = crop["z_high_m"] - (np.arange(h) + 0.5) / ppm
    eaves, top = wall["eaves_h"] - wall["base_h"], wall["top_h"] - wall["base_h"]
    gable = top - eaves > 1.0
    edge = eaves + (top - eaves) * np.clip(1 - np.abs(2 * x / max(wall["length_m"], 0.1) - 1), 0, 1) if gable \
        else np.full(w, eaves)
    inside = (x >= crop["x0_m"] + 0.2) & (x <= crop["x1_m"] - 0.2)
    X, Z = np.meshgrid(x, z)
    lab = colour.to_lab(picture.reshape(-1, 3)).reshape(h, w, 3)
    roof_from = eaves + 0.35
    if not gable:  # LoD2 eaves are a metre off on some houses: the picture says where the wall's colour ends
        seen = eaves_in_picture(lab, labels == L["building"], inside, z, eaves, ppm)
        if seen is not None:
            eaves, roof_from = seen
            edge = np.full(w, eaves)
    on_wall = inside[None, :] & (Z >= 0.0) & (Z <= edge[None, :] - 0.15)
    out = {"visible": 0.0, "in_front": {}, "parts": {}, "gable": bool(gable), "map": np.zeros((h, w), np.uint8),
           "eaves_m": round(float(eaves), 2), "white": None}
    area = int(on_wall.sum())
    if area < 4 * MIN_PART_PX:
        return out
    house = np.isin(labels, HOUSE)
    out["white"] = white_of(lab[labels == L["building"]])
    out["visible"] = round(float(house[on_wall].mean()), 2)
    out["in_front"] = {name: round(float((labels[on_wall] == L[name]).mean()), 2) for name in IN_FRONT
                       if (labels[on_wall] == L[name]).mean() >= 0.01}
    out["map"][on_wall & ~house] = 255
    out["free_m2"] = round(float((house & on_wall).sum() / ppm ** 2), 1)
    if out["visible"] < MIN_VISIBLE or out["free_m2"] < MIN_WALL_M2:
        return out
    plain = labels == L["building"]
    openings = (labels == L["window"]) | (labels == L["door"])
    ring = _grow(openings, max(2, round(0.14 * ppm))) & ~_grow(openings, 1) & plain & on_wall
    # Pixels at the edge of what stands in front are mixtures of leaf and wall: left out.
    beside = _grow(np.isin(labels, [L[name] for name in IN_FRONT]), max(2, round(0.1 * ppm)))
    cladding = plain & on_wall & ~_grow(openings, max(3, round(0.2 * ppm))) & ~beside

    def record(name: str, mask: np.ndarray, near: np.ndarray, centre: np.ndarray, **more):
        index = np.flatnonzero(mask.ravel())[near]
        out["map"].ravel()[index] = PARTS.index(name) + 1
        out["parts"][name] = {**_part(lab.reshape(-1, 3)[mask.ravel()], near, centre, int(mask.sum())), **more}
        return index

    # The plinth: what the lowest visible rows share, when it is not the cladding's colour.
    whole = mode(lab[cladding])
    if whole is None:
        return out
    wall_colour = whole[0]
    foot = cladding & (Z < PLINTH_MAX_M)
    plinth_top = 0.0
    low = mode(lab[cladding & (Z < 0.45)])
    if low is not None and np.linalg.norm(low[0] - wall_colour) > 8.0:
        is_plinth = np.linalg.norm(lab - low[0], axis=2) <= CLUSTER_DE
        rows = np.flatnonzero(foot.any(1))
        share = np.array([is_plinth[r][foot[r]].mean() for r in rows])
        tall = z[rows][share >= 0.5]
        if len(tall) and tall.max() + 0.5 / ppm >= PLINTH_MIN_M:
            plinth_top = float(min(PLINTH_MAX_M, tall.max() + 0.5 / ppm))
            band = cladding & (Z < plinth_top)
            found = mode(lab[band])
            if found is not None:
                record("plinth", band, found[1], found[0], height_m=round(plinth_top, 2))
    body = cladding & (Z >= plinth_top + 0.08)
    pixels = lab[body]
    first = wall_mode(pixels, Z[body])
    if first is None:
        return out
    centre, near = first
    record("wall", body, near, centre)
    rest = body.copy()
    rest.ravel()[np.flatnonzero(body.ravel())[near]] = False
    # A second colour on the wall: a zone of cladding when it holds together, trim when it is thin lines.
    for _ in range(2):
        second = mode(lab[rest]) if rest.sum() >= 0.06 * body.sum() else None
        if second is None or second[1].sum() < 0.04 * body.sum() or np.linalg.norm(second[0] - centre) < 10.0:
            break
        hue_apart = float(np.linalg.norm(second[0][1:] - centre[1:]))
        if hue_apart < SHADE_AB and second[0][0] < centre[0] + 12.0:
            # The wall's own paint in shade (under the eaves, beside a porch): not a part. How much darker it is
            # tells how hard the light was.
            out["parts"]["wall"]["shade_L"] = round(float(centre[0] - second[0][0]), 1)
            rest.ravel()[np.flatnonzero(rest.ravel())[second[1]]] = False
            continue
        members = np.zeros(h * w, bool)
        members[np.flatnonzero(rest.ravel())[second[1]]] = True
        members = members.reshape(h, w)
        thick = max(2, round(0.18 * ppm))
        core = ~_grow(~members, thick)  # what survives an erosion by 18 cm
        solid = float(core.sum() / max(1, members.sum()))
        rows_z = Z[members]
        if solid >= 0.2 and "wall2" not in out["parts"]:
            where = "gable" if np.median(rows_z) > eaves else "upper" if np.median(rows_z) > np.median(Z[body]) else "lower"
            record("wall2", rest, second[1], second[0], zone=where, solid=round(solid, 2))
        elif solid < 0.2 and "trim" not in out["parts"]:
            record("trim", rest, second[1], second[0], solid=round(solid, 2))
        rest = rest & ~members
    frame = mode(lab[ring])
    if frame is not None:
        record("frame", ring, frame[1], frame[0])
    door = (labels == L["door"]) & on_wall
    found = mode(lab[door])
    if found is not None:
        record("door", door, found[1], found[0])
    if not gable:  # the roof slope shows above the eaves of an eaves-side wall
        above = inside[None, :] & (Z > roof_from) & (Z < roof_from + 1.9) & np.isin(labels, (L["building"], L["roof"]))
        found = mode(lab[above])
        if found is not None and above.sum() >= 0.15 * inside.sum() * 1.9 * ppm:
            record("roof", above, found[1], found[0])
    return out


def score(crop: dict, parts: dict, overcast: float | None) -> float:
    """How good a crop is for colour: overcast light first (a sunny view counts a sixth of an overcast one), then
    free wall up to ENOUGH_FREE (a strip over a hedge under cloud beats a whole wall in sun), then the view's own
    quality (frontal, near, sharp, recent). Overcast unknown counts as half."""
    free = min(1.0, parts["visible"] / ENOUGH_FREE) ** 0.5
    return round((0.15 + 0.85 * (0.5 if overcast is None else overcast)) * free * crop["quality"], 3)


def light_of(row: dict) -> float:
    return 0.5 if row.get("overcast") is None else row["overcast"]


def combine(rows: list[dict], best: int = 3) -> dict:
    """One colour a part from the best crops: the weighted median in CIELAB over the `best` highest-scoring crops
    that hold the part; `agreement` is the weighted mean distance of those crops from it.

    The wall is trusted on its own. Every other part is small or easily hidden, and what makes one wrong (leaf
    shade read as trim, a hedge's foot as a plinth, a neighbour's wall as a roof) differs from panorama to
    panorama: such a part is confident only when two different panoramas give it within REPEATS_DE. A part in the
    wall's own colour is no part. The roof from the street is kept for the eye (sheets) and never confident: the
    slope is seen at a grazing angle and mirrors the sky, a gable side shows none of it, and between panoramas
    of one wall under cloud it scatters by dE 14 to 23; the orthophoto sees every roof from above."""
    out = {}
    for name in PARTS:
        held = sorted((r for r in rows if name in r["parts"]), key=lambda r: -r["score"])[:best]
        held = [r for r in held if r["score"] >= NEAR_BEST * held[0]["score"]]
        if not held:
            continue
        labs = np.array([r["parts"][name]["lab"] for r in held])
        weights = np.array([r["score"] * r["parts"][name]["px"] ** 0.5 for r in held]) + 1e-6
        centre = np.array([labs[o, k][min(len(held) - 1, np.searchsorted(np.cumsum(weights[o]), weights.sum() / 2))]
                           for k, o in ((k, np.argsort(labs[:, k])) for k in range(3))])
        if name != "wall" and "wall" in out and np.linalg.norm(centre - np.array(out["wall"]["lab"])) < (10.0 if name == "wall2" else 8.0) \
                and name not in ("roof", "door"):
            continue
        distance = np.linalg.norm(labs - centre, axis=1)
        agreement = float((distance * weights).sum() / weights.sum())
        top = held[0]
        panoramas = {r["panorama"]["id"] for r in held}
        visible = float(np.mean([r["visible"] for r in held]))
        conf = 0.3 + 0.35 * min(visible, 0.8) / 0.8 + (0.2 if len(panoramas) > 1 and agreement < 6 else 0.0) \
            - (0.2 if agreement > 12 else 0.0) + 0.1 * min(1.0, top["parts"][name]["px"] / 4000)
        conf *= 0.5 + 0.5 * light_of(top)  # a colour only the sun shows is half as sure
        if name == "roof" or (name != "wall" and not (len(panoramas) > 1 and agreement <= REPEATS_DE)):
            conf = min(conf, 0.3)
        whites = [r["white"] for r in held if r.get("white")]
        part = {"lab": [round(float(v), 1) for v in centre], "pixel": colour.to_hex(centre),
                "spread": round(float(np.mean([r["parts"][name]["spread"] for r in held])), 1),
                "share": round(float(np.mean([r["parts"][name]["share"] for r in held])), 2),
                "visible": round(visible, 2), "agreement": round(agreement, 1), "n": len(panoramas),
                "conf": round(float(np.clip(conf, 0.1, 0.95)), 2),
                "from": {**{k: top["panorama"][k] for k in ("provider", "id", "date")}, "overcast": top.get("overcast")}}
        if whites:
            part["white"] = [round(float(v), 1) for v in np.median(np.array(whites), axis=0)]
        for extra in ("zone", "height_m"):
            if extra in top["parts"][name]:
                part[extra] = top["parts"][name][extra]
        out[name] = part
    return out


def visit_of(panorama: dict) -> str:
    return f"{panorama['provider']}:{(panorama.get('date') or '')[:7]}"


def visit_white(results: list[dict], least: int = 6) -> dict:
    """{visit: [L, a, b]}: the white point of each visit of a camera car, the median of its crops' whites
    (`white_of`); `*` is the median over the overcast crops of every visit, for visits with fewer than `least`."""
    by_visit: dict = {}
    for result in results:
        for row in result["candidates"]:
            if row.get("white"):
                by_visit.setdefault(visit_of(row["panorama"]), []).append(row["white"])
                if light_of(row) >= 0.5:
                    by_visit.setdefault("*", []).append(row["white"])
    return {visit: [round(float(v), 1) for v in np.median(np.array(found), axis=0)]
            for visit, found in by_visit.items() if len(found) >= least or visit == "*"}


def paint(lab: list, white: list | None, balance: bool = True, white_l: float = WHITE_L) -> np.ndarray:
    """A pixel colour brought to the paint scale by its visit's white point: in linear light each channel is
    divided by the white's and multiplied by white paint's (exposure and, with `balance`, the colour of the
    light). Without a white point the pixel colour stands."""
    if not white:
        return np.array(lab, float)
    linear = lambda value: colour.to_linear(np.array(value, float))  # noqa: E731
    reference = linear([white_l, 0.0, 0.0])
    seen = linear(white) if balance else linear([white[0], 0.0, 0.0])
    gain = np.clip(reference / np.maximum(seen, 1e-3), 0.8, 2.6)
    return colour.from_linear(np.clip(linear(lab) * gain, 0.0, 1.0))


def calibrate(results: list[dict], whites: dict | None = None) -> dict:
    """Adds `colour` (hex on the paint scale) to every combined part of the results, by the white point of the
    visit its best crop is from. Returns the white points used."""
    whites = whites if whites is not None else visit_white(results)
    for result in results:
        for part in result["parts"].values():
            white = whites.get(visit_of(part["from"])) or (whites.get("*") if (part["from"].get("overcast") or 0) >= 0.5 else None)
            part["colour"] = colour.to_hex(paint(part["lab"], white))
            part["white_point"] = white
    return whites


def measured(result: dict, least: float = 0.0) -> dict:
    """The `measured` block of a stored description (docs/specs/building-description.md) from a calibrated result:
    one measuredColour a part of STORED."""
    block = {}
    for name in STORED:
        part = result["parts"].get(name)
        if not part or "colour" not in part or part["conf"] < least:
            continue
        entry = {"colour": part["colour"], "spread": part["spread"], "share": part["share"],
                 "conf": {"*": part["conf"]}, "visible": part["visible"], "agreement": part["agreement"],
                 "n": part["n"], "pixel": part["pixel"],
                 "from": {k: v for k, v in part["from"].items() if v is not None}}
        for extra in ("zone", "height_m"):
            if extra in part:
                entry[extra] = part[extra]
        block[name] = entry
    if "wall2" in block and "wall" not in block:
        del block["wall2"]
    return block


def of_packet(packet: Path, tag: str = TAG, overcast: dict | None = None, keep_maps: bool = False) -> dict | None:
    """Every crop of a prepared building measured and ranked; the parts combined. None without a views record."""
    if not (packet / "views.json").exists():
        return None
    record = json.loads((packet / "views.json").read_text())
    walls = {w["id"]: w for w in record["walls"]}
    rows = []
    for crop in record["crops"]:
        path = packet / Path(crop["file"]).name
        labels_path = path.with_suffix(f".{tag}.png")
        wall = walls.get(crop["wall"])
        if not path.exists() or not labels_path.exists() or wall is None:
            continue
        picture = colour.picture_of(path)
        labels = np.asarray(Image.open(labels_path))
        if labels.shape != picture.shape[:2]:
            continue
        measured = of_crop(picture, labels, crop, wall)
        pano = crop["panorama"]
        cloud = (overcast or {}).get(f"{pano['provider']}:{pano['id']}")
        row = {"file": path.name, "wall": crop["wall"], "panorama": pano, "distance_m": crop["distance_m"],
               "oblique_deg": crop["oblique_deg"], "quality": crop["quality"], "overcast": cloud,
               "white": measured.get("white"),
               "visible": measured["visible"], "free_m2": measured.get("free_m2", 0.0),
               "in_front": measured["in_front"], "gable": measured["gable"],
               "parts": measured["parts"], "score": score(crop, measured, cloud)}
        if keep_maps:
            row["map"] = measured["map"]
        rows.append(row)
    rows.sort(key=lambda r: -r["score"])
    return {"building": record["building"], "address": record.get("register", {}).get("address"),
            "year": record.get("register", {}).get("year"), "candidates": rows, "parts": combine(rows)}


def measured_packets(root: Path, tag: str = TAG, overcast: dict | None = None) -> list[dict]:
    """Every packet under root/buildings measured once (of_packet)."""
    found = (of_packet(p, tag, overcast) for p in sorted((root / "buildings").iterdir()) if p.is_dir())
    return [r for r in found if r is not None]


def scatter(results: list[dict], part: str = "wall", free: float = 0.5, cloudy: float = 0.5,
            sunny: float = 0.2) -> dict:
    """How far a part's colour scatters between independent panoramas of one wall, under cloud and in sun.

    `results`: measured_packets. Every pair of different panoramas that both show a wall at least `free` is one
    distance (CIELAB, pixel colours): both overcast (>= `cloudy`), both sunny (<= `sunny`), or one of each. Pairs
    inside one visit of a camera car (same provider and month: the same paint, the same weather, another
    standpoint) are counted apart as `overcast_same_visit` and `sunny_same_visit`: only they compare like with
    like, since between visits years pass and houses are repainted. Also per axis what sun does to a colour:
    sunny minus the wall's overcast median."""
    pairs = {"overcast": [], "sunny": [], "mixed": [], "overcast_same_visit": [], "sunny_same_visit": []}
    shift, walls = [], 0
    for result in results:
        by_wall: dict = {}
        for row in result["candidates"]:
            if part in row["parts"] and row["visible"] >= free and row["overcast"] is not None:
                by_wall.setdefault(row["wall"], []).append(row)
        for rows in by_wall.values():
            if len(rows) < 2:
                continue
            walls += 1
            kind = lambda r: "overcast" if r["overcast"] >= cloudy else "sunny" if r["overcast"] <= sunny else None  # noqa: E731
            for i, a in enumerate(rows):
                for b in rows[i + 1:]:
                    if a["panorama"]["id"] == b["panorama"]["id"] or kind(a) is None or kind(b) is None:
                        continue
                    distance = float(np.linalg.norm(np.array(a["parts"][part]["lab"]) - np.array(b["parts"][part]["lab"])))
                    pairs[kind(a) if kind(a) == kind(b) else "mixed"].append(distance)
                    if visit_of(a["panorama"]) == visit_of(b["panorama"]) and kind(a) == kind(b):
                        pairs[kind(a) + "_same_visit"].append(distance)
            under_cloud = [r["parts"][part]["lab"] for r in rows if kind(r) == "overcast"]
            if under_cloud:
                reference = np.median(np.array(under_cloud), axis=0)
                shift += [np.array(r["parts"][part]["lab"]) - reference for r in rows if kind(r) == "sunny"]

    def stats(values: list) -> dict:
        if not values:
            return {"pairs": 0}
        v = np.array(values)
        return {"pairs": len(v), "median": round(float(np.median(v)), 1), "upper_quartile": round(float(np.quantile(v, 0.75)), 1),
                "within_6": round(float((v <= 6).mean()), 2)}

    out = {"part": part, "walls": walls, **{name: stats(values) for name, values in pairs.items()}}
    if shift:
        s = np.array(shift)
        out["sunny_minus_overcast"] = {"n": len(s), "L": round(float(np.median(s[:, 0])), 1), "a": round(float(np.median(s[:, 1])), 1),
                                       "b": round(float(np.median(s[:, 2])), 1),
                                       "spread_L_a_b": [round(float(np.quantile(s[:, k], 0.75) - np.quantile(s[:, k], 0.25)), 1) for k in range(3)]}
    return out
