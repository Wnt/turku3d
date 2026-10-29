"""Audit actual per-wall paint authority in generated Linnankatu pilot diagnostics."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

PILOT = Path(__file__).resolve().parents[3] / "textures/library/facade-programs.json"
TILES = ("t0_0_0", "t0_-1_0", "t0_0_1", "t0_-1_1")


def audit(root):
    result = {p["building_id"]: {"review_accepted": not p["needs_review"], "parts": []}
              for p in json.loads(PILOT.read_text())}
    for tile in TILES:
        for p in json.loads((root / tile / "facade-programs.json").read_text()):
            key = p["building_id"].removeprefix("gis-")
            if key not in result:
                continue
            result[key]["parts"].append(dict(
                tile=tile, part_id=p["part_id"], register_storeys=p["storeys"],
                walls=[{k: w[k] for k in ("wall_id", "paint_source", "measured_wall_id", "stored_facade_index") if k in w}
                       for w in p["walls"]]))
    for key, row in result.items():
        walls = [w for p in row["parts"] for w in p["walls"]]
        if not walls or any("paint_source" not in w for w in walls):
            raise ValueError(f"{key}: missing actual wall paint provenance")
        if row["review_accepted"] and not any(w.get("measured_wall_id") for w in walls):
            raise ValueError(f"{key}: reviewed wall did not bind")
    return result


def markdown(rows):
    lines = ["| Building / tile / part | Paint source | Wall indices |", "| --- | --- | --- |"]
    for key, row in rows.items():
        for part in row["parts"]:
            groups = defaultdict(list)
            for wall in part["walls"]:
                source = wall["paint_source"]
                if wall.get("measured_wall_id"):
                    source += f" ({wall['measured_wall_id']})"
                groups[source].append(wall["wall_id"].rsplit(":", 1)[-1])
            for source, indices in groups.items():
                lines.append(f"| {key} / {part['tile']} / {part['part_id']} | {source} | {', '.join(indices)} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="directory containing tile diagnostic subdirectories")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    rows = audit(args.root)
    print(markdown(rows) if args.markdown else json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
