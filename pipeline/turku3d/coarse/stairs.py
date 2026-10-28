"""Mapped stair flights: measured landings, real treads and exact terrain openings."""

import json

import numpy as np
import shapely

from turku3d.ground.mesh import Mesh
from turku3d.structures.geometry import Faces

from .geometry import clip_mesh
from .kerbs import collision as collision
from .kerbs import visuals as visuals
from .surfaces import split_boundaries


def prepare(tile, path):
    from turku3d.spatial import rows as source_rows

    rows, areas, seen = [], [], set()
    window = shapely.box(*tile.bbox.buffered(50).as_tuple())
    for _, row in source_rows(path, tile.bbox.buffered(50)):
        tags = json.loads(row["tags"] or "{}")
        if tags.get("highway") != "steps" and tags.get("area:highway") != "steps":
            continue
        geom = shapely.force_2d(row["geometry_local"])
        if not geom.intersects(window):
            continue
        if tags.get("area:highway") == "steps" or tags.get("area") == "yes":
            if geom.geom_type == "LineString" and geom.is_ring:
                geom = shapely.Polygon(geom)
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                areas.append(geom)
        elif geom.geom_type == "LineString" and row["osm_id"] not in seen:
            rows.append((str(row["osm_id"]), geom, tags))
            seen.add(row["osm_id"])
    return rows, areas


def number(value, default):
    try:
        n = float(str(value).removesuffix(" m"))
        return n if np.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def produce(tile, prepared, sample):
    meshes, openings = [], []
    rows, areas = prepared or ((), ())
    for key, line, tags in rows:
        if line.length < 0.2 or tags.get("step_count") == "0":
            continue
        width = np.clip(number(tags.get("width"), 2.5), 0.5, 30)
        # An area:highway outline supplies the full mapped width when the way lacks it.
        area = next((a for a in areas if a.covers(line.interpolate(0.5, normalized=True))), None)
        if area is not None and "width" not in tags:
            width = np.clip(area.area / line.length, 0.5, 30)
        a, b = np.asarray(line.coords)[[0, -1], :2]
        bottom, top = sample(*a), sample(*b)
        if top < bottom:
            line = shapely.LineString(list(line.coords)[::-1])
            bottom, top = top, bottom
        rise = top - bottom
        if rise < 0.06:
            # Incline can recover a flight whose sparse terrain samples are level.
            incline = str(tags.get("incline", ""))
            if incline.endswith("%"):
                rise = line.length * abs(number(incline[:-1], 0)) / 100
                top = bottom + rise
            if rise < 0.06:
                continue
        count = round(number(tags.get("step_count"), max(1, round(rise / 0.16))))
        # Keep collision risers walkable even when mapped counts disagree with terrain.
        count = max(count, int(np.ceil(rise / 0.30)), 1)
        count = min(count, 2000)
        faces = Faces()
        corridor = line.buffer(width / 2, cap_style="flat", join_style="mitre")
        # Preserve every mapped bend and share mitered tread edges. Independent
        # rectangular steps leave triangular holes inside the cut corridor.
        original = np.asarray(line.coords)[:, :2]
        bends = np.r_[0, np.cumsum(np.linalg.norm(np.diff(original, axis=0), axis=1))]
        distances = np.unique(np.r_[np.linspace(0, line.length, count + 1), bends])
        centres = np.array([line.interpolate(float(d)).coords[0] for d in distances])
        directions = np.diff(centres, axis=0)
        directions /= np.linalg.norm(directions, axis=1)[:, None]
        normals = np.c_[-directions[:, 1], directions[:, 0]]
        offsets = (
            np.vstack(
                [
                    normals[0],
                    (normals[:-1] + normals[1:])
                    / np.maximum(1 + (normals[:-1] * normals[1:]).sum(axis=1), 0.1)[:, None],
                    normals[-1],
                ]
            )
            * width
            / 2
        )
        previous = bottom
        for i, (p, q) in enumerate(zip(centres[:-1], centres[1:], strict=True)):
            step = min(count, int((distances[i] + distances[i + 1]) / 2 / line.length * count) + 1)
            low, high = previous, bottom + rise * step / count
            previous = high
            corners = [p + offsets[i], q + offsets[i + 1], q - offsets[i + 1], p - offsets[i]]
            offset = offsets[i]
            tread = np.array([[x, high, z] for x, z in corners])
            faces.face(tread, tread[:, [0, 2]])
            riser = np.array(
                [
                    [(p - offset)[0], low, (p - offset)[1]],
                    [(p + offset)[0], low, (p + offset)[1]],
                    [(p + offset)[0], high, (p + offset)[1]],
                    [(p - offset)[0], high, (p - offset)[1]],
                ]
            )
            faces.face(riser)
            for u, v in ((corners[0], corners[1]), (corners[2], corners[3])):
                faces.face(
                    [
                        [u[0], min(sample(*u), low) - 0.05, u[1]],
                        [v[0], min(sample(*v), low) - 0.05, v[1]],
                        [v[0], high, v[1]],
                        [u[0], high, u[1]],
                    ]
                )
        from .inputs import CELLS
        from .kerbs import terrain_patches

        aprons = []
        for end, outward, height in ((0, -directions[0], bottom), (-1, directions[-1], top)):
            inner = np.array([centres[end] + offsets[end], centres[end] - offsets[end]])
            outer = inner + outward
            apron = shapely.Polygon([inner[0], outer[0], outer[1], inner[1]])
            aprons.append(apron)
            for patch in terrain_patches(
                np.asarray(apron.exterior.coords)[:-1], tile.size / CELLS, tile.bbox.as_tuple()
            ):
                blend = np.clip((patch - centres[end]) @ outward, 0, 1)
                ground = np.array([sample(x, z) for x, z in patch])
                yy = height + (ground - height) * blend
                points = np.c_[patch[:, 0], yy, patch[:, 1]]
                faces.face(points[::-1], patch[::-1])
        corridor = shapely.union_all([corridor, *aprons])
        if faces.indices:
            mesh = clip_mesh(faces.mesh(f"stairs:{key}", "curb"), tile.bbox)
            if mesh is not None:
                meshes.append(mesh)
                openings.append(corridor)
    return meshes, openings


def cut_ground(mesh, openings):
    if not openings or not len(mesh.indices):
        return mesh
    opening = shapely.union_all(openings)
    triangles = mesh.positions[mesh.indices]
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    terrain = np.abs(normal[:, 1]) > 1e-9
    centres = triangles.mean(axis=1)
    material = mesh.material.copy()
    material[shapely.contains_xy(opening, centres[:, 0], centres[:, 2])] = 255
    p, ix, ids, _ = split_boundaries(
        mesh.positions,
        mesh.indices,
        material,
        terrain,
        [(opening, 255)],
        fallback_materials=mesh.material,
    )
    # River banks are vertical solid faces; they have no invertible X/Z plane.
    return Mesh(
        p,
        np.concatenate([ix[ids != 255], mesh.indices[~terrain]]),
        np.concatenate([ids[ids != 255], mesh.material[~terrain]]),
    ).compact()


def stage(cache, tile, prepared, grid_pos, sample, dry, solid, neighbours):
    from pathlib import Path

    from turku3d import config as C
    from turku3d.stage_cache import source_key

    from . import kerbs

    base = Path(__file__).parents[1]
    dependencies = [
        Path(__file__),
        Path(kerbs.__file__),
        Path(__file__).with_name("geometry.py"),
        Path(C.__file__),
        base / "structures/geometry.py",
        base / "col/materials_v1.json",
    ]
    meshes, openings = cache.run(
        "stairs",
        source_key(*dependencies),
        [tile, prepared, grid_pos, neighbours],
        lambda: produce(tile, prepared, sample),
    )
    dry, solid = cache.run(
        "stair_ground",
        source_key(cut_ground, split_boundaries, *dependencies),
        [dry, solid, openings],
        lambda: (cut_ground(dry, openings), cut_ground(solid, openings)),
    )
    return meshes, dry, solid
