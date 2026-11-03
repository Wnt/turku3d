"""One orthophoto colour per complete water body, shared across tile boundaries."""

import hashlib
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.package.gltf import GlbMesh
from turku3d.stage_cache import source_key
from turku3d.tile_layer import identities


def sample_colour(body, path):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    # A canonical interior patch bounds work for long rivers. Small basins use
    # their entire outline, never the containing tile's river or ground colour.
    centre = body.representative_point()
    region = body.intersection(shapely.box(centre.x - 32, centre.y - 32, centre.x + 32, centre.y + 32))
    x0, z0, x1, z1 = region.bounds
    width, height = max(4, int(np.ceil((x1-x0)*2))), max(4, int(np.ceil((z1-z0)*2)))
    with rasterio.open(path) as ds:
        bounds = (C.ORIGIN_E+x0, C.ORIGIN_N-z1, C.ORIGIN_E+x1, C.ORIGIN_N-z0)
        data = ds.read([1, 2, 3], window=from_bounds(*bounds, ds.transform),
                       out_shape=(3, height, width), boundless=True, fill_value=0,
                       resampling=Resampling.average)
    x, z = np.meshgrid(x0+(np.arange(width)+.5)*(x1-x0)/width,
                       z0+(np.arange(height)+.5)*(z1-z0)/height)
    inset = region.buffer(-min(.6, np.sqrt(region.area)*.05))
    rgb = np.moveaxis(data, 0, -1)
    valid = shapely.contains_xy(inset, x, z) & (rgb.max(axis=2)>20) & (rgb.min(axis=2)<245)
    pixels = rgb[valid].astype(float)
    if len(pixels)<6:
        return None
    brightness = pixels.mean(axis=1)
    lo, hi = np.percentile(brightness, [20, 80])
    pixels = pixels[(brightness>=lo)&(brightness<=hi)]
    return (np.median(pixels, axis=0)/255).round(5).tolist()


def prepare(cache, inputs, sources):
    from .sources import NoCoverage, content_paths

    bodies = list(shapely.get_parts(shapely.union_all(inputs.water)))
    bodies.extend(g for g, _, _ in inputs.gardens.get("pools", ()))
    records, paths = [], {}
    for body in bodies:
        if body.is_empty or body.geom_type != "Polygon":
            continue
        key = hashlib.sha256(body.normalize().wkb).hexdigest()[:20]
        point = body.representative_point()
        try:
            path = sources.ortho_source(C.VisualTile.at(point.x, point.y))
        except (NoCoverage, FileNotFoundError):
            path = None
        records.append((body, key, path))
        if path:
            paths.update({str(p): p for p in content_paths([path])})
    return cache.run(
        "water_appearance", source_key(Path(__file__)),
        [[(body, key, str(path)) for body, key, path in records], identities(paths)],
        lambda: [(body, key, sample_colour(body, path) if path else None)
                 for body, key, path in records],
    )


# One mirror plane serves a mesh: measured inland surfaces split into 0.5 m bands, and
# reaches steeper than 2 % (rapids, weirs) form their own unmirrored mesh; measured pools
# undulate by a few centimetres between level nodes and stay mirrored.
BAND = 0.5
SLOPE = 0.02


def visuals(tile, water, records):
    triangles = water.positions[water.indices]
    centres = triangles.mean(axis=1)
    owner = np.full(len(centres), -1, dtype=int)
    for i, (body, _, _) in enumerate(records):
        owner[shapely.intersects_xy(body, centres[:, 0], centres[:, 2])] = i
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    sloped = np.hypot(normal[:, 0], normal[:, 2]) > SLOPE * np.abs(normal[:, 1])
    band = np.where(sloped, -1, np.floor(centres[:, 1] / BAND + 1e-9).astype(int))
    meshes = []
    for i, b in sorted({(int(i), int(b)) for i, b in zip(owner, band)}):
        key, colour = records[i][1:] if i>=0 else ("unmeasured", None)
        selected = water.indices[(owner==i) & (band==b)]
        ids, inverse = np.unique(selected, return_inverse=True)
        suffix = "" if b == 0 else ("_slope" if b < 0 else f"_b{b}")
        extras = dict(layer="ground", object_id=f"water_{tile.id}_{key}{suffix}",
                      material_key="water", sensor=True, water_body=key,
                      reflection_surface=key)
        if b != 0:
            extras["water_band"] = b
        if b < 0:
            extras["water_slope"] = True
        if colour is not None:
            extras["water_color"] = colour  # sRGB, converted to linear by the shader
        meshes.append(GlbMesh(extras["object_id"], water.positions[ids]-tile.origin,
                              inverse.reshape(-1, 3).astype(np.uint32),
                              normals=np.tile(np.float32([0, 1, 0]), (len(ids), 1)),
                              uvs=water.positions[ids][:, [0, 2]]*[1, -1], extras=extras))
    return meshes
