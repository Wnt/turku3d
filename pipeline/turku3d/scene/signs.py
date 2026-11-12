"""Prepared Finnish sign plates retain register codes, including former fallbacks."""
from __future__ import annotations
import hashlib
import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from turku3d.props.models import model, COLORS

PRIMARY_CODES = frozenset(('C1','E2','E1','B5','B6','C37','C38','D3.1'))
MOUNT_AMBIGUITY_M = .6


def qualified_signs(props):
    """Keep mapped primary plates; unresolved shared mounts need source evidence.

    Right-hand-traffic yaw is an existing source-adapter inference, not a survey
    heading. Do not turn a centreline default into an asserted orientation.
    """
    signs=[p for p in props if p.source=='GIS_Liikennemerkit']
    for prop in signs:
        if prop.code not in PRIMARY_CODES:continue
        if prop.code not in ('C1','E2'):
            if prop.orientation!='right_hand_traffic':continue
            if any(q.object_id!=prop.object_id and math.hypot(q.x-prop.x,q.z-prop.z)<MOUNT_AMBIGUITY_M for q in signs):
                continue
        yield prop


def prototype(code):
    if code in PRIMARY_CODES-{'C1'}:
        return model('sign',code)
    if code != 'C1':
        raise ValueError('No prepared sign face for '+code)
    # Reuse the existing 640 mm circular plate and shared pole geometry. C1's
    # original procedural atlas follows Traficom C1: 640 mm diameter, 115 mm
    # red ring and 10 mm yellow outside rim. No reference image is copied.
    from dataclasses import replace
    image=Image.new('RGB',(256,128),COLORS[0]);draw=ImageDraw.Draw(image)
    for i,color in enumerate(COLORS):draw.rectangle((i*16,0,i*16+15,127),fill=color)
    draw.rectangle((128,0,255,127),fill=COLORS[2])
    for radius,color in [(62,'#ffda36'),(62*310/320,'#d9202c'),(62*195/320,'#ffda36')]:
        draw.ellipse((192-radius,64-radius,192+radius,64+radius),fill=color)
    data=io.BytesIO();image.save(data,format='PNG')
    return replace(model('sign','C38'),name='sign_C1',image=data.getvalue())


def place_signs(scene, props):
    from .compile import mesh_record
    from .fixtures import support_query
    query=support_query(scene)
    sources=[row['id'] for row in scene['sources'] if row['kind']=='poles']
    materials={m['id'] for m in scene['materials']}
    for prop in qualified_signs(props):
        hit=query(prop.x,prop.z)
        if hit is None:
            continue
        support,y=hit
        # Preserve the source adapter's inferred right-hand approach. Plate
        # dimensions and mounting height remain the existing family priors.
        shape=prototype(prop.code);yaw=prop.yaw;c,s=np.cos(yaw),np.sin(yaw)
        p=shape.positions.copy();x,z=p[:,0].copy(),p[:,2].copy()
        p[:,0]=prop.x+c*x+s*z;p[:,2]=prop.z-s*x+c*z;p[:,1]+=y
        contact=True
        for index in np.flatnonzero(np.isclose(shape.positions[:,1],0)):
            point=query(p[index,0],p[index,2])
            if point is None or abs(point[1]-y)>.03:
                contact=False;break
            p[index,1]=point[1]
        if not contact:
            if prop.code in ('C1','E2'):raise ValueError('Unsupported sign post contact')
            continue
        material='sign-'+prop.code
        if material not in materials:
            sha=hashlib.sha256(shape.image).hexdigest()
            scene['materials'].append(dict(id=material,color=[1.,1.,1.],roughness=.6,metalness=.15,
                                            baseColorTexture=dict(url='shared/'+sha+'.png',sha256=sha,wrap='clamp')))
            materials.add(material)
        key=prop.object_id+':sign'
        scene['meshes'].append(mesh_record(key,prop.object_id,material,p,shape.indices,support=support['id'],uv=shape.uvs))
        dimensions=np.ptp(shape.positions,axis=0).tolist()
        scene['fixtures'].append(dict(id=prop.object_id,family='fi-sign-'+prop.code+'-v1',supportId=support['id'],
                                      position=[prop.x,y,prop.z],yaw=yaw,dimensions=dimensions,meshIds=[key],
                                      emitterIds=[],sourceIds=sources,uncertaintyM=.3))


def write_textures(scene, output):
    for material in scene['materials']:
        if material['id'] not in {'sign-'+code for code in PRIMARY_CODES}:
            continue
        shape=prototype(material['id'][5:]);record=material['baseColorTexture']
        if hashlib.sha256(shape.image).hexdigest()!=record['sha256']:
            raise ValueError('Prepared sign atlas hash changed')
        path=Path(output)/record['url'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(shape.image)
