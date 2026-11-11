"""Compile a bounded semantic pilot from frozen prepared source evidence.

Prepared pickle inputs are trusted local pipeline products, never network payloads.
The adapter ends here: the runtime reads only the current semantic scene contract.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
import triangle

from turku3d import config as C
from .schema import SCHEMA, validate_scene

PALETTE = {
    "ground": ([.23, .22, .19], .95, 0), "road": ([.12, .13, .14], .94, 0),
    "footway": ([.39, .37, .33], .85, 0), "water": ([.025, .08, .09], .2, .1),
    "wall": ([.58, .43, .28], .9, 0), "roof": ([.15, .11, .09], .8, .1),
    "barrier": ([.9, .48, .025], .7, 0),
    "iron": ([.025, .03, .035], .6, .7), "glass": ([.75, .58, .33], .2, .05),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def mesh_record(key, entity, material, positions, indices, policy="solid", support=None, uv=None):
    p, ix = np.asarray(positions, float), np.asarray(indices, int).reshape(-1, 3)
    area = np.linalg.norm(np.cross(p[ix[:, 1]] - p[ix[:, 0]], p[ix[:, 2]] - p[ix[:, 0]]), axis=1)
    ix = ix[area >= 2e-6]
    # Flat prepared normals preserve masonry/roof corners without runtime inference.
    p = p[ix].reshape(-1, 3)
    n = np.cross(p[1::3] - p[::3], p[2::3] - p[::3])
    n /= np.linalg.norm(n, axis=1)[:, None]
    uv = np.asarray(uv)[ix].reshape(-1, 2) if uv is not None else p[:, [0, 2]]
    return dict(id=key, entityId=entity, materialId=material, positions=p.ravel().tolist(),
                indices=list(range(len(p))), collision=policy, supportId=support,
                normals=np.repeat(n, 3, axis=0).ravel().tolist(), uv=uv.ravel().tolist())


def load_evidence(cache, bounds):
    points, buildings, streets, waters, sources, props = [], {}, {}, {}, [], {}
    xmin, zmin, xmax, zmax = bounds
    for i in range(int(xmin // 250), int(xmax // 250) + 1):
        for j in range(int(zmin // 250), int(zmax // 250) + 1):
            root = cache / f"t0_{i}_{j}"
            for dataset in ("ground", "wfs", "poles"):
                paths = sorted(root.glob(dataset + ".*.pkl"))
                if not paths:
                    if dataset == "poles":
                        continue
                    raise ValueError(f"Missing source dataset {root}/{dataset}")
                # Multiple revisions require an explicit frozen input selection.
                if len(paths) != 1:
                    raise ValueError(f"Ambiguous {dataset} revisions in {root}")
                data = paths[0].read_bytes()
                value = pickle.loads(data)["value"]
                sources.append(dict(id=f"t0_{i}_{j}/{paths[0].name}", sha256=digest(data),
                                    kind=dataset, uncertaintyM=.15 if dataset == "ground" else .3))
                if dataset == "ground":
                    points.append(value["points"])
                elif dataset == "wfs":
                    buildings.update((str(b.key), b) for b in value["buildings"])
                    streets.update((g.wkb, (g, m)) for g, m in value["streets"])
                    waters.update((g.wkb, g) for g in value["water"])
                else:
                    props.update((p.object_id, p) for p in value)
    return np.unique(np.concatenate(points), axis=0), list(buildings.values()), list(streets.values()), list(waters.values()), sources, list(props.values())


def ground_scene(scene, points, buildings, streets, waters):
    window = shapely.box(*scene["bounds"])
    water = shapely.union_all(waters).intersection(window)
    building_area = shapely.union_all([b.footprint for b in buildings])
    dry = window.difference(water).difference(building_area)
    regions = [(g.intersection(dry), "footway" if m in (2, 7, 8, 9) else "road")
               for g, m in streets if g.intersects(dry)]
    network = shapely.union_all([dry.boundary, *[g.boundary for g, _ in regions]])
    patches = [g for g in shapely.get_parts(shapely.polygonize(shapely.get_parts(network)))
               if dry.covers(g.representative_point()) and g.area > .005]
    linear = LinearNDInterpolator(points[:, [0, 2]], points[:, 1])
    nearest = NearestNDInterpolator(points[:, [0, 2]], points[:, 1])
    height_cache, boundary_cache = {}, {}
    def height(xz):
        result = []
        for x, z in np.round(xz, 7):
            key = (float(x), float(z))
            if key not in height_cache:
                y = float(linear(x, z))
                height_cache[key] = float(nearest(x, z)) if not np.isfinite(y) else y
            result.append(height_cache[key])
        return np.asarray(result)
    for poly in sorted(patches, key=lambda g: g.wkb):
        poly = shapely.segmentize(poly, 1.)
        key = "surface:" + digest(poly.wkb)[:16]
        kind = "ground"
        for region, candidate in regions:
            if region.covers(poly.representative_point()):
                kind = candidate
        coords, segments, holes, ids = [], [], [], []
        for ring_no, ring in enumerate([poly.exterior, *poly.interiors]):
            r = np.asarray(ring.coords)[:-1]
            start = len(coords)
            coords.extend(r.tolist())
            segments.extend((start + k, start + (k + 1) % len(r)) for k in range(len(r)))
            if ring_no:
                holes.append(list(shapely.Polygon(r).representative_point().coords)[0])
            for a, b in zip(r, np.roll(r, -1, axis=0), strict=True):
                endpoints = np.array(sorted([tuple(a), tuple(b)]))
                bid = "edge:" + digest(np.round(endpoints, 7).tobytes())[:16]
                if bid not in boundary_cache:
                    boundary_cache[bid] = dict(id=bid, positions=np.c_[endpoints[:, 0], height(endpoints), endpoints[:, 1]].tolist())
                ids.append(bid)
        # Reuse measured ground samples inside the patch, with vector boundaries fixed.
        local = points[shapely.contains_xy(poly, points[:, 0], points[:, 2])][:, [0, 2]]
        data = dict(vertices=np.r_[np.asarray(coords), local], segments=np.asarray(segments))
        if holes:
            data["holes"] = holes
        out = triangle.triangulate(data, "pQ")
        xz = out["vertices"]
        p = np.c_[xz[:, 0], height(xz), xz[:, 1]]
        ix = out["triangles"][:, [0, 2, 1]]
        mesh = mesh_record(key + ":mesh", key, kind, p, ix, support=key)
        scene["meshes"].append(mesh)
        scene["supports"].append(dict(id=key, kind=kind, boundaryIds=sorted(set(ids)), meshId=mesh["id"]))
    scene["boundaries"] = list(boundary_cache.values())
    if not water.is_empty:
        import mapbox_earcut
        for i, poly in enumerate(shapely.get_parts(water)):
            if poly.geom_type != "Polygon":
                continue
            rings = [np.asarray(r.coords)[:-1] for r in [poly.exterior, *poly.interiors]]
            xz = np.concatenate(rings)
            ix = mapbox_earcut.triangulate_float64(xz, np.cumsum([len(r) for r in rings]).astype(np.uint32)).reshape(-1, 3)
            p = np.c_[xz[:, 0], np.full(len(xz), .05), xz[:, 1]]
            normals = np.cross(p[ix[:, 1]]-p[ix[:, 0]], p[ix[:, 2]]-p[ix[:, 0]])[:, 1]
            ix[normals < 0] = ix[normals < 0][:, [0, 2, 1]]
            scene["meshes"].append(mesh_record(f"water:{i}", f"water:{i}", "water", p, ix, "sensor"))
    return height


def compile_scene(cache, bounds=(140., 220., 215., 295.), scene_id="office"):
    from turku3d.coarse.extrusion import extrude
    points, buildings, streets, waters, sources, props = load_evidence(Path(cache), bounds)
    scene = dict(schema=SCHEMA, release="pending", id=scene_id,
                 origin=[C.ORIGIN_E, C.ORIGIN_N, 0], bounds=list(bounds), sources=sources,
                 materials=[dict(id=k, color=c, roughness=r, metalness=m) for k, (c, r, m) in PALETTE.items()],
                 boundaries=[], supports=[], meshes=[], fixtures=[], emitters=[],
                 coverage=dict(mode="replace", bounds=list(bounds), entityIds=[]), collision={},
                 spawn=dict(position=[184., 4., 265.], headingDeg=65., vehicle=dict(position=[177., 4., 271.], yaw=1.05)))
    height = ground_scene(scene, points, buildings, streets, waters)
    window = shapely.box(*bounds)
    for b in buildings:
        if not b.footprint.intersects(window):
            continue
        # Complete building entities preserve footprint and roof-plane identity.
        roof, ri, floor, fi, walls, uv, wi = extrude(b)
        for suffix, material, p, ix, tex in (("roof", "roof", roof, ri, None), ("wall", "wall", walls, wi, uv)):
            if len(ix):
                scene["meshes"].append(mesh_record(f"{b.key}:{suffix}", str(b.key), material, p, ix, uv=tex))
    for location in [scene["spawn"], scene["spawn"]["vehicle"]]:
        p = location["position"]
        p[1] = float(height([[p[0], p[2]]])[0]) + .15
    # Explicit temporary pilot perimeter: visible orange solid barriers, never an invisible blocker.
    xmin, zmin, xmax, zmax = bounds
    from turku3d.structures.geometry import Faces
    footprint_union = shapely.union_all([b.footprint for b in buildings])
    for side, (a, b) in enumerate([((xmin,zmin),(xmax,zmin)), ((xmax,zmin),(xmax,zmax)),
                                   ((xmax,zmax),(xmin,zmax)), ((xmin,zmax),(xmin,zmin))]):
        a, b = np.asarray(a), np.asarray(b)
        length = np.linalg.norm(b-a)
        tangent = (b-a)/length
        normal = np.array([-tangent[1],tangent[0]])*.12
        for k in range(int(np.ceil(length/2))):
            start, end = a+tangent*(k*2), a+tangent*min(length,(k+1)*2)
            centre = (start+end)/2
            if footprint_union.covers(shapely.Point(centre)):
                continue
            bottom = float(height([centre])[0])-.1
            ring = np.array([start-normal,end-normal,end+normal,start+normal])
            lo = np.c_[ring[:,0],np.full(4,bottom),ring[:,1]]
            hi = lo.copy(); hi[:,1] += 1.3
            f = Faces()
            f.face(hi[::-1])
            for j in range(4):
                n=(j+1)%4
                f.face([lo[j],hi[j],hi[n],lo[n]])
            scene["meshes"].append(mesh_record(f"pilot-perimeter:{side}:{k}", "pilot-perimeter", "barrier", f.positions, f.indices))
    scene["coverage"]["entityIds"] = sorted({m["entityId"] for m in scene["meshes"]})
    scene["release"] = digest(json.dumps(scene, sort_keys=True, separators=(",", ":")).encode())
    return scene


def write_products(scene, output):
    from turku3d.coarse.geometry import clip_mesh
    from turku3d.col import ColTile, write_col
    from turku3d.coarse.collision import compact_collision
    from turku3d.col.materials import mat_id
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    xmin, zmin, xmax, zmax = scene["bounds"]
    for i in range(int(xmin // 62.5), int(xmax // 62.5) + 1):
        for j in range(int(zmin // 62.5), int(zmax // 62.5) + 1):
            from turku3d.structures.geometry import Mesh
            p, ix, mats, count = [], [], [], 0
            bbox = C.BBoxLocal(i * 62.5, j * 62.5, (i + 1) * 62.5, (j + 1) * 62.5)
            for m in scene["meshes"]:
                if m["collision"] not in ("solid", "sensor"):
                    continue
                pos = np.asarray(m["positions"]).reshape(-1, 3)
                name = {"footway": "sidewalk", "wall": "building", "ground": "gravel", "iron": "pole", "barrier": "fence"}.get(m["materialId"], m["materialId"])
                source = Mesh(m["id"], name, pos, np.asarray(m["indices"]).reshape(-1, 3), np.asarray(m["uv"]).reshape(-1, 2))
                clipped = clip_mesh(source, bbox)
                if clipped is None:
                    continue
                p.append(clipped.positions)
                ix.append(clipped.indices + count)
                mats.extend([mat_id(name)] * len(clipped.indices))
                count += len(clipped.positions)
            if not p:
                continue
            origin = np.array([i * 62.5, 0, j * 62.5])
            col = ColTile(i, j, np.concatenate(p) - origin, np.concatenate(ix), np.asarray(mats, np.uint8), aoi_border=True)
            compact_collision(col)
            raw = write_col(col, encoding="raw")
            name = f"{col.id}.{digest(raw)[:16]}.col"
            (output / name).write_bytes(raw)
            scene["collision"][col.id] = dict(url=name, sha256=digest(raw), bytes=len(raw), tris=len(col.indices), h=[float(col.positions[:, 1].min()), float(col.positions[:, 1].max())])
    validate_scene(scene)
    (output / f"{scene['id']}.scene.json").write_text(json.dumps(scene, separators=(",", ":")) + "\n")
    return scene
