"""Surveyed wall matching, exposed shell ownership and generated facade assembly."""

import numpy as np

from turku3d import config as C
from turku3d.package.gltf import GlbMesh


def match_wall(points, facades):
    """Only near-collinear, overlapping walls of the same stable building may share a map."""
    return match_prepared(points, prepare_walls(facades))


def prepare_walls(facades):
    """Convert invariant surveyed segments once for all of a building's roof fragments."""
    rows = []
    for facade in facades:
        en = np.asarray(facade["coordinates"], float)[:, :2]
        segment = np.column_stack((en[:, 0] - C.ORIGIN_E, C.ORIGIN_N - en[:, 1]))
        width = float(np.linalg.norm(segment[-1] - segment[0]))
        if width >= 0.1:
            outward = (
                np.asarray(facade["normal"])[[0, 2]] if facade.get("normal") is not None else [0, 0]
            )
            rows.append((facade, segment[0], (segment[-1] - segment[0]) / width, width, outward))
    if not rows:
        return None
    return ([r[0] for r in rows], *[np.asarray([r[i] for r in rows]) for i in range(1, 5)])


def match_prepared(points, prepared, *, min_coverage=0.8):
    if prepared is None:
        return None
    facades, starts, tangents, widths, outward = prepared
    xz = np.asarray(points)[:, [0, 2]]
    a, b = xz[0], xz[1]
    length = float(np.linalg.norm(b - a))
    if length < 1e-6:
        return None
    direction = (b - a) / length
    delta = xz[None, :, :] - starts[:, None, :]
    normals = np.column_stack((-tangents[:, 1], tangents[:, 0]))
    distance = np.abs(np.einsum("nvi,ni->nv", delta, normals)).max(axis=1)
    along = np.einsum("nvi,ni->nv", delta, tangents)
    coverage = (
        np.maximum(0, np.minimum(along.max(axis=1), widths) - np.maximum(along.min(axis=1), 0))
        / length
    )
    side = outward @ [direction[1], -direction[0]]
    valid = (
        (np.abs(tangents @ direction) >= 0.995) & (distance <= 0.65) & (coverage >= min_coverage)
    )
    valid &= (np.linalg.norm(outward, axis=1) == 0) | (side >= 0.95)
    matches = np.flatnonzero(valid)
    if not len(matches):
        return None
    index = matches[np.argmin((distance + 1 - coverage)[matches])]
    facade = facades[index]
    uv = np.column_stack((along[index], np.asarray(points)[:, 1] - facade["base_height"]))
    transform = facade["metadata"].get("uv")
    if transform:
        scale, offset = np.asarray(transform["scale"]), np.asarray(transform["offset"])
        # Unique maps originally clamp at the wall boundary. Preserve that behavior before
        # atlas packing, where sampling past an edge would instead leak into another wall.
        uv = (np.clip(uv * scale + offset, 0, 1) - offset) / scale
    return facade, uv


FRONT = 0.001  # m: probe distance in front of a wall. Coincident party walls (float noise ~0.1 mm)
# are inside the neighbour; a sliver part thinner than this is left to the same-facing rule.


def shell_solids(shells, emit, groups):
    """Every shell's roof triangles with the shell's base, group and emission, for exposed_wall_quads.
    A shell is one solid: its footprint from ``base`` up to its roof (coarse/extrusion._extrude)."""
    tris, base, group, emitted = [], [], [], []
    for (b, shell), e, g in zip(shells, emit, groups, strict=True):
        if shell[0] is None or shell[1] is None:  # walls without a roof (test fixtures) fill nothing
            continue
        roof, index = np.asarray(shell[0], float), np.asarray(shell[1], int).reshape(-1, 3)
        if not len(index):
            continue
        tris.append(roof[index])
        base += [b.base] * len(index)
        group += [g] * len(index)
        emitted += [e] * len(index)
    if not tris:
        return np.empty((0, 3, 3)), np.empty(0), np.empty(0, int), np.empty(0, bool)
    return np.concatenate(tris), np.asarray(base, float), np.asarray(group), np.asarray(emitted, bool)


def exposed_wall_quads(walls, *, with_owners=False, emit=None, groups=None, solids=None):
    """Paint one surface per outward wall, including near-coincident LOD2 slivers, and no wall
    inside a neighbouring solid.

    Roof-part solids remain closed for collision. Their independently simplified
    rings can put two outward faces within millimetres of each other, especially
    where a narrow fallback roof strip borders a surveyed roof. Subtract covered
    areas in wall metres before assigning facade programs, keeping the taller
    face and any exposed remainder.

    Every roof plane is its own solid from the base up, so each boundary between two
    roof parts (and each party wall between two emitted buildings) carried two
    back-to-back walls from the ground to the roof, inside the building: up to five
    nested layers under a stepped roof, ~40 % of a city-centre tile's wall triangles
    (t0_0_-1, Jonni 2026-11-08). So in two steps:

    1. A wall loses what a solid fills right in front of it (``solids``: the roof triangles of
       every shell with its base, group and emission; see ``shell_solids``). Parts of one
       building (``groups``) always trim each other, so both tiles of a seam see the same
       building; different buildings only when both are emitted here (a context tile may load
       later or come from another generation). Solids, not opposite-facing walls: footprint
       rings that run along one edge both ways put an inward-facing wall on a street facade
       with nothing in front of it (train16 t0_0_-1: the end wall of Building_567784, the
       market pavilion, the roof steps of Uusi Börs).
    2. Same-facing walls then share what step 1 left of them, highest first. Owning by the
       untrimmed walls painted nothing where a millimetre sliver part stood in front of a
       taller facade: step 1 removed the facade there and step 2 gave the area to the
       facade (train12 t0_0_-1: full-height slots in Uusi Börs and the market pavilion).
    """
    import shapely
    from shapely import affinity

    from turku3d.wall_surfaces import trapezoids

    quads = np.asarray(walls).reshape(-1, 4, 3)
    if not len(quads):
        return (walls, []) if with_owners else walls
    emitted = np.ones(len(quads), bool) if emit is None else np.asarray(emit, bool)
    group = np.zeros(len(quads), int) if groups is None else np.asarray(groups)
    starts = quads[:, 0][:, [0, 2]]
    directions = quads[:, 1][:, [0, 2]] - starts
    lengths = np.linalg.norm(directions, axis=1)
    directions = directions / lengths[:, None]
    priority = sorted(range(len(quads)), key=lambda i: (-quads[i, 2:, 1].max(), -lengths[i], i))
    rank = np.argsort(priority)
    low, high = quads.min(axis=1), quads.max(axis=1)
    bounds = shapely.box(
        low[:, 0] - 0.006, low[:, 2] - 0.006, high[:, 0] + 0.006, high[:, 2] + 0.006
    )
    tree = shapely.STRtree(bounds)

    def surface(i):
        return shapely.Polygon(np.c_[[0, lengths[i], lengths[i], 0], quads[i, :, 1]])

    def neighbours(i):
        """Higher-ranked walls facing the same way within 6 mm of wall i, with the shared
        frontage interval of each in i's along-wall metres."""
        direction = directions[i]
        others = tree.query(bounds[i])
        others = others[others != i]
        others = others[
            (rank[others] < rank[i])
            & (directions[others] @ direction > 0.99998)
            & (high[others, 1] > low[i, 1])
            & (low[others, 1] < high[i, 1])
        ]
        if not len(others):
            return others, None
        normal = np.array([-direction[1], direction[0]])
        delta = quads[others][:, :, [0, 2]] - starts[i]
        along = delta @ direction
        # Compare planes only over their shared frontage. A long surveyed edge
        # may diverge beyond a short sliver without changing that overlap.
        across = delta[:, :2] @ normal
        run = along[:, 1] - along[:, 0]
        slope = (across[:, 1] - across[:, 0]) / np.where(np.abs(run) < 1e-9, 1e-9, run)
        shared_left = np.maximum(0, along[:, :2].min(axis=1))
        shared_right = np.minimum(lengths[i], along[:, :2].max(axis=1))
        # Resolve every shared interval within 6 mm, including diverging edges
        # that cross the 5 mm validation band. The extra millimetre covers export
        # rounding; the angular margin likewise covers narrow float32 triangles.
        intercept = across[:, 0] - along[:, 0] * slope
        parallel = abs(slope) < 1e-12
        safe_slope = np.where(parallel, 1, slope)
        ends_a, ends_b = (-0.006 - intercept) / safe_slope, (0.006 - intercept) / safe_slope
        near_left = np.where(parallel, -np.inf, np.minimum(ends_a, ends_b))
        near_right = np.where(parallel, np.inf, np.maximum(ends_a, ends_b))
        near_right = np.where(parallel & (abs(intercept) > 0.006), -np.inf, near_right)
        shared_left = np.maximum(shared_left, near_left)
        shared_right = np.minimum(shared_right, near_right)
        keep = shared_right > shared_left
        return others[keep], [shapely.box(a, -10000, b, 10000) for a, b in
                              zip(shared_left[keep], shared_right[keep], strict=True)]

    def polygonal(geometry):
        """Only the areas: a band or region that merely touches another yields lines and points,
        and GEOS cannot overlay such mixed collections (t0_0_-1: "Unable to determine overlay
        result geometry dimension")."""
        parts = [g for g in shapely.get_parts(geometry) if g.geom_type == "Polygon" and g.area > 1e-9]
        return shapely.MultiPolygon(parts) if parts else shapely.Polygon()

    def minus(region, cover):
        # Roof triangles cut a solid's cover into many collinear trapezoids: weld them (10 µm)
        # so the remainder splits into few slabs.
        cover = polygonal(shapely.union_all([polygonal(c) for c in cover]).simplify(1e-5))
        if cover.is_empty:
            return region
        try:
            return polygonal(region.difference(cover))
        except shapely.errors.GEOSException:
            # Nearly coincident float edges: snap both to a micrometre grid and retry.
            return polygonal(shapely.set_precision(region, 1e-6).difference(shapely.set_precision(cover, 1e-6)))

    exposed = {}

    if solids is not None:
        roof_tris, roof_base, roof_group, roof_emit = solids
        roof_tree = shapely.STRtree(shapely.polygons(roof_tris[:, :, [0, 2]]))

    def filled(i):
        """Where solids stand within FRONT of wall i, as trapezoids in i's along metres: each roof
        triangle the probe line crosses fills from its base up to its roof."""
        outward = np.array([directions[i][1], -directions[i][0]])  # footprints run counter-clockwise
        p0 = starts[i] + FRONT * outward
        delta = directions[i] * lengths[i]
        found = roof_tree.query(shapely.LineString([p0, p0 + delta]))
        found = found[(roof_group[found] == group[i]) | (roof_emit[found] & emitted[i])]
        def cross(u, v):
            return u[0] * v[1] - u[1] * v[0]

        pieces = []
        for t in found:
            corners, heights = roof_tris[t][:, [0, 2]], roof_tris[t][:, 1]
            if cross(corners[1] - corners[0], corners[2] - corners[0]) < 0:
                corners, heights = corners[[0, 2, 1]], heights[[0, 2, 1]]
            low, high = 0.0, 1.0
            for k in range(3):  # clip p0 + s * delta to the inside of each edge
                edge = corners[(k + 1) % 3] - corners[k]
                start = cross(edge, p0 - corners[k])
                rate = cross(edge, delta)
                if abs(rate) < 1e-15:
                    if start < 0:
                        low, high = 1.0, 0.0
                    continue
                cut = -start / rate
                if rate > 0:
                    low = max(low, cut)
                else:
                    high = min(high, cut)
            if high - low < 1e-9:
                continue
            ends = []
            for at in (low, high):
                point = p0 + at * delta
                (u, v), *_ = np.linalg.lstsq(
                    np.c_[corners[1] - corners[0], corners[2] - corners[0]], point - corners[0], rcond=None)
                ends.append(heights[0] + u * (heights[1] - heights[0]) + v * (heights[2] - heights[0]))
            (h0, h1), base = ends, roof_base[t]
            if max(h0, h1) <= base:
                continue
            a, b = low * lengths[i], high * lengths[i]
            if h0 < base:  # the roof dips under the floor part-way: keep the part above it
                a, h0 = a + (b - a) * (base - h0) / (h1 - h0), base
            elif h1 < base:
                b, h1 = a + (b - a) * (h0 - base) / (h0 - h1), base
            pieces.append(shapely.Polygon([(a, base), (b, base), (b, h1), (a, h0)]))
        return pieces

    def level(region, whole):
        """Give every slab a level bottom. Facade programs draw storeys from a slab's bottom up and
        paint only the band above its highest corner: a bottom along a sloping roof in front left
        the triangle below open (train18 t0_0_-1, between the Uusi Börs towers). A wall's own
        bottom is level, so a sloping one runs along a solid in front: extending the slab down to
        its lowest corner paints only inside that solid."""
        slabs = []
        for xy in trapezoids(region):
            if abs(xy[0, 1] - xy[1, 1]) > 1e-9:
                xy = xy.copy()
                xy[0, 1] = xy[1, 1] = min(xy[0, 1], xy[1, 1])
            slabs.append(shapely.Polygon(xy))
        return polygonal(shapely.union_all([region, *slabs]).intersection(whole))

    def outside(i):
        """Step 1: wall i less the solids standing right in front of it (i's along metres)."""
        if i not in exposed:
            region = whole = surface(i)
            if solids is not None:
                region = minus(region, filled(i))
                if not region.is_empty and not region.equals(whole):
                    region = level(region, whole)
            exposed[i] = region
        return exposed[i]

    result, owners = [], []
    for i, vertices in enumerate(quads):
        if not emitted[i]:
            continue
        region = outside(i)
        others, bands = neighbours(i)
        if len(others):
            cover = []
            for j, o in enumerate(others):
                # Step 2: a higher-ranked same-facing wall owns what step 1 left of it.
                scale = float(directions[o] @ directions[i])
                offset = float((starts[o] - starts[i]) @ directions[i])
                mine = affinity.affine_transform(outside(o), [scale, 0, 0, 1, offset, 0])
                cover.append(polygonal(mine).intersection(bands[j]))
            region = minus(region, cover)
        if region.is_empty:
            continue
        if abs(region.area - surface(i).area) < 1e-9 and region.equals(surface(i)):
            result.append(vertices)
            owners.append(i)
            continue
        for xy in trapezoids(region):
            points = np.array([vertices[0]] * 4)
            points[:, [0, 2]] = starts[i] + xy[:, :1] * directions[i]
            points[:, 1] = xy[:, 1]
            result.append(points)
            owners.append(i)
    output = np.asarray(result, float).reshape(-1, 3)
    return (output, owners) if with_owners else output


def assemble_walls(shells, context=()):
    """Resolve all roof parts, footprint and passage splits together before paint."""
    requested = len(shells)
    # Stable source order makes shared-frontage ownership independent of which
    # neighbour builds first. Context is used for occlusion, never emitted here.
    all_shells = [*shells, *context]
    order = sorted(
        range(len(all_shells)),
        key=lambda i: (all_shells[i][0].key, all_shells[i][0].base, all_shells[i][0].footprint.wkb),
    )
    shells = [all_shells[i] for i in order]
    counts = [len(shell[4]) // 4 for _, shell in shells]
    if not sum(counts):
        return [np.empty((0, 3)) for _ in range(requested)]
    keys = {key: n for n, key in enumerate(sorted({b.key for b, _ in shells}))}
    shell_emit = np.array(order) < requested
    shell_group = [keys[b.key] for b, _ in shells]
    walls, owners = exposed_wall_quads(
        np.concatenate([shell[4] for _, shell in shells]),
        with_owners=True,
        emit=np.repeat(shell_emit, counts),
        groups=np.repeat(shell_group, counts),
        solids=shell_solids(shells, shell_emit, shell_group),
    )
    source = np.repeat(np.arange(len(shells)), counts)
    owner = source[owners]
    quads = walls.reshape(-1, 4, 3)
    result = {original: quads[owner == i].reshape(-1, 3) for i, original in enumerate(order)}
    return [result[i] for i in range(requested)]


def facade_layouts(shells):
    """Recover one paint coordinate frame across passage-split building solids."""
    import copy
    import shapely

    grouped = {}
    for b, shell in shells:
        grouped.setdefault(b.key, []).append((b, shell))
    result = {}
    for key, parts in grouped.items():
        if len(parts) == 1:
            continue
        owner = copy.copy(max(parts, key=lambda row: row[0].footprint.area)[0])
        owner.base = min(b.base for b, _ in parts)
        owner.footprint = shapely.orient_polygons(
            shapely.union_all([b.footprint for b, _ in parts]).simplify(.02)
        )
        heights = []
        for _, shell in parts:
            for p in shell[4].reshape(-1, 4, 3):
                width = float(np.linalg.norm(p[1, [0, 2]] - p[0, [0, 2]]))
                heights.append((max(.1, float(p[2:, 1].min() - owner.base)), width))
        if not heights:
            continue
        half, accumulated = sum(w for _, w in heights) / 2, 0
        for height, width in sorted(heights):
            accumulated += width
            if accumulated >= half:
                break
        result[key] = owner, height
    return result


def split_storey(vertices, height):
    """The wall quad below and above a horizontal line (the upper part is None above the top)."""
    v = np.asarray(vertices, float)
    lower = v.copy()
    lower[2, 1], lower[3, 1] = min(v[2, 1], height), min(v[3, 1], height)
    if min(v[2, 1], v[3, 1]) <= height + 0.05:
        return lower, None
    upper = v.copy()
    upper[0, 1] = upper[1, 1] = height
    return lower, upper


def base_course(vertices, wall, style):
    """A blank wall over a base course of another material (a monolith's granite course) paints
    the course with that material's own atlas cell. Tinting the wall material's cell in the
    base colour left a flat, untextured grey band under the render or brick."""
    from turku3d.facade_inference import material as normalize_material
    from turku3d.facade_inference import supported
    from turku3d.facade_kit import MATERIALS

    # Only the class's measured course (facade_monolith.blank_wall), never a sampled rule material.
    if (style["module"] < 42 or wall.get("measured_geometry") or "monolith_role" not in wall
            or not supported(wall["base"].get("material"))):
        return [(vertices, wall, style)]
    base = normalize_material(wall["base"]["material"]["value"] or "")
    if base not in MATERIALS or 42 + MATERIALS.index(base) == style["module"]:
        return [(vertices, wall, style)]
    top = float(style["origin"][1]) + float(style["ground"])  # the ground storey, from the base
    if top <= float(vertices[:2, 1].min()) + 0.05:
        return [(vertices, wall, style)]
    lower, upper = split_storey(vertices, top)
    # The whole course samples the ground row: its top coincides with the storey line.
    course = {**style, "module": 42 + MATERIALS.index(base), "wall": style["base"],
              "ground": style["ground"] + 0.5}
    return [(lower, wall, course)] + ([(upper, wall, style)] if upper is not None else [])


def wall_meshes(b, walls, origin, layout=None):
    if not len(walls):
        return []
    from turku3d.facade_component_geometry import geometry, plan, slots
    from turku3d.facade_program_geometry import wall_parts
    from turku3d.facade_relief import composed_parts
    from turku3d.facade_programs import compile_wall, resolve, measured
    from turku3d.facade_apertures import cuts, volumes
    from turku3d.open_ground import edge_plans, interior_meshes
    from turku3d.facade_garage import garage_wall, is_garage

    garage = is_garage(b)

    meshes = []
    # Each extrusion ring closes before the next roof part starts. Its lowest top
    # is the eave, including when a gable side has several collinear segments.
    eaves = {}
    ring_start = 0
    for end in range(0, len(walls), 4):
        if np.linalg.norm(walls[end + 1, [0, 2]] - walls[ring_start, [0, 2]]) < 1e-6:
            eave = float(walls[ring_start : end + 4].reshape(-1, 4, 3)[:, 2:, 1].min())
            eaves.update((i, eave) for i in range(ring_start, end + 4, 4))
            ring_start = end + 4
    owner, height = layout if layout is not None else (b, None)
    program = resolve(owner, walls, eaves, layout_height=height)
    from turku3d.facade_slab import is_slab, slab_wall, storey_pitch

    slab = not garage and is_slab(b, program)
    pitch = storey_pitch(b, walls) if slab else None
    from turku3d.night_lighting import use_class
    night_use = use_class(getattr(owner, "attributes", {}))
    apertures = volumes(measured(b.facade_key))
    instructions, program_parts = [], []
    previous_instruction = None
    # The extrusion contract emits four vertices and two triangles for each vertical wall.
    for start in range(0, len(walls), 4):
        vertices = walls[start : start + 4]
        wall = program["walls"][start // 4]
        if garage and not wall.get("measured_wall_id"):
            wall = program["walls"][start // 4] = garage_wall(wall, vertices, b)
        elif slab and not wall.get("measured_wall_id"):
            wall = program["walls"][start // 4] = slab_wall(wall, vertices, b, pitch)
        plans = plan(vertices, wall) + cuts(vertices, apertures)
        plans += edge_plans(vertices, b, plans)
        components, _ = geometry(vertices, plans, b.facade_key, origin)
        meshes.extend(components)
        wall["rendered_components"] = [p["kind"] for p in plans]
        reviewed = bool(wall.get("measured_wall_id")) and (
            "render_sections" not in wall or bool(wall["render_sections"])
        )
        wall["paint_source"] = (
            "reviewed-program"
            if reviewed
            else "existing-description"
            if program.get("description_source")
            else "rule-sampled"
        )
        style = compile_wall(wall, program["storeys"]["value"])
        style["origin"][1] = owner.base
        style["night_use"] = night_use
        segments = [(vertices, wall, style)]
        if wall.get("ground_band"):
            # A glazed street storey under a blank volume (facade_monolith.ground_band).
            band = wall["ground_band"]
            lower, upper = split_storey(vertices, float(vertices[:2, 1].min()) + band["height_m"])
            band_style = compile_wall(band["wall"], 2)
            band_style["origin"][1] = owner.base
            band_style["night_use"] = night_use
            segments = [(lower, band["wall"], band_style)] + ([(upper, wall, style)] if upper is not None else [])
        else:
            segments = base_course(vertices, wall, style)
        for points, uv, instruction in (part for v, w, st in segments for part in wall_parts(v, w, st)):
            instruction = {k: v for k, v in instruction.items() if k != "unreviewed"}
            parts = composed_parts(points, uv, instruction, plans)
            for p, tex, instruction in parts:
                # Coalesce only adjacent ranges with identical generated paint.
                if instruction == previous_instruction:
                    instructions[-1]["count"] += 4
                else:
                    instructions.append(dict(start=len(program_parts) * 4, count=4, **instruction))
                    previous_instruction = instruction
                program_parts.append((p, tex))
    meshes.extend(interior_meshes(b, walls, origin))
    if program_parts:
        meshes.append(
            GlbMesh(
                f"walls_{b.key}",
                np.concatenate([p for p, _ in program_parts]) - origin,
                (
                    np.array([[0, 3, 2], [0, 2, 1]], np.uint32)[None, :, :]
                    + 4 * np.arange(len(program_parts), dtype=np.uint32)[:, None, None]
                ).reshape(-1, 3),
                uvs=np.concatenate([uv for _, uv in program_parts]),
                color=(1, 1, 1, 1),
                extras=dict(
                    layer="buildings",
                    object_id=f"walls_{b.key}",
                    building_id=b.facade_key,
                    material_key="facade_kit",
                    surface="wall",
                    base_height=b.base,
                    top_height=b.top,
                    # The legacy fallback describes the first quad; the full
                    # program below owns all coalesced ranges.
                    facade_kit=dict(instructions[0], count=4),
                    facade_kit_walls=instructions,
                ),
            )
        )
    if meshes:
        # build() exports this diagnostic program separately before writing the layer GLB.
        meshes[0].extras["facade_program"] = program
        from turku3d.facade_programs import measured

        component_slots = slots(walls, measured(b.facade_key), getattr(b, "facade_passage", None))
        if component_slots:
            meshes[0].extras["facade_component_slots"] = component_slots
    return meshes
