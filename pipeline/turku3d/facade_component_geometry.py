"""Wall-local low-poly components, paint-preserving cuts and simple solid collision.

The expanded source meshes are checked by wall_surfaces before packaging converts
repeated boxes into GPU instances. All coordinates are metres in the surveyed wall
frame; no shell, generated map or photographic description is overwritten.
"""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.package.gltf import GlbMesh
from turku3d.wall_surfaces import trapezoids

TRI = np.array([[0, 3, 2], [0, 2, 1]], np.uint32)
COLOURS = dict(slab=(.55, .53, .49, 1), rail=(.16, .18, .18, 1),
               glass=(.12, .23, .28, 1), wall=(.55, .53, .49, 1))


def frame(vertices):
    tangent = vertices[1] - vertices[0]
    tangent[1] = 0
    tangent /= np.linalg.norm(tangent)
    return tangent, np.array([tangent[2], 0, -tangent[0]])


def plan(vertices, wall):
    """Admit only components contained by this surviving roof/passage wall fragment.

    This prevents decoration bridging a mapped through-passage or duplicated LOD2
    part. Partial/occluded fragments stay unresolved rather than recreating a wall.
    """
    descriptors = wall.get("geometry_components", [])
    if not descriptors:
        return []
    g = wall.get("measured_geometry", wall.get("geometry", {}))
    coordinates = g.get("coordinates") or [g["p0"], g["p1"]]
    a = np.array([coordinates[0][0] - C.ORIGIN_E, g.get("base_h", vertices[0, 1]),
                  C.ORIGIN_N - coordinates[0][1]])
    b = np.array([coordinates[-1][0] - C.ORIGIN_E, a[1], C.ORIGIN_N - coordinates[-1][1]])
    width = np.linalg.norm(b - a)
    measured_width, height = wall.get("measured_size", [wall["width_m"], wall["height_m"]])
    tangent, normal = frame(vertices)
    sign = 1 if (b - a) @ tangent > 0 else -1
    offset = (a - vertices[0]) @ tangent
    length = (vertices[1] - vertices[0]) @ tangent
    boundary = shapely.Polygon(np.c_[[0, length, length, 0], vertices[:, 1]])
    admitted, occupied = [], shapely.Polygon()
    for desc in descriptors:
        if desc["kind"] in {"passage", "artwork"}:
            continue  # Mapped passages are already cut by tunnels; artwork is only a slot.
        x, y, w, h = desc["bounds"]
        left, right = sorted([offset + sign*x*width, offset + sign*(x+w)*width])
        bottom, top = a[1] + y*height, a[1] + (y+h)*height
        # Insets keep corner returns/slabs clear of neighbouring wall and roof faces.
        left, right = left + .04, right - .04
        bottom, top = bottom + .04, top - .04
        rect = shapely.box(left, bottom, right, top)
        if (right-left < .3 or top-bottom < .3 or not boundary.buffer(1e-6).covers(rect)
                or rect.intersection(occupied).area > 1e-6):
            continue
        occupied = occupied.union(rect)
        admitted.append({**desc, "rect": rect, "metric": [left, bottom, right, top],
                         "tangent": tangent, "normal": normal})
    return admitted


def surface_parts(points, uv, plans, *, collision=False):
    """Replace the occupied patch, reusing its UV frame on the displaced face."""
    if not plans:
        yield points, uv
        return
    tangent, normal = frame(points)
    start = points[0].copy()
    start[1] = 0
    # Plans use the original wall frame, whereas paint patches may start inside it.
    reference = plans[0]["origin"]
    local = np.c_[(points-reference) @ tangent, points[:, 1]]
    surface = shapely.Polygon(local)
    cuts = [p for p in plans if p["kind"] in {"bay", "recess", "loggia", "arcade"}
            and (not collision or p["kind"] == "arcade")]
    regions = [(surface.difference(shapely.union_all([p["rect"] for p in cuts])), 0)]
    for p in cuts:
        depth = p["depth_m"] * (1 if p["kind"] == "bay" else -1)
        regions.append((surface.intersection(p["rect"]), depth))
    # UV is affine on each existing wall paint quad, including stored-map clamping.
    matrix = np.c_[local, np.ones(len(local))]
    transform = np.linalg.lstsq(matrix, uv, rcond=None)[0]
    for region, depth in regions:
        for xy in trapezoids(region):
            out = reference + xy[:, :1] * tangent + depth * normal
            out[:, 1] = xy[:, 1]
            yield out, np.c_[xy, np.ones(len(xy))] @ transform


def quad_mesh(points, name, material, building, origin, **extras):
    return GlbMesh(name, np.asarray(points) - origin, TRI.copy(), color=COLOURS[material],
                   roughness=.28 if material == "glass" else .85,
                   extras=dict(layer="buildings", surface="facade-component", building_id=building,
                               material_key=f"component_{material}", **extras))


def geometry(vertices, plans, building, origin):
    """Return visible meshes and hit boxes; no decorative collider fills an opening."""
    meshes, boxes = [], []
    if not plans:
        return meshes, boxes
    tangent, normal = frame(vertices)
    reference = vertices[0].copy()
    reference[1] = 0

    def world(xyz):
        xyz = np.asarray(xyz, float)
        out = reference + xyz[:, :1]*tangent + xyz[:, 2:3]*normal
        out[:, 1] = xyz[:, 1]
        return out

    def face(coords, material="wall"):
        meshes.append(quad_mesh(world(coords), f"component_{building}_{len(meshes)}", material,
                                building, origin))

    def box(x0, y0, z0, x1, y1, z1, material="slab", solid=True):
        if min(x1-x0, y1-y0, z1-z0) <= .001:
            return
        # Each face has flat normals. Winding faces outside in wall-local x/y/outward.
        v = np.array([[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],
                      [x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]])
        faces = [[0,1,2,3],[5,4,7,6],[4,0,3,7],[1,5,6,2],[3,2,6,7],[4,5,1,0]]
        # Our frame is left-handed: the default TRI winding yields exterior normals.
        points = world(v[np.array(faces)[:, ::-1].ravel()])
        size = [x1-x0, y1-y0, z1-z0]
        center = world([[(x0+x1)/2,(y0+y1)/2,(z0+z1)/2]])[0]
        mesh = GlbMesh(f"component_{building}_{len(meshes)}", points-origin,
                      np.concatenate([TRI+i*4 for i in range(6)]), color=COLOURS[material],
                      roughness=.28 if material == "glass" else .85,
                      extras=dict(layer="buildings", surface="facade-component", building_id=building,
                                  material_key=f"component_{material}",
                                  component_box=dict(center=(center-origin).tolist(), size=size,
                                                     tangent=tangent.tolist())))
        meshes.append(mesh)
        if solid:
            boxes.append((points, mesh.indices))

    def rail(l, r, y, d):
        # Posts meet horizontal bars at boundaries, never overlap their front surfaces.
        box(l,y,d-.06,r,y+.07,d,"rail")
        box(l,y+.93,d-.06,r,y+1,d,"rail")
        count = max(1, int((r-l)/.3))
        for x in np.linspace(l+.03,r-.03,count+1):
            box(x-.025,y+.07,d-.055,x+.025,y+.93,d-.005,"rail",False)

    for p in plans:
        p["origin"] = reference
        l,y,r,t = p["metric"]
        d, kind = p["depth_m"], p["kind"]
        if kind in {"bay", "recess", "loggia", "arcade"}:
            z = d if kind == "bay" else -d
            # The original wall patch is removed; only its returns and moved face exist.
            for coords in ([[l,y,0],[l,y,z],[l,t,z],[l,t,0]],
                           [[r,y,z],[r,y,0],[r,t,0],[r,t,z]],
                           [[l,y,z],[l,y,0],[r,y,0],[r,y,z]],
                           [[l,t,0],[l,t,z],[r,t,z],[r,t,0]]):
                face(coords)
            if kind == "bay":
                # Collision uses a box, but its expanded mesh is not emitted twice.
                before = len(meshes)
                box(l,y,.02,r,t,d)
                del meshes[before:]
            if kind == "arcade":
                # Back and returns are supplied to collision below. Column rhythm is
                # observed when counted, otherwise a conservative <=4 m spacing prior.
                count = max(2, p["count"] if p["count"] > 1 else int((r-l)/4)+1)
                for x in np.linspace(l+.18,r-.18,count):
                    box(x-.12,y,.03,x+.12,t-.2,.31)
                if p["head"] != "flat":
                    # Faceted arch spandrels occupy only the head above the opening.
                    for a,b in zip(np.linspace(l+.3,r-.3,count)[:-1], np.linspace(l+.3,r-.3,count)[1:]):
                        radius=(b-a)/2
                        xs=np.linspace(a,b,9)
                        for x0,x1 in zip(xs[:-1],xs[1:]):
                            h0=t-.2-radius+np.sqrt(max(0,radius**2-(x0-(a+b)/2)**2))
                            h1=t-.2-radius+np.sqrt(max(0,radius**2-(x1-(a+b)/2)**2))
                            face([[x0,h0,.34],[x1,h1,.34],[x1,t-.1,.34],[x0,t-.1,.34]])
        if kind in {"balcony", "glazed_balcony", "loggia", "veranda"}:
            rows, count = p["rows"], p["count"]
            for row in range(rows):
                bottom = y+(t-y)*row/rows
                top = y+(t-y)*(row+1)/rows
                for col in range(count):
                    a=l+(r-l)*col/count+.03
                    b=l+(r-l)*(col+1)/count-.03
                    outward = .12 if kind == "loggia" else d
                    back = -d+.02 if kind == "loggia" else .03
                    box(a,bottom,back,b,bottom+.12,outward)
                    if top-bottom < 1.25:
                        continue
                    if kind in {"glazed_balcony", "veranda"}:
                        # Low-cost opaque tinted glass shares one material; avoids alpha sorting.
                        box(a,bottom+.12,outward-.04,b,top-.1,outward,"glass")
                        box(a,bottom+.12,back,a+.06,top-.1,outward-.06,"rail")
                        box(b-.06,bottom+.12,back,b,top-.1,outward-.06,"rail")
                    else:
                        rail(a,b,bottom+.12,outward+.08)
        if kind == "stairs":
            rise, run = float(p["rise_m"]), float(p["run_m"])
            if run > r-l or rise > t-y:
                continue
            count=max(1,int(np.ceil(rise/.18)))
            for step in range(count):
                a=l+run*step/count if p["direction"] > 0 else r-run*(step+1)/count
                box(a,y,.03,a+run/count,y+rise*(step+1)/count,d)
    return meshes, boxes


def collision_walls(building, walls):
    """Open collision only for walkable arcades; passages remain tunnel-owned."""
    from turku3d.facade_programs import measured, matching_wall

    observation = measured(building.facade_key)
    if not observation or not any(w.get("geometry_components") for w in observation["walls"]):
        idx = np.concatenate([TRI+i*4 for i in range(len(walls)//4)]) if len(walls) else np.empty((0,3),np.uint32)
        return walls, idx
    positions, indices = [], []
    count = 0

    def append(points, idx):
        nonlocal count
        positions.append(points)
        indices.append(idx+count)
        count += len(points)

    for vertices in walls.reshape(-1,4,3):
        own = matching_wall(vertices, observation)
        plans = plan(vertices, own) if own else []
        meshes, boxes = geometry(vertices, plans, building.facade_key, np.zeros(3))
        uv = np.zeros((4,2))
        for points, _ in surface_parts(vertices, uv, plans, collision=True):
            append(points, TRI)
        for points, idx in boxes:
            append(points, idx)
        # Arcade returns are collision surfaces; upper loggias stay nonwalkable.
        for p in plans:
            if p["kind"] != "arcade":
                continue
            only, _ = geometry(vertices, [p], building.facade_key, np.zeros(3))
            for mesh in only[:4]:
                append(mesh.positions, mesh.indices)
    return (np.concatenate(positions), np.concatenate(indices)) if positions else (walls,np.empty((0,3),np.uint32))
