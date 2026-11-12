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
    "cobblestone": ([.35,.33,.30],.9,0), "tree-trunk": ([.12,.08,.04],.95,0), "quay-wall": ([.34,.32,.29],.9,0), "quay": ([.34,.32,.29],.9,0), "wood": ([.18,.09,.035],.8,0),
    "ground": ([.23, .22, .19], .95, 0), "road": ([.12, .13, .14], .94, 0),
    "footway": ([.39, .37, .33], .85, 0), "water": ([.025, .08, .09], .2, .1),
    "wall": ([.58, .43, .28], .9, 0), "roof": ([.15, .11, .09], .8, .1),
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
    points, buildings, streets, waters, sources, props, walks = [], {}, {}, {}, [], {}, {}
    xmin, zmin, xmax, zmax = bounds
    for i in range(int(xmin // 250), int(np.ceil(xmax / 250))):
        for j in range(int(zmin // 250), int(np.ceil(zmax / 250))):
            root = cache / f"t0_{i}_{j}"
            for dataset in ("ground", "wfs", "poles", "walkable"):
                paths = sorted(root.glob(dataset + ".*.pkl"))
                if not paths:
                    if dataset in ("poles", "walkable"):
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
                elif dataset == "poles":
                    props.update((p.object_id, p) for p in value)
                else:
                    for patch in value:
                        identity = str(patch["key"]) + ":" + digest(patch["knots"].tobytes())[:12]
                        walks[identity] = patch
    return np.unique(np.concatenate(points), axis=0), list(buildings.values()), list(streets.values()), list(waters.values()), sources, list(props.values()), list(walks.values())


def ground_scene(scene, points, buildings, streets, waters, walks=(), retaining=()):
    window = shapely.box(*scene["bounds"])
    water = shapely.union_all(waters).intersection(window)
    building_area = shapely.union_all([b.footprint for b in buildings])
    dry = shapely.set_precision(window.difference(water).difference(building_area), 1e-5)
    regions = [(g.intersection(dry), "footway" if m in (2,3,4,7,8) else ("road" if m in (1,5,6) else "ground"))
               for g, m in streets if g.intersects(dry)]
    lower_walks = [p for p in walks if np.nanmedian(p["heights"][:, 3]) < 2.]
    for p in walks:
        band = p["line"].buffer(p["width"]/2, cap_style="flat", join_style="mitre").intersection(dry)
        if not band.is_empty:
            regions.append((band, "quay" if np.nanmedian(p["heights"][:, 3]) < 2. else "footway"))
    regions = [(shapely.set_precision(g, 1e-5), kind) for g, kind in regions]
    linear = LinearNDInterpolator(points[:, [0, 2]], points[:, 1])
    nearest = NearestNDInterpolator(points[:, [0, 2]], points[:, 1])
    height_cache, boundary_cache = {}, {}
    def base_height(xz):
        from turku3d.coarse.walkable import grade
        from turku3d.coarse.banks import quay_crest
        xz = np.round(np.asarray(xz), 7)
        missing = [tuple(row) for row in xz if tuple(row) not in height_cache]
        if missing:
            xy = np.array(missing)
            y = linear(xy)
            bad = ~np.isfinite(y)
            y[bad] = nearest(xy[bad])
            p = np.c_[xy[:,0], y, xy[:,1]]
            p = grade(p, walks)
            if lower_walks:
                masonry = shapely.union_all([w["line"].buffer(w["width"]/2+2) for w in lower_walks])
                p = quay_crest(p, lower_walks, water, masonry)
            height_cache.update((key,float(v)) for key,v in zip(missing,p[:,1],strict=True))
        return np.array([height_cache[tuple(row)] for row in xz])
    from .retaining import RetainingField
    field = RetainingField(retaining, base_height, dry)
    def height(xz, owner=None):
        return field.height(xz, base_height(xz), owner)
    network = shapely.union_all([dry.boundary, *[g.boundary for g, _ in regions], *field.boundaries], grid_size=1e-5)
    patches = [g for g in shapely.get_parts(shapely.polygonize(shapely.get_parts(network)))
               if dry.covers(g.representative_point()) and g.area > 2e-6]
    for poly in sorted(patches, key=lambda g: g.wkb):
        poly = shapely.set_precision(poly, 1e-5)
        owner = field.owner(poly)
        key = "surface:" + digest(poly.wkb)[:16]
        kind = "ground"
        for region, candidate in regions:
            if region.covers(poly.representative_point()):
                kind = candidate
        coords, segments, holes, ids = [], [], [], []
        for ring_no, ring in enumerate([poly.exterior, *poly.interiors]):
            vertices = np.asarray(ring.coords)
            # GEOS segmentize can erase a valid narrow polygon with a returning
            # survey edge. Subdivide its ring directly, preserving every vertex.
            r = np.concatenate([a + (b-a)*np.arange(max(1,int(np.ceil(np.linalg.norm(b-a)))) )[:,None]/max(1,int(np.ceil(np.linalg.norm(b-a))))
                                for a,b in zip(vertices[:-1],vertices[1:],strict=True)])
            start = len(coords)
            coords.extend(r.tolist())
            segments.extend((start + k, start + (k + 1) % len(r)) for k in range(len(r)))
            if ring_no:
                holes.append(list(shapely.Polygon(r).representative_point().coords)[0])
            for a, b in zip(r, np.roll(r, -1, axis=0), strict=True):
                endpoints = np.array(sorted([tuple(a), tuple(b)]))
                positions = np.c_[endpoints[:, 0], height(endpoints, owner), endpoints[:, 1]]
                # Vertical walls have two different, explicitly owned boundaries
                # at the same plan edge. Equal 3-D edges still share one identity.
                positions = np.round(positions, 7)
                bid = "edge:" + digest(positions.tobytes())[:16]
                if bid not in boundary_cache:
                    boundary_cache[bid] = dict(id=bid, positions=positions.tolist())
                ids.append(bid)
        # Reuse measured ground samples inside the patch, with vector boundaries fixed.
        local = points[shapely.contains_xy(poly.buffer(-1e-4), points[:, 0], points[:, 2])][:, [0, 2]]
        data = dict(vertices=np.r_[np.asarray(coords), local], segments=np.asarray(segments))
        if holes:
            data["holes"] = holes
        out = triangle.triangulate(data, "pQ")
        xz = out["vertices"]
        p = np.c_[xz[:, 0], height(xz, owner), xz[:, 1]]
        ix = out["triangles"][:, [0, 2, 1]]
        finish = "road" if kind=="footway" else kind
        for source_polygon, source_material in streets:
            if source_polygon.covers(poly.representative_point()):
                if source_material in (1,5,6):finish="road"
                elif source_material==7:finish="cobblestone"
                elif source_material==8:finish="footway"
        mesh = mesh_record(key + ":mesh", key, finish, p, ix, support=key)
        scene["meshes"].append(mesh)
        scene["supports"].append(dict(id=key, kind=kind, boundaryIds=sorted(set(ids)), meshId=mesh["id"]))
    scene["boundaries"] = list(boundary_cache.values())
    scene["meshes"].extend(field.wall_meshes(scene["boundaries"]))
    if lower_walks and not water.is_empty:
        from turku3d.structures.geometry import Faces
        shore = water.boundary.intersection(window.buffer(-.001))
        for i,line in enumerate(shapely.get_parts(shore)):
            if line.geom_type != "LineString":
                continue
            xz = np.asarray(shapely.segmentize(line,1.).coords)
            p = np.c_[xz[:,0], height(xz), xz[:,1]]
            faces=Faces()
            for a,b in zip(p[:-1],p[1:],strict=True):
                if min(a[1],b[1]) < .5:
                    continue
                # Visible wall continues below water. Depth is an explicit visual prior.
                face=np.array([a,b,[b[0],-.5,b[2]],[a[0],-.5,a[2]]])
                n=np.cross(face[1]-face[0],face[2]-face[0])[[0,2]]
                n/=max(np.linalg.norm(n),1e-12)
                midpoint=(a[[0,2]]+b[[0,2]])/2
                if not water.covers(shapely.Point(midpoint+n*.01)):
                    face=face[::-1]
                faces.face(face)
            if faces.indices:
                scene["meshes"].append(mesh_record(f"quay-face:{i}",f"quay-face:{i}","quay-wall",faces.positions,faces.indices,uv=faces.uvs))
    if not water.is_empty:
        import mapbox_earcut
        for i, poly in enumerate(shapely.get_parts(shapely.union_all(waters).intersection(window.buffer(500)))):
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


def compile_scene(cache, bounds=(125., 187.5, 250., 312.5), scene_id="office", assets=(), material_recipes=(), trees=()):
    from turku3d.coarse.extrusion import extrude
    points, buildings, streets, waters, sources, props, walks = load_evidence(Path(cache), bounds)
    retaining_path = Path(cache) / "retaining.pkl"
    retaining = []
    if retaining_path.exists():
        raw = retaining_path.read_bytes()
        retaining = pickle.loads(raw)["value"]
        sources.append(dict(id="raw-lidar/retaining.pkl",sha256=digest(raw),kind="retaining-survey",uncertaintyM=.15))
    scene = dict(schema=SCHEMA, release="pending", id=scene_id,
                 origin=[C.ORIGIN_E, C.ORIGIN_N, 0], bounds=list(bounds), sources=sources,
                 materials=[dict(id=k, color=c, roughness=r, metalness=m) for k, (c, r, m) in PALETTE.items()],
                 boundaries=[], supports=[], meshes=[], fixtures=[], emitters=[], assets=list(assets),
                 coverage=dict(mode="replace", bounds=list(bounds), entityIds=[]), collision={},
                 spawn=dict(position=[175.55, 4., 260.14], headingDeg=65., vehicle=dict(position=[164., 4., 265.], yaw=1.05)))
    next(m for m in scene["materials"] if m["id"]=="water")["water"] = dict(point=[0.,.05,0.],normal=[0.,1.,0.],band="aurajoki-office")
    height = ground_scene(scene, points, buildings, streets, waters, walks, retaining)
    window = shapely.box(*bounds)
    for b in buildings:
        if not b.footprint.intersects(window):
            continue
        # Complete building entities preserve footprint and roof-plane identity.
        roof, ri, floor, fi, walls, uv, wi = extrude(b)
        for suffix, material, p, ix, tex in (("roof", "roof", roof, ri, None), ("wall", "wall", walls, wi, uv)):
            if len(ix):
                record = mesh_record(f"{b.key}:{suffix}", str(b.facade_key), material, p, ix, uv=tex)
                if any(a["entityId"] == str(b.facade_key) for a in assets):
                    record["render"] = False
                scene["meshes"].append(record)
    from .fixtures import place_furniture
    place_furniture(scene, props)
    if trees:
        from .fixtures import place_trunks
        place_trunks(scene, trees)
    office=next((a for a in assets if a['entityId']=='103295893K'),None)
    anchors=json.loads(Path(__file__).with_name('office_detail_anchors.json').read_text())
    if office and office['sha256']==anchors['authoredGlbSha256']:
        from .facade_details import office_window_details, OFFICE_ORNAMENT_MATERIAL
        scene['materials'].append(OFFICE_ORNAMENT_MATERIAL)
        for piece in office_window_details():
            scene['meshes'].append(mesh_record(piece['id'],'103295893K',piece['materialId'],np.array(piece['positions']).reshape(-1,3),np.array(piece['indices']).reshape(-1,3),'passable'))
    for location in [scene["spawn"], scene["spawn"]["vehicle"]]:
        p = location["position"]
        p[1] = float(height([[p[0], p[2]]])[0]) + .15
    scene["coverage"]["entityIds"] = sorted({m["entityId"] for m in scene["meshes"]} | {a["entityId"] for a in assets})
    for recipe in material_recipes:
        material=recipe["material"]
        scene["materials"]=[m for m in scene["materials"] if m["id"]!=material["id"]]+[material]
        for mesh in scene["meshes"]:
            if mesh["materialId"]==material["id"]:
                mesh["uv"]=(np.asarray(mesh["uv"]).reshape(-1,2)*recipe["uvScale"]).ravel().tolist()
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
    for i in range(int(xmin // 62.5), int(np.ceil(xmax / 62.5))):
        for j in range(int(zmin // 62.5), int(np.ceil(zmax / 62.5))):
            from turku3d.structures.geometry import Mesh
            p, ix, mats, count = [], [], [], 0
            bbox = C.BBoxLocal(i * 62.5, j * 62.5, (i + 1) * 62.5, (j + 1) * 62.5)
            for m in scene["meshes"]:
                if m["collision"] not in ("solid", "sensor"):
                    continue
                pos = np.asarray(m["positions"]).reshape(-1, 3)
                name = {"footway": "sidewalk", "wall": "building", "ground": "gravel", "iron": "street_furniture", "wood": "street_furniture", "quay-wall": "quay", "tree-trunk": "tree_trunk"}.get(m["materialId"], m["materialId"])
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
