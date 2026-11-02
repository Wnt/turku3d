"""Aerial lawn outlines and mapped play areas inside residential parcels."""

import json

import numpy as np
import shapely
from affine import Affine
from rasterio.features import shapes
from scipy.ndimage import label

from turku3d.col.materials import mat_id
from turku3d.spatial import rows

from .garden_inputs import polygon


def features(tile, path):
    parcels, playgrounds = [], {}
    for key, row in rows(path, tile.bbox.buffered(25)):
        tags = json.loads(row['tags'] or '{}')
        area = polygon(row['geometry_local'])
        if area.is_empty:
            continue
        if tags.get('landuse') == 'residential':
            parcels.append(area)
        if tags.get('leisure') == 'playground':
            identity = str(row.get('osm_id', key))
            playgrounds[identity] = area
    return shapely.union_all(parcels), sorted(playgrounds.items())


def lawn_mask(rgb):
    r, g, b = np.moveaxis(rgb.astype(float), -1, 0)
    sun = (g > .95*r) & (g > 1.15*b) & (g > 25)
    # Blue skylight in building shade obscures the usual green/blue ratio.
    # Asphalt stays neutral in red/green, unlike a shaded lawn.
    shade = (g > 1.15*r) & (g > .8*b) & (g > 10) & (rgb.max(axis=-1) < 110)
    return sun | shade


def regions(tile, rgb, parcels, blocked):
    """Keep half-metre lawn islands that a two-metre triangle centroid misses."""
    if parcels.is_empty:
        return []
    domain = parcels.difference(shapely.union_all(blocked))
    if domain.is_empty:
        return []
    step = tile.size / rgb.shape[0]
    mask = lawn_mask(rgb)
    components, _ = label(mask)
    counts = np.bincount(components.ravel())
    keep = counts * step**2 >= 1.5
    keep[0] = False
    mask = keep[components].astype(np.uint8)
    transform = Affine(step, 0, tile.origin[0], 0, step, tile.origin[2])
    patches = []
    for geo, value in shapes(mask, mask=mask.astype(bool), transform=transform):
        if not value:
            continue
        area = shapely.make_valid(shapely.geometry.shape(geo).simplify(.2)).intersection(domain)
        for part in shapely.get_parts(area):
            if part.geom_type == 'Polygon' and part.area >= 1.5:
                patches.append((part, mat_id('grass')))
    return sorted(patches, key=lambda p: (-p[0].area, p[0].wkb))


def playgrounds(tile, records, sample, blocked):
    """Small generic play equipment fits only inside explicitly mapped play areas."""
    from turku3d.structures.geometry import Faces
    from .bridge_details import tube, appearance, combine
    from .geometry import clip_mesh

    result = []
    obstacles = shapely.union_all(blocked)
    for identity, area in records:
        safe = area.difference(obstacles).buffer(-.4)
        inner = safe.buffer(-2.3)
        if inner.is_empty:
            continue
        centre = np.asarray(inner.representative_point().coords[0])
        ring = np.asarray(area.minimum_rotated_rectangle.exterior.coords)
        edges = np.diff(ring, axis=0)
        axis = edges[np.argmax(np.linalg.norm(edges, axis=1))]
        axis /= np.linalg.norm(axis)
        normal = np.array([-axis[1], axis[0]])
        footprint = shapely.Polygon([centre+axis*a+normal*b
                                    for a,b in [(-2,-1.4),(2,-1.4),(2,1.4),(-2,1.4)]])
        if not safe.covers(footprint):
            continue
        key = 'playground:' + identity
        parts = []
        def at(a,b,h):
            p=centre+axis*a+normal*b
            return np.array([p[0],sample(*p)+h,p[1]])
        for end in (-1.8,1.8):
            top=at(end,0,2.3)
            for side in (-1,1):
                parts.append(tube(key,at(end,side,0),top,.055,'wood','wall'))
        parts.append(tube(key,at(-1.9,0,2.3),at(1.9,0,2.3),.065,'paint','wall'))
        for seat in (-.65,.65):
            for side in (-.22,.22):
                parts.append(tube(key,at(seat+side,0,.5),at(seat+side,0,2.3),.012,'steel'))
            corners=[at(seat+a,b,.5) for a,b in [(-.25,-.18),(.25,-.18),(.25,.18),(-.25,.18)]]
            faces=Faces();faces.face(corners[::-1]);faces.face(corners)
            parts.append(appearance(faces.mesh(key,'wall'),'wood',collision=False))
        for mesh in combine(parts,key):
            mesh.metadata['kind']='playground'
            clipped=clip_mesh(mesh,tile.bbox)
            if clipped is not None:
                result.append(clipped)
    return result
