"""Original, restrained timber finish for the reusable promenade bench.

The existing linear brown is retained. Grain/roughness and 75-micrometre relief
are construction/material priors, not measurements of wood species or weathering.
No photographic pixels are used. Metric local UVs keep fibres along the slats
when a fixture is rotated or moved; the geometry and ground contact are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import numpy as np
from PIL import Image

EXTENT_METRES=(2.,.5)
SIZE=(1024,256)
BASE_LINEAR=(.18,.09,.035)


def timber_maps():
    nx,ny=SIZE
    u,v=np.meshgrid((np.arange(nx)+.5)/nx,(np.arange(ny)+.5)/ny)
    warp=.012*np.sin(2*np.pi*u)+.005*np.sin(2*np.pi*(3*u+v))
    grain=v+warp
    broad=.035*np.sin(2*np.pi*(5*grain+.12*np.sin(2*np.pi*u)))
    broad+=.025*np.sin(2*np.pi*(13*grain+.1*np.sin(4*np.pi*u)))
    fibre=.02*np.sin(2*np.pi*59*grain)+.012*np.sin(2*np.pi*(89*grain+.15*np.sin(6*np.pi*u)))
    variation=broad+fibre
    linear=np.array(BASE_LINEAR)[None,None,:]*(1+variation[:,:,None])
    rgb=np.clip(np.where(linear<=.0031308,linear*12.92,1.055*linear**(1/2.4)-.055)*255,0,255)
    height=.000075*(.7*np.sin(2*np.pi*59*grain)+.3*np.sin(2*np.pi*13*grain))
    # Periodic differences retain a seamless normal field, in physical metres.
    dx=(np.roll(height,-1,axis=1)-np.roll(height,1,axis=1))/(2*EXTENT_METRES[0]/nx)
    dy=(np.roll(height,-1,axis=0)-np.roll(height,1,axis=0))/(2*EXTENT_METRES[1]/ny)
    normal=np.stack([-dx,dy,np.ones_like(dx)],-1);normal/=np.linalg.norm(normal,axis=-1)[:,:,None]
    roughness=np.clip(.78+variation*.8,.65,.9)
    orm=np.stack([np.ones_like(roughness),roughness,np.zeros_like(roughness)],-1)
    return {name:Image.fromarray(array.astype('uint8')) for name,array in
            (('albedo',rgb),('normal',(normal*.5+.5)*255),('orm',orm*255))}


def bench_uvs(mesh,position,yaw):
    """Metric local projections for the separate flat faces of family wood boxes."""
    if len(position)!=3 or not all(math.isfinite(v) for v in (*position,yaw)):
        raise ValueError('Finite fixture transform required')
    p=np.asarray(mesh['positions'],float).reshape(-1,3)-position
    c,s=math.cos(yaw),math.sin(yaw)
    local=np.c_[c*p[:,0]-s*p[:,2],p[:,1],s*p[:,0]+c*p[:,2]]
    uv=np.full((len(p),2),np.nan)
    for tri in np.asarray(mesh['indices'],int).reshape(-1,3):
        a,b,d=local[tri];normal=np.cross(b-a,d-a)
        if np.linalg.norm(normal)<1e-10:raise ValueError('Degenerate timber face')
        axis=int(np.argmax(np.abs(normal)))
        axes=(2,1) if axis==0 else (0,2) if axis==1 else (0,1)
        projected=local[tri][:,axes]/EXTENT_METRES
        previous=uv[tri];known=np.isfinite(previous).all(axis=1)
        if np.any(np.abs(previous[known]-projected[known])>1e-7):
            raise ValueError('Timber UVs require split face vertices')
        uv[tri]=projected
    if not np.isfinite(uv).all():raise ValueError('Unreferenced timber vertices')
    return uv.ravel().tolist()


def write_maps(directory):
    """Produce immutable KTX2 inputs outside Git; return a prepared material record."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    tool=shutil.which('toktx') or str(Path.home()/'turku3d/tools/ktx-4.4.2/usr/local/bin/toktx')
    textures={};assets=[]
    for name,image in timber_maps().items():
        png=directory/(name+'.png');image.save(png);temporary=directory/(name+'.ktx2')
        options=['--t2','--genmipmap','--threads','1','--assign_oetf','srgb' if name=='albedo' else 'linear',
                 '--assign_primaries','bt709' if name=='albedo' else 'none','--encode','etc1s' if name=='albedo' else 'uastc']
        options+=['--qlevel','180','--clevel','1'] if name=='albedo' else ['--uastc_quality','2','--zcmp','18']
        subprocess.run([tool,*options,str(temporary),str(png)],check=True,capture_output=True)
        raw=temporary.read_bytes();extent=struct.unpack_from('<II',raw,20)
        if extent!=SIZE or any(n%4 for n in extent):raise ValueError('Timber KTX2 extent is not GPU block-aligned')
        digest=hashlib.sha256(raw).hexdigest();target=directory/(digest+'.ktx2');temporary.replace(target)
        key={'albedo':'baseColorTexture','normal':'normalTexture','orm':'ormTexture'}[name]
        textures[key]={'url':'shared/'+target.name,'sha256':digest,'wrap':'repeat'}
        assets.append({'path':str(target.resolve()),'sha256':digest,'bytes':len(raw),'extent':list(extent)})
    result={'material':{'id':'wood','color':[1,1,1],'roughness':1,'metalness':0,**textures},
            'assets':assets,'extentMetres':list(EXTENT_METRES),'provenance':{'pixels':'original procedural synthesis',
                'grainAndRoughness':'material priors; not surveyed wood species/finish','baseLinear':list(BASE_LINEAR),
                'reliefAmplitudeM':.000075,'placement':'retained mapped fixture; no new placement'}}
    (directory/'material.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory');args=parser.parse_args()
    result=write_maps(args.directory);print(json.dumps({'assets':len(result['assets']),'extent':SIZE,'manifest':str(Path(args.directory)/'material.json')}))
