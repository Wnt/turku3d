"""Manual lidar audit over every prepared draft tile with classified source coverage.

python -m turku3d.coarse.roof_audit [--build] [--tiles t0_-1_1 ...]
Outputs retained locally under WORK/reports/roofs; no external reference is opened.
"""

import argparse
import copy
import json

import numpy as np
import shapely

from turku3d import config as C
from turku3d.buildings.reconstruct import class6
from turku3d.coarse import roofs
from turku3d.coarse.build import build
from turku3d.coarse.inputs import buildings, gather
from turku3d.coarse.sources import WFS_LAYERS, Sources

ROOT = C.work_root() / "reports/roofs"


def heights(building, points):
    out = np.full(len(points), np.nan)
    for poly, plane in building.roof_parts:
        inside = shapely.intersects_xy(poly, points[:, 0], points[:, 2])
        out[inside] = roofs.plane_height(plane, points[inside][:, [0, 2]])
    return out


def distribution(errors):
    if not len(errors):
        return {}
    values = np.abs(errors)
    return dict(
        n=len(values),
        median=float(np.median(values)),
        p75=float(np.percentile(values, 75)),
        p90=float(np.percentile(values, 90)),
        p95=float(np.percentile(values, 95)),
        mean=float(values.mean()),
        over_2m=float(np.mean(values > 2)),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    sources = Sources()
    # Audit every prepared L0 cell inside every cached full classified LAZ block,
    # including cells never requested by the player. No finding-based selection.
    tiles = args.tiles or sorted(
        {
            t.id
            for path in (C.raw_dir("laz") / "pistepilvi").glob("*.laz")
            for t in C.visual_tiles_for_bbox(C.LazTile.parse(path.stem).bbox_en.to_local())
            if sources.wfs_source(WFS_LAYERS[0], t)[0]
        }
    )
    records, skipped, all_errors = [], [], [[], [], []]
    for tid in tiles:
        tile = C.VisualTile.parse(tid)
        path = sources.wfs_source(WFS_LAYERS[0], tile)[1]
        paths = roofs.lidar_paths(tile)
        if path is None or not paths:
            skipped.append(tid)
            continue
        bs = buildings(path, tile)
        if not bs:
            continue
        points = class6(paths, C.cache_dir("coarse-roofs"))
        # Keep the same independent, deterministic 20% of returns out of fitting.
        mm = np.round(points * 1000).astype(np.int64)
        holdout = ((mm[:, 0] * 31 + mm[:, 1] * 17 + mm[:, 2] * 13) % 5) == 0
        before = [b.top for b in bs]
        original = copy.deepcopy(bs)
        roofs.refine(bs, [], points=points[~holdout])
        test = points[holdout]
        local_records = []
        for b, lod2, old in zip(bs, original, before, strict=True):
            q = test[
                shapely.contains_xy(b.footprint.buffer(-0.75), test[:, 0], test[:, 2])
                & (test[:, 1] > b.base + 2)
            ]
            if not len(q):
                continue
            h, initial = heights(b, q), heights(lod2, q)
            good = np.isfinite(h) & np.isfinite(initial)
            q, h, initial = q[good], h[good], initial[good]
            errors = [old - q[:, 1], initial - q[:, 1], h - q[:, 1]]
            for target, values in zip(all_errors, errors, strict=True):
                target.extend(values.tolist())
            local_records.append(
                dict(
                    building=b.key,
                    base=b.base,
                    old_height=old,
                    roof_parts=len(b.roof_parts),
                    evidence=b.height_evidence,
                    before=distribution(errors[0]),
                    lod2_envelope=distribution(errors[1]),
                    after=distribution(errors[2]),
                )
            )
        result = dict(
            tile=tid, buildings=len(bs), evaluated=len(local_records), records=local_records
        )
        records.append(result)
        (ROOT / ("height-audit-selected.json" if args.tiles else "height-audit.json")).write_text(
            json.dumps(
                dict(
                    tiles=records,
                    skipped=skipped,
                    distribution=dict(
                        zip(
                            ["before", "lod2_envelope", "after"],
                            map(distribution, all_errors),
                            strict=True,
                        )
                    ),
                ),
                indent=2,
            )
            + "\n"
        )
        print(tid, len(bs), len(local_records), flush=True)
        if args.build:
            inputs = gather(tile, ROOT / "inputs", sources)
            baseline = copy.deepcopy(inputs)
            old_by_id = {b.key: top for b, top in zip(bs, before, strict=True)}
            for b in baseline.buildings:
                b.roof_parts = []
                b.height_evidence = []
                b.top = old_by_id[b.key]
            for phase, value in [("before", baseline), ("after", inputs)]:
                manifest = build(tid, ROOT / phase / tid, inputs=value)
                print(phase, tid, manifest["buildings"], flush=True)


if __name__ == "__main__":
    main()
