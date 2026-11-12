"""Measured hedge crowns with bases attached to canonical support triangles."""
from pathlib import Path
import numpy as np
import shapely

# Preserve the approved library material used by the existing mapped hedge.
TEXTURE = '8c50377a99a03f7845db639e3b9fd9753526c22fbfad28e6532b05ac7aab2d7c'
MATERIAL = dict(id='hedge', color=[1., 1., 1.], roughness=.95, metalness=0., alphaTest=.5, doubleSided=True,
                baseColorTexture=dict(url='shared/'+TEXTURE+'.ktx2', sha256=TEXTURE, wrap='repeat'))


def segments(row, bounds, maximum=1.):
    line = shapely.from_geojson(row['line']).intersection(shapely.box(*bounds))
    line = line.difference(shapely.from_geojson(row['openings']))
    for part in shapely.get_parts(line):
        if part.geom_type != 'LineString':
            continue
        points = np.asarray(shapely.segmentize(part, maximum).coords)
        yield from zip(points[:-1], points[1:], strict=True)


def segment_geometry(a, b, width, top, relative_height, query):
    """One solid volume: source top stays fixed while four feet follow ground."""
    direction = b-a;length = float(np.linalg.norm(direction))
    if length < 1e-6:
        return None
    normal = np.array([-direction[1], direction[0]])/length*width/2
    corners = np.array([a-normal, b-normal, b+normal, a+normal])
    feet = [query(x, z, reference_y=top-relative_height if top is not None else None) for x, z in corners]
    if any(hit is None for hit in feet):
        return None
    bottom = np.array([hit[1] for hit in feet])
    crown = np.full(4, top) if top is not None else bottom+relative_height
    if np.any(crown-bottom < .15):
        return None
    p = np.r_[np.c_[corners[:, 0], bottom, corners[:, 1]], np.c_[corners[:, 0], crown, corners[:, 1]]]
    # Face winding points outward; UVs are metres, independent of segment length.
    faces = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (4, 5, 6, 7), (3, 2, 1, 0)]
    positions, indices, uv = [], [], []
    for face in faces:
        q = p[list(face)];start = len(positions);positions.extend(q)
        if face == faces[4] or face == faces[5]:
            uv.extend(corners[list(np.array(face) % 4)])
        else:
            span = np.linalg.norm(q[1, [0, 2]]-q[0, [0, 2]])
            uv.extend([[0, 0], [span, 0], [span, q[2, 1]-q[1, 1]], [0, q[3, 1]-q[0, 1]]])
        indices.extend([[start, start+2, start+1], [start, start+3, start+2]])
    return np.array(positions), np.array(indices), np.array(uv), feet[0][0]['id']


def append(scene, records):
    if not records:
        return 0
    from .compile import mesh_record
    from .fixtures import support_query
    query = support_query(scene);count = 0
    for row in records:
        for index, (a, b) in enumerate(segments(row, scene['bounds'])):
            geometry = segment_geometry(a, b, row['widthM'], row['worldTopM'], row['heightM'], query)
            if geometry is None:
                continue
            p, ix, uv, support = geometry
            key = row['id']+':hedge:'+str(index)
            scene['meshes'].append(mesh_record(key, row['id'], 'hedge', p, ix, support=support, uv=uv))
            count += 1
    if count:
        scene['materials'].append(MATERIAL.copy())
    return count


def write_texture(scene, output):
    if not any(m['materialId'] == 'hedge' for m in scene['meshes']):
        return
    from .asset_store import resolve_asset
    target = Path(output)/MATERIAL['baseColorTexture']['url']
    source = resolve_asset(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target.resolve():
        target.write_bytes(source.read_bytes())
