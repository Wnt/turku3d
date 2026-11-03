"""Own-source draft surface classes and shared, metre-scale ground materials."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh, vertex_normals
from . import street_materials

KEYS = (
    "asphalt_fresh",
    "asphalt_worn",
    "granite_curb",
    "cobblestone",
    "concrete_slabs",
    "grass",
    "gravel",
    "quay_stone",
    *street_materials.FINISH_KEYS.values(),
    "granite_rock",
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
    "rock": KEYS.index("granite_rock"),
    "quay": 7,
    "wall": 7,
    "curb": 2,
}
SURFACES = {
    "asphalt": "road",
    "paved": "paving_slab",
    "brick": "paving_slab",
    "bricks": "paving_slab",
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
    records = []
    parks, tagged_paths, path_finishes = [], [], []
    for _, row in rows(path, tile.bbox.buffered(50)):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        geom = shapely.force_2d(shapely.make_valid(row["geometry_local"]))
        records.append((tags, geom))
        if tags.get("leisure") in ("park", "garden", "nature_reserve"):
            area = geom
            if area.geom_type == "LineString" and area.is_ring:
                area = shapely.make_valid(shapely.Polygon(area))
            if area.geom_type in ("Polygon", "MultiPolygon"):
                parks.append(area)
        if (
            tags.get("highway") in ("footway", "path") and tags.get("surface")
            and tags.get("bridge", "no") == "no" and tags.get("tunnel", "no") == "no"
            and tags.get("layer", "0") in ("0", "")
            and geom.geom_type in ("LineString", "MultiLineString")
        ):
            tagged_paths.append(geom)
            path_finishes.append(SURFACES.get(tags["surface"]))
    park_index = shapely.STRtree(parks)
    path_index = shapely.STRtree(tagged_paths)
    rocks = []
    for tags, geom in records:
        if tags.get("natural") in ROCK_TAGS:
            area = geom
            if area.geom_type == "LineString" and area.is_ring:
                area = shapely.make_valid(shapely.Polygon(area))
            if area.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection"):
                rocks.append(area)
            continue
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
        if (
            not key
            and tags.get("amenity") == "parking"
            and tags.get("parking") not in ("multi-storey", "underground")
        ):
            key = "road"
        if not key and highway == "footway" and not tags.get("surface"):
            # A missing tag is not evidence of slab joints. In a mapped park, inherit
            # gravel only from a physically joined, explicitly tagged path, with no
            # conflicting joined finish. Full source ways keep this tile-independent.
            connected = path_index.query(geom, predicate="dwithin", distance=0.05)
            if (
                len(connected)
                and all(path_finishes[i] == "gravel" for i in connected)
                and any(parks[i].covers(geom) for i in park_index.query(geom))
            ):
                key = "gravel"
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
    rock = mat_id("rock")
    if rocks:
        # Bare rock replaces land cover only: paths, paving and other mapped
        # surfaces across an outcrop keep their own finish.
        built = shapely.union_all([g for g, m in result if m != mat_id("grass")])
        area = shapely.union_all(rocks).intersection(window).difference(built)
        area = shapely.union_all([g for g in shapely.get_parts(area) if g.area > 0.5])
        if not area.is_empty:
            result.append((area, rock))
    # Broad land cover first, smaller explicitly mapped areas/corridors last; bare
    # rock (already clear of every built surface) after the cover it lies on.
    return sorted(result, key=lambda row: (row[1] == rock, -row[0].area))


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
    # A mapped outcrop outline is coarse: bright lawn green in the ortho is a
    # grassy hollow. Dark canopy over the rock does not say what lies under it.
    rock = out == mat_id("rock")
    if rock.any():
        r, g, b = (rgb_mean(rgb, row, col, k) for k in range(3))
        out[rock & lawn(r, g, b)] = mat_id("grass")
    return out


ROCK_TAGS = ("bare_rock", "rock")


def rgb_mean(rgb, row, col, channel, radius=3):
    """Ortho channel averaged over a small window, so single pixels do not decide."""
    from scipy.ndimage import uniform_filter

    return uniform_filter(rgb[..., channel].astype(np.float32), 2 * radius + 1, mode="nearest")[row, col]


def lawn(r, g, b):
    return (g > 1.08 * r) & (g > 1.2 * b) & (g > 70)


def bare_rock(materials, centroids, rgb, tile, positions, indices, water=()):
    """Unmapped bare rock inside land cover, from the ortho and the lidar terrain.

    Confidence is the product of an ortho score (grey or pink, low saturation,
    not green, bright) and a lidar score (2 m grid roughness or curvature of
    cracked, stepped bedrock), so neither a flat grey square nor a rough lawn
    passes alone. Thresholds come from mapped Vartiovuori outcrops against its
    park lawn. Isolated cells are dropped; paths and paving are never candidates,
    nor is ground within 10 m of water, where quay steps and paved edges are
    just as grey and rough (mapped shore rock still comes from OSM).
    """
    from scipy.ndimage import binary_opening, label

    cover = materials == mat_id("grass")
    n = int(round(np.sqrt(len(positions)))) if len(positions) else 0
    if not cover.any() or n * n != len(positions) or 2 * (n - 1) ** 2 != len(indices):
        return materials
    px = rgb.shape[0]
    col = np.clip(((centroids[:, 0] - tile.origin[0]) / tile.size * px).astype(int), 0, px - 1)
    row = np.clip(((centroids[:, 2] - tile.origin[2]) / tile.size * px).astype(int), 0, px - 1)
    r, g, b = (rgb_mean(rgb, row, col, k) for k in range(3))
    high, low = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    saturation = (high - low) / np.maximum(high, 1)
    ortho = (np.clip((0.3 - saturation) / 0.15, 0, 1) * np.clip((1.12 - g / np.maximum(r, 1)) / 0.1, 0, 1)
             * np.clip((high - 100) / 40, 0, 1))
    h = positions[:, 1].reshape(n, n)
    pad = np.pad(h, 1, mode="edge")
    laplace = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:] - 4 * h) / 4
    rough = np.abs(pad[:-2, :-2] + pad[2:, 2:] - pad[:-2, 2:] - pad[2:, :-2]) / 4
    relief = np.maximum(rough, np.abs(laplace))
    relief = ((relief[:-1, :-1] + relief[1:, 1:] + relief[:-1, 1:] + relief[1:, :-1]) / 4).ravel()
    lidar = np.clip((relief - 0.05) / 0.08, 0, 1).repeat(2)
    confident = cover & (ortho * lidar >= ROCK_CONFIDENCE)
    if len(water):
        shore = shapely.union_all(list(water)).buffer(10.0)
        confident &= ~shapely.intersects_xy(shore, centroids[:, 0], centroids[:, 2])
    mask = binary_opening(confident.reshape(-1, 2).any(axis=1).reshape(n - 1, n - 1), np.ones((2, 2), bool))
    labels, count = label(mask)
    if count:
        mask &= np.bincount(labels.ravel())[labels] >= ROCK_MIN_CELLS
    result = materials.copy()
    result[np.repeat(mask.ravel(), 2) & cover] = mat_id("rock")
    return result


# Joint ortho x lidar confidence for unmapped rock, and the smallest outcrop (2 m cells).
ROCK_CONFIDENCE = 0.6
ROCK_MIN_CELLS = 8


# A face steeper than this (|normal y| below it, about 63 degrees) is a wall,
# quay, bank or step face, not ground a lawn or paving finish lies on.
STEEP = 0.45


def steep(triangles):
    """Near-vertical faces of (n, 3, 3) triangles."""
    face = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    return np.abs(face[:, 1]) < STEEP * np.linalg.norm(face, axis=1)


def walking_finishes():
    """Lawn and paving finishes painted in plan, never onto a near-vertical face."""
    names = ("grass", "soil", "sand", "gravel", "road", "sidewalk", "crossing", "cycleway",
             "cobblestone", "paving_slab", "bridge_deck")
    return np.asarray([*(mat_id(name) for name in names), *street_materials.FINISH_KEYS], np.uint8)


# Terrain still this steep (rise over run) beyond a face continues as natural rock.
ROCK_SLOPE = 0.5
# Lower faces are kerb or step risers that edge their own paving.
RISER = 0.25


def steep_context(mesh, terrain, abutments=None, retaining=None, quays=None):
    """Material from context for near-vertical faces that still carry a lawn or paving id.

    Applied after the closure stage to the walking and the collision ground, so
    both agree: quay stone within reach of water or a bridge abutment
    (`abutments`), the wall material beside a mapped retaining wall
    (`retaining`), quay stone beside a mapped quay (`quays`), natural rock where
    the lidar `terrain` (a HeightQuery) keeps sloping past the face, otherwise soil.
    """
    triangles = mesh.positions[mesh.indices]
    # Soil and sand are bare natural ground, the default here, not a finish.
    finishes = np.setdiff1d(walking_finishes(), [mat_id("soil"), mat_id("sand")])
    faces = (
        steep(triangles)
        & np.isin(mesh.material, finishes)
        & (np.ptp(triangles[:, :, 1], axis=1) > RISER)
    )
    if not faces.any():
        return mesh
    ids = np.flatnonzero(faces)
    centre = triangles[ids].mean(axis=1)[:, [0, 2]]
    normal = np.cross(triangles[ids, 1] - triangles[ids, 0], triangles[ids, 2] - triangles[ids, 0])
    across = normal[:, [0, 2]] / np.maximum(np.linalg.norm(normal[:, [0, 2]], axis=1, keepdims=True), 1e-12)
    h = [np.nan_to_num(terrain(centre + s * across), nan=0.0) for s in (-3.5, -1.5, 1.5, 3.5)]
    beyond = np.maximum(abs(h[1] - h[0]), abs(h[3] - h[2])) / 2
    material = np.where(beyond >= ROCK_SLOPE, mat_id("rock"), mat_id("soil")).astype(np.uint8)
    for zone, name in ((quays, "quay"), (retaining, "wall"), (abutments, "quay")):
        if zone is not None and not zone.is_empty:
            material[shapely.intersects_xy(zone, centre[:, 0], centre[:, 1])] = mat_id(name)
    result = mesh.material.copy()
    result[ids] = material
    return type(mesh)(mesh.positions, mesh.indices, result)


def slope_fallback(positions, indices, materials):
    """Avoid projecting pavement onto bare, near-vertical terrain faces."""
    result = materials.copy()
    result[steep(positions[indices]) & (materials == mat_id("paving_slab"))] = mat_id("rock")
    return result


def visuals(tile, positions, indices, materials, keep, normals=None):
    """Same terrain triangles/normals as collision; metric UVs on steep banks too."""
    normal = vertex_normals(positions, indices) if normals is None else normals
    triangles = positions[indices]
    face = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    axis = np.argmax(np.abs(face), axis=1)
    channels = np.full(len(materials), 4)
    for name, channel in BINDINGS.items():
        channels[materials == mat_id(name)] = channel
    for material, name in street_materials.FINISH_KEYS.items():
        channels[materials == material] = KEYS.index(name)
    rock = KEYS.index("granite_rock")
    moss = rock_tint(positions, indices, keep & (channels == rock)) if (keep & (channels == rock)).any() else None
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
                    colors=moss[ids] if channel == rock and moss is not None else None,
                    color=(1, 1, 1, 1),
                    extras=dict(
                        layer="ground",
                        object_id=f"ground_{tile.id}_{key}_{projection}",
                        material_key=key,
                    ),
                )
            )
    return meshes


def rock_tint(positions, indices, rock):
    """Vertex tint of bare rock: moss toward its edges and in lidar hollows.

    The terrain is the lidar surface, so its curvature at the 2 m grid tells
    rounded knobs (clean, pale rock) from hollows and cracks (moss, thin grass).
    The edge band softens the change from rock to the surrounding lawn.
    """
    from turku3d.ground.mesh import HeightQuery, Mesh

    tint = np.ones((len(positions), 3), np.float32)
    used = np.unique(indices[rock])
    xz = positions[used][:, [0, 2]]
    footprint = shapely.union_all(shapely.polygons(positions[indices[rock]][:, :, [0, 2]]).tolist())
    edge = np.clip(1 - shapely.distance(footprint.boundary, shapely.points(xz)) / MOSS_EDGE, 0, 1)
    height = HeightQuery(Mesh(positions, indices, np.zeros(len(indices), np.uint8)))
    centre = height(xz)
    around = [height(xz + offset) for offset in ([2, 0], [-2, 0], [0, 2], [0, -2])]
    laplace = np.nan_to_num(np.mean(around, axis=0) - centre, nan=0.0)
    hollow = np.clip(laplace / 0.25, 0, 1)
    knob = np.clip(-laplace / 0.25, 0, 1)
    weight = np.clip(0.7 * edge + 0.6 * hollow, 0, 0.85)[:, None]
    tint[used] = (1 - weight) * (1 + 0.06 * knob[:, None]) + weight * np.asarray(MOSS, np.float32)
    return tint


# Linear tint of moss and lichen on granite, and the width of the edge band (m).
MOSS = (0.55, 0.68, 0.38)
MOSS_EDGE = 1.5


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


def channels(meshes):
    """Terrain palette entries only; structures carry their own texture namespaces."""
    selected = set()
    for mesh in meshes:
        key = mesh.extras.get("material_key", "")
        if not key.startswith("draft_"):
            continue
        name = key.removeprefix("draft_")
        if name == "quay_stone_lit":
            name = "quay_stone"
        if name in KEYS:
            selected.add(KEYS.index(name))
        elif mesh.extras.get("layer") == "ground":
            raise ValueError(f"Unknown draft ground surface: {key}")
    return selected


def library(root: Path, base: Path, channels):
    from .surface_library import library as shared_library

    library = shared_library(root, base, channels)
    if KEYS.index("quay_stone") in channels:
        try:
            (library / "draft_quay_stone_lit").symlink_to("draft_quay_stone", target_is_directory=True)
        except FileExistsError:
            pass
    return library


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


DECK_PAINT_LIFT = 0.012  # m above a built bridge deck's top surface
GROUND_PAINT_LIFT = 0.008  # m above the road triangle's plane


def _deck_height(deck, xz):
    """Deck top Y at x/z points: the deck's own sampler, else its axis profile."""
    if deck.height_at is not None:
        return np.asarray(deck.height_at(xz), float)
    stations = shapely.line_locate_point(deck.line, shapely.points(xz))
    return np.interp(stations, deck.stations, deck.heights)


def paint_visual(tile, positions, indices, materials, keep, polygons, decks=()):
    """Drape road paint on the tile's road surface: one ``road_paint`` mesh, colours in COLOR_0.

    ``polygons`` holds explicit OSM marking polygons (white, any road triangle) and rule paint
    items ``(polygon, rgb, deck, kind)`` from ``turku3d.roads.paint``. A deck item lies on a built
    bridge deck (``decks``, at its top surface) and on road triangles only where no deck carries it;
    a ground item never lands on bridge-deck triangles, so a street passing under a bridge never
    paints the deck above it. ``rgb`` multiplies the shared paint colour."""
    import mapbox_earcut

    from turku3d.structures.geometry import Faces

    if not polygons:
        return None
    selected = keep & np.isin(
        materials, [mat_id(k) for k in ("road", "crossing", "cycleway", "bridge_deck")]
    )
    triangles = positions[indices[selected]]
    on_deck = materials[selected] == mat_id("bridge_deck")
    footprints = shapely.polygons(triangles[:, :, [0, 2]]) if len(triangles) else []
    tree = shapely.STRtree(footprints) if len(triangles) else None
    window = shapely.box(*tile.bbox.as_tuple())
    deck_shapes = []
    for deck in decks:
        outline = deck.way.get(
            "outline",
            deck.line.buffer(deck.way["width"] / 2, cap_style="flat", join_style="mitre"),
        )
        shape = outline.buffer(-0.3).intersection(window)
        if not shape.is_empty:
            deck_shapes.append((shape, deck))
    faces = Faces()
    colours = []

    def emit(geometry, height, rgb):
        for part in getattr(geometry, "geoms", [geometry]):
            if part.geom_type != "Polygon" or part.area < 0.0001:
                continue
            rings = [np.asarray(r.coords)[:-1] for r in [part.exterior, *part.interiors]]
            xz = np.concatenate(rings)
            ix = mapbox_earcut.triangulate_float64(
                xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)
            ).reshape(-1, 3)
            p = np.c_[xz[:, 0], height(xz), xz[:, 1]]
            for tri in ix:
                v = p[tri]
                if np.cross(v[1] - v[0], v[2] - v[0])[1] < 0:
                    v = v[::-1]
                faces.face(v, v[:, [0, 2]])
                colours.extend([rgb or (1.0, 1.0, 1.0)] * 3)

    for item in polygons:
        polygon, rgb, deck = (
            (item[0], item[1], item[2]) if isinstance(item, tuple) else (item, None, None)
        )
        if deck:
            for shape, built in deck_shapes:
                if shape.intersects(polygon):
                    emit(
                        polygon.intersection(shape),
                        lambda xz, built=built: _deck_height(built, xz) + DECK_PAINT_LIFT,
                        rgb,
                    )
                    polygon = polygon.difference(shape)
            if polygon.is_empty:
                continue
        if tree is None:
            continue
        for index in tree.query(polygon, predicate="intersects"):
            if deck is False and on_deck[index]:
                continue
            clipped = polygon.intersection(footprints[index])
            if clipped.area < 0.0001:
                continue  # also every vertical (zero-footprint) triangle, which has no plane
            triangle = triangles[index]
            plane = np.linalg.solve(np.c_[triangle[:, [0, 2]], np.ones(3)], triangle[:, 1])
            emit(
                clipped,
                lambda xz, plane=plane: np.c_[xz, np.ones(len(xz))] @ plane + GROUND_PAINT_LIFT,
                rgb,
            )
    if not faces.indices:
        return None
    return GlbMesh(
        "ground_paint",
        np.asarray(faces.positions) - tile.origin,
        np.asarray(faces.indices, np.uint32),
        color=(0.72, 0.71, 0.66, 1),
        roughness=0.9,
        extras=dict(layer="markings", object_id=f"paint_{tile.id}", material_key="road_paint"),
        colors=np.asarray(colours, np.float32),
    )


def split_boundaries(
    positions,
    indices,
    materials,
    keep,
    regions,
    fallback_materials=None,
    *,
    preserve_materials=False,
    keep_steep=False,
):
    """Cut only vector-boundary triangles, keeping the measured terrain plane and smooth normals.

    With `keep_steep` (the visual finish pass), near-vertical faces are not cut
    and no lawn or paving region or ortho fallback repaints them: they keep the
    material of the stage that made them, which collision uses too. A masonry
    finish such as a mapped quay still applies.
    """
    import mapbox_earcut

    def overlay(operation, left, right):
        try:
            return operation(left, right)
        except shapely.errors.GEOSException:
            # Keep successful overlays bit-for-bit. Rare nearly coincident source
            # edges can defeat GEOS' floating overlay even on valid polygons.
            # Retry only the failing pair on a 0.1 micrometre grid; this is far
            # below the exported float32 resolution and leaves collision intact.
            result = []
            for geometry in left:
                try:
                    result.append(operation(geometry, right))
                except shapely.errors.GEOSException:
                    result.append(
                        operation(
                            shapely.make_valid(geometry), shapely.make_valid(right), grid_size=1e-7
                        )
                    )
            return np.asarray(result, dtype=object)

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
    planar = shapely.area(footprints) > 1e-12
    fixed = planar & steep(triangles) if keep_steep and not preserve_materials else np.zeros_like(planar)
    boundary = boundary[planar[boundary] & ~fixed[boundary]]
    if not preserve_materials:
        region_materials = np.asarray([m for _, m in regions], np.uint8)

        def last_region(faces, predicate):
            qi, pi = shapely.STRtree(polygons).query(
                shapely.points(triangles[faces][:, :, [0, 2]].mean(axis=1)), predicate=predicate
            )
            winner = np.full(len(faces), -1, dtype=np.int64)
            np.maximum.at(winner, qi, pi)
            return winner

        # Interior triangles need the same last-region-wins classification as
        # cut pieces. A visual finish can differ from the physical input ids.
        interior = np.flatnonzero(planar & ~fixed & ~np.isin(np.arange(len(selected)), boundary))
        winner = last_region(interior, "within")
        matched = np.flatnonzero(winner >= 0)
        materials = materials.copy()
        materials[selected[interior[matched]]] = region_materials[winner[matched]]
        faces = np.flatnonzero(fixed)
        if len(faces):
            winner = last_region(faces, "intersects")
            masonry = np.flatnonzero(winner >= 0)
            masonry = masonry[~np.isin(region_materials[winner[masonry]], walking_finishes())]
            materials[selected[faces[masonry]]] = region_materials[winner[masonry]]
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
    # Keep exact query order within each region, without scanning the complete
    # intersection table again for every paint region.
    order = np.argsort(region_ids, kind="stable")
    grouped = region_ids[order]
    region_groups, starts, counts = np.unique(grouped, return_index=True, return_counts=True)
    for region, start, count in zip(region_groups[::-1], starts[::-1], counts[::-1], strict=True):
        hits = triangle_ids[order[start:start + count]]
        pieces.append(overlay(shapely.intersection, remaining[hits], polygons[region]))
        owners.append(boundary[hits])
        channels.append(
            materials[selected[boundary[hits]]]
            if preserve_materials
            else np.full(len(hits), regions[region][1], np.uint8)
        )
        remaining[hits] = overlay(shapely.difference, remaining[hits], polygons[region])
    pieces.append(remaining)
    owners.append(boundary)
    fallback = materials if fallback_materials is None else fallback_materials
    channels.append(fallback[selected[boundary]])
    parts, parent = shapely.get_parts(np.concatenate(pieces), return_index=True)
    valid = (shapely.get_type_id(parts) == 3) & (shapely.area(parts) > 1e-12)
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
