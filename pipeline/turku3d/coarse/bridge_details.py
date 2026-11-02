"""OSM-driven bridge furniture and structural profiles, batched by surface material."""

from functools import lru_cache
from io import BytesIO

import numpy as np
import shapely
from PIL import Image

from turku3d.bridges.build import _box, _boxes
from turku3d.structures.geometry import Faces, profile_wall

PATHS = {"footway", "cycleway", "path", "steps", "pedestrian", "bridleway"}

# Permanent crossing identities, not tile-specific meshes. Public photo sources and
# the distinction between surveyed decks and estimated details are in the README.
PROFILES = {
    "Q11853512": dict(body="steel", support="stone", rail_surface="paint",
                      entrance_height=4.8, entrance_width=1.05, entrance_inset=1.2,
                      rail_pattern="heritage"),
    "Q11898665": dict(body="concrete", support="stone", arch_edge=True),
    "Q5408563": dict(structure="cable-stayed", body="steel", support="stone",
                     thickness=.32, tower_height=5.6, tower_radius=.17),
}


def style(way):
    tags = way["tags"]
    foot = tags.get("highway") in PATHS
    rail = bool(tags.get("railway"))
    structure = tags.get("bridge:structure", "beam")
    inferred = (
        tags.get("surface")
        if foot and tags.get("surface") in ("wood", "metal", "steel", "metal_grid")
        else "concrete"
    )
    material = tags.get("bridge:material", tags.get("material", inferred))
    body = (
        "stone"
        if material in ("stone", "masonry", "granite")
        else "steel"
        if material in ("steel", "metal", "metal_grid")
        else "wood"
        if material == "wood"
        else "concrete"
    )
    thickness = (
        (0.28 if foot else 0.6)
        if structure in ("arch", "humpback")
        else ((0.32 if way.get("stair_access") else 0.45) if foot else 0.85)
    )
    if rail:
        thickness = 1.1
    if way.get("landing"):
        thickness = 0.12 if body == "steel" else 0.18
    try:
        thickness = float(tags.get("bridge:thickness", thickness))
    except ValueError:
        pass
    railing = tags.get("railing", tags.get("barrier", "railing"))
    if tags.get("bridge:parapet") in ("stone", "concrete"):
        railing = tags["bridge:parapet"]
    surface = tags.get("surface", "asphalt" if not foot else "concrete")
    surface = {
        "paving_stones": "stone",
        "sett": "stone",
        "paved": "stone",
        "wood": "wood",
        "metal": "steel",
        "metal_grid": "steel",
        "concrete": "concrete",
        "asphalt": "asphalt",
    }.get(surface, "asphalt")
    result = dict(
        structure=structure,
        body=body,
        surface="ballast" if rail else surface,
        thickness=float(np.clip(thickness, 0.15, 2.5)),
        railing=railing,
        rail_height=1.6 if rail else 1.2,
        foot=foot,
        railway=rail,
        support=body,
        rail_surface="paint" if way.get("stair_access") and body == "concrete" else "steel",
        arch_edge=False,
    )
    result.update(PROFILES.get(tags.get("wikidata"), {}))
    return result


def appearance(mesh, key, *, collision=True):
    mesh.metadata.update(surface=key, collision=collision)
    return mesh


def combine(meshes, key):
    """Retain component semantics while merging bars/posts into a handful of draws."""
    groups = {}
    for mesh in meshes:
        group = (
            mesh.material,
            mesh.metadata.get("surface", "concrete"),
            mesh.metadata.get("collision", True),
            mesh.visual,
        )
        faces = groups.setdefault(group, Faces())
        faces.triangles(mesh.positions, mesh.indices, mesh.uvs)
    return [
        appearance(
            f.mesh(f"{key}:{surface}:{i}", material, visual=visible), surface, collision=solid
        )
        for i, ((material, surface, solid, visible), f) in enumerate(groups.items())
        if f.indices
    ]


def wall(key, line, bottom, top, thickness, surface, material="wall"):
    return appearance(
        profile_wall(key, line, bottom, top, thickness=thickness, material=material), surface
    )


def tube(key, a, b, radius, surface="paint", material="railing", sides=6):
    """Low-sided round stock for handrails, balusters and concrete columns."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    direction = b - a
    length = np.linalg.norm(direction)
    direction /= length
    normal = np.cross(direction, [0, 1, 0] if abs(direction[1]) < 0.9 else [1, 0, 0])
    normal /= np.linalg.norm(normal)
    other = np.cross(direction, normal)
    angle = np.arange(sides) * (2 * np.pi / sides)
    ring = radius * (np.cos(angle)[:, None] * normal + np.sin(angle)[:, None] * other)
    bottom, top = a + ring, b + ring
    faces = Faces()
    for i in range(sides):
        j = (i + 1) % sides
        faces.face([bottom[i], bottom[j], top[j], top[i]])
    faces.face(bottom[::-1])
    faces.face(top)
    return appearance(faces.mesh(key, material), surface)


def handrail(key, line, heights, height=1.1, surface="paint"):
    """Tubular top/bottom rails and close vertical infill on a measured profile."""
    from .bridges import samples

    stations, posts = samples(line, 0.16)
    yy = np.interp(stations, heights[0], heights[1])
    meshes = []
    rail_s = np.unique(np.r_[heights[0], 0, line.length])
    rail_p = shapely.get_coordinates(shapely.line_interpolate_point(line, rail_s))
    rail_y = np.interp(rail_s, heights[0], heights[1])
    for lift, radius in ((0.12, 0.018), (height, 0.028)):
        for a, b, ya, yb in zip(rail_p[:-1], rail_p[1:], rail_y[:-1], rail_y[1:], strict=True):
            meshes.append(
                tube(key, [a[0], ya + lift, a[1]], [b[0], yb + lift, b[1]], radius, surface)
            )
    for i, (point, y) in enumerate(zip(posts, yy, strict=True)):
        meshes.append(
            tube(
                key,
                [point[0], y + 0.1, point[1]],
                [point[0], y + height, point[1]],
                0.024 if i % 10 == 0 else 0.012,
                surface,
            )
        )
    return meshes


def roadside_supports(way, height_at, sample_ground, polygon, obstacles):
    """Slim columns immediately outside the crossed carriageways, never in a lane."""
    from .bridges import PATHS

    roads = [w for w in way.get("under", []) if w["tags"].get("highway") not in PATHS]
    if not roads:
        return []
    axis = way["line"]
    road = shapely.union_all([w["line"].buffer(w["width"] / 2) for w in roads])
    crossing = axis.intersection(road.buffer(0.65))
    meshes = []
    stations = sorted(
        {
            axis.project(shapely.Point(p))
            for part in shapely.get_parts(crossing)
            if part.geom_type == "LineString"
            for p in (part.coords[0], part.coords[-1])
        }
    )
    if not stations:
        return []
    for station in (stations[0], stations[-1]):
        p = np.asarray(axis.interpolate(float(station)).coords[0])
        footprint = shapely.Point(p).buffer(0.28)
        if (
            road.intersects(footprint)
            or obstacles.intersects(footprint)
            or not polygon.covers(footprint)
        ):
            continue
        top = float(height_at(np.array([p]))[0]) - style(way)["thickness"]
        bottom = float(sample_ground(np.array([p]))[0]) - 0.25
        if top - bottom > 1:
            meshes.append(
                tube(
                    f"draft-bridge:{way['id']}:column",
                    [p[0], bottom, p[1]],
                    [p[0], top, p[1]],
                    0.26,
                    "concrete",
                    "wall",
                    sides=8,
                )
            )
    return meshes


# Survey/OSM outlines can split one crossing into adjacent road and path decks.
# Two metres bridges mapping slivers, while retaining rails across a real gap.
RAILING_GAP = 2.0


def deck_outline(way):
    return way.get(
        "outline", way["line"].buffer(way["width"] / 2, cap_style="flat", join_style="mitre")
    )


def railing_lines(ways):
    """Outer longitudinal edges only; never fence abutments between lane openings."""
    from .bridges import parallel

    outlines = [deck_outline(w) for w in ways]
    tree = shapely.STRtree(outlines)
    result = []
    for i, (way, outline) in enumerate(zip(ways, outlines, strict=True)):
        members = way.get("member_ways", [way])
        openings = shapely.union_all(
            [
                shapely.Point(p).buffer(w["width"] / 2 + 0.5)
                for w in members
                for p in (w["line"].coords[0], w["line"].coords[-1])
            ]
        )
        edges = outline.boundary.difference(openings)
        # Separate source outlines may describe the same crossing. Do not change
        # their surveyed slabs/heights; only suppress furniture on adjoining edges.
        neighbours = [
            outlines[j]
            for j in tree.query(outline, predicate="dwithin", distance=RAILING_GAP)
            if j != i and parallel(way, ways[j])
        ]
        # Also suppress facing edges of disjoint pieces inside one mapped outline.
        parts = list(shapely.get_parts(outline))
        neighbour_mask = shapely.union_all(neighbours).buffer(RAILING_GAP + 1e-6)
        part_masks = [p.buffer(RAILING_GAP + 1e-6) for p in parts]
        segments = []
        for part in shapely.get_parts(edges):
            if part.geom_type not in ("LineString", "LinearRing"):
                continue
            coords = np.asarray(part.coords)
            for a, b in zip(coords[:-1], coords[1:], strict=True):
                segment = shapely.LineString([a, b])
                point = segment.interpolate(0.5, normalized=True)
                axis = min(members, key=lambda w: w["line"].distance(point))["line"]
                t = axis.project(point)
                tangent = np.asarray(
                    axis.interpolate(min(axis.length, t + 0.5)).coords[0]
                ) - np.asarray(axis.interpolate(max(0, t - 0.5)).coords[0])
                # End caps can survive circular lane openings as long cross-road
                # barriers. Side rails run along the nearest road/path, not across it.
                if abs((b - a) @ tangent) < 0.5 * np.linalg.norm(b - a) * np.linalg.norm(tangent):
                    continue
                segment = segment.difference(neighbour_mask)
                for poly, mask in zip(parts, part_masks, strict=True):
                    if poly.distance(point) > 1e-6:
                        segment = segment.difference(mask)
                segments.extend(p for p in shapely.get_parts(segment) if not p.is_empty)
        result.append(shapely.line_merge(shapely.union_all(segments)))
    return result


def furniture(way, rail_lines, height_at, sample_ground, blocked, polygon, *, obstacles=None):
    from .bridges import samples

    key = f"draft-bridge:{way['id']}"
    spec = style(way)
    meshes = []
    road_footbridge = spec["foot"] and any(
        w["tags"].get("highway") not in PATHS for w in way.get("under", [])
    )
    # Join source vertices before rejecting fragments shorter than one post bay.
    for part in shapely.get_parts(shapely.line_merge(rail_lines)):
        if part.geom_type not in ("LineString", "LinearRing") or part.length < 1.8:
            continue
        _, side = samples(part, 1.8)
        base = height_at(side)
        if spec["foot"] and spec["railing"] not in ("stone", "concrete", "wall", "parapet"):
            distances = np.array([part.project(shapely.Point(p)) for p in side])
            meshes.extend(
                handrail(key, part, (distances, base), spec["rail_height"], spec["rail_surface"])
            )
            continue
        # A raised edge beam has visible depth and anchors the posts.
        for a, b, y0, y1 in zip(side[:-1], side[1:], base[:-1], base[1:], strict=True):
            line, hh = np.array([a, b]), np.array([y0, y1])
            meshes.append(wall(key, line, hh - spec["thickness"], hh + 0.14, 0.32, spec["body"]))
            if spec["railing"] in ("stone", "concrete", "wall", "parapet"):
                surface = "stone" if spec["railing"] == "stone" else "concrete"
                meshes.append(
                    wall(key, line, hh + 0.14, hh + spec["rail_height"], 0.3, surface, "railing")
                )
                meshes.append(
                    wall(
                        key,
                        line,
                        hh + spec["rail_height"],
                        hh + spec["rail_height"] + 0.08,
                        0.42,
                        surface,
                        "railing",
                    )
                )
            else:
                bands = ((0.25, 0.05), (0.65, 0.04), (spec["rail_height"], 0.065))
                if spec.get("rail_pattern") == "heritage":
                    bands = ((0.16, .035), (.42, .025), (.85, .025),
                             (1.02, .025), (1.12, .025), (spec["rail_height"], .055))
                for lift, depth in bands:
                    meshes.append(
                        wall(
                            key,
                            line,
                            hh + lift,
                            hh + lift + depth,
                            depth,
                            spec["rail_surface"],
                            "railing",
                        )
                    )
        if spec["railing"] not in ("stone", "concrete", "wall", "parapet"):
            # Baluster gaps are below capsule radius: the visible bars also block cars/players.
            for spacing, half in ((1.8, 0.045), (0.18, 0.014)):
                _, posts = samples(part, spacing)
                yy = height_at(posts)
                meshes.append(
                    appearance(
                        _boxes(
                            key,
                            np.c_[posts[:, 0], yy + 0.68, posts[:, 1]],
                            (half, 0.54, half),
                            "railing",
                        ),
                        spec["rail_surface"],
                    )
                )
    axis, width = way["line"], way["width"]
    a, b = np.asarray(axis.coords[0]), np.asarray(axis.coords[-1])
    direction = (b - a) / np.linalg.norm(b - a)
    normal = np.array([-direction[1], direction[0]])
    # End walls extend into the bank, never across a mapped lower way.
    if road_footbridge:
        meshes.extend(
            roadside_supports(
                way,
                height_at,
                sample_ground,
                polygon,
                way.get("obstacles", shapely.GeometryCollection()),
            )
        )
    if not way.get("landing") and not road_footbridge:
        for endpoint, sign in ((a, -1), (b, 1)):
            centre = endpoint + direction * sign * 0.8
            line = np.array(
                [centre - normal * (width / 2 - 0.3), centre + normal * (width / 2 - 0.3)]
            )
            if any(shapely.LineString(line).intersects(g) for g in blocked):
                continue
            top = height_at(line) - spec["thickness"]
            bottom = np.minimum(sample_ground(line) - 0.6, top - 1.0)
            meshes.append(wall(key, line, bottom, top, 1.6, spec["support"]))
    # Type-based supports are explicitly fallback dimensions, not claimed lidar measurements.
    stations = []
    if (
        not way.get("landing")
        and not road_footbridge
        and spec["structure"]
        not in (
            "arch",
            "humpback",
            "suspension",
            "cable-stayed",
        )
    ):
        spans = max(1, int(np.ceil(axis.length / (35 if spec["foot"] else 40))))
        stations = np.linspace(0, axis.length, spans + 1)[1:-1].tolist()
        measured = way.get("survey", {}).get("pier_stations", [])
        if len(measured) >= 2 or (len(measured) == 1 and len(stations) <= 1):
            stations = measured
        elif measured and stations:
            for measured_station in measured:
                nearest = int(np.argmin(np.abs(np.asarray(stations) - measured_station)))
                if abs(stations[nearest] - measured_station) < axis.length * 0.15:
                    stations[nearest] = measured_station
    for station in stations:
        p = np.asarray(axis.interpolate(float(station)).coords[0])
        line = np.array([p - normal * width * 0.32, p + normal * width * 0.32])
        support = shapely.LineString(line).buffer(0.8)
        if any(support.intersects(g) for g in blocked) or not polygon.buffer(0.2).covers(support):
            continue
        top = height_at(line) - spec["thickness"]
        bottom = sample_ground(line) - 0.4
        evidence = way.get("survey", {})
        for measured_station, measured_base in zip(
            evidence.get("pier_stations", []), evidence.get("pier_bases", []), strict=True
        ):
            if abs(station - measured_station) < 3:
                bottom = np.minimum(bottom, measured_base)
        if np.min(top - bottom) < 1:
            continue
        meshes.append(wall(key, line, bottom, top, 1.2 if spec["foot"] else 1.8, spec["support"]))
        meshes.append(
            wall(key, line, top - 0.45, top, 1.8 if spec["foot"] else 2.4, spec["support"])
        )
    if spec["structure"] in ("arch", "humpback") and not way.get("landing"):
        # A shallow arch soffit follows the surveyed crown without raising the driving surface.
        _, centres = samples(axis, 2)
        s = np.array([axis.project(shapely.Point(p)) for p in centres])
        # Widened concrete arches have exposed fascia at the deck edges, not
        # hidden several metres inside the carriageways. Keep other arch types.
        rib_offset = width / 2 - 0.5 if spec["arch_edge"] else width * 0.36
        for offset in (-rib_offset, rib_offset):
            line = centres + normal * offset
            top = height_at(line) - spec["thickness"]
            depth = 0.2 + min(2.5, axis.length * 0.035) * (2 * s / axis.length - 1) ** 2
            if spec["arch_edge"]:
                depth = 0.25 + min(3.0, axis.length * 0.065) * (2 * s / axis.length - 1) ** 2
                # The widened concrete fascia is an open-spandrel arch: a rib
                # below the deck with vertical posts, rather than a filled web.
                rib_top = top - depth
                meshes.append(wall(key, line, rib_top - 0.5, rib_top, 0.65, spec["body"]))
                for station in np.arange(3, axis.length - 2, 4.5):
                    p = np.asarray(axis.interpolate(float(station)).coords[0]) + normal * offset
                    post = np.asarray([p - direction * 0.15, p + direction * 0.15])
                    yy = height_at(post) - spec["thickness"]
                    drop = (
                        0.25 + min(3.0, axis.length * 0.065) * (2 * station / axis.length - 1) ** 2
                    )
                    meshes.append(wall(key, post, yy - drop, yy, 0.4, spec["body"]))
            else:
                meshes.append(
                    wall(key, line, top - depth, top, min(1.2, width * 0.15), spec["body"])
                )
    if spec["body"] == "steel" and spec["structure"] == "beam" and not way.get("landing"):
        # Exposed transverse floor beams and edge stiffeners under steel decks.
        # These remain below the surveyed road, with no extra road collision skin.
        for station in np.arange(1.0, axis.length - 0.5, 2.0):
            p = np.asarray(axis.interpolate(float(station)).coords[0])
            line = np.array([p - normal * (width / 2 - 0.2), p + normal * (width / 2 - 0.2)])
            top = height_at(line) - spec["thickness"]
            meshes.append(wall(key, line, top - 0.25, top, 0.14, "steel"))
            for edge in line:
                y = float(height_at(np.array([edge]))[0])
                meshes.append(
                    appearance(
                        _box(
                            key,
                            (edge[0], y - spec["thickness"] / 2, edge[1]),
                            (0.06, spec["thickness"] / 2, 0.06),
                            "wall",
                        ),
                        "steel",
                    )
                )
    from .bridge_kit import entrances, stayed_span

    meshes.extend(stayed_span(way, height_at, sample_ground, blocked, polygon, spec))
    # Lower quay passages constrain supports below the deck, not pylons above it.
    # Only same-level building obstacles can conflict with an entrance ornament.
    entrance_obstacles = obstacles if obstacles is not None else way.get("obstacles")
    meshes.extend(entrances(way, height_at,
                           [] if entrance_obstacles is None else [entrance_obstacles], spec))
    for point, tags in way.get("lamps", []):
        p = np.asarray(point)
        y = float(height_at(np.array([p]))[0])
        try:
            h = float(tags.get("height", 7 if not spec["foot"] else 4))
        except ValueError:
            h = 7
        h = float(np.clip(h, 2, 14))
        meshes.append(
            appearance(_box(key, (p[0], y + h / 2, p[1]), (0.07, h / 2, 0.07), "wall"), "steel")
        )
        try:
            heads = int(tags.get("light:count", 1))
        except ValueError:
            heads = 1
        heads = int(np.clip(heads, 1, 8))
        angled = tags.get("lamp_mount") == "angled_mast"
        for angle in np.arange(heads) * (2 * np.pi / heads):
            offset = np.array([np.cos(angle), np.sin(angle)]) * (0.55 if angled else 0.25)
            tip = p + offset
            meshes.append(
                wall(
                    key,
                    np.array([p, tip]),
                    np.array([y + h - 0.35, y + h]) - 0.025,
                    np.array([y + h - 0.35, y + h]) + 0.025,
                    0.05,
                    "steel",
                )
            )
            meshes.append(
                appearance(_box(key, (tip[0], y + h, tip[1]), (0.18, 0.09, 0.12), "wall"), "steel")
            )
            meshes.append(
                appearance(
                    _box(key, (tip[0], y + h - 0.091, tip[1]), (0.15, 0.008, 0.1), "wall"),
                    "paint",
                    collision=False,
                )
            )
    return combine(meshes, key + ":furniture")


@lru_cache(maxsize=16)
def texture(surface):
    """Small seamless procedural albedos, independent of all reference imagery."""
    rng = np.random.default_rng(23)
    y, x = np.mgrid[:128, :128]
    palette = {
        "asphalt": (65, 67, 69),
        "concrete": (158, 156, 148),
        "stone": (144, 140, 132),
        "steel": (72, 80, 83),
        "wood": (110, 91, 70),
        "ballast": (107, 103, 95),
        "paint": (224, 222, 206),
        "rock": (127, 128, 122),
        "granite": (158, 153, 137),
        "bronze": (64, 91, 80),
    }
    noise = rng.normal(0, 4 if surface != "steel" else 1, (128, 128))
    if surface == "granite":
        noise += rng.normal(0, 8, (128, 128))
        noise += 3 * np.cos(x * np.pi / 32) * np.cos(y * np.pi / 32)
    elif surface == "stone":
        noise -= 25 * ((y % 32 < 2) | ((x + (y // 32 % 2) * 32) % 64 < 2))
    elif surface == "wood":
        noise += 4 * np.sin(x * np.pi / 2) - 24 * (x % 32 < 2)
    elif surface == "ballast":
        noise += np.repeat(np.repeat(rng.normal(0, 14, (32, 32)), 4, 0), 4, 1)
    elif surface == "rock":
        noise += 9 * np.sin(x * np.pi / 32 + 2 * np.sin(y * np.pi / 64))
        noise -= 28 * (np.abs(np.sin((x + y) * np.pi / 64)) < 0.06)
    pixels = np.clip(np.array(palette[surface])[None, None, :] + noise[:, :, None], 0, 255).astype(
        np.uint8
    )
    out = BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def surfaces(way, polygon, height_at):
    """Mapped pedestrian strips and railway tracks on the same surveyed deck."""
    from .bridges import number, samples, slab

    meshes = []
    key = f"draft-bridge:{way['id']}"
    members = way.get("member_ways", [way])
    main_spec = style(way)
    for member in members:
        axis, tags = member["line"], member["tags"]
        s, coords = samples(axis, 2)
        hh = height_at(coords)
        spec = style(member)
        if spec["foot"] and not main_spec["foot"]:
            strip = axis.buffer(
                member["width"] / 2, cap_style="flat", join_style="mitre"
            ).intersection(polygon.buffer(-0.2))
            parts = slab(
                key + ":pavement",
                strip,
                axis,
                s,
                hh + 0.14,
                "sidewalk",
                lambda p: height_at(p) + 0.14,
                0.14,
            )
            for mesh in parts:
                meshes.append(
                    appearance(mesh, spec["surface"] if mesh.material == "sidewalk" else "stone")
                )
        if spec["railway"]:
            # Gauge is millimetres in OSM; track membership keeps parallel rails distinct.
            gauge = number(tags.get("gauge"), 1524) / 1000
            for offset in (-gauge / 2, gauge / 2):
                shifted = axis.offset_curve(offset)
                if shifted.geom_type != "LineString":
                    continue
                _, points = samples(shifted, 2)
                ys = height_at(points)
                meshes.append(wall(key, points, ys + 0.12, ys + 0.27, 0.07, "steel", "gravel"))
            for station in np.arange(0.5, axis.length - 0.5, 0.65):
                a = np.asarray(axis.interpolate(float(station - 0.2)).coords[0])
                b = np.asarray(axis.interpolate(float(station + 0.2)).coords[0])
                n = np.array([-(b - a)[1], (b - a)[0]]) / np.linalg.norm(b - a)
                p = (a + b) / 2
                line = np.array([p - n * 1.25, p + n * 1.25])
                ys = height_at(line)
                meshes.append(wall(key, line, ys + 0.02, ys + 0.12, 0.22, "concrete", "gravel"))
        # Lane paint on road decks comes from the road graph (turku3d.roads.paint), draped in the
        # tile's one road_paint mesh with the Finnish centre-line colour.
    return combine(meshes, key + ":surface")
