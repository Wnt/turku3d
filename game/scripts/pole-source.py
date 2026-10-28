"""Real sign positions for the headless full/draft pole acceptance run."""

import json
import os
import pickle
import struct
from pathlib import Path

from turku3d.props import Prop  # noqa: F401 - needed to unpickle prepared draft props


def main() -> None:
    data = Path(os.environ["TURKU3D_DATA"])
    work = Path(os.environ["TURKU3D_WORK"])
    full = data / "build/layers/props/t0_0_0.vis.glb"
    blob = full.read_bytes()
    size = struct.unpack_from("<I", blob, 12)[0]
    gltf = json.loads(blob[20 : 20 + size])
    origin = gltf["nodes"][0].get("translation", [0, 0, 0])
    full_poles = [
        {
            "id": node["extras"]["pole_id"],
            "x": origin[0] + node["translation"][0],
            "z": origin[2] + node["translation"][2],
        }
        for node in gltf["nodes"]
        if node.get("extras", {}).get("pole_id")
    ]
    cache = next((work / "build/coarse/.inputs/t0_2_0").glob("poles.*.pkl"))
    prepared = pickle.loads(cache.read_bytes())["value"]
    draft_poles = [
        {"id": f"t0_2_0:{pole.object_id}", "x": pole.x, "z": pole.z} for pole in prepared
    ]
    report = work / "reports/pole-source.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"full": full_poles, "draft": draft_poles}, indent=2))
    print(f"{report}: full={len(full_poles)} draft={len(draft_poles)}")


if __name__ == "__main__":
    main()
