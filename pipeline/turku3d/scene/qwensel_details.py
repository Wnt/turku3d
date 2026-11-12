"""Reference-informed timber shutters and frames on retained Qwensel walls.

Only authored parameters and generated geometry are used. The opening layout is
an explicit frontage correction; no reference image is sampled into an asset.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from .families import _Geometry, _check_attachment
from .facade_details import _extrude

QWENSEL_FRONTAGE = json.loads(Path(__file__).with_name('qwensel_frontage.json').read_text())
QWENSEL_MATERIALS = [
    {'id':'qwensel-trim','color':[.6105,.6445,.6240],'roughness':.82,'metalness':0},
    {'id':'qwensel-shutter-recess','color':[.37,.40,.38],'roughness':.9,'metalness':0},
    {'id':'qwensel-iron','color':[.035,.04,.038],'roughness':.65,'metalness':0},
]


def frontage_length(data=QWENSEL_FRONTAGE):
    return sum(math.dist(a,b) for a,b in zip(data['frontLocalPath'],data['frontLocalPath'][1:]))


def frontage_anchor(along, height, data=QWENSEL_FRONTAGE):
    """Interpolate the actual two-segment authored wall, not a guessed plane."""
    if not math.isfinite(along) or not 0 <= along <= frontage_length(data):
        raise ValueError('Frontage attachment outside retained wall')
    remaining=along
    for a,b in zip(data['frontLocalPath'],data['frontLocalPath'][1:]):
        length=math.dist(a,b)
        if remaining<=length+1e-8:
            dx,dz=(b[0]-a[0])/length,(b[1]-a[1])/length
            p=data['buildingPosition']
            return (p[0]+a[0]+dx*remaining,height,p[2]+a[1]+dz*remaining),math.atan2(-dz,dx)
        remaining-=length
    raise ValueError('Frontage attachment beyond final segment')


def _clip(poly,a,b,limit):
    result=[]
    for p,q in zip(poly,poly[1:]+poly[:1]):
        fp,fq=a*p[0]+b*p[1]-limit,a*q[0]+b*q[1]-limit
        if fp<=0:result.append(p)
        if (fp<=0)!=(fq<=0):
            t=fp/(fp-fq);result.append((p[0]+t*(q[0]-p[0]),p[1]+t*(q[1]-p[1])))
    return result


def timber_shutter_pair(position, yaw=0, *, width, height):
    """Two nearly flat-open leaves, mirrored diagonal boarding and iron hinges.

    Position is the framed opening's lower centre. Dimensions exclude trim.
    The .035m leaf thickness and .14m board pitch are construction priors.
    """
    _check_attachment(position,yaw)
    if not all(math.isfinite(v) and .5<=v<=3 for v in (width,height)):
        raise ValueError('Explicit bounded opening dimensions required')
    boards,recess,iron=_Geometry(),_Geometry(),_Geometry()
    leaf=(width+.10)/2;low=-.06;high=height+.06
    for sign in (-1,1):
        inner=sign*(width/2+.10);centre=inner+sign*leaf/2
        recess.box((centre,(low+high)/2,.055),(leaf,high-low,.035))
        rect=[(-leaf/2,low),(leaf/2,low),(leaf/2,high),(-leaf/2,high)]
        # Straight joint channels are actual gaps between boards, mirrored by leaf.
        pitch=.14
        for i in range(math.floor((low-leaf/2)/pitch)-1,math.ceil((high+leaf/2)/pitch)+1):
            poly=_clip(rect,sign,1,(i+1)*pitch-.008)
            poly=_clip(poly,-sign,-1,-i*pitch-.008)
            if len(poly)>=3:_extrude(boards,[(x+centre,y) for x,y in poly],.073,.09)
        for h in (height*.18,height*.82):
            iron.box((inner+sign*.065,h,.105),(.16,.035,.025))
            iron.beam((inner,h-.06,.09),(inner,h+.06,.09),.018,6)
    return [boards.record('shutter-boards','qwensel-trim','passable',position,yaw),
            recess.record('shutter-recess','qwensel-shutter-recess','passable',position,yaw),
            iron.record('shutter-hinges','qwensel-iron','passable',position,yaw)]


def timber_window_frame(position,yaw=0,*,width,height,columns,rows):
    _check_attachment(position,yaw)
    if not all(math.isfinite(v) and .5<=v<=3 for v in (width,height)) or columns not in (2,4) or rows not in (3,4):
        raise ValueError('Unsupported historical opening dimensions or pane grid')
    g=_Geometry()
    for sign in (-1,1):g.box((sign*(width/2+.055),height/2,.055),(.11,height+.16,.11))
    g.box((0,-.055,.075),(width+.28,.11,.15))
    g.box((0,height+.06,.075),(width+.25,.12,.15))
    g.box((0,height+.14,.10),(width+.36,.045,.20))
    for i in range(1,columns):
        g.box((-width/2+width*i/columns,height/2,.035),(.045 if i==columns/2 else .025,height,.07))
    for i in range(1,rows):g.box((0,height*i/rows,.032),(width,.025,.064))
    return [g.record('window-frame','qwensel-trim','passable',position,yaw)]


def qwensel_window_details(data=QWENSEL_FRONTAGE):
    result=[]
    for opening in data['windows']:
        position,yaw=frontage_anchor(opening['along'],data['baseHeight']+opening['sill'],data)
        meshes=timber_window_frame(position,yaw,width=opening['width'],height=opening['height'],columns=opening['columns'],rows=opening['rows'])
        if opening['shutters']:meshes+=timber_shutter_pair(position,yaw,width=opening['width'],height=opening['height'])
        for mesh in meshes:mesh['id']='qwensel-'+opening['id']+'-'+mesh['id']
        result+=meshes
    return result
