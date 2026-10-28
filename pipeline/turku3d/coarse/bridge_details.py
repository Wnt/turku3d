"""OSM-driven bridge furniture and structural profiles, batched by surface material."""

from functools import lru_cache
from io import BytesIO

import numpy as np
import shapely
from PIL import Image

from turku3d.bridges.build import _box
from turku3d.structures.geometry import Faces, profile_wall

PATHS = {"footway", "cycleway", "path", "steps", "pedestrian", "bridleway"}

# Permanent crossing identities, not tile-specific meshes. Public photo sources and
# the distinction between surveyed decks and estimated details are in the README.
PROFILES = {
    "Q11853512": dict(body="steel", support="stone", rail_surface="paint"),
    "Q11898665": dict(body="concrete", support="stone", arch_edge=True),
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
        (0.28 if foot else 0.6) if structure in ("arch", "humpback") else (0.45 if foot else 0.85)
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
        rail_surface="steel",
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
        )
        faces = groups.setdefault(group, Faces())
        for tri in mesh.indices:
            faces.face(mesh.positions[tri], mesh.uvs[tri])
    return [
        appearance(f.mesh(f"{key}:{surface}:{i}", material), surface, collision=solid)
        for i, ((material, surface, solid), f) in enumerate(groups.items())
        if f.indices
    ]


def wall(key, line, bottom, top, thickness, surface, material="wall"):
    return appearance(
        profile_wall(key, line, bottom, top, thickness=thickness, material=material), surface
    )


def furniture(way, rail_lines, height_at, sample_ground, blocked, polygon):
    from .bridges import samples

    key = f"draft-bridge:{way['id']}"
    spec = style(way)
    meshes = []
    for part in shapely.get_parts(rail_lines):
        if part.geom_type not in ("LineString", "LinearRing") or part.length < 0.5:
            continue
        _, side = samples(part, 1.8)
        base = height_at(side)
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
                for lift, depth in ((0.25, 0.05), (0.65, 0.04), (spec["rail_height"], 0.065)):
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
                for p, y in zip(posts, yy, strict=True):
                    meshes.append(
                        appearance(
                            _box(key, (p[0], y + 0.68, p[1]), (half, 0.54, half), "railing"),
                            spec["rail_surface"],
                        )
                    )
    axis, width = way["line"], way["width"]
    a, b = np.asarray(axis.coords[0]), np.asarray(axis.coords[-1])
    direction = (b - a) / np.linalg.norm(b - a)
    normal = np.array([-direction[1], direction[0]])
    # End walls extend into the bank, never across a mapped lower way.
    if not way.get("landing"):
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
    if not way.get("landing") and spec["structure"] not in (
        "arch",
        "humpback",
        "suspension",
        "cable-stayed",
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
    }
    noise = rng.normal(0, 4 if surface != "steel" else 1, (128, 128))
    if surface == "stone":
        noise -= 25 * ((y % 32 < 2) | ((x + (y // 32 % 2) * 32) % 64 < 2))
    elif surface == "wood":
        noise += 4 * np.sin(x * np.pi / 2) - 24 * (x % 32 < 2)
    elif surface == "ballast":
        noise += np.repeat(np.repeat(rng.normal(0, 14, (32, 32)), 4, 0), 4, 1)
    pixels = np.clip(np.array(palette[surface])[None, None, :] + noise[:, :, None], 0, 255).astype(
        np.uint8
    )
    out = BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def surfaces(way, polygon, height_at):
    """Mapped pedestrian strips, lane paint and railway tracks on the same surveyed deck."""
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
        if spec["foot"] or spec["railway"]:
            continue
        lanes = max(1, min(8, int(number(tags.get("lanes"), 2))))
        road_width = min(member["width"] - 0.7, lanes * 3.25)
        for lane in range(1, lanes):
            offset = -road_width / 2 + road_width * lane / lanes
            stripe = axis.offset_curve(offset)
            if stripe.geom_type != "LineString":
                continue
            # Lane paint has no separate collision shell or curb.
            for station in np.arange(1, stripe.length - 2, 6):
                a = stripe.interpolate(float(station))
                b = stripe.interpolate(float(min(station + 3, stripe.length)))
                mark = (
                    shapely.LineString([a, b]).buffer(0.06, cap_style="flat").intersection(polygon)
                )
                parts = slab(
                    key + ":marking",
                    mark,
                    axis,
                    s,
                    hh,
                    "road",
                    lambda p: height_at(p) + 0.012,
                    0.005,
                )
                if parts:
                    meshes.append(appearance(parts[0], "paint", collision=False))
    return combine(meshes, key + ":surface")
