"""Select cached panorama views offline; copy only the listed files from OCI."""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from turku3d import config
from turkutex.facade_derive import building_attributes
from turkutex.streetview.facades import load_facades, save_facades
from turkutex.streetview.panos import PanoMeta
from turkutex.streetview.refs import SelectParams, equirect_model, evaluate


class NoDepth:
    def load_depth(self, _):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--panoramas", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--buildings", type=int, default=260)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    walls = load_facades(a.inventory)
    groups = defaultdict(list)
    for f in walls:
        if 8 <= f.length <= 65 and f.base_h is not None:
            groups[f.props["building_id"]].append(f)
    # Office and pizzeria are one block apart; order around their midpoint.
    centre = np.array([config.ORIGIN_E + 122.5, config.ORIGIN_N - 216.8])
    ids = sorted(groups, key=lambda k: (np.linalg.norm(groups[k][0].centre - centre), k))[
        : a.buildings
    ]
    selected = [f for bid in ids for f in sorted(groups[bid], key=lambda f: -f.length)[:2]]
    attrs = building_attributes(config.raw_dir("wfs/work-3x3"), ids)
    panos = [m for m in json.loads(a.panoramas.read_text()) if m["cached_images"]]
    xy = np.array([[m["E"], m["N"]] for m in panos])
    output, paths, rejected = [], set(), []
    started = time.monotonic()
    for i, f in enumerate(selected):
        choices = []
        near = np.flatnonzero(np.linalg.norm(xy - f.centre, axis=1) < 55)
        # Cheap front/angle screening before the existing full LOD2 ray test.
        for j in near:
            d = xy[j] - f.centre
            if d @ f.normal < 2 or (d @ f.normal) / np.linalg.norm(d) < 0.5:
                continue
            m = panos[j]
            score = (d @ f.normal) / np.linalg.norm(d) / (1 + abs(np.linalg.norm(d) - 20) / 25)
            choices.append((score, m))
        views = []
        for _, m in sorted(choices, key=lambda x: -x[0])[:8]:
            evaluations = evaluate(
                NoDepth(),
                f,
                PanoMeta.from_dict(m),
                f.base_h,
                SelectParams(max_dist=55, max_hfov=140),
            )
            full = next((v for v in evaluations if v["part"] == "full"), None)
            if full is None:
                continue
            image = max(m["cached_images"], key=lambda v: v["bytes"])
            model = equirect_model(full["meta"])
            view = dict(
                source=m["source"],
                pano_id=m["id"],
                pano=m,
                panorama=image["path"],
                reference_version="characterization-1",
                camera={"pos_EN_H": full["cam"].pos.tolist()},
                camera_height_source=full["camera_height_source"],
                facade_corners_EN_H=full["corners"].tolist(),
                image_to_grid=model.rotation.tolist(),
                lod2_visibility=full["lod2_visibility"],
                score=full["score"],
            )
            views.append(view)
            if len(views) == 2:
                break
        if not views:
            rejected.append(f.id)
            continue
        for v in views:
            paths.add(v["panorama"])
        output.append(
            dict(
                facade_id=f.id,
                building_id=f.props["building_id"],
                width_m=f.length,
                height_m=f.height_m,
                base_h=f.base_h,
                p0=f.p0.tolist(),
                p1=f.p1.tolist(),
                normal=f.normal.tolist(),
                attributes=attrs[f.props["building_id"]],
                views=views,
            )
        )
        if (i + 1) % 25 == 0:
            print(
                json.dumps(
                    dict(
                        processed=i + 1,
                        selected=len(output),
                        seconds=round(time.monotonic() - started, 1),
                    )
                ),
                flush=True,
            )
    save_facades(selected, a.output / "inventory.geojson")
    (a.output / "selection.json").write_text(json.dumps(output, indent=1))
    (a.output / "attributes.json").write_text(json.dumps(attrs, indent=1))
    (a.output / "rsync-files.txt").write_text("".join(x + "\n" for x in sorted(paths)))
    summary = dict(
        requested_buildings=len(ids),
        walls=len(selected),
        selected_walls=len(output),
        photographed_buildings=len({r["building_id"] for r in output}),
        unique_panoramas=len(paths),
        rejected_walls=rejected,
        seconds=time.monotonic() - started,
        all_metadata=len(json.loads(a.panoramas.read_text())),
    )
    (a.output / "preparation.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "rejected_walls"}), flush=True)


if __name__ == "__main__":
    main()
