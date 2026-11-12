"""Drape bounded visual markings onto existing canonical surface triangles.

Original geometry, supports, physics and lighting arrays are never edited. The
new passable triangles interpolate their receiver's lighting; an explicit tiny
optical lift separates the finish from the underlying surface without changing
walkable heights. This helper also accepts source road-paint polygons.
"""
from __future__ import annotations
import numpy as np
import shapely
from shapely.ops import triangulate


def drape(scene, identity, polygon, material_id, *, support_kinds=('road','footway'),
          reference_height=3.7, height_tolerance=.5, optical_lift=.002,
          uv_origin=(0.,0.), uv_axes=((1.,0.),(0.,1.)), metres_per_repeat=(2.,2.)):
    """Return new render meshes and a contact receipt for a bounded XZ polygon.

    Height selection is explicit so a marking below a bridge cannot jump onto
    the deck. Input polygons may contain holes; output triangles stay inside.
    UV axes are orthonormal directions in engine XZ, with metric repeat lengths.
    """
    polygon=shapely.make_valid(polygon)
    if polygon.is_empty or not np.isfinite(shapely.get_coordinates(polygon)).all():
        raise ValueError('Inlay needs a finite nonempty polygon')
    axes=np.asarray(uv_axes,float);origin=np.asarray(uv_origin,float);repeat=np.asarray(metres_per_repeat,float)
    if axes.shape!=(2,2) or not np.allclose(axes@axes.T,np.eye(2),atol=1e-6):
        raise ValueError('Inlay UV axes must be orthonormal')
    if not np.isfinite([reference_height,height_tolerance,optical_lift,*origin,*repeat]).all() or height_tolerance<0 or not 0<=optical_lift<=.005 or np.any(repeat<=0):
        raise ValueError('Invalid inlay metric/height parameters')
    materials={m['id'] for m in scene['materials']}
    if material_id not in materials:raise ValueError('Unknown inlay material')
    low_x,low_z,high_x,high_z=polygon.bounds
    meshes={m['id']:m for m in scene['meshes']};sources=[];triangles=[];polygons=[]
    for support in scene['supports']:
        if support['kind'] not in support_kinds:continue
        mesh=meshes[support['meshId']]
        if mesh.get('render') is False:continue
        p=np.asarray(mesh['positions'],float).reshape(-1,3)
        indices=np.asarray(mesh['indices'],int).reshape(-1,3);all_tri=p[indices]
        low,high=all_tri.min(axis=1),all_tri.max(axis=1)
        eligible=(low[:,0]<=high_x)&(high[:,0]>=low_x)&(low[:,2]<=high_z)&(high[:,2]>=low_z)&(low[:,1]<=reference_height+height_tolerance)&(high[:,1]>=reference_height-height_tolerance)
        for ix,tri in zip(indices[eligible],all_tri[eligible],strict=True):
            shape=shapely.Polygon(tri[:,[0,2]])
            if shape.area<1e-9 or not shape.intersects(polygon):continue
            sources.append((mesh,support,ix));triangles.append(tri);polygons.append(shape)
    batches={};covered=0.;max_error=0.
    for (mesh,support,ix),tri,shape in zip(sources,triangles,polygons,strict=True):
        clipped=shape.intersection(polygon)
        if clipped.area<1e-9:continue
        key=mesh['id']
        if key not in batches:
            row=dict(id=f'{identity}:{key}',entityId=identity,materialId=material_id,
                     positions=[],indices=[],collision='passable',supportId=support['id'],normals=[],uv=[])
            for name in ('bakedVisibility','nightIrradiance'):
                if name in mesh:row[name]=[]
            batches[key]=row
        row=batches[key];matrix=np.column_stack((tri[1,[0,2]]-tri[0,[0,2]],tri[2,[0,2]]-tri[0,[0,2]]))
        for part in shapely.get_parts(clipped):
            if part.geom_type!='Polygon':continue
            for face in triangulate(part):
                if not part.covers(face) or face.area<1e-9:continue
                xz=np.asarray(face.exterior.coords)[:3]
                # Positive XZ winding points down in world XYZ; reverse it.
                a,b=xz[1]-xz[0],xz[2]-xz[0]
                if a[0]*b[1]-a[1]*b[0]>0:xz=xz[[0,2,1]]
                uvw=np.linalg.solve(matrix,(xz-tri[0,[0,2]]).T).T
                weights=np.column_stack((1-uvw.sum(axis=1),uvw))
                p=weights@tri
                if np.any(np.abs(p[:,1]-reference_height)>height_tolerance):continue
                max_error=max(max_error,float(np.max(np.abs(p[:,[0,2]]-xz))))
                p[:,1]+=optical_lift
                row['indices'].extend(range(len(row['positions'])//3,len(row['positions'])//3+3));row['positions'].extend(p.ravel().tolist())
                row['uv'].extend((((xz-origin)@axes.T)/repeat).ravel().tolist())
                if 'normals' in mesh:
                    normals=weights@np.asarray(mesh['normals']).reshape(-1,3)[ix]
                    normals/=np.linalg.norm(normals,axis=1)[:,None]
                else:
                    normal=np.cross(p[1]-p[0],p[2]-p[0]);normal/=np.linalg.norm(normal);normals=np.tile(normal,(3,1))
                row['normals'].extend(normals.ravel().tolist())
                for name in ('bakedVisibility','nightIrradiance'):
                    if name in row:
                        values=weights@np.asarray(mesh[name]).reshape(-1,3)[ix]
                        if name=='bakedVisibility':values=np.clip(np.rint(values),0,255).astype(int)
                        row[name].extend(values.ravel().tolist())
                covered+=face.area
    rows=[r for r in batches.values() if r['indices']]
    return rows,dict(id=identity,requestedAreaM2=float(polygon.area),coveredAreaM2=covered,
                     meshes=len(rows),triangles=sum(len(r['indices'])//3 for r in rows),
                     interpolationResidualM=max_error,opticalLiftM=optical_lift,
                     canonicalSupportHeightChangeM=0.)
