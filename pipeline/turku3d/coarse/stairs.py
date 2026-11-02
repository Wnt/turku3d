"""Mapped stair flights: measured landings, real treads and exact terrain openings."""

import json

import numpy as np
import shapely

from turku3d.ground.mesh import Mesh
from turku3d.structures.geometry import Faces

from .geometry import clip_mesh
from .surfaces import split_boundaries


def prepare(tile, path, survey_paths=()):
    from turku3d.spatial import rows as source_rows

    rows, areas, seen = [], [], set()
    bridges, footways = [], []
    window = shapely.box(*tile.bbox.buffered(50).as_tuple())
    for _, row in source_rows(path, tile.bbox.buffered(50)):
        tags = json.loads(row["tags"] or "{}")
        geom = shapely.force_2d(row["geometry_local"])
        if (
            tags.get("highway") in ("footway", "path", "pedestrian", "cycleway")
            and geom.geom_type == "LineString"
        ):
            (bridges if tags.get("bridge", "no") != "no" else footways).append(geom)
        counted = tags.get("highway") in ("footway", "path", "pedestrian") and tags.get(
            "step_count", "0"
        ) not in ("0", "")
        if tags.get("highway") != "steps" and tags.get("area:highway") != "steps" and not counted:
            continue
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
    from .stair_survey import attach

    attach(rows, survey_paths, bridges, footways)
    return rows, areas


def number(value, default):
    try:
        n = float(str(value).removesuffix(" m"))
        return n if np.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def produce(tile, prepared, sample, decks=()):
    meshes, openings = [], []
    rows, areas = prepared or ((), ())
    window = shapely.box(*tile.bbox.as_tuple())
    for key, line, tags in rows:
        if line.length < 0.2 or tags.get("step_count") == "0":
            continue
        width = np.clip(number(tags.get("width"), 2.5), 0.5, 30)
        # An area:highway outline supplies the full mapped width when the way lacks it.
        area = next((a for a in areas if a.covers(line.interpolate(0.5, normalized=True))), None)
        if area is not None and "width" not in tags:
            width = np.clip(area.area / line.length, 0.5, 30)
        # Include ample room for the bounded miter and one-metre landing aprons.
        # Off-tile flights must not prepare neighbouring terrain just to be clipped away.
        if line.distance(window) > width * 5 + 1:
            continue
        a, b = np.asarray(line.coords)[[0, -1], :2]
        profile = tags.get("_profile")
        knots = np.asarray(profile["stations"] if profile else [0, line.length], float)
        levels = np.asarray(profile["heights"] if profile else [sample(*a), sample(*b)], float)
        bridge_access = False
        for end, p in ((0, a), (-1, b)):
            for deck in decks:
                if (
                    deck.way.get("stair_access")
                    and deck.line.distance(shapely.Point(p)) < deck.way["width"] / 2 + 0.1
                ):
                    target = (
                        float(deck.height_at(np.array([p]))[0])
                        if deck.height_at is not None
                        else float(
                            np.interp(
                                deck.line.project(shapely.Point(p)), deck.stations, deck.heights
                            )
                        )
                    )
                    # Spread mapping/end-band uncertainty over the final flight,
                    # keeping a walkable grade instead of one abrupt deck step.
                    blend = np.clip(1 - np.abs(knots - knots[end]) / 3, 0, 1)
                    levels += (target - levels[end]) * blend
                    bridge_access = True
        bottom, top = levels[[0, -1]]
        if top < bottom:
            line = shapely.LineString(list(line.coords)[::-1])
            bottom, top = top, bottom
            knots, levels = line.length - knots[::-1], levels[::-1]
        # Reversed segment sums can differ by an ulp. Anchor both ends exactly:
        # even a tiny negative station means "from the far end" to Shapely.
        knots[[0, -1]] = [0, line.length]
        rise = top - bottom
        if rise < 0.06:
            # Incline can recover a flight whose sparse terrain samples are level.
            incline = str(tags.get("incline", ""))
            if incline.endswith("%"):
                rise = line.length * abs(number(incline[:-1], 0)) / 100
                top = bottom + rise
                levels[-1] = top
            if rise < 0.06:
                continue
        count = round(number(tags.get("step_count"), max(1, round(rise / 0.16))))
        # Keep collision risers walkable even when mapped counts disagree with terrain.
        count = max(count, int(np.ceil(rise / 0.30)), 1)
        count = min(count, 2000)
        faces, body = Faces(), Faces()
        corridor = line.buffer(width / 2, cap_style="flat", join_style="mitre")
        # Preserve every mapped bend and share mitered tread edges. Independent
        # rectangular steps leave triangular holes inside the cut corridor.
        original = np.asarray(line.coords)[:, :2]
        bends = np.r_[0, np.cumsum(np.linalg.norm(np.diff(original, axis=0), axis=1))]
        bends[-1] = line.length
        # Measured plateaus remain flat; each rising interval owns its own treads.
        levels = np.maximum.accumulate(np.clip(levels, bottom, top))
        counts = np.maximum(1, np.rint(np.diff(levels) / rise * count).astype(int))
        counts = np.maximum(counts, np.ceil(np.diff(levels) / 0.3).astype(int))
        distances = np.unique(
            np.r_[
                bends,
                *[
                    np.linspace(lo, hi, n + 1)
                    for lo, hi, n in zip(knots[:-1], knots[1:], counts, strict=True)
                ],
            ]
        )
        # A tread boundary and mapped bend can represent the same station with
        # different roundoff. Keep the mapped bend, never a zero-length tread.
        near = np.clip(np.searchsorted(bends, distances), 1, len(bends) - 1)
        nearest = np.where(
            np.abs(distances - bends[near - 1]) < np.abs(distances - bends[near]), near - 1, near
        )
        snap = np.abs(distances - bends[nearest]) < 1e-8
        distances[snap] = bends[nearest[snap]]
        distances = np.unique(distances)
        centres = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
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
        mid = (distances[:-1] + distances[1:]) / 2
        interval = np.clip(np.searchsorted(knots, mid, side="right") - 1, 0, len(counts) - 1)
        step = np.minimum(
            counts[interval],
            (
                (mid - knots[interval]) / (knots[interval + 1] - knots[interval]) * counts[interval]
            ).astype(int)
            + 1,
        )
        high = levels[interval] + np.diff(levels)[interval] * step / counts[interval]
        low = np.r_[bottom, high[:-1]]
        left, right = centres + offsets, centres - offsets

        def points(xz, height):
            return np.c_[xz[:, 0], height, xz[:, 1]]

        # A flight is a batch of independent treads, risers and side faces.
        # Share height samples at adjacent step corners, but retain each step's
        # original foundation and flat shading.
        tread_xz = np.stack([left[:-1], left[1:], right[1:], right[:-1]], axis=1)
        tread = np.empty((*tread_xz.shape[:2], 3))
        tread[:, :, [0, 2]] = tread_xz
        tread[:, :, 1] = high[:, None]
        indices = (np.arange(len(high))[:, None, None] * 4 + [[0, 1, 2], [0, 2, 3]]).reshape(-1, 3)
        faces.triangles(tread.reshape(-1, 3), indices, tread_xz.reshape(-1, 2))
        faces.quads(
            np.stack(
                [
                    points(right[:-1], low),
                    points(left[:-1], low),
                    points(left[:-1], high),
                    points(right[:-1], high),
                ],
                axis=1,
            )
        )
        side_ground = [np.array([sample(x, z) for x, z in side]) for side in (left, right)]
        supported = np.maximum(*side_ground) >= np.interp(distances, knots, levels) - 0.6
        for side, ground, reverse in ((left, side_ground[0], False), (right, side_ground[1], True)):
            if bridge_access:
                # Suspended access stairs have a thin soffit, rather than a solid
                # wedge reaching down through the sidewalk beside the building.
                ground = np.where(supported, ground, np.interp(distances, knots, levels) - 0.22)
            u, v = (side[1:], side[:-1]) if reverse else (side[:-1], side[1:])
            gu, gv = (ground[1:], ground[:-1]) if reverse else (ground[:-1], ground[1:])
            body.quads(
                np.stack(
                    [
                        points(u, np.minimum(gu, low) - 0.05),
                        points(v, np.minimum(gv, low) - 0.05),
                        points(v, high),
                        points(u, high),
                    ],
                    axis=1,
                )
            )
        if bridge_access:
            slope = np.interp(distances, knots, levels)
            body.quads(
                np.stack(
                    [
                        points(right[:-1], slope[:-1] - 0.22),
                        points(right[1:], slope[1:] - 0.22),
                        points(left[1:], slope[1:] - 0.22),
                        points(left[:-1], slope[:-1] - 0.22),
                    ],
                    axis=1,
                )
            )
        from .inputs import CELLS
        from .kerbs import terrain_patches

        aprons, apron_collision = [], Faces()
        for end, outward, height in ((0, -directions[0], bottom), (-1, directions[-1], top)):
            if bridge_access and any(
                deck.way.get("stair_access")
                and deck.line.distance(shapely.Point(centres[end])) < deck.way["width"] / 2 + 0.1
                for deck in decks
            ):
                continue  # The bridge landing already continues this endpoint.
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
                patch_xyz = np.c_[patch[:, 0], yy, patch[:, 1]]
                faces.face(patch_xyz[::-1], patch[::-1])
                apron_collision.face(patch_xyz[::-1], patch[::-1])
        corridor = shapely.union_all([corridor, *aprons])
        if faces.indices:
            from .bridge_details import appearance, combine, handrail

            parts = [appearance(faces.mesh(f"stairs:{key}", "curb"), "concrete")]
            if bridge_access:
                parts[0].metadata["collision"] = False
                ramp = Faces()
                slope = np.interp(distances, knots, levels)
                ramp.quads(
                    np.stack(
                        [
                            points(left[:-1], slope[:-1]),
                            points(left[1:], slope[1:]),
                            points(right[1:], slope[1:]),
                            points(right[:-1], slope[:-1]),
                        ],
                        axis=1,
                    )
                )
                if apron_collision.indices:
                    ramp.triangles(
                        np.asarray(apron_collision.positions),
                        np.asarray(apron_collision.indices),
                        np.asarray(apron_collision.uvs),
                    )
                parts.append(
                    appearance(ramp.mesh(f"stairs:{key}:walk", "curb", visual=False), "concrete")
                )
            if body.indices:
                parts.append(
                    appearance(
                        body.mesh(f"stairs:{key}:sides", "wall"),
                        "stone" if bridge_access and supported.mean() > 0.5 else "concrete",
                    )
                )
            if tags.get("handrail") != "no" and (tags.get("handrail") == "yes" or bridge_access):
                for side, name in ((left, "left"), (right, "right")):
                    if tags.get("handrail:" + name) == "no":
                        continue
                    edge = shapely.LineString(side)
                    station = np.r_[0, np.cumsum(np.linalg.norm(np.diff(side, axis=0), axis=1))]
                    parts.extend(
                        handrail(
                            f"stairs:{key}:rail",
                            edge,
                            (station, np.interp(distances, knots, levels)),
                        )
                    )
            if not bridge_access:
                # Retain the exact existing tread collision for ordinary stairs.
                for part in parts:
                    part.material = "curb"
            for part in combine(parts, f"stairs:{key}"):
                mesh = clip_mesh(part, tile.bbox)
                if mesh is not None:
                    meshes.append(mesh)
            openings.append(corridor)
    return meshes, openings


def visuals(tile, meshes):
    from turku3d.package.gltf import GlbMesh

    from .bridge_materials import METRES, maps

    return [
        GlbMesh(
            m.object_id,
            m.positions - tile.origin,
            m.indices,
            uvs=m.uvs / METRES,
            **maps(m.metadata.get("surface", "concrete")),
            repeat=True,
            color=(1, 1, 1, 1),
            extras=dict(
                layer="structures",
                object_id=m.object_id,
                material_key="draft_bridge_" + m.metadata.get("surface", "concrete"),
                kind="retaining_wall" if m.object_id.startswith("retaining:") else "stairs",
            ),
        )
        for m in meshes
        if m.visual
    ]


def stats(meshes, prepared=None):
    emitted = {m.object_id.split(":")[1] for m in meshes if m.object_id.startswith("stairs:")}
    return dict(
        flights=len(emitted),
        lidar_flights=sum(
            key in emitted and "_profile" in tags for key, _, tags in (prepared or ((), ()))[0]
        ),
        retaining_walls=sum(m.object_id.startswith("retaining:") for m in meshes),
        triangles=sum(len(m.indices) for m in meshes),
    )


def collision(cell, meshes, pos, idx, mat):
    from turku3d.col.materials import mat_id

    positions, indices, materials = [pos], [idx], [mat]
    count = len(pos)
    for mesh in meshes:
        if not mesh.metadata.get("collision", True):
            continue
        piece = clip_mesh(mesh, cell.bbox)
        if piece is None:
            continue
        positions.append(piece.positions)
        indices.append(piece.indices + count)
        materials.append(np.full(len(piece.indices), mat_id(mesh.material), np.uint8))
        count += len(piece.positions)
    return np.concatenate(positions), np.concatenate(indices), np.concatenate(materials)


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


def from_inputs(cache, tile, inputs, grid_pos, sample, dry, solid, sources, decks):
    from .stages import kerb_inputs

    neighbours = kerb_inputs(tile, sources) if inputs.stairs[0] or inputs.barriers else None
    return stage(
        cache,
        tile,
        inputs.stairs,
        grid_pos,
        sample,
        dry,
        solid,
        neighbours,
        decks,
        inputs.barriers,
        inputs.water,
        inputs.walkable,
    )


def stage(
    cache,
    tile,
    prepared,
    grid_pos,
    sample,
    dry,
    solid,
    neighbours,
    decks=(),
    barriers=(),
    water=(),
    walks=(),
):
    from pathlib import Path

    from turku3d import config as C
    from turku3d.stage_cache import source_key

    from . import kerbs, retaining

    base = Path(__file__).parents[1]
    dependencies = [
        Path(__file__),
        Path(kerbs.__file__),
        Path(__file__).with_name("geometry.py"),
        Path(__file__).with_name("bridge_details.py"),
        Path(__file__).with_name("stair_survey.py"),
        Path(__file__).with_name("walkable.py"),
        Path(__file__).with_name("tunnels.py"),
        Path(retaining.__file__),
        Path(__file__).with_name("gardens.py"),
        base / "bridges/build.py",
        Path(C.__file__),
        base / "structures/geometry.py",
        base / "col/materials_v1.json",
    ]

    def geometry():
        flights, flight_cuts = produce(tile, prepared, sample, decks)
        paving = [
            p["line"].buffer(p["width"] / 2, cap_style="flat", join_style="mitre") for p in walks
        ]
        walls, wall_cuts = retaining.produce(tile, barriers, sample, [*flight_cuts, *paving], water)
        return [*walls, *flights], [*wall_cuts, *flight_cuts]

    meshes, openings = cache.run(
        "stairs",
        source_key(*dependencies),
        [
            tile,
            prepared,
            grid_pos,
            neighbours,
            [(d.way, d.line, d.stations, d.heights) for d in decks],
            barriers,
            water,
            walks,
        ],
        geometry,
    )

    def ground():
        from .banks import join

        apron = retaining.terrain(meshes, dry)
        cut_dry, cut_solid = cut_ground(dry, openings), cut_ground(solid, openings)
        if apron is not None:
            cut_dry, cut_solid = join(cut_dry, apron), join(cut_solid, apron)
        return cut_dry, cut_solid

    dry, solid = cache.run(
        "stair_ground",
        source_key(cut_ground, split_boundaries, *dependencies),
        [dry, solid, openings, meshes],
        ground,
    )
    return [m for m in meshes if not m.metadata.get("terrain")], dry, solid
