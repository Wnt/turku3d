"""Fit aggregate wall priors from retained accepted scorer patches (private outputs).

No imagery or per-building sampled colours belong in git. This deliberately uses the
scorer's base-colour appearance, which has unknown baked lighting, not true albedo.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "textures"))
from turkutex import facade_fast_render as renderer  # noqa: E402
from turkutex.wall_palette import group_keys, lab, table  # noqa: E402


def normalise(data, coverage, scorer_dir):
    """Estimate bounded illumination from separate, geometrically matched open ground.

    This is a colour-constancy proxy, not recovery of directional wall illumination.
    Existing scorer masks exclude vegetation/water/gaps; source footprints exclude roofs.
    """
    sys.path.insert(0, str(scorer_dir))
    import shapely
    from surface import source_building_footprints
    from turku3d import config as C
    from turku3d.coarse.sources import Sources

    def linear(values):
        v = np.asarray(values, dtype=float) / 255
        return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)

    quarters = json.loads(coverage.read_text())["quarters"]
    corrections = {}
    for quarter, state in quarters.items():
        if state["status"] != "scored":
            continue
        report_path = Path(state["report"])
        report = json.loads(report_path.read_text())
        with np.load(report_path.parent / "comparison.npz") as f:
            own = f["ours_rgb"].copy()
            valid = f["valid"] & f["color_valid"] & (np.abs(f["difference"]) < 0.3)
        with np.load(report_path.parent / "reference-dsm.npz") as f:
            reference = f["rgb"].copy()
        tile = C.VisualTile.parse(state["tile"])
        _, path = Sources().wfs_source("bldg:Building_LOD2", tile)
        footprints = source_building_footprints(path, tile.bbox.buffered(5))
        e0, n0, e1, n1 = report["extent_en"]
        rows, cols = np.indices(valid.shape)
        x = e0 + (cols + 0.5) * (e1 - e0) / valid.shape[1] - C.ORIGIN_E
        z = C.ORIGIN_N - n1 + (rows + 0.5) * (n1 - n0) / valid.shape[0]
        roofs = shapely.union_all(footprints).buffer(2)
        valid &= ~shapely.contains_xy(roofs, x, z)
        valid &= (reference.min(axis=-1) > 40) & (reference.max(axis=-1) < 235)
        valid &= (own.min(axis=-1) > 40) & (own.max(axis=-1) < 235)
        if valid.sum() < 80:
            gain = np.ones(3)
        else:
            gain = np.clip(
                np.exp(
                    np.median(
                        np.log(np.maximum(linear(own[valid]), 1e-5))
                        - np.log(np.maximum(linear(reference[valid]), 1e-5)),
                        axis=0,
                    )
                ),
                0.5,
                2,
            )
        corrections[quarter] = dict(
            gain_linear=gain.tolist(),
            ground_cells=int(valid.sum()),
            accepted=bool(valid.sum() >= 80),
        )
    measurements = defaultdict(list)
    for group in data["wall_groups"]:
        gain = corrections[group["quarter"]]["gain_linear"]
        v = linear(group["reference_rgb"]) * gain
        corrected = np.clip(
            np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055) * 255, 0, 255
        )
        group["normalised_reference_rgb"] = corrected.tolist()
        measurements[group.get("building_id", group["building_key"])].append(corrected)
    for row in data["buildings"]:
        key = row["building_id"] if row["building_id"] in measurements else row["building_key"]
        row["normalised_reference_rgb"] = np.median(measurements[key], axis=0).tolist()
    data["illumination"] = corrections
    data["lighting"] = (
        "Bounded per-channel linear ground illumination proxy; directional wall shadows remain unknown"
    )
    return data


def held_out(key):
    return int(hashlib.sha256(("wall-holdout-v1:" + key).encode()).hexdigest()[:8], 16) % 5 == 0


def collect(coverage):
    from turku3d import config as C
    from turku3d.coarse.inputs import buildings
    from turku3d.coarse.register import enrich, read_osm, read_register
    from turku3d.coarse.sources import Sources

    sources = Sources()
    attributes = {}
    measurements = defaultdict(list)
    groups = []
    quarters = json.loads(coverage.read_text())["quarters"]
    for tile_id in sorted({r["tile"] for r in quarters.values() if r["status"] == "scored"}):
        tile = C.VisualTile.parse(tile_id)
        _, path = sources.wfs_source("bldg:Building_LOD2", tile)
        if path is None:
            raise ValueError(f"Missing retained building source for {tile_id}")
        registered = read_register(
            [
                p
                for name in ("kanta:Rakennus", "GIS:Rakennukset")
                if (p := sources.wfs_source(name, tile)[1])
            ]
        )
        osm_path = sources.osm_source(tile)
        osm = read_osm(tile, osm_path) if osm_path else {}
        for building in buildings(path, tile):
            enrich(building, registered, osm)
            attributes[building.key] = dict(
                building_id=building.facade_key,
                attributes=building.attributes,
                footprint_m2=building.footprint.area,
                height_m=building.top - building.base,
            )
    for quarter, state in quarters.items():
        if state["status"] != "scored":
            continue
        report = json.loads(Path(state["report"]).read_text())
        for group, patches in report["facades"]["colour_groups"].items():
            obj = group.rsplit(":normal-", 1)[0]
            # Trim bands are separate materials, not a wall-paint observation.
            key = obj.removeprefix("walls_")
            if key not in attributes or len(patches) < 10:
                continue
            values = list(patches.values())
            reference = np.median([v["reference"] for v in values], axis=0)
            own = np.median([v["own"] for v in values], axis=0)
            measurements[key].append(reference)
            groups.append(
                dict(
                    quarter=quarter,
                    group=group,
                    building_key=key,
                    building_id=attributes[key]["building_id"],
                    reference_rgb=reference.tolist(),
                    own_rgb=own.tolist(),
                    reference_sha256=report["reference_sha256"],
                    own_sha256=report["build_sha256"],
                )
            )
    rows = [
        dict(
            building_key=key,
            **attributes[key],
            reference_rgb=np.median(values, axis=0).tolist(),
            held_out=held_out(attributes[key]["building_id"]),
        )
        for key, values in sorted(measurements.items())
    ]
    # Multiple surveyed solids sharing a register ID must get one vote and one split.
    unique = defaultdict(list)
    for row in rows:
        unique[row["building_id"]].append(row)
    rows = [
        dict(
            values[0],
            reference_rgb=np.median([r["reference_rgb"] for r in values], axis=0).tolist(),
        )
        for values in unique.values()
    ]
    for row in rows:
        row["baseline_wall_colour"] = renderer.register_description(
            row["building_id"], row["building_id"], row["attributes"], 12, row["height_m"]
        )["wall_colour"]
    return dict(
        buildings=rows,
        wall_groups=groups,
        scored_quarters=sum(r["status"] == "scored" for r in quarters.values()),
        source_coverage=str(coverage),
        lighting="Scorer does not remove baked lighting; no lit-normalised export exists",
    )


def fit(rows):
    normalised = all("normalised_reference_rgb" in row for row in rows)
    values = defaultdict(list)
    for row in rows:
        if row["held_out"]:
            continue
        for key in group_keys(row["attributes"]):
            values[key].append(lab(row.get("normalised_reference_rgb", row["reference_rgb"])))
    priors = table().get("priors", {})
    groups = dict(priors)
    for key, data in sorted(values.items()):
        # Fine district groups need enough independent buildings to avoid memorisation.
        minimum = 12 if len(json.loads(key)) == 3 else 8
        if len(data) < minimum:
            continue
        data = np.asarray(data)
        lower, upper = np.percentile(data, [10, 90], axis=0)
        clipped = np.clip(data, lower, upper)
        groups[key] = dict(
            source="masked reference appearance with bounded ground illumination proxy"
            if normalised
            else "raw reference appearance",
            buildings=len(data),
            mean_lab=clipped.mean(axis=0).tolist(),
            covariance_lab=np.cov(clipped.T).tolist(),
            lower_lab=lower.tolist(),
            upper_lab=upper.tolist(),
        )
    if "[]" not in groups:
        raise ValueError("Insufficient independent buildings for a global prior")
    return dict(
        version=1,
        training_buildings=sum(not r["held_out"] for r in rows),
        holdout_buildings=sum(r["held_out"] for r in rows),
        colour_space="CIELAB D65",
        spread_scale=0.25,
        method=(
            "Winsorised building-weighted appearance with bounded ground illumination proxy; directional wall lighting unknown"
            if normalised
            else "Winsorised building-weighted raw base-colour appearance; baked lighting unknown"
        ),
        holdout="SHA256(wall-holdout-v1:register-id) first32bits modulo5 ==0",
        groups=groups,
        priors=priors,
    )


def evaluate(rows, model):
    report = {}
    for name, selected in (
        ("train", [r for r in rows if not r["held_out"]]),
        ("holdout", [r for r in rows if r["held_out"]]),
    ):
        errors = defaultdict(list)
        for row in selected:
            attrs = row["attributes"]
            before = row["baseline_wall_colour"]
            after = renderer.register_description(
                row["building_id"],
                row["building_id"],
                attrs,
                12,
                row["height_m"],
                palette_model=model,
            )["wall_colour"]
            for label, colour in (("before", before), ("after", after)):
                colour_rgb = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]
                for target in ("reference_rgb", "normalised_reference_rgb"):
                    if target in row:
                        errors[label + "_" + target].append(
                            float(np.linalg.norm(lab(colour_rgb) - lab(row[target])))
                        )
        report[name] = dict(
            buildings=len(selected),
            **{
                k: dict(mean=float(np.mean(v)), median=float(np.median(v)))
                for k, v in errors.items()
            },
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--measurements", type=Path)
    parser.add_argument("--normalise", action="store_true")
    parser.add_argument(
        "--scorer-dir", type=Path, default=Path.home() / "turku3d/private/gmaps-extract"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    data = (
        json.loads(args.measurements.read_text()) if args.measurements else collect(args.coverage)
    )
    if args.normalise:
        data = normalise(data, args.coverage, args.scorer_dir)
    (args.out / "measurements.json").write_text(json.dumps(data, indent=2) + "\n")
    model = fit(data["buildings"])
    model["scored_quarters"] = data["scored_quarters"]
    (args.out / "wall_palette.json").write_text(json.dumps(model, indent=2) + "\n")
    results = evaluate(data["buildings"], model)
    (args.out / "evaluation.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
