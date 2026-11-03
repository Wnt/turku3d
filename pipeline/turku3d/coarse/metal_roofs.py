"""Historic sheet-metal roofs on LoD2 roof facets, instead of the blurred aerial texture.

Historic monumental masonry (massive masonry, or a classical order) with mainly
pitched LoD2 facets gets a physical-scale standing-seam surface. Seams run straight
down each facet and the paint colour is the median of the roof's own valid
orthophoto pixels, so grey castle roofs and red-painted library roofs keep their
measured colour. Mapped non-metal roof materials abstain.
"""

from functools import lru_cache

import numpy as np

from turku3d.package.gltf import GlbMesh

PITCHED_DEG = 15
PITCHED_SHARE = 0.6
# Panel pitch measured on local references (castle courtyard, Vähätori library):
# 0.5-0.6 m standing-seam panels with cross seams every ~2 m.
SEAM_M = 0.55
REPEAT_M = 4.0
NOT_METAL = {
    "tile", "roof_tiles", "tiles", "slate", "concrete", "tar_paper", "asphalt", "glass",
    "grass", "thatch", "stone", "wood", "eternit", "plants", "gravel",
}
DEFAULT = (128, 133, 134)  # Unpainted galvanised grey when no own imagery exists.


def candidate(building):
    """Historic monumental masonry selected by the facade classes' own data rules."""
    from turku3d.facade_kit import renderer

    renderer()  # The shared textures package is importable only through the kit.
    from turkutex.facade_archetypes import classical_order, massive_masonry

    attrs = dict(building.attributes, footprint_m2=float(building.footprint.area))
    if str(attrs.get("roof_material") or "").lower() in NOT_METAL:
        return False
    if massive_masonry(attrs):
        return True
    from turku3d.facade_kit import read_description
    from turku3d.facade_programs import measured, order_evidence

    key = getattr(building, "facade_key", building.key)
    return classical_order(attrs, order_evidence(measured(key), read_description(key)))


def facets(roof, indices):
    tri = np.asarray(roof, float)[np.asarray(indices).reshape(-1, 3)]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(normal, axis=1) / 2
    unit = normal / np.maximum(2 * area, 1e-12)[:, None]
    slope = np.degrees(np.arccos(np.clip(np.abs(unit[:, 1]), 0, 1)))
    return tri, unit, area, slope


def pitched(roof, indices):
    _, _, area, slope = facets(roof, indices)
    total = area.sum()
    return bool(total > 0 and area[slope >= PITCHED_DEG].sum() / total >= PITCHED_SHARE)


def colour(tile, roof, indices, photo):
    """Median of the roof's own valid ortho pixels at its triangle samples."""
    if photo is None:
        return DEFAULT
    from .build import ortho_uv
    from .stages import valid_roof_pixels

    tri = np.asarray(roof, float)[np.asarray(indices).reshape(-1, 3)]
    samples = np.concatenate([tri.mean(axis=1), tri.reshape(-1, 3)])
    uv = ortho_uv(tile, samples)
    inside = np.all((uv >= 0) & (uv < 1), axis=1)
    if not inside.any():
        return DEFAULT
    h, w = photo.shape[:2]
    px = photo[(uv[inside, 1] * h).astype(int), (uv[inside, 0] * w).astype(int)]
    px = px[valid_roof_pixels(px)]
    if len(px) < 8:
        return DEFAULT
    return tuple(int(round(c)) for c in np.median(px, axis=0))


@lru_cache(maxsize=32)
def maps(rgb):
    from turku3d.landmarks.procedural import copper
    from turku3d.landmarks.procedural import maps as pbr

    spec = dict(colour=list(rgb), seam_m=SEAM_M)
    return pbr(*copper(REPEAT_M, REPEAT_M, spec, 11), REPEAT_M, REPEAT_M, metal=True)


def downslope_uv(tri, unit):
    """u along the facet's horizontal eave direction, v up the fall line, in repeats."""
    up = np.array([0.0, 1.0, 0.0])
    along = np.cross(up, unit)
    flat = np.linalg.norm(along, axis=1) < 1e-6
    along[flat] = [1.0, 0.0, 0.0]
    along /= np.linalg.norm(along, axis=1)[:, None]
    fall = np.cross(unit, along)
    u = np.einsum("tij,tj->ti", tri, along)
    v = np.einsum("tij,tj->ti", tri, fall)
    return np.stack([u, v], axis=-1).reshape(-1, 2) / REPEAT_M


def mesh(tile, building, roof, indices, origin, photo):
    """A standing-seam roof mesh, or None when the class or the geometry abstains."""
    if not len(indices) or not pitched(roof, indices) or not candidate(building):
        return None
    tri, unit, _, _ = facets(roof, indices)
    rgb = colour(tile, roof, indices, photo)
    albedo, normal, orm = maps(rgb)
    vertices = tri.reshape(-1, 3)
    return GlbMesh(
        f"roof_{building.key}",
        vertices - origin,
        np.arange(len(vertices), dtype=np.uint32).reshape(-1, 3),
        uvs=downslope_uv(tri, unit),
        image=albedo,
        normal_image=normal,
        orm_image=orm,
        repeat=True,
        extras=dict(
            layer="buildings",
            object_id=f"roof_{building.key}",
            building_id=building.facade_key,
            address=building.attributes.get("address"),
            material_key="roof_metal",
            surface="roof",
            roof_colour="#{:02x}{:02x}{:02x}".format(*rgb),
        ),
    )
