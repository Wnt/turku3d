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
    {'id':'qwensel-tread','color':[.22,.25,.24],'roughness':.9,'metalness':0},
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


def opposed_timber_stair(position,yaw=0,*,landing_width,flight_run,depth,back_offset,
                          steps,foot_ground_heights,post_ground_heights,support_height=None,support_faces=None):
    """Opposed walkable flights attached to a threshold, with explicit contacts.

    Ground heights are absolute and supplied independently for both flight toes
    and the two platform posts. No family-wide flat-ground assumption is made.
    """
    _check_attachment(position,yaw)
    ground=list(foot_ground_heights)+list(post_ground_heights)
    if len(ground)!=4 or not all(math.isfinite(v) and .7<=position[1]-v<=2.5 for v in ground):
        raise ValueError('Four explicit bounded entrance ground contacts required')
    if not isinstance(steps,int) or not 4<=steps<=12 or not all(math.isfinite(v) and .5<=v<=3 for v in (landing_width,flight_run,depth)):
        raise ValueError('Bounded stair dimensions and integer step count required')
    if not math.isfinite(back_offset) or not 0<=back_offset<=.5:raise ValueError('Invalid wall clearance')
    c,s=math.cos(yaw),math.sin(yaw)
    def ground_at(x,z,fallback):
        height=support_height(position[0]+c*x+s*z,position[2]-s*x+c*z) if support_height else fallback
        if not math.isfinite(height) or not .7<=position[1]-height<=2.5:raise ValueError('Unsupported stair contact sample')
        return height-position[1]
    def supported_prism(g,x0,x1,z0,z1,top,fallback):
        bottom=[(x,ground_at(x,z,fallback),z) for x,z in ((x0,z0),(x1,z0),(x1,z1),(x0,z1))]
        if max(p[1] for p in bottom)>=top:raise ValueError('Terrain reaches above first stair tread')
        upper=[(x,top,z) for x,_,z in bottom];g.face(bottom);g.face(tuple(reversed(upper)))
        for i in range(4):
            j=(i+1)%4;g.face((bottom[i],upper[i],upper[j],bottom[j]))
    tread,frame,rail=_Geometry(),_Geometry(),_Geometry();half=landing_width/2
    middle=back_offset+depth/2;front=back_offset+depth;edge=half+flight_run
    def top_face(name,x0,x1,z0,z1,y):
        if support_faces is None:return
        g=_Geometry();g.face(((x0,y,z0),(x0,y,z1),(x1,y,z1),(x1,y,z0)))
        record=g.record(name,'qwensel-tread','passable',position,yaw);record['render']=False;support_faces.append(record)
    tread.box((0,-.045,middle),(landing_width,.09,depth))
    top_face('entrance-landing',-half,half,back_offset,front,0)
    for side,sign in enumerate((-1,1)):
        toe=sign*edge
        low=min(ground_at(x,z,foot_ground_heights[side]) for x in (toe-.0175,toe+.0175) for z in (back_offset,front))
        rise=-low/steps;going=flight_run/(steps-1)
        if rise>.20:raise ValueError('Stair rise exceeds supported gameplay step height')
        for i in range(steps-1):
            top=low+(i+1)*rise;x=sign*(edge-(i+.5)*going)
            tread.box((x,top-.035,middle),(going+.015,.07,depth))
            top_face(f'entrance-{side}-tread-{i}',x-(going+.015)/2,x+(going+.015)/2,back_offset,front,top)
            if i==0:supported_prism(frame,toe-.0175,toe+.0175,back_offset,front,top,foot_ground_heights[side])
            else:frame.box((sign*(edge-i*going),top-rise/2,middle),(.035,rise,depth))
        frame.box((sign*half,-rise/2,middle),(.035,rise,depth))
        for z in (back_offset+.055,front-.055):
            stringer_ground=ground_at(sign*edge,z,foot_ground_heights[side])
            frame.beam((sign*edge,stringer_ground+.04,z),(sign*half,-.10,z),.05,4)
            bottom=(sign*(edge-.10),low+rise,z);upper=(sign*half,0,z)
            for x,y,zz in (bottom,upper):frame.box((x,y+.46,zz),(.075,.92,.075))
            rail.beam((bottom[0],bottom[1]+.91,z),(upper[0],.91,z),.045,4)
            # Crossed infill follows the actual flight slope, leaving the tread
            # centre unobstructed while giving the pale railing its silhouette.
            rail.beam((bottom[0],bottom[1]+.12,z),(upper[0],.79,z),.026,4)
            rail.beam((bottom[0],bottom[1]+.79,z),(upper[0],.12,z),.026,4)
        supported_prism(frame,sign*half-.05,sign*half+.05,front-.105,front-.005,0,post_ground_heights[side])
    rail.beam((-half,.91,front-.055),(half,.91,front-.055),.045,4)
    rail.beam((-half,.12,front-.055),(half,.79,front-.055),.026,4)
    rail.beam((-half,.79,front-.055),(half,.12,front-.055),.026,4)
    return [tread.record('stair-treads','qwensel-tread','solid',position,yaw),
            frame.record('stair-frame','qwensel-trim','solid',position,yaw),
            rail.record('stair-rails','qwensel-trim','solid',position,yaw)]


def qwensel_entrance_details(data=QWENSEL_FRONTAGE,*,support_height=None):
    door,entry=data['door'],data['entrance'];position,yaw=frontage_anchor(door['along'],entry['landingHeight'],data)
    meshes=opposed_timber_stair(position,yaw,landing_width=entry['landingWidth'],flight_run=entry['flightRun'],
                               depth=entry['depth'],back_offset=entry['backOffset'],steps=entry['stepsPerFlight'],
                               foot_ground_heights=entry['footGroundHeights'],post_ground_heights=entry['postGroundHeights'],support_height=support_height)
    # Raised panelled double door and shallow moulded surround. The wall collider
    # remains authoritative; these trim strips do not imply an opening interior.
    g=_Geometry();width,height=door['width'],door['height']
    for sign in (-1,1):g.box((sign*(width/2+.06),height/2,.065),(.12,height+.18,.13))
    for y,w,h,d in ((-.04,width+.26,.08,.18),(height+.06,width+.28,.12,.16),(height+.15,width+.40,.05,.22)):
        g.box((0,y,d/2),(w,h,d))
    for x in (-width/2+.045,0,width/2-.045):g.box((x,height/2,.04),(.07,height,.08))
    for y in (.06,.83,height-.06):g.box((0,y,.045),(width,.07,.09))
    for sign in (-1,1):
        cx=sign*width/4
        for low,h in ((.15,.59),(.94,1.10)):
            for edge_x in (-1,1):g.box((cx+edge_x*(width/4-.09),low+h/2,.055),(.025,h,.035))
            for y in (low,low+h):g.box((cx,y,.055),(width/2-.18,.025,.035))
    meshes.append(g.record('panelled-door','qwensel-trim','passable',position,yaw))
    for mesh in meshes:mesh['id']='qwensel-entrance-'+mesh['id']
    return meshes


def qwensel_entrance_contacts(data=QWENSEL_FRONTAGE,*,support_height=None):
    """Explicit authored contact centres for producer/source ground audits."""
    entry=data['entrance'];position,yaw=frontage_anchor(data['door']['along'],entry['landingHeight'],data)
    c,s=math.cos(yaw),math.sin(yaw);half=entry['landingWidth']/2;edge=half+entry['flightRun']
    result=[]
    for i,sign in enumerate((-1,1)):
        for kind,x,z,height,size in [
            ('toe',sign*edge,entry['backOffset']+entry['depth']/2,entry['footGroundHeights'][i],[.035,entry['depth']]),
            ('post',sign*half,entry['backOffset']+entry['depth']-.055,entry['postGroundHeights'][i],[.10,.10])]:
            world_x,world_z=position[0]+c*x+s*z,position[2]-s*x+c*z
            if support_height:height=support_height(world_x,world_z)
            result.append({'id':f'{"left" if sign<0 else "right"}-{kind}',
                           'position':[position[0]+c*x+s*z,height,position[2]-s*x+c*z],
                           'footprintSize':size,'yaw':yaw,'sourceUncertaintyM':.03})
    return result


def qwensel_entrance_supports(data=QWENSEL_FRONTAGE,*,support_height=None):
    """Exact tread/landing top faces emitted during construction, no mesh scan.

    These render:false/passable records are canonical support proxies only;
    collision remains in the solid entrance meshes, so no collider is duplicated.
    """
    entry=data['entrance'];position,yaw=frontage_anchor(data['door']['along'],entry['landingHeight'],data)
    supports=[]
    opposed_timber_stair(position,yaw,landing_width=entry['landingWidth'],flight_run=entry['flightRun'],
                         depth=entry['depth'],back_offset=entry['backOffset'],steps=entry['stepsPerFlight'],
                         foot_ground_heights=entry['footGroundHeights'],post_ground_heights=entry['postGroundHeights'],
                         support_height=support_height,support_faces=supports)
    return supports
