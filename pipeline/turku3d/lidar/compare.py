"""Compare actual ground meshes on a fixed, independently observed station set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from turku3d.col.materials import mat_id


def measure_mesh_step(offsets, profile):
    """Slope-fitted sharp transition measurement, shared historical W1-01 method."""
    mid = (offsets[:-1] + offsets[1:]) / 2
    gradient = np.diff(profile) / np.diff(offsets)
    slope = np.median(gradient[np.abs(mid) > 0.6])
    search = np.flatnonzero(np.abs(mid) <= 0.45)
    edge = mid[search[np.argmax(np.abs(gradient[search] - slope))]]
    relative = offsets - edge
    masks = [(relative < -0.12) & (relative > -0.8), (relative > 0.12) & (relative < 0.8)]
    coeff = [np.polyfit(relative[m], profile[m], 1) for m in masks]
    step = float(coeff[1][1] - coeff[0][1])
    if abs(step) < 0.01:
        return step, 1.0, float(edge)
    window = np.abs(relative) <= 0.6
    detrended = profile[window] - relative[window] * np.mean([c[0] for c in coeff])
    normalized = (detrended - coeff[0][1]) / step
    rising = np.maximum.accumulate(np.clip(normalized, 0, 1))
    width = float(np.interp(0.9, rising, offsets[window]) - np.interp(0.1, rising, offsets[window]))
    return step, width, float(edge)


def load_query(directory):
    import matplotlib.tri as tri

    positions, indices, offset = [], [], 0
    for path in sorted(Path(directory).glob("c_*.col.npz")):
        with np.load(path, allow_pickle=False) as mesh:
            positions.append(mesh["positions"])
            indices.append(mesh["indices"][mesh["material"] != mat_id("water")] + offset)
            offset += len(mesh["positions"])
    if not positions:
        raise ValueError(f"no collision layers in {directory}")
    p, inverse = np.unique(np.concatenate(positions), axis=0, return_inverse=True)
    faces = inverse[np.concatenate(indices)]
    interpolator = tri.LinearTriInterpolator(
        tri.Triangulation(p[:, 0], p[:, 2], faces[:, [0, 2, 1]]), p[:, 1]
    )

    def query(xz):
        return np.asarray(interpolator(xz[:, 0], xz[:, 1]).filled(np.nan))

    return query


def summarize(pairs):
    """Pairs use one held-out truth and length weight for both actual meshes."""
    strong = [p for p in pairs if abs(p["truth"]) >= 0.06]
    length = sum(p["length"] for p in strong)
    output = {
        "supported_stations": len(pairs),
        "raised_stations": len(strong),
        "raised_length_m": length,
    }
    for name in ("before", "after"):
        valid = [p for p in strong if p[name] is not None]
        stepped = [p for p in valid if abs(p[name]) >= 0.03]
        step_length = sum(p["length"] for p in stepped)
        error = [abs(p[name] - p["truth"]) for p in valid]
        output[name] = {
            "mesh_step_stations": len(stepped),
            "mesh_step_length_m": step_length,
            "mesh_step_fraction": step_length / length if length else None,
            "median_step_error_m": float(np.median(error)) if error else None,
            "p90_step_error_m": float(np.quantile(error, 0.9)) if error else None,
            "missing_mesh_profiles": len(strong) - len(valid),
        }
    return output


def compare(before_dir, before_qa, after_dir, after_qa):
    before = json.loads(Path(before_qa).read_text())
    after = json.loads(Path(after_qa).read_text())
    previous = {(s["object_id"], s["distance"]): s for s in before["stations"]}
    records = {
        r["object_id"]: r for r in json.loads((Path(after_dir) / "breaklines.json").read_text())
    }
    query = load_query(before_dir)
    offsets = np.linspace(-1.2, 1.2, 241)
    mapped, added = [], []
    for station in after["stations"]:
        ident, distance = station["object_id"], station["distance"]
        pair = {
            "truth": station["lidar_step"],
            "length": station["length_weight_m"],
            "after": station.get("mesh_step"),
        }
        if (ident, distance) in previous:
            pair["before"] = previous[ident, distance].get("mesh_step")
            mapped.append(pair)
        elif ident.startswith("lidar:"):
            record = records[ident]
            centers, normals = np.asarray(record["centers_xz"]), np.asarray(record["normals_xz"])
            chainage = np.r_[0, np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
            normal = np.array([np.interp(distance, chainage, normals[:, axis]) for axis in (0, 1)])
            normal /= np.linalg.norm(normal)
            profile = query(np.asarray(station["center_xz"]) + offsets[:, None] * normal)
            pair["before"] = (
                measure_mesh_step(offsets, profile)[0] if np.isfinite(profile).all() else None
            )
            added.append(pair)
    return {
        "mapped_curbs": summarize(mapped),
        "lidar_only": summarize(added),
        "combined": summarize(mapped + added),
        "before_ground_checks": before["checks"],
        "after_ground_checks": after["checks"],
        "method": "Same independently held-out station truth/lengths for both meshes; >=6cm lidar relief, >=3cm mesh step. Added locations are sampled from the baseline collision triangles, not declared input heights.",
        "before_qa": str(before_qa),
        "after_qa": str(after_qa),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--before-qa", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--after-qa", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.before, args.before_qa, args.after, args.after_qa)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=2, allow_nan=False)
    args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
