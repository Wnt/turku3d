"""Compare published layer collision with its visual GLBs near the player spawn."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from turku3d import config as C
from turku3d.col.materials import table
from turku3d.layers import read_collision, read_manifest
from turku3d.package.gltf import accessor_array, read_glb


def visual_vertices(path: Path) -> tuple[np.ndarray, list[tuple[np.ndarray, np.ndarray]]]:
    js, binary = read_glb(path.read_bytes())
    vertices = []
    bounds = []

    def append(points: np.ndarray) -> None:
        vertices.append(points)
        bounds.append((points.min(axis=0), points.max(axis=0)))

    def visit(index: int, parent: np.ndarray) -> None:
        node = js["nodes"][index]
        local = np.eye(4)
        if "matrix" in node:
            local = np.asarray(node["matrix"], dtype=float).reshape(4, 4).T
        else:
            x, y, z, w = node.get("rotation", [0, 0, 0, 1])
            local[:3, :3] = np.array(
                [
                    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
                ]
            ) @ np.diag(node.get("scale", [1, 1, 1]))
            local[:3, 3] = node.get("translation", [0, 0, 0])
        transform = parent @ local
        if "mesh" in node:
            for primitive in js["meshes"][node["mesh"]]["primitives"]:
                points = accessor_array(js, binary, primitive["attributes"]["POSITION"])
                instances = node.get("extensions", {}).get("EXT_mesh_gpu_instancing")
                if instances:
                    attrs = instances["attributes"]
                    translations = accessor_array(js, binary, attrs["TRANSLATION"])
                    scales = accessor_array(js, binary, attrs["SCALE"])
                    for translation, scale in zip(translations, scales, strict=True):
                        append(
                            (points * scale + translation) @ transform[:3, :3].T + transform[:3, 3]
                        )
                else:
                    append(points @ transform[:3, :3].T + transform[:3, 3])
        for child in node.get("children", []):
            visit(child, transform)

    for root in js["scenes"][js.get("scene", 0)]["nodes"]:
        visit(root, np.eye(4))
    return (np.concatenate(vertices) if vertices else np.empty((0, 3))), bounds


def primitive_samples(p) -> np.ndarray:
    center = np.asarray(p["center"], dtype=float)
    yaw = float(p["yaw"])
    c, s = math.cos(yaw), math.sin(yaw)
    if p["kind"] == "box":
        h = p["half_extents"]
        offsets = np.array(
            [[x, y, z] for x in (-h[0], h[0]) for y in (-h[1], h[1]) for z in (-h[2], h[2])]
        )
    else:
        h = float(p["height"]) / 2
        r = float(p["radius"])
        offsets = np.array(
            [
                [r * math.cos(a), y, r * math.sin(a)]
                for y in (-h, h)
                for a in np.arange(0, 2 * math.pi, math.pi / 4)
            ]
        )
    offsets[:, [0, 2]] = offsets[:, [0, 2]] @ np.array([[c, -s], [s, c]])
    return center + offsets


def audit(
    layers: Path, center: tuple[float, float], radius: float = 40, tolerance: float = 0.35
) -> list[dict]:
    issues = []
    materials = {m.id: m for m in table()}
    for layer_root in sorted(layers.iterdir()):
        if not (layer_root / "MANIFEST.json").exists():
            continue
        manifest = read_manifest(layer_root)
        glbs = [layer_root / name for name in manifest["tiles"] if name.endswith(".vis.glb")]
        visual = [visual_vertices(path) for path in glbs]
        bounds = [b for _, group in visual for b in group]
        visible = np.concatenate([v for v, _ in visual]) if visual else np.empty((0, 3))
        visible = visible[np.linalg.norm(visible[:, [0, 2]] - center, axis=1) < radius + 15]
        tree = cKDTree(visible) if len(visible) else None
        for name in manifest["tiles"]:
            if not name.endswith(".col.npz"):
                continue
            col = read_collision(layer_root / name)
            tile = name.removesuffix(".col.npz")
            # Sparse samples keep continuous ground and shell audits cheap.
            if len(col.indices):
                triangles = col.positions[col.indices]
                chosen = np.linalg.norm(triangles.mean(axis=1)[:, [0, 2]] - center, axis=1) < radius
                if layer_root.name == "ground":
                    normals = np.cross(
                        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
                    )
                    up = np.abs(normals[:, 1]) / np.maximum(np.linalg.norm(normals, axis=1), 1e-12)
                    relief = np.ptp(triangles[:, :, 1], axis=1)
                    # These materials claim ordinary walkable street pavement. A steep
                    # bank, stair or quay must be classified and modelled explicitly.
                    bad = chosen & np.isin(col.material, [1, 2, 5, 6, 7, 8])
                    bad &= (up < math.cos(math.pi / 4)) & (relief > 0.35)
                    for i in np.flatnonzero(bad):
                        issues.append(
                            dict(
                                layer="ground",
                                tile=tile,
                                kind="blocking_walkable_surface",
                                primitive=None,
                                position=triangles[i].mean(axis=0).tolist(),
                                rise_m=round(float(relief[i]), 3),
                                material=materials[int(col.material[i])].name,
                            )
                        )
                for i in np.flatnonzero(chosen)[:: max(1, int(chosen.sum() / 2500))]:
                    tri = triangles[i]
                    material = materials[int(col.material[i])]
                    if material.cls != "solid":
                        continue
                    point = tri.mean(axis=0)
                    distance = tree.query(point)[0] if tree else math.inf
                    # Vertex distance is meaningful for facades and narrow blockers;
                    # broad walkable ground triangles may have distant vertices.
                    normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
                    vertical = abs(normal[1]) < np.linalg.norm(normal) * 0.5
                    if (
                        vertical
                        and distance
                        > tolerance
                        + max(np.linalg.norm(tri[1] - tri[0]), np.linalg.norm(tri[2] - tri[0])) / 2
                    ):
                        issues.append(
                            dict(
                                layer=layer_root.name,
                                tile=tile,
                                kind="unmatched_wall",
                                position=point.tolist(),
                                distance_m=round(float(distance), 3)
                                if math.isfinite(distance)
                                else None,
                                material=material.name,
                            )
                        )
            for i, p in enumerate(col.primitives):
                material = materials[int(p["material"])]
                if material.cls != "solid":
                    continue
                center3 = np.asarray(p["center"], dtype=float)
                if np.linalg.norm(center3[[0, 2]] - center) > radius:
                    continue
                samples = primitive_samples(p)
                distance = min(
                    (
                        float(np.max(np.maximum(np.maximum(lo - samples, samples - hi), 0)))
                        for lo, hi in bounds
                        if np.linalg.norm((lo + hi)[[0, 2]] / 2 - center3[[0, 2]]) < 5
                    ),
                    default=math.inf,
                )
                if distance > tolerance:
                    issues.append(
                        dict(
                            layer=layer_root.name,
                            tile=tile,
                            kind="oversize_or_unmatched_primitive",
                            primitive=i,
                            position=center3.tolist(),
                            distance_m=round(distance, 3) if math.isfinite(distance) else None,
                            material=material.name,
                        )
                    )
    return issues


def map_issues(
    issues: list[dict],
    center: tuple[float, float],
    radius: float,
    path: Path,
    sweep: dict | None = None,
) -> None:
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.merge import merge

    xmin, xmax = C.ORIGIN_E + center[0] - radius, C.ORIGIN_E + center[0] + radius
    ymin, ymax = C.ORIGIN_N - center[1] - radius, C.ORIGIN_N - center[1] + radius
    ortho = C.data_root() / "raw/ortho/ortho2025/office-block"
    files = []
    for filename in ortho.glob("*.tif"):
        ds = rasterio.open(filename)
        if (
            ds.bounds.right > xmin
            and ds.bounds.left < xmax
            and ds.bounds.top > ymin
            and ds.bounds.bottom < ymax
        ):
            files.append(ds)
        else:
            ds.close()
    fig, ax = plt.subplots(figsize=(9, 9))
    try:
        if files:
            image, _ = merge(files, bounds=(xmin, ymin, xmax, ymax), res=0.15)
            ax.imshow(np.moveaxis(image[:3], 0, -1), extent=(xmin, xmax, ymin, ymax))
        for issue in issues:
            x, _, z = issue["position"]
            ax.plot(C.ORIGIN_E + x, C.ORIGIN_N - z, "rx", markersize=7)
        if sweep:
            for stop in sweep["stops"]:
                x, _, z = stop["end"]
                color = "orange" if stop.get("reason") else "red"
                ax.plot(C.ORIGIN_E + x, C.ORIGIN_N - z, marker="x", color=color, markersize=5)
        ax.plot(C.ORIGIN_E + center[0], C.ORIGIN_N - center[1], "bo", markersize=5)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.set_title(
            f"Collision audit: {len(issues)} geometry candidates, "
            f"{len(sweep['stops']) if sweep else 0} movement stops"
        )
        fig.savefig(path, dpi=150)
    finally:
        for ds in files:
            ds.close()
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layers", type=Path, default=C.work_root() / "build/layers")
    parser.add_argument("--out", type=Path, default=C.work_root() / "reports/collision-audit")
    parser.add_argument("--spawn", type=Path, default=C.work_root() / "dist/tiles/manifest.json")
    parser.add_argument("--radius", type=float, default=40)
    parser.add_argument("--sweep", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.spawn.read_text())
    center = tuple(manifest["spawn"]["position"][i] for i in (0, 2))
    issues = audit(args.layers, center, args.radius)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "issues.json").write_text(json.dumps(issues, indent=2) + "\n")
    sweep = json.loads(args.sweep.read_text()) if args.sweep else None
    map_issues(issues, center, args.radius, args.out / "map.png", sweep)
    unexplained = len(sweep.get("unexplained", [])) if sweep else 0
    print(
        f"collision audit: {len(issues)} geometry candidates, {unexplained} unexplained stops; {args.out}"
    )
    if issues or unexplained:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
