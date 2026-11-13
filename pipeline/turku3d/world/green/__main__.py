"""python -m turku3d.world.green: measure the planting of an area and report how the lidar and the registers agree.

  report --area NAME | --at E,N [--radius R] [--image FILE] [--json FILE]
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from ..api import Area
from ..frame import cell_at, cell_box_en
from . import census


def cells_of(area: Area) -> list[tuple[int, int]]:
    box = area.bbox()
    (i0, j1), (i1, j0) = cell_at(box[0], box[1]), cell_at(box[2], box[3])
    return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1) if area.touches(cell_box_en(i, j))]


def report(area: Area, features, log=print) -> dict:
    started = time.perf_counter()
    trees, absent, hedges, masses, shrubs = [], [], [], [], []
    cells = cells_of(area)
    for i, j in cells:
        result = census.cell(i, j, features)
        trees += result["trees"]
        absent += result["absent"]
        hedges += result["hedges"]
        masses += result["masses"]
        shrubs += result["shrubs"]
    inside = lambda t: np.hypot(t["e"] - area.e, t["n"] - area.n) <= area.radius  # noqa: E731
    trees, absent = [t for t in trees if inside(t)], [t for t in absent if inside(t)]
    joined = [t for t in trees if t["source"] == "register+lidar"]
    offsets = np.array([np.hypot(t["e"] - t["top"][0], t["n"] - t["top"][1]) for t in joined]) if joined else np.zeros(1)
    heights = np.array([t["height"] for t in trees]) if trees else np.zeros(1)
    sizes = {"pieni": [], "keskikokoinen": [], "suuri": []}
    for t in joined:
        if t["register"].get("size") in sizes:
            sizes[t["register"]["size"]].append(t["height"])
    registered = len(joined) + len(absent)
    summary = {
        "area": {"e": area.e, "n": area.n, "radius": area.radius}, "cells": len(cells), "seconds": round(time.perf_counter() - started, 2),
        "trees": len(trees), "lidarOnly": len(trees) - len(joined), "registerTrees": registered,
        "registerFoundInLidar": len(joined), "registerAbsentInLidar": len(absent),
        "registerFoundShare": round(len(joined) / max(registered, 1), 3),
        "lidarTreesInRegisterShare": round(len(joined) / max(len(trees), 1), 3),
        "trunkToTopMedianM": round(float(np.median(offsets)), 2), "trunkToTopP90M": round(float(np.percentile(offsets, 90)), 2),
        "heightMedianM": round(float(np.median(heights)), 1), "heightMaxM": round(float(heights.max()), 1),
        "lidarHeightBySurveyedSize": {k: [len(v), round(float(np.median(v)), 1) if v else None] for k, v in sizes.items()},
        "absentByLayer": {layer: sum(1 for t in absent if t["layer"] == layer) for layer in sorted({t["layer"] for t in absent})},
        "osmJoined": sum(1 for t in trees if "osm" in t),
        "hedges": hedge_summary(hedges), "shrubMasses": len(masses), "shrubs": len(shrubs),
    }
    log(json.dumps(summary, indent=1))
    return {"summary": summary, "trees": trees, "absent": absent, "hedges": hedges, "masses": masses, "shrubs": shrubs}


def hedge_summary(hedges: list[dict]) -> dict:
    """Hedge pieces of the area's cells: metres by source and form, and how much of the mapped length the lidar saw."""
    length = lambda h: float(np.linalg.norm(np.diff(np.array(h["line"]), axis=0), axis=1).sum())  # noqa: E731
    metres = lambda rows: round(sum(length(h) for h in rows), 1)  # noqa: E731
    mapped = [h for h in hedges if h["source"] != "lidar"]
    heights = [v for h in hedges if h.get("measured", True) for v in h["height"]]
    return {
        "pieces": len(hedges), "metres": metres(hedges),
        "metresBySource": {source: metres([h for h in hedges if h["source"] == source]) for source in sorted({h["source"] for h in hedges})},
        "mappedMetres": metres(mapped), "mappedSeenInLidarMetres": metres([h for h in mapped if h.get("measured", True)]),
        "clippedMetres": metres([h for h in hedges if h["form"] == "clipped"]), "looseMetres": metres([h for h in hedges if h["form"] == "loose"]),
        "heightMedianM": round(float(np.median(heights)), 2) if heights else None,
    }


def draw(area: Area, result: dict, path: str) -> None:
    """A plan of the area: canopy in green, roofs in blue, crowns as ellipses, trunks as dots."""
    import cv2

    from . import inventory, lidar

    scale = 4  # pixels per metre
    box = area.bbox()
    window = lidar.Window(box)
    grid = inventory.canopy(window, box)
    side = int(2 * area.radius * scale)
    image = np.zeros((grid["rows"], grid["cols"], 3), np.uint8)
    image[..., 1] = np.clip(grid["top"] * 7, 0, 150)
    image[..., 0] = cv2.resize((window.roof > 2.5).astype(np.uint8) * 110, (grid["cols"], grid["rows"]), interpolation=cv2.INTER_NEAREST)[:grid["rows"], :grid["cols"]]
    image = cv2.resize(image[::-1], (side, side), interpolation=cv2.INTER_NEAREST)

    def pixel(e, n):
        return int((e - box[0]) * scale), int((box[3] - n) * scale)

    for tree in result["trees"]:
        colour = (0, 255, 255) if tree["source"] == "lidar" else (255, 255, 255)
        cv2.ellipse(image, pixel(*tree["centre"]), (int(tree["major"] * scale / 2), int(tree["minor"] * scale / 2)),
                    tree["angle"] - 90, 0, 360, colour, 1, cv2.LINE_AA)
        cv2.circle(image, pixel(tree["e"], tree["n"]), 3, colour, -1)
        cv2.putText(image, f"{tree['height']:.0f}", pixel(tree["e"] + 0.8, tree["n"]), cv2.FONT_HERSHEY_SIMPLEX, 0.35, colour, 1, cv2.LINE_AA)
    for hedge in result["hedges"]:
        colour = (255, 0, 255) if hedge["form"] == "clipped" else (255, 120, 60)
        points = np.array([pixel(e, n) for e, n in hedge["line"]], np.int32)
        cv2.polylines(image, [points], False, colour, max(1, int(np.median(hedge["width"]) * scale)), cv2.LINE_AA)
    for item in result["masses"]:
        cv2.polylines(image, [np.array([pixel(e, n) for e, n in item["ring"]], np.int32)], True, (60, 200, 255), 1, cv2.LINE_AA)
    for shrub in result["shrubs"]:
        cv2.circle(image, pixel(shrub["e"], shrub["n"]), max(2, int(shrub["width"] * scale / 2)), (60, 200, 255), 1, cv2.LINE_AA)
    for tree in result["absent"]:
        cv2.drawMarker(image, pixel(tree["e"], tree["n"]), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 9, 2)
    cv2.imwrite(path, image)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m turku3d.world.green", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("report")
    where = command.add_mutually_exclusive_group(required=True)
    where.add_argument("--area")
    where.add_argument("--at", metavar="E,N")
    command.add_argument("--radius", type=float)
    command.add_argument("--image")
    command.add_argument("--json")
    args = parser.parse_args()
    from ..sources import Sources

    if args.area:
        from ..driver import area_of_views

        area = area_of_views(args.area)
        area = area.grown(args.radius) if args.radius else area
    else:
        e, n = (float(v) for v in args.at.split(","))
        area = Area(e, n, args.radius or 150.0)
    result = report(area, Sources(log=lambda *_: None).wfs.features)
    if args.image:
        draw(area, result, args.image)
    if args.json:
        with open(args.json, "w") as out:
            json.dump(result, out)


if __name__ == "__main__":
    main()
