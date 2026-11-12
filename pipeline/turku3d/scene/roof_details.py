"""Small timber roof families attached to an existing authored roof plane.

Dimensions are explicit visual priors. Office anchors combine retained game
geometry, qualified panorama triangulation and rounded compact roof-peak fits.
No photograph pixels, raw point samples or replacement building shell are used.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from .families import _Geometry, _check_attachment
from .facade_details import _extrude

ROOF_DETAIL_MATERIALS = [
    dict(id="office-timber-paint", color=[.38,.30,.20], roughness=.82, metalness=0.),
    dict(id="office-roof-metal", color=[.035,.040,.045], roughness=.52, metalness=0.),
    dict(id="office-chimney-mortar", color=[.23,.205,.17], roughness=.94, metalness=0.),
    dict(id="office-chimney-brick", color=[.26,.083,.042], roughness=.89, metalness=0.),
    dict(id="office-chimney-brick-light", color=[.32,.115,.067], roughness=.90, metalness=0.),
    dict(id="office-chimney-brick-dark", color=[.20,.058,.030], roughness=.91, metalness=0.),
    dict(id="office-dormer-glass", color=[.055,.085,.10], roughness=.22, metalness=0.),
]
OFFICE_RESTORED_ORNAMENT_MATERIAL = dict(id="office-ornament",color=[.38,.30,.20],roughness=.82,metalness=0.)


def timber_eave(position: tuple, yaw: float, *, width: float) -> list[dict]:
    """Eave top at Y=0, outward +Z; 0.46m deep with repeated curved corbels."""
    _check_attachment(position,yaw)
    if not math.isfinite(width) or not 1 <= width <= 40:
        raise ValueError("Eave width must be bounded in metres")
    g=_Geometry()
    for y,h,d in ((-.055,.11,.46),(-.20,.18,.39),(-.325,.07,.30),(-.39,.045,.24)):
        g.box((0,y,d/2),(width,h,d))
    count=max(2,round(width/.80))
    # Convex stepped/curved support profile, extruded across the narrow bracket.
    profile=[(.025,-.91),(.09,-.91),(.13,-.79),(.20,-.69),(.30,-.59),
             (.39,-.50),(.40,-.30),(.025,-.30)]
    for i in range(count):
        x=-width/2+(i+.5)*width/count
        tmp=_Geometry();_extrude(tmp,profile,-.075,.075)
        # temporary XY profile means outward Z/up Y; temporary extrusion means X.
        # Axis permutation has negative determinant, so reverse each triangle.
        start=len(g.positions)//3
        for j in range(0,len(tmp.positions),3):
            z,y,dx=tmp.positions[j:j+3];g.positions.extend((x+dx,y,z))
        for j in range(0,len(tmp.indices),3):
            a,b,c=tmp.indices[j:j+3];g.indices.extend((start+a,start+c,start+b))
        g.box((x,-.86,.065),(.19,.10,.13))
        g.box((x,-.32,.225),(.23,.09,.45))
    return [g.record("cornice-corbels","office-timber-paint","passable",position,yaw)]


def brick_chimney(position: tuple, yaw: float, *, height: float) -> list[dict]:
    """Roof flashing base to rain cap; shaft and cap dimensions are visual priors."""
    _check_attachment(position,yaw)
    if not math.isfinite(height) or not .8 <= height <= 4:
        raise ValueError("Chimney height must be explicitly bounded")
    mortar,metal=_Geometry(),_Geometry()
    bricks=[_Geometry(),_Geometry(),_Geometry()]
    w,d=.66,.54;bottom=.30;top=height-.28
    mortar.box((0,(bottom+top)/2,0),(w,top-bottom,d))
    metal.box((0,.20,0),(.90,.40,.80))
    # Shallow face quads preserve mortar joints without hundreds of solid boxes.
    rows=max(1,round((top-bottom)/.075));pitch=(top-bottom)/rows
    for row in range(rows):
        y0=bottom+row*pitch+.004;y1=bottom+(row+1)*pitch-.004
        for side,length in enumerate((w,d,w,d)):
            count=3 if side%2==0 else 2;step=length/count
            edges=[-length/2]+[max(-length/2,min(length/2,-length/2+(j+.5*(row%2))*step)) for j in range(1,count+1)]+[length/2]
            for col,(a,b) in enumerate(zip(edges,edges[1:])):
                if b-a<.015:continue
                a+=.004;b-=.004
                if side==0:points=[(a,y0,d/2+.003),(b,y0,d/2+.003),(b,y1,d/2+.003),(a,y1,d/2+.003)]
                elif side==1:points=[(w/2+.003,y0,-a),(w/2+.003,y0,-b),(w/2+.003,y1,-b),(w/2+.003,y1,-a)]
                elif side==2:points=[(-a,y0,-d/2-.003),(-b,y0,-d/2-.003),(-b,y1,-d/2-.003),(-a,y1,-d/2-.003)]
                else:points=[(-w/2-.003,y0,a),(-w/2-.003,y0,b),(-w/2-.003,y1,b),(-w/2-.003,y1,a)]
                bricks[(row*7+col*5+side)%3].face(points)
    # Brick oversailing courses and a visibly separated metal rain hood.
    for offset,extra in ((.18,.07),(.12,.14),(.055,.18)):
        bricks[0].box((0,top-offset,0),(w+extra,.055,d+extra))
    metal.box((0,top+.025,0),(.91,.045,.79))
    for x in (-.30,.30):
        for z in (-.24,.24):metal.box((x,top+.12,z),(.025,.19,.025))
    metal.box((0,height-.025,0),(.96,.05,.84))
    records=[mortar.record("shaft","office-chimney-mortar","passable",position,yaw),metal.record("flashing-cap","office-roof-metal","passable",position,yaw)]
    for g,name in zip(bricks,("office-chimney-brick","office-chimney-brick-light","office-chimney-brick-dark")):
        records.append(g.record(name,name,"passable",position,yaw))
    return records


def timber_dormer(position: tuple, yaw: float, *, apex_height: float) -> list[dict]:
    """Narrow pointed glazed dormer; rear intersects retained sloping roof."""
    _check_attachment(position,yaw)
    if not 1 <= apex_height <= 2.5:raise ValueError("Dormer apex must be relative metres")
    paint,metal,glass=_Geometry(),_Geometry(),_Geometry()
    half=.78;shoulder=apex_height-.57;depth=2.8
    _extrude(paint,[(-half,0),(half,0),(half,shoulder),(0,apex_height),(-half,shoulder)],-depth,0)
    # Faceted pointed glazing, raised from the backing so the original roof never
    # needs destructive cutouts; roof intersection hides the rear side walls.
    pane=[(-.43,.16),(.43,.16),(.43,shoulder-.17),(0,apex_height-.32),(-.43,shoulder-.17)]
    glass.face([(x,y,.018) for x,y in pane])
    for x in (-.50,.50):paint.box((x,shoulder/2,.055),(.12,shoulder,.14))
    paint.box((0,.09,.075),(1.18,.13,.18))
    for sign in (-1,1):
        _extrude(paint,[(sign*.51,shoulder-.05),(0,apex_height-.20),(0,apex_height-.10),(sign*.59,shoulder+.015)],.025,.16)
        x=sign*(half+.14)
        a=(0,apex_height+.045,.21);b=(x,shoulder-.025,.21)
        c=(x,shoulder-.025,-depth);d=(0,apex_height+.045,-depth)
        metal.face([a,b,c,d] if sign>0 else [d,c,b,a])
        # Raised eave fascia under the roof cover.
        paint.beam((0,apex_height-.015,.13),(x,shoulder-.085,.13),.055,4)
    paint.box((0,shoulder-.12,-depth/2),(1.80,.12,depth))
    # Clear the cross-building box from the glazing front: it is a rear roof tie.
    return [paint.record("timber-frame","office-timber-paint","passable",position,yaw),
            metal.record("metal-roof","office-roof-metal","passable",position,yaw),
            glass.record("glazing","office-dormer-glass","passable",position,yaw)]


def office_roof_details() -> list[dict]:
    """Attach additive families; existing building shell and roof own collision."""
    from shapely.geometry import Polygon, LineString
    data=json.loads(Path(__file__).with_name("office_roof_details.json").read_text())
    roof=data["roof"];n=roof["normal"]
    def roof_y(x,z):return (roof["d"]-n[0]*x-n[2]*z)/n[1]
    meshes=[]
    def add(prefix,parts):
        for p in parts:p["id"]=prefix+":"+p["id"];meshes.append(p)
    for a in data["cornices"]:add(a["id"],timber_eave(tuple(a["position"]),a["yaw"],width=a["width"]))
    for a in data["chimneys"]:
        x,z=a["centerXZ"];base=min(roof_y(x+dx,z+dz) for dx in (-.45,.45) for dz in (-.40,.40))-.04
        # Chimneys near the ridge need a base spanning both slopes; lower burial
        # is hidden by the existing shell and keeps the flashing attached.
        base=min(base,13.28)
        add(a["id"],brick_chimney((x,base,z),a["yaw"],height=a["topH"]-base))
    for a in data["dormers"]:add("office-dormer-"+a["id"],timber_dormer(tuple(a["position"]),a["yaw"],apex_height=a["apexH"]-a["position"][1]))
    # Folded seams follow the retained polygon, clipped clear of roof edges.
    polygon=Polygon(roof["polygon"]).buffer(-.025);along=(.87356,-.48672);down=(.48672,.87356)
    coords=list(polygon.exterior.coords);us=[x*along[0]+z*along[1] for x,z in coords];vs=[x*down[0]+z*down[1] for x,z in coords]
    seams=_Geometry();u=math.ceil(min(us)/.55)*.55
    while u<max(us):
        line=LineString([(along[0]*u+down[0]*v,along[1]*u+down[1]*v) for v in (min(vs)-1,max(vs)+1)])
        cut=polygon.intersection(line)
        for segment in ([cut] if cut.geom_type=="LineString" else getattr(cut,"geoms",[])):
            if segment.length<.1:continue
            a,b=list(segment.coords)[0],list(segment.coords)[-1]
            seams.beam((a[0],roof_y(*a)+.018,a[1]),(b[0],roof_y(*b)+.018,b[1]),.018,4)
        u+=.55
    meshes.append(seams.record("office-standing-seams","office-roof-metal","passable",(0,0,0),0))
    return meshes
