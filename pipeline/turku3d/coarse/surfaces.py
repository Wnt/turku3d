"""Own-source draft surface classes and shared, metre-scale ground materials."""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import shapely
from PIL import Image

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh, vertex_normals

KEYS = (
    "asphalt_fresh",
    "asphalt_worn",
    "granite_curb",
    "cobblestone",
    "concrete_slabs",
    "grass",
    "gravel",
    "quay_stone",
)
BINDINGS = {
    "road": 1,
    "cycleway": 1,
    "bridge_deck": 1,
    "crossing": 1,
    "sidewalk": 4,
    "cobblestone": 3,
    "paving_slab": 4,
    "grass": 5,
    "gravel": 6,
    "soil": 6,
    "sand": 6,
    "rock": 6,
    "quay": 7,
    "curb": 2,
}
SURFACES = {
    "asphalt": "road",
    "paved": "paving_slab",
    "paving_stones": "paving_slab",
    "concrete": "paving_slab",
    "concrete:plates": "paving_slab",
    "sett": "cobblestone",
    "cobblestone": "cobblestone",
    "unhewn_cobblestone": "cobblestone",
    "gravel": "gravel",
    "fine_gravel": "gravel",
    "compacted": "gravel",
    "ground": "soil",
    "dirt": "soil",
    "sand": "sand",
    "grass": "grass",
}


def grid_height(tile, heights, x, z):
    """Sample the actual two ground triangles, including their shared diagonal."""
    cells = heights.shape[0] - 1
    u = min(max((x - tile.origin[0]) / tile.size * cells, 0.0), cells)
    v = min(max((z - tile.origin[2]) / tile.size * cells, 0.0), cells)
    i, j = min(int(u), cells - 1), min(int(v), cells - 1)
    fu, fv = u - i, v - j
    if fu + fv <= 1:
        return float(
            heights[j, i] * (1 - fu - fv) + heights[j, i + 1] * fu + heights[j + 1, i] * fv
        )
    return float(
        heights[j + 1, i + 1] * (fu + fv - 1)
        + heights[j + 1, i] * (1 - fu)
        + heights[j, i + 1] * (1 - fv)
    )


def osm_surfaces(tile, path):
    """Mapped areas and ground-level road/path corridors; no elevated transport painted on terrain."""
    from turku3d.spatial import rows

    result = []
    window = shapely.box(*tile.bbox.as_tuple())
    for _, row in rows(path, tile.bbox.buffered(50)):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") not in (
            "no",
            "building_passage",
        ):
            continue
        if tags.get("building") or tags.get("layer", "0") not in ("0", ""):
            continue
        retaining = (
            tags.get("man_made") in ("quay", "wall", "embankment")
            or tags.get("barrier") in ("retaining_wall", "wall", "city_wall")
            or tags.get("embankment") == "yes"
        )
        key = "quay" if retaining else SURFACES.get(tags.get("surface"))
        highway = tags.get("highway")
        if not key and (
            tags.get("landuse")
            in ("grass", "meadow", "forest", "farmland", "orchard", "allotments")
            or tags.get("natural") in ("grassland", "wood", "scrub")
            or tags.get("leisure") in ("park", "garden", "nature_reserve")
        ):
            key = "grass"
        if not key and highway:
            key = (
                "paving_slab"
                if highway in ("footway", "pedestrian", "steps")
                else "gravel"
                if highway in ("path", "track")
                else "road"
            )
        if not key:
            continue
        geom = shapely.force_2d(shapely.make_valid(row["geometry_local"]))
        if not geom.intersects(window):
            continue
        if (
            geom.geom_type == "LineString"
            and geom.is_ring
            and (not highway or tags.get("area") == "yes")
        ):
            geom = shapely.make_valid(shapely.Polygon(geom))
        if geom.geom_type in ("LineString", "MultiLineString") and key == "quay":
            geom = geom.buffer(0.45, cap_style="flat")
        if geom.geom_type in ("LineString", "MultiLineString") and highway:
            default = (
                2.5
                if highway in ("path", "footway", "steps", "cycleway")
                else 4
                if highway in ("service", "track")
                else 6
            )
            try:
                width = float(str(tags.get("width", default)).removesuffix(" m"))
            except ValueError:
                width = default
            if not np.isfinite(width) or not 0.3 <= width <= 30:
                width = default
            geom = geom.buffer(width / 2, cap_style="flat")
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            result.append((geom.intersection(window), mat_id(key)))
    # Broad land cover first, smaller explicitly mapped areas/corridors last.
    return sorted(result, key=lambda row: -row[0].area)


def classify(centroids, rgb, tile, streets, regions=()):
    # Unmapped neutral ground is paving, not evidence of concrete slab joints.
    out = np.full(len(centroids), mat_id("road"), np.uint8)
    px = rgb.shape[0]
    col = np.clip(((centroids[:, 0] - tile.origin[0]) / tile.size * px).astype(int), 0, px - 1)
    row = np.clip(((centroids[:, 2] - tile.origin[2]) / tile.size * px).astype(int), 0, px - 1)
    r, g, b = (rgb[row, col, k].astype(float) for k in range(3))
    out[(r > 1.05 * g) & (g > 1.1 * b) & (r < 1.6 * np.maximum(b, 1))] = mat_id("gravel")
    # Yellow/dry lawns and fields have little green/red contrast in the true ortho.
    out[(g > 0.95 * r) & (g > 1.15 * b) & (g > 25)] = mat_id("grass")
    # Surveyed city paving wins over estimated OSM corridor widths and RGB fallback.
    for polygons in (regions, streets):
        if not polygons:
            continue
        qi, pi = shapely.STRtree([g for g, _ in polygons]).query(
            shapely.points(centroids[:, [0, 2]]), predicate="within"
        )
        order = np.argsort(pi, kind="stable")
        out[qi[order]] = np.asarray([m for _, m in polygons], np.uint8)[pi[order]]
    return out


def slope_fallback(positions, indices, materials):
    """Avoid projecting pavement onto bare, near-vertical terrain faces."""
    triangles = positions[indices]
    face = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    steep = np.abs(face[:, 1]) < 0.45 * np.linalg.norm(face, axis=1)
    result = materials.copy()
    result[steep & (materials == mat_id("paving_slab"))] = mat_id("rock")
    return result


def visuals(tile, positions, indices, materials, keep, normals=None, ortho=None):
    """Same terrain triangles/normals as collision; metric UVs on steep banks too."""
    image = None
    if ortho is not None:
        encoded = io.BytesIO()
        Image.fromarray(ortho).save(encoded, format="PNG")
        image = encoded.getvalue()
    normal = vertex_normals(positions, indices) if normals is None else normals
    triangles = positions[indices]
    face = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    axis = np.argmax(np.abs(face), axis=1)
    channels = np.full(len(materials), 4)
    for name, channel in BINDINGS.items():
        channels[materials == mat_id(name)] = channel
    meshes = []
    for channel in np.unique(channels[keep]):
        for projection in range(3):
            selected = indices[keep & (channels == channel) & (axis == projection)]
            if not len(selected):
                continue
            ids, inverse = np.unique(selected, return_inverse=True)
            p = positions[ids]
            uv = (
                p[:, [2, 1]]
                if projection == 0
                else p[:, [0, 2]] * [1, -1]
                if projection == 1
                else p[:, [0, 1]]
            )
            key = "draft_" + KEYS[channel]
            meshes.append(
                GlbMesh(
                    f"ground_{key}_{projection}",
                    p - tile.origin,
                    inverse.reshape(-1, 3).astype(np.uint32),
                    normals=normal[ids],
                    uvs=uv,
                    color=(1, 1, 1, 1),
                    image=image if projection == 1 else None,
                    extras=dict(
                        layer="ground",
                        object_id=f"ground_{tile.id}_{key}_{projection}",
                        material_key=key,
                        **({"ground_ortho_size": tile.size} if image and projection == 1 else {}),
                    ),
                )
            )
    return meshes


def fallback(channel, size=256):
    """Deterministic procedural floor when the generated library is not installed."""
    rng = np.random.default_rng(754 + channel)
    palette = [
        (62, 63, 63),
        (87, 86, 82),
        (139, 135, 129),
        (127, 123, 116),
        (153, 150, 142),
        (83, 103, 53),
        (127, 119, 102),
        (122, 117, 109),
    ]
    noise = rng.normal(0, 5, (size, size))
    y, x = np.indices((size, size))
    if channel in (3, 4, 7):
        rows = 4 if channel == 4 else 8 if channel == 3 else 3
        cols = 4 if channel == 4 else 10 if channel == 3 else 4
        joint = ((y * rows % size) < 5) | (
            ((x * cols + (y * rows // size % 2) * size // 2) % size) < 5
        )
        noise[joint] -= 32
    rgb = np.clip(np.asarray(palette[channel]) + noise[..., None], 0, 255).astype(np.uint8)
    return rgb


def neutral_asphalt(rgb):
    """Remove the generated warm cast while retaining linear-light grain contrast."""
    srgb = np.asarray(rgb, dtype=np.float64) / 255
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    light = linear @ [0.2126, 0.7152, 0.0722]
    grey = np.where(light <= 0.0031308, light * 12.92, 1.055 * light ** (1 / 2.4) - 0.055)
    return np.repeat(np.rint(np.clip(grey, 0, 1) * 255).astype(np.uint8)[..., None], 3, axis=-1)


def library(root: Path, base: Path, channels):
    """Retain native albedo detail at 1024 px/2 m; cache and share maps across tiles."""
    sources = [C.work_root() / "textures/library", C.data_root() / "textures/library"]
    source = next(
        (
            p
            for p in sources
            if all((p / f"{s}.array.ktx2").exists() for s in ("albedo", "normal", "orm"))
        ),
        None,
    )
    identity = [
        str(base.resolve()),
        KEYS,
        *(inspect.getsource(f) for f in (library, fallback, neutral_asphalt)),
    ]
    if source:
        identity.extend(
            f"{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
            for p in sorted(source.glob("*.array.ktx2"))
        )
    key = hashlib.sha256(repr(identity).encode()).hexdigest()[:16]
    target = root / f".surface-library-{key}"
    target.mkdir(parents=True, exist_ok=True)
    if base.exists():
        for p in base.iterdir():
            dest = target / p.name
            if not dest.exists():
                try:
                    dest.symlink_to(p.resolve(), target_is_directory=p.is_dir())
                except FileExistsError:
                    pass
    for channel in channels:
        dest = target / ("draft_" + KEYS[channel])
        if dest.exists():
            continue
        with tempfile.TemporaryDirectory(dir=target) as tmp:
            stage = Path(tmp)
            for slot in ("albedo", "normal", "orm"):
                output = stage / f"{slot}.png"
                if source:
                    subprocess.run(
                        [
                            "ktx",
                            "extract",
                            "--transcode",
                            "rgba8",
                            "--layer",
                            str(channel),
                            "--level",
                            "0" if slot == "albedo" else "1" if slot == "normal" else "2",
                            str(source / f"{slot}.array.ktx2"),
                            str(output),
                        ],
                        check=True,
                        capture_output=True,
                    )
                else:
                    pixels = (
                        fallback(channel)
                        if slot == "albedo"
                        else np.full(
                            (256, 256, 3),
                            (128, 128, 255) if slot == "normal" else (255, 230, 0),
                            np.uint8,
                        )
                    )
                    Image.fromarray(pixels).save(output)
                if slot == "albedo" and channel in (0, 1):
                    with Image.open(output) as image:
                        pixels = neutral_asphalt(np.asarray(image.convert("RGB")))
                    Image.fromarray(pixels).save(output)
            (stage / "material.json").write_text(
                json.dumps(
                    dict(
                        uv_scale=[0.5, 0.5], source="own ground library" if source else "procedural"
                    )
                )
            )
            try:
                stage.rename(dest)
            except OSError:
                if not dest.exists():
                    raise
    return target


def mapped_paint(tile, path):
    """Only explicit OSM line markings/crosswalks; points cannot establish a paint heading."""
    from turku3d.facade_kit import renderer
    from turku3d.spatial import rows

    renderer()  # expose the optional texture package to the native pipeline environment
    from turkutex.ground.markings import paint_polygons

    result = []
    window = shapely.box(*tile.bbox.as_tuple())
    for _, row in rows(path, tile.bbox):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        crossing = tags.get("footway") == "crossing" or tags.get("highway") == "crossing"
        kind = "crosswalk" if crossing else tags.get("road_marking")
        if kind not in ("crosswalk", "lane_solid", "lane_dashed", "stop_line"):
            continue
        if tags.get("crossing:markings") == "no" or tags.get("crossing") == "unmarked":
            continue
        if tags.get("bridge", "no") != "no" or tags.get("tunnel", "no") != "no":
            continue
        if tags.get("layer", "0") not in ("0", ""):
            continue
        geom = shapely.force_2d(row["geometry_local"])
        if geom.geom_type != "LineString" or not geom.intersects(window):
            continue
        try:
            width = float(
                tags.get("width", 3 if crossing else 0.5 if kind == "stop_line" else 0.12)
            )
            polygons = paint_polygons(geom, kind, width=width)
        except (ValueError, TypeError):
            continue
        result.extend(p.intersection(window) for p in polygons if p.intersects(window))
    return result


def paint_visual(tile, positions, indices, materials, keep, polygons):
    """Intersect paint with each dry road triangle and interpolate its actual plane."""
    import mapbox_earcut

    from turku3d.structures.geometry import Faces

    if not polygons:
        return None
    road = np.isin(materials, [mat_id(k) for k in ("road", "crossing", "cycleway", "bridge_deck")])
    triangles = positions[indices[keep & road]]
    if not len(triangles):
        return None
    footprints = shapely.polygons(triangles[:, :, [0, 2]])
    tree = shapely.STRtree(footprints)
    faces = Faces()
    for polygon in polygons:
        for index in tree.query(polygon, predicate="intersects"):
            clipped = polygon.intersection(footprints[index])
            for part in getattr(clipped, "geoms", [clipped]):
                if part.geom_type != "Polygon" or part.area < 0.0001:
                    continue
                rings = [np.asarray(r.coords)[:-1] for r in [part.exterior, *part.interiors]]
                xz = np.concatenate(rings)
                ix = mapbox_earcut.triangulate_float64(
                    xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)
                ).reshape(-1, 3)
                triangle = triangles[index]
                plane = np.linalg.solve(np.c_[triangle[:, [0, 2]], np.ones(3)], triangle[:, 1])
                p = np.c_[xz[:, 0], np.c_[xz, np.ones(len(xz))] @ plane + 0.008, xz[:, 1]]
                for tri in ix:
                    v = p[tri]
                    if np.cross(v[1] - v[0], v[2] - v[0])[1] < 0:
                        v = v[::-1]
                    faces.face(v, v[:, [0, 2]])
    if not faces.indices:
        return None
    return GlbMesh(
        "ground_paint",
        np.asarray(faces.positions) - tile.origin,
        np.asarray(faces.indices, np.uint32),
        color=(0.72, 0.71, 0.66, 1),
        roughness=0.9,
        extras=dict(layer="markings", object_id=f"paint_{tile.id}", material_key="road_paint"),
    )


def split_boundaries(positions, indices, materials, keep, regions, fallback_materials=None):
    """Cut only vector-boundary triangles, keeping the measured terrain plane and smooth normals."""
    import mapbox_earcut

    normals = vertex_normals(positions, indices)
    if not regions:
        return positions, indices[keep], materials[keep], normals
    selected = np.flatnonzero(keep)
    triangles = positions[indices[selected]]
    footprints = shapely.polygons(triangles[:, :, [0, 2]])
    polygons = [g for g, _ in regions]
    tree = shapely.STRtree(footprints)
    _, boundary = tree.query(shapely.boundary(polygons), predicate="intersects")
    boundary = np.unique(boundary)
    # Vertical bank faces have no terrain plane in x/z. Preserve them exactly;
    # trying to invert their projected basis used to fail the entire tile.
    boundary = boundary[shapely.area(footprints[boundary]) > 1e-12]
    if not len(boundary):
        return positions, indices[keep], materials[keep], normals
    uncut = np.ones(len(selected), bool)
    uncut[boundary] = False
    remaining = footprints[boundary].copy()
    # GEOS handles arrays in C. Apply each region to all intersecting triangles,
    # in the same last-region-wins order, instead of two Python GEOS calls and
    # a spatial query for every individual triangle/region pair.
    region_ids, triangle_ids = shapely.STRtree(remaining).query(polygons, predicate="intersects")
    pieces, owners, channels = [], [], []
    for region in np.unique(region_ids)[::-1]:
        hits = triangle_ids[region_ids == region]
        pieces.append(shapely.intersection(remaining[hits], polygons[region]))
        owners.append(boundary[hits])
        channels.append(np.full(len(hits), regions[region][1], np.uint8))
        remaining[hits] = shapely.difference(remaining[hits], polygons[region])
    pieces.append(remaining)
    owners.append(boundary)
    fallback = materials if fallback_materials is None else fallback_materials
    channels.append(fallback[selected[boundary]])
    parts, parent = shapely.get_parts(np.concatenate(pieces), return_index=True)
    valid = (shapely.get_type_id(parts) == 3) & (shapely.area(parts) >= 1e-7)
    parts, parent = parts[valid], parent[valid]
    if not len(parts):
        return positions, indices[selected[uncut]], materials[selected[uncut]], normals
    owner = selected[np.concatenate(owners)[parent]]
    material = np.concatenate(channels)[parent]
    rings, polygon = shapely.get_rings(parts, return_index=True)
    ring_lengths = shapely.get_num_coordinates(rings)
    ring_ends = np.cumsum(ring_lengths)
    coordinates = shapely.get_coordinates(rings)
    # Earcut does not need the repeated closing vertex of a linear ring.
    opened = np.ones(len(coordinates), bool)
    opened[ring_ends - 1] = False
    xz = coordinates[opened]
    ring_ends = np.cumsum(ring_lengths - 1)
    polygon_rings = np.r_[0, np.cumsum(np.bincount(polygon, minlength=len(parts)))]
    ends = ring_ends[polygon_rings[1:] - 1]
    starts = np.r_[0, ends[:-1]]
    ix, face_materials = [], []
    for k, (start, end) in enumerate(zip(starts, ends, strict=True)):
        triangles = mapbox_earcut.triangulate_float64(
            xz[start:end],
            (ring_ends[polygon_rings[k] : polygon_rings[k + 1]] - start).astype(np.uint32),
        ).reshape(-1, 3)
        ix.append(triangles + start)
        face_materials.append(np.full(len(triangles), material[k], np.uint8))
    ix = np.concatenate(ix)
    vertex_owner = np.repeat(owner, ends - starts)
    triangle = positions[indices[vertex_owner]]
    a, b = (
        triangle[:, 1, [0, 2]] - triangle[:, 0, [0, 2]],
        triangle[:, 2, [0, 2]] - triangle[:, 0, [0, 2]],
    )
    delta = xz - triangle[:, 0, [0, 2]]
    determinant = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    u = (delta[:, 0] * b[:, 1] - delta[:, 1] * b[:, 0]) / determinant
    v = (a[:, 0] * delta[:, 1] - a[:, 1] * delta[:, 0]) / determinant
    weights = np.c_[1 - u - v, u, v]
    points = np.c_[xz[:, 0], np.einsum("ij,ij->i", weights, triangle[:, :, 1]), xz[:, 1]]
    faces = points[ix]
    flip = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])[:, 1] < 0
    ix[flip] = ix[flip][:, [0, 2, 1]]
    n = np.einsum("ij,ijk->ik", weights, normals[indices[vertex_owner]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return (
        np.concatenate([positions, points]),
        np.concatenate([indices[selected[uncut]], ix + len(positions)]).astype(np.uint32),
        np.concatenate([materials[selected[uncut]], *face_materials]),
        np.concatenate([normals, n]),
    )
