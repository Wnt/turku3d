"""Select wide, independent LOD2 walls for a local facade truth contingency."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from turku3d import config

from .facade_priors import building_group
from .streetview.facades import load_facades, save_facades


def select(facades, excluded: set[str], centre: tuple[float, float], limit: int):
    """Choose one nearby physical wall per previously unseen building."""
    candidates = [
        wall
        for wall in facades
        if 15 <= wall.length <= 40
        and wall.height_m >= 3
        and wall.props.get("street_facing")
        and building_group(wall.id) not in excluded
    ]
    candidates.sort(
        key=lambda wall: (
            math.dist(wall.centre, centre),
            abs(wall.length - 24),
            wall.id,
        )
    )
    chosen = []
    seen = set(excluded)
    for wall in candidates:
        building = building_group(wall.id)
        if building in seen:
            continue
        chosen.append(wall)
        seen.add(building)
        if len(chosen) == limit:
            break
    if len(chosen) != limit:
        raise ValueError(f"only {len(chosen)} independent regional walls; need {limit}")
    return chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--exclude-inventory", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()
    excluded = {
        building_group(wall.id) for path in args.exclude_inventory for wall in load_facades(path)
    }
    centre = (config.OFFICE_E, config.OFFICE_N)
    chosen = select(load_facades(args.inventory), excluded, centre, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_facades(chosen, args.output)
    print(dict(selected=len(chosen), min_width=min(wall.length for wall in chosen)))


if __name__ == "__main__":
    main()
