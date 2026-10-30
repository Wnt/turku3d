"""Classified lidar orbit and an orthophoto projected through the captured camera."""
import json
import math
import os
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont
root=Path(os.environ['TURKU3D_WORK'])/'video'
font=lambda n:ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',n)
def encode(path,width,height):
    return subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{width}x{height}','-framerate','30','-i','pipe:0','-an','-c:v','libx264','-crf','17','-preset','fast','-threads','3','-pix_fmt','yuv420p',str(path)],stdin=subprocess.PIPE)
def lidar():
    points=np.load(root/'assets/office-points.npy')[::4];xyz=points[:,:3].copy()
    xyz[:,:2]-=xyz[:,:2].mean(axis=0);xyz[:,2]-=15
    colors=np.where((points[:,3]==2)[:,None],np.array([117,231,207]),np.array([237,181,111])).astype('uint8')
    enc=encode(root/'assets/lidar-orbit.mp4',650,496)
    for f in range(210):
        theta=-.7+.5*f/209;c,s=np.cos(theta),np.sin(theta)
        x=xyz[:,0]*c-xyz[:,1]*s;y=xyz[:,0]*s+xyz[:,1]*c
        z=xyz[:,2];depth=y*.8+z*.6
        px=(325+x*1.55).astype(int);py=(290+y*.6*1.55-z*.8*1.55).astype(int)
        keep=(px>1)&(px<648)&(py>65)&(py<427);order=np.argsort(depth[keep]);xx=px[keep][order];yy=py[keep][order];co=colors[keep][order]
        im=np.empty((496,650,3),dtype='uint8');im[:]=[6,16,25]
        for dx,dy in [(0,0),(1,0),(0,1)]:im[yy+dy,xx+dx]=co
        im=Image.fromarray(im);d=ImageDraw.Draw(im)
        d.text((22,20),'ACTUAL CITY LIDAR / CLASSIFIED RETURNS',font=font(23),fill='#75e7cf')
        d.text((22,438),'Class 2 ground: teal · Other returns: ochre',font=font(23),fill='white')
        enc.stdin.write(np.asarray(im).tobytes())
    enc.stdin.close();assert enc.wait()==0

def ortho():
    source=Image.open(root/'assets/ortho.png').convert('RGB')
    meta=json.loads((root/'shots/ortho_top.json').read_text());motion=meta['motion'];assert len(motion)>=180
    # Real projected source bounds, not hand-placed screen-space corners.
    world=np.array([[0,0,70,1],[330,0,70,1],[330,0,400,1],[0,0,400,1]],float)
    uv=np.array([[0,0],[source.width,0],[source.width,source.height],[0,source.height]],float)
    enc=encode(root/'assets/ortho-camera.mp4',1920,1080)
    for state in motion[:180]:
        projection=np.array(state['camera']['projection']).reshape(4,4,order='F');view=np.array(state['camera']['view']).reshape(4,4,order='F')
        clip=(projection@view@world.T).T;ndc=clip[:,:2]/clip[:,3:4]
        screen=np.column_stack([(ndc[:,0]+1)*960,(1-ndc[:,1])*540])
        A=[];b=[]
        for (x,y),(u,v) in zip(screen,uv):
            A += [[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]];b += [u,v]
        coeff=np.linalg.solve(A,b)
        im=source.transform((1920,1080),Image.Transform.PERSPECTIVE,coeff,Image.Resampling.BICUBIC,fillcolor='#0c1725')
        enc.stdin.write(np.asarray(im).tobytes())
    enc.stdin.close();assert enc.wait()==0
if __name__=='__main__':
    import sys
    if 'lidar' in sys.argv:lidar()
    if 'ortho' in sys.argv:ortho()
