"""Measure raised curb length on the actual before and after collision meshes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id


class TileQuery:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.cache = {}

    def sample(self, point):
        tile = C.CollisionTile.at(*point)
        if tile.id not in self.cache:
            path = self.directory / f"{tile.id}.col.npz"
            with np.load(path, allow_pickle=False) as data:
                p = data["positions"]
                solid = data["material"] != mat_id("water")
                triangles = p[data["indices"][solid]]
                material = data["material"][solid]
            polygons = shapely.polygons(triangles[:, :, [0, 2]])
            self.cache[tile.id] = (triangles, material, shapely.STRtree(polygons))
        triangles, material, tree = self.cache[tile.id]
        best = None
        for index in tree.query(shapely.Point(point), predicate="intersects"):
            a, b, c = triangles[index]
            ab, ac, q = b[[0, 2]] - a[[0, 2]], c[[0, 2]] - a[[0, 2]], point - a[[0, 2]]
            determinant = ab[0] * ac[1] - ab[1] * ac[0]
            if abs(determinant) < 1e-12:
                continue
            u = (q[0] * ac[1] - q[1] * ac[0]) / determinant
            v = (ab[0] * q[1] - ab[1] * q[0]) / determinant
            height = float(a[1] + u * (b[1] - a[1]) + v * (c[1] - a[1]))
            if best is None or height > best[0]:
                best = height, int(material[index])
        return best

    def material(self, point):
        found = self.sample(point)
        return found[1] if found else None


def measure(before, after, breaklines, bounds=None):
    old, new = TileQuery(before), TileQuery(after)
    rows = []
    for feature in json.loads(Path(breaklines).read_text()):
        if feature["kind"] != "curb":
            continue
        centers = np.asarray(feature["centers_xz"])
        normals = np.asarray(feature["normals_xz"])
        heights = np.asarray(feature["side_heights"])
        for i in range(len(centers) - 1):
            center = centers[i : i + 2].mean(axis=0)
            if bounds is not None and not (
                bounds.xmin <= center[0] < bounds.xmax and bounds.zmin <= center[1] < bounds.zmax
            ):
                continue
            normal = normals[i : i + 2].mean(axis=0)
            normal /= np.linalg.norm(normal)
            points = [center - normal * 0.055, center + normal * 0.055]
            length = float(np.linalg.norm(centers[i + 1] - centers[i]))
            row = {
                "source": feature["provenance"],
                "length_m": length,
                "measured_step_m": float(np.abs(np.diff(heights[i : i + 2], axis=1)).mean()),
            }
            for label, query in (("before", old), ("after", new)):
                samples = [query.sample(p) for p in points]
                row[label] = abs(samples[1][0] - samples[0][0]) if None not in samples else None
            row["curb_material"] = new.material(center) == mat_id("curb")
            rows.append(row)

    def summary(records):
        length = sum(r["length_m"] for r in records)
        result = {"measured_length_m": length, "segments": len(records)}
        result["curb_material_length_m"] = sum(r["length_m"] for r in records if r["curb_material"])
        for label in ("before", "after"):
            stepped = sum(
                r["length_m"] for r in records if r[label] is not None and r[label] >= 0.03
            )
            result[label] = {
                "step_length_m": stepped,
                "fraction": stepped / length if length else None,
                "missing_segments": sum(r[label] is None for r in records),
            }
        return result

    return {
        "mapped": summary([r for r in rows if r["source"] != "lidar"]),
        "lidar_only": summary([r for r in rows if r["source"] == "lidar"]),
        "combined": summary(rows),
        "method": "Every measured raised-curb segment is sampled 5.5 cm either side of its center on both collision meshes; a >=3 cm height difference counts as a mesh step. Fractions are length weighted.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--breaklines", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = measure(args.before, args.after, args.breaklines, C.OFFICE_BLOCK.core)
    result["extent"] = "office core c_1_2 through c_4_5"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
