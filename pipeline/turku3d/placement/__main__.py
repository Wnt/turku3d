"""Revalidate all published instance inventories together; fail on hard violations."""

import json

from turku3d import config as C
from turku3d.layers import layer_root, read_manifest
from turku3d.placement.inputs import INVENTORIES, PLACEMENT_ORDER, context, instance


def main():
    report = {}
    for layer in PLACEMENT_ORDER:
        filename, key = INVENTORIES[layer]
        root = layer_root() / layer
        manifest = read_manifest(root)
        inventory = json.loads((root / filename).read_text())
        objects = [instance(r, layer) for r in (inventory[key] if key else inventory)]
        if not objects:
            report[layer] = {"objects": 0, "hard_violations": []}
            continue
        params = manifest["params"]
        scope = (
            params["metrics"]["bbox_local"]
            if layer == "props"
            else params["bounds"]
            if layer == "vegetation"
            else C.OFFICE_BLOCK.core.as_tuple()
        )
        bounds = C.BBoxLocal(*scope)
        validator = context(bounds, {}, layer=layer)
        validator.occupied.extend((o.object_id, o.shape) for o in objects)
        violations = [
            {"object_id": o.object_id, "violations": issues}
            for o in objects
            if (issues := validator.check(o))
        ]
        report[layer] = {
            "objects": len(objects),
            "hard_violations": violations,
            "orientation_unverified": [
                o.object_id
                for o in objects
                if o.kind in ("bench", "sign", "bus_stop") and validator.facing(o) is None
            ],
        }
    out = C.work_root() / "reports/placement/validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if any(r["hard_violations"] for r in report.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
