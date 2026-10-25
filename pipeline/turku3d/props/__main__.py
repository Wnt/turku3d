"""Build the props layer from immutable local city inputs."""

import argparse
import json
import time

from turku3d import config as C
from turku3d.layers import layer_root
from turku3d.props import GroundHeights, publish, read_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", choices=["office-block", "phase1"], default="office-block")
    parser.add_argument("--tiles", nargs="+")
    parser.add_argument("--margin", type=float)
    parser.add_argument("--source-area", default="work-3x3")
    args = parser.parse_args()
    margin = (
        args.margin if args.margin is not None else (C.MESH_MARGIN if args.area == "phase1" else 0)
    )
    if margin < 0:
        parser.error("Expected a nonnegative margin")
    if args.tiles or args.area == "phase1":
        tiles = [
            C.VisualTile.parse(t) for t in (args.tiles or ["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"])
        ]
        if any(t.level != 0 for t in tiles):
            parser.error("Expected L0 tiles")
        boxes = [t.bbox for t in tiles]
        bbox = C.BBoxLocal(
            min(b.xmin for b in boxes),
            min(b.zmin for b in boxes),
            max(b.xmax for b in boxes),
            max(b.zmax for b in boxes),
        ).buffered(margin)
    else:
        bbox = C.OFFICE_BLOCK.core.buffered(margin)
    start = time.perf_counter()
    props, inputs, metadata = read_sources(bbox, args.source_area)
    if not props:
        raise ValueError("No props in requested area")
    heights = GroundHeights(bbox, inputs)
    placed, unsupported = [], []
    for p in props:
        try:
            p.y, p.height_distance = heights.at(p.x, p.z)
        except ValueError:
            unsupported.append(p.object_id)
            continue
        placed.append(p)
    metadata["missing_ground_objects"] = unsupported
    props = placed
    metadata.update(
        bbox_local=list(bbox.as_tuple()),
        source_area=args.source_area,
        build_seconds=round(time.perf_counter() - start, 3),
    )
    metrics = publish(props, layer_root() / "props", inputs, metadata)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
