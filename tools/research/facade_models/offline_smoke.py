"""Exercise the real describe dispatcher with socket connections forbidden."""

import json
import os
import socket
from pathlib import Path

from turkutex.facade_fast import describe, plan_walls, write_json

root = Path(os.environ["TURKU3D_WORK"])
fixture = Path.home() / "turku3d/wt/textures-fast-facade-pass/work/textures/fast-measure"
refs = fixture.parent.parent / "textures/refs"
run = root / "textures/model-smoke"
inventory = fixture / "facades.geojson"
wall_ids = [w["facade_id"] for w in json.loads((fixture / "plan.json").read_text())["walls"]]
# The archived plan hashes the original full inventory, while facades.geojson is its subset.
# Make a fresh plan for that subset through the normal planner; keep the guard enabled.
write_json(run / "plan.json", plan_walls(inventory, fixture / "empty-assembly", [], wall_ids))


def deny(*_args, **_kwargs):
    raise AssertionError("network forbidden during describe")


socket.socket.connect = deny
result = describe(run, inventory, refs, 1, 80, engine="cv-models")
records = [json.loads(line) for line in (run / "run_describe/out.jsonl").read_text().splitlines()]
assert result["exit"] == 0 and result["calls"] == 0
assert result["jobs"] == len(wall_ids) == 20
assert all(record["meta"]["cv"]["needs_review"] for record in records)
print(json.dumps(result), flush=True)
