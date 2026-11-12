"""Original periodic clay and chimney brick maps; no photographic pixels."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import numpy as np
from PIL import Image

DATA_PATH=Path(__file__).with_name('qwensel_roof.json')
SIZE=(1024,1024)


def normal_map(height,extent):
    ny,nx=height.shape
    dx=(np.roll(height,-1,1)-np.roll(height,1,1))/(2*extent[0]/nx)
    dy=(np.roll(height,-1,0)-np.roll(height,1,0))/(2*extent[1]/ny)
    n=np.stack([-dx,dy,np.ones_like(dx)],-1)
    return n/np.linalg.norm(n,axis=-1)[:,:,None]


def surface_maps(kind):
    nx,ny=SIZE;u,v=np.meshgrid((np.arange(nx)+.5)/nx,(np.arange(ny)+.5)/ny)
    if kind=='clay':
        # Four tile troughs and four overlapping courses per metric repeat.
        # A smooth periodic approximation supplies relief without altering roof planes.
        tu=u*4;tv=v*4;row=np.floor(tv).astype(int);col=np.floor(tu).astype(int)
        x=tu%1;y=tv%1
        ridge=.014*np.cos(2*np.pi*x)+.004*np.cos(4*np.pi*x)
        lap=.008*(1-np.tanh((y-.965)/.015))/2
        height=ridge+lap
        individual=np.array([[.96,1.03,.92,1.02],[1.02,.94,1.06,.97],[.95,1.02,.98,1.04],[1.04,.97,1.01,.95]])[row,col]
        fine=.008*np.sin(2*np.pi*(71*u+43*v))+.005*np.sin(2*np.pi*(139*u-89*v))
        # Retain the approximate original authored roof mean (sRGB156.6/102.6/78.3).
        rgb=np.array([156.6,102.6,78.3])[None,None,:]*(individual+fine)[:,:,None]
        ao=1-.16*np.exp(-((y-.975)/.018)**2)
        roughness=.86+fine
        extent=(.92,1.32)
    elif kind=='brick':
        tv=v*8;row=np.floor(tv).astype(int);tu=(u*4+(row%2)*.5)%4
        x=tu%1;y=tv%1;col=np.floor(tu).astype(int)
        joint=np.maximum(np.exp(-(np.minimum(x,1-x)/.022)**4),np.exp(-(np.minimum(y,1-y)/.065)**4))
        variation=.94+.12*((row*13+col*7)%17)/16
        rgb=np.array([137.,76.,61.])[None,None,:]*variation[:,:,None]
        rgb=rgb*(1-joint[:,:,None])+np.array([148.,137.,119.])[None,None,:]*joint[:,:,None]
        height=.0025*(1-joint)
        roughness=.88+joint*.08;ao=1-joint*.12;extent=(.96,.60)
    else: raise ValueError('Unknown roof surface')
    normals=normal_map(height,extent);orm=np.stack([ao,roughness,np.zeros_like(ao)],-1)
    return {name:Image.fromarray(np.clip(array,0,255).astype('uint8')) for name,array in
            [('albedo',rgb),('normal',(normals*.5+.5)*255),('orm',orm*255)]}


def write_maps(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    tool=shutil.which('toktx') or str(Path.home()/'turku3d/tools/ktx-4.4.2/usr/local/bin/toktx')
    result={'roofDataSha256':hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),'sourceGlbSha256':json.loads(DATA_PATH.read_text())['sourceGlbSha256'],'surfaces':{}}
    for kind in ('clay','brick'):
        maps={}
        for slot,image in surface_maps(kind).items():
            png=directory/f'{kind}-{slot}.png';image.save(png);temp=png.with_suffix('.ktx2')
            opts=['--t2','--genmipmap','--threads','1','--assign_oetf','srgb' if slot=='albedo' else 'linear','--assign_primaries','bt709' if slot=='albedo' else 'none','--encode','etc1s' if slot=='albedo' else 'uastc']
            opts+=['--qlevel','180','--clevel','1'] if slot=='albedo' else ['--uastc_quality','2','--zcmp','18']
            subprocess.run([tool,*opts,str(temp),str(png)],check=True,capture_output=True)
            raw=temp.read_bytes();extent=struct.unpack_from('<II',raw,20)
            if extent!=SIZE or any(n%4 for n in extent):raise ValueError('Roof map extent is not GPU-block aligned')
            sha=hashlib.sha256(raw).hexdigest();target=directory/(sha+'.ktx2');temp.replace(target)
            maps[slot]={'sha256':sha,'uri':'../shared/'+target.name,'path':str(target.resolve()),'extent':list(extent)}
        result['surfaces'][kind]=maps
    (directory/'maps.json').write_text(json.dumps(result,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();r=write_maps(a.directory)
    print(json.dumps({'maps':6,'extent':SIZE,'manifest':str(Path(a.directory)/'maps.json')}))
