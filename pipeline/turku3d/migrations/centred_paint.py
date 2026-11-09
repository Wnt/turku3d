"""Refresh paint left behind by a road-only seam-centering install.

The normal generator records the planar graph used for its paint. Existing products
without that receipt are refreshed only when their graph contains moved seams.
Reuse the rule/explicit/rail producers and drape on the retained visible floor;
all non-paint meshes, materials, collision and parked-car products stay untouched.
"""
from __future__ import annotations

import json
import posixpath

import numpy as np

from turku3d import config as C
from turku3d.roads import paint

from .core import Migration, Product, glb_chunks, glb_pack


def translation(document, index):
    parents = {child: i for i, parent in enumerate(document["nodes"]) for child in parent.get("children", [])}
    offset = np.zeros(3)
    while index is not None:
        node = document["nodes"][index]
        if any(k in node for k in ("rotation", "scale", "matrix")):
            raise ValueError("rotated or scaled road surface is not supported")
        offset += node.get("translation", [0, 0, 0])
        index = parents.get(index)
    return offset


def partition_visible(visible, collision, materials):
    """Clip semantic boundaries while taking every output height from the visible plane.

    Nearest collision levels claim their overlapping footprint first. Thus a deck
    cannot classify the road below it, and a centroid never grants a whole triangle
    the road class across a differently tessellated sidewalk boundary.
    """
    import shapely
    import mapbox_earcut

    footprint = shapely.polygons(collision[:, :, [0, 2]])
    keep = shapely.area(footprint) > 1e-8
    collision, materials, footprint = collision[keep], materials[keep], footprint[keep]
    tree = shapely.STRtree(footprint)
    result, kinds = [], []
    for triangle in visible:
        shape = shapely.Polygon(triangle[:, [0, 2]])
        if shape.area < 1e-8:
            continue
        candidates = tree.query(shape, predicate="intersects")
        plane = np.linalg.solve(np.c_[triangle[:, [0, 2]], np.ones(3)], triangle[:, 1])
        parts = []
        for index in candidates:
            overlap = shape.intersection(footprint[index])
            if overlap.area < 1e-8:
                continue
            centre = overlap.centroid
            at = np.array([centre.x, centre.y, 1])
            other = collision[index]
            other_plane = np.linalg.solve(np.c_[other[:, [0, 2]], np.ones(3)], other[:, 1])
            parts.append((abs(at @ (plane - other_plane)), int(index), overlap))
        remaining = shape
        for _, index, overlap in sorted(parts, key=lambda item: item[:2]):
            clipped = remaining.intersection(overlap)
            remaining = remaining.difference(overlap)
            for part in shapely.get_parts(clipped):
                if part.geom_type != "Polygon" or part.area < 1e-8:
                    continue
                rings = [np.asarray(r.coords)[:-1] for r in [part.exterior, *part.interiors]]
                xz = np.concatenate(rings)
                indices = mapbox_earcut.triangulate_float64(xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)).reshape(-1, 3)
                xyz = np.c_[xz[:, 0], np.c_[xz, np.ones(len(xz))] @ plane, xz[:, 1]]
                for tri in xyz[indices]:
                    if np.cross(tri[1] - tri[0], tri[2] - tri[0])[1] < 0:
                        tri = tri[::-1]
                    result.append(tri)
                    kinds.append(materials[index])
    return np.asarray(result).reshape(-1, 3, 3), np.asarray(kinds, dtype=np.int32)


def visible_floor(product, polygons=None):
    from turku3d.coarse import surfaces
    from turku3d.col.format import read_col

    from .furniture import read_accessor

    entry = product.manifest["visual"][product.tile]
    document, binary = glb_chunks(product.read(entry["url"]))
    floor = []
    window = None
    if polygons is not None:
        import shapely
        window = shapely.union_all([item[0] if isinstance(item, tuple) else item for item in polygons])
    keys = {"draft_" + key for key in surfaces.KEYS}
    for i, node in enumerate(document["nodes"]):
        extras = node.get("extras", {})
        object_id = extras.get("object_id", "")
        bridge_top = (extras.get("layer") == "bridges" and
                      ((object_id.startswith("draft-bridge:") and object_id.endswith(":deck")) or
                       (object_id.startswith("bridge:") and object_id.endswith((":road", ":deck")))))
        ground = extras.get("layer") in ("ground", "structures") and extras.get("material_key") in keys
        if "mesh" not in node or not (ground or bridge_top):
            continue
        offset = translation(document, i)
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            positions = read_accessor(document, binary, product, entry["url"], primitive["attributes"]["POSITION"]) + offset
            indices = read_accessor(document, binary, product, entry["url"], primitive["indices"]).astype(np.int64).reshape(-1, 3)
            triangles = positions[indices]
            normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            triangles = triangles[normal[:, 1] > np.linalg.norm(normal, axis=1) * .5]
            if window is not None and len(triangles):
                triangles = triangles[shapely.intersects(shapely.polygons(triangles[:, :, [0, 2]]), window)]
            floor.extend(triangles)
    if not floor:
        raise ValueError(f"{product.tile}: no retained visible floor")
    collision, materials = [], []
    for entry in product.manifest["collision"].values():
        col = read_col(product.read(entry["url"]))
        collision.append(col.world_positions()[col.indices.astype(np.int64)])
        materials.append(col.materials)
    floor = np.asarray(floor)
    return partition_visible(floor, np.concatenate(collision), np.concatenate(materials))


def drape_visible(tile, triangles, materials, polygons):
    """Bridge paint only hits the visible deck; ground paint never projects onto that deck."""
    from turku3d.coarse import surfaces
    from turku3d.col.materials import mat_id

    import shapely

    meshes = []
    deck_triangles = triangles[materials == mat_id("bridge_deck")]
    deck_shape = shapely.union_all(shapely.polygons(deck_triangles[:, :, [0, 2]]))
    for deck in (False, True):
        items = []
        for item in polygons:
            flag = item[2] if isinstance(item, tuple) else None
            if deck:
                if flag is not False:
                    items.append(item)
            elif flag is True:
                # A bridge way also includes at-grade approaches. Paint those
                # beyond the built deck, but never the road underneath it.
                shape = item[0].difference(deck_shape)
                if not shape.is_empty:
                    items.append((shape, *item[1:]))
            else:
                items.append(item)
        keep = (materials == mat_id("bridge_deck")) if deck else (materials != mat_id("bridge_deck"))
        mesh = surfaces.paint_visual(tile, triangles.reshape(-1, 3), np.arange(triangles.size // 3).reshape(-1, 3),
                                     materials, keep, items)
        if mesh is not None:
            if deck:
                mesh.positions[:, 1] += surfaces.DECK_PAINT_LIFT - surfaces.GROUND_PAINT_LIFT
            meshes.append(mesh)
    if not meshes:
        return None
    combined = meshes[0]
    for mesh in meshes[1:]:
        combined.indices = np.concatenate([combined.indices, mesh.indices + len(combined.positions)])
        combined.positions = np.concatenate([combined.positions, mesh.positions])
        combined.colors = np.concatenate([combined.colors, mesh.colors])
    return combined


def refreshed_mesh(product: Product, graph: dict):
    from turku3d.coarse import surfaces
    from turku3d.coarse.sources import current_sources
    import hashlib

    tile = C.VisualTile.parse(product.tile)
    osm = current_sources().osm_source(tile)
    if osm is None:
        raise ValueError(f"{product.tile}: paint refresh requires its OSM source")
    explicit = surfaces.mapped_paint(tile, osm)
    rails = surfaces.tram_rails(tile, osm)
    rules = paint.markings(graph, explicit)
    triangles, materials = visible_floor(product, [*explicit, *rules, *rails])
    mesh = drape_visible(tile, triangles, materials, [*explicit, *rules, *rails])
    # apply --step pins current_sources to the product's base source version.
    # Record the actual source bytes and retained floor used for this correction.
    product.manifest["paint_refresh_sources"] = dict(
        osm_sha256=hashlib.sha256(osm.read_bytes()).hexdigest(),
        visual_sha256=product.manifest["visual"][product.tile]["sha256"],
        collision_sha256=[e["sha256"] for _, e in sorted(product.manifest["collision"].items())],
        floor_sha256=hashlib.sha256(triangles.tobytes() + materials.tobytes()).hexdigest())
    return mesh, paint.summary([*rules, *rails])


def transform(product: Product) -> None:
    manifest = product.manifest
    roads = manifest.get("roads")
    entry = manifest.get("visual", {}).get(product.tile)
    if not roads or not entry or not manifest.get("collision"):
        return
    graph = json.loads(product.read(roads["url"]))
    key = paint.graph_key(graph)
    if not graph.get("seamOrigins") or manifest.get("paint_road_graph") == key:
        return
    from scipy.spatial import cKDTree
    from turku3d.package.gltf import vertex_normals

    from .furniture import read_accessor
    document, binary = glb_chunks(product.read(entry["url"]))
    nodes = [n for n in document["nodes"] if n.get("extras", {}).get("object_id") == f"paint_{product.tile}"]
    if len(nodes) != 1:
        raise ValueError(f"{product.tile}: expected one unbatched road-paint node")
    node = nodes[0]
    offset = translation(document, document["nodes"].index(node))
    primitives = document["meshes"][node["mesh"]]["primitives"]
    if len(primitives) != 1:
        raise ValueError("expected one paint primitive")
    primitive = primitives[0]
    if set(primitive["attributes"]) - {"POSITION", "NORMAL", "COLOR_0", "COLOR_1"}:
        raise ValueError("unsupported paint vertex attributes")
    mesh, counts = refreshed_mesh(product, graph)
    if mesh is None:
        raise ValueError(f"{product.tile}: refusing to erase existing paint")
    old_count = document["accessors"][primitive["indices"]]["count"] // 3
    positions = mesh.positions + np.asarray(C.VisualTile.parse(product.tile).origin) - offset
    values = {"POSITION": np.asarray(positions, np.float32),
              "NORMAL": vertex_normals(mesh.positions, mesh.indices),
              "COLOR_0": np.asarray(mesh.colors, np.float32)}
    # Transfer the existing baked-visibility field to regenerated paint vertices.
    # Paint has no textures or UV-dependent material.
    if "COLOR_1" in primitive["attributes"]:
        old = read_accessor(document, binary, product, entry["url"], primitive["attributes"]["POSITION"])
        acc_id = primitive["attributes"]["COLOR_1"]
        visibility = read_accessor(document, binary, product, entry["url"], acc_id)
        acc = document["accessors"][acc_id]
        if acc.get("normalized") and visibility.dtype.kind == "u":
            visibility = visibility.astype(np.float32) / np.iinfo(visibility.dtype).max
        values["COLOR_1"] = visibility[cKDTree(old).query(values["POSITION"])[1]]
    # Append one immutable payload; existing buffer views and all other meshes
    # retain their exact indices and bytes, including meshopt-compressed data.
    target = len(document["buffers"])
    payload = bytearray()

    def accessor(array, kind, component=5126):
        array = np.asarray(array, dtype="<f4" if component == 5126 else "<u4")
        payload.extend(b"\0" * (-len(payload) % 4))
        view = len(document["bufferViews"])
        document["bufferViews"].append(dict(buffer=target, byteOffset=len(payload), byteLength=array.nbytes))
        payload.extend(array.tobytes())
        record = dict(bufferView=view, componentType=component, count=len(array), type=kind)
        if kind == "VEC3":
            record.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        index = len(document["accessors"])
        document["accessors"].append(record)
        return index

    primitive["attributes"] = {name: accessor(value, f"VEC{value.shape[1]}") for name, value in values.items()}
    primitive["indices"] = accessor(mesh.indices.ravel(), "SCALAR", 5125)
    url = product.put("../shared/{hash}.bin", bytes(payload))
    document["buffers"].append(dict(byteLength=len(payload), uri=posixpath.relpath(url, posixpath.dirname(entry["url"]) or ".")))
    data = glb_pack(document, binary)
    product.replace(entry, data)
    entry["tris"] = entry.get("tris", old_count) - old_count + len(mesh.indices)
    entry.pop("brotli_bytes", None)  # Delivery now includes the separate immutable paint buffer.
    manifest.update(paint_road_graph=key, marking_triangles=len(mesh.indices), rule_paint=counts)


MIGRATION = Migration("centred-paint", 1, transform)
