"""Revalidate all published instance inventories per listed tile; fail on hard violations."""

import json
from collections import defaultdict

from turku3d import config as C
from turku3d.layers import layer_root, read_manifest
from turku3d.placement.inputs import INVENTORIES, PLACEMENT_ORDER, context, instance


def main():
    report = {}
    listed = {C.VisualTile.parse(t) for t in C.playable_tile_ids()}
    for layer in PLACEMENT_ORDER:
        filename, key = INVENTORIES[layer]
        root = layer_root() / layer
        read_manifest(root)
        inventory = json.loads((root / filename).read_text())
        objects = [instance(r, layer) for r in (inventory[key] if key else inventory)]
        by_tile = defaultdict(list)
        for o in objects:
            by_tile[C.VisualTile.at(o.x, o.z)].append(o)
        violations, unverified = [], []
        for tile in sorted(by_tile.keys() & listed):
            validator = context(tile.bbox, C.source_area(tile), layer=layer)
            validator.occupied.extend((o.object_id, o.shape) for o in objects)
            for o in by_tile[tile]:
                if issues := validator.check(o):
                    violations.append({"object_id": o.object_id, "violations": issues})
                if o.kind in ("bench", "sign", "bus_stop") and validator.facing(o) is None:
                    unverified.append(o.object_id)
        report[layer] = {
            "objects": len(objects),
            "outside_listed_tiles": sum(len(v) for t, v in by_tile.items() if t not in listed),
            "hard_violations": violations,
            "orientation_unverified": unverified,
        }
    out = C.work_root() / "reports/placement/validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if any(r["hard_violations"] for r in report.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
