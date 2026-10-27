"""How well the city lidar's point colours describe a facade.

The city ``pistepilvi`` LAZ carries RGB from the 2021 aerial imagery. For every street wall
with a bound map of the given buildings, the points within 0.6 m of the wall plane
(turku3d.lidar.walls) form a colour layer in 0.5 m cells, which is compared with the map at the
same u, v and with the roof and ground colours next to the wall (smear). Writes measure.json
and one comparison image per generated (or large) wall: the map, its 0.5 m cells, the raw
lidar cells, the inpainted lidar layer (a full per-wall map) and the dominant colour with its
vertical gradient, at one metric scale. Results: docs/reports/textures-lidar-facade-color.md.

    python -m turkutex.facade_lidar --inventory FACADES --assembly A \
        --buildings 103304659B 103304672S --output DIR
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .facade_style import lab

CELL = 0.5  # colour layer cell (metres)
FACADE_DISTANCE = 0.6  # points this close to the wall plane belong to it
# Ground, vegetation, noise and model key points never colour a wall.
EXCLUDED_CLASSES = (2, 3, 4, 5, 7, 8, 18)
GROUND_CLEARANCE = 0.3  # points lower than this above the wall foot are ground
# Context windows for the smear test: the roof just inside the wall top, the ground in front.
ROOF = {"inside": (0.3, 3.0), "above": (-1.5, 3.0)}
GROUND = {"outside": (FACADE_DISTANCE, 3.0), "height": (-0.5, 0.5)}
MIN_CELL_POINTS = 3  # a compared cell has at least this many points
MIN_POINTS = 12  # fewer wall points than this: no colour is read
DEFAULT_MATERIAL = "render_smooth"  # what an unbound wall shows today


def facade_points(records: np.ndarray, width: float, height: float) -> np.ndarray:
    """The wall's own points: on its plane, above the foot, inside the rectangle, and not
    ground, vegetation or noise."""
    keep = (
        (np.abs(records["d"]) <= FACADE_DISTANCE)
        & (records["u"] >= 0)
        & (records["u"] <= width)
        & (records["h"] >= GROUND_CLEARANCE)
        & (records["h"] <= height)
        & ~np.isin(records["classification"], EXCLUDED_CLASSES)
    )
    return records[keep]


def grid_shape(width: float, height: float, cell: float = CELL) -> tuple[int, int]:
    return max(1, math.ceil(height / cell - 1e-9)), max(1, math.ceil(width / cell - 1e-9))


def colour_cells(records: np.ndarray, width: float, height: float, cell: float = CELL):
    """Per-cell median sRGB (rows from the top, NaN where empty) and point counts."""
    rows, cols = grid_shape(width, height, cell)
    r = np.clip(((height - records["h"]) / cell).astype(int), 0, rows - 1)
    c = np.clip((records["u"] / cell).astype(int), 0, cols - 1)
    flat = r * cols + c
    counts = np.bincount(flat, minlength=rows * cols).reshape(rows, cols)
    cells = np.full((rows * cols, 3), np.nan, np.float32)
    if not len(flat):
        return cells.reshape(rows, cols, 3), counts
    order = np.argsort(flat, kind="stable")
    keys, starts = np.unique(flat[order], return_index=True)
    rgb = records["rgb"][order].astype(np.float32)
    for key, a, b in zip(keys, starts, [*starts[1:], len(order)], strict=True):
        cells[key] = np.median(rgb[a:b], axis=0)
    return cells.reshape(rows, cols, 3), counts


def _blur(values: np.ndarray, sigma: float) -> np.ndarray:
    from scipy import ndimage

    return ndimage.gaussian_filter(values, (sigma, sigma, 0)[: values.ndim], mode="nearest")


def inpaint(cells: np.ndarray, counts: np.ndarray, fallback) -> np.ndarray:
    """Fill empty cells by normalized convolution at growing scales; cells no scale reaches
    take the fallback colour. Observed cells keep their value."""
    known = counts > 0
    out = np.where(known[..., None], cells, 0).astype(np.float32)
    if not known.any():
        return np.broadcast_to(np.asarray(fallback, np.float32), cells.shape).copy()
    weight = known.astype(np.float32)
    filled = out.copy()
    todo = ~known
    for sigma in (1.0, 2.0, 4.0, 8.0, 16.0):
        w = _blur(weight, sigma)
        value = _blur(out * weight[..., None], sigma) / np.maximum(w, 1e-6)[..., None]
        reach = todo & (w > 0.05)
        filled[reach] = value[reach]
        todo &= ~reach
        if not todo.any():
            break
    filled[todo] = fallback
    return filled


def denoise(filled: np.ndarray, counts: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Point-count weighted smoothing: a cell of one point leans on its neighbours."""
    weight = np.sqrt(np.minimum(counts, 9)).astype(np.float32) + 0.25
    value = _blur(filled * weight[..., None], sigma) / _blur(weight, sigma)[..., None]
    trust = (np.minimum(counts, 9) / 9.0)[..., None]
    return trust * filled + (1 - trust) * value


def dominant_colour(records: np.ndarray) -> np.ndarray:
    """Median sRGB of the wall's points."""
    return np.median(records["rgb"].astype(np.float32), axis=0)


def vertical_gradient(cells: np.ndarray, counts: np.ndarray, dominant) -> np.ndarray:
    """Per-row multiplier of the dominant colour's lightness (rows from the top), from rows
    with enough points, smoothed over 1.5 m and limited to +-25 %."""
    lum = cells @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    rows = cells.shape[0]
    base = float(np.dot(dominant, [0.2126, 0.7152, 0.0722])) or 1.0
    ratio = np.ones(rows, np.float32)
    support = np.zeros(rows, np.float32)
    for row in range(rows):
        ok = counts[row] > 0
        if ok.sum() >= 2:
            ratio[row] = np.median(lum[row][ok]) / base
            support[row] = min(1.0, counts[row].sum() / 12)
    from scipy import ndimage

    w = ndimage.gaussian_filter1d(support, 1.5, mode="nearest")
    value = ndimage.gaussian_filter1d(ratio * support, 1.5, mode="nearest") / np.maximum(w, 1e-6)
    value = np.where(w > 0.05, value, 1.0)
    return np.clip(value, 0.75, 1.25)


def delta_e(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """CIE76 colour difference of sRGB arrays (..., 3)."""
    return np.linalg.norm(lab(np.asarray(a, np.float32)) - lab(np.asarray(b, np.float32)), axis=-1)


def map_cells(albedo: np.ndarray, width: float, height: float) -> np.ndarray:
    """Mean sRGB of the map per 0.5 m cell, rows from the top (as colour_cells)."""
    rows, cols = grid_shape(width, height)
    h, w = albedo.shape[:2]
    ys = np.minimum((np.arange(h) + 0.5) * height / h / CELL, rows - 1).astype(int)
    xs = np.minimum((np.arange(w) + 0.5) * width / w / CELL, cols - 1).astype(int)
    total = np.zeros((rows, cols, 3), np.float64)
    count = np.zeros((rows, cols), np.float64)
    np.add.at(total, (ys[:, None], xs[None, :]), albedo[..., :3].astype(np.float64))
    np.add.at(count, (ys[:, None], xs[None, :]), 1)
    return (total / np.maximum(count, 1)[..., None]).astype(np.float32)


def context_colours(records: np.ndarray, height: float) -> dict:
    """Median sRGB of the roof points inside the wall top and of the ground in front of it."""
    d, h = records["d"], records["h"]
    roof = records[
        (records["classification"] == 6)
        & (d <= -ROOF["inside"][0])
        & (d >= -ROOF["inside"][1])
        & (h >= height + ROOF["above"][0])
        & (h <= height + ROOF["above"][1])
    ]
    ground = records[
        (d >= GROUND["outside"][0])
        & (d <= GROUND["outside"][1])
        & (h >= GROUND["height"][0])
        & (h <= GROUND["height"][1])
    ]
    return {
        name: (dominant_colour(points) if len(points) >= 5 else None)
        for name, points in (("roof", roof), ("ground", ground))
    }


def _corr(a, b) -> float | None:
    if len(a) < 4 or np.std(a) < 1e-3 or np.std(b) < 1e-3:
        return None
    return round(float(np.corrcoef(a, b)[0, 1]), 3)


def height_bands(points: np.ndarray, height: float, options: dict) -> dict:
    """Per third of the wall height (low, mid, high): which of the options (name -> sRGB)
    the band's dominant point colour is nearest to, or None for a band with too few points."""
    out = {}
    for name, a, b in (("low", 0, 1 / 3), ("mid", 1 / 3, 2 / 3), ("high", 2 / 3, 1.01)):
        band = points[(points["h"] >= a * height) & (points["h"] < b * height)]
        if len(band) < 10:
            out[name] = None
            continue
        colour = dominant_colour(band)
        out[name] = min(options, key=lambda k: float(delta_e(colour, options[k])))
    return out


def measure_wall(
    records: np.ndarray, albedo: np.ndarray, width: float, height: float, default
) -> dict:
    """Coverage, colour agreement, structure and smear numbers of one wall; default is the
    colour an unbound wall shows today."""
    points = facade_points(records, width, height)
    cells, counts = colour_cells(points, width, height)
    reference = map_cells(albedo, width, height)
    out = {
        "width_m": round(width, 2),
        "height_m": round(height, 2),
        "points": int(len(points)),
        "points_per_m2": round(len(points) / (width * height), 2),
        "coverage_1": round(float((counts >= 1).mean()), 3),
        "coverage_3": round(float((counts >= MIN_CELL_POINTS).mean()), 3),
    }
    if len(points) < MIN_POINTS:
        return out
    dominant = dominant_colour(points)
    map_dominant = np.median(albedo[..., :3].reshape(-1, 3).astype(np.float32), axis=0)
    out["lidar_colour"] = [round(float(v)) for v in dominant]
    out["map_colour"] = [round(float(v)) for v in map_dominant]
    out["delta_e_dominant"] = round(float(delta_e(dominant, map_dominant)), 1)
    out["delta_e_default"] = round(float(delta_e(default, map_dominant)), 1)
    out["lidar_beats_default"] = bool(out["delta_e_dominant"] < out["delta_e_default"])
    context = context_colours(records, height)
    for name, colour in context.items():
        if colour is not None:
            out[f"{name}_colour"] = [round(float(v)) for v in colour]
            out[f"delta_e_to_{name}"] = round(float(delta_e(dominant, colour)), 1)
    if all(c is not None for c in context.values()):
        out["bands_nearest"] = height_bands(points, height, {"map": map_dominant, **context})
    ok = counts >= MIN_CELL_POINTS
    if ok.sum() >= 4:
        cell_de = delta_e(cells[ok], reference[ok])
        flat_de = delta_e(np.broadcast_to(dominant, cells[ok].shape), reference[ok])
        lidar_l, map_l = lab(cells[ok])[:, 0], lab(reference[ok])[:, 0]
        out.update(
            cells_compared=int(ok.sum()),
            delta_e_cells_median=round(float(np.median(cell_de)), 1),
            delta_e_flat_median=round(float(np.median(flat_de)), 1),
            lightness_corr_cells=_corr(lidar_l, map_l),
        )
        rows = [r for r in range(cells.shape[0]) if ok[r].sum() >= 2]
        if len(rows) >= 4:
            out["lightness_corr_rows"] = _corr(
                [np.median(lab(cells[r][ok[r]])[:, 0]) for r in rows],
                [np.median(lab(reference[r][ok[r]])[:, 0]) for r in rows],
            )
        options = [reference[ok]] + [
            np.broadcast_to(c, cells[ok].shape) for c in context.values() if c is not None
        ]
        nearest = np.argmin(np.stack([delta_e(cells[ok], o) for o in options]), axis=0)
        out["smeared_cells"] = round(float((nearest > 0).mean()), 3)
    return out


def comparison_image(albedo, records, width, height, scale=12.0):
    """Map | map cells | lidar cells (grey checker where empty) | inpainted lidar layer (a full
    per-wall map) | dominant colour with its vertical gradient, each at `scale` px/m."""
    from PIL import Image, ImageDraw

    points = facade_points(records, width, height)
    cells, counts = colour_cells(points, width, height)
    fallback = dominant_colour(points) if len(points) else np.array([128, 128, 128])
    raw = np.where((counts > 0)[..., None], cells, 0)
    rows, cols = counts.shape
    checker = ((np.indices((rows, cols)).sum(0) % 2) * 40 + 90)[..., None].repeat(3, -1)
    raw = np.where((counts > 0)[..., None], raw, checker)
    filled = denoise(inpaint(cells, counts, fallback), counts)
    gradient = vertical_gradient(cells, counts, fallback)
    banded = fallback[None, None, :] * gradient[:, None, None] * np.ones((1, cols, 1))
    size = (max(8, round(width * scale)), max(8, round(height * scale)))
    panels = [
        ("generated map", Image.fromarray(albedo[..., :3]).resize(size, Image.LANCZOS)),
        ("map 0.5 m", map_cells(albedo, width, height)),
        ("lidar cells", raw),
        ("lidar inpainted", filled),
        ("dominant+gradient", banded),
    ]
    tiles = []
    for label, panel in panels:
        if not isinstance(panel, Image.Image):
            panel = Image.fromarray(np.clip(panel, 0, 255).astype(np.uint8)).resize(
                size, Image.NEAREST
            )
        tiles.append((label, panel))
    pad, strip = 8, 16
    sheet = Image.new(
        "RGB", (len(tiles) * (size[0] + pad) + pad, size[1] + strip + 2 * pad), (245, 244, 240)
    )
    draw = ImageDraw.Draw(sheet)
    for k, (label, panel) in enumerate(tiles):
        x = pad + k * (size[0] + pad)
        draw.text((x, pad), label, fill=(0, 0, 0))
        sheet.paste(panel, (x, pad + strip))
    return sheet


def city_median_baseline(rows: list[dict]) -> None:
    """Add the colour a wall would get from one constant: the median colour of the other
    buildings' generated maps (leave the wall's own building out), and its colour difference."""
    generated = [r for r in rows if r["source_mode"] == "generate" and "map_colour" in r]
    for row in rows:
        if "lidar_colour" not in row:
            continue
        building = row["facade_id"].rsplit("_", 1)[0]
        others = [
            r["map_colour"] for r in generated if r["facade_id"].rsplit("_", 1)[0] != building
        ]
        if not others:
            continue
        constant = np.median(np.asarray(others, np.float32), axis=0)
        row["city_median_colour"] = [round(float(v)) for v in constant]
        row["delta_e_city_median"] = round(
            float(delta_e(constant, np.asarray(row["map_colour"], np.float32))), 1
        )
        row["lidar_beats_city_median"] = bool(row["delta_e_dominant"] < row["delta_e_city_median"])


def summarize(rows: list[dict]) -> dict:
    def med(key, subset=rows):
        values = [r[key] for r in subset if r.get(key) is not None]
        return round(float(np.median(values)), 3) if values else None

    area = sum(r["width_m"] * r["height_m"] for r in rows)
    read = [r for r in rows if "lidar_colour" in r]
    bands = {}
    for band in ("low", "mid", "high"):
        nearest = [
            r["bands_nearest"][band] for r in read if (r.get("bands_nearest") or {}).get(band)
        ]
        bands[band] = (
            {k: round(nearest.count(k) / len(nearest), 3) for k in ("map", "roof", "ground")}
            if nearest
            else None
        )
    return {
        "walls": len(rows),
        "area_m2": round(area, 1),
        "area_weighted_coverage_1": round(
            sum(r["coverage_1"] * r["width_m"] * r["height_m"] for r in rows) / area, 3
        ),
        "area_weighted_coverage_3": round(
            sum(r["coverage_3"] * r["width_m"] * r["height_m"] for r in rows) / area, 3
        ),
        **{
            f"median_{k}": med(k)
            for k in (
                "points_per_m2",
                "delta_e_dominant",
                "delta_e_default",
                "delta_e_city_median",
                "delta_e_cells_median",
                "delta_e_flat_median",
                "lightness_corr_cells",
                "lightness_corr_rows",
                "smeared_cells",
                "delta_e_to_roof",
                "delta_e_to_ground",
            )
        },
        "walls_with_colour": len(read),
        "lidar_beats_default_share": (
            round(sum(r["lidar_beats_default"] for r in read) / len(read), 3) if read else None
        ),
        "lidar_beats_city_median_share": (
            round(sum(r.get("lidar_beats_city_median", False) for r in read) / len(read), 3)
            if read
            else None
        ),
        "bands_nearest_share": bands,
    }


def measure(inventory: Path, assembly: Path, buildings, points_path: Path, output: Path):
    from turku3d.lidar import walls as lidar_walls

    from .facade_derive import decode_ktx2, published_maps

    bound = set(json.loads((assembly / "MANIFEST.json").read_text())["bound"])
    features = [
        f
        for f in json.loads(inventory.read_text())["features"]
        if f["properties"]["building_id"] in buildings and f["properties"].get("street_facing")
    ]
    walls = {w.id: w for w in lidar_walls.walls_from_features(features)}
    targets = [w for w in walls.values() if w.id in bound]
    window = lidar_walls.Window(inside=3.0, outside=3.0, above=3.0, below=0.5)
    if points_path.exists():
        points = lidar_walls.load(points_path)
    else:
        points = lidar_walls.wall_points(targets, window)
        lidar_walls.save(points_path, points)
    library = decode_ktx2(assembly / "library" / DEFAULT_MATERIAL / "albedo.ktx2")
    default = np.median(library[..., :3].reshape(-1, 3).astype(np.float32), axis=0)
    rows = []
    images = output / "images"
    for wall in sorted(targets, key=lambda w: w.id):
        building, index = wall.id.rsplit("_", 1)
        meta = json.loads((assembly / "facades" / building / f"{index}.json").read_text())
        albedo = published_maps(assembly, meta, ("albedo",))["albedo"]
        records = points.get(wall.id, np.zeros(0, lidar_walls.WALL_POINT_DTYPE))
        row = {
            "facade_id": wall.id,
            "source_mode": meta["source_mode"],
            **measure_wall(records, albedo, wall.length, wall.height, default),
        }
        rows.append(row)
        if meta["source_mode"] == "generate" or wall.length * wall.height >= 60:
            images.mkdir(parents=True, exist_ok=True)
            comparison_image(albedo, records, wall.length, wall.height).save(
                images / f"{wall.id}.jpg", quality=85
            )
    city_median_baseline(rows)
    report = {
        "buildings": sorted(buildings),
        "default_colour": [round(float(v)) for v in default],
        "generated": summarize([r for r in rows if r["source_mode"] == "generate"]),
        "all": summarize(rows),
        "walls": rows,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "measure.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", type=Path, required=True)
    ap.add_argument("--assembly", type=Path, required=True)
    ap.add_argument("--buildings", nargs="+", required=True)
    ap.add_argument("--points", type=Path, help="wall points NPZ (written when missing)")
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args(argv)
    report = measure(
        a.inventory, a.assembly, set(a.buildings), a.points or a.output / "points.npz", a.output
    )
    print(json.dumps({"generated": report["generated"], "all": report["all"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
