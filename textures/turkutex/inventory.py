"""Temporary footprint inventory until the W1-03 buildings layer is available.

Delete this extractor once docs/specs/facades.md and the authoritative layer land.
The boundary is the union of config.OFFICE_BLOCK.visual_tiles plus config.MESH_MARGIN.
All dimensions and facade indices are explicitly provisional.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from turku3d import config

from .paths import work_root
from .streetview.facades import (
    Facade,
    fetch_wfs,
    merge_edges,
    read_footprints,
    read_streets,
    save_facades,
    street_facades,
)


def derive(out: Path):
    tiles = [tile.bbox for tile in config.OFFICE_BLOCK.visual_tiles]
    bb = (
        config.BBoxLocal(
            min(b.xmin for b in tiles),
            min(b.zmin for b in tiles),
            max(b.xmax for b in tiles),
            max(b.zmax for b in tiles),
        )
        .buffered(config.MESH_MARGIN)
        .to_en()
        .as_tuple()
    )
    cache = work_root() / "cache/wfs"
    buildings = read_footprints(fetch_wfs("kanta:Rakennus", bb, cache / "aoi-buildings.gml"))
    streets = read_streets(fetch_wfs("GIS:Keskilinjat", bb, cache / "aoi-streets.gml"))
    street = street_facades(buildings, streets, min_len=0.1)
    lookup = {(f.props["building_id"], tuple(f.p0), tuple(f.p1)): f for f in street}
    facades = []
    for building in buildings:
        bid, ring = building["id"], building["ring"]
        for index, (start, end) in enumerate(merge_edges(ring)):
            facade = lookup.get((bid, tuple(ring[start]), tuple(ring[end])))
            facing = facade is not None
            if facade is None:
                facade = Facade(
                    f"{bid}_wall_{index}",
                    ring[start].copy(),
                    ring[end].copy(),
                    0.8 + (building["floors"] or 1) * 3.2,
                    props={k: building[k] for k in ("address", "floors", "material", "year")},
                )
            if facade.length < 0.1:
                continue
            facade.props.update(
                building_id=bid,
                facade_index=index,
                street_facing=facing,
                material_key="render_smooth",
                roof_material_key="roof_standing_seam_metal",
                height_source="provisional footprint: floors*3.2+0.8",
                inventory_source="kantakartta interim; replace with W1-03",
            )
            facades.append(facade)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_facades(facades, out)
    return {
        "buildings": len(buildings),
        "facades": len(facades),
        "street_facades": sum(f.props["street_facing"] for f in facades),
        "bbox_en": bb,
    }


def main(argv=None):
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(derive(args.out)))
    return 0
