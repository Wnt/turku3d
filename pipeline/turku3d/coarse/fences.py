"""Cheap OSM barrier segments: GPU prototypes and matching solid collision boxes."""

from __future__ import annotations

import io
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageDraw

from turku3d import config as C
from turku3d.col.format import Primitive
from turku3d.col.materials import mat_id
from turku3d.layers import write_visual
from turku3d.package.gltf import GlbMesh, read_glb
from turku3d.structures.geometry import profile_wall

DEFAULTS = {
    "fence": 1.6,
    "wall": 1.8,
    "retaining_wall": 0.0,
    "city_wall": 1.8,
    "hedge": 1.4,
    "guard_rail": 0.8,
    "handrail": 1.0,
    "bollard": 0.9,
    "gate": 1.6,
}
# prototype -> collision material, thickness and linear RGB colour
STYLES = {
    "chain": ("fence", 0.08, (0.32, 0.36, 0.34, 1)),
    "wood": ("fence", 0.12, (0.32, 0.20, 0.10, 1)),
    "bars": ("fence", 0.10, (0.12, 0.15, 0.16, 1)),
    "stone": ("wall", 0.30, (0.36, 0.35, 0.32, 1)),
    "brick": ("wall", 0.28, (0.40, 0.18, 0.10, 1)),
    "hedge": ("vegetation_low", 0.65, (0.07, 0.19, 0.045, 1)),
    "rail": ("railing", 0.18, (0.43, 0.47, 0.48, 1)),
    "bollard": ("bollard", 0.22, (0.20, 0.22, 0.23, 1)),
}


def style(tags):
    kind = tags["barrier"]
    material = tags.get("material", "")
    if kind == "hedge":
        return "hedge"
    if kind == "bollard":
        return "bollard"
    if kind in ("guard_rail", "handrail"):
        return "rail"
    if kind in ("wall", "city_wall", "retaining_wall"):
        return "brick" if material == "brick" else "stone"
    if material == "wood" or tags.get("fence_type") in ("wood", "picket", "split_rail"):
        return "wood"
    return "bars" if tags.get("fence_type") in ("bars", "railing", "steel") else "chain"


def height(tags):
    try:
        value = float(str(tags.get("height", DEFAULTS[tags["barrier"]])).removesuffix(" m"))
        return min(max(value, 0.3), 12.0) if math.isfinite(value) else DEFAULTS[tags["barrier"]]
    except ValueError:
        return DEFAULTS[tags["barrier"]]


def prepare(tile, path):
    """Read barrier lines/points and area outlines, with road membership for deliberate road barriers."""
    from turku3d.spatial import rows as source_rows

    rows = []
    roads = []
    obstacles, bridge_outlines = [], []
    from .bridges import footprint

    window = shapely.box(*tile.bbox.as_tuple())
    for _, row in source_rows(path, tile.bbox):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        if (
            not tags.get("highway")
            and tags.get("barrier") not in DEFAULTS
            and not tags.get("building")
            and tags.get("man_made") != "bridge"
        ):
            continue
        geom = shapely.force_2d(row["geometry_local"])
        if not geom.intersects(window):
            continue
        poly = footprint(geom)
        if tags.get("building") not in (None, "no") and not poly.is_empty:
            obstacles.append(poly)
        if tags.get("man_made") == "bridge" and not poly.is_empty:
            bridge_outlines.append(poly)
        if tags.get("highway") and geom.geom_type in ("LineString", "MultiLineString"):
            roads.append(geom)
        if tags.get("barrier") in DEFAULTS:
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                geom = geom.boundary
            rows.append((geom, tags))
    road = shapely.union_all(roads)
    result = []
    blocked = shapely.union_all(obstacles).buffer(0.2)
    decks = shapely.union_all(bridge_outlines).buffer(0.5)
    seen = set()
    for geom, tags in rows:
        identity = (geom.normalize().wkb, json.dumps(tags, sort_keys=True))
        if identity in seen:
            continue
        seen.add(identity)
        geom = geom.difference(blocked)
        if tags.get("layer") not in (None, "0", "-1"):
            geom = geom.difference(decks)
        if geom.is_empty:
            continue
        on_road = bool(tags.get("highway"))
        if geom.geom_type == "Point":
            on_road |= not road.is_empty and road.distance(geom) < 0.15
        elif not road.is_empty:
            on_road |= geom.intersection(road).length > 0.5
        result.append((geom, tags, on_road))
    return result


def lines(geom):
    if geom.geom_type in ("LineString", "LinearRing"):
        yield geom
    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from lines(part)


def segments(line, roads, openings, window, *, on_road=False):
    """Clip at roads and gates, then split at corners, cell edges and at most 2.5 m apart."""
    clipped = line.intersection(window).difference(openings)
    if not on_road:
        # The half-width clearance keeps the solid box out of the street too.
        clipped = clipped.difference(roads.buffer(0.35))
    for part in lines(clipped):
        coords = np.asarray(part.coords)[:, :2]
        for p, q in zip(coords[:-1], coords[1:], strict=True):
            delta = q - p
            length = float(np.linalg.norm(delta))
            if length < 1e-6:
                continue
            cuts = set(np.linspace(0, 1, math.ceil(length / 2.5) + 1))
            for axis in (0, 1):
                if abs(delta[axis]) < 1e-9:
                    continue
                low, high = sorted((p[axis], q[axis]))
                for edge in (
                    np.arange(
                        math.floor(low / C.COLLISION_TILE_SIZE) + 1,
                        math.ceil(high / C.COLLISION_TILE_SIZE),
                    )
                    * C.COLLISION_TILE_SIZE
                ):
                    cuts.add(float((edge - p[axis]) / delta[axis]))
            cuts = sorted(cuts)
            for a, b in zip(cuts[:-1], cuts[1:], strict=True):
                if (b - a) * length > 1e-5:
                    yield p + a * delta, p + b * delta


@dataclass
class Segment:
    kind: str
    x: float
    y: float
    z: float
    length: float
    height: float
    yaw: float


def place(tile, records, streets, sample, water=(), tunnels=()):
    window = shapely.box(*tile.bbox.as_tuple()).difference(shapely.union_all(water).buffer(0.35))
    from .tunnels import dimensions

    # Tunnel mouths own their frames; OSM barriers must never add panels or
    # retaining blocks inside the opening, even when tagged as on-road.
    mouths = []
    for _, line, tags in tunnels:
        if tags.get("tunnel") == "building_passage":
            continue
        coords = np.asarray(line.coords)
        for end, neighbour in ((0, 1), (-1, -2)):
            point = coords[end]
            outward = point - coords[neighbour]
            outward /= np.linalg.norm(outward)
            approach = shapely.LineString([point - outward * 2, point + outward * 4])
            mouths.append(approach.buffer(dimensions(tags)[0] / 2 + 1.9, cap_style="flat"))
    window = window.difference(shapely.union_all(mouths))
    roads = shapely.union_all([g for g, _ in streets])
    openings = shapely.union_all(
        [
            g.buffer(1.5 if g.geom_type == "Point" else 0.15)
            for g, t, _ in records
            if t["barrier"] == "gate"
        ]
    )
    out = []
    for geom, tags, on_road in records:
        if tags["barrier"] == "gate":
            continue
        kind, h = style(tags), height(tags)
        # A retaining edge is the bank face below grade, not a free-standing fence.
        # Only an explicit height/parapet tag justifies a barrier above the bank.
        if tags["barrier"] == "retaining_wall" and "height" not in tags:
            if tags.get("wall") != "parapet":
                continue
            h = 0.5
        if geom.geom_type == "Point":
            if not window.covers(geom) or (not on_road and roads.covers(geom)):
                continue
            out.append(Segment(kind, geom.x, sample(geom.x, geom.y), geom.y, 0.22, h, 0))
            continue
        if kind == "bollard":
            for line in lines(geom.intersection(window)):
                for distance in np.arange(0, line.length + 0.01, 2.5):
                    point = line.interpolate(distance)
                    if not tile.bbox.contains(point.x, point.y):
                        continue
                    if openings.covers(point) or (not on_road and roads.covers(point)):
                        continue
                    out.append(
                        Segment(kind, point.x, sample(point.x, point.y), point.y, 0.22, h, 0)
                    )
            continue
        for p, q in segments(geom, roads, openings, window, on_road=on_road):
            mid = (p + q) / 2
            bottom = min(sample(*p), sample(*q), sample(*mid))
            top = max(sample(*p), sample(*q), sample(*mid)) + h
            out.append(
                Segment(
                    kind,
                    mid[0],
                    bottom,
                    mid[1],
                    float(np.linalg.norm(q - p)),
                    top - bottom,
                    -math.atan2(q[1] - p[1], q[0] - p[0]),
                )
            )
    return out


def collision(batch, ct):
    result = []
    for s in batch:
        if C.CollisionTile.at(s.x, s.z) != ct:
            continue
        material, thickness, _ = STYLES[s.kind]
        center = (s.x - ct.origin[0], s.y + s.height / 2, s.z - ct.origin[2])
        result.append(
            Primitive.box(
                mat_id(material),
                center,
                (s.length / 2, s.height / 2, thickness / 2),
                (0, math.sin(s.yaw / 2), 0, math.cos(s.yaw / 2)),
            )
        )
    return result


def prototypes(kind):
    """Reuse structures.profile_wall for hard-edged unit prototype components."""
    material, _, color = STYLES[kind]
    parts = []

    def box(x0, x1, y0, y1, width=1):
        parts.append(
            profile_wall(
                "prototype",
                [[x0, 0], [x1, 0]],
                [y0] * 2,
                [y1] * 2,
                thickness=width,
                material=material,
            )
        )

    if kind in ("stone", "brick", "hedge", "bollard"):
        box(-0.5, 0.5, 0, 1)
    else:
        box(-0.5, -0.46, 0, 1)
        box(0.46, 0.5, 0, 1)
        if kind == "wood":
            for x in np.linspace(-0.44, 0.35, 8):
                box(x, x + 0.09, 0.05, 0.98, 0.6)
        elif kind == "bars":
            for x in np.linspace(-0.4, 0.4, 9):
                box(x, x + 0.012, 0.05, 0.98, 0.5)
        if kind != "chain":
            for y in (0.55, 0.92) if kind == "rail" else (0.2, 0.8):
                box(-0.5, 0.5, y, y + 0.07, 0.7)
    pos, idx, uv, count = [], [], [], 0
    for mesh in parts:
        pos.extend(mesh.positions)
        idx.extend(mesh.indices + count)
        uv.extend(mesh.uvs)
        count += len(mesh.positions)
    meshes = [
        GlbMesh(
            kind,
            np.array(pos),
            np.array(idx),
            uvs=np.array(uv),
            color=color,
            extras=dict(layer="structures", object_id=kind, material_key=f"draft_{kind}"),
        )
    ]
    if kind == "chain":
        image = Image.new("RGBA", (64, 64))
        draw = ImageDraw.Draw(image)
        for offset in range(-64, 128, 16):
            draw.line((offset, 0, offset + 64, 64), fill=(145, 155, 150, 255), width=2)
            draw.line((offset, 0, offset - 64, 64), fill=(145, 155, 150, 255), width=2)
        data = io.BytesIO()
        image.save(data, format="PNG")
        meshes.append(
            GlbMesh(
                "chain_panel",
                np.array([[-0.5, 0, 0], [0.5, 0, 0], [0.5, 1, 0], [-0.5, 1, 0]]),
                np.array([[0, 1, 2], [0, 2, 3]]),
                uvs=np.array([[0, 0], [1, 0], [1, 1], [0, 1]]),
                image=data.getvalue(),
                color=(1, 1, 1, 1),
                extras=dict(
                    layer="structures", object_id="chain_panel", material_key="draft_chain_panel"
                ),
            )
        )
    return meshes


def write_instances(path, tile, batch):
    """Same EXT_mesh_gpu_instancing format as vegetation.visual; at most nine prototype draws."""
    meshes, batches = [], []
    for kind in sorted({s.kind for s in batch}):
        selected = [s for s in batch if s.kind == kind]
        for mesh in prototypes(kind):
            meshes.append(mesh)
            batches.append(selected)
    write_visual(path, meshes, root_translation=tile.origin)
    js, data = read_glb(Path(path).read_bytes())
    blob = bytearray(data)

    def append(values, typ):
        a = np.asarray(values, dtype="<f4")
        blob.extend(b"\0" * (-len(blob) % 4))
        js["bufferViews"].append(dict(buffer=0, byteOffset=len(blob), byteLength=a.nbytes))
        blob.extend(a.tobytes())
        js["accessors"].append(
            dict(bufferView=len(js["bufferViews"]) - 1, componentType=5126, count=len(a), type=typ)
        )
        return len(js["accessors"]) - 1

    for node, selected in zip(js["nodes"][1:], batches, strict=True):
        attrs = {
            "TRANSLATION": append(
                [[s.x - tile.origin[0], s.y, s.z - tile.origin[2]] for s in selected], "VEC3"
            ),
            "SCALE": append([[s.length, s.height, STYLES[s.kind][1]] for s in selected], "VEC3"),
            "ROTATION": append(
                [[0, math.sin(s.yaw / 2), 0, math.cos(s.yaw / 2)] for s in selected], "VEC4"
            ),
        }
        node["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": attrs}}
    for material in js["materials"]:
        if "baseColorTexture" in material.get("pbrMetallicRoughness", {}):
            material.update(alphaMode="MASK", alphaCutoff=0.5, doubleSided=True)
    js["extensionsUsed"] = js["extensionsRequired"] = ["EXT_mesh_gpu_instancing"]
    js["buffers"][0]["byteLength"] = len(blob)
    text = json.dumps(js, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 28 + len(text) + len(blob))
    glb += (
        struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(blob), 0x004E4942)
        + blob
    )
    Path(path).write_bytes(glb)


def produce(tile, inputs, stage, layer_files, sample):
    import time

    started = time.perf_counter()
    batch = place(tile, inputs.barriers, inputs.streets, sample, inputs.water, inputs.tunnels)
    if batch:
        path = stage / "fences.vis.glb"
        write_instances(path, tile, batch)
        layer_files.append(path)
    return {ct: collision(batch, ct) for ct in tile.collision_tiles()}, {
        "segments": len(batch),
        "seconds": round(time.perf_counter() - started, 3),
    }
