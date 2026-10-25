"""Stable facade order, metric UVs, street visibility and orthophoto roof classes."""

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from turku3d import config as C
from turku3d.package.gltf import GlbMesh


def roof_material(rgb):
    r, g, b = np.asarray(rgb, dtype=float)
    if r > g * 1.25 and r > b * 1.35:
        return "roof_tile_red"
    if g > r * 1.12 and g > b * 0.95:
        return "roof_metal_green"
    return "roof_metal_dark" if np.mean(rgb) < 110 else "roof_metal_gray"


def facade_material(building):
    source = building.properties.get("facade_material", "").lower()
    return (
        "facade_brick"
        if "tiili" in source
        else "facade_wood"
        if "puu" in source
        else "facade_concrete"
        if "betoni" in source
        else "facade_plaster"
    )


def make_visuals(building, shell, streets, blockers, roof_key):
    footprint = building.footprint
    edges = []
    for ring in [
        footprint.exterior,
        *sorted(footprint.interiors, key=lambda r: tuple(r.coords[0])),
    ]:
        coords = np.array(ring.coords)
        edges.extend((a, b) for a, b in zip(coords[:-1], coords[1:], strict=True))
    walls, meshes, features = [], [], []
    for sid, surface in enumerate(shell.surfaces):
        selected = shell.indices[shell.surface_ids == sid]
        if not len(selected):
            continue
        used, inverse = np.unique(selected, return_inverse=True)
        p, idx = shell.positions[used], inverse.reshape(-1, 3).astype(np.uint32)
        tri = shell.positions[selected]
        normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]).sum(axis=0)
        normal /= np.linalg.norm(normal)
        if surface.kind == "WallSurface":
            if abs(normal[1]) > 0.02:
                raise ValueError("facade is not vertical within tolerance")
            mid = p.mean(axis=0)[[0, 2]]
            edge = min(range(len(edges)), key=lambda k: LineString(edges[k]).distance(Point(mid)))
            a, b = edges[edge]
            direction = np.array([normal[2], -normal[0]])
            direction /= np.linalg.norm(direction)
            if direction @ (b - a) < 0:
                direction = -direction
            # All wall polygons, including roof step walls, get a deterministic index.
            walls.append(
                (
                    (edge, float((mid - a) @ direction), float(p[:, 1].min()), tuple(mid)),
                    p,
                    idx,
                    normal,
                    direction,
                )
            )
        else:
            key = roof_key if surface.kind == "RoofSurface" else facade_material(building)
            oid = f"{building.id}/{surface.kind}/{sid}"
            meshes.append(
                GlbMesh(
                    oid,
                    p,
                    idx,
                    uvs=np.column_stack((p[:, 0], -p[:, 2])),
                    extras={
                        "layer": "buildings",
                        "object_id": oid,
                        "building_id": building.id,
                        "material_key": key,
                        "surface_type": surface.kind,
                    },
                )
            )
    for index, (_, p, idx, normal, direction) in enumerate(sorted(walls, key=lambda w: w[0])):
        distances = p[:, [0, 2]] @ direction
        start = p[np.argmin(distances), :][[0, 2]]
        end = start + direction * (distances.max() - distances.min())
        base, top = float(p[:, 1].min()), float(p[:, 1].max())
        uv = np.column_stack((distances - distances.min(), p[:, 1] - base))
        mid = (start + end) / 2
        facing = False
        if not streets.is_empty:
            street = np.array(nearest_points(Point(mid), streets)[1].coords[0])[:2]
            delta = street - mid
            distance = np.linalg.norm(delta)
            if 0.01 < distance <= 60 and delta @ normal[[0, 2]] / distance > 0.35:
                sight = LineString([mid + normal[[0, 2]] * 0.05, street])
                facing = not any(sight.intersects(other) for other in blockers)
        oid = f"{building.id}/facade/{index}"
        props = {
            "layer": "buildings",
            "object_id": oid,
            "building_id": building.id,
            "facade_index": index,
            "street_facing": bool(facing),
            "material_key": facade_material(building),
            "base_height": base,
            "top_height": top,
            "normal": normal.tolist(),
            "width_m": float(distances.max() - distances.min()),
        }
        meshes.append(GlbMesh(oid, p, idx, uvs=uv, extras=props))
        coords = [[float(x + C.ORIGIN_E), float(C.ORIGIN_N - z)] for x, z in (start, end)]
        features.append(
            {
                "type": "Feature",
                "id": oid,
                "properties": props,
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
    return meshes, features


def ortho_roof_key(building, paths):
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    from shapely.ops import transform

    en = transform(
        lambda x, z: (np.asarray(x) + C.ORIGIN_E, C.ORIGIN_N - np.asarray(z)), building.footprint
    )
    samples = []
    for path in paths:
        with rasterio.open(path) as src:
            if not shapely.box(*src.bounds).intersects(en):
                continue
            overlap = shapely.box(*src.bounds).intersection(en)
            if overlap.area == 0:
                continue
            window = (
                from_bounds(*overlap.bounds, transform=src.transform)
                .round_offsets()
                .round_lengths()
            )
            if window.width < 1 or window.height < 1:
                continue
            # Sample at <= 1 m spacing, bounded memory even for whole perimeter blocks.
            height, width = (
                max(1, int(overlap.bounds[3] - overlap.bounds[1])),
                max(1, int(overlap.bounds[2] - overlap.bounds[0])),
            )
            rgb = src.read([1, 2, 3], window=window, out_shape=(3, height, width), masked=True)
            tr = src.window_transform(window) * rasterio.Affine.scale(
                window.width / width, window.height / height
            )
            inside = geometry_mask([shapely.geometry.mapping(en)], (height, width), tr, invert=True)
            valid = inside & ~np.ma.getmaskarray(rgb).any(axis=0)
            if valid.any():
                samples.extend(rgb.data[:, valid].T.tolist())
    if not samples:
        raise ValueError(f"{building.id}: no valid true-ortho roof pixels")
    rgb = np.median(samples, axis=0)
    return roof_material(rgb), {"rgb_median": rgb.tolist(), "pixels": len(samples)}
